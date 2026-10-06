import hashlib

import pytest
from paperspeak import (
    config,
    db,
    figure_extract,
    papers,
    story,
    story_pictures,
    story_video,
    storyboards,
)


def test_unpunctuated_captions_do_not_turn_prose_references_into_figures():
    for text in (
        "Figure 1 Results Overview. Tasks across several simulators.",
        "Figure 2 Architecture. Inverted residual blocks.",
        "Fig. 7 Data Coverage. Broader experience.",
    ):
        matches = figure_extract.caption_matches(text)
        assert len(matches) == 1
        assert matches[0].group(2) in {"1", "2", "7"}
    for text in (
        "Figure 10.(b) compares different noise settings.",
        "Figure 7 shows our ablation results.",
        "As shown in Figure 1, the tasks vary.",
    ):
        assert figure_extract.caption_matches(text) == []


def test_unrelated_original_is_not_a_successful_fallback(monkeypatch):
    monkeypatch.setattr(
        story,
        "original_catalogue",
        lambda _: [
            {
                "asset_id": "noise",
                "caption": "Noise repeat and target entropy ablation",
                "label": "Figure 10",
                "regions": [],
            }
        ],
    )
    scene = {
        "title": "The replay drawer",
        "focus": "Reuse past experience in a replay buffer",
    }
    p = {"data": {"paper_title": "Off-policy reinforcement learning", "evidence": []}}
    result = storyboards.fallback(p, scene)
    assert result["visual"]["template"] == "replay_memory"
    assert "original_asset_id" not in result["visual"]


def test_original_fallback_uses_a_readable_verified_panel(monkeypatch):
    monkeypatch.setattr(
        story,
        "original_catalogue",
        lambda _: [
            {
                "asset_id": "robot",
                "caption": "Robot stair climbing",
                "label": "Figure 6",
                "regions": [
                    {"id": "caption", "label_en": "Figure caption", "area": 0.4},
                    {"id": "chart", "label_en": "Training curve", "area": 0.1},
                    {
                        "id": "robot_strip",
                        "label_en": "Robot climbing stairs",
                        "area": 0.2,
                    },
                ],
            }
        ],
    )
    result = storyboards.fallback(
        {},
        {
            "title": "Robot stair climbing",
            "title_ja": "ロボットが階段を登る",
            "focus": "Look at the real robot climbing stairs",
        },
    )
    assert result["beats"][0].get("region") is None
    assert result["beats"][1]["region"] == "robot_strip"


def test_visual_domain_is_checked_and_fallback_is_applicable():
    p = {"data": {"paper_title": "Robot reinforcement learning", "evidence": []}}
    with pytest.raises(ValueError, match="unrelated paper"):
        story_pictures.validate({"template": "surface_cells", "nodes": [1, 2, 3]}, p)
    language = {"data": {"paper_title": "A language model", "evidence": []}}
    result = story_pictures.teaching_spec(
        {"title": "Scale", "focus": "Scale a language model"}, language
    )
    assert result["template"] == "signal_path"


def test_coverage_repair_rewrites_dialogue_once_before_audio():
    def scene(index):
        return {
            "title": "Norm bounds",
            "focus": "Control gradient and feature scale",
            "visual": {"original_asset_id": "chart", "type": "original"},
            "storyboard": {
                "visual": {"original_asset_id": "chart"},
                "beats": [{"focus": 0}],
            },
            "utterances": [{"text": f"Old chart explanation {index}"}],
            "visual_ready": True,
        }

    track = {"scenes": [scene(i) for i in range(6)]}
    p = {
        "data": {
            "paper_title": "Robot reinforcement learning",
            "evidence": [],
            "modes": {"overview": track},
        }
    }
    assert story_pictures.repair_repeated_background(p, "overview")
    assert len(track["scenes"][0]["utterances"]) == 1
    assert all("utterances" not in s for s in track["scenes"][1:])
    assert all(s["visual"]["template"] == "norm_bounds" for s in track["scenes"][1:])
    assert all(s["storyboard_ready"] for s in track["scenes"][1:])
    assert not story_pictures.repair_repeated_background(p, "overview")


def test_repeated_original_with_new_verified_panels_is_a_valid_continuation():
    scenes = [
        {
            "visual": {"original_asset_id": "plot"},
            "storyboard": {"beats": [{"region": str(i)}]},
        }
        for i in range(4)
    ]
    p = {"data": {"modes": {"overview": {"scenes": scenes}}}}
    assert not story_pictures.repair_repeated_background(p, "overview")


