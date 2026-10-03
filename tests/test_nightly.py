import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from paperspeak import db, nightly, papers, story, thumbnails
from paperspeak.runtime import GPUUnavailable, PracticePreempted

NOW = dt.datetime(2026, 10, 3, 2, tzinfo=nightly.ZONE)


def meta(ident, category="cs.CL", days=1):
    return {
        "source_id": ident,
        "version": "v1",
        "title": "A New Scientific Idea",
        "published": (NOW - dt.timedelta(days=days)).isoformat(),
        "categories": [category],
    }


def raw_run():
    return db.one(
        "SELECT * FROM nightly_video_runs WHERE id=?", (nightly.start(now=NOW),)
    )


def test_jst_schedule_one_run_even_for_concurrent_calls(database):
    db.set_setting("nightly_video_enabled", True)
    nightly.schedule(NOW - dt.timedelta(minutes=1))
    assert not db.all("SELECT id FROM nightly_video_runs")
    nightly.schedule(NOW.astimezone(dt.timezone.utc))
    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = list(pool.map(lambda _: nightly.start(now=NOW), range(10)))
    assert len(set(ids)) == 1
    assert len(db.all("SELECT id FROM jobs WHERE kind='nightly_video'")) == 1
    assert db.one("SELECT day FROM nightly_video_runs")["day"] == "2026-10-03"


def test_disabled_schedule_and_boot_catch_up(database):
    nightly.schedule(NOW + dt.timedelta(hours=8))
    assert not db.all("SELECT id FROM nightly_video_runs")
    db.set_setting("nightly_video_enabled", True)
    nightly.schedule(NOW + dt.timedelta(hours=8))
    assert len(db.all("SELECT id FROM nightly_video_runs")) == 1


def test_manual_after_completed_day_preserves_history_and_deduplicates(database):
    db.set_setting("nightly_video_enabled", True)
    first = raw_run()
    first["state"] = "ready"
    first["data"]["phase"] = "complete"
    nightly.save(first)
    with ThreadPoolExecutor(max_workers=5) as pool:
        ids = list(pool.map(lambda _: nightly.start(now=NOW, manual=True), range(10)))
    assert len(set(ids)) == 1 and ids[0] != first["id"]
    new = nightly.get(ids[0])
    assert new["day"] == first["day"]
    assert new["data"]["manual"] and new["data"]["manual_repeat"]
    assert nightly.get(first["id"])["state"] == "ready"
    nightly.schedule(NOW + dt.timedelta(hours=12))
    assert len(db.all("SELECT id FROM nightly_video_runs")) == 2
    assert nightly.start(now=NOW) == first["id"]


def test_manual_start_reuses_a_previous_day_in_progress(database):
    first = raw_run()
    assert nightly.start(now=NOW + dt.timedelta(days=1), manual=True) == first["id"]
    assert len(db.all("SELECT id FROM nightly_video_runs")) == 1


def test_manual_api_after_completion_lists_new_run_first_and_keeps_old(client):
    first = client.post("/api/nightly-video-runs").json()
    db.execute("UPDATE nightly_video_runs SET state='ready' WHERE id=?", (first["id"],))
    db.execute(
        "UPDATE jobs SET state='completed' WHERE kind='nightly_video' AND target=?",
        (first["id"],),
    )
    added = client.post("/api/nightly-video-runs").json()
    again = client.post("/api/nightly-video-runs").json()
    assert added["id"] != first["id"] and again["id"] == added["id"]
    assert added["day"] == first["day"] and added["data"]["manual_repeat"]
    history = client.get("/api/nightly-video-runs").json()
    assert [r["id"] for r in history] == [added["id"], first["id"]]
    assert history[1]["state"] == "ready"


