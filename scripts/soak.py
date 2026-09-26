#!/usr/bin/env python3
"""Resumable 72-hour local health observation, with explicit gaps and failures."""

import argparse
import fcntl
import json
import os
import time

import httpx
from paperspeak import config, db

p = argparse.ArgumentParser()
p.add_argument("--hours", type=float, default=72)
p.add_argument("--interval", type=int, default=60)
p.add_argument(
    "--new-run",
    action="store_true",
    help="Archive the previous observation before starting a new one",
)
a = p.parse_args()
db.init()
lock = (config.DATA / "soak.lock").open("a")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("A PaperSpeak observation is already running.")
folder = config.DATA / "evaluation"
report = folder / "soak-report.json"
events = folder / "soak-samples.jsonl"
if a.new_run and (report.exists() or events.exists()):
    archive = folder / "soak-history" / str(time.time_ns())
    archive.mkdir(parents=True)
    for path in [report, events]:
        if path.exists():
            path.replace(archive / path.name)
    print(f"Previous observation preserved in {archive}", flush=True)
state = (
    json.loads(report.read_text())
    if report.exists()
    else {
        "started": time.time(),
        "duration_hours": a.hours,
        "samples": 0,
        "failures": 0,
        "gaps": [],
        "last": None,
        "status": "running",
    }
)
state["scope"] = (
    "Service availability and persisted workflow observation; not a physical LAN microphone or teaching-quality acceptance test."
)
state.setdefault("job_failures", {})
end = state["started"] + state["duration_hours"] * 3600
while time.time() < end:
    now = time.time()
    sample = {"time": now}
    if state["last"] and now - state["last"] > a.interval * 2.5:
        state["gaps"].append({"from": state["last"], "to": now})
    try:
        response = httpx.get(f"http://127.0.0.1:{config.API_PORT}/health", timeout=10)
        response.raise_for_status()
        worker = db.one("SELECT * FROM cursors WHERE key='worker'")
        sample["worker_age"] = now - worker["data"]["heartbeat"] if worker else None
        sample["healthy"] = (
            sample["worker_age"] is not None and sample["worker_age"] < 90
        )
        sample["jobs"] = db.all(
            "SELECT kind,state,COUNT(*) AS count FROM jobs GROUP BY kind,state"
        )
        for job in db.all("SELECT id,kind,stage,error FROM jobs WHERE state='failed'"):
            state["job_failures"].setdefault(job["id"], job | {"observed": now})
        days = db.all(
            "SELECT DISTINCT json_extract(checkpoint,'$.day') AS day FROM jobs "
            "WHERE kind='discover' AND state='completed' AND updated>=?",
            (state["started"],),
        )
        state["workflow"] = {
            "discovery_completed_days": [d["day"] for d in days if d["day"]],
            "ready_chapters": db.one(
                "SELECT count(*) n FROM chapters WHERE state='ready'"
            )["n"],
            "ready_lessons": db.one(
                "SELECT count(*) n FROM lessons WHERE state='ready'"
            )["n"],
            "automatic_papers_selected": db.one(
                "SELECT count(DISTINCT paper_id) n FROM recommendations WHERE state='selected'"
            )["n"],
        }
        sample["disk_free_gib"] = (
            os.statvfs(config.DATA).f_bavail * os.statvfs(config.DATA).f_frsize / 2**30
        )
    except Exception as e:
        sample.update(healthy=False, error=str(e))
    with events.open("a") as f:
        f.write(json.dumps(sample) + "\n")
    state["samples"] += 1
    state["failures"] += not sample["healthy"]
    state["last"] = now
    tmp = report.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(report)
    time.sleep(min(a.interval, max(0, end - time.time())))
if not state["last"] or end - state["last"] > a.interval * 2.5:
    state["gaps"].append({"from": state["last"], "to": end})
state["finished"] = time.time()
state["service_status"] = (
    "passed" if state["failures"] == 0 and not state["gaps"] else "needs_review"
)
state["status"] = (
    "passed"
    if state["service_status"] == "passed" and not state["job_failures"]
    else "needs_review"
)
report.write_text(json.dumps(state, indent=2))
