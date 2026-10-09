"""Multiple meaningful pictures in one scene, bound to spoken word anchors."""

from __future__ import annotations

import copy

from . import math_concepts

VERSION = "scene-visual-sequence-1"
BRIEF = (
    "Use shots to advance the visible explanation inside a scene. The first shot reenacts a specific task/input/rule; "
    "a later shot explains the operation or shows the relevant original evidence. Do not hold a page or generic data-flow diagram while the topic changes. "
    "Return shots:[{id:short_id,visual:{...}}] (one to four pictures), and beats:[{shot:exact shot ID,focus:local step index,notice:visible detail,region:optional verified original region ID}]. "
    "Use worked_steps for concrete input-to-answer examples: show the actual rule, example input, distraction/change and expected answer in bilingual details. "
    "Use the supplied originals for evidence. Introduce the task and metric before showing a leaderboard or dense plot. "
    "A paper with a behavioral protocol may have no central equation: explain its test design, example and scoring in depth without inventing mathematical requirements. "
    "For genuine equations, show their specific quantities in a conceptual picture, then the source symbols. "
    "Each paragraph supplies visual_beat; optional visual_cues use exact phrases from that paragraph and a beat number for within-paragraph changes. "
    "Every planned shot must actually be used in the conversation."
)


def validate(board, project, mode, scene=None):
    from . import storyboards

    shots = board.get("shots")
    if not isinstance(shots, list) or not 1 <= len(shots) <= 4:
        raise ValueError("Plan one to four meaningful pictures for this scene")
    ids = [s.get("id") for s in shots]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(
        set(ids)
    ) != len(ids):
        raise ValueError("Every shot needs a distinct ID")
    if scene:
        for shot in shots:
            spec = shot["visual"]
            if spec.get("template") == "worked_steps":
                learning = scene.get("learning", {})
                spec.setdefault(
                    "question_en",
                    learning.get("question_en", board.get("question", "")),
                )
                spec.setdefault("question_ja", learning.get("question_ja", ""))
    if scene and scene.get("opening_scene"):
        board["concrete_first"] = True
        case = next(
            (s for s in shots if s["visual"].get("template") == "worked_steps"), None
        )
        if not case:
            raise ValueError(
                "Open with the concrete task and its visible steps before dense evidence"
            )
        if shots[0] is not case:
            shots.remove(case)
            shots.insert(0, case)
            board["beats"] = [b for b in board["beats"] if b["shot"] == case["id"]] + [
                b for b in board["beats"] if b["shot"] != case["id"]
            ]
    beats = board.get("beats", [])
    if (
        not isinstance(beats, list)
        or not 1 <= len(beats) <= 12
        or any(b.get("shot") not in ids for b in beats)
    ):
        raise ValueError("Every beat must refer to a supplied shot")
    # An unchanged whole original is one shot, not three new explanations just
    # because its metadata lists three guide labels. Keep genuinely distinct
    # verified regions and mathematical reveals.
    whole_originals = {
        s["id"]
        for s in shots
        if s["visual"].get("original_asset_id") and not s["visual"].get("equations")
    }
    seen_regions, unique = set(), []
    for beat in beats:
        if beat["shot"] in whole_originals:
            key = (beat["shot"], beat.get("region"))
            if key in seen_regions:
                continue
            seen_regions.add(key)
            beat["focus"] = 0
        unique.append(beat)
    beats = board["beats"] = unique
    for shot in shots:
        if shot["visual"].get("template") == "worked_steps":
            # A useful task animation must show the input/rule as well as its
            # last answer. Expand missing steps from the checked visual itself.
            local = [b for b in beats if b["shot"] == shot["id"]]
            if len({b.get("focus") for b in local}) < len(shot["visual"]["nodes"]):
                expanded = [
                    next(
                        (b for b in local if b.get("focus") == i),
                        {"shot": shot["id"], "focus": i, "notice": node["detail_en"]},
                    )
                    for i, node in enumerate(shot["visual"]["nodes"])
                ]
                at = next(
                    (i for i, b in enumerate(beats) if b["shot"] == shot["id"]),
                    len(beats),
                )
                others = [b for b in beats if b["shot"] != shot["id"]]
                beats = others[:at] + expanded + others[at:]
                board["beats"] = beats
    for shot in shots:
        local_beats = [
            {k: v for k, v in b.items() if k != "shot"}
            for b in beats
            if b["shot"] == shot["id"]
        ]
        shadow = {k: board.get(k) for k in ("question", "takeaway", "humor")}
        shadow.update(visual=shot.get("visual"), beats=local_beats)
        storyboards.validate(shadow, project, mode)
    if (
        scene
        and scene.get("visual_type") in {"equation", "matrix"}
        and mode == "deep_dive"
        and not any(s["visual"].get("equations") for s in shots)
    ):
        raise ValueError(
            "Keep the mathematical explanation somewhere in the visual sequence"
        )
    board["visual"] = shots[0]["visual"]
    return board


