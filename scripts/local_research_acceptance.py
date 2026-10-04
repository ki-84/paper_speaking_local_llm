"""Verify the real local reader through the production worker, without making a film.

PYTHONPATH=backend .venv/bin/python scripts/local_research_acceptance.py
"""

import datetime as dt
import json
import time

from paperspeak import awards, config, db, research


def preserved_state():
    return {
        "projects": sorted(r["id"] for r in db.all("SELECT id FROM video_projects")),
        "nightly_runs": sorted(
            r["id"] for r in db.all("SELECT id FROM nightly_video_runs")
        ),
        "paused_lessons": sorted(
            r["id"]
            for r in db.all(
                "SELECT id FROM jobs WHERE kind='lesson' AND state='paused'"
            )
        ),
        "nightly_enabled": db.settings()["nightly_video_enabled"],
        "discovery_enabled": db.settings()["discovery_enabled"],
    }


def main():
    now = dt.datetime.now(awards.ZoneInfo("Asia/Tokyo"))
    before = preserved_state()
    spec = next(
        s
        for s in awards.sources(now.year, db.settings()["nightly_video_categories"])
        if s["venue"] == "ICLR" and s["year"] == now.year
    )
    ident = db.enqueue(
        "award_refresh", "local-ai-award-acceptance", {"sources": [spec]}, priority=8
    )
    print(json.dumps({"job_id": ident, "source": spec}, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        job = db.one("SELECT * FROM jobs WHERE id=?", (ident,))
        if job["state"] in {"completed", "failed", "paused", "cancelled"}:
            break
        time.sleep(1)
    result = research.receipt(spec)
    ai = (result or {}).get("local_ai", {})
    records = ai.get("records", [])
    prompts = {r.get("prompt_sha256") for r in records if r.get("prompt_sha256")}
    ai_winners = []
    for path in (config.DATA / "cache").glob("research-unit-*.json"):
        unit = json.loads(path.read_text())
        if unit.get("prompt_sha256") in prompts:
            ai_winners.extend(unit.get("winners", []))
    identities = []
    for winner in ai_winners[:2]:
        metadata, error = None, None
        for _ in range(3):
            try:
                metadata = awards.resolve(winner)
                break
            except Exception as exc:
                error = str(exc)[:300]
        identities.append(
            {"title": winner["title"], "metadata": metadata, "error": error}
        )
    checks = {
        "worker_completed": job["state"] == "completed",
        "current_research_version": ai.get("version") == research.VERSION,
        "actual_local_model_ran": any(
            r.get("prompt_sha256") and r.get("model", {}).get("weights")
            for r in records
        ),
        "test_of_time_recipients_read_by_local_ai": sum(
            r.get("winner_count", 0) for r in records
        )
        >= 2,
        "locally_read_recipients_resolve_to_actual_papers": len(identities) == 2
        and all(
            i["metadata"]
            and awards.normalized(i["title"])
            == awards.normalized(i["metadata"]["title"])
            for i in identities
        ),
        "no_duplicate_prizes": len(
            {
                (
                    awards.normalized(p["title"]),
                    awards.award_key(p["name"], p["venue"], p["year"]),
                )
                for p in result["papers"]
            }
        )
        == result["winner_count"],
        "all_saved_awards_pass_evidence_checks": all(
            awards.verified({"title": p["title"], "awards": [p]})
            for p in result["papers"]
        ),
        "existing_work_and_automation_preserved": before == preserved_state(),
    }
    report = {
        "checked_at": now.isoformat(),
        "version": research.VERSION,
        "scope": "Real local Qwen award reading through the existing GPU-owning worker; no additional paper selection or film creation.",
        "job": {
            k: job[k]
            for k in (
                "id",
                "state",
                "created",
                "updated",
                "stage",
                "checkpoint",
                "error",
            )
        },
        "source": spec,
        "receipt": result,
        "identities": identities,
        "reused_saved_inference": all(
            r.get("checked_at", 0) < job["created"] for r in records
        ),
        "checks": checks,
        "before": before,
        "after": preserved_state(),
        "status": "passed" if all(checks.values()) else "failed",
    }
    path = config.DATA / "evaluation" / "local-research-live.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("status", "checks", "job")}, ensure_ascii=False
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
