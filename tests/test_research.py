import datetime as dt
import hashlib
import socket

import pytest
from paperspeak import awards, db, nightly, papers, research
from paperspeak.runtime import GPUUnavailable, PracticePreempted

SPEC = {
    "venue": "RSS",
    "year": 2026,
    "url": "https://roboticsconference.org/2026/program/awards/",
}
TITLE = "Robots Learn From Everyday Mistakes"
QUOTE = f"RSS 2026 Best Paper Award winner: {TITLE}"


def install_page(database, html, url=SPEC["url"]):
    doc = {
        "url": url,
        "html": html,
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
        "retrieved_at": 1,
    }
    path = (
        database
        / "cache"
        / ("award-page-" + hashlib.sha256(url.encode()).hexdigest() + ".json")
    )
    path.write_text(db.dumps(doc))
    return doc


def install_receipt(
    database, html=f"<title>RSS 2026 Awards</title><main><div>{QUOTE}</div></main>"
):
    doc = install_page(database, html)
    research.save_receipt(
        {
            "source": SPEC,
            "papers": [],
            "winner_count": 0,
            "paper_count": 0,
            "status": "no confirmed winners",
            "documents": [{k: doc[k] for k in ("url", "sha256", "retrieved_at")}],
            "coverage": {},
        }
    )
    return doc


