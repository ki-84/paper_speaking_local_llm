import pytest

from paperspeak import db, japanese_cards, paper_search, papers, recommendation_ja


def test_japanese_request_returns_persistent_selectable_papers(client, monkeypatch):
    response = client.post("/api/paper-searches", json={"query": "ロボットの拡散モデルで物をつかむ論文"})
    assert response.status_code == 200
    jid = response.json()["job_id"]
    assert client.post("/api/paper-searches", json={"query": "ロボットの拡散モデルで物をつかむ論文"}).json()["job_id"] == jid
    assert client.post("/api/paper-searches", json={"query": "別の日本語の希望"}).status_code == 409
    seen = []

    def fetch(url, params):
        seen.append(params)
        return b"feed"

    monkeypatch.setattr(papers, "fetch", fetch)
    monkeypatch.setattr(
        papers,
        "entries",
        lambda raw: [
            {
                "source_id": "2609.11111", "version": "v1", "title": "Robot Diffusion Control",
                "abstract": "We use diffusion to control a robot gripper in simulation.",
                "categories": ["cs.RO"], "url": "https://arxiv.org/abs/2609.11111v1",
            },
            {
                "source_id": "2609.22222", "version": "v2", "title": "Diffusion for Robot Grasping",
                "abstract": "We test grasping in a robot arm benchmark.",
                "categories": ["cs.RO"], "url": "https://arxiv.org/abs/2609.22222v2",
            },
        ],
    )

    class LocalModel:
        def ask(self, prompt, **kwargs):
            if prompt.startswith("Turn the user's Japanese request"):
                return {"terms": ["robot", "diffusion"], "categories": ["cs.RO"]}
            if prompt.startswith("Choose up to eight"):
                return {"ids": [2]}
            assert "Japanese" in kwargs["system"] or "日本語" in kwargs["system"]
            return {"items": [{
                "id": "1", "title_ja": "ロボットの把持に使う拡散モデル",
                "summary_ja": "ロボットアームによる把持を試します。実験では把持の性能を調べます。",
                "fit_ja": "拡散モデルを使うロボットの把持なので、希望に合います。",
            }]}

    for _ in range(10):
        if paper_search.step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), LocalModel()):
            break
    else:
        raise AssertionError("Search did not finish")
    assert "all:robot AND all:diffusion" in seen[0]["search_query"]
    assert "all:robot" in seen[1]["search_query"]
    result = client.get("/api/paper-searches").json()[0]
    assert result["query"] == "ロボットの拡散モデルで物をつかむ論文"
    assert len(result["results"]) == 1
    paper = result["results"][0]
    assert paper["title_ja"] == "ロボットの把持に使う拡散モデル"
    assert paper["source_id"] == "2609.22222" and paper["version"] == "v2"
    assert client.post(f"/api/papers/{paper['paper_id']}/lessons").status_code == 200


def test_search_retries_broader_arxiv_query_when_narrow_one_is_empty(database, monkeypatch):
    jid = db.enqueue("paper_search", "search", {"query": "拡散ロボット"}, priority=7)
    db.patch_job(jid, checkpoint={"phase": "collect", "terms": ["robot", "diffusion"], "categories": ["cs.RO"], "model": "qwen-q8"})
    queries = []

    def fetch(url, params):
        queries.append(params["search_query"])
        return b"feed"

    monkeypatch.setattr(papers, "fetch", fetch)
    monkeypatch.setattr(papers, "entries", lambda _: [])
    for _ in range(5):
        assert not paper_search.step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), None)
    assert "all:robot AND all:diffusion" in queries[0]
    assert "all:diffusion" not in queries[1]
    assert "cat:cs.AI" in queries[2]
    assert db.one("SELECT * FROM jobs WHERE id=?", (jid,))["checkpoint"]["phase"] == "rank"


def test_multiword_model_terms_keep_specific_concepts():
    terms, categories = paper_search.checked_plan({
        "terms": ["novel object grasping", "learning"],
        "categories": ["cs.RO", "not-a-category"],
    })
    assert terms == ["object", "grasping"]
    assert categories == ["cs.RO"]


def test_search_uses_an_alternative_when_one_term_has_another_meaning():
    variants = paper_search.search_variants(["grasping", "novelty", "robot"], ["cs.RO"])
    assert "all:grasping AND all:novelty" in variants[0]
    assert "all:grasping AND all:robot" in variants[1]


def test_english_title_is_repaired_before_a_result_is_visible(database):
    jid = db.enqueue("paper_search", "repair", {"query": "物をつかむロボット"}, priority=7)
    item = {
        "paper_id": "paper1", "source_id": "2609.11111", "version": "v1",
        "title": "Robot Grasping", "abstract": "A robot grasps objects."
    }
    db.patch_job(jid, checkpoint={"phase": "translate", "model": "qwen-q8", "results": [item]})

    class LocalModel:
        calls = 0

        def ask(self, prompt, **kwargs):
            self.calls += 1
            return {"items": [{
                "id": "1", "title_ja": "Robot Grasping" if self.calls == 1 else "ロボットによる把持",
                "summary_ja": "ロボットが物をつかみます。",
                "fit_ja": "把持の研究に関連します。",
            }]}

    model = LocalModel()
    assert not paper_search.step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), model)
    saved = db.one("SELECT * FROM jobs WHERE id=?", (jid,))["checkpoint"]["results"][0]
    assert saved["title_ja"] == "ロボットによる把持"
    assert model.calls == 2


