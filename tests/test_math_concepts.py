import copy
import json
import subprocess
import wave

import numpy as np
import pytest
from paperspeak import config, math_concepts, story, story_video, storyboards


def spec(template="parallel_paths"):
    eq = {
        "latex": r"h=W_0x+\frac{\alpha}{r}BAx",
        "en": "Keep the original path and add a learned correction",
        "ja": "元の経路に学習する補正を足す",
    }
    concept = math_concepts.lora(eq)
    if template != "parallel_paths":
        eq = {
            "latex": "y=ax",
            "en": "A relation between the input and output",
            "ja": "入力と出力の関係",
        }
        concept = {
            "template": template,
            "parts": [
                {"en": "Inputs", "ja": "入力"},
                {"en": "Operation", "ja": "操作"},
                {"en": "Result", "ja": "結果"},
            ],
            "symbols": [
                {"latex": "x", "en": "Input", "ja": "入力", "part": 0},
                {
                    "latex": "a",
                    "en": "Operation parameter",
                    "ja": "操作のパラメータ",
                    "part": 1,
                },
                {"latex": "y", "en": "Result", "ja": "結果", "part": 2},
            ],
        }
    return {
        "type": "equation",
        "nodes": concept["parts"],
        "equations": [eq],
        "concepts": [concept],
        "caption_en": eq["en"],
        "caption_ja": eq["ja"],
    }


def test_mathematical_board_requires_concept_before_notation_and_real_symbols(
    monkeypatch,
):
    monkeypatch.setattr(story, "original_catalogue", lambda _p: [])
    visual = spec()
    board = {
        "question": "What changes?",
        "takeaway": "The base stays fixed",
        "humor": "A small seasoning change",
        "visual": visual,
        "beats": [
            {"focus": 0, "notice": "See the two paths"},
            {"focus": 1, "notice": "Map the same paths to the equation"},
        ],
    }
    assert storyboards.validate(board, {}, "deep_dive") is board
    missing = copy.deepcopy(board)
    missing["visual"].pop("concepts")
    with pytest.raises(ValueError, match="conceptual picture"):
        storyboards.validate(missing, {}, "deep_dive")
    wrong_order = copy.deepcopy(board)
    wrong_order["beats"].reverse()
    with pytest.raises(ValueError, match="before revealing"):
        storyboards.validate(wrong_order, {}, "deep_dive")
    wrong_symbol = copy.deepcopy(board)
    wrong_symbol["visual"]["concepts"][0]["symbols"][0]["latex"] = "z"
    with pytest.raises(ValueError, match="absent"):
        storyboards.validate(wrong_symbol, {}, "deep_dive")
    with pytest.raises(ValueError, match="no equations"):
        storyboards.validate(board, {}, "overview")


@pytest.mark.parametrize("template", sorted(math_concepts.TEMPLATES))
def test_real_concept_and_formula_frames_fit_and_keep_colored_meanings(
    database, template
):
    visual = spec(template)
    math_concepts.validate(visual, required=True)
    layouts = []
    for focus in (0, 1):
        source = database / f"{template}-{focus}.json"
        png = source.with_suffix(".png")
        source.write_text(
            json.dumps(
                {
                    "title_en": "Understand the relation",
                    "title_ja": "量の関係を絵で理解する",
                    "mode": "deep_dive",
                    "focus": focus,
                    "visual": visual,
                    "data_root": str(database),
                }
            )
        )
        subprocess.run(
            [
                str(config.ROOT / ".tools/node/bin/node"),
                str(config.ROOT / "scripts/render_story_slide.mjs"),
                str(source),
                str(png),
            ],
            check=True,
            capture_output=True,
            timeout=40,
        )
        assert png.stat().st_size > 10000
        layouts.append(json.loads(png.with_name(png.name + ".layout.json").read_text()))
    assert layouts[0]["visible_equations"] == layouts[0]["visible_symbol_keys"] == 0
    assert layouts[1]["visible_equations"] == 1
    assert layouts[1]["visible_symbol_keys"] == len(visual["concepts"][0]["symbols"])
    assert all(not layout["layout_errors"] for layout in layouts)
    # Actual KaTeX output must use all three colors, not just color the legend.
    assert {t["color"] for t in layouts[1]["colored_formula_terms"]} >= {
        "rgb(37, 127, 158)",
        "rgb(194, 123, 36)",
        "rgb(119, 81, 168)",
    }