def test_legacy_unique_day_migration_keeps_history_and_project(database):
    root = story.create(papers.register(meta("2609.00001")))
    with db.connection() as c:
        c.execute("DROP TABLE nightly_video_runs")
        c.execute("""CREATE TABLE nightly_video_runs (
          id TEXT PRIMARY KEY, day TEXT NOT NULL UNIQUE, state TEXT NOT NULL,
          project_id TEXT REFERENCES video_projects(id), data TEXT NOT NULL,
          created REAL NOT NULL, updated REAL NOT NULL)""")
        c.execute(
            "INSERT INTO nightly_video_runs VALUES (?,?,?,?,?,?,?)",
            (
                "legacy",
                "2026-10-03",
                "ready",
                root["project_id"],
                db.dumps({"phase": "complete", "history": ["original"]}),
                1,
                2,
            ),
        )
    db.init()
    db.init()
    old = db.one("SELECT * FROM nightly_video_runs WHERE id='legacy'")
    assert old["project_id"] == root["project_id"] and old["data"]["history"] == [
        "original"
    ]
    assert old["created"] == 1 and old["updated"] == 2
    added = nightly.start(now=NOW, manual=True)
    assert added != "legacy"
    assert len(db.all("SELECT id FROM nightly_video_runs")) == 2
    with db.connection() as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_carry_over_and_paused_old_lessons_are_independent(database):
    old = db.enqueue("lesson", "old")
    db.patch_job(old, state="paused")
    first = raw_run()
    db.execute(
        "UPDATE nightly_video_runs SET state='paused' WHERE id=?", (first["id"],)
    )
    second = nightly.get(nightly.start(now=NOW + dt.timedelta(days=1)))
    assert second["state"] == "skipped"
    assert second["job"] is None
    assert "未完了" in second["data"]["reason"]
    assert db.one("SELECT state FROM jobs WHERE id=?", (old,))["state"] == "paused"


def test_next_day_keeps_continuing_project_and_controls_visible(database):
    first = raw_run()
    root = story.create(papers.register(meta("2610.00002")))
    first["state"] = "building"
    first["project_id"] = root["project_id"]
    first["data"]["selected"] = {"title": "The selected new paper"}
    nightly.save(first)
    db.patch_job(root["job_id"], stage="Deep dive is continuing")
    second = nightly.get(nightly.start(now=NOW + dt.timedelta(days=1)))
    assert second["state"] == "skipped"
    assert second["job"] is None
    assert second["project"]["id"] == root["project_id"]
    assert second["continuing_run"]["id"] == first["id"]
    assert second["continuing_run"]["job"]["state"] == "queued"
    assert second["data"]["waiting_reason"] == "Deep dive is continuing"


def test_shortlist_filters_revisions_stale_future_and_keeps_domain_balance(database):
    pid = papers.register(meta("2609.00001"))
    story.create(pid)
    candidates = [
        meta("2609.00001") | {"version": "v2"},
        meta("2610.00002", "cs.CL"),
        meta("2610.00003", "cs.RO"),
        meta("2610.00004", "cs.AI"),
        meta("2609.00005", days=20),
        meta("2610.00006", days=-1),
    ]
    hot = {
        "2610.00002": {
            "rank": 1,
            "upvotes": 50,
            "retrieved_at": 1,
            "source_url": "https://huggingface.co/papers/2610.00002",
        }
    }
    seven = nightly.shortlist(candidates, hot, now=NOW, days=7)
    assert {c["source_id"] for c in seven} == {"2610.00002", "2610.00003", "2610.00004"}
    assert seven[0]["source_id"] == "2610.00002"
    assert seven[0]["attention"] == hot["2610.00002"]
    assert seven[1]["attention"] is None
    assert "2609.00005" in {
        c["source_id"] for c in nightly.shortlist(candidates, {}, now=NOW, days=30)
    }


def test_no_candidates_expands_then_skips_with_reason(database):
    run = raw_run()
    assert not nightly._expand_or_skip(run)
    assert run["data"]["window_days"] == 30
    assert nightly._expand_or_skip(run)
    assert nightly.get(run["id"])["state"] == "skipped"
    assert nightly.get(run["id"])["data"]["reason"]


