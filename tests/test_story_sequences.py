import pytest
from paperspeak import (
    config,
    db,
    papers,
    story,
    story_direction,
    story_shots,
    story_video,
    storyboards,
)


def steps():
    return [
        {
            "en": "Copy from A to B",
            "ja": "AからBへコピー",
            "detail_en": "A → B",
            "detail_ja": "元はA、コピー先はB",
            "icon": "file",
            "objects": [
                {"en": "Source: image.png", "ja": "元の画像", "icon": "file"},
                {"en": "Destination: backup", "ja": "コピー先", "icon": "folder"},
            ],
            "relation": "flow",
        },
        {
            "en": "Destination comes first",
            "ja": "コピー先を先に書く",
            "detail_en": "copy_file(B, A)",
            "detail_ja": "先にB、次にA",
            "icon": "rule",
            "objects": [
                {"en": "B: destination", "ja": "先にコピー先", "icon": "folder"},
                {"en": "A: source", "ja": "次に元の画像", "icon": "file"},
            ],
            "relation": "order",
        },
        {
            "en": "A later test",
            "ja": "後から出す問題",
            "detail_en": "Which argument comes first now?",
            "detail_ja": "次のコピーでも、先に書くのは？",
            "icon": "chat",
        },
    ]


def project():
    paper = papers.register(
        {
            "source_id": "sequence-test",
            "version": "v1",
            "title": "A rule-following benchmark",
        }
    )
    return db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper, modes=["overview"])["project_id"],),
    )


def board():
    return {
        "question": "Can the rule survive a distraction?",
        "takeaway": "Look at the first response",
        "humor": "Aiden blames the unusual argument order.",
        "shots": [
            {
                "id": "case",
                "visual": {
                    "type": "example",
                    "template": "worked_steps",
                    "nodes": steps(),
                    "caption_en": "Apply the new rule",
                    "caption_ja": "新しいルールを使う",
                },
            },
            {
                "id": "result",
                "visual": {
                    "type": "comparison",
                    "nodes": [
                        {"en": "Expected: B first", "ja": "正解：Bを先に"},
                        {
                            "en": "Illustrative mistake: A first",
                            "ja": "仮の間違い：Aを先に",
                        },
                    ],
                    "caption_en": "A possible mistake, not a measured model result",
                    "caption_ja": "実測結果ではなく、間違いの例",
                },
            },
        ],
        "beats": [
            {
                "shot": "case",
                "focus": 0,
                "notice": "A is the input and B the destination",
            },
            {"shot": "case", "focus": 1, "notice": "B is written before A"},
            {"shot": "case", "focus": 2, "notice": "The rule is used again"},
            {"shot": "result", "focus": 0, "notice": "Compare the two argument orders"},
        ],
    }


def test_prerequisites_are_explained_before_use():
    first = {"title": "A task", "title_ja": "問題", "focus": "A concrete copy rule"}
    story_direction.ensure_fallback_contract(first)
    first["learning"]["introduces"] = [
        {
            "term": "destination",
            "definition_en": "Where the copied file ends up",
            "definition_ja": "ファイルのコピー先",
        }
    ]
    assert story_direction.validate_learning(first)
    second = {"title": "The rule", "title_ja": "ルール", "focus": "Apply the rule"}
    story_direction.ensure_fallback_contract(second)
    second["learning"]["needs"] = ["destination"]
    with pytest.raises(ValueError, match="prerequisite"):
        story_direction.validate_learning(second)
    assert story_direction.validate_learning(second, ["destination"])


def test_every_planned_picture_is_used_in_dialogue(database):
    p = project()
    b = storyboards.validate(board(), p, "overview")
    turns = [{"text": "Copy A to B.", "visual_beat": 0}]
    with pytest.raises(ValueError, match="every planned picture"):
        story_shots.bind(b, turns, require=True)
    turns.append({"text": "Now compare the two answers.", "visual_beat": 3})
    with pytest.raises(ValueError, match="every concrete step"):
        story_shots.bind(b, turns, require=True)
    turns[1:1] = [
        {"text": "The destination is written first.", "visual_beat": 1},
        {"text": "Try a new copy after unrelated questions.", "visual_beat": 2},
    ]
    story_shots.bind(b, turns, require=True)


