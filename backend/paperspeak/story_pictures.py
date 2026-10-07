"""Semantic visual capabilities: a picture's operation must agree with the narration."""

import re

VERSION = "purposeful-pictures-1"
TEMPLATES = {
    "signal_path": 3,
    "robot_control": 3,
    "replay_memory": 3,
    "update_schedule": 3,
    "norm_bounds": 3,
    "noise_trajectory": 2,
    "distribution_return": 3,
    "surface_cells": 3,
    "worked_steps": None,
}
BRIEF = (
    "worked_steps is a concrete sequence of two to six steps. Each node needs en/ja short headings, detail_en/detail_ja (the actual input, rule, decision or answer), and icon from file|folder|chat|rule|model|memory|robot|number. "
    "Its active step is drawn LARGE, with a progress strip. Use this to reenact a paper example, show a changing rule/state/answer, or reveal a hypothetical calculation; do not supply generic Input/Processing/Output labels. "
    "Give the concrete input and the operation/rule separate steps when they require different pictures. A copy-flow arrow shows where the file goes; numbered arguments show command order. Do not use the same flow picture while explaining a different ordering relation. Advance with an exact spoken phrase when one paragraph explains two steps. "
    "For a visible relationship, each step can add objects:[{en:short concrete name,ja:日本語,icon:supported icon}] with two or three objects, and relation:flow|order|contrast. flow draws a directional arrow; order numbers the objects in command/operation order; contrast separates alternatives. Show actual named input/output objects instead of one decorative icon. "
    "Choose visuals from REAL renderer capabilities. signal_path draws input data packets, processing layers, output signals; robot_control draws a robot, observations, a policy and joint-action arrows; "
    "replay_memory draws recorded transitions in a replay drawer and a batch reused for learning; "
    "update_schedule draws data, model capacity and sparse vs dense optimizer-update ticks (not larger step sizes); "
    "norm_bounds draws weights, features and gradients as bounded vectors; "
    "noise_trajectory draws an illustrative jittering vs sustained exploration direction toward an object; "
    "distribution_return draws hypothetical predicted return probabilities, NOT experimental scores; "
    "surface_cells draws a 2D surface/voxel schematic ONLY for surface or voxel representations. "
    "These templates use visual type example, with exactly three short bilingual nodes (noise_trajectory uses two). "
    "Other flow/comparison nodes render as TEXT BOXES ONLY: never claim a vehicle, density cloud, robot photograph or distribution is visible in those boxes. "
    "Use checked ORIGINALS for real photos and measured comparisons. Choose the particular figure whose actual caption supports THIS explanation; do not use the same unrelated chart as an all-purpose background. "
    "A conceptual picture is a schematic, not measured data. Do not rename its objects as something else. Give its caption one concrete takeaway, not repeated disclaimers. "
)


def validate(spec, project=None):
    template = spec.get("template")
    if not template:
        return
    if template == "worked_steps":
        nodes = spec.get("nodes", [])
        if not 2 <= len(nodes) <= 6:
            raise ValueError("A concrete example needs two to six visible steps")
        for node in nodes:
            if not all(
                isinstance(node.get(k), str) and node[k].strip() for k in ("en", "ja")
            ):
                raise ValueError("Example headings need both languages")
            if node.get("icon") not in {
                "file",
                "folder",
                "chat",
                "rule",
                "model",
                "memory",
                "robot",
                "number",
            }:
                raise ValueError("Choose a supported visible example object")
            for field, limit in (("detail_en", 150), ("detail_ja", 100)):
                if (
                    not isinstance(node.get(field), str)
                    or not 1 <= len(node[field]) <= limit
                ):
                    raise ValueError(
                        "Put this example's actual input or answer in short bilingual step details"
                    )
            if "objects" in node:
                objects = node["objects"]
                if (
                    not isinstance(objects, list)
                    or not 2 <= len(objects) <= 3
                    or node.get("relation") not in {"flow", "order", "contrast"}
                ):
                    raise ValueError(
                        "Draw two or three named objects with an explicit flow, order or contrast"
                    )
                for obj in objects:
                    if obj.get("icon") not in {
                        "file",
                        "folder",
                        "chat",
                        "rule",
                        "model",
                        "memory",
                        "robot",
                        "number",
                    } or not all(
                        isinstance(obj.get(k), str) and 1 <= len(obj[k]) <= limit
                        for k, limit in (("en", 35), ("ja", 25))
                    ):
                        raise ValueError(
                            "Visible objects need short bilingual names and supported icons"
                        )
        return
    if template not in TEMPLATES or len(spec.get("nodes", [])) != TEMPLATES[template]:
        raise ValueError(
            "Choose a supported pictorial template with its required panel count"
        )
    if template == "surface_cells" and project:
        text = (
            project.get("data", {}).get("paper_title", "")
            + " "
            + " ".join(
                c.get("claim", "") for c in project.get("data", {}).get("evidence", [])
            )
        )
        if not re.search(
            r"voxel|mesh|surface reconstruction|3d generation", text, re.I
        ):
            raise ValueError(
                "The surface-cell illustration cannot explain an unrelated paper"
            )
    if (
        template
        in {
            "robot_control",
            "replay_memory",
            "noise_trajectory",
            "distribution_return",
            "update_schedule",
        }
        and project
    ):
        text = (
            project.get("data", {}).get("paper_title", "")
            + " "
            + " ".join(
                c.get("claim", "") for c in project.get("data", {}).get("evidence", [])
            )
        )
        pattern = {
            "robot_control": r"robot|reinforcement|control",
            "replay_memory": r"replay|off.policy|experience buffer",
            "noise_trajectory": r"explor|reinforcement",
            "distribution_return": r"reinforcement|return distribution|distributional critic",
            "update_schedule": r"update.to.data|optimizer updates|flashsac",
        }[template]
        if not re.search(pattern, text, re.I):
            raise ValueError(
                "Choose a picture operation that is supported by this paper"
            )


