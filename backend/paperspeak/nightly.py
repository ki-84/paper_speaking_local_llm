"""One resumable nightly paper selection and two-film project, separate from old lessons."""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from zoneinfo import ZoneInfo

import httpx

from . import config, db, lessons, local_network, papers, story, thumbnails
from .runtime import GPUUnavailable, PracticePreempted

ZONE = ZoneInfo("Asia/Tokyo")
ACTIVE = ("searching", "reading", "building", "paused")


def control_project(project_id, state):
    """Cascade user controls only to this project's unfinished outputs."""
    project = db.one("SELECT * FROM video_projects WHERE id=?", (project_id,))
    if not project:
        return
    children = db.all(
        "SELECT id,checkpoint FROM jobs WHERE kind='thumbnail' AND target IN (SELECT id FROM thumbnail_sets WHERE project_id=?) AND state IN ('queued','running','paused','failed','cancelled')",
        (project_id,),
    )
    for track in project["data"]["modes"].values():
        children += db.all(
            "SELECT id,checkpoint FROM jobs WHERE kind='story_video' AND target IN (SELECT id FROM video_exports WHERE lesson_id=?) AND state IN ('queued','running','paused','failed','cancelled')",
            (track["lesson_id"],),
        )
    for child in children:
        cp = child["checkpoint"]
        cp.pop("_failures", None)
        db.patch_job(child["id"], state=state, checkpoint=cp, error=None, available=0)
    db.execute(
        "UPDATE thumbnail_sets SET state=? WHERE project_id=? AND state!='ready'",
        ("building" if state == "queued" else state, project_id),
    )


def control_run(ident, state):
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    if not run:
        return
    run["state"] = {"paused": "paused", "cancelled": "cancelled"}.get(
        state,
        {"production": "building", "read": "reading", "choose": "reading"}.get(
            run["data"]["phase"], "searching"
        ),
    )
    run["data"].pop("reason", None)
    if state == "queued":
        run["data"].pop("finished", None)
        run["data"]["project_retries"] = 0
        run["data"]["thumbnail_retries"] = {}
    if run["project_id"]:
        root = db.one(
            "SELECT id,checkpoint,state FROM jobs WHERE kind='video_project' AND target=? ORDER BY created DESC LIMIT 1",
            (run["project_id"],),
        )
        if root and root["state"] != "completed":
            cp = root["checkpoint"]
            cp.pop("_failures", None)
            db.patch_job(
                root["id"], state=state, checkpoint=cp, error=None, available=0
            )
        control_project(run["project_id"], state)
        if state == "queued":
            project = db.one(
                "SELECT * FROM video_projects WHERE id=?", (run["project_id"],)
            )
            for track in project["data"]["modes"].values():
                track["export_retries"] = 0
            story.save(project)
            db.execute(
                "UPDATE thumbnail_sets SET state='building' WHERE project_id=? AND state='failed'",
                (run["project_id"],),
            )
    save(run)


def save(run):
    db.execute(
        "UPDATE nightly_video_runs SET state=?,project_id=?,data=?,updated=? WHERE id=?",
        (
            run["state"],
            run.get("project_id"),
            db.dumps(run["data"]),
            time.time(),
            run["id"],
        ),
    )
    db.event("nightly_video", {"id": run["id"], "state": run["state"]})


