"""Source-grounded visual direction, checked before the dialogue is drafted."""

from __future__ import annotations

import copy
import re

from . import config, db, math_concepts, story, story_pictures, story_video
from .runtime import GPUUnavailable, PracticePreempted

VERSION = "visual-before-dialogue-3-purposeful-pictures"
TEMPLATES = set(story_pictures.TEMPLATES)


def validate(board, project, mode, scene=None):
    if isinstance(board, dict) and board.get("shots"):
        from . import story_shots

        return story_shots.validate(board, project, mode, scene)
    if scene is not None and project.get("data", {}).get("direction_policy"):
        raise ValueError(
            "The new scene needs shots with concrete examples and meaningful visual beats"
        )
    if not isinstance(board, dict) or any(
        not isinstance(board.get(k), str) or not board[k].strip()
        for k in ("question", "takeaway", "humor")
    ):
        raise ValueError(
            "Specify the viewer question, takeaway and a useful humor beat"
        )
    spec = story.validate_visual(board.get("visual"), mode)
    if project.get("data", {}).get("paper_title") and story.is_lora(project):
        math_concepts.normalize_lora_symbols(spec)
    math_concepts.validate(spec, required=mode == "deep_dive")
    story_pictures.validate(spec, project)
    if (
        scene
        and mode == "deep_dive"
        and scene.get("visual_type") in {"equation", "matrix"}
        and not spec.get("equations")
    ):
        raise ValueError(
            "Keep the planned mathematical explanation; do not replace its equation with an unrelated photograph"
        )
    if (
        mode == "deep_dive"
        and spec["type"] in {"equation", "matrix"}
        and not spec.get("equations")
    ):
        raise ValueError(
            "A mathematical storyboard must include the source equation and its conceptual picture"
        )
    if spec.get("template") and spec["template"] not in TEMPLATES:
        raise ValueError("Choose a supported fixed diagram template")
    if spec.get("template") and spec["type"] != "example":
        raise ValueError(
            "A fixed teaching sketch is a conceptual example, not measured evidence"
        )
    if spec.get("template") == "surface_cells" and len(spec["nodes"]) != 3:
        raise ValueError("The surface-cell sketch needs exactly three panels")
    originals = {a["asset_id"]: a for a in story.original_catalogue(project)}
    original = originals.get(spec.get("original_asset_id"))
    if spec.get("original_asset_id") and not original:
        raise ValueError("Choose a checked original from the supplied catalogue")
    if spec["type"] == "original" and not original:
        raise ValueError("Original visuals require a checked source asset")
    beats = board.get("beats")
    limit = (
        8
        if spec.get("concepts")
        else 6
        if spec.get("template") == "worked_steps"
        else 4
    )
    if not isinstance(beats, list) or not 1 <= len(beats) <= limit:
        raise ValueError("Plan a bounded sequence of visual beats")
    regions = {r["id"] for r in original.get("regions", [])} if original else set()
    for beat in beats:
        if not isinstance(beat, dict) or not beat.get("notice"):
            raise ValueError("Every beat needs an observable detail")
        if beat.get("region") and beat["region"] not in regions:
            raise ValueError("Use an existing verified panel ID")
        focus = beat.get("focus", 0)
        count = math_concepts.focus_count(spec)
        if not isinstance(focus, int) or not 0 <= focus < count:
            raise ValueError("Visual focus must name a supplied diagram element")
    if spec.get("concepts"):
        focuses = [b.get("focus", 0) for b in beats if not b.get("region")]
        for index in range(len(spec["equations"])):
            formula = math_concepts.formula_focus(spec, index)
            if (
                formula - 1 not in focuses
                or formula not in focuses
                or focuses.index(formula - 1) > focuses.index(formula)
            ):
                raise ValueError(
                    "Show every equation's conceptual picture before revealing its symbols"
                )
    return board