def relevant_original(scene, originals, *, used=()):
    """A zero-score or generic keyword match is not evidence of relevance."""
    stop = {
        "with",
        "from",
        "this",
        "that",
        "paper",
        "model",
        "training",
        "method",
        "results",
        "learning",
        "flashsac",
        "explain",
        "using",
        "through",
        "algorithm",
    }
    words = lambda text: set(re.findall(r"[a-z]{4,}", text.lower())) - stop
    query = words(scene["title"] + " " + scene["focus"])
    ranked = sorted(
        originals,
        key=lambda a: (len(query & words(a["caption"])), a["asset_id"] not in used),
        reverse=True,
    )
    return (
        ranked[0] if ranked and len(query & words(ranked[0]["caption"])) >= 2 else None
    )


def teaching_spec(scene, project=None):
    text = (scene["title"] + " " + scene["focus"]).lower()
    if project and not re.search(
        r"norm|gradient|stabiliz|stability|noise|explor|distribution|return prediction|coverage|replay|data reuse|on.policy|off.policy|scal|updat|capacity|efficien|paradox",
        text,
    ):
        text += (
            " "
            + " ".join(
                c.get("claim", "")
                for c in project.get("data", {}).get("evidence", [])
                if c.get("id") in scene.get("claim_ids", [])
            ).lower()
        )
    if re.search(r"norm|gradient|stabiliz|stability", text):
        template, labels = (
            "norm_bounds",
            [
                ("Weight scale", "重みの大きさ"),
                ("Feature scale", "特徴の大きさ"),
                ("Gradient scale", "勾配の大きさ"),
            ],
        )
    elif re.search(r"noise|explor", text):
        template, labels = (
            "noise_trajectory",
            [
                ("Changing direction", "方向が毎回変わる"),
                ("Persisting direction", "方向をしばらく保つ"),
            ],
        )
    elif re.search(r"distribution|return prediction", text):
        template, labels = (
            "distribution_return",
            [
                ("State and action", "状態と行動"),
                ("Possible returns", "あり得る収益"),
                ("Expected return", "期待する収益"),
            ],
        )
    elif re.search(r"coverage|replay|data reuse|on.policy|off.policy", text):
        template, labels = (
            "replay_memory",
            [
                ("Collect transitions", "経験を集める"),
                ("Keep past experience", "過去の経験を保存"),
                ("Reuse a batch", "まとめて再利用"),
            ],
        )
    elif re.search(r"scal|updat|capacity|efficien|paradox", text):
        template, labels = (
            "update_schedule",
            [
                ("More collected data", "多くのデータ"),
                ("More model capacity", "大きいモデル"),
                ("Fewer optimizer updates", "更新回数を減らす"),
            ],
        )
    elif re.search(
        r"robot|reinforcement|control",
        text + " " + (project or {}).get("data", {}).get("paper_title", "").lower(),
    ):
        template, labels = (
            "robot_control",
            [
                ("Observe the robot", "状態を観察する"),
                ("Choose an action", "行動を選ぶ"),
                ("Act and observe again", "動いて再び観察"),
            ],
        )
    else:
        template, labels = (
            "signal_path",
            [
                ("Input data", "入力データ"),
                ("Transform the representation", "表現を変える"),
                ("Output information", "出力情報"),
            ],
        )
    spec = {
        "type": "example",
        "template": template,
        "nodes": [{"en": en, "ja": ja} for en, ja in labels],
        "caption_en": "Conceptual illustration; not a measured result",
        "caption_ja": "仕組みを理解するための模式図。実測結果ではありません。",
    }
    try:
        validate(spec, project)
    except ValueError:
        # A similar-sounding scene title in a different field must not acquire
        # a robot or a FlashSAC-specific update strategy by accident.
        spec.update(
            template="signal_path",
            nodes=[
                {"en": "Input data", "ja": "入力データ"},
                {"en": "Transform the representation", "ja": "表現を変える"},
                {"en": "Output information", "ja": "出力情報"},
            ],
        )
    takeaways = {
        "signal_path": (
            "Follow information from input to output",
            "入力から出力まで、情報の流れを追う",
        ),
        "robot_control": (
            "Observe, choose, act — then observe again",
            "観察し、選び、動き、また観察する",
        ),
        "replay_memory": (
            "Save experience. Sample it again for learning.",
            "経験を保存し、取り出してまた学習に使う",
        ),
        "update_schedule": (
            "Larger data and capacity, fewer optimizer updates",
            "データとモデルを大きくし、更新回数を減らす",
        ),
        "norm_bounds": (
            "Keep weights, features and gradients under control",
            "重み・特徴・勾配の大きさを制御する",
        ),
        "noise_trajectory": (
            "Illustrative paths: new direction vs persistent noise",
            "経路の模式例：方向を毎回変える場合と、しばらく保つ場合",
        ),
        "distribution_return": (
            "Predict a range of possible returns",
            "あり得る収益の分布を予測する",
        ),
    }
    spec["caption_en"], spec["caption_ja"] = takeaways[spec["template"]]
    return spec


