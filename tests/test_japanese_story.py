import copy
import time
import wave
from pathlib import Path

import pytest
from paperspeak import (
    audience,
    db,
    learning_home,
    papers,
    publication,
    story,
    story_video,
    video,
    video_library,
    video_overlay,
)
from paperspeak import (
    japanese_story as ja,
)
from paperspeak.runtime import PracticePreempted


def project():
    pid = papers.register(
        {"source_id": "japanese-film", "version": "v1", "title": "A Robot World Model"}
    )
    result = story.create(pid)
    return db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))


def wave_file(path, seconds=2):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(b"\0\0" * round(seconds * 24000))


def english_ready(p):
    source = p["paper_id"] + ":claim"
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (
            source,
            p["paper_id"],
            "text",
            db.dumps({"text": "The predictor has 8B parameters."}),
        ),
    )
    p["data"]["evidence"] = [
        {
            "id": "C1",
            "source_ids": [source],
            "claim": "The predictor has 8B parameters.",
        }
    ]
    track = p["data"]["modes"]["deep_dive"]
    track.update(
        outline={"central_question": "How?"},
        release_check={"passed": True},
        packaging={
            "title": "English detail",
            "title_en": "English detail",
            "candidates": [],
        },
    )
    track["scenes"] = [
        {
            "title": "Predictor",
            "title_ja": "予測器",
            "focus": "Prediction",
            "claim_ids": ["C1"],
            "visual": {
                "type": "flow",
                "nodes": [{"en": "Input", "ja": "入力"}],
                "caption_en": "Prediction",
                "caption_ja": "予測",
            },
            "utterances": [
                {
                    "id": "english-turn",
                    "speaker": "guide",
                    "text": "The predictor has 8B parameters.",
                    "source_ids": [source],
                    "visual_focus": 0,
                    "audio": "audio/english.wav",
                    "duration": 2,
                    "sentence_ranges": [["The predictor has 8B parameters.", 0, 2]],
                }
            ],
            "subtitle_items": {
                "0:0": {
                    "english": "The predictor has 8B parameters.",
                    "japanese": "予測器には80億個のパラメータがあります。",
                }
            },
            "reviews": {},
            "visual_ready": True,
        }
    ]
    story.save(p)
    return track


def test_default_and_api_retire_overview_and_keep_order(client):
    p = project()
    assert list(p["data"]["modes"]) == ["deep_dive", "deep_dive_ja"]
    assert p["data"]["modes"]["deep_dive_ja"]["subtitle_languages"] == ["ja"]
    assert (
        client.post(
            f"/api/papers/{p['paper_id']}/video-projects", json={"modes": ["overview"]}
        ).status_code
        == 422
    )
    with pytest.raises(ValueError, match="retired"):
        story.create(p["paper_id"], modes=["overview"])
    same = story.create(p["paper_id"], modes=["deep_dive_ja", "deep_dive"])
    assert same["project_id"] == p["id"]
    data = client.get(f"/api/video-projects/{p['id']}").json()["data"]["modes"]
    assert data["deep_dive_ja"]["language"] == "ja"


def test_japanese_is_excluded_from_english_lessons_and_home(client):
    p = project()
    japanese_lid = p["data"]["modes"][ja.MODE]["lesson_id"]
    assert japanese_lid not in [row["id"] for row in client.get("/api/lessons").json()]
    assert all(
        row.get("lesson_id") != japanese_lid
        for row in learning_home.dashboard().get("courses", [])
    )


def test_japanese_localization_requires_completed_english_and_never_copies_audio(
    database,
):
    p = project()
    with pytest.raises(ValueError, match="Finish the English"):
        ja.script_step(p, object())
    original = copy.deepcopy(english_ready(p))
    ja.script_step(p, object())
    japanese = p["data"]["modes"][ja.MODE]
    assert japanese["scenes"][0]["utterances"] == []
    assert not japanese["scenes"][0].get("visual_ready")
    assert japanese["scenes"][0]["language"] == "ja"
    assert p["data"]["modes"]["deep_dive"] == original


def test_localized_turn_preserves_provenance_and_uses_japanese_writer(database):
    p = project()
    english_ready(p)
    ja.script_step(p, object())

    class Model:
        def ask(self, prompt, **kwargs):
            assert "Japanese" in kwargs["system"]
            return {
                "text": "予測器には80億個のパラメータがあります。",
                "spoken_text": "予測器には80億個のパラメータがあります。",
            }

    ja.script_step(p, Model())
    turn = p["data"]["modes"][ja.MODE]["scenes"][0]["utterances"][0]
    assert turn["source_ids"] == [p["paper_id"] + ":claim"]
    assert turn["origin_utterance_id"] == "english-turn"
    assert turn["id"] != "english-turn" and "audio" not in turn


def test_preemption_does_not_consume_localization_budget(database):
    p = project()
    english_ready(p)
    ja.script_step(p, object())

    class Model:
        def ask(self, *args, **kwargs):
            raise PracticePreempted("Recording")

    with pytest.raises(PracticePreempted):
        ja.script_step(p, Model())
    assert p["data"]["repairs"]["ja_localize:0:0"]["attempts"] == 0


