import time
import wave

import pytest
from paperspeak import config, db, papers, story, story_video, storyboards
from paperspeak.runtime import PracticePreempted


def project():
    paper = papers.register(
        {
            "source_id": "storyboard-test",
            "version": "v1",
            "title": "Native Surface Representation",
        }
    )
    return db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper, modes=["overview"])["project_id"],),
    )


def test_3d_research_titles_and_conference_years_do_not_become_false_hype():
    hooks = {
        "hook_candidates": [
            {
                "title_en": f"3D generation at CVPR 2026: idea {i}",
                "title_ja": f"3D生成・CVPR 2026・発想{i}",
                "hook": "Why is representing the surface harder than storing a box?",
                "thumbnail_ja": "形をどう記録する？",
            }
            for i in range(3)
        ]
    }
    assert story.validate_hooks(hooks) is hooks
    hooks["hook_candidates"][0]["title_en"] = "100x faster for every 3D object"
    with pytest.raises(ValueError, match="performance promises"):
        story.validate_hooks(hooks)


@pytest.mark.parametrize("structured", [False, True])
def test_source_reuse_preserves_reading_coverage_and_does_not_copy_old_script(
    database, structured
):
    previous = project()
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (
            "main-source",
            previous["paper_id"],
            "section",
            db.dumps({"text": "Evidence"}),
        ),
    )
    fact = {"id": "C1", "claim": "Checked claim", "source_ids": ["main-source"]}
    previous["state"] = "ready"
    previous["data"].update(
        evidence=[fact, {"id": "C2", "source_ids": ["other-paper-source"]}],
        reading_includes_structured=structured,
    )
    previous["data"]["modes"]["overview"]["scenes"] = [
        {"utterances": [{"text": "Old script", "audio": "old.wav"}]}
    ]
    story.save(previous)
    result = story.create(previous["paper_id"], modes=["deep_dive"])
    current = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    assert current["data"]["evidence"] == [fact]
    assert current["data"]["reading_includes_structured"] is structured
    assert current["data"]["modes"]["deep_dive"]["scenes"] == []
    assert (
        db.one("SELECT state FROM video_projects WHERE id=?", (previous["id"],))[
            "state"
        ]
        == "ready"
    )


def test_one_film_project_completes_without_creating_or_accessing_deep_dive(
    database, monkeypatch
):
    p = project()
    assert set(p["data"]["modes"]) == {"overview"}
    assert len(db.all("SELECT id FROM lessons WHERE paper_id=?", (p["paper_id"],))) == 1
    t = p["data"]["modes"]["overview"]
    t.update(
        phase="export",
        export_id="export",
        packaging={"title": "An overview", "title_en": "An overview"},
    )
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "export",
            t["lesson_id"],
            "",
            "overview",
            "digest",
            "ready",
            "{}",
            time.time(),
            time.time(),
        ),
    )
    p["data"].update(phase="production", current_mode="overview")
    story.save(p)
    monkeypatch.setattr(
        story_video,
        "finalize_packaging",
        lambda *_args, **_kwargs: {"status": "passed"},
    )
    job = db.one(
        "SELECT * FROM jobs WHERE target=? AND kind='video_project'", (p["id"],)
    )
    assert story.step(job, None)
    assert (
        db.one("SELECT state FROM video_projects WHERE id=?", (p["id"],))["state"]
        == "ready"
    )


def test_visual_cue_times_follow_recognized_words_and_skip_missing_anchors():
    scene = {
        "storyboard": {"beats": [{"focus": 0}, {"region": "panel", "focus": 0}]},
        "visual": {"zoom_start": 2, "zoom_regions": [{"id": "panel"}]},
        "render_paths": ["whole", "unused", "panel"],
    }
    u = {
        "audio_check": {
            "timestamps": [
                {"text": "Notice", "start": 0.8},
                {"text": "the", "start": 1.1},
                {"text": "opening.", "start": 1.4},
            ]
        },
        "visual_cues": [
            {"phrase": "the opening", "beat": 1},
            {"phrase": "absent words", "beat": 1},
        ],
    }
    assert storyboards.timed_cues(scene, u) == [
        {"start": 1.1, "focus": 2, "phrase": "the opening", "method": "ASR word anchor"}
    ]


def test_visual_cuts_preserve_every_audio_sample_and_subtitle_duration(database):
    source = database / "audio" / "original.wav"
    raw = b"".join((i % 5000).to_bytes(2, "little", signed=True) for i in range(72000))
    with wave.open(str(source), "wb") as f:
        f.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        f.writeframes(raw)
    scene = {
        "title_ja": "内部構造",
        "visual": {"type": "original"},
        "render_paths": ["whole.png", "detail.png"],
        "utterances": [
            {
                "id": "u",
                "speaker": "guide",
                "text": "Notice the opening.",
                "audio": "audio/original.wav",
                "duration": 3,
                "visual_focus": 0,
                "visual_events": [{"start": 1.1, "focus": 1}],
                "sentence_ranges": [["Notice the opening.", 0, 3]],
            }
        ],
        "subtitle_items": {"0:0": {"japanese": "開口部を見てください。"}},
    }
    root = database / "jobs" / "cuts"
    root.mkdir()
    speech, captions, _, _ = story_video.timeline({"scenes": [scene]}, root)
    assert [s["scene"] for s in speech] == ["whole.png", "detail.png"]
    assert (
        sum(s["frames"] for s in speech) == sum(c["frames"] for c in captions) == 72000
    )
    actual = b""
    for segment in speech:
        with wave.open(str(config.safe_path(segment["audio"])), "rb") as f:
            actual += f.readframes(f.getnframes())
    assert actual == raw


