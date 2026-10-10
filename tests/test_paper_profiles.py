import pytest
from paperspeak import db, paper_profile, papers, story, story_pictures
from paperspeak.runtime import GPUUnavailable, PracticePreempted


def saved_project(title="A useful dataset", kind="dataset", training="not_applicable"):
    pid = papers.register({"source_id": title, "title": title, "version": "v1"})
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        ("main-" + pid, pid, "section", db.dumps({"text": "Main contribution"})),
    )
    return {
        "id": "readonly-" + pid,
        "paper_id": pid,
        "data": {
            "paper_title": title,
            "model": "local",
            "direction_policy": "newcomer-first-1",
            "evidence": [
                {
                    "id": "C1",
                    "topic": "mechanism",
                    "claim": "The contribution is a resource and its quality checks.",
                    "source_ids": ["main-" + pid],
                }
            ],
            "research_profile": {
                "kind": kind,
                "domain": "general",
                "training": training,
                "math": "not_required",
            },
        },
    }


@pytest.mark.parametrize(
    "kind,required",
    [
        ("method", "mechanism"),
        ("theory", "proof"),
        ("analysis", "hypothesis"),
        ("benchmark", "protocol"),
        ("dataset", "annotat"),
        ("systems", "runtime"),
        ("survey", "taxonomy"),
        ("generic", "mechanism"),
    ],
)
def test_narrative_structure_matches_the_contribution(database, kind, required):
    p = saved_project(kind=kind)
    deep = " ".join(story.story_beats(p, "deep_dive")).lower()
    assert required in deep
    assert len(story.story_beats(p, "overview")) == 6
    assert len(story.story_beats(p, "deep_dive")) == 10
    if kind != "benchmark":
        assert "first test answer" not in deep and "learning/exposure" not in deep
    plan = story._fallback_plan(p, "deep_dive")
    assert all(s["visual_type"] != "equation" for s in plan["scenes"])
    assert not any(
        "factor" in s["title"].lower() or "rank" in s["title"].lower()
        for s in plan["scenes"]
    )


def test_training_free_method_does_not_require_a_new_training_pipeline(database):
    p = saved_project("A denoiser cache for fast inference", "systems", "training_free")
    assert "Do not invent new training" in " ".join(story.story_beats(p, "deep_dive"))
    assert "does not learn" in paper_profile.brief(
        p
    ) or "do not invent" in paper_profile.brief(p)


@pytest.mark.parametrize(
    "title,canonical",
    [
        ("LoRA: Low-Rank Adaptation of Large Language Models", True),
        ("LoRA", True),
        ("Exploration for Robot Planning", False),
        ("Beyond LoRA: Weight Decomposition", False),
        ("A Comparison of LoRA and Other Adapters", False),
        ("LoRA: A Different Scaling Law", False),
    ],
)
def test_lora_variant_does_not_receive_original_scaling_or_rank_conventions(
    title, canonical
):
    assert story.is_lora({"data": {"paper_title": title}}) is canonical


def test_profile_reads_main_evidence_without_borrowing_reference_results(database):
    p = saved_project()
    p["data"].pop("research_profile")
    other = papers.register(
        {"source_id": "related", "title": "LoRA reference", "version": "v1"}
    )
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        ("related-source", other, "section", db.dumps({"text": "Other work"})),
    )
    p["data"]["evidence"].extend(
        [
            {
                "id": "C2",
                "topic": "history",
                "claim": "DO_NOT_IMPORT_LORA",
                "source_ids": ["related-source"],
            },
            {
                "id": "C3",
                "topic": "equation",
                "claim": "DO_NOT_IMPORT_REFERENCE_EQUATION",
                "source_ids": ["related-source"],
            },
        ]
    )

    class Local:
        def ask(self, prompt, **kwargs):
            assert "DO_NOT_IMPORT" not in prompt
            assert "resource and its quality checks" in prompt
            return {
                "kind": "dataset",
                "domain": "vision",
                "training": "not_applicable",
                "math": "not_required",
                "reason": "The main resource is explicit.",
                "claim_ids": ["C1"],
            }

    assert paper_profile.classify(p, Local())["kind"] == "dataset"

    class Bad:
        def ask(self, *args, **kwargs):
            return {
                "kind": "method",
                "domain": "general",
                "training": "trained",
                "math": "central",
                "reason": "Wrong reference",
                "claim_ids": ["C3"],
            }

    with pytest.raises(ValueError, match="main-paper"):
        paper_profile.classify(p, Bad())