@pytest.mark.parametrize(
    "text",
    [
        "短い字幕です。",
        "原理を具体例で考えながら、予測と実際の結果の違いを順に確認していきます。" * 8,
        "V-JEPA 2.1は画像から特徴を取り出します。",
    ],
)
def test_large_japanese_captions_keep_every_character_and_stay_inside_footer(text):
    pages = video_overlay.caption_pages("", text, languages=["ja"])
    assert "".join(p["japanese"] for p in pages) == text
    for page in pages:
        layout = page["layout"]
        assert not layout["english"]
        assert 54 <= layout["ja_size"] <= 64
        assert len(layout["japanese"]) <= 3
        assert all(
            video_overlay._width(line, layout["ja_size"]) <= video_overlay.CAPTION_WIDTH
            for line in layout["japanese"]
        )


def test_japanese_ass_has_only_japanese_dialogue_and_same_duration(database):
    wav = database / "audio/ja.wav"
    wave_file(wav)
    turns = [
        {
            "english": "",
            "japanese": "これは日本語の詳しい解説です。",
            "frames": 48000,
            "audio": "audio/ja.wav",
            "speaker": "guide",
        }
    ]
    tracks, ass, duration = video._captions(turns, animate=True, languages=["ja"])
    assert not tracks["en"] and "日本語" in tracks["ja"]
    assert ",English," not in "\n".join(
        line for line in ass.splitlines() if line.startswith("Dialogue:")
    )
    assert "\\fs64" in ass and duration == 2


def test_japanese_alignment_retains_text_and_uses_its_own_times():
    text = "これは予測です。次は実験です。"
    parts = ja.chunks(text)
    stamps = [
        {"word": "これは予測です", "start": 0.1, "end": 2},
        {"word": "次は実験です", "start": 3, "end": 5},
    ]
    ranges = ja.aligned_ranges(parts, stamps, 6)
    assert "".join(r[0] for r in ranges) == text
    assert ranges[0][1] == 0 and ranges[1][1] == 3 and ranges[-1][2] == 6
    fallback = ja.aligned_ranges(parts, [], 6)
    assert fallback[0][1] == 0 and fallback[-1][2] == 6


def test_speech_comparison_handles_japanese_and_protects_numbers_and_negation():
    assert ja.speech_check("モデルは8個です。", "モデルは８個です")[1]
    assert not ja.speech_check("モデルは8個です。", "モデルは9個です。")[1]
    assert not ja.speech_check("万能ではありません。", "万能です。")[1]
    assert ja.speech_check("カメラです。", "かめらです")[1]


def test_japanese_tts_uses_voice_design_for_both_roles_and_pinned_language(
    database, monkeypatch
):
    p = project()
    english_ready(p)
    t = p["data"]["modes"][ja.MODE]
    t["scenes"] = [
        {
            "utterances": [
                {"id": "ja-m", "speaker": "guide", "text": "日本語です。"},
                {"id": "ja-a", "speaker": "host", "text": "なぜですか？"},
            ]
        }
    ]
    calls = []

    class Runtime:
        def speech(self, operation, request):
            calls.append((operation, request))
            wave_file(request["output"])
            return {
                "duration": 2,
                "generation_settings": {"language": request["language"]},
            }

    for _ in range(3):
        story._tts_step(p, Runtime(), ja.MODE)
    assert len(calls) == 2 and all(
        op == "tts_design" and req["language"] == "Japanese" for op, req in calls
    )
    assert calls[0][1]["instruction"] != calls[1][1]["instruction"]
    assert t["phase"] == "align"


def test_japanese_release_identity_description_and_no_english_practice(database):
    p = project()
    english_ready(p)
    ja.script_step(p, object())
    publication.package(p)
    description = publication.description(p, ja.MODE)
    assert "日本語解説" in description and "日本語音声" in description
    assert "自然な英語" not in description and "#英語学習" not in description
    assert publication.identity(p, "deep_dive")["edition"] == "英語・詳細解説"


def test_japanese_personas_are_native_not_english_learners(database, monkeypatch):
    captured = []

    class Model:
        def ask(self, prompt, **kwargs):
            captured.append(prompt)
            return {"personas": []}

    monkeypatch.setattr(audience, "validate", lambda result, _: result)
    audience.assess(Model(), ja.MODE, [], profile="qwen-q8", checkpoint="scene")
    assert (
        "NOT English practice" in captured[0] and "mathematical notation" in captured[0]
    )


def test_japanese_video_library_links_to_same_project_english_lesson(database):
    p = project()
    lid = p["data"]["modes"][ja.MODE]["lesson_id"]
    path = database / "videos/test-ja.mp4"
    path.write_bytes(b"video")
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "ja-export",
            lid,
            "",
            ja.MODE,
            "digest",
            "ready",
            db.dumps(
                {"mp4": "videos/test-ja.mp4", "title": "日本語", "language": "ja"}
            ),
            time.time(),
            time.time(),
        ),
    )
    rows = video_library.catalogue()
    assert rows[0]["kind"] == ja.MODE and rows[0]["data"]["language"] == "ja"
    assert (
        rows[0]["data"]["english_lesson_id"]
        == p["data"]["modes"]["deep_dive"]["lesson_id"]
    )


