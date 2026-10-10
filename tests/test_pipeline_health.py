import datetime as dt
import os
import select
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from paperspeak import config, db, nightly, papers, pipeline_health, story, worker


def project():
    paper = papers.register(
        {"source_id": "health-check", "version": "v1", "title": "A paper"}
    )
    info = story.create(paper, modes=["overview"])
    return db.one("SELECT * FROM video_projects WHERE id=?", (info["project_id"],))


def export(p, state="queued"):
    track = p["data"]["modes"]["overview"]
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "film",
            track["lesson_id"],
            "",
            "overview",
            "digest",
            state,
            "{}",
            time.time(),
            time.time(),
        ),
    )
    p["data"].update(phase="production", current_mode="overview")
    track.update(phase="export", export_id="film")
    story.save(p)
    return track


def test_schedule_failure_cannot_prevent_other_work_and_is_reported(database):
    def broken():
        raise ValueError("Bad archived day")

    def healthy():
        db.enqueue("practice", "recording", priority=0)

    errors = worker.scheduled_tasks([("broken", broken), ("healthy", healthy)])
    assert errors[0]["unit"] == "broken"
    assert db.claim("live")["target"] == "recording"
    assert db.one("SELECT data FROM cursors WHERE key='scheduler-health'")["data"][
        "errors"
    ]


def test_restart_restores_fresh_dead_owner_without_waiting_or_losing_checkpoint(
    database,
):
    jid = db.enqueue("nightly_video", "one")
    db.claim("dead-worker")
    db.patch_job(jid, checkpoint={"reading_unit": 7, "_failures": 1})
    held = db.enqueue("lesson", "old")
    db.patch_job(held, state="paused")
    pipeline_health.recover_interrupted("new-worker")
    recovered = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert recovered["state"] == "queued" and recovered["owner"] is None
    assert recovered["checkpoint"] == {"reading_unit": 7, "_failures": 1}
    assert db.one("SELECT state FROM jobs WHERE id=?", (held,))["state"] == "paused"
    voice = db.enqueue("practice", "voice", priority=0)
    assert db.claim("new-worker")["id"] == voice


def test_graceful_restart_does_not_keep_a_shutdown_message_while_resuming(database):
    jid = db.enqueue("video_project", "saved-film")
    db.patch_job(
        jid,
        state="queued",
        stage="Saving the checkpoint for service restart.",
        checkpoint={"scene": 3},
        progress=0.4,
        available=time.time() - 1,
    )
    waiting = db.enqueue("video_review", "not-yet-ready")
    db.patch_job(waiting, stage="2本の動画の完成を待っています")
    pipeline_health.recover_interrupted("new-worker")
    claimed = db.claim("new-worker")
    assert claimed["id"] == jid
    assert claimed["stage"] == "Resuming after worker restart"
    assert claimed["checkpoint"] == {"scene": 3}
    assert claimed["progress"] == 0.4
    assert (
        db.one("SELECT stage FROM jobs WHERE id=?", (waiting,))["stage"]
        == "2本の動画の完成を待っています"
    )


@pytest.mark.parametrize("interrupted_state", ["running", "queued"])
def test_restart_preserves_stop_intent_if_crash_interrupts_cascade(
    database, interrupted_state
):
    p = project()
    export(p)
    child = db.enqueue("story_video", "film", priority=0)
    assert db.claim("dead")["id"] == child
    pipeline_health.control(p["id"], "paused")
    db.patch_job(child, state=interrupted_state)
    pipeline_health.recover_interrupted("new")
    assert db.one("SELECT state FROM jobs WHERE id=?", (child,))["state"] == "paused"
    pipeline_health.reconcile()
    assert db.one("SELECT state FROM jobs WHERE id=?", (child,))["state"] == "paused"


def test_dispatch_guard_respects_incomplete_user_stop_before_next_maintenance(database):
    p = project()
    jid = db.one(
        "SELECT id FROM jobs WHERE kind='video_project' AND target=?", (p["id"],)
    )["id"]
    pipeline_health.control(p["id"], "cancelled")
    claimed = db.claim("worker")
    assert claimed["id"] == jid
    assert pipeline_health.stop_requested(claimed) == "cancelled"


def test_missing_controller_is_rebuilt_once_and_keeps_saved_content(database):
    p = project()
    saved = p["data"]
    saved["modes"]["overview"]["scenes"] = [
        {"utterances": [{"audio": "audio/keep.wav"}], "visual_ready": True}
    ]
    (database / "audio/keep.wav").write_bytes(b"existing voice")
    story.save(p)
    db.execute("DELETE FROM jobs WHERE kind='video_project' AND target=?", (p["id"],))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: pipeline_health.reconcile(), range(8)))
    jobs = db.all(
        "SELECT id FROM jobs WHERE kind='video_project' AND target=?", (p["id"],)
    )
    assert len(jobs) == 1
    assert (
        db.one("SELECT data FROM video_projects WHERE id=?", (p["id"],))["data"]
        == saved
    )
    assert (database / "audio/keep.wav").read_bytes() == b"existing voice"
    assert (
        len(
            db.one("SELECT data FROM cursors WHERE key='pipeline-health'")["data"][
                "history"
            ]
        )
        == 1
    )