@pytest.mark.parametrize(
    "template", [t for t in story_pictures.TEMPLATES if t != "surface_cells"]
)
def test_pictorial_templates_render_and_highlight_the_actual_picture_offline(
    database, template
):
    # Chromium rejects every external URL; all graphics and fonts are local.
    nodes = [
        {"en": "A visible operation", "ja": "見える仕組み"}
        for _ in range(story_pictures.TEMPLATES[template])
    ]
    scene = {
        "title": "Understand the operation",
        "title_ja": "仕組みを理解する",
        "focus": "An input and its operation",
        "visual": {"type": "example", "template": template, "nodes": nodes},
        "utterances": [{"text": "Notice this panel.", "visual_focus": 0}],
    }
    paper_id = papers.register(
        {"source_id": "pictorial-test", "version": "v1", "title": "An operation"}
    )
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper_id, modes=["overview"])["project_id"],),
    )
    p["data"]["modes"]["overview"]["scenes"] = [scene]
    story_video.render_scene(p, "overview", 0)
    paths = scene["render_paths"]
    assert len(paths) == len(nodes)
    data = [config.safe_path(path).read_bytes() for path in paths]
    assert all(len(b) > 15000 for b in data)
    assert len({hashlib.sha256(b).hexdigest() for b in data}) == len(nodes)
    import pymupdf

    pix = pymupdf.Pixmap(str(config.safe_path(paths[0])))
    assert (pix.width, pix.height) == (1920, 1080)


def test_claim_aliases_resolve_to_real_source_ids():
    p = {"data": {"evidence": [{"id": "C1", "source_ids": ["paper:H9", "paper:H10"]}]}}
    result = {"utterances": [{"source_ids": ["C1", "paper:H9", "missing"]}]}
    story.resolve_claim_references(result, p)
    assert result["utterances"][0]["source_ids"] == ["paper:H9", "paper:H10", "missing"]


def test_exact_equations_survive_reused_reading_notes(database):
    paper_id = papers.register(
        {"source_id": "equation-test", "version": "v1", "title": "An update rule"}
    )
    for ident, kind, text in (
        ("eq:H8", "section", "The target copy follows current weights slowly."),
        ("eq:H9", "equation", r"\bar\phi\gets\tau\phi+(1-\tau)\bar\phi (3)"),
        ("eq:H10", "equation", r"\bar\phi\gets\tau\phi+(1-\tau)\bar\phi (3)"),
    ):
        db.execute(
            "INSERT INTO sources VALUES (?,?,?,?)",
            (ident, paper_id, kind, db.dumps({"text": text})),
        )
    p = {"paper_id": paper_id, "data": {"evidence": [], "references": []}}
    story.attach_equation_sources(p)
    story.attach_equation_sources(p)
    assert len(p["data"]["evidence"]) == 1
    claim = p["data"]["evidence"][0]
    assert claim["topic"] == "equation"
    assert claim["source_ids"] == ["eq:H9", "eq:H8"]
    assert not story.plan_evidence(p, "overview")
    assert story.plan_evidence(p, "deep_dive") == [claim]


def test_planned_math_is_preserved_in_fallback(monkeypatch):
    p = {"data": {"paper_title": "Robot learning", "evidence": []}}
    monkeypatch.setattr(
        story, "context_for", lambda *_: {"claims": [{"source_ids": ["eq"]}]}
    )
    monkeypatch.setattr(
        story,
        "source_lookup",
        lambda _: {
            "eq": {
                "id": "eq",
                "kind": "equation",
                "data": {"text": r"y=r+\gamma Q(s',a') (5)"},
            },
        },
    )
    scene = {
        "title": "The target",
        "focus": "A value prediction target",
        "visual_type": "equation",
    }
    result = storyboards.fallback(p, scene)
    assert result["equation_source_id"] == "eq"
    assert result["visual"]["equations"][0]["latex"] == r"y=r+\gamma Q(s',a')"
    assert [b["focus"] for b in result["beats"]] == [0, 1]
    assert result["visual"]["concepts"]