def test_fitted_point_in_the_picture_is_the_actual_least_squares_solution():
    result = subprocess.run(
        [
            str(config.ROOT / ".tools/node/bin/node"),
            "--input-type=module",
            "-e",
            "import {constraintExample} from './scripts/math_concept_diagrams.mjs';const e=constraintExample();process.stdout.write(JSON.stringify({constraints:e.constraints,initial:e.initial,fitted:e.fitted,loss_before:e.loss(e.initial),loss_after:e.loss(e.fitted)}));",
        ],
        cwd=config.ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    normals = np.array([r["n"] for r in data["constraints"]])
    targets = np.array([np.dot(r["n"], r["p"]) for r in data["constraints"]])
    expected = np.linalg.lstsq(normals, targets, rcond=None)[0]
    assert np.allclose(data["fitted"], expected)
    assert np.allclose(normals.T @ (normals @ data["fitted"] - targets), 0)
    assert data["loss_after"] < data["loss_before"]


def test_symbol_meanings_use_source_conventions_and_avoid_color_overrides():
    visual = spec()
    visual["concepts"][0]["symbols"][2]["en"] = "Expand the input"
    scene = {"visual": visual, "visual_ready": True}
    math_concepts.ensure(scene, lora_paper=True)
    assert scene["visual"]["concepts"][0]["symbols"][2]["en"] == "Reduce directions"
    assert "visual_ready" not in scene
    scene["visual_ready"] = True
    math_concepts.ensure(scene, lora_paper=True)
    assert scene["visual_ready"]  # A completed source check never loops.
    composite = copy.deepcopy(visual)
    composite["concepts"][0]["symbols"][0]["latex"] = "W_0x+BAx"
    with pytest.raises(ValueError, match="composite"):
        math_concepts.validate(composite)
    changes = math_concepts.normalize_symbols(composite)
    math_concepts.validate(composite)
    assert changes and len(composite["concepts"][0]["symbols"]) == 6
    function = {
        "equations": [{"latex": "y=f(x)"}],
        "concepts": [
            {
                "template": "relationship",
                "parts": visual["concepts"][0]["parts"],
                "symbols": [
                    {"latex": "f(x)", "en": "Mapping", "ja": "変換", "part": 1},
                    {"latex": "x", "en": "Input", "ja": "入力", "part": 0},
                ],
            }
        ],
    }
    math_concepts.normalize_symbols(function)
    math_concepts.validate(function)
    assert function["concepts"][0]["symbols"][0]["latex"] == "f"
    fitting = spec("fit_constraints")
    fitting["concepts"][0]["parts"][2] = {
        "en": "Graph of the loss",
        "ja": "誤差のグラフ",
    }
    assert math_concepts.normalize_parts(fitting) == ["fit_constraints"]
    assert fitting["concepts"][0]["parts"][2]["en"] == "Move toward a better fit"
    assert fitting["concepts"][0]["planned_parts"][2]["en"] == "Graph of the loss"
    assert not math_concepts.normalize_parts(fitting)


def test_script_must_actually_explain_the_concept_before_its_equation():
    board = {
        "visual": spec(),
        "beats": [{"focus": 0}, {"focus": 1}],
    }
    missing = [{"text": "Here is the equation.", "visual_beat": 1}]
    with pytest.raises(ValueError, match="actual dialogue"):
        storyboards.bind_dialogue(board, missing, require_math_sequence=True)
    turns = [
        {"text": "The original path stays fixed.", "visual_beat": 0},
        {"text": "Now connect the two paths to the equation.", "visual_beat": 1},
    ]
    storyboards.bind_dialogue(board, turns, require_math_sequence=True)
    assert [u["visual_focus"] for u in turns] == [0, 1]


@pytest.mark.parametrize("picture_first", [False, True])
def test_best_effort_math_leadin_preserves_every_spoken_sample(database, picture_first):
    raw = (np.arange(72000) % 2000).astype("<i2").tobytes()
    with wave.open(str(database / "audio" / "spoken.wav"), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(raw)
    scene = {
        "title_ja": "意味から式へ",
        "visual": spec(),
        "render_paths": ["concept.png", "formula.png"],
        "utterances": [
            {
                "id": "u",
                "speaker": "guide",
                "audio": "audio/spoken.wav",
                "visual_focus": 0 if picture_first else 1,
                "visual_events": [{"start": 1.1, "focus": 1}] if picture_first else [],
                "sentence_ranges": [
                    ["The base stays fixed; we add a correction.", 0, 3]
                ],
            }
        ],
        "subtitle_items": {"0:0": {"japanese": "元を固定し、補正を足します。"}},
    }
    root = database / "jobs" / "math-timeline"
    root.mkdir()
    speech, captions, _, _ = story_video.timeline({"scenes": [scene]}, root)
    assert speech[0]["scene"] == "concept.png"
    assert speech[-1]["scene"] == "formula.png"
    assert sum(s["frames"] for s in speech) == sum(c["frames"] for c in captions)
    assert sum(s["frames"] for s in speech if s.get("concept_leadin")) == (
        0 if picture_first else 36000
    )
    actual = b""
    for segment in speech:
        if segment.get("silence"):
            continue
        with wave.open(str(config.safe_path(segment["audio"])), "rb") as wav:
            actual += wav.readframes(wav.getnframes())
    assert actual == raw


def test_late_lora_example_keeps_concepts_and_its_spoken_numerical_cues(
    database, monkeypatch
):
    from paperspeak import db, papers

    paper = papers.register(
        {"source_id": "lora-example", "version": "v1", "title": "LoRA"}
    )
    project = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper, legacy=True)["project_id"],),
    )
    monkeypatch.setattr(story, "lora_anchor", lambda *_: "primary")
    track = project["data"]["modes"]["deep_dive"]
    scene = {
        "title": "Add the correction",
        "title_ja": "補正を足す",
        "focus": "Add the output coordinate-wise.",
        "visual": spec(),
        "utterances": [
            {
                "id": "intro",
                "speaker": "guide",
                "kind": "paper",
                "source_ids": ["primary"],
                "text": "Add the output coordinate-wise.",
            }
        ],
    }
    track["scenes"] = [scene]
    story.ensure_lora_worked_example(project, track)
    math_concepts.ensure(scene, lora_paper=True)
    story.ensure_worked_example_cues(scene)
    assert len(scene["visual"]["concepts"]) == len(scene["visual"]["equations"]) == 3
    assert [u["visual_focus"] for u in scene["utterances"]] == [0, 0, 3, 5, 5]
    assert scene["visual"]["worked_example"]["output"] == [8, 15]
    story_video.render_scene(project, "deep_dive", 0)
    assert len(scene["render_paths"]) == 6
    assert all(
        not json.loads(config.safe_path(p + ".layout.json").read_text())[
            "layout_errors"
        ]
        for p in scene["render_paths"]
    )


