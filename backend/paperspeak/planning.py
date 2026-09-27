"""Concept-based planning with a small, non-repeating teaching path."""

import json

from .quality import english_only

PLANNING_VERSION = 3
MAX_TEACHING_CLAIMS = 8


def distinct_for_goal(claims, mapping, index):
    return [c for c in claims if mapping[c["id"]]["goal"] == index
            and mapping[c["id"]]["canonical"] == c["id"]]


def editorial_selection(data, claims, runtime):
    """Choose facts worth saying aloud; retain the rest as reference material."""
    mapping = data["claim_map"]
    selected = data.setdefault("teaching_claims", {})
    for index, goal in enumerate(data["learning_goals"]):
        distinct = distinct_for_goal(claims, mapping, index)
        if not distinct or str(index) in selected:
            continue
        if len(distinct) <= MAX_TEACHING_CLAIMS:
            selected[str(index)] = [c["id"] for c in distinct]
            continue
        result = runtime.ask(
            "Edit a beginner's spoken course about this paper. Pick four to eight indispensable facts for this one goal. "
            "Prefer the central mechanism, a representative result WITH its model, dataset, metric, comparator and conditions, and a real limitation. "
            "Do not narrate every table cell, dataset size, learning rate, appendix setting or repeated result; those remain in the paper for reference. "
            "Retain at least one fact from each available type: mechanism, result, limitation, equation. "
            "Keep the original IDs, in their original order. Return {\"claim_ids\":[\"ID\"]}.\nGOAL: "
            + json.dumps(goal) + "\nFACTS: "
            + json.dumps([{k: c[k] for k in ("id", "claim", "topic") if k in c} for c in distinct]),
            profile=data["model"], thinking=False, max_tokens=1800,
        )
        ids = result.get("claim_ids")
        known = [c["id"] for c in distinct]
        required_topics = {c.get("topic") for c in distinct} & {"mechanism", "result", "limitation", "equation"}
        kept_topics = {c.get("topic") for c in distinct if isinstance(ids, list) and c["id"] in ids}
        if (not isinstance(ids, list) or not all(isinstance(cid, str) for cid in ids)
            or not 4 <= len(ids) <= MAX_TEACHING_CLAIMS
            or len(set(ids)) != len(ids) or ids != [cid for cid in known if cid in ids]
            or not required_topics <= kept_topics):
            mandatory = []
            for topic in ("mechanism", "result", "limitation", "equation"):
                if topic in required_topics:
                    mandatory.append(next(c["id"] for c in distinct if c.get("topic") == topic))
            chosen = set(mandatory[:MAX_TEACHING_CLAIMS])
            for cid in known:
                if len(chosen) >= MAX_TEACHING_CLAIMS:
                    break
                chosen.add(cid)
            ids = [cid for cid in known if cid in chosen]
            data.setdefault("editorial_fallbacks", {})[str(index)] = "The local model returned an invalid selection; representative source claims were selected deterministically."
        selected[str(index)] = ids
        return False  # Persist one expensive local-model decision at a time.
    return True


def compact_fallback_groups(data, active):
    """Merge only adjacent small goals when an LLM grouping is malformed."""
    selected = data["teaching_claims"]
    groups = []
    for index in active:
        count = len(selected[str(index)])
        current = groups[-1] if groups else []
        current_count = sum(len(selected[str(i)]) for i in current)
        if (current and len(current) < 3 and current_count + count <= 8
            and (count <= 2 or len(selected[str(current[-1])]) <= 2)):
            current.append(index)
        else:
            groups.append([index])
    result = []
    for indices in groups:
        dominant = max(indices, key=lambda i: len(selected[str(i)]))
        goals = [data["learning_goals"][i] for i in indices]
        focus_parts = [goal["focus"].removeprefix("Learn ").rstrip(".") for goal in goals]
        result.append({"goals": indices, "title": data["learning_goals"][dominant]["title"],
                       "focus": "Learn " + ", and ".join(focus_parts) + "."})
    return result


