"""Local FSRS schedules; memory ratings are distinct from pronunciation scores."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime

from fsrs import Card, Rating, Scheduler

from . import db

VERSION = "fsrs-6.3.2"
RATINGS = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


def scheduler():
    return Scheduler(desired_retention=0.9, maximum_interval=3650, enable_fuzzing=False)


AVAILABLE = """FROM reviews r JOIN lessons l ON l.id=r.lesson_id JOIN chapters c ON c.id=r.chapter_id
 WHERE c.state='ready' AND coalesce(json_extract(l.data,'$.archived'),0)=0
 AND coalesce(json_extract(r.data,'$.suspended'),0)=0
 AND ((r.turn_id IS NOT NULL AND EXISTS(SELECT 1 FROM json_each(c.data,'$.turns') t WHERE json_extract(t.value,'$.id')=r.turn_id))
 OR (r.turn_id IS NULL AND EXISTS(SELECT 1 FROM json_each(c.data,'$.questions') q WHERE json_extract(q.value,'$.id')=json_extract(r.data,'$.question_id'))))"""


class ReviewConflict(ValueError):
    pass


def card_for(row):
    saved = row["data"].get("fsrs")
    if saved:
        return Card.from_dict(saved)
    # Preserve an old card's due date. Historical fixed-step ratings were not
    # logged, so do not invent past recalls to initialize its memory strength.
    ident = int(hashlib.sha256(row["id"].encode()).hexdigest()[:15], 16)
    return Card(card_id=ident, due=datetime.fromtimestamp(row["due"], UTC))


def target(row, chapter=None):
    c = chapter or db.one(
        "SELECT * FROM chapters WHERE id=? AND lesson_id=?",
        (row["chapter_id"], row["lesson_id"]),
    )
    if not c or c["state"] != "ready":
        return None
    data = c["data"]
    translations = data.get("translation", {}).get("items", {})
    if row.get("turn_id"):
        item = next(
            (t for t in data.get("turns", []) if t["id"] == row["turn_id"]), None
        )
        if not item:
            return None
        translated = translations.get("turn:" + item["id"], {})
        ja = (
            translated.get("japanese", "")
            if translated.get("english") == item["text"]
            else ""
        )
        return {
            "kind": "read",
            "prompt_en": "Say this idea in English before looking at the words.",
            "cue_ja": ja,
            "answer_en": item["text"],
            "answer_ja": ja,
            "audio": item.get("audio"),
            "chapter_title": data["title"],
        }
    item = next(
        (
            q
            for q in data.get("questions", [])
            if q["id"] == row["data"].get("question_id")
        ),
        None,
    )
    if not item:
        return None
    return {
        "kind": "answer",
        "prompt_en": item["question"],
        "cue_ja": item.get("question_ja", ""),
        "answer_en": item.get("sample_answer", ""),
        "answer_ja": item.get("sample_answer_ja", ""),
        "audio": None,
        "chapter_title": data["title"],
    }


def enroll(lesson_id, chapter_id, turn_id=None, question_id=None, *, now=None):
    now = time.time() if now is None else now
    if bool(turn_id) == bool(question_id):
        raise ValueError("Choose one sentence or understanding question")
    rid = f"{chapter_id}:{turn_id or question_id}"
    row = {
        "id": rid,
        "lesson_id": lesson_id,
        "chapter_id": chapter_id,
        "turn_id": turn_id,
        "due": now + 86400,
        "data": {"kind": "read" if turn_id else "answer", "question_id": question_id},
    }
    if not target(row):
        raise ValueError("Choose a finished sentence or question")
    row["data"].update(
        scheduler=VERSION, fsrs=card_for(row).to_dict(), revision=0, enrolled_at=now
    )
    db.execute(
        "INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,?,?)",
        (rid, lesson_id, chapter_id, turn_id, row["due"], 0, db.dumps(row["data"])),
    )
    return db.one("SELECT * FROM reviews WHERE id=?", (rid,))


def public(row, now=None, chapter=None):
    now = time.time() if now is None else now
    content = target(row, chapter)
    if not content:
        return None
    card = card_for(row)
    model = scheduler()
    when = datetime.fromtimestamp(now, UTC)
    options = []
    for label, rating in RATINGS.items():
        updated, _ = model.review_card(
            Card.from_dict(card.to_dict()), rating, review_datetime=when
        )
        options.append(
            {
                "rating": label,
                "due": updated.due.timestamp(),
                "seconds": max(0, round(updated.due.timestamp() - now)),
            }
        )
    return (
        row
        | content
        | {
            "revision": row["data"].get("revision", 0),
            "scheduler": VERSION,
            "desired_retention": model.desired_retention,
            "estimated_recall": model.get_card_retrievability(
                card, current_datetime=when
            )
            if card.stability is not None
            else None,
            "options": options,
        }
    )


def due_cards(now=None, limit=100):
    now = time.time() if now is None else now
    rows = db.all(
        "SELECT r.*,json_object('title',json_extract(l.data,'$.title')) AS lesson_data "
        + AVAILABLE
        + " AND r.due<=? ORDER BY r.due LIMIT ?",
        (now, limit),
    )
    cache, output = {}, []
    for r in rows:
        if r["chapter_id"] not in cache:
            cache[r["chapter_id"]] = db.one(
                "SELECT * FROM chapters WHERE id=?", (r["chapter_id"],)
            )
        item = public(r, now, cache[r["chapter_id"]])
        if item:
            output.append(item)
    return output


def rate(ident, rating, *, event_id=None, revision=None, now=None):
    if rating not in RATINGS:
        raise ValueError("Choose again, hard, good or easy")
    now = time.time() if now is None else now
    event_id = event_id or db.uid()
    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        prior = db.row(
            c.execute("SELECT * FROM review_events WHERE id=?", (event_id,)).fetchone()
        )
        if prior:
            if prior["review_id"] != ident or prior["rating"] != rating:
                raise ReviewConflict("Review event identifier is already in use")
            return prior["data"]["response"]
        r = db.row(c.execute("SELECT * FROM reviews WHERE id=?", (ident,)).fetchone())
        if not r:
            raise ValueError("Review not found")
        if revision is not None and revision != r["data"].get("revision", 0):
            raise ReviewConflict(
                "This review was already rated. Refresh today's reviews"
            )
        if not target(r):
            raise ValueError("This review item is no longer available")
        model = scheduler()
        updated, log = model.review_card(
            card_for(r),
            RATINGS[rating],
            review_datetime=datetime.fromtimestamp(now, UTC),
        )
        data = r["data"] | {
            "scheduler": VERSION,
            "fsrs": updated.to_dict(),
            "revision": r["data"].get("revision", 0) + 1,
            "last_rating": rating,
            "last_reviewed": now,
        }
        if "fsrs" not in r["data"]:
            data["legacy_schedule"] = {"due": r["due"], "step": r["step"]}
        response = {
            "ok": True,
            "id": ident,
            "due": updated.due.timestamp(),
            "seconds": round(updated.due.timestamp() - now),
            "revision": data["revision"],
            "rating": rating,
            "scheduler": VERSION,
        }
        c.execute(
            "UPDATE reviews SET data=?,due=?,step=step+1 WHERE id=?",
            (db.dumps(data), response["due"], ident),
        )
        c.execute(
            "INSERT INTO review_events VALUES (?,?,?,?,?)",
            (
                event_id,
                ident,
                rating,
                now,
                db.dumps(
                    {
                        "log": log.to_dict(),
                        "parameters": model.to_dict(),
                        "response": response,
                    }
                ),
            ),
        )
    db.event("review", {"id": ident, "due": response["due"]})
    return response