def test_scene_really_changes_between_example_and_comparison_offline(database):
    import pymupdf

    p = project()
    b = storyboards.validate(board(), p, "overview")
    s = {
        "title": "Will the rule survive?",
        "title_ja": "ルールは残る？",
        "focus": "Copy destination first",
        "storyboard": b,
        "visual": b["visual"],
        "utterances": [
            {"text": beat["notice"], "visual_beat": i}
            for i, beat in enumerate(b["beats"])
        ],
    }
    story_shots.bind(b, s["utterances"], require=True)
    p["data"]["modes"]["overview"]["scenes"] = [s]
    story_video.render_scene(p, "overview", 0)
    assert len(s["render_paths"]) == 5  # three concrete steps, two comparison focuses
    assert [u["visual_focus"] for u in s["utterances"]] == [0, 1, 2, 3]
    assert s["render_steps"][0]["shot"] == "case"
    assert s["render_steps"][3]["shot"] == "result"
    assert len({a["asset_id"] for a in s["focus_assets"]}) == 5
    for path in s["render_paths"]:
        pix = pymupdf.Pixmap(str(config.safe_path(path)))
        assert (pix.width, pix.height) == (1920, 1080)
    u = {
        "visual_cues": [{"phrase": "compare the answers", "beat": 3}],
        "audio_check": {
            "timestamps": [
                {"text": "compare", "start": 0.5},
                {"text": "the", "start": 0.7},
                {"text": "answers", "start": 0.9},
            ]
        },
    }
    assert storyboards.timed_cues(s, u)[0]["focus"] == 3


def test_preview_audio_is_prepared_before_the_unwritten_rest(database, monkeypatch):
    p = project()
    intro = {
        "title": "The question",
        "title_ja": "疑問",
        "visual_ready": True,
        "utterances": [{"text": "Can it follow the new rule?"}],
    }
    later = {"title": "The mechanism", "title_ja": "仕組み"}
    t = p["data"]["modes"]["overview"]
    t.update(scenes=[intro, later], phase="script")

    def tts(facade, runtime, mode):
        track = facade["data"]["modes"][mode]
        assert len(track["scenes"]) == 1
        track["scenes"][0]["utterances"][0]["audio"] = "audio/intro.wav"
        track["phase"] = "align"

    def align(facade, runtime, mode):
        track = facade["data"]["modes"][mode]
        assert len(track["scenes"]) == 1
        track["preview_id"] = "actual-preview-job"

    monkeypatch.setattr(story, "_tts_step", tts)
    monkeypatch.setattr(story, "_align_step", align)
    story._script_step(p, None, "overview")
    story._script_step(p, None, "overview")
    assert t["intro_prepared"] and t["preview_id"] == "actual-preview-job"
    assert t["phase"] == "script" and "utterances" not in later


def test_empirical_deep_dive_does_not_invent_matrix_factorization(database):
    p = project()
    p["data"]["research_profile"] = {"kind": "benchmark", "training": "not_applicable", "math": "not_required"}
    beats = story.story_beats(p, "deep_dive")
    assert any("behavioral test protocol" in b for b in beats)
    assert not any("rank hypothesis" in b for b in beats)


def test_official_anthology_is_usable_for_publication_identity():
    from paperspeak import awards

    assert awards.trusted("https://aclanthology.org/2026.acl-long.1301/")
    assert not awards.trusted(
        "https://aclanthology.org.attacker.test/2026.acl-long.1301/"
    )