def get(ident):
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (ident,))
    if not run:
        raise ValueError("Nightly run not found")
    run["job"] = db.one(
        "SELECT id,state,stage,progress,error FROM jobs WHERE kind='nightly_video' AND target=? ORDER BY created DESC LIMIT 1",
        (ident,),
    )
    # A skipped date still exposes the previous run that is using the GPU.
    # Otherwise a midnight rollover hides its finished overview and controls.
    if run["state"] == "skipped" and run["data"].get("waiting_reason"):
        previous = db.one(
            "SELECT * FROM nightly_video_runs WHERE id=?",
            (run["data"]["waiting_reason"],),
        )
        if previous and previous.get("project_id"):
            run["project"] = story.get(previous["project_id"])
            run["continuing_run"] = {
                "id": previous["id"],
                "day": previous["day"],
                "data": {"selected": previous["data"].get("selected")},
                "job": db.one(
                    "SELECT id,state,stage,progress,error FROM jobs WHERE kind='nightly_video' AND target=? ORDER BY created DESC LIMIT 1",
                    (previous["id"],),
                ),
            }
            run["data"]["waiting_reason"] = (run["project"].get("job") or {}).get(
                "stage"
            ) or "前日の動画を保存済み工程から作成しています。"
    if run.get("project_id"):
        run["project"] = story.get(run["project_id"])
        if run["state"] == "building":
            root = run["project"].get("job") or {}
            run["data"]["waiting_reason"] = (
                root.get("stage") or "保存済み工程から動画の作成を続けています。"
            )
            if root.get("state") in {"paused", "cancelled", "failed"}:
                run["data"]["waiting_reason"] = (
                    "動画作成が停止しています。続きから再開できます。"
                )
            elif root.get("state") == "completed":
                pending = next(
                    (
                        t.get("thumbnails")
                        for t in run["project"]["data"]["modes"].values()
                        if t.get("thumbnails") and t["thumbnails"]["state"] != "ready"
                    ),
                    None,
                )
                if pending:
                    run["data"]["waiting_reason"] = "サムネイル · " + (
                        (pending.get("job") or {}).get("stage") or pending["state"]
                    )
    # Keep bulky reading notes and raw metadata in the database, not the periodic UI response.
    run["data"] = {
        k: v
        for k, v in run["data"].items()
        if k
        in {
            "phase",
            "started",
            "finished",
            "window_days",
            "selected",
            "reason",
            "warnings",
            "timings",
            "attention_source",
            "paper_count",
            "shortlist_summary",
            "review_summary",
            "waiting_reason",
        }
    }
    return run


def start(*, now=None, manual=False):
    now = now or dt.datetime.now(ZONE)
    day = now.astimezone(ZONE).date().isoformat()
    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute(
            "SELECT id FROM nightly_video_runs WHERE day=?", (day,)
        ).fetchone()
        if old:
            return old["id"]
        active = c.execute(
            "SELECT id FROM nightly_video_runs WHERE state IN ('searching','reading','building','paused') OR (state='failed' AND project_id IS NOT NULL) ORDER BY created LIMIT 1"
        ).fetchone()
        ident, stamp = db.uid(), time.time()
        data = {
            "phase": "attention",
            "started": stamp,
            "window_days": 7,
            "repairs": {},
            "warnings": [],
            "attention": {},
            "candidates": {},
            "collected_categories": [],
            "reading": [],
            "review_index": 0,
            "model": db.settings()["model_profile"],
            "manual": manual,
            "categories": db.settings()["nightly_video_categories"],
            "timings": {},
        }
        if active:
            state = "skipped"
            data.update(
                phase="complete",
                finished=stamp,
                reason="前日の動画が未完了のため、新しい作成を追加しません。",
                waiting_reason=active["id"],
            )
        else:
            state = "searching"
        c.execute(
            "INSERT INTO nightly_video_runs VALUES (?,?,?,?,?,?,?)",
            (ident, day, state, None, db.dumps(data), stamp, stamp),
        )
        if state != "skipped":
            db.queue_job(c, "nightly_video", ident, priority=12)
    db.event("nightly_video", {"id": ident})
    return ident


def schedule(now=None):
    settings = db.settings()
    if not settings["nightly_video_enabled"]:
        return
    now = now or dt.datetime.now(ZONE)
    now = now.astimezone(ZONE)
    if (now.hour, now.minute) >= (
        settings["nightly_video_hour"],
        settings["nightly_video_minute"],
    ):
        start(now=now)