def test_original_math_scene_has_a_source_then_paired_concept_then_zoom(
    database, monkeypatch
):
    import pymupdf
    from paperspeak import db, papers

    paper_id = papers.register(
        {"source_id": "math-source", "version": "v1", "title": "A source"}
    )
    project = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper_id, modes=["deep_dive"], legacy=True)["project_id"],),
    )
    image = database / "source.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 500, 350), False)
    pix.clear_with(200)
    pix.save(str(image))
    db.execute(
        "INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
        (
            "original",
            paper_id,
            None,
            "original",
            db.dumps(
                {
                    "image_path": "source.png",
                    "review": {"passed": True},
                    "regions": [
                        {
                            "id": "detail",
                            "box": [0.1, 0.1, 0.5, 0.5],
                            "label_en": "A detail",
                            "label_ja": "一部分",
                        }
                    ],
                }
            ),
            0,
        ),
    )
    visual = spec()
    visual["original_asset_id"] = "original"
    scene = {
        "title": "From the paper to the idea",
        "title_ja": "原図から意味へ",
        "focus": "Two paths",
        "visual": visual,
        "utterances": [
            {"text": "See the picture.", "visual_focus": 1},
            {"text": "Now the equation.", "visual_focus": 2},
            {"text": "See the original detail.", "visual_focus_region": "detail"},
        ],
    }
    project["data"]["modes"]["deep_dive"]["scenes"] = [scene]
    story_video.render_scene(project, "deep_dive", 0)
    assert len(scene["render_paths"]) == 4
    assert scene["visual"]["zoom_start"] == 3
    assert scene["utterances"][-1]["visual_focus"] == 3
    layouts = [
        json.loads(config.safe_path(path + ".layout.json").read_text())
        for path in scene["render_paths"]
    ]
    assert [p["phase"] for p in layouts] == [
        "original",
        "intuition",
        "symbols",
        "original",
    ]
    assets = db.all(
        "SELECT kind FROM visual_assets WHERE lesson_id=?",
        (project["data"]["modes"]["deep_dive"]["lesson_id"],),
    )
    assert [a["kind"] for a in assets] == [
        "original",
        "teaching",
        "teaching",
        "original",
    ]