@pytest.mark.parametrize(
    "error", [PracticePreempted("recording"), GPUUnavailable("GPU busy")]
)
def test_gpu_wait_and_recording_do_not_consume_retries(database, error):
    run = raw_run()

    def fail():
        raise error

    with pytest.raises(type(error)):
        nightly._tried(run, "read", fail, lambda: [])
    assert run["data"]["repairs"]["read"]["attempts"] == 0
    row = {"data": {"repairs": {}, "warnings": []}}
    with pytest.raises(type(error)):
        thumbnails._attempt(row, "image", fail, lambda: {})
    assert row["data"]["repairs"]["image"]["attempts"] == 0


def test_three_network_failures_fall_back_without_inventing_attention(
    database, monkeypatch
):
    run = raw_run()
    calls = []

    def fail(day):
        calls.append(day)
        raise OSError("offline")

    monkeypatch.setattr(nightly, "attention_feed", fail)
    job = db.one("SELECT * FROM jobs WHERE target=?", (run["id"],))
    for _ in range(4):
        nightly.step(job, None)
    fresh = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run["id"],))
    assert len(calls) == 3
    assert fresh["data"]["attention"] == {}
    assert fresh["data"]["phase"] == "award_sources"
    assert not fresh["data"]["attention_source"]["available"]
    assert fresh["data"]["warnings"]


def test_unreadable_full_text_moves_to_next_candidate(database, monkeypatch):
    run = raw_run()
    run["data"].update(phase="read", shortlist=[meta("2610.00002"), meta("2610.00003")])
    nightly.save(run)
    count = []

    def fail(pid, **kwargs):
        count.append(pid)
        raise ValueError("PDF unavailable")

    monkeypatch.setattr(papers, "ingest", fail)
    job = db.one("SELECT * FROM jobs WHERE target=?", (run["id"],))
    for _ in range(4):
        nightly.step(job, None)
    fresh = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run["id"],))
    assert len(count) == 3
    assert fresh["data"]["review_index"] == 1
    assert not fresh["data"]["reading"]
    assert not db.all("SELECT id FROM video_projects")


def test_selected_paper_reuses_reading_and_snapshotted_model(database):
    run = raw_run()
    pid = papers.register(meta("2610.00002"))
    candidate = meta("2610.00002") | {
        "paper_id": pid,
        "area": "llm",
        "attention": None,
        "retrieval_score": 20,
        "reading_seconds": 10,
        "notes": [
            {
                "claim": "A qualified claim from the complete paper",
                "topic": "mechanism",
                "source_ids": ["s1"],
            }
        ],
        "assessment": {
            "suitable": True,
            "content_quality": 4,
            "story_value": 4,
            "why_ja": "本文の根拠から選びました",
            "source_ids": ["s1"],
        },
    }
    run["data"].update(phase="choose", reading=[candidate], model="qwen-q6")
    nightly.save(run)
    job = db.one("SELECT * FROM jobs WHERE target=?", (run["id"],))
    nightly.step(job, None)
    fresh = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run["id"],))
    project = db.one("SELECT * FROM video_projects WHERE id=?", (fresh["project_id"],))
    assert fresh["state"] == "building"
    assert project["data"]["model"] == "qwen-q6"
    assert project["data"]["reading_complete"]
    assert project["data"]["evidence"][0]["source_ids"] == ["s1"]
    assert project["data"]["nightly_run_id"] == run["id"]
    assert (
        len(
            db.all(
                "SELECT * FROM lessons WHERE json_extract(data,'$.format')='paper-story-1'"
            )
        )
        == 2
    )


def test_nightly_api_idempotence_and_pause_resume_children(client):
    a = client.post("/api/nightly-video-runs").json()
    b = client.post("/api/nightly-video-runs").json()
    assert a["id"] == b["id"]
    root = story.create(papers.register(meta("2610.00002")))
    db.execute(
        "UPDATE nightly_video_runs SET project_id=? WHERE id=?",
        (root["project_id"], a["id"]),
    )
    stamp = time.time()
    db.execute(
        "INSERT INTO thumbnail_sets VALUES (?,?,?,?,?,?,?,?)",
        ("ts", root["project_id"], "overview", "x", "building", "{}", stamp, stamp),
    )
    thumbjob = db.enqueue("thumbnail", "ts")
    for action, state in [("pause", "paused"), ("resume", "queued")]:
        assert client.post(f"/api/jobs/{a['job']['id']}/{action}").status_code == 200
        for jid in [root["job_id"], thumbjob]:
            assert db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == state
    assert (
        client.get("/api/nightly-video-runs").json()[0]["project_id"]
        == root["project_id"]
    )


