from pathlib import Path

from paperspeak import db, papers, revoice


def chapter_fixture(database, count=1):
    pid = papers.register({"source_id": "revoice-test", "version": "v1", "title": "Test paper"})
    lid = db.uid()
    cid = db.uid()
    db.execute(
        "INSERT INTO lessons VALUES (?,?,?,?,?,?)",
        (lid, pid, "ready", db.dumps({"title": "Test paper", "glossary": []}), 0, 0),
    )
    (database / "audio").mkdir(exist_ok=True)
    (database / "audio" / "original.wav").write_bytes(b"old voice")
    data = {
        "turns": [{
            "id": f"guide-turn-{i}", "speaker": "guide", "text": "The old weights stay fixed.",
            "voice": "Ryan", "audio": "audio/original.wav", "audio_verified": True,
            "duration": 3.0,
        } for i in range(count)],
    }
    db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)", (cid, lid, 0, "ready", db.dumps(data)))
    return cid


def test_revoice_keeps_old_audio_until_new_audio_is_verified(database):
    cid = chapter_fixture(database)
    assert revoice.schedule() == revoice.schedule()
    job = db.one("SELECT * FROM jobs WHERE kind='revoice' AND target=?", (cid,))

    class Voice:
        def speech(self, mode, request):
            if mode == "tts_design":
                Path(request["output"]).write_bytes(b"new voice")
                return {"duration": 2.0, "generation_settings": {"model": "tts-design"}}
            assert mode == "asr"
            return {"text": "The old weights stay fixed.", "timestamps": []}

    assert not revoice.step(job, Voice())
    pending = db.one("SELECT * FROM chapters WHERE id=?", (cid,))["data"]["turns"][0]
    assert pending["voice"] == "Ryan" and pending["audio"] == "audio/original.wav"
    assert revoice.step(job, Voice())
    updated = db.one("SELECT * FROM chapters WHERE id=?", (cid,))["data"]["turns"][0]
    assert updated["voice"] == "Maya" and updated["audio_verified"]
    assert updated["audio_history"][0]["audio"] == "audio/original.wav"
    assert (database / "audio" / "original.wav").read_bytes() == b"old voice"
    assert (database / updated["audio"]).read_bytes() == b"new voice"


def test_revoice_retries_bad_speech_without_replacing_old_voice(database):
    cid = chapter_fixture(database)
    job = db.one("SELECT * FROM jobs WHERE id=?", (revoice.schedule()[0],))

    class Voice:
        def speech(self, mode, request):
            if mode == "tts_design":
                Path(request["output"]).write_bytes(b"bad voice")
                return {"duration": 2.0}
            return {"text": "Different words.", "timestamps": []}

    assert not revoice.step(job, Voice())
    assert not revoice.step(job, Voice())
    turn = db.one("SELECT * FROM chapters WHERE id=?", (cid,))["data"]["turns"][0]
    assert turn["voice"] == "Ryan"
    assert turn["voice_candidate_attempts"] == 1
    assert turn["voice_candidate_failures"][0]["transcript"] == "Different words."
    db.patch_job(job["id"], state="failed")
    assert revoice.schedule() == []


def test_revoice_batches_generation_before_verification(database):
    cid = chapter_fixture(database, count=2)
    jid = revoice.schedule()[0]
    calls = []

    class Voice:
        def speech(self, mode, request):
            calls.append(mode)
            if mode == "tts_design":
                Path(request["output"]).write_bytes(b"new voice")
                return {"duration": 2.0}
            return {"text": "The old weights stay fixed.", "timestamps": []}

    for _ in range(3):
        assert not revoice.step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), Voice())
    assert calls == ["tts_design", "tts_design", "asr"]
