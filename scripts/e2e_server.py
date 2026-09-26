"""Isolated UI fixtures, never imported by the production server or worker."""

import json
import os
import shutil
import tempfile

os.environ["PAPERSPEAK_DATA"] = tempfile.mkdtemp(prefix="paperspeak-ui-")
import uvicorn
from paperspeak import config, db, lessons, papers

try:
    db.init()
    db.set_setting("discovery_enabled", False)
    (config.DATA / "credentials.json").write_text(
        json.dumps({"password": "ui-test-only", "api_key": "ui-test-only-api"})
    )
    source = config.ROOT / "data/audio/smoke.wav"
    if not source.exists():
        import numpy as np
        import soundfile as sf

        sf.write(config.DATA / "audio/sample.wav", np.zeros(16000), 16000)
    else:
        shutil.copyfile(source, config.DATA / "audio/sample.wav")
    pid = papers.register(
        {
            "source_id": "test-interface",
            "version": "v1",
            "title": "Interface test: a small change",
            "url": "https://arxiv.org/abs/2106.09685",
            "categories": ["cs.LG"],
        }
    )
    lid = lessons.create(pid)
    l = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    l["state"] = "ready"
    l["data"]["phase"] = "complete"
    db.save_lesson(l)
    sid = pid + ":P1.1"
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (
            sid,
            pid,
            "page",
            db.dumps(
                {"text": "Original evidence for the interface test.", "label": "Page 1"}
            ),
        ),
    )
    turns = [
        {
            "id": f"test-turn-{i}",
            "speaker": "host" if i == 0 else "guide",
            "text": t,
            "kind": "paper",
            "source_ids": [sid],
            "audio": "audio/sample.wav",
            "audio_verified": True,
            "duration": 3.84,
        }
        for i, t in enumerate(
            [
                "The model learns a small change instead of changing every weight.",
                "The old weights stay fixed.",
            ]
        )
    ]
    questions = [
        {
            "id": "q1",
            "question": "What stays fixed?",
            "hints": ["Think about the old model.", "Think about its weights."],
            "sample_answer": "The old weights stay fixed.",
            "key_points": ["frozen base"],
            "source_ids": [sid],
        }
    ]
    japanese = {
        "title": ("A small change", "小さな変更"),
        "focus": ("Learn what changes.", "何が変わるか学びます。"),
        "turn:test-turn-0": (turns[0]["text"], "モデルはすべての重みを変える代わりに、小さな変更を学びます。"),
        "turn:test-turn-1": (turns[1]["text"], "元の重みは固定されたままです。"),
        "question:q1": (questions[0]["question"], "何が固定されたままですか？"),
        "hint:q1:0": (questions[0]["hints"][0], "元のモデルについて考えてください。"),
        "hint:q1:1": (questions[0]["hints"][1], "その重みについて考えてください。"),
        "answer:q1": (questions[0]["sample_answer"], "元の重みは固定されたままです。"),
    }
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        (
            "test-chapter",
            lid,
            0,
            "ready",
            db.dumps(
                {
                    "title": "A small change",
                    "focus": "Learn what changes.",
                    "turns": turns,
                    "questions": questions,
                    "translation": {
                        "model": "fixture",
                        "items": {key: {"english": en, "japanese": ja} for key, (en, ja) in japanese.items()},
                    },
                }
            ),
        ),
    )
    uvicorn.run("paperspeak.api:app", host="127.0.0.1", port=8195, log_level="warning")
finally:
    shutil.rmtree(config.DATA, ignore_errors=True)
