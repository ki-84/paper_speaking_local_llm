from __future__ import annotations

import fcntl
import logging
import os
import signal
import threading
import time

from . import (
    awards,
    benchmark,
    calibration,
    config,
    db,
    discovery,
    lessons,
    local_network,
    nightly,
    paper_search,
    papers,
    phoneme_probe,
    practice,
    recommendation_ja,
    revoice,
    story,
    story_video,
    thumbnails,
    translation,
    video,
    youtube,
)
from .quality import QualityHold
from .runtime import GPUUnavailable, PracticePreempted, Runtime

log = logging.getLogger("paperspeak.worker")


def import_step(job):
    pid = papers.register_arxiv(job["payload"]["reference"])
    papers.ingest(pid)
    checkpoint = {"paper_id": pid}
    if job["payload"].get("create_video"):
        checkpoint["project_id"] = story.create(pid)["project_id"]
    elif job["payload"].get("generate", True):
        db.enqueue("lesson", lessons.create(pid))
    db.patch_job(job["id"], checkpoint=checkpoint, progress=1, stage="Paper added")
    return True


def run():
    db.init()
    owner = db.uid()
    runtime = Runtime()
    stopped = threading.Event()
    lock = open(config.DATA / "worker.lock", "w")
    step_lock = open(config.DATA / "work-step.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("A PaperSpeak worker is already running.")

    def stop(*_):
        stopped.set()
        runtime.shutdown_requested = True
        runtime.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def heartbeat():
        while not stopped.wait(10):
            db.execute(
                "UPDATE jobs SET heartbeat=? WHERE owner=? AND state='running'",
                (time.time(), owner),
            )
            db.execute(
                "INSERT INTO cursors VALUES ('worker',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                (
                    db.dumps(
                        {
                            "pid": os.getpid(),
                            "heartbeat": time.time(),
                            "model": runtime.mode,
                        }
                    ),
                ),
            )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    translation.schedule_backfill()
    recommendation_ja.schedule()
    revoice.schedule()
    video.schedule()
    last_schedule = 0
    try:
        while not stopped.is_set():
            if time.time() - last_schedule > 60:
                discovery.schedule()
                nightly.schedule()
                recommendation_ja.schedule()
                revoice.schedule()
                video.schedule()
                last_schedule = time.time()
            job = db.claim(owner)
            if not job:
                if runtime.mode and time.monotonic() - runtime.last_used > 180:
                    runtime.close()
                stopped.wait(1)
                continue
            try:
                fcntl.flock(step_lock, fcntl.LOCK_EX)
                current = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
                if not current or current["state"] != "running" or stopped.is_set():
                    continue
                runtime.job_id = job["id"]
                runtime.job_target = job["target"]
                runtime.job_kind = job["kind"]
                if job["kind"] == "lesson":
                    done = lessons.lesson_step(job, runtime)
                elif job["kind"] == "video_project":
                    project = db.one(
                        "SELECT data FROM video_projects WHERE id=?", (job["target"],)
                    )
                    if project and project["data"]["phase"] != "sources":
                        with local_network.inference_only():
                            done = story.step(job, runtime)
                    else:
                        done = story.step(job, runtime)
                elif job["kind"] == "story_video":
                    with local_network.inference_only():
                        done = story_video.step(job, runtime)
                elif job["kind"] == "thumbnail":
                    with local_network.inference_only():
                        done = thumbnails.step(job, runtime)
                elif job["kind"] == "nightly_video":
                    done = nightly.step(job, runtime)
                elif job["kind"] == "award_refresh":
                    done = awards.refresh_step(job, runtime)
                elif job["kind"] == "discover":
                    done = discovery.discovery_step(job, runtime)
                elif job["kind"] == "practice":
                    done = practice.practice_step(job, runtime)
                elif job["kind"] == "translate":
                    done = translation.translation_step(job, runtime)
                elif job["kind"] == "paper_search":
                    done = paper_search.step(job, runtime)
                elif job["kind"] == "recommendation_ja":
                    done = recommendation_ja.step(job, runtime)
                elif job["kind"] == "revoice":
                    done = revoice.step(job, runtime)
                elif job["kind"] == "benchmark":
                    done = benchmark.benchmark_step(job, runtime)
                elif job["kind"] == "phoneme_probe":
                    done = phoneme_probe.probe_step(job, runtime)
                elif job["kind"] == "calibration":
                    done = calibration.calibration_step(job, runtime)
                elif job["kind"] in {"chapter_video", "full_video"}:
                    done = video.video_step(job, runtime)
                elif job["kind"] == "youtube_upload":
                    done = youtube.upload_step(job, runtime)
                elif job["kind"] == "import":
                    done = import_step(job)
                else:
                    raise ValueError("Unknown job type")
                current = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
                if current["state"] == "running":
                    cp = current["checkpoint"]
                    cp.pop("_failures", None)
                    db.patch_job(
                        job["id"],
                        state="completed" if done else "queued",
                        owner=None,
                        checkpoint=cp,
                        error=None,
                    )
            except PracticePreempted as e:
                current = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
                if current and current["state"] == "running":
                    db.patch_job(
                        job["id"],
                        state="queued",
                        stage=str(e),
                        available=time.time() + 1,
                        owner=None,
                    )
            except GPUUnavailable as e:
                current = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
                if current and current["state"] == "running":
                    db.patch_job(
                        job["id"],
                        state="queued",
                        stage=str(e),
                        available=time.time() + 20,
                        owner=None,
                    )
            except Exception as e:
                log.exception("Job %s step failed", job["id"])
                current = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
                if (
                    stopped.is_set()
                    or not current
                    or current["state"] in {"paused", "cancelled"}
                ):
                    continue
                cp = current["checkpoint"]
                failures = cp.get("_failures", 0) + 1
                cp["_failures"] = failures
                # A chapter has already exhausted its own bounded repair loop,
                # or a repeated step error cannot be repaired here. Preserve
                # its checkpoint and continue with the next chapter.
                if job["kind"] == "lesson" and (
                    isinstance(e, QualityHold) or failures >= 3
                ):
                    held_number = lessons.hold_current_chapter(job["target"], e)
                    if held_number is not None:
                        cp.pop("_failures", None)
                        db.patch_job(
                            job["id"],
                            state="queued",
                            owner=None,
                            checkpoint=cp,
                            error=None,
                            available=0,
                            stage=f"Chapter {held_number} needs attention; continuing with the next chapter",
                        )
                        continue
                state = (
                    "failed"
                    if isinstance(e, QualityHold) or failures >= 3
                    else "queued"
                )
                db.patch_job(
                    job["id"],
                    state=state,
                    checkpoint=cp,
                    error=str(e)[:1200],
                    available=time.time() + min(120, 10 * failures),
                    owner=None,
                )
                if state == "failed" and job["kind"] == "practice":
                    db.execute(
                        "UPDATE attempts SET state='failed' WHERE id=?",
                        (job["target"],),
                    )
                    db.event("attempt", {"id": job["target"]})
                if state == "failed" and job["kind"] == "thumbnail":
                    row = db.one(
                        "SELECT * FROM thumbnail_sets WHERE id=?", (job["target"],)
                    )
                    if row:
                        row["state"] = "failed"
                        row["data"]["error"] = str(e)[:1200]
                        thumbnails.save(row)
                if state == "failed" and job["kind"] == "nightly_video":
                    row = db.one(
                        "SELECT * FROM nightly_video_runs WHERE id=?", (job["target"],)
                    )
                    if row:
                        row["state"] = "failed"
                        row["data"].update(reason=str(e)[:1200], finished=time.time())
                        nightly.save(row)
                if state == "failed" and job["kind"] in {
                    "chapter_video",
                    "full_video",
                    "story_video",
                }:
                    export = db.one(
                        "SELECT * FROM video_exports WHERE id=?", (job["target"],)
                    )
                    if export:
                        video._set_export(export, "failed", error=str(e)[:1200])
            finally:
                runtime.job_kind = None
                runtime.job_id = None
                runtime.job_target = None
                fcntl.flock(step_lock, fcntl.LOCK_UN)
    finally:
        stopped.set()
        runtime.close()
        db.execute(
            "UPDATE jobs SET state='queued',owner=NULL WHERE owner=? AND state='running'",
            (owner,),
        )
        lock.close()
        step_lock.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    run()