def test_flattened_example_steps_are_normalized_without_losing_content():
    scene = {
        "learning": {
            "question_en": "Which comes first?",
            "question_ja": "どちらが先？",
            "example_en": "A to B, destination first",
            "example_ja": "AからB、先にコピー先",
            "takeaway_en": "Use B before A",
            "takeaway_ja": "Bを先に書く",
            "needs": [],
            "introduces": [],
        },
        "learning_example_steps": steps(),
    }
    story_direction.validate_learning(scene)
    assert scene["learning"]["example_steps"] == steps()


def test_checked_cached_plan_is_reused_after_schema_alias_repair(database):
    p = project()
    p["data"]["repairs"]["plan:overview"] = {
        "attempts": 1,
        "candidate": {"answer": "source-grounded plan"},
    }
    result = story.bounded(
        p, None, "plan:overview", "Plan", lambda r: r, lambda _: None
    )
    assert result["answer"] == "source-grounded plan"


def test_opening_shows_complete_example_before_the_paper_page(database):
    p = project()
    b = board()
    b["shots"].reverse()
    b["beats"] = [b["beats"][3], b["beats"][2]]
    storyboards.validate(b, p, "overview", {"opening_scene": True})
    assert b["shots"][0]["id"] == "case"
    assert [(b["shot"], b["focus"]) for b in b["beats"]] == [
        ("case", 0),
        ("case", 1),
        ("case", 2),
        ("result", 0),
    ]


def test_script_context_omits_accumulating_review_logs(database):
    p = project()
    b = board()
    scene = {
        "title": "Will the rule survive?",
        "title_ja": "ルールは残る？",
        "focus": "Apply the copy rule",
        "claim_ids": [],
        "storyboard": b,
        "reviews": {"content": {"history": [{"notes": "UNRELATED_REVIEW_LOG" * 2000}]}},
        "storyboard_preview": ["visuals/unneeded-preview-path.png"] * 1000,
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]
    prompt = story._script_prompt(p, "overview", scene, 0)
    assert "UNRELATED_REVIEW_LOG" not in prompt
    assert "unneeded-preview-path" not in prompt
    assert "copy_file(B, A)" in prompt
    assert len(prompt) < 16000


def test_fallback_preserves_the_checked_concrete_example(database):
    p = project()
    b = storyboards.validate(board(), p, "overview")
    scene = {"focus": "Apply the copy rule", "storyboard": b}
    draft = story._fallback_script(p, "overview", scene, None)
    assert "B is written before A" in " ".join(u["text"] for u in draft["utterances"])
    story_shots.bind(b, draft["utterances"], require=True)


def test_editor_can_remove_repetition_without_losing_a_concrete_step(database):
    p = project()
    b = storyboards.validate(board(), p, "overview")
    turns = [
        {
            "id": str(i),
            "speaker": "guide" if i else "host",
            "text": beat["notice"],
            "kind": "example",
            "source_ids": [],
            "visual_beat": i,
        }
        for i, beat in enumerate(b["beats"])
    ]
    turns.insert(
        1,
        {
            "id": "duplicate",
            "speaker": "guide",
            "text": "A is the input and B the destination, again.",
            "kind": "example",
            "source_ids": [],
            "visual_beat": 0,
            "audio": "audio/old-duplicate.wav",
        },
    )
    scene = {
        "title": "A concrete task",
        "focus": "The copy rule",
        "claim_ids": [],
        "visual": b["visual"],
        "storyboard": b,
        "utterances": turns,
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]

    class Editor:
        def ask(self, prompt, **kwargs):
            return {
                "issues": [
                    {
                        "utterance_id": "duplicate",
                        "action": "remove",
                        "reason": "Repeats the source and destination without adding a distinction",
                    }
                ],
                "notes": "Keep the first explanation",
            }

    story._review_scene(p, Editor(), "overview", scene, 0, "editorial")
    assert "duplicate" not in {u["id"] for u in scene["utterances"]}
    assert scene["omissions"][0]["utterance"]["audio"] == "audio/old-duplicate.wav"
    story_shots.bind(b, scene["utterances"], require=True)