def _fallback_single(project, scene):
    candidate = scene.get("math_storyboard_candidate")
    if candidate:
        board = copy.deepcopy(candidate)
        spec = board["visual"]
        # Keep only formulas that already rendered successfully; avoid guessing new math.
        spec["concepts"] = [math_concepts.fallback(eq) for eq in spec["equations"]]
        board["beats"] = [
            {
                "notice": "See the quantities and their relationship"
                if phase == 0
                else "Match the relationship to its equation",
                "focus": math_concepts.formula_focus(spec, index) - 1 + phase,
            }
            for index in range(len(spec["equations"]))
            for phase in (0, 1)
        ]
        board["fallback"] = True
        return board
    if scene.get("visual_type") in {"equation", "matrix"}:
        lookup = story.source_lookup(project)
        refs = {
            sid
            for c in story.context_for(project, scene)["claims"]
            for sid in c["source_ids"]
        }
        candidates = [
            s for sid, s in lookup.items() if sid in refs and s["kind"] == "equation"
        ]
        if candidates:
            source = candidates[0]
            latex = re.sub(r",?\s*\(\d+\)\s*$", "", source["data"]["text"]).replace(
                r"\bm", r"\boldsymbol"
            )
            spec = story_pictures.teaching_spec(scene, project)
            spec.pop("template", None)
            spec.update(
                type="equation",
                equations=[
                    {
                        "latex": latex,
                        "en": "Relation from the cited paper",
                        "ja": "原論文にある関係",
                    }
                ],
                caption_en="Connect the quantities before using the equation",
                caption_ja="量の意味を確認してから式でつなぐ",
            )
            spec["concepts"] = [math_concepts.fallback(spec["equations"][0])]
            return {
                "question": scene["focus"],
                "takeaway": scene["focus"],
                "humor": "Aiden asks what a symbol actually refers to, and Maya maps it to the pictured quantity.",
                "visual": spec,
                "beats": [
                    {"notice": "See the quantities", "focus": 0},
                    {"notice": "Connect them to the source relation", "focus": 1},
                ],
                "fallback": True,
                "equation_source_id": source["id"],
            }
    originals = story.original_catalogue(project)
    chosen = story_pictures.relevant_original(scene, originals)
    planned = next(
        (
            a
            for a in originals
            if scene.get("visual_type") == "original"
            and a["asset_id"] == scene.get("visual_asset_id")
        ),
        None,
    )
    # A source explicitly selected for a scene is useful even when the scene
    # calls it a 'concrete example' rather than repeating its caption words.
    # The implicit fallback, however, must establish relevance first.
    chosen = planned or chosen
    visual = story_pictures.teaching_spec(scene, project)
    beats = [
        {
            "notice": "See the concrete quantities and the operation",
            "focus": 0,
        }
    ]
    if chosen:
        visual = story.simple_visual(scene)
        visual.update(
            type="original",
            original_asset_id=chosen["asset_id"],
            caption_en=f"{chosen['label']} · Original paper figure",
            caption_ja=f"{chosen['label']} · 論文の原図",
        )
        region = next(
            (
                r
                for r in chosen["regions"]
                if r["id"] == scene.get("visual_focus_region")
            ),
            None,
        )
        if not region:
            # Keep uncertain cuts as complete originals, but do not leave a
            # known readable panel unused after falling back to a full page.
            words = set(
                re.findall(
                    r"[a-z]{4,}", (scene["title"] + " " + scene["focus"]).lower()
                )
            )
            candidates = [
                r
                for r in chosen["regions"]
                if not re.search(r"caption|legend", r.get("label_en", ""), re.I)
            ]
            region = max(
                candidates,
                key=lambda r: (
                    len(
                        words
                        & set(re.findall(r"[a-z]{4,}", r.get("label_en", "").lower()))
                    ),
                    r.get("area", 0),
                ),
                default=None,
            )
        if region:
            beats.append(
                {
                    "notice": "Look at the selected detail in the original figure",
                    "focus": 0,
                    "region": region["id"],
                }
            )
    else:
        beats = [
            {"notice": node["en"], "focus": i} for i, node in enumerate(visual["nodes"])
        ]
    return {
        "question": scene["focus"],
        "takeaway": scene["focus"],
        "humor": "Aiden asks a concrete question about the visible object; Maya gives a brief dry response without inventing results.",
        "beats": beats,
        "visual": visual,
        "fallback": True,
    }


