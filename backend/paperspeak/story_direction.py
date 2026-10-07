"""Newcomer learning contracts and scene-specific evidence scope."""

from . import config, db

VERSION = "newcomer-first-1"
BRIEF = (
    "Build understanding in spoken order for a viewer with NO specialist knowledge. "
    "Start with one visible, concrete task and let the audience predict an answer BEFORE naming the technical idea. "
    "The first spoken paragraph gives the paper/topic name while showing named inputs and a specific choice, not an abstract definition. "
    "After a short paper/topic orientation, show an input, the rule or operation, and the outcome. A result leaderboard is not an introduction to the problem. "
    "Explain a new term by what it does in THIS example, then reuse it consistently. "
    "Every scene adds one new distinction to the previous scene; do not restart the explanation with another metaphor. "
    "Aiden voices the audience's plausible prediction, uncertainty or objection. Maya explicitly corrects an incorrect prediction and points to the visible evidence. "
    "Aiden does not introduce unexplained jargon or lecture before Maya. Use dry humor tied to the actual task, with one opening callback at the end. "
    "Alternate a specific example, its explanation, a consequence/question, and the source evidence. Show the mechanism while explaining it, not only results or paper pages. "
    "Keep natural B2/C1 English. Simpler concepts do not require babyish sentences. Do not pad, rush, or target a fixed runtime. "
)
SCOPE_BRIEF = (
    "Distinguish observations, the authors' interpretation, and a teaching example. "
    "A highest score among tested models is not a universal performance ceiling. Comparisons apply to the specified dataset/tasks/models/conditions. "
    "A behavioral test operationalizes a task; it does not measure a model's conscious feelings, pain, or a biological reflex. "
    "First-attempt scoring excludes repair after feedback; it cannot reveal whether an AI consciously remembers. A correct answer meets this task's criterion, not proof of a habit or its internal mechanism. A failure does not prove that the model cannot state or recall the rule. "
    "Applying an instruction in context does not by itself establish changed model weights or persistent memory across sessions. "
    "An automatic-execution example must request the action on a new input, not ask 'What rule did I tell you?' or 'Which parameter do you remember?'. "
    "On-policy methods retain learned parameters while refreshing rollout data; they do not learn everything from scratch at every update. "
    "Training-update frequency, batch size, learning-rate step size, and real-time action/inference frequency are different quantities. "
    "Introduce a simplified/basic mathematical model as such, and explain when the paper's actual objective or implementation differs. "
    "Results from a few memory frameworks do not prove every retrieval system fails or that only an architectural change can help. Attribute proposed explanations to the authors. "
)
SCHEMA = (
    'learning:{question_en:"one question the viewer can answer",question_ja:"日本語",'
    'needs:["terms already explained earlier"],introduces:[{term:"new term",definition_en:"meaning in this case",definition_ja:"意味"}],'
    'example_en:"specific input, operation and expected output",example_ja:"具体例",'
    'takeaway_en:"what the viewer can explain afterward",takeaway_ja:"理解して言えること"}'
)


def validate_learning(scene, known=()):
    learning = scene.get("learning")
    if not isinstance(learning, dict):
        raise ValueError("Give the scene a concrete newcomer learning contract")
    # Some local-model JSON responses flatten the dotted schema name.
    # This is an explicit alias of the supplied steps, not invented content.
    if "example_steps" not in learning:
        for alias in ("learning_example_steps", "example_steps"):
            if isinstance(scene.get(alias), list):
                learning["example_steps"] = scene[alias]
                break
    for name in (
        "question_en",
        "question_ja",
        "example_en",
        "example_ja",
        "takeaway_en",
        "takeaway_ja",
    ):
        if not isinstance(learning.get(name), str) or not learning[name].strip():
            raise ValueError(
                "Each scene needs a question, specific example and takeaway in both languages"
            )
    if not isinstance(learning.get("needs", []), list) or not isinstance(
        learning.get("introduces", []), list
    ):
        raise ValueError("Track prerequisites and newly explained terms")
    explained = {t.casefold() for t in known}
    for term in learning.get("needs", []):
        if not isinstance(term, str) or term.casefold() not in explained:
            raise ValueError("Explain this prerequisite before using it: " + str(term))
    for term in learning.get("introduces", []):
        if not isinstance(term, dict) or not all(
            isinstance(term.get(k), str) and term[k].strip()
            for k in ("term", "definition_en", "definition_ja")
        ):
            raise ValueError(
                "New terms need an example-based explanation in English and Japanese"
            )
    if not learning.get("fallback"):
        from . import story_pictures

        story_pictures.validate(
            {"template": "worked_steps", "nodes": learning.get("example_steps", [])}
        )
    return scene