def bind(board, utterances, require=False):
    beats = board["beats"]
    used, ordered = set(), []
    for u in utterances:
        index = u.get("visual_beat", 0)
        if type(index) is not int or not 0 <= index < len(beats):
            if require:
                raise ValueError(
                    "Choose the actual storyboard beat for every paragraph"
                )
            index = 0
        beat = beats[index]
        u.update(
            visual_beat=index,
            visual_shot=beat["shot"],
            visual_focus=beat.get("focus", 0),
        )
        u.pop("visual_focus_region", None)
        if beat.get("region"):
            u["visual_focus_region"] = beat["region"]
        indices = [index]
        for cue in u.get("visual_cues", []) or []:
            ci = cue.get("beat")
            if (
                type(ci) is int
                and 0 <= ci < len(beats)
                and cue.get("phrase", "").casefold() in u["text"].casefold()
            ):
                indices.append(ci)
        for ci in indices:
            used.add(beats[ci]["shot"])
            ordered.append((beats[ci]["shot"], beats[ci].get("focus", 0)))
    if require:
        if (
            board.get("concrete_first")
            and ordered
            and ordered[0] != (board["shots"][0]["id"], 0)
        ):
            raise ValueError(
                "The spoken opening must show the concrete input before the source evidence"
            )
        if used != {s["id"] for s in board["shots"]}:
            raise ValueError("The dialogue must actually show every planned picture")
        for shot in board["shots"]:
            spec = shot["visual"]
            if spec.get("template") == "worked_steps":
                focuses = [f for name, f in ordered if name == shot["id"]]
                seen = set(focuses)
                if seen != set(range(len(spec["nodes"]))):
                    raise ValueError(
                        "Show and explain every concrete step, including the input and expected answer"
                    )
                if [focuses.index(i) for i in range(len(spec["nodes"]))] != sorted(
                    focuses.index(i) for i in range(len(spec["nodes"]))
                ):
                    raise ValueError(
                        "Explain the concrete example's inputs and rule before its outcome"
                    )
            if spec.get("concepts"):
                focuses = [f for name, f in ordered if name == shot["id"]]
                for n in range(len(spec["equations"])):
                    formula = math_concepts.formula_focus(spec, n)
                    if (
                        formula - 1 not in focuses
                        or formula not in focuses
                        or focuses.index(formula - 1) >= focuses.index(formula)
                    ):
                        raise ValueError(
                            "Speak to the concrete quantities before revealing their symbols"
                        )


