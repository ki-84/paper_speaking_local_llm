"""Source-grounded visual direction, checked before the dialogue is drafted."""

from __future__ import annotations

import re

from . import config, db, story, story_video
from .runtime import GPUUnavailable, PracticePreempted

VERSION = "visual-before-dialogue-1"
TEMPLATES = {"surface_cells"}


def validate(board, project, mode):
    if not isinstance(board, dict) or any(
        not isinstance(board.get(k), str) or not board[k].strip()
        for k in ("question", "takeaway", "humor")
    ):
        raise ValueError(
            "Specify the viewer question, takeaway and a useful humor beat"
        )
    spec = story.validate_visual(board.get("visual"), mode)
    if spec.get("template") and spec["template"] not in TEMPLATES:
        raise ValueError("Choose a supported fixed diagram template")
    if spec.get("template") and spec["type"] != "example":
        raise ValueError(
            "The surface-cell sketch is a conceptual example, not measured evidence"
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
    if not isinstance(beats, list) or not 1 <= len(beats) <= 4:
        raise ValueError("Plan one to four visual beats")
    regions = {r["id"] for r in original.get("regions", [])} if original else set()
    for beat in beats:
        if not isinstance(beat, dict) or not beat.get("notice"):
            raise ValueError("Every beat needs an observable detail")
        if beat.get("region") and beat["region"] not in regions:
            raise ValueError("Use an existing verified panel ID")
        focus = beat.get("focus", 0)
        count = max(len(spec["nodes"]), len(spec.get("equations", [])), 1)
        if not isinstance(focus, int) or not 0 <= focus < count:
            raise ValueError("Visual focus must name a supplied diagram element")
    return board


def fallback(project, scene):
    originals = story.original_catalogue(project)
    terms = set(
        re.findall(r"[a-z]{4,}", (scene["title"] + " " + scene["focus"]).lower())
    )
    chosen = (
        max(
            originals,
            key=lambda a: len(
                terms & set(re.findall(r"[a-z]{4,}", a["caption"].lower()))
            ),
        )
        if originals
        else None
    )
    planned = next(
        (
            a
            for a in originals
            if scene.get("visual_type") == "original"
            and a["asset_id"] == scene.get("visual_asset_id")
        ),
        None,
    )
    chosen = planned or chosen
    visual = story.simple_visual(scene)
    beats = [
        {
            "notice": "Orient the viewer to the complete checked source figure",
            "focus": 0,
        }
    ]
    if chosen:
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
        if region:
            beats.append(
                {
                    "notice": "Look at the selected detail in the original figure",
                    "focus": 0,
                    "region": region["id"],
                }
            )
    return {
        "question": scene["focus"],
        "takeaway": scene["focus"],
        "humor": "Aiden asks a concrete question about the visible object; Maya gives a brief dry response without inventing results.",
        "beats": beats,
        "visual": visual,
        "fallback": True,
    }


def bind_dialogue(board, utterances):
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


def _preview(project, mode, index):
    scene = project["data"]["modes"][mode]["scenes"][index]
    board = scene["storyboard"]
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


def step(project, runtime, mode, index):
    scene = project["data"]["modes"][mode]["scenes"][index]
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
                "action": "Use a complete checked original instead of an unclear invented diagram",
            }
        )
        return
    if not scene.get("storyboard"):
        prompt = (
            "Design the actual VISUAL STORYBOARD before a documentary scene is written. "
            + story.VISUAL_DIRECTION_BRIEF
            + "A viewer should understand one new distinction by looking at this picture. Choose an ORIGINAL when it shows the object, mechanism or evidence. "
            "Original beats start with the whole figure, then a verified region; no new coordinates. Use a short visible detail as notice. "
            "For the sparse-surface idea you may use example template surface_cells: a clearly hypothetical 2D surface sketch comparing a full grid, surface cells, and geometry/material attributes. It does NOT show learned latent compression, real measured outputs or a performance result. "
            "Avoid jargon cards. Plan a specific question, causal takeaway and one lightly witty misunderstanding that the visible picture can resolve. "
            'Return {"question":"viewer question","takeaway":"one insight","humor":"specific exchange idea","beats":[{"notice":"what to notice","focus":0,"region":"optional verified original region ID, omit for whole"}],'
            '"visual":{"type":"original|comparison|flow|timeline|equation|example","original_asset_id":"optional checked ID","template":"optional surface_cells","nodes":[{"en":"short label","ja":"日本語"}],"caption_en":"one short sentence","caption_ja":"短い説明"}}. '
            "For surface_cells supply exactly three nodes for the three panels. For equations preserve supplied notation. Use at most three labels unless essential.\n"
            + "MODE: "
            + mode
            + "\nSCENE: "
            + db.dumps(scene)
            + "\nEVIDENCE: "
            + db.dumps(story.context_for(project, scene))
            + "\nCHECKED ORIGINALS: "
            + db.dumps(story.original_catalogue(project))
            + "\nPREVIOUS VISUAL ISSUE: "
            + str(state.get("error", ""))
        )
        board = story.bounded(
            project,
            runtime,
            f"storyboard:{mode}:{index}",
            prompt,
            lambda r: validate(r, project, mode),
            lambda _: fallback(project, scene),
            max_tokens=2800,
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
            "For originals the nodes are guide labels; they need not be printed over the unmodified source image. Use the verified caption and source identity below; do not guess that a checked figure number or page is wrong from its appearance. "
            "Region names identify locations, not proof of camera angles or material-channel meanings. If the source does not establish a subimage's meaning, use a visible description rather than guessing it. "
            'Return {"passed":true,"visible_observation":"what is actually visible","issues":["only concrete problems"]}.\n'
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
            images=[config.safe_path(p) for p in scene["storyboard_preview"][:2]],
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
    except (PracticePreempted, GPUUnavailable):
        raise
    except Exception as exc:
        state.update(attempts=state["attempts"] + 1, error=str(exc)[:700])
        state["history"].append({"error": str(exc)[:700]})
        scene.pop("storyboard", None)
        scene.pop("storyboard_preview", None)


def timed_cues(scene, utterance):
    """Resolve spoken anchor phrases against ASR words, never guessed durations."""
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
