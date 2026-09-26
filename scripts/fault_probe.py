#!/usr/bin/env python3
"""Explicit local worker crash/recovery acceptance probe, for this studio only."""

import argparse
import json
import os
import signal
import time
from pathlib import Path

from paperspeak import config, db

p = argparse.ArgumentParser()
p.add_argument("--kill-owned-worker", action="store_true")
a = p.parse_args()
if not a.kill_owned_worker:
    raise SystemExit("Pass --kill-owned-worker to run the interruption test.")
db.init()
before = db.one("SELECT * FROM cursors WHERE key='worker'")["data"]
pid = before["pid"]
cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
if b"paperspeak.worker" not in cmd:
    raise SystemExit("The recorded process is not our worker.")
job = db.one("SELECT * FROM jobs WHERE state='running'")
record = {
    "started": time.time(),
    "old_worker_pid": pid,
    "job": job["id"] if job else None,
    "stage_before": job["stage"] if job else None,
    "checkpoint_before": job["checkpoint"] if job else None,
}
os.kill(pid, signal.SIGKILL)
print("Stopped the owned worker to test recovery.", flush=True)
for _ in range(180):
    current = db.one("SELECT * FROM cursors WHERE key='worker'")["data"]
    resumed = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) if job else None
    if (
        current["pid"] != pid
        and time.time() - current["heartbeat"] < 20
        and (
            not resumed
            or resumed["state"] != "running"
            or resumed["owner"] != job["owner"]
        )
    ):
        record.update(
            finished=time.time(),
            new_worker_pid=current["pid"],
            stage_after=resumed["stage"] if resumed else None,
            state_after=resumed["state"] if resumed else None,
            passed=True,
        )
        break
    time.sleep(1)
else:
    record.update(finished=time.time(), passed=False)
(config.DATA / "evaluation/worker-crash-recovery.json").write_text(
    json.dumps(record, indent=2)
)
print({k: v for k, v in record.items() if k != "checkpoint_before"})
if not record["passed"]:
    raise SystemExit(1)