def test_frozen_backbone_is_not_confused_with_no_parameter_training(database):
    p = saved_project("Low-rank adapters", kind="method")
    p["data"]["evidence"][0]["claim"] = (
        "The backbone is frozen and only A and B receive gradient updates during adaptation."
    )

    class Local:
        def ask(self, prompt, **kwargs):
            assert "ANY parameters" in prompt
            return {
                "kind": "method",
                "domain": "language",
                "requires_parameter_training": False,
                "math": "central",
                "reason": "The backbone is frozen.",
                "claim_ids": ["C1"],
            }

    with pytest.raises(ValueError, match="frozen backbone"):
        paper_profile.classify(p, Local())

    class Fixed:
        def ask(self, *args, **kwargs):
            return {
                "kind": "method",
                "domain": "language",
                "requires_parameter_training": True,
                "math": "central",
                "reason": "A and B are updated.",
                "claim_ids": ["C1"],
            }

    assert paper_profile.classify(p, Fixed())["training"] == "trained"


@pytest.mark.parametrize("exception", [GPUUnavailable, PracticePreempted])
def test_profile_gpu_wait_and_recording_do_not_consume_attempts(database, exception):
    p = saved_project()
    p["data"].pop("research_profile")

    class Wait:
        def ask(self, *args, **kwargs):
            raise exception("Wait")

    with pytest.raises(exception):
        paper_profile.prepare(p, Wait())
    assert p["data"]["profile_attempts"]["count"] == 0


def test_bad_classification_uses_finite_generic_fallback(database):
    p = saved_project()
    p["data"].pop("research_profile")

    class Bad:
        def ask(self, *args, **kwargs):
            return {"kind": "robot-recipe"}

    for _ in range(3):
        assert not paper_profile.prepare(p, Bad())
    assert paper_profile.prepare(p, Bad())
    assert p["data"]["research_profile"]["kind"] == "generic"
    assert all(
        s["visual_type"] != "equation"
        for s in story._fallback_plan(p, "deep_dive")["scenes"]
    )


@pytest.mark.parametrize(
    "title,focus",
    [
        ("An image diffusion model", "Noise distributions in denoising"),
        (
            "A theory of local attention",
            "A stable language with controlled comparisons",
        ),
        ("A retrieval encoder", "Efficient model scaling and data coverage"),
        ("A speech model", "Acoustic distributions and semantic stability"),
        ("A stable motion primitive", "A robot converges to a target pose"),
    ],
)
def test_ambiguous_words_do_not_select_unrelated_rl_or_gradient_drawings(title, focus):
    p = {"data": {"paper_title": title, "evidence": []}}
    scene = {"title": focus, "focus": focus}
    spec = story_pictures.teaching_spec(scene, p)
    assert spec.get("template") not in {
        "noise_trajectory",
        "distribution_return",
        "norm_bounds",
        "update_schedule",
        "replay_memory",
    }


def test_checked_concrete_example_is_preserved_instead_of_generic_keyword_fallback():
    nodes = [
        {
            "en": "Input",
            "ja": "入力",
            "detail_en": "A new image",
            "detail_ja": "新しい画像",
            "icon": "file",
        },
        {
            "en": "Decision",
            "ja": "判定",
            "detail_en": "Class A or class B",
            "detail_ja": "AかBか",
            "icon": "rule",
        },
    ]
    s = {
        "title": "Noise distribution",
        "focus": "Classify an image",
        "learning": {
            "example_steps": nodes,
            "question_en": "Which class?",
            "question_ja": "どのクラス？",
        },
    }
    spec = story_pictures.teaching_spec(
        s, {"data": {"paper_title": "Image model", "evidence": []}}
    )
    assert spec["template"] == "worked_steps" and spec["nodes"] == nodes


def test_audit_job_is_checkpointed_and_does_not_edit_existing_project(database):
    p = saved_project()
    db.execute(
        "INSERT INTO video_projects VALUES (?,?,?,?,?,?,?)",
        (p["id"], p["paper_id"], "fixture-profile", "ready", db.dumps(p["data"]), 1, 1),
    )
    before = db.one("SELECT * FROM video_projects WHERE id=?", (p["id"],))
    ident = db.enqueue("paper_profile_audit", "profile-test")
    db.patch_job(ident, checkpoint={"projects": [p["id"]]})

    class Local:
        def ask(self, *args, **kwargs):
            return {
                "kind": "dataset",
                "domain": "vision",
                "training": "not_applicable",
                "math": "not_required",
                "reason": "Main resource",
                "claim_ids": ["C1"],
            }

    assert paper_profile.audit_step(
        db.one("SELECT * FROM jobs WHERE id=?", (ident,)), Local()
    )
    assert db.one("SELECT * FROM video_projects WHERE id=?", (p["id"],)) == before
    assert (
        db.one("SELECT checkpoint FROM jobs WHERE id=?", (ident,))["checkpoint"][
            "index"
        ]
        == 1
    )
