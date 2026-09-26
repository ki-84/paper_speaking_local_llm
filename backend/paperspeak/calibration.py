"""Human-labelled held-out speech probes. These never turn estimates into grades."""

from __future__ import annotations

import json
import re
import time

from . import config, db
from .quality import word_diff

ARPA = dict(
    zip(
        "AA AE AH AO AW AY B CH D DH EH ER EY F G HH IH IY JH K L M N NG OW OY P R S SH T TH UH UW V W Y Z ZH".split(),
        "ɑ æ ʌ ɔ aʊ aɪ b tʃ d ð ɛ ɚ eɪ f ɡ h ɪ i dʒ k l m n ŋ oʊ ɔɪ p ɹ s ʃ t θ ʊ u v w j z ʒ".split(),
    )
)


def phone(p):
    return p.replace("ː", "").replace("ɝ", "ɚ").replace("r", "ɹ")


def alignment(a, b):
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + (a[i - 1] != b[j - 1]),
            )
    pairs = []
    i, j = len(a), len(b)
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return list(reversed(pairs))


def counts(truth, predicted):
    out = {"tp": 0, "fp": 0, "tn": 0, "fn": 0, "abstain": 0}
    for y, p in zip(truth, predicted):
        if p is None:
            out["abstain"] += 1
        else:
            out["tp" if y and p else "fp" if p else "fn" if y else "tn"] += 1
    return out


def calibration_step(job, runtime):
    manifest = json.loads((config.DATA / "evaluation/human-labels.json").read_text())
    learner = next(
        s["samples"] for s in manifest["sets"] if "speechocean" in s["dataset"]
    )
    actors = next(
        s["samples"] for s in manifest["sets"] if "StressTest" in s["dataset"]
    )
    tasks = (
        [("asr", s) for s in learner]
        + [("phoneme", s) for s in learner]
        + [("stress", s) for s in actors]
    )
    cp = {"index": 0, "results": [], **job["checkpoint"]}
    i = cp["index"]
    if i >= len(tasks):
        report = {
            "date": time.strftime("%Y-%m-%d"),
            "sample_counts": {
                "human_learners": len(learner),
                "human_actor_stress": len(actors),
            },
            "models": config.manifest(),
            "methods": {
                "phoneme": "Canonical ARPAbet mapped to IPA, Levenshtein aligned to Wav2Vec2 free decoding. Human phone accuracy < 1.5/2 defines an error. Confidence < 0.8 or deleted phones abstain. This probes the recognizer, not a validated pronunciation grade.",
                "stress": "WhiStress 0.5 threshold against StressTest actor word labels. Contractions remain one word. Word segmentation failures abstain.",
                "asr": "Normalized word error rate against human transcript.",
            },
            "results": cp["results"],
        }
        for mode in ["phoneme", "stress"]:
            total = {
                k: sum(
                    r.get("counts", {}).get(k, 0)
                    for r in cp["results"]
                    if r["mode"] == mode
                )
                for k in ["tp", "fp", "tn", "fn", "abstain"]
            }
            tp, fp, tn, fn = [total[k] for k in ["tp", "fp", "tn", "fn"]]
            report[mode] = total | {
                "precision": tp / max(1, tp + fp),
                "recall_scored": tp / max(1, tp + fn),
                "false_positive_rate": fp / max(1, fp + tn),
                "false_negative_rate_scored": fn / max(1, tp + fn),
                "coverage": (tp + fp + tn + fn) / max(1, sum(total.values())),
            }
        asrs = [r["wer"] for r in cp["results"] if r["mode"] == "asr"]
        report["mean_utterance_wer"] = sum(asrs) / max(1, len(asrs))
        report["decision"] = (
            "Keep sound and stress feedback as estimates. No pronunciation score or automatic error verdict is enabled. A broader learner/accent validation is needed before grading."
        )
        (config.DATA / "evaluation/human-speech-report.json").write_text(
            json.dumps(report, indent=2)
        )
        db.patch_job(job["id"], stage="Human speech checks complete", progress=1)
        return True
    mode, sample = tasks[i]
    db.patch_job(
        job["id"],
        stage=f"Human audio check: {mode} {i + 1}/{len(tasks)}",
        progress=i / len(tasks),
    )
    result = runtime.speech(
        mode,
        {
            "audio": str(config.safe_path(sample["file"])),
            "text": sample.get("text", sample.get("transcription", "")),
        },
    )
    row = {
        "mode": mode,
        "dataset": sample["dataset"],
        "row_index": sample["row_index"],
        "speaker": sample.get("speaker", sample.get("metadata", {})),
        "result": result,
    }
    if mode == "asr":
        row["wer"] = word_diff(sample["text"], result["text"])["wer"]
    elif mode == "phoneme":
        canonical = []
        labels = []
        for w in sample["words"]:
            for p, score in zip(w["phones"], w["phones-accuracy"]):
                base = re.sub(r"\d", "", p)
                ipa = "ə" if p == "AH0" else ARPA.get(base, base)
                canonical.append(phone(ipa))
                labels.append(score < 1.5)
        heard = [phone(p["phone"]) for p in result["phones"]]
        truth = []
        pred = []
        for a, b in alignment(canonical, heard):
            if a is None:
                continue
            truth.append(labels[a])
            pred.append(
                None
                if b is None or result["phones"][b]["confidence"] < 0.8
                else canonical[a] != heard[b]
            )
        row["counts"] = counts(truth, pred)
    else:
        truth = sample["stress_pattern"]["binary"]
        units = result.get("words", [])
        pred = (
            [u["emphasized"] for u in units]
            if len(units) == len(truth)
            else [None] * len(truth)
        )
        row["counts"] = counts(truth, pred)
    cp["results"].append(row)
    cp["index"] += 1
    db.patch_job(job["id"], checkpoint=cp)
    return False