def test_recommendation_japanese_backfill_preserves_original_and_feedback(client):
    pid = papers.register({
        "source_id": "2609.33333", "version": "v1", "title": "A Better Robot Grasp",
        "abstract": "We test a robot grasp in a controlled setting.",
        "url": "https://arxiv.org/abs/2609.33333v1",
    })
    rid = db.uid()
    db.execute(
        "INSERT INTO recommendations VALUES (?,?,?,?,?,?)",
        (rid, pid, "2026-09-26", "recommended", db.dumps({
            "easy_english": True, "why": "The robot grasps objects in a new way.",
            "learn": "You can learn how the grasp is planned.",
            "cautions": ["The test is small."], "source_ids": ["source1"],
        }), None),
    )
    jid = recommendation_ja.schedule()
    assert recommendation_ja.schedule() == jid

    class LocalTranslator:
        def ask(self, prompt, **kwargs):
            assert "ABSTRACT" in prompt or "要旨" in prompt
            assert "Japanese" in kwargs["system"] or "日本語" in kwargs["system"]
            return {
                "title": "ロボットの把持を改良する方法",
                "summary": "制御された条件でロボットの把持を試します。",
                "why": "ロボットが新しい方法で物をつかみます。",
                "learn": "把持の計画方法を学べます。",
                "cautions": ["試験の規模は小さいです。"],
            }

    assert not recommendation_ja.step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), LocalTranslator())
    assert recommendation_ja.step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), LocalTranslator())
    public = client.get("/api/recommendations").json()[0]
    assert public["data"]["ja"]["title"] == "ロボットの把持を改良する方法"
    assert public["data"]["why"] == "The robot grasps objects in a new way."
    assert recommendation_ja.pending() == []
    changed = db.one("SELECT * FROM recommendations WHERE id=?", (rid,))
    changed["data"]["why"] = "The robot grasps objects under new conditions."
    db.execute("UPDATE recommendations SET data=? WHERE id=?", (db.dumps(changed["data"]), rid))
    assert [r["id"] for r in recommendation_ja.pending()] == [rid]
    assert client.post(f"/api/recommendations/{rid}/feedback", json={"value": "interested"}).status_code == 200


def test_japanese_title_cannot_change_a_number():
    paper = {"title": "Robot 3D Grasp"}
    with pytest.raises(ValueError, match="数値"):
        paper_search.checked_japanese({"items": [{
            "id": "1", "title_ja": "ロボットの二次元把持",
            "summary_ja": "把持を試します。", "fit_ja": "関連します。",
        }]}, [paper])


def test_written_english_numbers_allow_equivalent_japanese_digits():
    assert 1 in japanese_cards.source_numbers("One-Grasp Adaptation")
    assert 500_000_000 in japanese_cards.source_numbers("a 500M decoder")
    assert 0 in japanese_cards.source_numbers("compare with $π_0$ (15%)")
    result = paper_search.checked_japanese({"items": [{
        "id": "1", "title_ja": "1回の把持で適応するロボット",
        "summary_ja": "1回の把持例から適応します。", "fit_ja": "ロボットの把持に関連します。",
    }]}, [{"title": "One-Grasp Adaptation", "abstract": "It adapts with one grasp."}])
    assert result["1"]["title_ja"].startswith("1回")


def test_known_grasping_mistransliteration_is_polished():
    assert japanese_cards.polish("ロボティック・グレイピング") == "ロボットによる把持"
    assert japanese_cards.polish("6つの特徴量 motivate する") == "6つの特徴量を導く"


def test_chinese_only_paper_card_is_not_published():
    paper = {"title": "Adaptive Robotic Grasping", "abstract": "A robot grasps objects."}
    with pytest.raises(ValueError, match="日本語"):
        paper_search.checked_japanese({"items": [{
            "id": "1", "title_ja": "机器人自适应抓取", "summary_ja": "ロボットが物をつかみます。",
            "fit_ja": "把持の研究に関連します。",
        }]}, [paper])


def test_outdated_recommendation_japanese_is_hidden(client):
    pid = papers.register({
        "source_id": "2609.44444", "version": "v1", "title": "Robot Grasping",
        "abstract": "We test a robot grasp.", "url": "https://arxiv.org/abs/2609.44444v1",
    })
    rid = db.uid()
    db.execute("INSERT INTO recommendations VALUES (?,?,?,?,?,?)", (
        rid, pid, "2026-09-26", "recommended", db.dumps({
            "easy_english": True, "why": "A new grasp.", "learn": "How to grasp.",
            "cautions": [], "ja": {"title": "古い題名", "source_digest": "outdated"},
        }), None,
    ))
    result = client.get("/api/recommendations").json()[0]
    assert "ja" not in result["data"]
    assert db.one("SELECT * FROM recommendations WHERE id=?", (rid,))["data"]["ja"]["title"] == "古い題名"
