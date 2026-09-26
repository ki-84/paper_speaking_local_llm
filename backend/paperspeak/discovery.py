from __future__ import annotations

import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

from . import db, lessons, papers
from .quality import english_only
from .runtime import ModelBudgetError


def learning_history(limit=20):
    return db.all(
        """SELECT p.title,p.data FROM papers p WHERE
        EXISTS(SELECT 1 FROM recommendations r WHERE r.paper_id=p.id AND r.feedback='read')
        OR EXISTS(SELECT 1 FROM lessons l WHERE l.paper_id=p.id AND (
          l.state='ready'
          OR EXISTS(SELECT 1 FROM attempts a WHERE a.lesson_id=l.id AND a.state='ready')
          OR EXISTS(SELECT 1 FROM cursors c JOIN chapters ch
            ON ch.id=json_extract(c.data,'$.chapter_id')
            WHERE c.key='lesson:'||l.id AND ch.lesson_id=l.id AND ch.state='ready'
            AND json_extract(c.data,'$.turn_index')>0)))
        ORDER BY p.updated DESC LIMIT ?""",
        (limit,),
    )


def shortlist(candidates, settings, limit=10):
    feedback = db.all(
        "SELECT r.feedback,p.data FROM recommendations r JOIN papers p ON p.id=r.paper_id WHERE r.feedback IS NOT NULL"
    )
    liked = set()
    disliked = set()
    tokens = lambda text: set(re.findall(r"[a-z]{5,}", text.lower()))
    stop = {
        "learning",
        "using",
        "model",
        "models",
        "paper",
        "based",
        "method",
        "approach",
        "results",
        "propose",
        "neural",
    }
    for f in feedback:
        if f["feedback"] not in {"interested", "not_interested"}:
            continue
        target = liked if f["feedback"] == "interested" else disliked
        target.update(tokens(f["data"].get("title", "")) - stop)
    interest = tokens(settings["interests"]) - stop
    learned = learning_history()
    learned_words = [
        tokens(p["title"] + " " + p["data"].get("abstract", "")) - stop for p in learned
    ]
    scored = []
    for c in candidates:
        text = c["title"] + " " + c["abstract"]
        words = tokens(text)
        # This is only candidate retrieval; scientific quality is assessed from the paper.
        score = (
            2 * len(words & interest)
            + 3 * len(words & liked)
            - 4 * len(words & disliked)
        )
        score -= 12 * max(
            (len(words & old) / max(1, len(words | old)) for old in learned_words),
            default=0,
        )
        if len(c["abstract"]) < 300:
            score -= 5
        scored.append((score, c))
    scored.sort(key=lambda x: (x[0], x[1].get("published", "")), reverse=True)
    result = []
    used = set()
    counts = {}
    # Reserve one place for each configured category, then fill with a diversity penalty.
    for cat in settings["categories"]:
        found = next(
            (
                c
                for _, c in scored
                if cat in c["categories"] and c["source_id"] not in used
            ),
            None,
        )
        if found:
            result.append(found)
            used.add(found["source_id"])
            counts[found["categories"][0]] = counts.get(found["categories"][0], 0) + 1
    while len(result) < limit:
        eligible = [
            (s - 3 * counts.get(c["categories"][0], 0), c)
            for s, c in scored
            if c["source_id"] not in used
        ]
        if not eligible:
            break
        c = max(eligible, key=lambda x: x[0])[1]
        result.append(c)
        used.add(c["source_id"])
        counts[c["categories"][0]] = counts.get(c["categories"][0], 0) + 1
    return result[:limit]


def explanation_issue(pending, issues, draft):
    """Repair one candidate without making the whole daily batch fail."""
    updated = pending["data"]
    updated["presentation_issues"] = issues
    updated["previous_presentation"] = draft
    updated["presentation_revisions"] = updated.get("presentation_revisions", 0) + 1
    updated["presentation_blocked"] = updated["presentation_revisions"] >= 3
    updated.pop("presentation_draft", None)
    db.execute(
        "UPDATE recommendations SET data=?,state=? WHERE id=?",
        (
            db.dumps(updated),
            "explanation_failed"
            if updated["presentation_blocked"]
            else "needs_explanation",
            pending["id"],
        ),
    )


