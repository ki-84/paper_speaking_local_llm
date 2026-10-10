import hashlib
import time

from paperspeak import config, db, papers, story, thumbnails


def project():
    pid = papers.register(
        {
            "source_id": "2106.09685",
            "version": "v2",
            "title": "LoRA: Low-Rank Adaptation",
        }
    )
    created = story.create(pid, legacy=True)
    row = db.one("SELECT * FROM video_projects WHERE id=?", (created["project_id"],))
    row["data"]["modes"]["overview"].update(
        packaging={"title": "AIの調整"},
        scenes=[{"utterances": [{"text": "One useful new idea."}]}],
    )
    story.save(row)
    return row


def test_thumbnail_enqueue_is_idempotent_and_regeneration_keeps_old_set(database):
    row = project()
    first = thumbnails.enqueue(row, "overview")
    assert thumbnails.enqueue(row, "overview") == first
    second = thumbnails.enqueue(row, "overview", regenerate=True)
    assert second != first
    assert len(db.all("SELECT id FROM thumbnail_sets")) == 2
    assert len(db.all("SELECT id FROM jobs WHERE kind='thumbnail'")) == 2
    assert thumbnails.get(row["id"], "overview")["id"] == second


def test_selection_preserves_movies_audio_and_old_thumbnail_and_serves_both_formats(
    client,
):
    row = project()
    ident = thumbnails.enqueue(row, "overview")
    thumb = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    directory = config.DATA / "thumbnails" / ident
    directory.mkdir(parents=True)
    candidates = []
    for index in range(2):
        for suffix in ("png", "jpg"):
            (directory / f"{index}.{suffix}").write_bytes(
                f"image {index} {suffix}".encode()
            )
        candidates.append(
            {
                "id": str(index),
                "png": f"thumbnails/{ident}/{index}.png",
                "jpg": f"thumbnails/{ident}/{index}.jpg",
            }
        )
    thumb["data"]["candidates"] = candidates
    thumbnails.save(thumb)
    movie = config.DATA / "videos/movie.mp4"
    movie.write_bytes(b"unchanged actual video bytes")
    prior_hash = hashlib.sha256(movie.read_bytes()).hexdigest()
    lid = row["data"]["modes"]["overview"]["lesson_id"]
    stamp = time.time()
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "export",
            lid,
            "",
            "overview",
            "v",
            "ready",
            db.dumps(
                {
                    "mp4": "videos/movie.mp4",
                    "thumbnail": "videos/old.png",
                    "sha256": prior_hash,
                }
            ),
            stamp,
            stamp,
        ),
    )
    for chosen in candidates:
        response = client.put(
            f"/api/thumbnail-sets/{ident}/selection",
            json={"candidate_id": chosen["id"]},
        )
        assert response.status_code == 200
        assert client.get("/api/files/" + chosen["png"]).status_code == 200
        assert client.get("/api/files/" + chosen["jpg"]).status_code == 200
    saved = db.one("SELECT * FROM video_exports WHERE id='export'")["data"]
    assert saved["thumbnail"] == candidates[1]["png"]
    assert saved["thumbnail_jpg"] == candidates[1]["jpg"]
    assert saved["thumbnail_history"] == ["videos/old.png", candidates[0]["png"]]
    assert (
        hashlib.sha256(movie.read_bytes()).hexdigest() == prior_hash == saved["sha256"]
    )
    assert (
        client.put(
            f"/api/thumbnail-sets/{ident}/selection", json={"candidate_id": "missing"}
        ).status_code
        == 404
    )
    assert client.get("/api/files/thumbnails/../../credentials.json").status_code == 404
    assert not db.all("SELECT id FROM jobs WHERE kind='story_video'")
    # A new set must not remove the last usable selected thumbnail while generating.
    thumbnails.enqueue(row, "overview", regenerate=True)
    assert thumbnails.selected(row["id"], "overview")["id"] == "1"


