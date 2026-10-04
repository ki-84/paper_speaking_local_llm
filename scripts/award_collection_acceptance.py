"""Collect official conference awards without starting AI or video jobs.

Run with PYTHONPATH=backend .venv/bin/python scripts/award_collection_acceptance.py.
The receipt records verified winners, unavailable hosts and unpublished editions;
it does not claim that every conference prize is already publicly documented.
"""

import datetime as dt
import json

from paperspeak import awards, config, db


def main():
    now = dt.datetime.now(awards.ZoneInfo("Asia/Tokyo"))
    before = {r["id"] for r in db.all("SELECT id FROM jobs")}
    report = {
        "checked_at": now.isoformat(),
        "version": awards.COLLECTION_VERSION,
        "scope": "Read-only official award acquisition and paper identity checks; no AI or video jobs started.",
        "sources": [],
    }
    for spec in awards.sources(now.year, db.settings()["nightly_video_categories"]):
        try:
            result = awards.collect(spec)
        except ValueError:
            result = json.loads(awards._collection_path(spec).read_text())
        report["sources"].append(result)
        print(
            spec["venue"],
            spec["year"],
            result["status"],
            result["winner_count"],
            result["paper_count"],
            flush=True,
        )
    report["winner_count"] = sum(r["winner_count"] for r in report["sources"])
    report["paper_count"] = len(
        {awards.normalized(p["title"]) for r in report["sources"] for p in r["papers"]}
    )
    report["checks"] = {
        "all_enabled_venues_and_editions_recorded": len(report["sources"])
        == len(awards.sources(now.year, db.settings()["nightly_video_categories"])),
        "every_award_has_official_evidence": all(
            awards.verified({"title": p["title"], "awards": [p]}, year=now.year)
            for r in report["sources"]
            for p in r["papers"]
        ),
        "no_duplicate_prizes": all(
            len(
                {
                    (
                        awards.normalized(p["title"]),
                        awards.award_key(p["name"], p["venue"], p["year"]),
                    )
                    for p in r["papers"]
                }
            )
            == r["winner_count"]
            for r in report["sources"]
        ),
        "no_video_or_ai_jobs_started": before
        == {r["id"] for r in db.all("SELECT id FROM jobs")},
    }
    report["status"] = "passed" if all(report["checks"].values()) else "failed"
    target = config.DATA / "evaluation" / "award-collection-live.json"
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: report[k] for k in ("status", "winner_count", "paper_count", "checks")},
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