def discovery_step(job, runtime):
    settings = db.settings()
    cp = job["checkpoint"]
    if "phase" not in cp:
        now = dt.datetime.now(dt.timezone.utc)
        last = settings.get("discovery_last_success")
        since = (
            dt.datetime.fromtimestamp(last, dt.timezone.utc) - dt.timedelta(days=1)
            if last
            else now - dt.timedelta(days=30)
        )
        cp = {
            "phase": "collect",
            "category_index": 0,
            "start": 0,
            "since": since.strftime("%Y%m%d%H%M"),
            "until": now.strftime("%Y%m%d%H%M"),
            "started": now.timestamp(),
            "collection_mode": "updates" if last else "new",
            "candidates": {},
            "day": now.astimezone(ZoneInfo(settings["timezone"])).date().isoformat(),
        }
    cp.setdefault("model", settings["model_profile"])
    cp.setdefault("explanation_model", settings["model_profile"])
    phase = cp["phase"]

    def checkpoint():
        db.patch_job(job["id"], checkpoint=cp)

    if phase == "collect":
        i = cp["category_index"]
        if i >= len(settings["categories"]):
            values = list(cp["candidates"].values())
            candidates = shortlist(values, settings)
            cp["selected"] = [papers.register(c) for c in candidates]
            cp["phase"] = "review"
            cp["review_index"] = 0
            cp["summaries"] = []
            cp["group_index"] = 0
            cp["collected_count"] = len(values)
            cp.pop("candidates", None)
            # Metadata collection is short network work. Full-paper reading yields
            # to manual lessons and recordings once that collection is saved.
            db.patch_job(
                job["id"],
                priority=30,
                checkpoint=cp,
                stage=f"Found {len(values):,} papers; ready to read {len(candidates)} candidates",
            )
            return False
        cat = settings["categories"][i]
        page_size = 500
        updates = cp.get("collection_mode") == "updates"
        db.patch_job(
            job["id"],
            stage=f"Finding papers in {cat}",
            progress=0.05 + 0.25 * i / len(settings["categories"]),
        )
        raw = papers.fetch(
            "https://export.arxiv.org/api/query",
            {
                "search_query": f"cat:{cat}"
                if updates
                else f"cat:{cat} AND submittedDate:[{cp['since']} TO {cp['until']}]",
                "start": cp["start"],
                "max_results": page_size,
                "sortBy": "lastUpdatedDate" if updates else "submittedDate",
                "sortOrder": "descending",
            },
            cache_scope=cp["until"] if updates else "",
        )
        items = papers.entries(raw)
        reached_old = False
        for item in items:
            if updates:
                stamp = dt.datetime.fromisoformat(
                    item["updated"].replace("Z", "+00:00")
                ).strftime("%Y%m%d%H%M")
                if stamp < cp["since"]:
                    reached_old = True
                    continue
                if stamp > cp["until"]:
                    continue
            if not db.one(
                "SELECT id FROM papers WHERE source_id=? AND version=?",
                (item["source_id"], item["version"]),
            ):
                cp["candidates"][item["source_id"]] = item
        if len(items) < page_size or reached_old:
            cp["category_index"] += 1
            cp["start"] = 0
        else:
            cp["start"] += page_size
        checkpoint()
        return False
    if phase == "review":
        pending = db.one(
            """SELECT r.*,p.title FROM recommendations r JOIN papers p ON p.id=r.paper_id
            WHERE r.day=? AND json_extract(r.data,'$.full_text_read')=1
            AND coalesce(json_extract(r.data,'$.easy_english'),0)=0
            AND coalesce(json_extract(r.data,'$.presentation_blocked'),0)=0 ORDER BY r.rowid LIMIT 1""",
            (cp["day"],),
        )
        if pending:
            db.patch_job(job["id"], stage="Explaining why this paper is worth reading")
            assessment = pending["data"].get("assessment") or {
                k: v
                for k, v in pending["data"].items()
                if k not in {"reading_notes", "selection_notes"}
            }
            draft = pending["data"].get("presentation_draft")
            if draft:
                check = runtime.ask(
                    "Check this plain-English recommendation against the detailed scientific assessment. Identify changed meaning, changed scope or unsupported judgments. "
                    "The explanation may omit secondary details and numbers; do not demand every detail or exact wording. "
                    "A correct example from one task is allowed even if the paper also studies other tasks; only flag scope when the wording incorrectly claims exclusivity or generality. "
                    "A condition applying to one main test must not be described as applying to all tests, especially when held-out tests also exist. "
                    "Shared practice and test questions do not by themselves make a reported score false. Evidence of a narrow answer pattern does not justify a blanket claim that a model cannot understand. "
                    "A possibility or task-specific finding must not become a universal benefit or harm. Check that terms are explained in everyday words. "
                    'Return {"passed":true,"issues":["specific changed meaning or unclear term, with a correction"]}.\nASSESSMENT: '
                    + json.dumps(assessment)
                    + "\nPLAIN EXPLANATION: "
                    + json.dumps(draft),
                    profile=cp["explanation_model"],
                    max_tokens=4500,
                )
                updated = pending["data"]
                updated["presentation_review"] = check
                if check.get("passed") is True and not check.get("issues"):
                    updated.update(
                        draft,
                        easy_english=True,
                        explanation_model=cp["explanation_model"],
                    )
                    state = updated.pop("intended_state", pending["state"])
                    if pending["state"] == "selected":
                        state = "selected"
                else:
                    explanation_issue(
                        pending,
                        check.get("issues") or ["The meaning check did not pass."],
                        draft,
                    )
                    checkpoint()
                    db.event("recommendations", {})
                    return False
                updated.pop("presentation_draft", None)
                db.execute(
                    "UPDATE recommendations SET data=?,state=? WHERE id=?",
                    (db.dumps(updated), state, pending["id"]),
                )
                checkpoint()
                db.event("recommendations", {})
                return False
            simple = runtime.ask(
                "Explain this assessed paper to a beginner in everyday A2 English. The detailed assessment is evidence, not wording to copy. "
                "For why and learn, use one or two short sentences each, at most 40 words per field. Give at most four important cautions, each one or two short sentences and at most 40 words. "
                "Explain the distinctive learning idea concretely, not merely that the paper tests a method or improves answers. Explain labels as correct answers supplied by people if that term is necessary. "
                "Every sentence should normally have 8-16 words. Use no unexplained abbreviations or lists of model names, benchmarks or metrics. Avoid unnecessary numbers. "
                "Say answers the model picks for itself instead of pseudo-labels; a small set of model numbers instead of bias subspace; several tries instead of rollouts. Explain the idea without requiring the reader to know reinforcement learning. "
                "Changing a small set of numbers does not establish that each number changes only slightly; do not add that claim. "
                "Avoid words such as ablation, multimodal, confound and protocol; express their meaning in ordinary words. "
                "Preserve the main idea and the most important limits: shared training/test questions, different comparison conditions, narrow tests or confounding causes of improvement when present. Never turn a qualified result into a universal claim. "
                "Do not claim that scores are falsely high merely because practice and test questions overlap. Do not make blanket claims about what a model truly understands. Preserve the distinction between a main shared test set and any separate tests on new questions. "
                "If a previous plain explanation and review issues are supplied, edit that explanation to fix each issue and retain its already accurate parts. Do not replace one unsupported judgment with another such as 'not better thinking'. State the observed cause positively, and qualify its task scope. "
                'Return {"why":"what is interesting","learn":"what you can learn","cautions":["main limit"],"source_ids":["existing evidence ID"]}.\nTITLE: '
                + pending["title"]
                + "\nASSESSMENT: "
                + json.dumps(assessment)
                + "\nPREVIOUS MEANING ISSUES: "
                + json.dumps(pending["data"].get("presentation_issues", []))
                + "\nPREVIOUS PLAIN EXPLANATION: "
                + json.dumps(pending["data"].get("previous_presentation")),
                profile=cp["explanation_model"],
                max_tokens=4500,
            )
            issues = []
            cautions = simple.get("cautions")
            ids = simple.get("source_ids")
            if not isinstance(cautions, list) or not 1 <= len(cautions) <= 4:
                issues.append("Supply one to four cautions as a list of short strings.")
                cautions = []
            fields = {"why": simple.get("why"), "learn": simple.get("learn")}
            fields.update({f"caution {i + 1}": v for i, v in enumerate(cautions)})
            for name, value in fields.items():
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or not english_only(value)
                ):
                    issues.append(f"{name} needs a nonempty English explanation.")
                elif len(value.split()) > 40:
                    issues.append(
                        f"{name} has {len(value.split())} words. Use at most 40 words in one or two short sentences; retain its main meaning."
                    )
            if (
                not isinstance(ids, list)
                or not ids
                or any(not isinstance(s, str) for s in ids)
                or not set(ids) <= set(assessment["source_ids"])
            ):
                issues.append(
                    "Use only source IDs supplied in the scientific assessment."
                )
            if issues:
                explanation_issue(pending, issues, simple)
                checkpoint()
                db.event("recommendations", {})
                return False
            updated = pending["data"] | {
                "assessment": assessment,
                "presentation_draft": simple,
                "easy_english": False,
                "explanation_model": cp["explanation_model"],
            }
            db.execute(
                "UPDATE recommendations SET data=? WHERE id=?",
                (db.dumps(updated), pending["id"]),
            )
            checkpoint()
            db.event("recommendations", {})
            return False
        i = cp["review_index"]
        if i >= len(cp["selected"]):
            cp["phase"] = "choose"
            checkpoint()
            return False
        pid = cp["selected"][i]
        db.patch_job(
            job["id"],
            stage=f"Reading candidate {i + 1} of {len(cp['selected'])}",
            progress=0.3 + 0.6 * i / max(1, len(cp["selected"])),
        )
        try:
            paper = papers.ingest(pid)
        except Exception as e:
            cp.setdefault("unavailable", []).append(
                {"paper_id": pid, "reason": str(e)[:300]}
            )
            cp["review_index"] += 1
            cp["summaries"] = []
            cp["group_index"] = 0
            checkpoint()
            return False
        sources = db.all(
            "SELECT * FROM sources WHERE paper_id=? ORDER BY rowid", (pid,)
        )
        # Read the complete extracted text, preferring structured HTML, then all PDF pages.
        structured = [s for s in sources if s["kind"] != "page"]
        primary = (
            structured
            if sum(len(s["data"]["text"]) for s in structured) > 2000
            else sources
        )
        groups = lessons.source_groups(primary, max_chars=25000)
        gi = cp["group_index"]
        if gi < len(groups):
            group = groups[gi]
            result = runtime.ask(
                "Read this part of a candidate AI paper. Record the central idea, evidence, experimental or theoretical support, comparison conditions, reproducibility details and limitations. "
                "This is for choosing useful learning material across AI, not for endorsing claims. Use source IDs and short exact quotes for important observations. "
                "Return at most eight findings. Each point is one sentence and each quote is at most 25 words; retain experimental conditions and qualifications. "
                'Return {"findings":[{"point":"...","source_ids":["ID"],"quote":"..."}],"concerns":["..."],"learning_value":"..."}.\n'
                + lessons.source_context(group),
                profile=cp["model"],
                max_tokens=7000,
            )
            lookup = {s["id"]: s["data"]["text"] for s in group}
            for finding in result.get("findings", []):
                refs = finding.get("source_ids", [])
                if not refs or not set(refs) <= lookup.keys():
                    raise ValueError("Candidate reading contains an unknown source")
                norm = lambda text: re.sub(r"\s+", " ", text).strip().casefold()
                quote = norm(finding.get("quote", ""))
                if quote and not any(quote in norm(lookup[r]) for r in refs):
                    finding["quote"] = ""
            cp["summaries"].append(result)
            cp["group_index"] += 1
            checkpoint()
            return False
        if len(json.dumps(cp["summaries"])) > 45000:
            db.patch_job(job["id"], stage=f"Combining notes for candidate {i + 1}")
            cp.setdefault("raw_reading_notes", list(cp["summaries"]))
            size = cp.get("rollup_size", 4)
            portion = cp["summaries"][:size]
            try:
                rollup = runtime.ask(
                    'Combine these full-text reading notes for a long paper. Consolidate related findings instead of copying every finding separately. Return at most 12 findings, each at most 35 words, at most six short concerns, and one short learning_value paragraph. Preserve the mechanism, quality of support, comparison conditions, reproducibility details, limitations, and important caveats. Keep existing source IDs. Do not invent quotes. Return {"findings":[{"point":"...","source_ids":["ID"]}],"concerns":["..."],"learning_value":"..."}.\n'
                    + json.dumps(portion),
                    profile=cp["model"],
                    thinking=False,
                    max_tokens=6000,
                )
            except ModelBudgetError as error:
                cp.setdefault("budget_adjustments", []).append(
                    {
                        "paper_id": pid,
                        "stage": "rollup",
                        "size": size,
                        "reason": str(error),
                    }
                )
                if size > 2:
                    cp["rollup_size"] = 2
                else:
                    # Incomplete notes must never become a recommendation, but
                    # one difficult candidate must not stop the whole day.
                    cp.setdefault("unavailable", []).append(
                        {
                            "paper_id": pid,
                            "reason": "Complete reading notes could not be combined within the model budget.",
                        }
                    )
                    cp["review_index"] += 1
                    cp["group_index"] = 0
                    cp["summaries"] = []
                    cp.pop("raw_reading_notes", None)
                    cp.pop("rollup_size", None)
                checkpoint()
                return False
            known = {
                sid
                for note in portion
                for f in note.get("findings", [])
                for sid in f.get("source_ids", [])
            }
            if any(
                not f.get("source_ids") or not set(f["source_ids"]) <= known
                for f in rollup.get("findings", [])
            ):
                raise ValueError("Combined notes have an unknown source")
            if not rollup.get("findings") or len(json.dumps(rollup)) >= len(
                json.dumps(portion)
            ):
                raise ValueError("Long-paper notes could not be reduced safely")
            cp["summaries"] = [rollup] + cp["summaries"][size:]
            checkpoint()
            return False
        recent_learning = learning_history(12)
        result = runtime.ask(
            "Assess this paper as learning material using the full-text reading notes. Ignore fame, hype and author identity. "
            "Score important learnable ideas, strength of evidence or theory, clear comparison conditions, reproducibility information, and interesting mechanisms from 0 to 4 each. "
            "A theoretical paper need not have empirical experiments. Flag unsupported claims. Recommend only if there is a coherent, well-supported idea worth learning. "
            "Compare with prior learning: prefer a new mechanism or a useful deeper angle, and flag substantial repetition in cautions. Write reasons in simple English. Cite source IDs from the notes. "
            'Return {"suitable":true,"scores":{"ideas":0,"support":0,"comparisons":0,"reproducibility":0,"mechanism":0},"why":"...","learn":"...","cautions":["..."],"source_ids":["ID"]}.\n'
            + "TITLE: "
            + paper["title"]
            + "\nPRIOR LEARNING: "
            + json.dumps(
                [
                    {
                        "title": p["title"],
                        "abstract": p["data"].get("abstract", "")[:700],
                    }
                    for p in recent_learning
                ]
            )
            + "\nNOTES: "
            + json.dumps(cp["summaries"]),
            profile=cp["model"],
            max_tokens=4500,
        )
        valid = {s["id"] for s in sources}
        if not result.get("source_ids") or not set(result["source_ids"]) <= valid:
            raise ValueError("Recommendation did not cite valid paper evidence")
        result["reading_notes"] = cp.pop("raw_reading_notes", cp["summaries"])
        result["selection_notes"] = cp["summaries"]
        result["model"] = cp["model"]
        result["full_text_read"] = True
        scores = result.get("scores", {})
        result["total"] = sum(
            max(0, min(4, int(scores.get(k, 0))))
            for k in ("ideas", "support", "comparisons", "reproducibility", "mechanism")
        )
        state = (
            "recommended"
            if result.get("suitable") is True
            and int(scores.get("support", 0)) >= 2
            and result["total"] >= 12
            else "passed_over"
        )
        result["intended_state"] = state
        db.execute(
            "INSERT INTO recommendations(id,paper_id,day,state,data,feedback) VALUES (?,?,?,?,?,NULL) ON CONFLICT(paper_id,day) DO UPDATE SET data=excluded.data,state=CASE WHEN recommendations.state='selected' THEN recommendations.state ELSE excluded.state END",
            (db.uid(), pid, cp["day"], "needs_explanation", db.dumps(result)),
        )
        cp["review_index"] += 1
        cp["group_index"] = 0
        cp["summaries"] = []
        cp.pop("rollup_size", None)
        checkpoint()
        db.event("recommendations", {})
        return False
    if phase == "choose":
        recs = db.all(
            "SELECT * FROM recommendations WHERE day=? AND state='recommended'",
            (cp["day"],),
        )
        ready_count = db.one(
            "SELECT count(*) AS n FROM recommendations WHERE day=? AND state='selected'",
            (cp["day"],),
        )["n"]
        backlog = db.one(
            "SELECT count(*) AS n FROM jobs WHERE kind='lesson' AND state IN ('queued','running','paused')"
        )["n"]
        available = max(
            0,
            min(
                settings["daily_limit"] - ready_count,
                settings["max_auto_backlog"] - backlog,
            ),
        )
        # Discourage consecutive lessons in the same area.
        recent = db.all(
            "SELECT p.data FROM lessons l JOIN papers p ON p.id=l.paper_id ORDER BY l.created DESC LIMIT 5"
        )

        def rank(rec):
            p = db.one("SELECT * FROM papers WHERE id=?", (rec["paper_id"],))
            cats = set(p["data"].get("categories", []))
            penalty = sum(
                bool(cats & set(x["data"].get("categories", []))) for x in recent
            )
            return rec["data"]["total"] - penalty

        for rec in sorted(recs, key=rank, reverse=True)[:available]:
            lid = lessons.create(rec["paper_id"])
            db.enqueue("lesson", lid, priority=20)
            db.execute(
                "UPDATE recommendations SET state='selected' WHERE id=?", (rec["id"],)
            )
        db.set_setting("discovery_last_success", cp["started"])
        db.patch_job(
            job["id"],
            stage=f"Read {len(cp['selected'])} candidates; selected up to {available}",
            progress=1,
        )
        return True
    raise ValueError("Invalid discovery stage")


def schedule():
    settings = db.settings()
    if not settings["discovery_enabled"]:
        return
    if db.one(
        "SELECT id FROM jobs WHERE kind='discover' AND state IN ('queued','running','paused')"
    ):
        return
    now = dt.datetime.now(ZoneInfo(settings["timezone"]))
    if (now.hour, now.minute) < (
        settings["schedule_hour"],
        settings["schedule_minute"],
    ):
        return
    day = now.date().isoformat()
    if db.one(
        "SELECT id FROM jobs WHERE kind='discover' AND (target=? OR json_extract(checkpoint,'$.day')=?)",
        (day, day),
    ):
        return
    db.enqueue("discover", day, priority=8)