def attention_feed(day):
    url = "https://huggingface.co/api/daily_papers"
    path = config.DATA / "cache" / ("nightly-hf-" + day + ".json")
    if path.is_file():
        return json.loads(path.read_text())
    with httpx.Client(timeout=45, follow_redirects=False) as client:
        response = client.get(url, params={"sort": "trending", "limit": 100})
        response.raise_for_status()
        rows = response.json()
    if not isinstance(rows, list):
        raise ValueError("Unexpected Daily Papers response")
    result = {}
    for rank, row in enumerate(rows):
        paper = row.get("paper", {})
        try:
            ident, _ = papers.parse_reference(paper.get("id", ""))
        except ValueError:
            continue
        result[ident] = {
            "rank": rank + 1,
            "upvotes": max(0, int(paper.get("upvotes", 0))),
            "source_url": "https://huggingface.co/papers/" + ident,
            "retrieved_at": time.time(),
            "published": paper.get("publishedAt"),
            "title": paper.get("title"),
            "api_url": url,
            "signal": "community trending rank",
        }
    path.write_text(db.dumps(result), encoding="utf-8")
    return result


def _tried(run, unit, action, fallback):
    repair = run["data"]["repairs"].setdefault(unit, {"attempts": 0})
    if repair["attempts"] >= 3:
        run["data"]["warnings"].append(
            {
                "unit": unit,
                "reason": repair.get("error"),
                "action": "Continue with the next usable candidate or saved evidence",
            }
        )
        return fallback()
    try:
        return action()
    except (PracticePreempted, GPUUnavailable):
        raise
    except Exception as exc:
        repair.update(attempts=repair["attempts"] + 1, error=str(exc)[:600])
        save(run)
        return None


def category(meta):
    cats = set(meta.get("categories", []))
    if "cs.RO" in cats:
        return "robotics"
    if "cs.CL" in cats:
        return "llm"
    return "ai"


def filmed_ids():
    return {
        r["source_id"]
        for r in db.all(
            "SELECT DISTINCT p.source_id FROM video_projects v JOIN papers p ON p.id=v.paper_id"
        )
    }


def shortlist(candidates, attention, *, now, days, excluded=()):
    used = filmed_ids() | set(excluded)
    oldest = now - dt.timedelta(days=days)
    recent = db.all(
        "SELECT p.data FROM nightly_video_runs n JOIN video_projects v ON n.project_id=v.id JOIN papers p ON p.id=v.paper_id ORDER BY n.created DESC LIMIT 7"
    )
    counts = {
        area: sum(category(r["data"]) == area for r in recent)
        for area in ("ai", "llm", "robotics")
    }
    eligible = []
    for meta in candidates:
        if meta["source_id"] in used:
            continue
        try:
            published = dt.datetime.fromisoformat(
                meta["published"].replace("Z", "+00:00")
            )
        except (ValueError, KeyError):
            continue
        if not oldest <= published <= now:
            continue
        signal = attention.get(meta["source_id"])
        hot = (
            0
            if not signal
            else min(
                30, 20 / math.sqrt(signal["rank"]) + 2 * math.log1p(signal["upvotes"])
            )
        )
        freshness = 15 * (1 - (now - published).total_seconds() / (days * 86400))
        balance = 10 / (1 + counts[category(meta)])
        eligible.append(
            meta
            | {
                "retrieval_score": round(hot + freshness + balance, 3),
                "attention": signal,
                "area": category(meta),
            }
        )
    eligible.sort(key=lambda m: (m["retrieval_score"], m["published"]), reverse=True)
    picks = []
    for area in ("ai", "llm", "robotics"):
        first = next((m for m in eligible if m["area"] == area), None)
        if first:
            picks.append(first)
    picks += [m for m in eligible if m not in picks]
    return sorted(picks[:10], key=lambda m: m["retrieval_score"], reverse=True)


def _sources(pid):
    return papers.reading_sources(pid)


def _ask(run, runtime, prompt, **kwargs):
    with local_network.inference_only():
        return runtime.ask(
            prompt, profile=run["data"]["model"], thinking=False, **kwargs
        )