def test_failed_thumbnail_does_not_wait_forever(database):
    run = raw_run()
    root = story.create(papers.register(meta("2610.00002")))
    run["project_id"] = root["project_id"]
    run["data"].update(
        phase="production",
        production_started=time.time(),
        thumbnail_retries={"overview": 3},
    )
    nightly.save(run)
    stamp = time.time()
    db.execute(
        "INSERT INTO thumbnail_sets VALUES (?,?,?,?,?,?,?,?)",
        (
            "ts",
            root["project_id"],
            "overview",
            "x",
            "failed",
            db.dumps({"error": "font missing", "candidates": []}),
            stamp,
            stamp,
        ),
    )
    job = db.one("SELECT * FROM jobs WHERE target=?", (run["id"],))
    assert nightly.step(job, None)
    failed = nightly.get(run["id"])
    assert failed["state"] == "failed"
    assert "font missing" in failed["data"]["reason"]


def test_nightly_settings_validation_and_legacy_clients_preserve_new_setting(client):
    settings = client.get("/api/settings").json()
    settings.update(
        nightly_video_enabled=True, nightly_video_hour=2, nightly_video_minute=0
    )
    assert client.put("/api/settings", json=settings).status_code == 200
    legacy = {k: v for k, v in settings.items() if not k.startswith("nightly_video_")}
    assert client.put("/api/settings", json=legacy).json()["nightly_video_enabled"]
    settings["nightly_video_categories"] = ["evil.code"]
    assert client.put("/api/settings", json=settings).status_code == 422
    settings["nightly_video_hour"] = 24
    assert client.put("/api/settings", json=settings).status_code == 422


def test_reading_retains_html_equations_tables_and_captions_without_duplicate_pdf(
    database,
):
    pid = papers.register(meta("2610.00002"))
    for kind in ["text", "equation", "table", "figure", "page"]:
        db.execute(
            "INSERT INTO sources VALUES (?,?,?,?)",
            (kind, pid, kind, db.dumps({"text": "source " + kind})),
        )
    assert [s["kind"] for s in papers.reading_sources(pid)] == [
        "text",
        "equation",
        "table",
        "figure",
    ]
    db.execute("DELETE FROM sources WHERE kind!='page'")
    assert [s["kind"] for s in nightly._sources(pid)] == ["page"]


def test_widening_search_does_not_read_the_same_unsuitable_papers_again(database):
    run = raw_run()
    already = meta("2610.00002") | {"assessment": {"suitable": False}}
    run["data"].update(shortlist=[already], reading=[already], review_index=1)
    assert not nightly._expand_or_skip(run)
    assert run["data"]["excluded_ids"] == ["2610.00002"]
    assert run["data"]["reviewed_papers"][0]["assessment"]["suitable"] is False
    candidates = [meta("2610.00002"), meta("2609.00003", days=20)]
    result = nightly.shortlist(
        candidates, {}, now=NOW, days=30, excluded=run["data"]["excluded_ids"]
    )
    assert [c["source_id"] for c in result] == ["2609.00003"]


def test_cancelled_project_is_not_retried_as_a_generation_failure(database):
    run = raw_run()
    root = story.create(papers.register(meta("2610.00002")))
    run["project_id"] = root["project_id"]
    run["data"].update(phase="production", production_started=time.time())
    nightly.save(run)
    db.patch_job(root["job_id"], state="cancelled")
    job = db.one("SELECT * FROM jobs WHERE target=?", (run["id"],))
    assert nightly.step(job, None)
    assert nightly.get(run["id"])["state"] == "cancelled"
    assert (
        db.one("SELECT state FROM jobs WHERE id=?", (job["id"],))["state"]
        == "cancelled"
    )
    assert (
        nightly.get(nightly.start(now=NOW + dt.timedelta(days=1)))["state"]
        == "searching"
    )