def fallback(project, scene):
    board = _fallback_single(project, scene)
    if not project.get("data", {}).get("direction_policy"):
        return board
    steps = scene.get("learning", {}).get("example_steps", [])
    if steps:
        example = {
            "type": "example",
            "template": "worked_steps",
            "nodes": steps,
            "caption_en": scene["learning"]["takeaway_en"],
            "caption_ja": scene["learning"]["takeaway_ja"],
        }
        from . import story_pictures

        try:
            story_pictures.validate(example, project)
        except (ValueError, TypeError):
            board["omitted_example"] = "The proposed example steps were not renderable"
            return board
        shots = [{"id": "example", "visual": example}]
        beats = [
            {"shot": "example", "focus": i, "notice": n["detail_en"]}
            for i, n in enumerate(steps)
        ]
        if board["visual"].get("original_asset_id") or board["visual"].get("equations"):
            shots.append({"id": "evidence", "visual": board["visual"]})
            beats.extend(b | {"shot": "evidence"} for b in board["beats"])
        board.update(shots=shots, beats=beats, visual=example)
    return board


def bind_dialogue(board, utterances, *, require_math_sequence=False):
    if board.get("shots"):
        from . import story_shots

        return story_shots.bind(board, utterances, require=require_math_sequence)
    for utterance in utterances:
        index = utterance.get("visual_beat", 0)
        if not isinstance(index, int) or not 0 <= index < len(board["beats"]):
            index = 0
        beat = board["beats"][index]
        utterance["visual_focus"] = beat.get("focus", 0)
        if beat.get("region"):
            utterance["visual_focus_region"] = beat["region"]
        else:
            utterance.pop("visual_focus_region", None)
    if require_math_sequence and board["visual"].get("concepts"):
        focuses = []
        for u in utterances:
            if not u.get("visual_focus_region"):
                focuses.append(u["visual_focus"])
            for cue in u.get("visual_cues", []) or []:
                beat = cue.get("beat")
                if (
                    isinstance(beat, int)
                    and 0 <= beat < len(board["beats"])
                    and cue.get("phrase")
                    and cue["phrase"].lower() in u["text"].lower()
                    and not board["beats"][beat].get("region")
                ):
                    focuses.append(board["beats"][beat].get("focus", 0))
        for index in range(len(board["visual"]["equations"])):
            formula = math_concepts.formula_focus(board["visual"], index)
            if (
                formula - 1 not in focuses
                or formula not in focuses
                or focuses.index(formula - 1) >= focuses.index(formula)
            ):
                raise ValueError(
                    f"Explain concept beat (focus {formula - 1}) before equation beat (focus {formula}) in the actual dialogue. Assign visual_beat indices from the approved storyboard to both turns."
                )


def _preview(project, mode, index):
    scene = project["data"]["modes"][mode]["scenes"][index]
    board = scene["storyboard"]
    if board.get("shots"):
        from . import story_shots

        return story_shots.preview(project, mode, index)
    preview = {
        "id": project["id"] + "-storyboard",
        "paper_id": project["paper_id"],
        "data": {
            "modes": {
                mode: {
                    "lesson_id": project["data"]["modes"][mode]["lesson_id"],
                    "scenes": [
                        {
                            "title": scene["title"],
                            "title_ja": scene["title_ja"],
                            "focus": scene["focus"],
                            "visual": dict(board["visual"]),
                            "utterances": [
                                {
                                    "text": b["notice"],
                                    "visual_focus": b.get("focus", 0),
                                    **(
                                        {"visual_focus_region": b["region"]}
                                        if b.get("region")
                                        else {}
                                    ),
                                }
                                for b in board["beats"]
                            ],
                        }
                    ],
                }
            }
        },
    }
    story_video.render_scene(preview, mode, 0)
    rendered = preview["data"]["modes"][mode]["scenes"][0]
    scene["storyboard_preview"] = list(
        dict.fromkeys(
            [
                rendered["render_paths"][u["visual_focus"]]
                for u in rendered["utterances"]
            ]
        )
    )
    scene["storyboard_render_sha256"] = rendered["renderer_sha256"]
    if board["visual"].get("concepts"):
        first = math_concepts.formula_focus(board["visual"]) - 1
        scene["math_storyboard_candidate"] = copy.deepcopy(board)
        # Review the concept AND its equation, rather than an optional source overview.
        scene["storyboard_review_images"] = rendered["render_paths"][first : first + 2]


def review_images(scene):
    return scene.get("storyboard_review_images", scene.get("storyboard_preview", []))[
        : 3 if scene.get("storyboard", {}).get("shots") else 2
    ]


