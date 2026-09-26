from paperspeak import db, lessons, papers, practice
from paperspeak.quality import word_diff


def stamps(line):
    return [
        {"word": word, "start": i * 0.2, "end": i * 0.2 + 0.15}
        for i, word in enumerate(line.split())
    ]


def test_changed_word_gives_a_cautious_sound_cue_and_word_clips():
    focus = practice.pronunciation_focus(
        word_diff("The whole model works", "The four model works"),
        stamps("The whole model works"),
        stamps("The four model works"),
    )
    item = focus["items"][0]
    assert focus["status"] == "word_recognition_only"
    assert (item["expected"], item["heard"], item["sound"]) == (
        "whole", "four", "/h/"
    )
    assert (item["reference_start"], item["start"]) == (0.2, 0.2)
    assert "Compare" in item["message"]
    assert "練習" in item["tip_ja"]
    assert "not proof" in focus["message"]


def test_identical_sounds_are_not_called_pronunciation_errors():
    for expected, heard in [("colour", "color"), ("two", "to")]:
        item = practice.pronunciation_focus(
            word_diff(f"Say {expected} again", f"Say {heard} again"), [], []
        )["items"][0]
        assert item["kind"] == "same_sound"
        assert item["sound"] is None and item["tip"] is None
        assert "alone" in item["message"]


def test_missing_timestamps_do_not_point_to_the_wrong_audio():
    focus = practice.pronunciation_focus(
        word_diff("The whole model works", "The four model works"),
        stamps("The whole model works")[:-1],
        stamps("The four model works"),
    )
    item = focus["items"][0]
    assert item["reference_start"] is None
    assert item["start"] == 0.2
    assert practice.pronunciation_focus(
        word_diff("The model works", "The model works"), [], []
    )["items"] == []


def test_transcription_saves_pronunciation_focus_before_slow_sound_analysis(client):
    pid = papers.register({"source_id": "2106.09685", "version": "v1", "title": "Example"})
    lid = lessons.create(pid)
    line = "The whole model works"
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        (
            "c", lid, 0, "ready",
            db.dumps({"turns": [{
                "id": "t", "text": line, "audio": "audio/example.wav",
                "audio_check": {"timestamps": stamps(line)},
            }]}),
        ),
    )
    response = client.post(
        "/api/attempts", data={"chapter_id": "c", "turn_id": "t"},
        files={"file": ("voice.webm", b"saved voice", "audio/webm")},
    )
    saved = response.json()
    attempt = db.one("SELECT * FROM attempts WHERE id=?", (saved["attempt_id"],))
    attempt["data"].update(
        phase="transcribe", wav="recordings/voice.wav", quality={"duration": 1.0}
    )
    practice.save_attempt(attempt)

    class FakeSpeech:
        def speech(self, model, payload):
            assert model == "asr"
            assert "The whole model works" not in payload["context"]
            return {"text": "The four model works", "timestamps": stamps("The four model works")}

    assert practice.practice_step(
        db.one("SELECT * FROM jobs WHERE id=?", (saved["job_id"],)), FakeSpeech()
    ) is False
    result = client.get("/api/attempts/" + saved["attempt_id"]).json()
    assert result["state"] == "pending"
    assert result["data"]["phase"] == "phones"
    assert result["data"]["pronunciation_focus"]["items"][0]["sound"] == "/h/"
