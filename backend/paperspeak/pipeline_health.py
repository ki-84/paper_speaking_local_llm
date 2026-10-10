"""Repair missing work and interrupted ownership without restarting user-stopped work."""

from __future__ import annotations

import hashlib
import json
import time

from . import db

ACTIVE = {"searching", "reading", "building"}
MAX_RECOVERIES = 3


def control(project_id, state):
    # A separate row survives a stale generator saving its older project snapshot.
    db.execute(
        "INSERT INTO cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
        (
            "pipeline-control:" + project_id,
            db.dumps({"state": state, "at": time.time()}),
        ),
    )


def recover_interrupted(owner):
    """Call only after obtaining the exclusive worker lock: other owners are gone."""
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM jobs WHERE state='queued' OR (state='running' AND (owner IS NULL OR owner!=?))",
            (owner,),
        ).fetchall()
        changes = []
        for row in rows:
            state = stopped_state(conn, row["kind"], row["target"])
            if json.loads(row["checkpoint"]).get("superseded"):
                state = "cancelled"
            if (
                state is None
                and row["state"] == "queued"
                and row["stage"] != "Saving the checkpoint for service restart."
            ):
                continue
            state = state or "queued"
            conn.execute(
                "UPDATE jobs SET state=?,owner=NULL,heartbeat=NULL,updated=?,stage=? WHERE id=?",
                (
                    state,
                    time.time(),
                    "Resuming after worker restart"
                    if state == "queued"
                    else "User stop preserved after restart",
                    row["id"],
                ),
            )
            changes.append({"job_id": row["id"], "state": state})
    for change in changes:
        db.event("job", {"id": change["job_id"], "state": change["state"]})
    db.execute(
        "INSERT INTO cursors VALUES ('worker-recovery',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
        (db.dumps({"checked_at": time.time(), "jobs": changes}),),
    )
    return changes


def stopped_state(conn, kind, target):
    if kind == "nightly_video":
        run = conn.execute(
            "SELECT state FROM nightly_video_runs WHERE id=?", (target,)
        ).fetchone()
        return run["state"] if run and run["state"] in {"paused", "cancelled"} else None
    project_id = target if kind == "video_project" else None
    if kind == "thumbnail":
        row = conn.execute(
            "SELECT project_id FROM thumbnail_sets WHERE id=?", (target,)
        ).fetchone()
        project_id = row["project_id"] if row else None
    if kind == "story_video":
        row = conn.execute(
            "SELECT p.id FROM video_projects p,json_each(p.data,'$.modes') t WHERE json_extract(t.value,'$.lesson_id')=(SELECT lesson_id FROM video_exports WHERE id=?) LIMIT 1",
            (target,),
        ).fetchone()
        project_id = row["id"] if row else None
    if project_id:
        row = conn.execute(
            "SELECT data FROM cursors WHERE key=?", ("pipeline-control:" + project_id,)
        ).fetchone()
        if row and json.loads(row["data"]).get("state") in {"paused", "cancelled"}:
            return json.loads(row["data"])["state"]
        row = conn.execute(
            "SELECT state FROM nightly_video_runs WHERE project_id=? AND state IN ('paused','cancelled') LIMIT 1",
            (project_id,),
        ).fetchone()
        return row["state"] if row else None
    return None


def stop_requested(job):
    if job["checkpoint"].get("superseded"):
        return "cancelled"
    with db.connection() as conn:
        return stopped_state(conn, job["kind"], job["target"])


def dependency_state(conn, kind, target):
    if kind == "video_project":
        row = conn.execute(
            "SELECT data FROM video_projects WHERE id=?", (target,)
        ).fetchone()
        if not row:
            return None
        data = json.loads(row["data"])
        track = data.get("modes", {}).get(data.get("current_mode"), {})
        export = conn.execute(
            "SELECT id,state FROM video_exports WHERE id=?", (track.get("export_id"),)
        ).fetchone()
        return [
            data.get("phase"),
            data.get("current_mode"),
            track.get("phase"),
            dict(export) if export else None,
        ]
    if kind == "nightly_video":
        row = conn.execute(
            "SELECT data,project_id FROM nightly_video_runs WHERE id=?", (target,)
        ).fetchone()
        if not row:
            return None
        project = conn.execute(
            "SELECT id,state FROM video_projects WHERE id=?", (row["project_id"],)
        ).fetchone()
        thumbs = conn.execute(
            "SELECT id,mode,input_digest,state FROM thumbnail_sets WHERE project_id=? ORDER BY created DESC,rowid DESC",
            (row["project_id"],),
        ).fetchall()
        latest = {}
        for thumb in thumbs:
            latest.setdefault(thumb["mode"], dict(thumb))
        return [
            json.loads(row["data"]).get("phase"),
            dict(project) if project else None,
            latest,
        ]
    return None