def step(project, runtime, mode, index):
    scene = project["data"]["modes"][mode]["scenes"][index]
    if project["data"].get("direction_policy"):
        scene["opening_scene"] = index == 0
    state = scene.setdefault("storyboard_review", {"attempts": 0, "history": []})
    if state["attempts"] >= 3:
        scene["storyboard"] = fallback(project, scene)
        _preview(project, mode, index)
        scene["storyboard_ready"] = True
        state.update(status="best_effort", complete=True)
        project["data"]["warnings"].append(
            {
                "unit": f"storyboard:{mode}:{index}",
                "reason": state.get("error"),
                "action": "Keep checked equations and simplify their conceptual pictures"
                if scene["storyboard"]["visual"].get("concepts")
                else "Use a relevant checked original or a fixed picture of this operation",
            }
        )
        return
    if not scene.get("storyboard"):
        prompt = (
            "Design the actual VISUAL STORYBOARD before a documentary scene is written. "
            + story.VISUAL_DIRECTION_BRIEF
            + story_pictures.BRIEF
            + "A viewer should understand one new distinction by looking at this picture. Choose an ORIGINAL when it shows the object, mechanism or evidence. "
            "Original beats start with the whole figure, then a verified region; no new coordinates. Use a short visible detail as notice. "
            "Avoid jargon cards. Plan a specific question, causal takeaway and one lightly witty misunderstanding that the visible picture can resolve. "
            + (
                math_concepts.BRIEF
                + " For each equation, beats show its conceptual picture at focus=(2*equation_index + source_offset), then its symbols at the next focus. source_offset is 1 when original_asset_id is present, otherwise 0. Use up to eight beats. Add "
                + math_concepts.SCHEMA
                + " and equations:[{latex:exact source formula,en:meaning,ja:meaning}] inside visual. "
                if mode == "deep_dive"
                else ""
            )
            + 'Return {"question":"viewer question","takeaway":"one insight","humor":"specific exchange idea","beats":[{"notice":"what to notice","focus":0,"region":"optional verified original region ID, omit for whole"}],'
            '"visual":{"type":"original|comparison|flow|timeline|equation|example","original_asset_id":"optional checked ID","template":"optional supported template","nodes":[{"en":"short label","ja":"日本語"}],"caption_en":"one short sentence","caption_ja":"短い説明"}}. '
            "Fixed pictorial templates use type example. For equations preserve supplied notation. Use at most three labels unless essential.\n"
            + "MODE: "
            + mode
            + "\nSCENE: "
            + db.dumps(scene)
            + "\nEVIDENCE: "
            + db.dumps(story.context_for(project, scene))
            + "\nCHECKED ORIGINALS: "
            + db.dumps(story.original_catalogue(project))
            + "\nVISUALS ALREADY USED IN THIS FILM (advance to a new distinction; do not recycle an unrelated figure): "
            + db.dumps(
                [
                    {
                        "title": s["title"],
                        "visual": s.get("storyboard", {}).get("visual"),
                    }
                    for s in project["data"]["modes"][mode]["scenes"][:index]
                ]
            )
            + "\nPREVIOUS VISUAL ISSUE: "
            + str(state.get("error", ""))
        )
        if project["data"].get("direction_policy"):
            from . import story_direction, story_shots

            prompt += (
                "\n"
                + story_direction.BRIEF
                + story_shots.BRIEF
                + "\nLEARNING CONTRACT: "
                + db.dumps(scene.get("learning", {}))
                + "\nUse shots and shot IDs on all beats. Keep visual as the first shot's visual for compatibility."
            )
        board = story.bounded(
            project,
            runtime,
            f"storyboard:{mode}:{index}",
            prompt,
            lambda r: validate(r, project, mode, scene),
            lambda _: fallback(project, scene),
            max_tokens=4500 if project["data"].get("direction_policy") else 2800,
        )
        if board is not None:
            scene["storyboard"] = board
        return
    try:
        if not scene.get("storyboard_preview"):
            _preview(project, mode, index)
            return
        result = story.ask(
            project,
            runtime,
            f"storyboard-image-review:{mode}:{index}",
            "Review these ACTUAL rendered frames against the planned question and takeaway. "
            "Say what a viewer can visibly notice. Flag labels that do not help, misleading relationships, illegible text, or a caption/beat that describes something absent. "
            "The source figure may contain more detail than the overview discusses; a checked conceptual sketch can deliberately omit technical details. "
            "Do not ask for unnecessary completeness. Do not praise a picture just because its metadata sounds plausible. "
            "A teaching adaptation may replace file names or unrelated distractions when clearly marked as an illustrative example; keep the test mechanism, and never claim those adapted details are the paper's exact experiment. "
            "Distinguish the protocol's expected answer from a measured model response. An expected-pattern example must say expected/correct answer, not assert that a model produced it. "
            "For originals the nodes are guide labels; they need not be printed over the unmodified source image. Use the verified caption and source identity below; do not guess that a checked figure number or page is wrong from its appearance. "
            "Region names identify locations, not proof of camera angles or material-channel meanings. If the source does not establish a subimage's meaning, use a visible description rather than guessing it. "
            + (
                math_concepts.BRIEF
                + " Verify the concept picture and formula show the same operation; meanings and colored symbols must agree with the source, not merely with the metadata. "
                if mode == "deep_dive"
                else ""
            )
            + 'Return {"passed":true,"visible_observation":"what is actually visible","issues":["only concrete problems"]}.\n'
            + db.dumps(scene["storyboard"])
            + "\nVERIFIED ORIGINAL SOURCE CAPTION AND REGIONS: "
            + db.dumps(
                [
                    a
                    for a in story.original_catalogue(project)
                    if a["asset_id"]
                    == scene["storyboard"].get("visual", {}).get("original_asset_id")
                ]
            ),
            images=[config.safe_path(p) for p in review_images(scene)],
            max_tokens=1500,
        )
        if not isinstance(result.get("issues"), list):
            raise ValueError("Image review needs a list of actual issues")
        state["attempts"] += 1
        state["history"].append(result)
        if result.get("passed") is True and not result["issues"]:
            scene["storyboard_ready"] = True
            state.update(complete=True, status="checked", version=VERSION)
        else:
            state["error"] = db.dumps(result["issues"])
            scene.pop("storyboard", None)
            scene.pop("storyboard_preview", None)
            scene.pop("storyboard_review_images", None)
    except (PracticePreempted, GPUUnavailable):
        raise
    except Exception as exc:
        state.update(attempts=state["attempts"] + 1, error=str(exc)[:700])
        state["history"].append({"error": str(exc)[:700]})
        scene.pop("storyboard", None)
        scene.pop("storyboard_preview", None)
        scene.pop("storyboard_review_images", None)