def test_renderer_failure_rewrites_dialogue_for_its_replacement(database, monkeypatch):
    paper_id = papers.register(
        {
            "source_id": "render-repair-test",
            "version": "v1",
            "title": "Robot reinforcement learning",
        }
    )
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper_id, modes=["overview"])["project_id"],),
    )
    s = {
        "title": "Norm bounds",
        "title_ja": "ノルムの制限",
        "focus": "Bound gradient and feature scale",
        "claim_ids": [],
        "visual": {"type": "flow", "nodes": [{"en": "A bound", "ja": "制限"}]},
        "utterances": [
            {"id": "u", "text": "Look at the old chart.", "speaker": "guide"}
        ],
        "storyboard": {"visual": {}},
        "storyboard_ready": True,
    }
    p["data"]["modes"]["overview"]["scenes"] = [s]
    monkeypatch.setattr(story, "_review_scene", lambda *_: True)
    monkeypatch.setattr(
        story_video,
        "render_scene",
        lambda *_: (_ for _ in ()).throw(ValueError("Text overflow")),
    )
    story._script_step(p, None, "overview")
    assert "utterances" not in s
    assert s["storyboard"]["visual"]["template"] == "norm_bounds"
    assert s["renderer_dialogue_repair"] is True
    assert p["data"]["modes"]["overview"]["phase"] == "script"


def test_tiny_later_response_does_not_erase_a_substantive_draft(database, monkeypatch):
    paper_id = papers.register(
        {"source_id": "draft-test", "version": "v1", "title": "Replay memory"}
    )
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper_id, modes=["overview"])["project_id"],),
    )
    p["data"]["evidence"] = [{"id": "C1", "source_ids": ["source"]}]
    monkeypatch.setattr(story, "source_lookup", lambda _: {"source": {}})
    turns = [
        {
            "speaker": "host",
            "text": "Why keep experience after we have used it?",
            "kind": "question",
            "source_ids": [],
        },
        {
            "speaker": "guide",
            "text": "A replay buffer lets the learner sample earlier transitions again, so collection and learning do not need to use only the newest experience.",
            "kind": "paper",
            "source_ids": ["C1"],
        },
    ]
    answers = iter(
        [{"utterances": turns}, {"utterances": turns[1:]}, {"utterances": turns[1:]}]
    )

    class Writer:
        def ask(self, *_args, **_kwargs):
            return next(answers)

    def repair_needed(_result):
        raise ValueError("The picture needs a local repair")

    for _ in range(3):
        assert (
            story.bounded(
                p,
                Writer(),
                "script:overview:0",
                "Draft a scene",
                repair_needed,
                lambda r: r,
            )
            is None
        )
    result = story.bounded(
        p, None, "script:overview:0", "Draft a scene", repair_needed, lambda r: r
    )
    assert len(result["utterances"]) == 2
    assert result["utterances"][1]["source_ids"] == ["source"]


def test_picture_change_does_not_reuse_an_exhausted_old_script_fallback(database):
    paper_id = papers.register(
        {
            "source_id": "rewrite-budget-test",
            "version": "v1",
            "title": "Robot reinforcement learning",
        }
    )
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper_id, modes=["overview"])["project_id"],),
    )
    scene = {
        "title": "Norm bounds",
        "title_ja": "ノルムを制限",
        "focus": "Control gradient scale",
        "claim_ids": [],
        "script_revision": 1,
        "storyboard_ready": True,
    }
    scene["storyboard"] = storyboards.fallback(p, scene)
    track = p["data"]["modes"]["overview"]
    track.update(scenes=[scene], packaging={"hook": "A large network with guardrails"})
    p["data"]["repairs"]["script:overview:0"] = {
        "attempts": 3,
        "candidate": {"utterances": []},
    }

    class Writer:
        calls = 0

        def ask(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "utterances": [
                    {
                        "speaker": "host",
                        "text": "Why is there a circle around that arrow?",
                        "kind": "question",
                    },
                    {
                        "speaker": "guide",
                        "text": "In this illustration, the circle marks a limit. The shorter arrow stays inside it.",
                        "kind": "example",
                    },
                ]
            }

    writer = Writer()
    story._script_step(p, writer, "overview")
    assert writer.calls == 1
    assert len(scene["utterances"]) == 2
    assert p["data"]["repairs"]["script:overview:0"]["attempts"] == 3
    assert p["data"]["repairs"]["script:overview:0:picture-1"]["attempts"] == 0