def _finish_search(run, job):
    data = run["data"]
    data["shortlist"] = shortlist(
        list(data["candidates"].values()),
        data["attention"],
        now=dt.datetime.fromtimestamp(data["started"], dt.timezone.utc),
        days=data["window_days"],
        excluded=data.get("excluded_ids", []),
    )
    data["shortlist_summary"] = [
        {
            "source_id": p["source_id"],
            "title": p["title"],
            "area": p["area"],
            "attention": p["attention"],
        }
        for p in data["shortlist"]
    ]
    data["paper_count"] = len(data["candidates"])
    data.update(phase="read", review_index=0)
    data["timings"]["collection_seconds"] = round(time.time() - data["started"], 2)
    run["state"] = "reading"
    save(run)
    db.patch_job(job["id"], stage="上位候補の本文を確認しています", progress=0.15)


def _expand_or_skip(run):
    data = run["data"]
    if data["window_days"] == 7:
        data.setdefault("reviewed_papers", []).extend(data["reading"])
        data["excluded_ids"] = sorted(
            set(data.get("excluded_ids", []))
            | {
                p["source_id"]
                for p in data.get("shortlist", [])[: data.get("review_index", 0)]
            }
        )
        data.update(
            window_days=30,
            phase="collect",
            collected_categories=[],
            candidates={},
            reading=[],
            review_index=0,
        )
        run["state"] = "searching"
    else:
        data.update(
            phase="complete",
            finished=time.time(),
            reason="本文を確認できる適した未動画化論文がありませんでした。",
        )
        run["state"] = "skipped"
    save(run)
    return run["state"] == "skipped"


