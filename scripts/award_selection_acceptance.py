"""Read-only live award discovery and title resolution; does not create videos."""

import datetime as dt
import json

from paperspeak import awards, config, nightly

now = dt.datetime.now(nightly.ZONE)
report = {
    "checked_at": now.isoformat(),
    "scope": "Official award pages and exact-title arXiv metadata; no production jobs started.",
    "sources": [],
    "resolved": [],
    "unresolved": [],
}
pool = []
for spec in awards.sources(now.year, ["cs.AI", "cs.RO", "cs.CL"]):
    try:
        result = awards.collect(spec)
        pool.extend(result["papers"])
        report["sources"].append(
            {k: v for k, v in result.items() if k != "papers"}
            | {"winner_count": len(result["papers"])}
        )
    except Exception as exc:
        report["sources"].append(
            {
                "source": spec,
                "status": "unavailable",
                "reason": str(exc)[:200],
                "winner_count": 0,
            }
        )
    print(
        spec["venue"], spec["year"], report["sources"][-1]["winner_count"], flush=True
    )
# Cover each venue represented by a verified result, then choose extra new papers.
picked = []
for venue in awards.VENUES:
    match = next((p for p in pool if p["venue"] == venue), None)
    if match:
        picked.append(match)
picked += [p for p in pool if p not in picked][:4]
for winner in picked:
    meta = None
    try:
        meta = awards.resolve(winner)
        if meta:
            report["resolved"].append(meta | {"awards": [winner]})
        else:
            report["unresolved"].append(
                {
                    "title": winner["title"],
                    "venue": winner["venue"],
                    "reason": "No unambiguous exact title match",
                }
            )
    except Exception as exc:
        report["unresolved"].append(
            {
                "title": winner["title"],
                "venue": winner["venue"],
                "reason": str(exc)[:200],
            }
        )
    print(
        "Resolved",
        winner["venue"],
        winner["title"],
        bool(meta),
        flush=True,
    )
report["shortlist"] = nightly.shortlist(
    report["resolved"], {}, now=now, days=7, awards_first=True
)
report["checks"] = {
    "official_verified_ai_candidates": any(
        p["venue"] not in awards.ROBOTICS for p in pool
    ),
    "official_verified_robotics_candidates": any(
        p["venue"] in awards.ROBOTICS for p in pool
    ),
    "resolved_ai_paper": any(p["area"] in {"ai", "llm"} for p in report["shortlist"]),
    "resolved_robotics_paper": any(
        p["area"] == "robotics" for p in report["shortlist"]
    ),
    "every_shortlisted_award_verified": bool(report["shortlist"])
    and all(awards.verified(p, year=now.year) for p in report["shortlist"]),
    "older_submission_eligible_when_recently_awarded": any(
        dt.datetime.fromisoformat(p["published"].replace("Z", "+00:00"))
        < now - dt.timedelta(days=30)
        for p in report["shortlist"]
    ),
}
report["status"] = "passed" if all(report["checks"].values()) else "failed"
target = config.DATA / "evaluation" / "award-selection-live.json"
target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {"status": report["status"], "checks": report["checks"], "report": str(target)},
        ensure_ascii=False,
    )
)
raise SystemExit(0 if report["status"] == "passed" else 1)
