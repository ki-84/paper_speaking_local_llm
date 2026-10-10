import re
import wave

import pytest
from paperspeak import config, db, nightly, papers, story, video, video_overlay


def long_captions():
    english = (
        "The system can follow the phone call while somebody explains quantum physics nearby, "
        "but the training data did not teach it whose conversation was actually directed at it."
    )
    japanese = (
        "モデルは電話の声を聞き取れますが、近くで量子物理学を説明する声も聞こえます。"
        "どの会話が自分に向けられているのかを見分ける必要があります。"
        "訓練データにその区別を教える情報が不足していたことを著者らは限界として挙げています。"
        "これは音が聞こえないという話ではなく、誰が誰に話しかけているかという問題です。"
    )
    return english, japanese


def project():
    paper = papers.register(
        {"source_id": "caption-recovery", "version": "v1", "title": "A paper"}
    )
    return db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper, legacy=True)["project_id"],),
    )


def test_caption_overflow_is_split_without_truncation_or_unreadable_type():
    en, ja = long_captions()
    with pytest.raises(ValueError, match="footer"):
        video_overlay.layout_captions(en, ja)
    pages = video_overlay.caption_pages(en, ja)
    assert len(pages) > 1
    assert "".join(p["english"] for p in pages) == en
    assert "".join(p["japanese"] for p in pages) == ja
    for page in pages:
        layout = page["layout"]
        assert len(layout["english"]) <= 3 and len(layout["japanese"]) <= 3
        assert layout["en_size"] >= 25 and layout["ja_size"] >= 25
        assert (
            layout["ja_top"] + len(layout["japanese"]) * layout["ja_size"] * 1.17
            <= 1070
        )


def test_long_identifiers_and_asymmetric_translations_do_not_loop_or_lose_text():
    for en, ja in [("A" * 700, "カ" * 600), ("Yes.", "説明文。" * 140)]:
        pages = video_overlay.caption_pages(en, ja)
        assert "".join(p["english"] for p in pages) == en
        assert "".join(p["japanese"] for p in pages) == ja
        assert len(pages) < max(len(en), len(ja))


def test_subtitle_pages_leave_audio_and_mouth_timing_continuous(database, monkeypatch):
    en, ja = long_captions()
    audio = database / "audio" / "original.wav"
    frames = 240000
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * frames)
    calls = []
    monkeypatch.setattr(
        video_overlay,
        "mouth_events",
        lambda role, path, count, offset, settings: calls.append(
            (role, path, count, offset)
        )
        or [],
    )
    words = en.split()
    stamps = [
        {"word": word, "start": i * 10 / len(words), "end": (i + 1) * 10 / len(words)}
        for i, word in enumerate(words)
    ]
    tracks, ass, duration = video._captions(
        [
            {"silence": True, "frames": 24000},
            {
                "speaker": "guide",
                "audio": "audio/original.wav",
                "frames": frames,
                "english": en,
                "japanese": ja,
                "word_timestamps": stamps,
            },
        ],
        animate=True,
    )
    assert duration == 11
    assert calls == [("guide", config.safe_path("audio/original.wav"), frames, 24000)]
    assert "00:00:01,000 -->" in tracks["en"]
    assert "--> 00:00:11,000" in tracks["en"]

    def text(srt):
        return "".join(
            line
            for line in srt.splitlines()
            if line and "-->" not in line and not line.isdigit()
        )

    assert re.sub(r"\s", "", text(tracks["en"])) == re.sub(r"\s", "", en)
    assert text(tracks["ja"]) == ja
    assert ass.count("Dialogue: 10,") == len(video_overlay.caption_pages(en, ja)) * 2


@pytest.mark.parametrize(
    "text", ['Maya: "One point."', 'One point.\\n\\nAiden: "A reply."']
)
def test_spoken_turns_reject_embedded_multi_speaker_script(text):
    with pytest.raises(ValueError, match="One speaker"):
        story.validate_script(
            {
                "utterances": [
                    {
                        "speaker": "guide",
                        "text": text,
                        "kind": "paper",
                        "source_ids": ["S1"],
                    }
                ]
            },
            "deep_dive",
            {"S1"},
        )


def test_subtitle_cache_is_bound_to_the_actual_english(database):
    p = project()
    scene = {
        "utterances": [
            {"text": "The new point.", "sentence_ranges": [["The new point.", 0, 2]]}
        ],
        "subtitle_items": {
            "0:0": {
                "id": "0:0",
                "english": "The old point.",
                "japanese": "古い内容です。",
            }
        },
    }
    p["data"]["modes"]["deep_dive"]["scenes"] = [scene]

    class Translator:
        def ask(self, prompt, **kwargs):
            assert "The new point." in prompt
            return {"items": [{"id": "0:0", "japanese": "新しい内容です。"}]}

    story._subtitle_step(p, Translator(), "deep_dive", 0)
    assert scene["subtitle_items"]["0:0"]["english"] == "The new point."
    assert scene["subtitle_items"]["0:0"]["japanese"] == "新しい内容です。"
    story._subtitle_step(p, None, "deep_dive", 0)
    assert scene["subtitles_ready"]


def test_review_changes_invalidate_old_voice_and_subtitles_but_keep_history(
    database, monkeypatch
):
    p = project()
    scene = {
        "title": "A point",
        "focus": "One idea",
        "visual": {"nodes": []},
        "utterances": [
            {
                "id": "u",
                "speaker": "guide",
                "kind": "paper",
                "source_ids": ["S1"],
                "text": "The original point.",
                "audio": "audio/old.wav",
                "duration": 2,
                "aligned": True,
            }
        ],
        "subtitles_ready": True,
        "clips_ready": True,
    }
    p["data"]["modes"]["deep_dive"]["scenes"] = [scene]
    monkeypatch.setattr(story, "context_for", lambda *_: {})
    monkeypatch.setattr(story, "source_lookup", lambda *_: {"S1": {}})

    class Editor:
        def ask(self, prompt, **kwargs):
            return {
                "issues": [
                    {
                        "utterance_id": "u",
                        "reason": "Correct the point",
                        "replacement": "The corrected point.",
                        "source_ids": ["S1"],
                    }
                ],
                "visual_issues": [],
            }

    story._review_scene(p, Editor(), "deep_dive", scene, 0, "editorial")
    u = scene["utterances"][0]
    assert u["text"] == "The corrected point."
    assert not u.get("audio") and not u.get("aligned")
    assert u["audio_history"][0]["audio"] == "audio/old.wav"
    assert not scene.get("subtitles_ready") and not scene.get("clips_ready")


def test_resuming_a_project_does_not_revive_superseded_exports(database):
    p = project()
    lid = p["data"]["modes"]["deep_dive"]["lesson_id"]
    import time

    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "obsolete",
            lid,
            "",
            "deep_dive",
            "digest",
            "failed",
            "{}",
            time.time(),
            time.time(),
        ),
    )
    old = db.enqueue("story_video", "obsolete")
    db.patch_job(old, state="cancelled", checkpoint={"superseded": True})
    nightly.control_project(p["id"], "queued")
    assert db.one("SELECT state FROM jobs WHERE id=?", (old,))["state"] == "cancelled"
