"""Compare phoneme decoders on one shared, coarse American-English phone inventory."""

import json
import re

from . import config, db
from .calibration import ARPA, alignment


def fold(value):
    for a, b in [
        ("ː", ""),
        ("ʧ", "tʃ"),
        ("ʤ", "dʒ"),
        ("ɝ", "ɚ"),
        ("g", "ɡ"),
        ("r", "ɹ"),
        ("ɔ", "ɑ"),
        ("ʌ", "ə"),
    ]:
        value = value.replace(a, b)
    return value


def expand(phones):
    result = []
    for p in phones:
        s = fold(p["phone"])
        for part in {
            "ɑɹ": ["ɑ", "ɹ"],
            "ɛɹ": ["ɛ", "ɹ"],
            "ɪɹ": ["ɪ", "ɹ"],
            "ʊɹ": ["ʊ", "ɹ"],
            "əl": ["ə", "l"],
            "ɜɹ": ["ɚ"],
        }.get(s, [s]):
            result.append(p | {"phone": part})
    return result


def score(sample, result):
    canonical = []
    labels = []
    for word in sample["words"]:
        for p, accuracy in zip(word["phones"], word["phones-accuracy"]):
            canonical.append(fold(ARPA.get(re.sub(r"\d", "", p), p)))
            labels.append(accuracy < 1.5)
    units = expand(result["phones"])
    heard = [p["phone"] for p in units]
    counts = {
        k: 0 for k in ["tp", "fp", "tn", "fn", "abstain_positive", "abstain_negative"]
    }
    for a, b in alignment(canonical, heard):
        if a is None:
            continue
        truth = labels[a]
        if b is None or units[b]["confidence"] < 0.8:
            counts["abstain_positive" if truth else "abstain_negative"] += 1
        else:
            predicted = canonical[a] != heard[b]
            counts[
                "tp"
                if truth and predicted
                else "fp"
                if predicted
                else "fn"
                if truth
                else "tn"
            ] += 1
    return counts


def summary(rows):
    out = {
        k: sum(r["counts"][k] for r in rows)
        for k in ["tp", "fp", "tn", "fn", "abstain_positive", "abstain_negative"]
    }
    tp, fp, tn, fn = [out[k] for k in ["tp", "fp", "tn", "fn"]]
    return out | {
        "precision": tp / max(1, tp + fp),
        "recall_including_abstentions": tp / max(1, tp + fn + out["abstain_positive"]),
        "false_positive_rate": fp / max(1, fp + tn),
        "coverage": (tp + fp + tn + fn) / max(1, sum(out.values())),
    }


def probe_step(job, runtime):
    dataset = json.loads((config.DATA / "evaluation/human-labels.json").read_text())
    samples = next(
        s["samples"] for s in dataset["sets"] if "speechocean" in s["dataset"]
    )
    cp = {"index": 0, "results": [], **job["checkpoint"]}
    if cp["index"] >= len(samples):
        baseline = json.loads(
            (config.DATA / "evaluation/human-speech-report.json").read_text()
        )
        lookup = {s["row_index"]: s for s in samples}
        rows = []
        for r in baseline["results"]:
            if r["mode"] == "phoneme":
                rows.append({"counts": score(lookup[r["row_index"]], r["result"])})
        report = {
            "models": config.manifest(),
            "samples": len(samples),
            "speakers": len({s["speaker"] for s in samples}),
            "method": "Both recognizers are aligned to the same canonical phones. Normalize IPA glyph variants, vowel length, common American AA/AO and AH vowel variants, and compound rhotic/syllabic tokens. Fixed confidence 0.8; human error threshold <1.5/2. Report abstentions, including human errors withheld. These are recognizer probes, not pronunciation grades.",
            "baseline": summary(rows),
            "timit": summary(cp["results"]),
            "results": cp["results"],
        }
        (config.DATA / "evaluation/phoneme-comparison.json").write_text(
            json.dumps(report, indent=2)
        )
        db.patch_job(job["id"], stage="Phoneme comparison complete", progress=1)
        return True
    sample = samples[cp["index"]]
    db.patch_job(
        job["id"],
        stage=f"Checking another phoneme reader {cp['index'] + 1}/{len(samples)}",
        progress=cp["index"] / len(samples),
    )
    result = runtime.speech(
        "phoneme",
        {
            "audio": str(config.safe_path(sample["file"])),
            "phoneme_profile": "phoneme-timit",
        },
    )
    cp["results"].append(
        {
            "row_index": sample["row_index"],
            "result": result,
            "counts": score(sample, result),
        }
    )
    cp["index"] += 1
    db.patch_job(job["id"], checkpoint=cp)
    return False
