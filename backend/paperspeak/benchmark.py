from __future__ import annotations

import json
import re
import time

from . import config, db, papers
from .lessons import source_context
from .runtime import gpu_info


def equivalent(actual, expected):
    if isinstance(expected, bool):
        return (
            actual is expected
            or isinstance(actual, str)
            and actual.lower() == str(expected).lower()
        )
    if isinstance(expected, (int, float)):
        if isinstance(actual, bool):
            return False
        try:
            return abs(float(actual) - expected) < 1e-6
        except (TypeError, ValueError):
            return False
    return actual == expected


def benchmark_step(job, runtime):
    suite = job["payload"].get("suite", "paper")
    cases = json.loads((config.ROOT / f"evaluation/{suite}_cases.json").read_text())[
        "cases"
    ]
    profiles = job["payload"].get("profiles", ["qwen-q8", "qwen-q6", "muse-q6"])
    cp = {"results": [], "index": 0, **job["checkpoint"]}
    i = cp["index"]
    if i >= len(cases) * len(profiles):
        lookup = {c["id"]: c for c in cases}
        for r in cp["results"]:
            r["checks"] = {
                k: equivalent(r["answer"].get("facts", {}).get(k), v)
                for k, v in lookup[r["case"]]["expected"].items()
            }
            r["correct"] = sum(r["checks"].values())
        report = {
            "date": time.strftime("%Y-%m-%d"),
            "results": cp["results"],
            "limitations": "Three fixed papers are a small task-specific comparison, not a universal model ranking.",
        }
        report["summary"] = [
            {
                "profile": p,
                "correct": sum(
                    r["correct"] for r in cp["results"] if r["profile"] == p
                ),
                "total": sum(r["total"] for r in cp["results"] if r["profile"] == p),
                "seconds": sum(
                    r["seconds"] for r in cp["results"] if r["profile"] == p
                ),
                "mean_sentence_words": sum(
                    r["mean_sentence_words"] for r in cp["results"] if r["profile"] == p
                )
                / len(cases),
            }
            for p in profiles
        ]
        report["recommended_profile"] = sorted(
            report["summary"],
            key=lambda r: (-r["correct"], r["mean_sentence_words"], r["seconds"]),
        )[0]["profile"]
        (
            config.DATA
            / (
                "evaluation/visual-comparison.json"
                if suite == "visual"
                else "evaluation/model-comparison.json"
            )
        ).write_text(json.dumps(report, indent=2))
        db.patch_job(job["id"], stage="Model comparison complete", progress=1)
        return True
    profile = profiles[i // len(cases)]
    case = cases[i % len(cases)]
    db.patch_job(
        job["id"],
        stage=f"Comparing {profile}: {case['id']}",
        progress=i / (len(cases) * len(profiles)),
    )
    pid = papers.register_arxiv(case["arxiv"])
    papers.ingest(pid)
    sources = db.all("SELECT * FROM sources WHERE paper_id=? ORDER BY rowid", (pid,))
    primary = [s for s in sources if s["kind"] == "page"]
    selected = [
        s
        for s in primary
        if any(k.lower() in s["data"]["text"].lower() for k in case["keywords"])
    ]
    # Deterministic evidence selection shared by all candidates.
    selected = selected[:10]
    image = next(
        (s for s in primary if s["data"].get("page") == case["image_page"]), None
    )
    start = time.monotonic()
    answer = runtime.ask(
        case["question"]
        + '\nReturn JSON: {"facts":'
        + json.dumps(case["fields"])
        + ',"explanation":"four very short sentences in easy English"}. Use only the provided paper.\n'
        + source_context(selected),
        images=[config.safe_path(image["data"]["image_path"])] if image else [],
        profile=profile,
        max_tokens=5000,
    )
    elapsed = time.monotonic() - start
    facts = answer.get("facts", {})
    checks = {k: equivalent(facts.get(k), v) for k, v in case["expected"].items()}
    explanation = answer.get("explanation", "")
    sentences = [s.split() for s in re.split(r"[.!?]", explanation) if s.strip()]
    cp["results"].append(
        {
            "profile": profile,
            "case": case["id"],
            "answer": answer,
            "checks": checks,
            "correct": sum(checks.values()),
            "total": len(checks),
            "seconds": elapsed,
            "gpu": gpu_info(),
            "mean_sentence_words": sum(map(len, sentences)) / max(1, len(sentences)),
            "source_ids": [s["id"] for s in selected],
        }
    )
    cp["index"] += 1
    db.patch_job(job["id"], checkpoint=cp)
    return False