def step(job, runtime):
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (job["target"],))
    if run["state"] in {"ready", "skipped"}:
        return True
    data = run["data"]
    phase = data["phase"]
    if phase == "attention":
        result = _tried(
            run, "attention", lambda: attention_feed(run["day"]), lambda: {}
        )
        if result is None:
            return False
        data.update(
            attention=result,
            attention_source={
                "name": "Hugging Face Daily Papers",
                "retrieved_at": time.time(),
                "available": bool(result),
            },
            phase="collect",
        )
    elif phase == "collect":
        pending = [
            cat for cat in data["categories"] if cat not in data["collected_categories"]
        ]
        if pending:
            cat = pending[0]
            now = dt.datetime.fromtimestamp(data["started"], dt.timezone.utc)
            since = (now - dt.timedelta(days=data["window_days"])).strftime(
                "%Y%m%d%H%M"
            )
            until = now.strftime("%Y%m%d%H%M")

            def collect():
                return papers.entries(
                    papers.fetch(
                        "https://export.arxiv.org/api/query",
                        {
                            "search_query": f"cat:{cat} AND submittedDate:[{since} TO {until}]",
                            "sortBy": "submittedDate",
                            "sortOrder": "descending",
                            "max_results": 100,
                        },
                        cache_scope=run["day"],
                        attempts=1,
                        timeout=45,
                    )
                )

            result = _tried(
                run, f"collect:{data['window_days']}:{cat}", collect, lambda: []
            )
            if result is None:
                return False
            data["candidates"].update({p["source_id"]: p for p in result})
            data["collected_categories"].append(cat)
            db.patch_job(
                job["id"], stage=f"新しい論文を探しています · {cat}", progress=0.05
            )
        else:
            _finish_search(run, job)
            return False
    elif phase == "read":
        index = data["review_index"]
        if index >= len(data["shortlist"]) or len(data["reading"]) >= 3:
            data["phase"] = "choose"
        else:
            candidate = data["shortlist"][index]
            pid = candidate.get("paper_id")
            if not pid:
                pid = papers.register(candidate)
                candidate["paper_id"] = pid
                save(run)
            if not candidate.get("ingested"):

                def ingest():
                    papers.ingest(pid, attempts=1)
                    if not _sources(pid):
                        raise ValueError("No full paper text")
                    return True

                result = _tried(run, "ingest:" + pid, ingest, lambda: False)
                if result is None:
                    return False
                if not result:
                    data["review_index"] += 1
                    save(run)
                    return False
                candidate.update(ingested=True, read_index=0, notes=[])
                candidate["read_started"] = time.time()
            groups = lessons.source_groups(_sources(pid))
            group_index = candidate["read_index"]
            if group_index < len(groups):
                group = groups[group_index]

                def read():
                    result = _ask(
                        run,
                        runtime,
                        'Read this complete-paper excerpt. Extract up to six important qualified claims: motivation, mechanism, experiments with comparison conditions, limitations. Keep each claim concise; retain exact source IDs. Return {"claims":[{"claim":"statement","topic":"mechanism|result|limitation|background|equation","source_ids":["ID"]}]}.\n'
                        + lessons.source_context(group),
                        max_tokens=3000,
                    )
                    return story._read_claims(result, {s["id"] for s in group})[
                        "claims"
                    ]

                result = _tried(run, f"read:{pid}:{group_index}", read, lambda: [])
                if result is None:
                    return False
                candidate["notes"].extend(result)
                candidate["read_index"] += 1
                db.patch_job(
                    job["id"],
                    stage=f"本文を読む · {index + 1}候補 · {group_index + 1}/{len(groups)}",
                    progress=0.15 + 0.15 * len(data["reading"]),
                )
            else:
                if candidate["notes"]:
                    known = {s["id"] for s in _sources(pid)}

                    def assess():
                        result = _ask(
                            run,
                            runtime,
                            'Assess this paper for an engaging, accurate overview and deep-dive video. Separate community attention from scientific evidence. Return {"suitable":true,"content_quality":4,"story_value":4,"why_ja":"何が面白く何を学べるか","cautions_ja":["限界"],"source_ids":["existing ID"]}. Scores 0..5. Do not reject complex math: the deep dive explains it.\nTITLE: '
                            + candidate["title"]
                            + "\nFULL PAPER READING NOTES:\n"
                            + db.dumps(candidate["notes"])[:30000],
                            max_tokens=2000,
                        )
                        if (
                            not isinstance(result.get("suitable"), bool)
                            or not isinstance(result.get("why_ja"), str)
                            or not result.get("source_ids")
                            or not set(result["source_ids"]) <= known
                        ):
                            raise ValueError("Assessment needs a source-backed reason")
                        for key in ("content_quality", "story_value"):
                            if (
                                not isinstance(result.get(key), (int, float))
                                or not 0 <= result[key] <= 5
                            ):
                                raise ValueError("Score outside 0..5")
                        return result

                    assessment = _tried(
                        run,
                        "assess:" + pid,
                        assess,
                        lambda: {
                            "suitable": True,
                            "content_quality": 3,
                            "story_value": 3,
                            "why_ja": "本文を確認し、新しさ・注目情報と説明できる発想から選定。",
                            "source_ids": candidate["notes"][0]["source_ids"],
                            "fallback": True,
                        },
                    )
                    if assessment is None:
                        return False
                    data["reading"].append(
                        candidate
                        | {
                            "assessment": assessment,
                            "reading_seconds": round(
                                time.time() - candidate["read_started"], 2
                            ),
                        }
                    )
                data["review_index"] += 1
    elif phase == "choose":
        suitable = [
            p
            for p in data["reading"]
            if p["assessment"]["suitable"] and p["source_id"] not in filmed_ids()
        ]
        data["review_summary"] = [
            {
                "paper_id": p["paper_id"],
                "title": p["title"],
                "assessment": p["assessment"],
                "reading_seconds": p["reading_seconds"],
            }
            for p in [*data.get("reviewed_papers", []), *data["reading"]]
        ]
        if not suitable:
            return _expand_or_skip(run)
        selected = max(
            suitable,
            key=lambda p: 7 * p["assessment"]["content_quality"]
            + 4 * p["assessment"]["story_value"]
            + p["retrieval_score"],
        )
        result = story.create(selected["paper_id"], profile=data["model"])
        run["project_id"] = result["project_id"]
        project = db.one(
            "SELECT * FROM video_projects WHERE id=?", (run["project_id"],)
        )
        project["data"].update(
            evidence=[
                {"id": "C" + str(i + 1), **claim}
                for i, claim in enumerate(selected["notes"])
            ],
            reading_complete=True,
            reading_includes_structured=True,
            nightly_run_id=run["id"],
            reading_reuse="nightly:" + run["id"],
        )
        story.save(project)
        data.update(
            selected={
                k: selected[k]
                for k in (
                    "paper_id",
                    "source_id",
                    "version",
                    "title",
                    "area",
                    "attention",
                    "assessment",
                )
            },
            phase="production",
            production_started=time.time(),
        )
        data["timings"]["selection_seconds"] = round(time.time() - data["started"], 2)
        run["state"] = "building"
    elif phase == "production":
        project = db.one(
            "SELECT * FROM video_projects WHERE id=?", (run["project_id"],)
        )
        root_job = db.one(
            "SELECT * FROM jobs WHERE kind='video_project' AND target=? ORDER BY created DESC LIMIT 1",
            (project["id"],),
        )
        if root_job["state"] in {"failed", "cancelled"}:
            if root_job["state"] == "cancelled":
                run["state"] = "cancelled"
                data.update(
                    reason="この動画プロジェクトは手動で停止されました。",
                    finished=time.time(),
                )
                save(run)
                db.patch_job(job["id"], state="cancelled", stage=data["reason"])
                return True
            rounds = data.get("project_retries", 0)
            if root_job["state"] == "failed" and rounds < 3:
                cp = root_job["checkpoint"] | {"_failures": 0}
                db.patch_job(
                    root_job["id"],
                    state="queued",
                    checkpoint=cp,
                    error=None,
                    available=0,
                )
                data["project_retries"] = rounds + 1
            else:
                run["state"] = "failed"
                data.update(
                    reason=root_job.get("error") or "Project cancelled",
                    finished=time.time(),
                )
                save(run)
                db.patch_job(
                    job["id"],
                    state="failed",
                    error=data["reason"],
                    stage="動画作成の問題を確認してください",
                )
                return True
        ready = project["state"] == "ready"
        for mode, track in project["data"]["modes"].items():
            thumb = thumbnails.get(project["id"], mode)
            if ready and thumb is None:
                thumbnails.enqueue(project, mode)
                ready = False
            elif not thumb or thumb["state"] != "ready":
                ready = False
                if thumb and thumb["state"] == "failed":
                    attempts = data.setdefault("thumbnail_retries", {}).get(mode, 0)
                    if attempts < 3:
                        thumbs_job = db.one(
                            "SELECT id FROM jobs WHERE kind='thumbnail' AND target=? ORDER BY created DESC LIMIT 1",
                            (thumb["id"],),
                        )
                        db.patch_job(
                            thumbs_job["id"],
                            state="queued",
                            checkpoint={},
                            error=None,
                            available=0,
                        )
                        thumb["state"] = "building"
                        thumbnails.save(thumb)
                        data["thumbnail_retries"][mode] = attempts + 1
                    else:
                        run["state"] = "failed"
                        data.update(
                            reason="動画は利用できますが、サムネイルの描画に繰り返し失敗しました。"
                            + thumb["data"].get("error", ""),
                            finished=time.time(),
                        )
                        save(run)
                        db.patch_job(
                            job["id"],
                            state="failed",
                            error=data["reason"],
                            stage="サムネイル作成の問題を確認してください",
                        )
                        return True
        if ready:
            run["state"] = "ready"
            data.update(phase="complete", finished=time.time())
            data["timings"]["production_seconds"] = round(
                time.time() - data["production_started"], 2
            )
            data["timings"]["total_seconds"] = round(time.time() - data["started"], 2)
            data["timings"]["active_stages"] = project["data"].get("stage_seconds", {})
            data.pop("waiting_reason", None)
            save(run)
            db.patch_job(
                job["id"], stage="解説編・詳解編・サムネイルが完成しました", progress=1
            )
            return True
        data["waiting_reason"] = (
            "時間で打ち切らず、保存済み工程から作成を続けています。"
        )
        db.patch_job(
            job["id"],
            stage=root_job["stage"],
            progress=0.55 + 0.4 * root_job["progress"],
            available=time.time() + 20,
        )
    else:
        raise ValueError("Unknown nightly stage")
    save(run)
    return False
