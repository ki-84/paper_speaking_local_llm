"""Checkpoint integration tests use an explicit scripted provider, not AI quality evidence."""

import json

import numpy as np
import pytest
import soundfile as sf
from paperspeak import config, db, lessons, papers, translation


def test_duplicate_claims_do_not_require_evidence_from_another_chapter():
    canonical = {"id": "n1", "claim": "One mechanism", "source_ids": ["paper:H1"]}
    duplicate = {"id": "n2", "claim": "The same mechanism", "source_ids": ["paper:P3"]}
    claims, sources = lessons.selected_sources(
        {"claim_ids": ["n1", "n2"], "evidence_claim_ids": ["n1"]},
        [{"claims": [canonical, duplicate]}],
        [
            {"id": "paper:H1", "data": {"text": "One mechanism"}},
            {"id": "paper:P3", "data": {"text": "The same mechanism"}},
        ],
    )
    assert claims == [canonical]
    assert [s["id"] for s in sources] == ["paper:H1"]


def test_local_repair_preserves_other_sentences_and_needs_review(database):
    sid = "source"
    evidence = [{"id": sid, "data": {"text": "The old weights stay fixed."}}]
    turns = [
        {
            "id": str(i) * 32,
            "speaker": "guide",
            "kind": "paper",
            "text": "The old weights stay fixed.",
            "source_ids": [sid],
            "audio": f"audio/{i}.wav",
            "audio_verified": True,
        }
        for i in range(5)
    ]
    chapter = {
        "turns": turns,
        "review": {
            "issues": [{"turn_id": "22222222", "reason": "Explain fixed weights."}],
            "missing_claim_ids": [],
        },
    }
    targets = lessons.repair_targets(chapter)
    assert targets == {str(i) * 32 for i in [1, 2, 3]}
    replacement = {
        "speaker": "guide",
        "kind": "background",
        "text": "Fixed means these numbers do not change during learning.",
        "source_ids": [],
    }
    lessons.apply_local_repairs(
        chapter,
        {"edits": [{"turn_id": "2" * 8, "replacement": [replacement]}]},
        targets,
        evidence,
    )
    assert chapter["turns"][0] == turns[0] and chapter["turns"][4] == turns[4]
    assert (
        chapter["turns"][2]["audio"] is None
        and not chapter["turns"][2]["audio_verified"]
    )
    assert (
        chapter["local_revision_history"][0]["changes"][0]["before"]["audio"]
        == "audio/2.wav"
    )
    before = json.dumps(chapter)
    with pytest.raises(ValueError, match="outside"):
        lessons.apply_local_repairs(
            chapter,
            {"edits": [{"turn_id": "0" * 32, "replacement": [replacement]}]},
            targets,
            evidence,
        )
    assert json.dumps(chapter) == before
    lessons.apply_local_repairs(
        chapter,
        {"edits": [{"turn_id": "1" * 32, "replacement": []}]},
        targets,
        evidence,
    )
    assert len(chapter["turns"]) == 4
    assert (
        chapter["local_revision_history"][-1]["changes"][0]["before"]["audio"]
        == "audio/1.wav"
    )
    before = json.dumps(chapter)
    with pytest.raises(ValueError, match="entire chapter"):
        lessons.apply_local_repairs(
            chapter,
            {
                "edits": [
                    {"turn_id": t["id"], "replacement": []} for t in chapter["turns"]
                ]
            },
            {t["id"] for t in chapter["turns"]},
            evidence,
        )
    assert json.dumps(chapter) == before
    chapter["review"]["missing_claim_ids"] = ["unexplained"]
    assert lessons.repair_targets(chapter) is None


