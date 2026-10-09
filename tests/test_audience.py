import copy
import json

import pytest
from paperspeak import audience, db, papers, story, story_pictures
from paperspeak.runtime import GPUUnavailable, PracticePreempted


def panel(material, gap=False):
    line = material[0]
    return {
        "personas": [
            {
                "id": p["id"],
                "retell_en": "The robot needs to put the banana on the plate.",
                "understood": [
                    {
                        "point_ja": "バナナを皿に載せる目標",
                        "utterance_id": line["id"],
                        "quote": line["text"][:35],
                    }
                ],
                "gaps": [
                    {
                        "utterance_id": line["id"],
                        "quote": line["text"][:35],
                        "question_ja": "ふたが閉じると何が変わる？",
                        "add_ja": "開いた皿と閉じたふたを同じ図で対比する。",
                        "priority": "important",
                    }
                ]
                if gap
                else [],
                "keep_watching": "maybe",
                "reason_ja": "次にふたを開ける手順を知りたい。",
                "next_question_ja": "何を先に実行する？",
                "scores": {"clarity": 3 if gap else 4, "engagement": 3, "humor": 2},
            }
            for p in audience.PERSONAS
        ]
    }


def scene():
    return {
        "title": "A changed task",
        "focus": "How does the robot recover?",
        "claim_ids": [],
        "learning": {
            "question_en": "What changes?",
            "example_en": "HIDDEN_AUTHOR_ANSWER",
        },
        "visual": {"nodes": []},
        "utterances": [
            {
                "id": "u1",
                "speaker": "guide",
                "text": "The robot needs to put the banana on the plate.",
                "source_ids": [],
            },
            {
                "id": "u2",
                "speaker": "host",
                "text": "But what if someone closes the lid?",
                "source_ids": [],
            },
        ],
    }


def test_opening_is_blind_to_later_words_and_unused_visual_steps():
    s = scene()
    s["utterances"][0]["text"] = " ".join(["banana"] * 65)
    s["utterances"][1]["text"] = "UNHEARD_FUTURE_ANSWER"
    s["storyboard"] = {
        "beats": [{"shot": "case", "focus": 0, "notice": "HIDDEN_NOTICE_ANSWER"}],
        "shots": [
            {
                "id": "case",
                "visual": {
                    "nodes": [
                        {"en": "Banana", "ja": "バナナ"},
                        {"en": "FUTURE_VISUAL", "ja": "未来"},
                    ]
                },
            }
        ],
    }
    material = audience.draft_material(s, opening=True)
    text = json.dumps(material)
    assert "UNHEARD_FUTURE_ANSWER" not in text
    assert "HIDDEN_AUTHOR_ANSWER" not in text
    assert "HIDDEN_NOTICE_ANSWER" not in text
    assert "FUTURE_VISUAL" not in text
    assert len(material) == 1


def test_assessment_cannot_cite_unheard_dialogue_or_fake_success():
    material = audience.draft_material(scene())
    result = panel(material)
    assert audience.validate(result, material)
    bad = copy.deepcopy(result)
    bad["personas"][0]["understood"][0]["quote"] = "A rule explained only later"
    with pytest.raises(ValueError, match="verbatim"):
        audience.validate(bad, material)


def test_quote_typography_is_repaired_using_only_actual_checkpoint_words():
    assert (
        audience.quote_span("A robot can 'think' about a task.", "think about a task")
        == "'think' about a task"
    )
    assert audience.quote_span("The value is x > 2.", "x >= 2") is None
    assert (
        audience.quote_span(
            "First it learns a rule, then it learns a motion.",
            "learns a rule...learns a motion",
        )
        == "learns a rule, then it learns a motion"
    )
    assert (
        audience.quote_span(
            "First it learns a rule.", "learns a rule...makes up a motion"
        )
        is None
    )
    material = audience.draft_material(scene())
    result = panel(material)
    result["personas"][0]["understood"][0]["utterance_id"] = "wrong-ID"
    repaired = audience.validate(result, material)["personas"][0]["understood"][0]
    assert repaired["utterance_id"] == "u1" and repaired["reference_repaired"]
    bad = copy.deepcopy(result)
    bad["personas"][0]["scores"]["clarity"] = 2
    with pytest.raises(ValueError, match="concrete understanding gap"):
        audience.validate(bad, material)