class Reader:
    def __init__(self, response=None, error=None):
        self.response = (
            response
            if response is not None
            else {
                "winners": [
                    {"title": TITLE, "name": "Best Paper Award", "evidence": QUOTE}
                ],
                "next_link_ids": [],
                "reason_ja": "原文に受賞が明示されています。",
            }
        )
        self.error, self.calls = error, []

    def ask(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if self.error:
            raise self.error
        # Every AI tool executes under the existing network guard.
        with pytest.raises(OSError):
            socket.getaddrinfo("outside.example", 443)
        return self.response


def finish(state, reader):
    for _ in range(100):
        if research.repair_step(SPEC, reader, state):
            return
    pytest.fail("Bounded research did not finish")


def test_changed_markup_is_recovered_with_exact_quotes_and_model_record(database):
    doc = install_receipt(database)
    assert not awards.parse(doc, "RSS", 2026)
    reader, state = Reader(), {}
    finish(state, reader)
    saved = research.receipt(SPEC)
    assert saved["papers"][0]["title"] == TITLE
    assert saved["papers"][0]["source_sha256"] == doc["sha256"]
    assert saved["papers"][0]["evidence_excerpt"] == QUOTE
    assert saved["local_ai"]["state"] == "completed"
    assert saved["local_ai"]["model"]["profile"] == "qwen-q8"
    assert saved["local_ai"]["records"][0]["prompt_sha256"]
    assert len(reader.calls) == 1
    assert not db.all("SELECT id FROM video_projects")


@pytest.mark.parametrize(
    "quote,name,title",
    [
        (QUOTE + " invented details", "Best Paper Award", TITLE),
        (QUOTE, "Best Student Paper Award", TITLE),
        (QUOTE, "Best Paper Award", "An Invented Winner With A Similar Name"),
        (f"RSS 2025 Best Paper Award winner: {TITLE}", "Best Paper Award", TITLE),
        (f"RSS 2026 Best Paper Award finalist: {TITLE}", "Best Paper Award", TITLE),
        (
            f"RSS 2026 Best Paper Award nominee and winner: {TITLE}",
            "Best Paper Award",
            TITLE,
        ),
        (
            f"RSS 2026 Best Paper Award winner: {TITLE} (workshop)",
            "Best Paper Award",
            TITLE,
        ),
    ],
)
def test_unproven_awards_are_rejected_even_when_ai_insists(
    database, quote, name, title
):
    doc = install_page(
        database, f"<title>RSS 2026 Awards</title><main><div>{quote}</div></main>"
    )
    unit = {"text": research.compact(quote), "page": None}
    rows, rejected = research.validate_winners(
        {
            "winners": [
                {
                    "title": title,
                    "name": name,
                    "evidence": quote,
                }
            ]
        },
        unit if "invented details" not in quote else {"text": QUOTE, "page": None},
        SPEC,
        doc,
    )
    assert not rows and rejected


def test_three_bad_extractions_advance_and_cache_failure_reason(database):
    install_receipt(database)
    reader = Reader(
        {
            "winners": [
                {
                    "title": "A Completely Fabricated Winner",
                    "name": "Best Paper Award",
                    "evidence": QUOTE,
                }
            ],
            "next_link_ids": [],
        }
    )
    state = {}
    finish(state, reader)
    assert len(reader.calls) == 3
    assert not research.receipt(SPEC)["papers"]
    assert state["records"][0]["error"].startswith("Unverified extraction")
    second = Reader()
    finish({}, second)
    assert not second.calls


@pytest.mark.parametrize(
    "error", [PracticePreempted("recording"), GPUUnavailable("busy")]
)
def test_gpu_wait_or_recording_never_consumes_repair_attempts(database, error):
    install_receipt(database)
    state = {}
    with pytest.raises(type(error)):
        research.repair_step(SPEC, Reader(error=error), state)
    assert state["attempts"] == 0 and state["index"] == 0
    finish(state, Reader())
    assert research.receipt(SPEC)["winner_count"] == 1


def test_resume_reuses_finished_inference_after_checkpoint_loss(database):
    install_receipt(database)
    reader = Reader()
    assert not research.repair_step(SPEC, reader, {})
    finish({}, reader)
    assert len(reader.calls) == 1
    assert research.receipt(SPEC)["winner_count"] == 1


def test_replayed_research_retains_original_inference_time(database, monkeypatch):
    install_receipt(database)
    monkeypatch.setattr(research.time, "time", lambda: 100)
    finish({}, Reader())
    monkeypatch.setattr(research.time, "time", lambda: 200)
    finish({}, Reader(error=RuntimeError("Cached inference must be reused")))
    record = research.receipt(SPEC)["local_ai"]["records"][0]
    assert record["checked_at"] == 100 and record["applied_at"] == 200


def test_ai_navigation_uses_only_offered_links_and_stops_at_bounded_depth(
    database, monkeypatch
):
    bridge = "https://roboticsconference.org/2026/program/award-results/"
    install_receipt(
        database,
        f'<title>RSS 2026</title><main>Program <a href="{bridge}">Award results</a></main>',
    )
    reader = Reader(
        {
            "winners": [],
            "next_link_ids": ["L1"],
            "reason_ja": "別の公式結果ページを確認します。",
        }
    )
    state = {}
    assert not research.repair_step(SPEC, reader, state)
    # Simulate loss of the DB checkpoint after the unit receipt was written.
    state = {}
    assert not research.repair_step(SPEC, reader, state)
    assert len(reader.calls) == 1 and state["units"][1]["url"] == bridge

    def fetch(url):
        assert url == bridge
        return install_page(
            database,
            f"<title>RSS 2026 Awards</title><main><div>{QUOTE}</div></main>",
            url,
        )

    monkeypatch.setattr(awards, "fetch", fetch)
    assert not research.repair_step(SPEC, reader, state)
    reader.response = {
        "winners": [{"title": TITLE, "name": "Best Paper Award", "evidence": QUOTE}],
        "next_link_ids": [],
    }
    finish(state, reader)
    assert research.receipt(SPEC)["papers"][0]["official_url"] == bridge


def test_invented_navigation_cannot_make_a_network_request(database, monkeypatch):
    install_receipt(database)
    monkeypatch.setattr(
        awards, "fetch", lambda *_a, **_kw: pytest.fail("Invented link fetched")
    )
    reader = Reader(
        {
            "winners": [],
            "next_link_ids": ["https://arbitrary.example"],
            "reason_ja": "移動",
        }
    )
    finish({}, reader)
    assert len(reader.calls) == 3


def test_changed_source_hash_invalidates_repair_cache(database):
    install_receipt(database)
    reader = Reader()
    finish({}, reader)
    install_receipt(
        database,
        f"<title>RSS 2026 Awards</title><main><div>{QUOTE}. Corrected author list.</div></main>",
    )
    finish({}, reader)
    assert len(reader.calls) == 2


@pytest.mark.parametrize(
    "terms", [["robot AND cat:cs.CL"], ["https://example.org"], ['x" OR all:*']]
)
def test_query_operators_cannot_escape_configured_search_domains(terms):
    with pytest.raises(ValueError):
        research.search_terms(terms)


def test_broader_title_search_still_requires_exact_paper_identity(
    database, monkeypatch
):
    reader = Reader({"terms": ["Everyday Mistakes"], "reason_ja": "特徴的な題名の語"})
    seen = []
    monkeypatch.setattr(
        papers, "fetch", lambda _url, params, **kw: seen.append(params) or "feed"
    )
    monkeypatch.setattr(
        papers,
        "entries",
        lambda _: [{"source_id": "2601.00001", "title": TITLE + " Extended"}],
    )
    assert (
        research.resolve_missing({"title": TITLE}, reader, "qwen-q8")["metadata"]
        is None
    )
    assert seen[0]["search_query"] == 'ti:"Everyday Mistakes"'
    monkeypatch.setattr(
        papers, "entries", lambda _: [{"source_id": "2601.00001", "title": TITLE}]
    )
    assert (
        research.resolve_missing({"title": TITLE}, reader, "qwen-q8")["metadata"][
            "source_id"
        ]
        == "2601.00001"
    )


def test_successful_broader_identity_is_reused_without_another_query(
    database, monkeypatch
):
    winner = {"title": TITLE}
    reader = Reader({"terms": ["Everyday Mistakes"], "reason_ja": "特徴的な題名の語"})
    monkeypatch.setattr(papers, "fetch", lambda *_a, **_kw: "feed")
    monkeypatch.setattr(
        papers, "entries", lambda _: [{"source_id": "2601.00001", "title": TITLE}]
    )
    assert research.resolve_missing(winner, reader, "qwen-q8")["metadata"]
    monkeypatch.setattr(
        papers,
        "fetch",
        lambda *_a, **_kw: pytest.fail("Successful identity was not reused"),
    )
    assert awards.resolve(winner)["source_id"] == "2601.00001"


def test_test_of_time_announcement_and_ai_name_do_not_duplicate_prizes(database):
    doc = install_receipt(
        database,
        f"<title>RSS 2026 Awards</title><main><div>RSS 2026 Test of Time Award winner: {TITLE}</div></main>",
    )
    value = research.receipt(SPEC)
    value["papers"] = [
        {
            "title": TITLE,
            "name": "Announcing the Test of Time Awards from RSS 2016",
            "venue": "RSS",
            "year": 2026,
            "official_url": SPEC["url"],
        }
    ]
    research.merge_receipt(
        value,
        [doc],
        [{"title": TITLE, "name": "Test of Time Award", "venue": "RSS", "year": 2026}],
    )
    assert value["winner_count"] == 1


def test_test_of_time_prompt_and_empty_answer_repair_distinguish_award_year(database):
    install_receipt(
        database,
        f"<title>RSS 2026 Awards</title><main><div>Test of Time awards for RSS 2026. Congratulations to the winners! {TITLE}. Originally published at RSS 2016.</div></main>",
    )
    reader = Reader({"winners": [], "next_link_ids": [], "reason_ja": "古い論文です。"})
    state = {}
    assert not research.repair_step(SPEC, reader, state)
    assert state["attempts"] == 1 and "old paper publication year" in state["error"]
    assert (
        "AWARD YEAR" in reader.calls[0][0]
        and "INCLUDE Test of Time" in reader.calls[0][0]
    )


def test_legacy_redirect_cache_is_located_by_exact_source_hash(database):
    original = SPEC["url"]
    final = "https://roboticsconference.org/2026/program/awards/results/"
    doc = install_page(database, "<main>Actual redirect result</main>", original)
    doc["url"] = final
    (
        database
        / "cache"
        / ("award-page-" + hashlib.sha256(original.encode()).hexdigest() + ".json")
    ).write_text(db.dumps(doc))
    assert research.cached_document({"url": final, "sha256": doc["sha256"]}) == doc
    assert research.cached_document({"url": final, "sha256": "wrong"}) is None


def test_nightly_ai_search_is_saved_and_date_domain_bounded(database, monkeypatch):
    now = dt.datetime(2026, 10, 4, 2, tzinfo=nightly.ZONE)
    ident = nightly.start(now=now)
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    run["data"]["phase"] = "search_plan"
    run["data"]["started"] = now.timestamp()
    nightly.save(run)
    reader = Reader(
        {
            "queries": [
                {
                    "category": "cs.RO",
                    "terms": ["robot learning"],
                    "reason_ja": "ロボティクスの候補を補います。",
                }
            ],
            "reason_ja": "分野の偏りを補います。",
        }
    )
    job = db.one("SELECT * FROM jobs WHERE target=?", (ident,))
    nightly.step(job, reader)
    saved = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    assert saved["data"]["phase"] == "search_queries"
    params = []
    monkeypatch.setattr(
        papers, "fetch", lambda _url, p, **kw: params.append(p) or "feed"
    )
    monkeypatch.setattr(papers, "entries", lambda _: [])
    nightly.step(job, reader)
    assert (
        params[0]["search_query"]
        == 'cat:cs.RO AND submittedDate:[202609261700 TO 202610031700] AND (ti:"robot learning")'
    )
    nightly.step(job, reader)
    saved = nightly.get(ident)
    assert saved["data"]["phase"] == "read"
    assert saved["data"]["search_history"][0]["result_count"] == 0
    assert saved["data"]["search_plans"][0]["model"]["profile"] == "qwen-q8"


def test_failed_search_plan_continues_to_saved_candidates_after_three_attempts(
    database,
):
    ident = nightly.start()
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    run["data"]["phase"] = "search_plan"
    nightly.save(run)
    reader = Reader(
        {"queries": [{"category": "cs.ET", "terms": ["irrelevant domain"]}]}
    )
    job = db.one("SELECT * FROM jobs WHERE target=?", (ident,))
    for _ in range(5):
        nightly.step(job, reader)
    assert len(reader.calls) == 3
    saved = nightly.get(ident)
    assert saved["data"]["phase"] == "read" and saved["data"]["warnings"]


def test_nightly_repairs_awards_before_resolving_papers(database, monkeypatch):
    install_receipt(database)
    ident = nightly.start()
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    run["data"].update(phase="award_repair", award_sources=[{"source": SPEC}])
    nightly.save(run)
    job = db.one("SELECT * FROM jobs WHERE target=?", (ident,))
    reader = Reader()
    for _ in range(3):
        nightly.step(job, reader)
    saved = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    assert saved["data"]["phase"] == "award_resolve"
    assert saved["data"]["award_winners"][0]["title"] == TITLE
    assert not db.all("SELECT id FROM video_projects")