def test_render_job_recovery_reuses_encoded_parts_and_skips_obsolete_exports(database):
    p = project()
    export(p)
    parts = database / "jobs/story-video-film"
    parts.mkdir()
    (parts / "part-000.mp4").write_bytes(b"saved first minute")
    pipeline_health.reconcile()
    jobs = db.all("SELECT * FROM jobs WHERE kind='story_video' AND target='film'")
    assert len(jobs) == 1 and jobs[0]["state"] == "queued"
    assert (parts / "part-000.mp4").read_bytes() == b"saved first minute"
    db.patch_job(jobs[0]["id"], state="cancelled", checkpoint={"superseded": True})
    pipeline_health.reconcile()
    assert (
        db.one("SELECT state FROM jobs WHERE id=?", (jobs[0]["id"],))["state"]
        == "cancelled"
    )


def test_failed_artifact_state_is_reconciled_so_parent_repair_can_advance(database):
    p = project()
    export(p)
    child = db.enqueue("story_video", "film")
    db.patch_job(
        child, state="failed", error="Encoder failed", checkpoint={"_failures": 3}
    )
    pipeline_health.reconcile()
    e = db.one("SELECT * FROM video_exports WHERE id='film'")
    assert e["state"] == "failed" and e["data"]["error"] == "Encoder failed"
    assert db.one("SELECT state FROM jobs WHERE id=?", (child,))["state"] == "failed"


def test_failed_controller_resumes_only_after_dependency_changes(database):
    p = project()
    export(p, "failed")
    jid = db.one(
        "SELECT id FROM jobs WHERE kind='video_project' AND target=?", (p["id"],)
    )["id"]
    cp = {
        "_failures": 3,
        "failed_dependency": pipeline_health.failed_dependency(
            "video_project", p["id"]
        ),
    }
    db.patch_job(
        jid, state="failed", checkpoint=cp, error="Local video export could not finish"
    )
    pipeline_health.reconcile()
    assert db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "failed"
    db.execute("UPDATE video_exports SET state='ready' WHERE id='film'")
    pipeline_health.reconcile()
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert (
        job["state"] == "queued"
        and len(job["checkpoint"]["auto_recovery"]["history"]) == 1
    )
    db.patch_job(
        jid,
        state="failed",
        checkpoint=job["checkpoint"],
        error="Same dependency problem",
    )
    assert not pipeline_health.reconcile()["repaired"]
    assert db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "failed"


def test_new_failure_with_already_ready_dependency_does_not_retry_unchanged_input(
    database,
):
    p = project()
    export(p, "ready")
    jid = db.one(
        "SELECT id FROM jobs WHERE kind='video_project' AND target=?", (p["id"],)
    )["id"]
    db.patch_job(
        jid,
        state="failed",
        checkpoint={
            "failed_dependency": pipeline_health.failed_dependency(
                "video_project", p["id"]
            )
        },
    )
    assert not pipeline_health.reconcile()["repaired"]
    assert db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "failed"


@pytest.mark.parametrize("state", ["paused", "cancelled"])
def test_user_control_survives_missing_job_and_stale_project_save(database, state):
    p = project()
    nightly.control_project(p["id"], state)
    story.save(p)  # an old generator saves its earlier snapshot
    db.execute("DELETE FROM jobs WHERE kind='video_project' AND target=?", (p["id"],))
    assert not pipeline_health.reconcile()["repaired"]
    assert not db.all(
        "SELECT id FROM jobs WHERE kind='video_project' AND target=?", (p["id"],)
    )


def test_health_endpoint_is_read_only(client, monkeypatch):
    monkeypatch.setattr(
        pipeline_health,
        "reconcile",
        lambda: (_ for _ in ()).throw(AssertionError("GET must not enqueue work")),
    )
    before = db.all("SELECT id FROM jobs")
    result = client.get("/api/pipeline-health")
    assert result.status_code == 200 and "scheduler-health" in result.json()
    assert db.all("SELECT id FROM jobs") == before


def test_missing_nightly_job_is_recovered_in_the_existing_day_slot(database):
    ident = nightly.start(now=dt.datetime(2026, 10, 6, 2, tzinfo=nightly.ZONE))
    before = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    db.execute("DELETE FROM jobs WHERE kind='nightly_video' AND target=?", (ident,))
    pipeline_health.reconcile()
    jobs = db.all(
        "SELECT * FROM jobs WHERE kind='nightly_video' AND target=?", (ident,)
    )
    assert len(jobs) == 1 and jobs[0]["state"] == "queued"
    assert db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,)) == before