def timed_cues(scene, utterance):
    """Resolve spoken anchor phrases against ASR words, never guessed durations."""
    if scene.get("beat_render_map") and scene.get("storyboard", {}).get("shots"):
        from . import story_shots

        return story_shots.timed_cues(scene, utterance)
    stamps = utterance.get("audio_check", {}).get("timestamps", [])
    norm = lambda value: re.findall(r"[a-z0-9]+", value.lower())
    tokens = [
        (word, item["start"])
        for item in stamps
        for word in norm(item.get("word", item.get("text", "")))
    ]
    cues = []
    for cue in utterance.get("visual_cues", []) or []:
        beat = cue.get("beat")
        if not isinstance(beat, int) or not 0 <= beat < len(
            scene.get("storyboard", {}).get("beats", [])
        ):
            continue
        words = norm(cue.get("phrase", ""))
        match = next(
            (
                i
                for i in range(len(tokens) - len(words) + 1)
                if words and [w for w, _ in tokens[i : i + len(words)]] == words
            ),
            None,
        )
        if match is None:
            continue
        target = scene["storyboard"]["beats"][beat]
        region = target.get("region")
        focus = (
            next(
                (
                    scene["visual"]["zoom_start"] + i
                    for i, r in enumerate(scene["visual"].get("zoom_regions", []))
                    if r["id"] == region
                ),
                target.get("focus", 0),
            )
            if region
            else target.get("focus", 0)
        )
        if 0 <= focus < len(scene.get("render_paths", [])):
            cues.append(
                {
                    "start": tokens[match][1],
                    "focus": focus,
                    "phrase": cue["phrase"],
                    "method": "ASR word anchor",
                }
            )
    return sorted(cues, key=lambda c: c["start"])
