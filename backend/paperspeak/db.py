from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid

from . import config


def uid() -> str:
    return uuid.uuid4().hex


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


@contextlib.contextmanager
def connection():
    con = sqlite3.connect(config.DATA / "paperspeak.sqlite3", timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def init():
    config.init_dirs()
    with connection() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
          id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version TEXT NOT NULL,
          title TEXT NOT NULL, data TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'new',
          created REAL NOT NULL, updated REAL NOT NULL, UNIQUE(source_id,version));
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          kind TEXT NOT NULL, data TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS sources_paper ON sources(paper_id);
        CREATE TABLE IF NOT EXISTS lessons (
          id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          state TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS chapters (
          id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
          ordinal INTEGER NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL,
          UNIQUE(lesson_id,ordinal));
        CREATE TABLE IF NOT EXISTS attempts (
          id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
          chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
          turn_id TEXT, kind TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews (
          id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
          chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
          turn_id TEXT, due REAL NOT NULL, step INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS recommendations (
          id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
          day TEXT NOT NULL, state TEXT NOT NULL, data TEXT NOT NULL, feedback TEXT,
          UNIQUE(paper_id,day));
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT, payload TEXT NOT NULL,
          state TEXT NOT NULL, stage TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
          checkpoint TEXT NOT NULL DEFAULT '{}', error TEXT, priority INTEGER NOT NULL DEFAULT 10,
          owner TEXT, heartbeat REAL, available REAL NOT NULL DEFAULT 0,
          created REAL NOT NULL, updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,priority,created);
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cursors (key TEXT PRIMARY KEY,data TEXT NOT NULL);
        PRAGMA user_version=1;
        """)
        for key, value in config.DEFAULTS.items():
            c.execute(
                "INSERT OR IGNORE INTO settings VALUES (?,?)", (key, dumps(value))
            )


def row(r):
    if r is None:
        return None
    result = dict(r)
    for k in ("data", "payload", "checkpoint"):
        if k in result:
            result[k] = json.loads(result[k])
    return result


def one(sql, args=()):
    with connection() as c:
        return row(c.execute(sql, args).fetchone())


def all(sql, args=()):
    with connection() as c:
        return [row(r) for r in c.execute(sql, args).fetchall()]


def execute(sql, args=()):
    with connection() as c:
        return c.execute(sql, args).rowcount


def settings():
    with connection() as c:
        return {
            r["key"]: json.loads(r["value"])
            for r in c.execute("SELECT * FROM settings")
        }


def set_setting(key, value):
    execute(
        "INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, dumps(value)),
    )


def event(kind, data):
    execute(
        "INSERT INTO events(type,data,created) VALUES (?,?,?)",
        (kind, dumps(data), time.time()),
    )


def enqueue(kind, target=None, payload=None, priority=10):
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        ident = queue_job(c, kind, target, payload, priority)
    event("job", {"id": ident, "state": "queued"})
    return ident


def queue_job(c, kind, target=None, payload=None, priority=10):
    """Queue inside the caller's write transaction, so content and work commit together."""
    existing = c.execute(
        "SELECT id FROM jobs WHERE kind=? AND target IS ? AND state IN ('queued','running','paused')",
        (kind, target),
    ).fetchone()
    if existing:
        return existing["id"]
    ident = uid()
    now = time.time()
    c.execute(
        "INSERT INTO jobs(id,kind,target,payload,state,stage,priority,created,updated) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            ident,
            kind,
            target,
            dumps(payload or {}),
            "queued",
            "Waiting",
            priority,
            now,
            now,
        ),
    )
    return ident


def claim(owner):
    now = time.time()
    with connection() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "UPDATE jobs SET state='queued',owner=NULL,stage='Resuming after interruption' WHERE state='running' AND heartbeat<?",
            (now - 90,),
        )
        r = c.execute(
            """SELECT * FROM jobs WHERE state='queued' AND available<=?
            ORDER BY CASE WHEN kind IN ('lesson','discover') AND priority>10
              AND coalesce(heartbeat,created)<? THEN 9 ELSE priority END,
              coalesce(heartbeat,created),created LIMIT 1""",
            (now, now - 15 * 60),
        ).fetchone()
        if not r:
            return None
        c.execute(
            "UPDATE jobs SET state='running',owner=?,heartbeat=?,updated=?,error=NULL WHERE id=?",
            (owner, now, now, r["id"]),
        )
        result = row(r)
        result.update(state="running", owner=owner)
        return result


def patch_job(ident, **changes):
    allowed = {
        "state",
        "stage",
        "progress",
        "checkpoint",
        "error",
        "available",
        "owner",
        "heartbeat",
        "priority",
    }
    if not changes.keys() <= allowed:
        raise ValueError("Invalid job update")
    changes["updated"] = time.time()
    if "checkpoint" in changes:
        changes["checkpoint"] = dumps(changes["checkpoint"])
    execute(
        "UPDATE jobs SET " + ",".join(k + "=?" for k in changes) + " WHERE id=?",
        (*changes.values(), ident),
    )
    event("job", {"id": ident})


def save_lesson(lesson):
    execute(
        "UPDATE lessons SET state=CASE WHEN state='cancelled' THEN state ELSE ? END,data=?,updated=? WHERE id=?",
        (lesson["state"], dumps(lesson["data"]), time.time(), lesson["id"]),
    )


def save_chapter(chapter):
    execute(
        "UPDATE chapters SET state=?,data=? WHERE id=?",
        (chapter["state"], dumps(chapter["data"]), chapter["id"]),
    )
