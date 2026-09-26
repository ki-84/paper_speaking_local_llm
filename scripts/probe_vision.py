#!/usr/bin/env python3
"""Check GPU vision offload with fixed original-paper images and VRAM sampling.

Run while the studio's generation jobs are paused and its model is unloaded.
Uses the already ingested benchmark papers; does not alter their lessons.
"""

import json
import threading
import time

from paperspeak import config, db, papers
from paperspeak.benchmark import equivalent
from paperspeak.runtime import Runtime, gpu_info

cases = json.loads((config.ROOT / "evaluation/visual_cases.json").read_text())["cases"]
report = {
    "started": time.time(),
    "models": config.manifest(),
    "vision_gpu": True,
    "results": [],
}
samples = []
stopped = threading.Event()


def monitor():
    while not stopped.is_set():
        samples.append(gpu_info())
        stopped.wait(0.5)


thread = threading.Thread(target=monitor, daemon=True)
thread.start()
runtime = Runtime(vision_gpu=True)
try:
    for i, case in enumerate(cases + [cases[-1]]):
        base, version = papers.parse_reference(case["arxiv"])
        paper = db.one(
            "SELECT id FROM papers WHERE source_id=? AND version=?", (base, version)
        )
        if not paper:
            raise RuntimeError("Run the paper benchmark first to ingest its sources.")
        sources = db.all(
            "SELECT * FROM sources WHERE paper_id=? AND kind='page'", (paper["id"],)
        )
        wanted = [case["image_page"]] if i < len(cases) else [1, 2]
        images = list(
            dict.fromkeys(
                config.safe_path(s["data"]["image_path"])
                for s in sources
                if s["data"].get("page") in wanted
            )
        )
        started = time.monotonic()
        result = runtime.ask(
            case["question"]
            + '\nReturn JSON: {"facts":'
            + json.dumps(case["fields"])
            + ',"explanation":"four short English sentences"}. Use only the supplied images.',
            images=images,
            profile="muse-q6",
            max_tokens=5000,
        )
        checks = {
            k: equivalent(result.get("facts", {}).get(k), v)
            for k, v in case["expected"].items()
        }
        row = {
            "case": case["id"],
            "images": len(images),
            "seconds": time.monotonic() - started,
            "answer": result,
            "checks": checks,
        }
        report["results"].append(row)
        print({k: row[k] for k in ["case", "images", "seconds", "checks"]}, flush=True)
    report["status"] = (
        "passed"
        if all(all(r["checks"].values()) for r in report["results"])
        else "needs_review"
    )
except Exception as e:
    report.update(status="failed", error=str(e))
    raise
finally:
    runtime.close()
    stopped.set()
    thread.join(timeout=10)
    report.update(
        finished=time.time(),
        min_free_mib=min((s["free_mib"] for s in samples), default=None),
        memory_samples=samples,
    )
    path = config.DATA / "evaluation/vision-gpu-probe.json"
    path.write_text(json.dumps(report, indent=2))
    print(path, flush=True)