def test_final_content_check_rechecks_editor_changes_against_sources(database):
    p = project()
    scene = {
        "title": "A task",
        "focus": "The rule",
        "claim_ids": [],
        "visual": {"type": "example"},
        "utterances": [
            {
                "id": "line",
                "speaker": "guide",
                "text": "The authors measured behavior.",
                "source_ids": [],
            }
        ],
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]

    class Reader:
        def ask(self, prompt, **kwargs):
            assert "Check scientific claims against SOURCE TEXT" in prompt
            return {"issues": [], "notes": "Checked the edited wording"}

    story._review_scene(p, Reader(), "overview", scene, 0, "content_final")
    assert scene["reviews"]["content_final"]["passed"]


def test_structural_editor_rewrites_order_and_keeps_visual_binding(database):
    p = project()
    b = storyboards.validate(board(), p, "overview")
    turns = [
        {
            "id": str(i),
            "speaker": "host" if i == 0 else "guide",
            "text": beat["notice"],
            "kind": "example",
            "source_ids": [],
            "visual_beat": i,
        }
        for i, beat in enumerate(b["beats"])
    ]
    scene = {
        "title": "The task",
        "focus": "Apply the rule",
        "claim_ids": [],
        "storyboard": b,
        "utterances": turns,
        "reviews": {"novice": {"notes": "Repetition needs more than proofreading"}},
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]

    class Editor:
        def ask(self, prompt, **kwargs):
            assert "SELECTING which paragraphs to keep" in prompt
            assert "DRAFT:" in prompt
            assert not kwargs.get("images")  # checked pictures are already described
            assert kwargs.get("thinking") is True
            return {
                "keep": ["U1", "U2", "U3", "U4"],
                "summary": "A concrete prediction and answer",
            }

    story._sequence_edit_step(p, Editor(), "overview", scene, 0)
    assert scene["structural_edit_done"]
    assert "reviews" not in scene
    assert scene["editing_history"][0]["reviews"]["novice"]["notes"]
    assert scene["utterances"][0]["id"] != "0"
    story_shots.bind(b, scene["utterances"], require=True)


def test_six_step_example_is_accepted_as_a_complete_visual_sequence(database):
    p = project()
    b = board()
    b["shots"][0]["visual"]["nodes"] = steps() + steps()
    storyboards.validate(b, p, "overview")
    assert [beat["focus"] for beat in b["beats"] if beat["shot"] == "case"] == list(
        range(6)
    )


def test_unchanged_original_is_not_three_new_visual_beats(database):
    import time

    import pymupdf

    p = project()
    path = config.DATA / "visuals" / "original.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 20, 20))
    pix.clear_with(255)
    pix.save(str(path))
    db.execute(
        "INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
        (
            "paper-original",
            p["paper_id"],
            None,
            "original",
            db.dumps(
                {
                    "image_path": "visuals/original.png",
                    "label": "Figure 1",
                    "review": {"passed": True},
                }
            ),
            time.time(),
        ),
    )
    b = board()
    b["shots"][1]["visual"].update(
        type="original",
        original_asset_id="paper-original",
        nodes=[
            {"en": "Learning", "ja": "学習"},
            {"en": "Distraction", "ja": "別の話題"},
            {"en": "Test", "ja": "テスト"},
        ],
    )
    b["beats"] += [
        {"shot": "result", "focus": i, "notice": "The same whole figure"}
        for i in (1, 2)
    ]
    storyboards.validate(b, p, "overview")
    assert len([beat for beat in b["beats"] if beat["shot"] == "result"]) == 1


