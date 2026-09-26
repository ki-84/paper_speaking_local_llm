"""Isolated UI fixtures, never imported by the production server or worker."""

import json
import os
import shutil
import tempfile

os.environ["PAPERSPEAK_DATA"] = tempfile.mkdtemp(prefix="paperspeak-ui-")
import uvicorn
from paperspeak import config, db, lessons, papers, practice
from paperspeak.quality import word_diff

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
    reference_line = "The old weights stay fixed"
    heard_line = "The old weights stay mixed"
    reference_stamps = [
        {"word": word, "start": i * 0.2, "end": i * 0.2 + 0.15}
        for i, word in enumerate(reference_line.split())
    ]
    heard_stamps = [
        {"word": word, "start": i * 0.2, "end": i * 0.2 + 0.15}
        for i, word in enumerate(heard_line.split())
    ]
    recording = {
        "phase": "done", "audio": "audio/sample.wav", "wav": "audio/sample.wav",
        "reference_audio": "audio/sample.wav", "transcript": heard_line,
        "pace_wpm": 125, "pauses": [],
        **word_diff(reference_line, heard_line),
    }
    recording["pronunciation_focus"] = practice.pronunciation_focus(
        recording, reference_stamps, heard_stamps
    )
    db.execute(
        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
        (
            "test-pronunciation-coach", lid, "test-chapter", "test-turn-1",
            "read", "ready", db.dumps(recording), 0,
        ),
    )
    search_paper = {
        "source_id": "2609.12345", "version": "v1",
        "title": "A Robot That Learns to Grasp",
        "abstract": "We study a robot that learns to grasp new objects in a controlled test.",
        "url": "https://arxiv.org/abs/2609.12345v1",
        "categories": ["cs.RO"], "published": "2026-09-20T00:00:00Z",
    }
    search_pid = papers.register(search_paper)
    search_job = db.enqueue(
        "paper_search", "ui-search", {"query": "初めて見る物をつかむロボット"}, priority=7
    )
    db.patch_job(
        search_job, state="completed", progress=1,
        checkpoint={"phase": "done", "results": [search_paper | {
            "paper_id": search_pid,
            "title_ja": "初めて見る物をつかむロボット",
            "summary_ja": "ロボットが新しい物をつかむ方法を調べます。条件を決めた実験で確かめます。",
            "fit_ja": "未知の物をつかむ学習について読めます。",
        }]},
    )
    db.execute(
        "INSERT INTO recommendations VALUES (?,?,?,?,?,?)",
        (
            db.uid(), search_pid, "2026-09-26", "recommended",
            db.dumps({
                "easy_english": True,
                "why": "A robot learns to grasp new objects.",
                "learn": "You can study how it learns.",
                "cautions": ["The test is controlled."],
                "source_ids": [],
                "ja": {
                    "title": "ロボットが新しい物をつかむ研究",
                    "summary": "ロボットが新しい物をつかむ方法を調べます。",
                    "why": "初めての物をつかむ方法が面白いです。",
                    "learn": "ロボットの学習方法を学べます。",
                    "cautions": ["条件を決めた実験です。"],
                },
            }), None,
        ),
    )
    uvicorn.run("paperspeak.api:app", host="127.0.0.1", port=8195, log_level="warning")
finally:
    shutil.rmtree(config.DATA, ignore_errors=True)