def earlier_terms(track, index):
    return [
        term
        for s in track.get("scenes", [])[:index]
        for term in s.get("learning", {}).get("introduces", [])
    ]


def writing_context(track, scene, index):
    return (
        "\nNEWCOMER LEARNING CONTRACT: "
        + db.dumps(scene.get("learning", {}))
        + "\nTERMS ACTUALLY INTRODUCED EARLIER: "
        + db.dumps(earlier_terms(track, index))
    )


def ensure_fallback_contract(scene):
    scene.setdefault(
        "learning",
        {
            "question_en": scene["focus"],
            "question_ja": scene["title_ja"],
            "needs": [],
            "introduces": [],
            "example_en": "Show the input and outcome in the supplied source example for "
            + scene["focus"],
            "example_ja": "この場面に対応する原論文の具体例で、入力と結果を確認する。",
            "takeaway_en": scene["focus"],
            "takeaway_ja": scene["title_ja"],
            "fallback": True,
        },
    )
    learning = scene["learning"]
    for name in ("question_en", "example_en", "takeaway_en"):
        learning.setdefault(name, scene["focus"])
    for name in ("question_ja", "example_ja", "takeaway_ja"):
        learning.setdefault(name, scene["title_ja"])
    learning.setdefault("needs", [])
    learning.setdefault("introduces", [])


def source_example_step(project, runtime):
    """Read the actual illustrated cases before proposing a teaching example."""
    from . import story

    data = project["data"]
    if data.get("source_examples_done"):
        return True
    examples = [
        a
        for a in story.original_catalogue(project)
        if any(
            t in a["caption"].casefold()
            for t in ("example", "illustrative", "protocol")
        )
    ][:3]
    index = data.get("source_example_index", 0)
    if index >= len(examples):
        data["source_examples_done"] = True
        return True
    source = examples[index]
    asset = db.one("SELECT data FROM visual_assets WHERE id=?", (source["asset_id"],))[
        "data"
    ]

    def valid(result):
        if result.get("usable") is False:
            return result
        case = result.get("example")
        if not isinstance(case, dict) or not all(
            isinstance(case.get(k), str) and case[k].strip()
            for k in (
                "input_en",
                "rule_en",
                "distraction_en",
                "probe_en",
                "expected_answer_en",
            )
        ):
            raise ValueError(
                "Extract this case's actual input, rule, intervening event, task probe and expected response"
            )
        return result

    result = story.bounded(
        project,
        runtime,
        f"source-example:{source['asset_id']}",
        'Read the ACTUAL illustrated paper example. Extract the concrete task, special rule, intervening distractions and test probe, and its EXPECTED answer. This is a protocol/example, not proof that a particular model produced the answer. Do not invent a result. If the pixels are not sufficiently readable, return {"usable":false,"reason":"why"}. Otherwise return {"usable":true,"example":{"input_en":"...","rule_en":"...","distraction_en":"...","probe_en":"the actual action or task, not a request to recall the rule","expected_answer_en":"...","summary_ja":"日本語"}}.\n'
        + db.dumps(source),
        valid,
        lambda _: {"usable": False, "reason": "Could not verify this example"},
        max_tokens=2200,
        images=[config.safe_path(asset["image_path"])],
    )
    if result is not None:
        if result.get("usable"):
            data.setdefault("source_examples", []).append(
                result["example"]
                | {
                    "source_original_asset_id": source["asset_id"],
                    "source_ids": source["source_ids"],
                    "label": source["label"],
                }
            )
        data["source_example_index"] = index + 1
    return False
