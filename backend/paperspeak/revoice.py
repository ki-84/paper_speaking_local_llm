"""Replace legacy guide speech one verified sentence at a time."""

from __future__ import annotations

from . import config, db, voices
from .quality import QualityHold, speech_context, speech_match


def remaining(chapter):
    return [
        turn for turn in chapter["data"].get("turns", [])
        if turn.get("speaker") == "guide" and turn.get("voice") == "Ryan"
    ]


def schedule():
    queued = []
    for chapter in db.all("SELECT * FROM chapters WHERE state='ready' ORDER BY rowid"):
        if remaining(chapter):
            previous = db.one(
                "SELECT state FROM jobs WHERE kind='revoice' AND target=? ORDER BY created DESC LIMIT 1",
                (chapter["id"],),
            )
            if previous and previous["state"] in {"failed", "cancelled"}:
                continue
            queued.append(db.enqueue("revoice", chapter["id"], priority=9))
    return queued


def step(job, runtime):
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (job["target"],))
    if not chapter or chapter["state"] != "ready":
        raise ValueError("Only a finished chapter can have its voice refreshed.")
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (chapter["lesson_id"],))
    if not lesson:
        raise ValueError("The lesson is missing.")
    pending = remaining(chapter)
    total = sum(turn.get("speaker") == "guide" for turn in chapter["data"].get("turns", []))
    if not pending:
        db.patch_job(job["id"], progress=1, stage="New guide voice is ready")
        return True
    checkpoint = job["checkpoint"]
    phase = checkpoint.get("phase", "generate")
    if phase == "generate":
        turn = next((t for t in pending if not t.get("voice_candidate")), None)
        if turn is None:
            phase = "verify"
            checkpoint["phase"] = phase
            db.patch_job(job["id"], checkpoint=checkpoint)
    if phase == "verify":
        turn = next((t for t in pending if t.get("voice_candidate")), None)
        if turn is None:
            phase = "generate"
            checkpoint["phase"] = phase
            db.patch_job(job["id"], checkpoint=checkpoint)
            turn = next(t for t in pending if not t.get("voice_candidate"))
    done_count = total - len(pending)
    db.patch_job(
        job["id"],
        progress=done_count / max(1, total),
        stage=f"{'Making' if phase == 'generate' else 'Checking'} Maya's voice {done_count + 1}/{total}",
    )
    candidate = turn.get("voice_candidate")
    if not candidate:
        attempts = turn.get("voice_candidate_attempts", 0)
        if attempts >= 3:
            turn["voice_candidate_attempts"] = 0
            db.save_chapter(chapter)
            raise QualityHold("The replacement voice did not match this sentence after three attempts.")
        key = voices.guide_audio_key(turn)
        path = config.DATA / "audio" / f"{key}-{attempts}.wav"
        seed = (int(key[:8], 16) + attempts) % (2**31)
        result = runtime.speech("tts_design", voices.guide_request(turn, path, seed))
        turn["voice_candidate"] = {
            "audio": str(path.relative_to(config.DATA)),
            "duration": result["duration"],
            "tts_settings": result.get("generation_settings", {}),
        }
        db.save_chapter(chapter)
        db.event("chapter", {"id": chapter["id"]})
        return False
    result = runtime.speech(
        "asr",
        {
            "audio": str(config.safe_path(candidate["audio"])),
            "context": speech_context(
                lesson["data"]["title"],
                lesson["data"].get("glossary", []),
                chapter["data"]["turns"],
            ),
        },
    )
    terms = [g["term"].lower() for g in lesson["data"].get("glossary", []) if g.get("term")]
    diff, acceptable = speech_match(turn["text"], result["text"], terms)
    if not acceptable:
        turn.setdefault("voice_candidate_failures", []).append(
            {"transcript": result["text"], "wer": diff["wer"]}
        )
        turn["voice_candidate_attempts"] = turn.get("voice_candidate_attempts", 0) + 1
        turn.pop("voice_candidate", None)
        db.save_chapter(chapter)
        db.event("chapter", {"id": chapter["id"]})
        return False
    turn.setdefault("audio_history", []).append(
        {
            "audio": turn["audio"],
            "voice": turn.get("voice"),
            "duration": turn.get("duration"),
            "tts_settings": turn.get("tts_settings", {}),
            "audio_check": turn.get("audio_check", {}),
        }
    )
    turn.update(
        audio=candidate["audio"],
        voice=voices.GUIDE_VOICE,
        duration=candidate["duration"],
        tts_settings=candidate["tts_settings"],
        audio_verified=True,
        audio_check={
            "transcript": result["text"],
            "wer": diff["wer"],
            "timestamps": result.get("timestamps", []),
            "asr_settings": result.get("generation_settings", {}),
        },
    )
    turn.pop("voice_candidate", None)
    turn.pop("voice_candidate_attempts", None)
    db.save_chapter(chapter)
    db.event("chapter", {"id": chapter["id"]})
    done = not remaining(chapter)
    if done:
        db.patch_job(job["id"], progress=1, stage="New guide voice is ready")
    return done
