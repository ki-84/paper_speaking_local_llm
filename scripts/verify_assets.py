#!/usr/bin/env python3
"""Full model SHA-256 audit. This reads large files and may take several minutes."""

import hashlib
import json
import time

from paperspeak import config


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(16 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


report = {"started": time.time(), "files": []}
seen = {}
for key, item in config.manifest()["models"].items():
    for artifact in item["files"]:
        path = (config.MODELS / key / artifact["file"]).resolve()
        cached = seen.get(str(path))
        if cached is None:
            cached = digest(path) if path.exists() else None
            seen[str(path)] = cached
        ok = cached == artifact["sha256"]
        report["files"].append({"model": key, "file": artifact["file"], "passed": ok})
        if not ok:
            print("Mismatch:", key, artifact["file"], flush=True)
    print("Checked", key, flush=True)
report["runtime"] = (
    digest(config.LLAMA_BIN) == config.manifest()["runtime"]["llama.cpp"]["sha256"]
)
report["finished"] = time.time()
report["passed"] = report["runtime"] and all(x["passed"] for x in report["files"])
(config.DATA / "evaluation/asset-verification.json").write_text(
    json.dumps(report, indent=2)
)
print("All hashes match:", report["passed"])
if not report["passed"]:
    raise SystemExit(1)