def render(project, mode, index):
    from . import story_video

    track = project["data"]["modes"][mode]
    scene = track["scenes"][index]
    board = scene["storyboard"]
    paths, assets, steps, beat_map = [], [], [], {}
    for shot in board["shots"]:
        shot_id = shot["id"]
        beats = [(i, b) for i, b in enumerate(board["beats"]) if b["shot"] == shot_id]
        local_turns = [
            {
                "text": b["notice"],
                "source_ids": list(
                    dict.fromkeys(
                        sid
                        for u in scene["utterances"]
                        if u.get("visual_beat") == bi
                        for sid in u.get("source_ids", [])
                    )
                ),
                "visual_focus": b.get("focus", 0),
                **({"visual_focus_region": b["region"]} if b.get("region") else {}),
            }
            for bi, b in beats
        ]
        child = {
            "title": scene["title"],
            "title_ja": scene["title_ja"],
            "focus": scene["focus"],
            "visual": copy.deepcopy(shot["visual"]),
            "utterances": local_turns,
            "storyboard": {
                "beats": [{k: v for k, v in b.items() if k != "shot"} for _, b in beats]
            },
        }
        facade = {
            "id": project["id"] + "-" + shot_id,
            "paper_id": project["paper_id"],
            "data": {
                "modes": {mode: {"lesson_id": track["lesson_id"], "scenes": [child]}}
            },
        }
        story_video.render_scene(facade, mode, 0)
        offset = len(paths)
        paths.extend(child["render_paths"])
        assets.extend(
            {"key": f"scene-{offset + n}", "asset_id": a["asset_id"]}
            for n, a in enumerate(child["focus_assets"])
        )
        for focus, path in enumerate(child["render_paths"]):
            steps.append(
                {
                    "path": path,
                    "shot": shot_id,
                    "local_focus": focus,
                    "math_phase": math_concepts.phase(child["visual"], focus),
                    "visual": child["visual"],
                }
            )
        for (beat_index, _), turn in zip(beats, child["utterances"]):
            beat_map[str(beat_index)] = offset + turn["visual_focus"]
    scene.update(
        render_paths=paths,
        focus_assets=assets,
        asset_id=assets[0]["asset_id"],
        render_steps=steps,
        beat_render_map=beat_map,
        renderer_sha256=story_video.renderer_digest(),
    )
    for u in scene["utterances"]:
        u["visual_focus"] = beat_map.get(str(u.get("visual_beat", 0)), 0)
        u.pop("visual_focus_region", None)
    return scene


def phase(scene, focus):
    steps = scene.get("render_steps", [])
    if type(focus) is int and 0 <= focus < len(steps):
        return steps[focus].get("math_phase")
    return math_concepts.phase(scene["visual"], focus)


def timed_cues(scene, utterance):
    from . import storyboards

    mapping = scene.get("beat_render_map", {})
    shadow = copy.deepcopy(scene)
    shadow["storyboard"] = {
        "beats": [
            {"focus": mapping.get(str(i), 0)}
            for i in range(len(scene["storyboard"]["beats"]))
        ]
    }
    return storyboards.timed_cues(shadow, utterance)


def preview(project, mode, index):
    scene = project["data"]["modes"][mode]["scenes"][index]
    board = scene["storyboard"]
    turns = [
        {"text": b["notice"], "visual_beat": i} for i, b in enumerate(board["beats"])
    ]
    shadow = copy.deepcopy(scene)
    shadow["utterances"] = turns
    facade = {
        "id": project["id"] + "-storyboard",
        "paper_id": project["paper_id"],
        "data": {
            "modes": {
                mode: {
                    "lesson_id": project["data"]["modes"][mode]["lesson_id"],
                    "scenes": [shadow],
                }
            }
        },
    }
    render(facade, mode, 0)
    selected = list(
        dict.fromkeys(shadow["render_paths"][u["visual_focus"]] for u in turns)
    )
    scene["storyboard_preview"] = selected
    # Review the opening example, its final state, and the source/equation.
    scene["storyboard_review_images"] = list(
        dict.fromkeys([selected[0], selected[len(selected) // 2], selected[-1]])
    )
    scene["storyboard_render_sha256"] = shadow["renderer_sha256"]