def test_recording_interrupt_does_not_consume_storyboard_review_attempt(database):
    p = project()
    s = {
        "title": "A shape",
        "title_ja": "形状",
        "focus": "Keep the shape",
        "storyboard": {"question": "What remains?"},
        "storyboard_preview": ["preview.png"],
    }
    p["data"]["modes"]["overview"]["scenes"] = [s]

    class Interrupted:
        def ask(self, *_args, **_kwargs):
            raise PracticePreempted("A recording is ready")

    with pytest.raises(PracticePreempted):
        storyboards.step(p, Interrupted(), "overview", 0)
    assert s["storyboard_review"]["attempts"] == 0
    assert s["storyboard_preview"] == ["preview.png"]


def test_fallback_keeps_verified_detail_and_hides_internal_direction(monkeypatch):
    monkeypatch.setattr(
        story,
        "original_catalogue",
        lambda _: [
            {
                "asset_id": "source",
                "label": "Figure 5",
                "caption": "Generated objects",
                "regions": [{"id": "trellis"}],
            }
        ],
    )
    scene = {
        "title": "A concrete example",
        "title_ja": "具体例",
        "focus": "Walk through an example. Aiden mistakenly thinks it fills gaps; Maya corrects him.",
        "visual_type": "original",
        "visual_asset_id": "source",
        "visual_focus_region": "trellis",
    }
    result = storyboards.fallback({}, scene)
    assert result["beats"][0].get("region") is None
    assert result["beats"][1]["region"] == "trellis"
    assert "Walk through" not in result["visual"]["caption_en"]
    assert "Maya" not in result["visual"]["caption_en"]
    assert result["visual"]["original_asset_id"] == "source"


def test_surface_cell_template_renders_actual_shapes_locally(database):
    p = project()
    s = {
        "title": "Store the surface",
        "title_ja": "表面を記録する",
        "focus": "Surface versus empty space",
        "visual": {
            "type": "example",
            "template": "surface_cells",
            "nodes": [
                {"en": "Full grid", "ja": "空間全体"},
                {"en": "Surface cells", "ja": "表面のセル"},
                {"en": "Shape and material", "ja": "形と材質"},
            ],
            "caption_en": "A conceptual 2D sketch, not measured output.",
            "caption_ja": "実験結果ではなく、2次元の模式図です。",
        },
        "utterances": [{"text": "Notice the empty space.", "visual_focus": 1}],
    }
    p["data"]["modes"]["overview"]["scenes"] = [s]
    story_video.render_scene(p, "overview", 0)
    assert len(s["render_paths"]) == 3
    assert all(
        config.safe_path(path).stat().st_size > 5000 for path in s["render_paths"]
    )


def test_zoomed_chart_keeps_its_separate_verified_legend_visible(database):
    import numpy as np
    import pymupdf

    p = project()
    image = database / "chart.png"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=1000, height=1000)
        page.draw_rect((200, 100, 800, 450), color=(0, 0, 1), fill=(0, 0, 1))
        # Unique magenta pixels occur only in the separate legend.
        page.draw_rect((200, 700, 800, 850), color=(1, 0, 1), fill=(1, 0, 1))
        page.get_pixmap().save(image)
    asset = {
        "image_path": "chart.png",
        "label": "Figure 1",
        "page": 1,
        "review": {"passed": True},
        "regions": [
            {
                "id": "plot",
                "label_en": "Plot",
                "label_ja": "グラフ",
                "box": [0.2, 0.1, 0.6, 0.4],
            },
            {
                "id": "legend",
                "label_en": "Legend",
                "label_ja": "凡例",
                "box": [0.2, 0.7, 0.6, 0.2],
            },
        ],
    }
    db.execute(
        "INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
        ("chart", p["paper_id"], None, "original", db.dumps(asset), time.time()),
    )
    scene = {
        "title": "Read the comparison",
        "title_ja": "比較を読む",
        "focus": "Match colors and methods",
        "visual": {
            "type": "original",
            "original_asset_id": "chart",
            "nodes": [{"en": "Compare", "ja": "比較"}],
        },
        "utterances": [{"text": "Look at the chart.", "visual_focus_region": "plot"}],
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]
    story_video.render_scene(p, "overview", 0)
    pix = pymupdf.Pixmap(str(config.safe_path(scene["render_paths"][-1])))
    # Ignore the context inset and avatar footer; the MAIN zoom must contain it.
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[
        204:660, 100:1500, :3
    ]
    assert ((rgb[..., 0] > 240) & (rgb[..., 1] < 20) & (rgb[..., 2] > 240)).sum() > 1000


def test_editor_names_exact_repeated_questions_without_flagging_short_reactions():
    repeated = "Can it really generate these high-detail assets without taking an hour on a GPU?"
    flags = story.duplicate_dialogue_flags(
        {
            "utterances": [
                {"id": "first", "text": repeated + " That sounds good."},
                {
                    "id": "answer",
                    "text": "Yes, in the authors' reported setup. That sounds good.",
                },
                {"id": "later", "text": "But wait. " + repeated},
            ]
        }
    )
    assert len(flags) == 1
    assert flags[0]["utterance_id"] == "later"
    assert flags[0]["earlier_utterance_id"] == "first"
    assert flags[0]["sentence"] == repeated