def test_lesson_checkpoints_audio_recovery_and_immutable_revision(
    database, monkeypatch
):
    pid = papers.register(
        {"source_id": "test", "version": "v1", "title": "Pipeline fixture"}
    )
    db.execute("UPDATE papers SET state='ready' WHERE id=?", (pid,))
    sid = pid + ":P1.1"
    text = "The model keeps its old weights fixed."
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (sid, pid, "page", db.dumps({"text": text, "label": "Page 1"})),
    )
    monkeypatch.setattr(
        config,
        "manifest",
        lambda: {
            "models": {"qwen-q8": {"revision": "fixed"}, "tts": {"revision": "fixed"}, "tts-design": {"revision": "fixed"}}
        },
    )
    lid = lessons.create(pid, format_version="paper-radio-1")
    jid = db.enqueue("lesson", lid)

    class ScriptedProvider:
        def __init__(self):
            self.tts_calls = 0

        def ask(self, prompt, **kwargs):
            if prompt.startswith("Read this part"):
                return {
                    "summary": "A fixed model",
                    "claims": [{"claim": text, "source_ids": [sid], "quote": text}],
                    "uncertainties": [],
                }
            if prompt.startswith("Design a learning path"):
                return {
                    "goals": [
                        {
                            "title": "What stays fixed",
                            "focus": "Understand the old weights",
                        }
                    ],
                    "glossary": [],
                }
            if prompt.startswith("Assign each new claim"):
                return {
                    "assignments": [
                        {"claim_id": "N1C1", "goal": 0, "duplicate_of": None}
                    ]
                }
            if prompt.startswith("Make the English"):
                return {
                    "turns": json.loads(
                        prompt.split("\nDIALOGUE: ")[1].split("\nREQUIRED TERMS:")[0]
                    )
                }
            if prompt.startswith("Write the next"):
                return {
                    "turns": [
                        {
                            "speaker": "guide",
                            "text": text,
                            "kind": "paper",
                            "source_ids": [sid],
                        }
                    ]
                }
            if prompt.startswith("Act as a skeptical"):
                return {"passed": True, "issues": [], "missing_claim_ids": []}
            if prompt.startswith("Create 3 short"):
                return {
                    "questions": [
                        {
                            "question": "What stays fixed?",
                            "sample_answer": "The old weights.",
                            "key_points": ["old weights"],
                            "hints": ["Think about the model.", "Think about weights."],
                            "source_ids": [sid],
                        }
                    ]
                }
            if prompt.startswith("Translate each English item"):
                items = json.loads(prompt.split("ITEMS: ", 1)[1])
                return {
                    "items": [
                        {"id": item["id"], "japanese": "訳です。" + item["english"]}
                        for item in items
                    ]
                }
            raise AssertionError(prompt[:80])

        def speech(self, mode, request):
            if mode in {"tts", "tts_design"}:
                self.tts_calls += 1
                sf.write(request["output"], np.zeros(1600), 16000)
                return {"duration": 0.1}
            return {"text": text, "timestamps": []}

    provider = ScriptedProvider()
    for i in range(35):
        # Every step reconstructs the job and lesson from SQLite, as after a restart.
        job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        if lessons.lesson_step(job, provider):
            break
    else:
        raise AssertionError("Did not finish")
    l = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    assert l["state"] == "ready"
    chapter = db.one("SELECT * FROM chapters WHERE lesson_id=?", (lid,))
    assert chapter["state"] == "ready"
    assert translation.complete(chapter, l)
    assert provider.tts_calls == 1 and all(
        t["audio_verified"] for t in chapter["data"]["turns"]
    )
    assert {t["voice"] for t in chapter["data"]["turns"]} == {"Maya"}
    paths = [config.safe_path(t["audio"]) for t in chapter["data"]["turns"]]
    next_lid = lessons.create(pid)
    assert next_lid != lid
    assert all(p.exists() for p in paths)
    assert db.one("SELECT state FROM lessons WHERE id=?", (lid,))["state"] == "ready"


