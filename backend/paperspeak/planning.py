"""Concept-based planning with explicit coverage and duplicate evidence links."""

import json
import math

from .quality import english_only


def plan_step(data, sources, runtime):
    claims = [c for note in data["notes"] for c in note["claims"]]
    if not claims:
        raise ValueError("No supported teaching claims were found in this paper.")
    if data.get("planning_version") != 2:
        data.update(planning_version=2, outline=[], claim_map={}, planning_index=0)
        data.pop("learning_goals", None)
    if "learning_goals" not in data:
        result = runtime.ask(
            "Design a learning path about this paper for a beginner. Organize by IDEAS, never by source pages or note numbers. "
            "Start with the practical problem and prerequisites, then previous approaches, an intuitive mechanism with examples, mathematical details, controlled experiments, and limits. "
            "Separate introductory intuition from notation and detailed experimental settings. Combine repeated topics in these reading summaries. "
            "Use short, friendly English titles, usually three to seven words. Each focus is one simple sentence starting with Learn, addressed directly to the learner. "
            "Include enough focused goals to explain the whole paper without fixing the length. Later goals must add something new. "
            'Return {"goals":[{"title":"simple English title","focus":"what the learner will understand"}],"glossary":[{"term":"needed technical word","meaning":"very simple English definition"}]}.\n'
            + "PAPER: "
            + data["title"]
            + "\nREADING SUMMARIES: "
            + json.dumps([n["summary"] for n in data["notes"]]),
            profile=data["model"],
            max_tokens=6500,
        )
        goals = result.get("goals", [])
        if not 1 <= len(goals) <= 40 or any(
            not g.get("title")
            or not g.get("focus")
            or not english_only(g["title"] + g["focus"])
            for g in goals
        ):
            raise ValueError("The learning path needs clear English goals.")
        data["learning_goals"] = goals
        data["glossary"] = [
            g
            for g in result.get("glossary", [])
            if g.get("term")
            and g.get("meaning")
            and english_only(json.dumps(g, ensure_ascii=False))
        ]
        return False
    glossary_index = data.get("glossary_review_index", 0)
    glossary = data.get("glossary", [])
    if glossary_index < len(glossary):
        batch = glossary[glossary_index : glossary_index + 6]
        result = runtime.ask(
            "Review these glossary definitions for scientific accuracy and beginner understanding. Correct misleading simplifications before they enter a lesson. "
            "Use everyday words and short sentences; explain a term instead of repeating it. Ground paper-specific meanings in the reading summaries, while keeping general mathematical definitions correct. "
            "Distinguish a matrix's number of entries, its rank (independent directions), and the magnitude of its values. A low-rank matrix can retain the same shape and be represented using fewer numbers. "
            "Do not confuse unchanged network structure or runtime cost with unchanged predictions after learning. Avoid suggesting that a method's one local pair of matrices is the only pair in an entire network. "
            'Keep each supplied term exactly once in the same order. Return {"glossary":[{"term":"original term","meaning":"correct, simple definition"}]}.\nTERMS: '
            + json.dumps(batch)
            + "\nREADING SUMMARIES: "
            + json.dumps([n["summary"] for n in data["notes"]]),
            profile=data["model"],
            max_tokens=4500,
        )
        reviewed = result.get("glossary", [])
        if [g.get("term") for g in reviewed] != [g["term"] for g in batch] or any(
            not isinstance(g.get("meaning"), str)
            or not g["meaning"].strip()
            or not english_only(g["meaning"])
            for g in reviewed
        ):
            raise ValueError(
                "The glossary review must keep every term and explain it in English."
            )
        data.setdefault("glossary_before_review", [dict(g) for g in glossary])
        data["glossary"][glossary_index : glossary_index + len(batch)] = reviewed
        data["glossary_review_index"] = glossary_index + len(batch)
        return False
    data["glossary_checked"] = True
    start = data["planning_index"]
    mapping = data["claim_map"]
    if start < len(claims):
        batch = claims[start : start + 24]
        previous = [
            {"id": c["id"], "claim": c["claim"]}
            for c in claims[:start]
            if mapping[c["id"]]["canonical"] == c["id"]
        ]
        result = runtime.ask(
            "Assign each new claim to the most suitable learning goal. A note/page number is NOT a learning topic. "
            "Put definitions before their use and keep technical notation out of an introductory problem chapter. "
            "Mark duplicate_of only when an earlier claim states the SAME fact with the SAME conditions, dataset, metric, scope and qualifications. "
            "A related fact, different experimental setting, or extra limitation is not a duplicate. A duplicate may refer to an earlier new claim in this batch. "
            "Keep every new claim ID exactly once, in the given order. Never omit a claim. "
            'Return {"assignments":[{"claim_id":"ID","goal":0,"duplicate_of":null}]}.\nGOALS: '
            + json.dumps(
                [{"index": i, **g} for i, g in enumerate(data["learning_goals"])]
            )
            + "\nEARLIER DISTINCT CLAIMS: "
            + json.dumps(previous)
            + "\nNEW CLAIMS: "
            + json.dumps(
                [{k: c[k] for k in ("id", "claim", "topic") if k in c} for c in batch]
            ),
            profile=data["model"],
            thinking=False,
            max_tokens=6000,
        )
        assignments = result.get("assignments", [])
        if [a.get("claim_id") for a in assignments] != [c["id"] for c in batch]:
            raise ValueError("The learning map must preserve each claim exactly once.")
        updated = dict(mapping)
        for a in assignments:
            goal = a.get("goal")
            if (
                not isinstance(goal, int)
                or isinstance(goal, bool)
                or not 0 <= goal < len(data["learning_goals"])
            ):
                raise ValueError("A claim was assigned to an unknown learning goal.")
            duplicate = a.get("duplicate_of")
            if duplicate and duplicate not in updated:
                raise ValueError("A duplicate must refer to an earlier known claim.")
            updated[a["claim_id"]] = {
                "goal": updated[duplicate]["goal"] if duplicate else goal,
                "canonical": updated[duplicate]["canonical"]
                if duplicate
                else a["claim_id"],
            }
        data["claim_map"] = updated
        data["planning_index"] = start + len(batch)
        return False
    lookup = {s["id"]: s for s in sources}
    outline = []
    for index, goal in enumerate(data["learning_goals"]):
        distinct = [
            c
            for c in claims
            if mapping[c["id"]]["goal"] == index
            and mapping[c["id"]]["canonical"] == c["id"]
        ]
        chunks = []
        current = []
        refs = set()
        for claim in distinct:
            merged = refs | set(claim["source_ids"])
            images = {lookup[s]["data"].get("image_path") for s in merged} - {None}
            chars = sum(len(lookup[s]["data"]["text"]) for s in merged)
            if current and (len(current) >= 6 or len(images) > 2 or chars > 24000):
                chunks.append(current)
                current = []
                refs = set()
            current.append(claim["id"])
            refs.update(claim["source_ids"])
        if current:
            chunks.append(current)
        for part, ids in enumerate(chunks):
            outline.append(
                goal
                | {
                    "title": goal["title"]
                    + (f" — part {part + 1}" if len(chunks) > 1 else ""),
                    "claim_ids": [
                        c["id"] for c in claims if mapping[c["id"]]["canonical"] in ids
                    ],
                    "evidence_claim_ids": ids,
                    "parts": max(2, math.ceil(len(ids) / 2)),
                }
            )
    data["outline"] = outline
    data["coverage"] = {
        c["id"]: [i for i, p in enumerate(outline) if c["id"] in p["claim_ids"]]
        for c in claims
    }
    if not outline or any(not chapters for chapters in data["coverage"].values()):
        raise ValueError("The concept plan has an uncovered claim.")
    return True