def chapter_groups(data, claims, runtime):
    if data.get("chapter_groups"):
        return True
    selected = data["teaching_claims"]
    active = [i for i in range(len(data["learning_goals"])) if selected.get(str(i))]
    if len(active) <= 1 or (len(active) == 2 and sum(len(selected[str(i)]) for i in active) <= 2):
        data["chapter_groups"] = [
            {"goals": [i], **data["learning_goals"][i]} for i in active
        ]
        return True
    lookup = {c["id"]: c for c in claims}
    result = runtime.ask(
        "Make a concise, engaging chapter sequence from the ordered goals below. Combine adjacent goals only when they form one coherent new idea. "
        "Every goal index must appear exactly once, in order. Each chapter may contain one to three adjacent goals and at most ten teaching facts. "
        "Do not split one goal into many chapters or repeat the same chapter focus. Give every chapter a distinct short English title and one distinct sentence starting with Learn. "
        "Separate the main mechanism, experimental evidence, and limits where their explanations need different figures. "
        'Return {"chapters":[{"goals":[0,1],"title":"short title","focus":"Learn ..."}]}.\nGOALS: '
        + json.dumps([{"index": i, **data["learning_goals"][i],
                       "facts": [lookup[cid]["claim"] for cid in selected[str(i)]]}
                      for i in active]),
        profile=data["model"], thinking=False, max_tokens=5000,
    )
    groups = result.get("chapters")
    valid = isinstance(groups, list) and bool(groups)
    flat = []
    titles, focuses = set(), set()
    if valid:
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("goals"), list):
                valid = False; break
            goal_ids = group["goals"]
            title, focus = group.get("title"), group.get("focus")
            if (not 1 <= len(goal_ids) <= 3 or any(not isinstance(i, int) or isinstance(i, bool) for i in goal_ids)
                or sum(len(selected.get(str(i), [])) for i in goal_ids) > 10
                or not isinstance(title, str) or not isinstance(focus, str)
                or not title.strip() or not focus.startswith("Learn ")
                or not english_only(title + focus)
                or title.casefold() in titles or focus.casefold() in focuses):
                valid = False; break
            titles.add(title.casefold()); focuses.add(focus.casefold())
            flat.extend(goal_ids)
    if not valid or flat != active:
        groups = compact_fallback_groups(data, active)
        data["grouping_fallback"] = "The local model returned an invalid chapter grouping; adjacent small goals were grouped deterministically."
        data["grouping_rejected"] = result
    data["chapter_groups"] = groups
    return False  # Checkpoint the chapter design before materializing it.


def plan_step(data, sources, runtime):
    claims = [c for note in data["notes"] for c in note["claims"]]
    if not claims:
        raise ValueError("No supported teaching claims were found in this paper.")
    if data.get("planning_version") != PLANNING_VERSION:
        data.update(planning_version=PLANNING_VERSION, outline=[], claim_map={}, planning_index=0)
        data.pop("learning_goals", None)
        data.pop("teaching_claims", None)
        data.pop("chapter_groups", None)
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
    if not editorial_selection(data, claims, runtime):
        return False
    if not chapter_groups(data, claims, runtime):
        return False
    outline = []
    for group in data["chapter_groups"]:
        goals = set(group["goals"])
        ids = [cid for index in group["goals"] for cid in data["teaching_claims"][str(index)]]
        outline.append({
            "title": group["title"], "focus": group["focus"],
            "goal_indices": group["goals"],
            "claim_ids": [c["id"] for c in claims if mapping[c["id"]]["goal"] in goals],
            "evidence_claim_ids": ids,
            "supporting_claim_ids": [c["id"] for c in claims if mapping[c["id"]]["goal"] in goals
                                     and mapping[c["id"]]["canonical"] == c["id"] and c["id"] not in ids],
            "parts": 1,
        })
    data["outline"] = outline
    data["coverage"] = {
        c["id"]: [i for i, p in enumerate(outline) if c["id"] in p["claim_ids"]]
        for c in claims
    }
    if not outline or any(not chapters for chapters in data["coverage"].values()):
        raise ValueError("The concept plan has an uncovered claim.")
    return True