def test_thumbnail_writer_receives_main_paper_evidence_separately_from_hype(database):
    row = project()
    db.execute("UPDATE papers SET source_id='new-paper' WHERE id=?", (row["paper_id"],))
    row["data"]["evidence"] = [
        {
            "claim": "Open surface boundaries are preserved.",
            "source_ids": [row["paper_id"] + ":H1"],
        },
        {
            "claim": "A different historical system matches 2D generation speed.",
            "source_ids": ["other-paper:H1"],
        },
    ]
    row["data"]["modes"]["overview"]["packaging"]["hook"] = "As fast as 2D?"
    story.save(row)
    ident = thumbnails.enqueue(row, "overview")
    job = db.one("SELECT * FROM jobs WHERE kind='thumbnail' AND target=?", (ident,))

    class LocalWriter:
        def ask(self, prompt, **kwargs):
            assert "PAPER CLAIMS:" in prompt
            facts = prompt.split("PAPER CLAIMS:")[1]
            assert "Open surface boundaries are preserved." in facts
            assert "different historical system" not in facts
            assert "As fast as 2D?" not in facts
            assert "not benchmark evidence" in prompt
            return {
                "candidates": [
                    {
                        "lines": ["開いた形", "新しい表現"],
                        "concept": "An open folded sheet",
                    }
                ]
                * 3
            }

    assert thumbnails.step(job, LocalWriter()) is False
    assert thumbnails.get(row["id"], "overview")["data"]["phase"] == "characters"


def test_missing_image_cannot_be_selected(database):
    row = project()
    ident = thumbnails.enqueue(row, "overview")
    thumb = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    thumb["data"]["candidates"] = [{"id": "unready", "png": "thumbnails/missing.png"}]
    thumbnails.save(thumb)
    import pytest

    with pytest.raises(ValueError, match="not ready"):
        thumbnails.select(ident, "unready")


def test_model_image_failure_falls_back_after_three_tries(database):
    row = project()
    ident = thumbnails.enqueue(row, "overview")
    thumb = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    calls = []

    def fail():
        calls.append(1)
        raise ValueError("model failed")

    for _ in range(3):
        assert (
            thumbnails._attempt(thumb, "image", fail, lambda: {"fallback": True})
            is None
        )
    assert thumbnails._attempt(thumb, "image", fail, lambda: {"fallback": True}) == {
        "fallback": True
    }
    assert len(calls) == 3
    assert thumb["data"]["warnings"][0]["reason"] == "model failed"


def test_general_story_prompts_do_not_inject_lora_mechanisms(database):
    pid = papers.register(
        {"source_id": "new", "version": "v1", "title": "Streaming Video Memory"}
    )
    row = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(pid, legacy=True)["project_id"],),
    )
    row["data"]["modes"]["overview"]["packaging"] = {"hook": "Watch a stream"}
    scene = {
        "word_budget": 400,
        "focus": "The actual streaming problem",
        "claim_ids": [],
    }
    row["data"]["modes"]["overview"]["scenes"] = [scene]
    prompt = story._script_prompt(row, "overview", scene, 0)
    assert "LoRA" not in prompt
    assert "kitchen" not in prompt
    assert "Follow the outline" in prompt


def test_manual_selection_survives_stale_worker_save_and_automatic_recommendation(
    database,
):
    row = project()
    ident = thumbnails.enqueue(row, "overview")
    directory = config.DATA / "thumbnails" / ident
    directory.mkdir(parents=True)
    for i in range(2):
        (directory / f"{i}.png").write_bytes(b"png")
        (directory / f"{i}.jpg").write_bytes(b"jpeg")
    thumb = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    thumb["data"]["candidates"] = [
        {
            "id": str(i),
            "png": f"thumbnails/{ident}/{i}.png",
            "jpg": f"thumbnails/{ident}/{i}.jpg",
        }
        for i in range(2)
    ]
    thumbnails.save(thumb)
    stale = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    thumbnails.select(ident, "1")
    stale["data"]["phase"] = "review"
    thumbnails.save(stale)
    thumbnails.select(ident, "0", automatic=True)
    assert thumbnails.get(row["id"], "overview")["data"]["selected_id"] == "1"


def test_render_completion_preserves_concurrent_thumbnail_selection(database):
    from paperspeak import video

    row = project()
    lid = row["data"]["modes"]["overview"]["lesson_id"]
    stamp = time.time()
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        ("export", lid, "", "overview", "v", "queued", "{}", stamp, stamp),
    )
    stale = db.one("SELECT * FROM video_exports WHERE id='export'")
    db.execute(
        "UPDATE video_exports SET data=? WHERE id=?",
        (
            db.dumps(
                {
                    "thumbnail": "thumbnails/chosen.png",
                    "thumbnail_jpg": "thumbnails/chosen.jpg",
                    "thumbnail_set_id": "set",
                    "thumbnail_history": ["videos/prior.png"],
                }
            ),
            "export",
        ),
    )
    video._set_export(
        stale, "ready", thumbnail="videos/stale.png", mp4="videos/done.mp4"
    )
    final = db.one("SELECT * FROM video_exports WHERE id='export'")
    assert final["data"]["thumbnail"] == "thumbnails/chosen.png"
    assert final["data"]["thumbnail_history"] == ["videos/prior.png"]
    assert final["data"]["mp4"] == "videos/done.mp4"
