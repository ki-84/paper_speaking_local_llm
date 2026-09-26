#!/usr/bin/env python3
"""Exercise a real ready chapter through HTTPS and the GPU worker.

Uses the synthesized reference as a test input, not a human pronunciation sample.
Saves the complete result, then removes only the attempts/reviews created here.
"""

import argparse
import json
import ssl
import time
import uuid

import httpx
from paperspeak import config, db

p = argparse.ArgumentParser()
p.add_argument("--url", default="https://192.168.10.112:8443")
p.add_argument("--lesson", required=True)
p.add_argument(
    "--wait",
    type=int,
    default=0,
    help="Wait up to this many seconds for a ready chapter",
)
a = p.parse_args()
credentials = json.loads((config.DATA / "credentials.json").read_text())
context = ssl.create_default_context(
    cafile=str(config.DATA / "tls/pki/authorities/local/root.crt")
)
report = {
    "started": time.time(),
    "lesson_id": a.lesson,
    "input": "Synthesized reference audio; integration check only, not pronunciation accuracy or human microphone validation.",
    "results": [],
}
output = config.DATA / "evaluation/real-pipeline.json"
with httpx.Client(
    base_url=a.url,
    verify=context,
    headers={"Authorization": "Bearer " + credentials["api_key"]},
    timeout=30,
) as client:

    def get(path):
        for retry in range(6):
            try:
                response = client.get(path)
                response.raise_for_status()
                return response.json()
            except (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.HTTPStatusError,
            ) as error:
                if retry == 5 or (
                    isinstance(error, httpx.HTTPStatusError)
                    and error.response.status_code < 500
                ):
                    raise
                report["connection_retries"] = report.get("connection_retries", 0) + 1
                time.sleep(2)

    deadline = time.monotonic() + a.wait
    while True:
        lesson = get("/api/lessons/" + a.lesson)
        chapter = next((c for c in lesson["chapters"] if c["state"] == "ready"), None)
        if chapter:
            break
        job = db.one(
            "SELECT state,stage,error FROM jobs WHERE kind='lesson' AND target=? ORDER BY created DESC LIMIT 1",
            (a.lesson,),
        )
        if job and job["state"] in {"failed", "cancelled", "paused"}:
            raise RuntimeError(
                f"Lesson is {job['state']}: {job['error'] or job['stage']}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError("No verified chapter is ready yet.")
        time.sleep(min(10, max(0, deadline - time.monotonic())))
    turn = next(t for t in chapter["data"]["turns"] if t["kind"] == "paper")
    response = client.get("/api/files/" + turn["audio"])
    response.raise_for_status()
    sample = response.content
    for kind in ["read", "answer"]:
        target = turn["id"] if kind == "read" else chapter["data"]["questions"][0]["id"]
        rid = chapter["id"] + ":" + target
        prior_review = db.one("SELECT id FROM reviews WHERE id=?", (rid,))
        data = {
            "chapter_id": chapter["id"],
            "client_id": str(uuid.uuid4()),
            "turn_id" if kind == "read" else "question_id": target,
        }
        response = client.post(
            "/api/attempts",
            data=data,
            files={"file": ("integration.wav", sample, "audio/wav")},
        )
        response.raise_for_status()
        queued = response.json()
        duplicate = client.post(
            "/api/attempts",
            data=data,
            files={"file": ("integration.wav", sample, "audio/wav")},
        )
        duplicate.raise_for_status()
        assert duplicate.json()["attempt_id"] == queued["attempt_id"]
        started = time.time()
        print(f"Queued real {kind} pipeline", flush=True)
        while time.time() - started < 900:
            attempt = get("/api/attempts/" + queued["attempt_id"])
            if attempt["state"] != "pending":
                break
            job = db.one("SELECT state,error FROM jobs WHERE id=?", (queued["job_id"],))
            if job["state"] == "failed":
                raise RuntimeError(job["error"])
            time.sleep(2)
        report["results"].append(
            {"kind": kind, "seconds": time.time() - started, "attempt": attempt}
        )
        output.write_text(json.dumps(report, indent=2))
        assert attempt["state"] == "ready", attempt["state"]
        result = attempt["data"]
        assert (
            result["transcript"] and result["timestamps"] and result["model_manifest"]
        )
        if kind == "read":
            assert result["word_match"] >= 92
            assert result["pronunciation"]["status"] == "estimated"
            assert result["stress"]["status"] == "estimated"
        else:
            assert all(
                result["comprehension"].get(k)
                for k in [
                    "understanding",
                    "content_feedback",
                    "english_feedback",
                    "next_step",
                ]
            )
        # The answer is intentionally the same recording as the read test: its
        # correctness is not assumed. We verify separated feedback, not its grade.
        response = client.delete("/api/attempts/" + queued["attempt_id"])
        response.raise_for_status()
        db.execute(
            "DELETE FROM jobs WHERE id=? AND state='completed'", (queued["job_id"],)
        )
        if not prior_review and not db.one(
            "SELECT id FROM attempts WHERE chapter_id=? AND (turn_id=? OR json_extract(data,'$.question_id')=?)",
            (chapter["id"], target, target),
        ):
            db.execute("DELETE FROM reviews WHERE id=?", (rid,))
        print(f"Passed real {kind} pipeline; test history cleaned up", flush=True)
report.update(finished=time.time(), status="passed")
output.write_text(json.dumps(report, indent=2))
print(output)