def repair_repeated_background(project, mode):
    """One bounded repair BEFORE speech: rewrite dialogue for the new pictures.

    A verified new panel is a legitimate continuation of an original. Merely
    renaming a full-page background for another topic is not a visual beat.
    """
    track = project["data"]["modes"][mode]
    audit = coverage(track)
    track["visual_audit"] = audit
    if not audit["repeated_background"] or track.get("picture_repair_round", 0):
        return False
    track["picture_repair_round"] = 1
    from . import storyboards

    seen_regions = set()
    repaired = []
    for index in audit["dominant_original_scenes"]:
        scene = track["scenes"][index]
        board = scene.get("storyboard", {})
        regions = {b["region"] for b in board.get("beats", []) if b.get("region")}
        new_panel = bool(regions - seen_regions)
        seen_regions |= regions
        if index == audit["dominant_original_scenes"][0] or new_panel:
            continue
        # Keep the saved original version for diagnosis. This runs before TTS,
        # and never changes ready videos or their existing practice recordings.
        scene.setdefault("picture_repair_history", []).append(
            {
                "visual": scene.get("visual"),
                "utterances": scene.get("utterances", []),
                "reason": "Repeated full figure without a new verified panel",
            }
        )
        replacement = teaching_spec(scene, project)
        board = {
            "question": scene["focus"],
            "takeaway": scene["focus"],
            "humor": "Aiden questions one visible operation and Maya clarifies it without inventing results.",
            "visual": replacement,
            "beats": [
                {"focus": i, "notice": n["en"]}
                for i, n in enumerate(replacement["nodes"])
            ],
            "fallback": True,
        }
        if scene.get("visual_type") in {"equation", "matrix"} and mode == "deep_dive":
            board = storyboards.fallback(project, scene)
        for key in (
            "utterances",
            "reviews",
            "visual_ready",
            "render_paths",
            "storyboard_preview",
            "storyboard_review_images",
            "visual_repair_issues",
        ):
            scene.pop(key, None)
        scene.update(storyboard=board, visual=board["visual"], storyboard_ready=True)
        scene["script_revision"] = scene.get("script_revision", 0) + 1
        repaired.append(index)
    track["visual_audit"]["repair_scenes"] = repaired
    return bool(repaired)


def coverage(track):
    scenes = track.get("scenes", [])
    uses = {}
    families = {}
    for index, scene in enumerate(scenes):
        visual = (
            scene.get("visual", scene.get("storyboard", {}).get("visual", {})) or {}
        )
        original = visual.get("original_asset_id")
        shot_specs = [
            s["visual"] for s in (scene.get("storyboard") or {}).get("shots", [])
        ]
        if shot_specs:
            for spec in shot_specs:
                name = spec.get("original_asset_id")
                if name:
                    uses.setdefault(name, []).append(index)
            original = None
        if original:
            uses.setdefault(original, []).append(index)
        family = (
            "original:" + original
            if original
            else "picture:" + visual["template"]
            if visual.get("template")
            else "math:" + ",".join(c["template"] for c in visual["concepts"])
            if visual.get("concepts")
            else "cards:" + str(visual.get("type"))
        )
        families.setdefault(family, []).append(index)
    dominant = max(uses.values(), key=len, default=[])
    return {
        "scenes": len(scenes),
        "originals": len(uses),
        "visual_families": families,
        "dominant_original_scenes": dominant,
        "repeated_background": len(scenes) >= 4 and len(dominant) > len(scenes) / 2,
        "pictorial_scenes": sum(
            bool(
                (s.get("visual") or s.get("storyboard", {}).get("visual", {})).get(
                    "template"
                )
            )
            or any(
                v["visual"].get("template")
                for v in (s.get("storyboard") or {}).get("shots", [])
            )
            for s in scenes
        ),
    }