def fingerprint(signature):
    return hashlib.sha256(db.dumps(signature).encode()).hexdigest()


def failed_dependency(kind, target):
    with db.connection() as conn:
        return fingerprint(dependency_state(conn, kind, target))


def reconcile():
    """A between-step check; never runs models, rewrites content, or kills long steps."""
    stamp = time.time()
    repaired, issues = [], []
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")

        def protected(project_id):
            row = conn.execute(
                "SELECT data FROM cursors WHERE key=?",
                ("pipeline-control:" + project_id,),
            ).fetchone()
            if row and json.loads(row["data"]).get("state") in {"paused", "cancelled"}:
                return True
            stopped = conn.execute(
                "SELECT id FROM nightly_video_runs WHERE project_id=? AND state IN ('paused','cancelled')",
                (project_id,),
            ).fetchone()
            return bool(stopped)

        def ensure(kind, target, priority, signature, *, changed_dependency=False):
            job = conn.execute(
                "SELECT * FROM jobs WHERE kind=? AND target=? ORDER BY created DESC,rowid DESC LIMIT 1",
                (kind, target),
            ).fetchone()
            if job is None:
                ident = db.queue_job(conn, kind, target, priority=priority)
                repaired.append(
                    {
                        "job_id": ident,
                        "kind": kind,
                        "target": target,
                        "reason": "Missing job restored from saved content",
                    }
                )
                return "queued"
            state = job["state"]
            cp = json.loads(job["checkpoint"])
            if state in {"queued", "running", "paused", "cancelled"} or cp.get(
                "superseded"
            ):
                return state
            if state not in {"failed", "completed"}:
                return state
            if state == "failed" and not changed_dependency:
                issues.append(
                    {
                        "job_id": job["id"],
                        "reason": job["error"],
                        "action": "Existing bounded repair or explicit retry required",
                    }
                )
                return state
            signature_hash = fingerprint(signature)
            if state == "failed" and cp.get("failed_dependency") == signature_hash:
                issues.append(
                    {
                        "job_id": job["id"],
                        "reason": "Dependencies have not changed since the failure",
                    }
                )
                return state
            guard = cp.setdefault("auto_recovery", {"signatures": [], "history": []})
            if (
                signature_hash in guard["signatures"]
                or len(guard["signatures"]) >= MAX_RECOVERIES
            ):
                issues.append(
                    {
                        "job_id": job["id"],
                        "reason": "Unchanged dependency or automatic recovery budget exhausted",
                    }
                )
                return state
            guard["signatures"].append(signature_hash)
            guard["history"].append(
                {
                    "at": stamp,
                    "from_state": state,
                    "reason": "Dependency became ready"
                    if changed_dependency
                    else "Controller completed before its content",
                }
            )
            cp.pop("_failures", None)
            conn.execute(
                "UPDATE jobs SET state='queued',owner=NULL,heartbeat=NULL,error=NULL,available=0,checkpoint=?,stage='Automatically resuming saved work',updated=? WHERE id=?",
                (db.dumps(cp), stamp, job["id"]),
            )
            repaired.append(
                {
                    "job_id": job["id"],
                    "kind": kind,
                    "target": target,
                    "reason": guard["history"][-1]["reason"],
                }
            )
            return "queued"

        projects = {}
        runs = conn.execute(
            "SELECT * FROM nightly_video_runs WHERE state IN ('searching','reading','building','failed')"
        ).fetchall()
        for run in runs:
            data = json.loads(run["data"])
            project = (
                conn.execute(
                    "SELECT * FROM video_projects WHERE id=?", (run["project_id"],)
                ).fetchone()
                if run["project_id"]
                else None
            )
            dependency_ready = bool(
                project
                and project["state"] == "ready"
                and data.get("phase") == "production"
            )
            if dependency_ready:
                modes = json.loads(project["data"]).get("modes", {})
                for mode in modes:
                    thumb = conn.execute(
                        "SELECT state FROM thumbnail_sets WHERE project_id=? AND mode=? ORDER BY created DESC,rowid DESC LIMIT 1",
                        (project["id"], mode),
                    ).fetchone()
                    dependency_ready = dependency_ready and bool(
                        thumb and thumb["state"] == "ready"
                    )
            if run["state"] in ACTIVE or dependency_ready:
                if project and protected(project["id"]):
                    continue
                state = ensure(
                    "nightly_video",
                    run["id"],
                    12,
                    dependency_state(conn, "nightly_video", run["id"]),
                    changed_dependency=dependency_ready,
                )
                if run["state"] == "failed" and state in {"queued", "running"}:
                    data.pop("reason", None)
                    data.pop("finished", None)
                    conn.execute(
                        "UPDATE nightly_video_runs SET state='building',data=?,updated=? WHERE id=?",
                        (db.dumps(data), stamp, run["id"]),
                    )
                if project and state not in {"paused", "cancelled"}:
                    projects[project["id"]] = project
        for project in conn.execute(
            "SELECT * FROM video_projects WHERE state='building'"
        ).fetchall():
            projects[project["id"]] = project

        for project in projects.values():
            if protected(project["id"]):
                continue
            data = json.loads(project["data"])
            if project["state"] != "ready":
                track = data.get("modes", {}).get(data.get("current_mode"), {})
                export = conn.execute(
                    "SELECT id,state FROM video_exports WHERE id=?",
                    (track.get("export_id"),),
                ).fetchone()
                dependency_ready = bool(
                    data.get("phase") == "production"
                    and track.get("phase") == "export"
                    and export
                    and export["state"] == "ready"
                )
                state = ensure(
                    "video_project",
                    project["id"],
                    10,
                    dependency_state(conn, "video_project", project["id"]),
                    changed_dependency=dependency_ready,
                )
                if state in {"paused", "cancelled"}:
                    continue
            for mode, track in data.get("modes", {}).items():
                for field in ("export_id", "preview_id"):
                    export = conn.execute(
                        "SELECT * FROM video_exports WHERE id=?", (track.get(field),)
                    ).fetchone()
                    if export and export["state"] in {"queued", "running"}:
                        child_state = ensure(
                            "story_video",
                            export["id"],
                            9,
                            [export["input_digest"], export["state"]],
                        )
                        if child_state == "failed":
                            failed = conn.execute(
                                "SELECT error FROM jobs WHERE kind='story_video' AND target=? ORDER BY created DESC LIMIT 1",
                                (export["id"],),
                            ).fetchone()
                            details = json.loads(export["data"])
                            details["error"] = failed["error"]
                            conn.execute(
                                "UPDATE video_exports SET state='failed',data=?,updated=? WHERE id=?",
                                (db.dumps(details), stamp, export["id"]),
                            )
                thumb = conn.execute(
                    "SELECT * FROM thumbnail_sets WHERE project_id=? AND mode=? ORDER BY created DESC,rowid DESC LIMIT 1",
                    (project["id"], mode),
                ).fetchone()
                if thumb and thumb["state"] == "building":
                    child_state = ensure(
                        "thumbnail",
                        thumb["id"],
                        9,
                        [thumb["input_digest"], json.loads(thumb["data"]).get("phase")],
                    )
                    if child_state == "failed":
                        failed = conn.execute(
                            "SELECT error FROM jobs WHERE kind='thumbnail' AND target=? ORDER BY created DESC LIMIT 1",
                            (thumb["id"],),
                        ).fetchone()
                        details = json.loads(thumb["data"])
                        details["error"] = failed["error"]
                        conn.execute(
                            "UPDATE thumbnail_sets SET state='failed',data=?,updated=? WHERE id=?",
                            (db.dumps(details), stamp, thumb["id"]),
                        )
        old = conn.execute(
            "SELECT data FROM cursors WHERE key='pipeline-health'"
        ).fetchone()
        history = (json.loads(old["data"]).get("history", []) if old else []) + repaired
        result = {
            "checked_at": stamp,
            "repaired": repaired,
            "issues": issues,
            "history": history[-40:],
        }
        conn.execute(
            "INSERT INTO cursors VALUES ('pipeline-health',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
            (db.dumps(result),),
        )
    for repair in repaired:
        db.event(
            "job",
            {"id": repair["job_id"], "state": "queued", "automatic_recovery": True},
        )
    return result
