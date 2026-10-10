"""Study dashboard and explicit course progress, independent of generation jobs."""

import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import db, repetition


def save_position(ident, values):
    old = db.one("SELECT data FROM cursors WHERE key=?", ("lesson:" + ident,))
    data = (old or {}).get("data", {}) | values
    data.setdefault("started_at", time.time())
    data["last_studied_at"] = time.time()
    if data.get("study_state") == "paused":
        data["study_state"] = "active"
    data.setdefault("study_state", "active")
    db.execute(
        "INSERT INTO cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
        ("lesson:" + ident, db.dumps(data)),
    )
    return data


def mark_chapter(lesson_id, chapter_id, completed=True):
    chapter = db.one(
        "SELECT * FROM chapters WHERE id=? AND lesson_id=?", (chapter_id, lesson_id)
    )
    if not chapter or chapter["state"] != "ready":
        raise ValueError("Choose a finished chapter")
    old = db.one("SELECT data FROM cursors WHERE key=?", ("lesson:" + lesson_id,))
    data = (old or {}).get("data", {})
    done = set(data.get("completed_chapters", []))
    if completed:
        done.add(chapter_id)
    else:
        done.discard(chapter_id)
    data.update(completed_chapters=sorted(done), study_state="active")
    save_position(lesson_id, data)
    if completed:
        for question in chapter["data"].get("questions", []):
            repetition.enroll(lesson_id, chapter_id, question_id=question["id"])
    db.event("study", {"lesson_id": lesson_id})
    return {"ok": True, "completed_chapters": sorted(done)}


def course_state(ident, state):
    if not db.one("SELECT id FROM lessons WHERE id=?", (ident,)):
        raise ValueError("Course not found")
    old = db.one("SELECT data FROM cursors WHERE key=?", ("lesson:" + ident,))
    data = (old or {}).get("data", {}) | {"study_state": state}
    data.setdefault("started_at", time.time())
    db.execute(
        "INSERT INTO cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
        ("lesson:" + ident, db.dumps(data)),
    )
    db.event("study", {"lesson_id": ident})
    return {"ok": True}


def dashboard(now=None):
    now = time.time() if now is None else now
    lessons = db.all(
        "SELECT * FROM lessons WHERE coalesce(json_extract(data,'$.archived'),0)=0 AND coalesce(json_extract(data,'$.learning_enabled'),1)=1 ORDER BY created DESC"
    )
    cursors = {
        r["key"][7:]: r["data"]
        for r in db.all("SELECT * FROM cursors WHERE key LIKE 'lesson:%'")
    }
    attempts = {
        r["lesson_id"]: r["last"]
        for r in db.all(
            "SELECT lesson_id,MAX(created) AS last FROM attempts GROUP BY lesson_id"
        )
    }
    chapter_rows = db.all("""SELECT id,lesson_id,ordinal,state,json_extract(data,'$.title') AS title,
      json_array_length(data,'$.turns') AS sentence_count FROM chapters ORDER BY ordinal""")
    chapters = {}
    for chapter in chapter_rows:
        chapters.setdefault(chapter["lesson_id"], []).append(chapter)
    projects = {
        p["id"]: p | {"tracks": {}}
        for p in db.all(
            "SELECT id,paper_id,state,json_extract(data,'$.paper_title') AS title FROM video_projects ORDER BY created DESC"
        )
    }
    for t in db.all("""SELECT p.id,j.key AS mode,json_extract(j.value,'$.lesson_id') AS lesson_id,
       json_array_length(j.value,'$.scenes') AS scene_count
       FROM video_projects p,json_each(p.data,'$.modes') j"""):
        projects[t["id"]]["tracks"][t["mode"]] = t
    due = repetition.due_cards(now, limit=3)
    schedules = db.all("SELECT r.lesson_id,r.due " + repetition.AVAILABLE)
    due_by_lesson = Counter(r["lesson_id"] for r in schedules if r["due"] <= now)
    active, resumable = [], []
    available_versions = set()
    for l in lessons:
        lid = l["id"]
        progress = cursors.get(lid, {})
        ready = [
            c
            for c in chapters.get(lid, [])
            if c["state"] == "ready" and c["sentence_count"]
        ]
        if not ready:
            continue
        track = (
            projects.get(l["data"].get("project_id"), {})
            .get("tracks", {})
            .get(l["data"].get("mode"), {})
        )
        total = max(
            len(chapters.get(lid, [])),
            track.get("scene_count") or 0,
            l["data"].get("chapter_count", 0),
        )
        completed = set(progress.get("completed_chapters", [])) & {
            c["id"] for c in ready
        }
        resume = next(
            (c for c in ready if c["id"] == progress.get("chapter_id")), ready[0]
        )
        state = progress.get("study_state", "active")
        if completed and len(completed) == total:
            state = "completed"
        row = {
            "id": lid,
            "title": l["data"].get("title", ""),
            "paper_id": l["paper_id"],
            "mode": l["data"].get("mode"),
            "study_state": state,
            "chapters_ready": len(ready),
            "chapters_total": total,
            "chapters_completed": len(completed),
            "resume_chapter": resume["title"],
            "resume_chapter_id": resume["id"],
            "turn_index": min(
                progress.get("turn_index", 0), max(0, resume["sentence_count"] - 1)
            ),
            "due_reviews": due_by_lesson[lid],
            "last_studied_at": progress.get(
                "last_studied_at", attempts.get(lid, progress.get("started_at", 0))
            ),
        }
        if (progress or lid in attempts) and state == "active":
            active.append(row)
        elif not progress and lid not in attempts:
            family = (l["paper_id"], l["data"].get("mode", "lesson"))
            if family not in available_versions:
                resumable.append(row)
                available_versions.add(family)
    active.sort(key=lambda c: c["last_studied_at"], reverse=True)
    making = []
    for p in projects.values():
        if p["state"] == "ready":
            continue
        job = db.one(
            "SELECT id,state,stage,progress,error FROM jobs WHERE kind='video_project' AND target=? ORDER BY created DESC LIMIT 1",
            (p["id"],),
        )
        if not job or job["state"] in {"completed", "cancelled"}:
            continue
        tracks = []
        for mode, t in p["tracks"].items():
            ready = [
                c
                for c in chapters.get(t["lesson_id"], [])
                if c["state"] == "ready" and c["sentence_count"]
            ]
            tracks.append(
                {
                    "mode": mode,
                    "lesson_id": t["lesson_id"],
                    "ready_chapters": len(ready),
                    "total_chapters": t["scene_count"],
                }
            )
        making.append(
            {
                "id": p["id"],
                "paper_id": p["paper_id"],
                "title": p["title"],
                "job": job,
                "tracks": tracks,
            }
        )
    z = ZoneInfo("Asia/Tokyo")
    dates = Counter()
    for r in schedules:
        dates[datetime.fromtimestamp(max(now, r["due"]), z).date().isoformat()] += 1
    today = datetime.fromtimestamp(now, z).date()
    return {
        "active_courses": active,
        "available_courses": resumable[:4],
        "making_courses": making[:4],
        "reviews_due": sum(due_by_lesson.values()),
        "review_sample": due[:3],
        "review_calendar": [
            {
                "date": (today + timedelta(days=i)).isoformat(),
                "count": dates[(today + timedelta(days=i)).isoformat()],
            }
            for i in range(7)
        ],
        "scheduler": repetition.VERSION,
        "desired_retention": 0.9,
        "updated_at": now,
    }
