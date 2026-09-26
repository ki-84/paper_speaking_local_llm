#!/usr/bin/env python3
"""Check technical-name spelling without supplying the answer sentence to ASR.

Run only while production GPU jobs are paused. Synthetic controls check that
vocabulary context does not hide a changed number or a changed ordinary word.
They do not establish human pronunciation grading accuracy.
"""

import json
import time

from paperspeak import config, db
from paperspeak.quality import changed_spoken_number, speech_context, word_diff
from paperspeak.runtime import Runtime

db.init()
if db.one("SELECT id FROM jobs WHERE state IN ('queued','running')"):
    raise SystemExit("Pause queued GPU work before this diagnostic.")
chapter = db.one("SELECT * FROM chapters ORDER BY ordinal LIMIT 1")
turn = next(
    t
    for t in chapter["data"]["turns"]
    if t["text"] == "LoRA keeps the old weights frozen."
)
folder = config.DATA / "evaluation/asr-context-controls"
folder.mkdir(parents=True, exist_ok=True)
lesson = db.one("SELECT data FROM lessons WHERE id=?", (chapter["lesson_id"],))["data"]
vocabulary = speech_context(
    lesson["title"], lesson.get("glossary", []), chapter["data"]["turns"]
)
report = {
    "started": time.time(),
    "context": vocabulary,
    "scope": "Synthetic integration controls; not a human pronunciation benchmark",
    "results": [],
}
runtime = Runtime()
try:
    for context in ["", vocabulary]:
        result = runtime.speech(
            "asr", {"audio": str(config.safe_path(turn["audio"])), "context": context}
        )
        report["results"].append(
            {
                "case": "technical_name",
                "context": context,
                "expected": turn["text"],
                "result": result,
                "diff": word_diff(turn["text"], result["text"]),
            }
        )
        print(
            "Technical-name transcription:", repr(context), result["text"], flush=True
        )
    controls = [
        (
            "changed_number",
            "LoRA uses three small matrices.",
            "LoRA uses two small matrices.",
        ),
        (
            "changed_word",
            "LoRA keeps the new weights frozen.",
            "LoRA keeps the old weights frozen.",
        ),
        (
            "changed_number_reverse",
            "LoRA uses two small matrices.",
            "LoRA uses three small matrices.",
        ),
    ]
    for case, spoken, _ in controls:
        runtime.speech(
            "tts",
            {
                "text": spoken,
                "voice": "Ryan",
                "seed": 707,
                "output": str(folder / (case + ".wav")),
            },
        )
    for case, spoken, expected in controls:
        result = runtime.speech(
            "asr", {"audio": str(folder / (case + ".wav")), "context": vocabulary}
        )
        diff = word_diff(expected, result["text"])
        report["results"].append(
            {
                "case": case,
                "spoken": spoken,
                "expected": expected,
                "result": result,
                "diff": diff,
            }
        )
        print(case, result["text"], flush=True)
    report["checks"] = {
        "technical_spelling": report["results"][1]["diff"]["wer"] == 0,
        "number_change_still_detected": changed_spoken_number(
            report["results"][2]["diff"]
        ),
        "ordinary_word_change_still_detected": any(
            w["expected"] == "old" and w["heard"] == "new"
            for w in report["results"][3]["diff"]["words"]
        ),
        "reverse_number_change_still_detected": changed_spoken_number(
            report["results"][4]["diff"]
        ),
    }
    report["status"] = "passed" if all(report["checks"].values()) else "needs_review"
finally:
    runtime.close()
    report["finished"] = time.time()
    (config.DATA / "evaluation/asr-context-probe.json").write_text(
        json.dumps(report, indent=2)
    )
print(report.get("status", "interrupted"))