def test_nightly_failure_waits_for_thumbnail_then_reconciles_completion(database):
    p = project()
    p["state"] = "ready"
    story.save(p)
    ident = nightly.start(now=dt.datetime(2026, 10, 6, 2, tzinfo=nightly.ZONE))
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    run["state"] = "failed"
    run["project_id"] = p["id"]
    run["data"].update(phase="production", reason="Thumbnail error")
    nightly.save(run)
    job = db.one(
        "SELECT id FROM jobs WHERE target=? AND kind='nightly_video'", (ident,)
    )["id"]
    db.patch_job(job, state="failed")
    db.execute(
        "INSERT INTO thumbnail_sets VALUES (?,?,?,?,?,?,?,?)",
        (
            "thumb",
            p["id"],
            "overview",
            "digest",
            "failed",
            "{}",
            time.time(),
            time.time(),
        ),
    )
    assert not pipeline_health.reconcile()["repaired"]
    assert db.one("SELECT state FROM jobs WHERE id=?", (job,))["state"] == "failed"
    db.execute("UPDATE thumbnail_sets SET state='ready' WHERE id='thumb'")
    pipeline_health.reconcile()
    assert db.one("SELECT state FROM jobs WHERE id=?", (job,))["state"] == "queued"
    assert (
        db.one("SELECT state FROM nightly_video_runs WHERE id=?", (ident,))["state"]
        == "building"
    )


def test_automatic_recovery_budget_is_bounded_even_with_changing_phases(database):
    p = project()
    job = db.one(
        "SELECT id FROM jobs WHERE target=? AND kind='video_project'", (p["id"],)
    )["id"]
    for phase in ("sources", "background", "plan"):
        p["data"]["phase"] = phase
        story.save(p)
        db.patch_job(job, state="completed")
        pipeline_health.reconcile()
        assert db.one("SELECT state FROM jobs WHERE id=?", (job,))["state"] == "queued"
    p["data"]["phase"] = "production"
    story.save(p)
    db.patch_job(job, state="completed")
    result = pipeline_health.reconcile()
    assert not result["repaired"] and result["issues"]
    assert (
        len(
            db.one("SELECT checkpoint FROM jobs WHERE id=?", (job,))["checkpoint"][
                "auto_recovery"
            ]["history"]
        )
        == 3
    )


def test_actual_worker_kill_and_restart_preserves_work_and_paused_jobs(database):
    db.set_setting("discovery_enabled", False)
    db.set_setting("nightly_video_enabled", False)
    jid = db.enqueue("nightly_video", "restart-test", priority=12)
    paused = db.enqueue("lesson", "old-lesson")
    db.patch_job(paused, state="paused")
    saved = database / "audio" / "kept.wav"
    saved.write_bytes(b"previously generated voice")
    code = """
import sys,time
from paperspeak import worker,db
class FakeRuntime:
    mode=None
    last_used=0
    shutdown_requested=False
    def close(self): pass
worker.Runtime=FakeRuntime
def step(job,runtime):
    if sys.argv[1]=='first':
        db.patch_job(job['id'],checkpoint={'reading_unit':7,'audio':'audio/kept.wav'})
        print('checkpointed',flush=True)
        time.sleep(1000)
    else:
        assert job['checkpoint']['reading_unit']==7
        db.patch_job(job['id'],checkpoint=job['checkpoint']|{'reading_unit':8})
        return True
worker.nightly.step=step
worker.run()
"""
    env = os.environ | {
        "PAPERSPEAK_DATA": str(database),
        "PYTHONPATH": str(config.ROOT / "backend"),
    }
    first = subprocess.Popen(
        [sys.executable, "-u", "-c", code, "first"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = None
    try:
        assert select.select([first.stdout], [], [], 15)[0]
        assert first.stdout.readline().strip() == "checkpointed"
        before = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        assert before["state"] == "running" and before["heartbeat"] > time.time() - 20
        first.kill()
        first.wait(timeout=5)
        second = subprocess.Popen(
            [sys.executable, "-u", "-c", code, "second"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (
                db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"]
                == "completed"
            ):
                break
            assert second.poll() is None
            time.sleep(0.1)
        job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        assert job["state"] == "completed" and job["checkpoint"]["reading_unit"] == 8
        assert (
            db.one("SELECT state FROM jobs WHERE id=?", (paused,))["state"] == "paused"
        )
        assert saved.read_bytes() == b"previously generated voice"
        assert len(db.all("SELECT id FROM jobs WHERE target='restart-test'")) == 1
        assert (
            db.one("SELECT data FROM cursors WHERE key='worker-recovery'")["data"][
                "jobs"
            ][0]["job_id"]
            == jid
        )
    finally:
        for proc in (first, second):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