def test_japanese_manifest_language_is_part_of_identity(database, monkeypatch):
    p = project()
    t = english_ready(p)
    ja.script_step(p, object())
    japanese = p["data"]["modes"][ja.MODE]
    japanese["scenes"] = copy.deepcopy(t["scenes"])
    japanese["scenes"][0]["render_paths"] = ["visuals/ja.png"]
    monkeypatch.setattr(publication, "prepare", lambda _: None)
    eid = story_video.enqueue(p, ja.MODE)
    manifest = db.one("SELECT data FROM video_exports WHERE id=?", (eid,))["data"][
        "manifest"
    ]
    assert manifest["language"] == "ja" and manifest["subtitle_languages"] == ["ja"]


def test_reuses_complete_modern_source_groups_without_restart_from_pdf(database):
    p = project()
    source = p["paper_id"] + ":proof"
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (source, p["paper_id"], "text", db.dumps({"text": "Verified primary source."})),
    )
    from paperspeak import lessons

    p["data"].update(
        version="youtube-storyboard-7-paper-types",
        phase="production",
        reading_complete=True,
        reading_index=len(lessons.source_groups(papers.reading_sources(p["paper_id"]))),
        evidence=[{"id": "C1", "claim": "Verified", "source_ids": [source]}],
        figure_index=12,
    )
    # Distinct older fingerprint: this fixture represents the pre-migration project.
    db.execute(
        "UPDATE video_projects SET input_digest='old-language-format' WHERE id=?",
        (p["id"],),
    )
    story.save(p)
    new = story.create(p["paper_id"])
    current = db.one("SELECT * FROM video_projects WHERE id=?", (new["project_id"],))
    assert current["data"]["phase"] == "plan"
    assert (
        current["data"]["reading_includes_structured"]
        and current["data"]["figure_index"] == 12
    )


def test_japanese_visual_cues_do_not_reuse_english_seconds():
    utterance = {
        "text": "まず入力を見ます。次に出力を見ます。",
        "duration": 8,
        "visual_beat": 0,
        "visual_cues": [{"focus": 1, "at_text": "次に出力"}],
        "audio_check": {
            "timestamps": [
                {"word": "まず入力を見ます", "start": 0, "end": 2},
                {"word": "次に出力を見ます", "start": 5, "end": 7},
            ]
        },
    }
    scene = {"storyboard": {}, "beat_render_map": {"0": 0}}
    scene["storyboard"] = {"beats": [{}]}
    events = ja.visual_events(scene, utterance)
    assert events[0] == {"start": 0, "focus": 0}
    assert events[1]["start"] == 5 and events[1]["focus"] == 1


def test_japanese_asr_and_subtitles_do_not_materialize_english_clips(database):
    p = project()
    t = p["data"]["modes"][ja.MODE]
    wave_file(database / "audio/ja-align.wav")
    t["scenes"] = [
        {
            "utterances": [
                {
                    "id": "j",
                    "speaker": "guide",
                    "text": "8個です。",
                    "spoken_text": "8個です。",
                    "audio": "audio/ja-align.wav",
                    "duration": 2,
                }
            ]
        }
    ]

    class Runtime:
        def speech(self, op, request):
            assert op == "asr" and request["language"] == "Japanese"
            return {
                "text": "8個です。",
                "timestamps": [{"word": "8個です", "start": 0, "end": 1.5}],
            }

    story._align_step(p, Runtime(), ja.MODE)
    story._align_step(p, Runtime(), ja.MODE)
    story._align_step(p, Runtime(), ja.MODE)
    scene = t["scenes"][0]
    assert scene["utterances"][0]["audio_check"]["metric"] == "ja_character_error_rate"
    assert scene["subtitle_items"]["0:0"] == {"japanese": "8個です。"}
    assert not db.all("SELECT * FROM chapters WHERE lesson_id=?", (t["lesson_id"],))


def test_pronunciation_text_cannot_change_negation_or_leave_latex():
    original = {"text": "The model has 8 parameters."}
    with pytest.raises(ValueError, match="negation"):
        ja._validate_localized(
            {"text": "8個ですが万能ではありません。", "spoken_text": "8個で万能です。"},
            original,
        )
    with pytest.raises(ValueError, match="LaTeX"):
        ja._validate_localized(
            {"text": "8個です。", "spoken_text": r"8個で \frac{1}{2} です。"}, original
        )


def test_numeric_error_diagnostics_are_json_serializable():
    import json

    difference, accepted = ja.speech_check("8個です。", "9個です。")
    assert not accepted and difference["lost_numbers"] == ["8"]
    assert json.loads(json.dumps(difference))["lost_numbers"] == ["8"]


def test_japanese_title_label_change_does_not_duplicate_a_running_project(
    database, monkeypatch
):
    p = project()
    preset = dict(story.MODES[ja.MODE])
    preset["label"] = "別の表示名"
    monkeypatch.setitem(story.MODES, ja.MODE, preset)
    assert story.create(p["paper_id"])["project_id"] == p["id"]
