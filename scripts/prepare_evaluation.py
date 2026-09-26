#!/usr/bin/env python3
"""Download fixed held-out learner/actor recordings and human labels, never user audio."""

import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/evaluation/human-audio"
DEST.mkdir(parents=True, exist_ok=True)


def get(url):
    for trial in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return r.read()
        except Exception:
            if trial == 3:
                raise
            time.sleep(2**trial)


def rows(dataset, offset, length):
    return json.loads(
        get(
            f"https://datasets-server.huggingface.co/rows?dataset={dataset}&config=default&split=test&offset={offset}&length={length}"
        )
    )["rows"]


def save_audio(row, dataset, revision, index):
    audio = row.pop("audio")
    name = dataset.split("/")[-1] + f"-{index}.wav"
    path = DEST / name
    if not path.exists():
        path.write_bytes(get(audio[0]["src"]))
    return row | {
        "row_index": index,
        "dataset": dataset,
        "revision": revision,
        "file": str(path.relative_to(ROOT / "data")),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


sets = []
for dataset, offsets, length in [
    ("mispeech/speechocean762", range(0, 2500, 250), 10),
    ("slprl/StressTest", range(0, 218, 100), 100),
]:
    revision = json.loads(get("https://huggingface.co/api/datasets/" + dataset))["sha"]
    samples = []
    for offset in offsets:
        for entry in rows(
            dataset,
            offset,
            min(length, 218 - offset) if "StressTest" in dataset else length,
        ):
            row = entry["row"]
            if (
                "StressTest" in dataset
                and row.get("metadata", {}).get("voice_name") != "actor"
            ):
                continue
            samples.append(save_audio(row, dataset, revision, entry["row_idx"]))
        print(dataset, "saved", len(samples), flush=True)
    sets.append(
        {"dataset": dataset, "revision": revision, "split": "test", "samples": samples}
    )
    manifest = ROOT / "data/evaluation/human-labels.json"
    temp = manifest.with_suffix(".tmp")
    temp.write_text(json.dumps({"sets": sets}, indent=2))
    temp.replace(manifest)
print("Ready:", sum(len(s["samples"]) for s in sets), "human recordings", flush=True)