def test_second_renderer_failure_uses_the_replacement_not_stale_shots(
    database, monkeypatch
):
    p = project()
    b = storyboards.validate(board(), p, "overview")
    scene = {
        "title": "A concrete task",
        "title_ja": "具体的な課題",
        "focus": "Apply the rule",
        "claim_ids": [],
        "visual": b["visual"],
        "storyboard": b,
        "structural_edit_done": True,
        "renderer_dialogue_repair": True,
        "scope_review": {"complete": True},
        "utterances": [
            {
                "id": str(i),
                "speaker": "host" if i == 0 else "guide",
                "text": beat["notice"],
                "kind": "example",
                "source_ids": [],
                "visual_beat": i,
            }
            for i, beat in enumerate(b["beats"])
        ],
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]
    monkeypatch.setattr(story, "_review_scene", lambda *args: True)
    attempts = []

    def render(project, mode, index):
        s = project["data"]["modes"][mode]["scenes"][index]
        attempts.append(bool(s.get("storyboard", {}).get("shots")))
        if attempts[-1]:
            raise ValueError("The sequence picture still has an overflowing label")
        s.update(
            render_paths=["visuals/safe.png"], focus_assets=[], asset_id="safe-picture"
        )

    monkeypatch.setattr(story_video, "render_scene", render)
    story._script_step(p, None, "overview")
    assert attempts == [True, False]
    assert scene["visual_ready"]


def test_newcomer_only_gets_spoken_explanation_not_source_answers(
    database, monkeypatch
):
    p = project()
    p["data"].pop("audience_policy", None)  # legacy single-listener rehearsal
    scene = {
        "title": "Copy a picture",
        "focus": "Which comes first?",
        "claim_ids": [],
        "visual": {"type": "example"},
        "learning": {"question_en": "What comes first?", "example_en": "UNSEEN_ANSWER"},
        "utterances": [
            {
                "id": "one",
                "speaker": "guide",
                "text": "The backup folder comes first in this command.",
                "source_ids": [],
            }
        ],
    }
    p["data"]["modes"]["overview"]["scenes"] = [scene]
    monkeypatch.setattr(
        story,
        "context_for",
        lambda *args: {"sources": [{"text": "UNSEEN_SOURCE_ANSWER"}]},
    )

    class Learner:
        def ask(self, prompt, **kwargs):
            assert "UNSEEN_SOURCE_ANSWER" not in prompt
            assert "UNSEEN_ANSWER" not in prompt
            assert "The backup folder comes first" in prompt
            return {
                "issues": [],
                "learner_explanation_en": "The folder where the copy goes is written first.",
                "unexplained_terms": [],
                "notes": "The speaker gives the answer",
            }

    story._review_scene(p, Learner(), "overview", scene, 0, "novice")
    assert scene["reviews"]["novice"]["passed"]


def test_outline_review_keeps_prepared_preview_and_unwritten_learning_contract():
    prepared = {
        "title": "The task",
        "focus": "Apply a rule",
        "utterances": [
            {
                "id": "ready",
                "text": "The folder comes first.",
                "audio": "audio/reusable.wav",
            }
        ],
        "visual_ready": True,
        "learning": {"example_steps": steps()},
    }
    unwritten = {
        "title": "Later",
        "focus": "What scores mean",
        "learning": {"example_steps": steps(), "needs": []},
    }
    track = {
        "scenes": [prepared, unwritten],
        "preview_id": "already-rendered",
        "intro_prepared": True,
    }
    review = {
        "scenes": [
            {"title": "A new hook", "focus": "Try another hook"},
            {"title": "Read the scores", "focus": "Only the tested setting"},
        ]
    }
    story._install_reviewed_outline(track, review)
    assert track["scenes"][0] is prepared
    assert track["scenes"][0]["utterances"][0]["audio"] == "audio/reusable.wav"
    assert track["scenes"][1]["learning"]["example_steps"] == steps()
    assert track["scenes"][1]["title"] == "Read the scores"
    assert track["preview_id"] == "already-rendered"