def test_audio_rephrasing_preserves_old_audio_and_requires_evidence_review(database):
    pid = papers.register(
        {"source_id": "audio-repair", "version": "v1", "title": "Audio repair"}
    )
    sid = pid + ":P1.1"
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (
            sid,
            pid,
            "page",
            db.dumps({"text": "The method reduces storage and switching cost."}),
        ),
    )
    lid = lessons.create(pid)
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    lesson["data"].update(
        phase="chapters",
        notes=[
            {
                "claims": [
                    {"id": "n1", "claim": "Storage cost falls", "source_ids": [sid]}
                ]
            }
        ],
        outline=[{"title": "Cost"}],
        glossary=[],
    )
    db.save_lesson(lesson)
    (database / "original.wav").write_bytes(b"original audio remains available")
    turn = {
        "id": "turn",
        "speaker": "host",
        "kind": "question",
        "text": "Does that cut storage and switching cost?",
        "source_ids": [],
        "audio": "original.wav",
        "audio_verified": False,
        "audio_retries": 2,
        "tts_settings": {"private_metadata": "MEDIA-MUST-STAY-LOCAL"},
    }
    data = {
        "title": "Cost",
        "claim_ids": ["n1"],
        "turns": [turn],
        "revision_round": 0,
        "english_polished": True,
    }
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        ("repair", lid, 0, "audio_review", db.dumps(data)),
    )
    jid = db.enqueue("lesson", lid)
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))

    class Provider:
        def speech(self, *args):
            return {
                "text": "Does that cut storage and switching costs?",
                "timestamps": [],
            }

        def ask(self, prompt, **kwargs):
            assert "MEDIA-MUST-STAY-LOCAL" not in prompt
            if prompt.startswith("Rephrase one"):
                return {"text": "Does that make storage and task changes cheaper?"}
            return {
                "passed": False,
                "issues": [{"reason": "The assigned claim is still missing."}],
                "missing_claim_ids": ["n1"],
            }

    provider = Provider()
    assert not lessons.lesson_step(job, provider)
    assert (
        db.one("SELECT state FROM chapters WHERE id='repair'")["state"]
        == "audio_rephrase"
    )
    assert not lessons.lesson_step(job, provider)
    changed = db.one("SELECT * FROM chapters WHERE id='repair'")
    assert changed["state"] == "review"
    assert changed["data"]["turns"][0]["audio"] is None
    assert (
        changed["data"]["turns"][0]["audio_rephrase_history"][0]["audio"]
        == "original.wav"
    )
    assert (
        database / "original.wav"
    ).read_bytes() == b"original audio remains available"
    assert not lessons.lesson_step(job, provider)
    assert db.one("SELECT state FROM chapters WHERE id='repair'")["state"] == "revise"


def test_saved_audio_is_reused_when_only_asr_spelling_differs(database):
    pid = papers.register({"source_id": "spoken-symbol", "version": "v1", "title": "Symbols"})
    sid = "symbol-source"
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (sid, pid, "page", db.dumps({"text": "The paper defines d ff."})),
    )
    lid = lessons.create(pid)
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    lesson["data"].update(
        phase="chapters",
        notes=[{"claims": [{"id": "n1", "claim": "The paper defines d ff.", "source_ids": [sid]}]}],
        outline=[{"title": "Symbols"}],
        glossary=[],
    )
    db.save_lesson(lesson)
    (database / "audio" / "symbol.wav").write_bytes(b"saved voice")
    turn = {
        "id": "turn",
        "speaker": "host",
        "kind": "question",
        "text": "What does the four-fold change in d ff mean?",
        "source_ids": [],
        "audio": "audio/symbol.wav",
        "audio_verified": False,
        "audio_check": {"transcript": "What does the fourfold change in DFF mean?"},
        "audio_retries": 2,
    }
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        (
            "symbol-chapter", lid, 0, "audio_rephrase",
            db.dumps({"title": "Symbols", "claim_ids": ["n1"], "turns": [turn], "audio_rephrase_turn": "turn"}),
        ),
    )
    jid = db.enqueue("lesson", lid)

    class NoModelNeeded:
        def ask(self, *args, **kwargs):
            raise AssertionError("The already checked audio must not be rewritten")

    assert not lessons.lesson_step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), NoModelNeeded())
    chapter = db.one("SELECT * FROM chapters WHERE id='symbol-chapter'")
    assert chapter["state"] == "audio_review"
    assert chapter["data"]["turns"][0]["audio_verified"]
    assert (database / "audio" / "symbol.wav").read_bytes() == b"saved voice"