def test_audience_and_scientific_editor_are_separate_and_checkpointed(
    database, monkeypatch
):
    pid = papers.register(
        {"source_id": "audience-test", "version": "v1", "title": "A robot idea"}
    )
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(pid, modes=["overview"])["project_id"],),
    )
    s = scene()
    p["data"]["modes"]["overview"]["scenes"] = [s]
    monkeypatch.setattr(
        story,
        "context_for",
        lambda *args: {"sources": [{"text": "CHECKED_SOURCE_FOR_EDITOR_ONLY"}]},
    )

    class Local:
        calls = 0

        def ask(self, prompt, **kwargs):
            self.calls += 1
            if "heard_and_seen" in prompt:
                assert "CHECKED_SOURCE_FOR_EDITOR_ONLY" not in prompt
                assert "HIDDEN_AUTHOR_ANSWER" not in prompt
                value = json.loads(prompt.rsplit("\n", 1)[-1])
                return panel(value["heard_and_seen"], gap=True)
            assert "CHECKED_SOURCE_FOR_EDITOR_ONLY" in prompt
            assert "ふたが閉じると何が変わる" in prompt
            return {
                "learner_explanation_en": "The goal is clear but recovery needs explanation.",
                "unexplained_terms": [],
                "issues": [],
                "visual_issues": [],
                "notes": "Confirmed",
            }

    local = Local()
    assert not story._review_scene(p, local, "overview", s, 0, "novice")
    story.save(p)
    p = db.one("SELECT * FROM video_projects WHERE id=?", (p["id"],))
    s = p["data"]["modes"]["overview"]["scenes"][0]
    assert not story._review_scene(p, local, "overview", s, 0, "novice")
    assert not story._review_scene(p, local, "overview", s, 0, "novice")
    assert s["reviews"]["novice"]["passed"]
    assert local.calls == 3
    assert story._review_scene(p, local, "overview", s, 0, "novice")
    assert local.calls == 3


@pytest.mark.parametrize("exception", [PracticePreempted, GPUUnavailable])
def test_recording_and_gpu_wait_do_not_consume_audience_attempts(exception):
    s = scene()
    p = {"data": {"model": "local", "modes": {"overview": {"scenes": [s]}}}}

    class Interrupted:
        def ask(self, *args, **kwargs):
            raise exception("Wait")

    with pytest.raises(exception):
        audience.draft_step(p, Interrupted(), "overview", s, 0)
    assert s["audience_rehearsal"]["errors"] == 0


def test_three_bad_audience_responses_finish_without_claiming_success(database):
    s = scene()
    p = {"data": {"model": "local", "modes": {"overview": {"scenes": [s]}}}}

    class Bad:
        def ask(self, *args, **kwargs):
            return {"personas": []}

    for _ in range(3):
        assert not audience.draft_step(p, Bad(), "overview", s, 0)
    assert audience.draft_step(p, Bad(), "overview", s, 0)
    assert "result" not in s["audience_rehearsal"]


def test_finished_opening_never_includes_caption_after_actual_cutoff(
    monkeypatch, tmp_path
):
    from paperspeak import config, story_video

    monkeypatch.setattr(config, "DATA", tmp_path)
    captions = [
        {
            "english": "The goal is a banana on a plate.",
            "japanese": "バナナを皿に載せる。",
            "speaker": "guide",
            "frames": 25 * 24000,
        },
        {
            "english": "UNHEARD_ANSWER_AFTER_30_SECONDS",
            "speaker": "guide",
            "frames": 10 * 24000,
        },
    ]
    monkeypatch.setattr(
        story_video, "timeline", lambda *args: ([], captions, [(0, "The goal")], [])
    )
    cp = audience.finished_material(
        {"id": "x", "data": {"manifest": {}}}, {"scenes": [{"frames": []}]}
    )
    assert cp[0]["end"] == 30
    assert len(cp[0]["material"]) == 1
    assert "UNHEARD_ANSWER" not in json.dumps(cp[0])
    assert len(cp[1]["material"]) == 2


def test_token_window_has_visible_tokens_and_not_a_decorative_document():
    spec = {
        "template": "worked_steps",
        "nodes": [
            {
                "en": "Read banana",
                "ja": "bananaを読む",
                "detail_en": "Only the last two positions are visible.",
                "detail_ja": "最後の2つだけが見える。",
                "icon": "token",
                "relation": "window",
                "objects": [
                    {"en": c, "ja": str(i + 1), "icon": "token", "selected": i >= 4}
                    for i, c in enumerate("banana")
                ],
            },
            {
                "en": "Compare",
                "ja": "比較",
                "detail_en": "A restricted model needs a specified output position.",
                "detail_ja": "出力する位置も指定する。",
                "icon": "rule",
            },
        ],
    }
    assert story_pictures.validate(spec) is None
    spec["nodes"][0]["objects"][0]["selected"] = "yes"
    with pytest.raises(ValueError, match="boolean"):
        story_pictures.validate(spec)