def test_long_bilingual_caption_fits_without_losing_the_example(database):
    p = project()
    b = storyboards.validate(board(), p, "overview")
    spec = b["shots"][0]["visual"]
    spec["caption_en"] = (
        "Hybrid attention pairs a wide-angle detective (global) with a close-up one (local). For this simple rule the wide-angle could handle both, but for trickier patterns only the pair sees the full story."
    )
    spec["caption_ja"] = (
        "ハイブリッドアテンションは広角の探偵（グローバル）とズームの探偵（ローカル）を組ませる。この単純ルールでは広角だけで対応できるが、複雑なパターンでは二人揃って初めて全体が把握できる。"
    )
    s = {
        "title": "A complete example",
        "title_ja": "具体例",
        "focus": "Explain the actual operation",
        "visual": spec,
        "utterances": [{"text": "Copy the file.", "visual_focus": 0}],
    }
    p["data"]["modes"]["overview"]["scenes"] = [s]
    story_video.render_scene(p, "overview", 0)
    assert len(s["render_paths"]) == 3
    assert all(
        config.safe_path(path).stat().st_size > 5000 for path in s["render_paths"]
    )


def test_semantic_repair_render_failure_uses_guarded_fallback(database, monkeypatch):
    p = project()
    p["data"].pop("scope_review_policy", None)
    b = storyboards.validate(board(), p, "overview")
    s = {
        "title": "The example",
        "title_ja": "具体例",
        "focus": "Explain the rule",
        "claim_ids": [],
        "visual": b["visual"],
        "storyboard": b,
        "structural_edit_done": True,
        "sequence_content_repair": True,
        "visual_repair_issues": [{"reason": "The selected figure is unrelated"}],
        "utterances": [
            {
                "id": "line",
                "speaker": "guide",
                "text": "Use the destination first.",
                "visual_beat": 0,
                "source_ids": [],
            }
        ],
    }
    p["data"]["modes"]["overview"]["scenes"] = [s]
    monkeypatch.setattr(story, "_review_scene", lambda *args: True)

    def broken(*args):
        raise RuntimeError("Visual text overflow: Worked example overlaps caption")

    monkeypatch.setattr(story_video, "render_scene", broken)
    story._script_step(p, None, "overview")
    assert s["renderer_dialogue_repair"]
    assert "utterances" not in s
    assert s["omissions"][-1]["reason"] == "visual renderer fallback"


def test_inference_audit_requires_explicit_categories_and_preserves_other_speech(
    database,
):
    p = project()
    scene = {
        "title": "The scoreboard",
        "focus": "Read the results",
        "claim_ids": [],
        "utterances": [
            {
                "id": "keep",
                "speaker": "host",
                "kind": "question",
                "source_ids": [],
                "text": "What does this score tell us?",
                "audio": "audio/keep.wav",
            },
            {
                "id": "fix",
                "speaker": "guide",
                "kind": "example",
                "source_ids": [],
                "text": "Fifty-five percent means the model is guessing.",
                "audio": "audio/old.wav",
                "aligned": True,
            },
        ],
        "subtitles_ready": True,
        "clips_ready": True,
    }

    class Audit:
        def ask(self, prompt, **kwargs):
            assert "task-specific chance baseline" in prompt
            assert kwargs["thinking"]
            cats = [
                "sample_vs_limit",
                "chance_vs_average",
                "measured_vs_expected",
                "memory_systems",
                "author_hypothesis",
                "experiment_attribution",
            ]
            return {
                "checks": [
                    {
                        "category": c,
                        "status": "unsupported"
                        if c == "chance_vs_average"
                        else "not_present",
                    }
                    for c in cats
                ],
                "issues": [
                    {
                        "utterance_id": "fix",
                        "replacement": "The average describes these test results. We cannot call it random guessing without the task-specific chance baseline.",
                        "source_ids": [],
                    }
                ],
                "notes": "Avoid an unsupported interpretation",
            }

    story_direction.scope_review_step(p, Audit(), "overview", scene, 0)
    assert scene["scope_review"]["complete"]
    assert scene["utterances"][0]["audio"] == "audio/keep.wav"
    assert "audio" not in scene["utterances"][1]
    assert scene["utterances"][1]["audio_history"][0]["audio"] == "audio/old.wav"
    assert not scene.get("subtitles_ready") and not scene.get("clips_ready")
