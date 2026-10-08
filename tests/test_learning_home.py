import time
from datetime import datetime

import pytest
from paperspeak import db, learning_home, lessons, papers, practice, repetition


def course():
    pid = papers.register(
        {"source_id": "home-test", "version": "v1", "title": "A paper to learn"}
    )
    lid = lessons.create(pid)
    data = {
        "title": "The main idea",
        "turns": [
            {
                "id": "line",
                "text": "Keep the original weights fixed.",
                "audio": "audio/example.wav",
            }
        ],
        "questions": [
            {
                "id": "q1",
                "question": "What stays fixed?",
                "sample_answer": "The original weights stay fixed.",
                "question_ja": "何を固定しますか？",
                "sample_answer_ja": "元の重みを固定します。",
            }
        ],
        "translation": {
            "items": {
                "turn:line": {
                    "english": "Keep the original weights fixed.",
                    "japanese": "元の重みを固定する。",
                }
            }
        },
    }
    for i, cid in enumerate(["chapter", "next"]):
        db.execute(
            "INSERT INTO chapters VALUES (?,?,?,?,?)",
            (
                cid,
                lid,
                i,
                "ready",
                db.dumps(
                    data | {"title": "The main idea" if i == 0 else "A second idea"}
                ),
            ),
        )
    return lid


def test_home_resumes_actual_position_and_retains_completion(database):
    lid = course()
    learning_home.save_position(
        lid, {"chapter_id": "next", "turn_index": 0, "role": "guide"}
    )
    learning_home.mark_chapter(lid, "chapter")
    learning_home.save_position(
        lid, {"chapter_id": "next", "turn_index": 0, "speed": 1.2}
    )
    h = learning_home.dashboard()
    c = h["active_courses"][0]
    assert c["id"] == lid and c["resume_chapter_id"] == "next"
    assert c["chapters_completed"] == 1 and c["chapters_total"] == 2
    assert (
        db.one("SELECT data FROM cursors WHERE key=?", ("lesson:" + lid,))["data"][
            "role"
        ]
        == "guide"
    )
    assert db.one("SELECT COUNT(*) n FROM reviews")["n"] == 1


def test_completed_course_leaves_active_home_but_records_are_retained(database):
    lid = course()
    learning_home.mark_chapter(lid, "chapter")
    learning_home.mark_chapter(lid, "next")
    assert learning_home.dashboard()["active_courses"] == []
    assert db.one("SELECT id FROM lessons WHERE id=?", (lid,))
    assert db.one("SELECT COUNT(*) n FROM reviews")["n"] == 2


def test_course_pause_changes_only_study_state(database):
    lid = course()
    learning_home.save_position(lid, {"chapter_id": "chapter", "turn_index": 0})
    learning_home.course_state(lid, "paused")
    assert learning_home.dashboard()["active_courses"] == []
    assert db.one("SELECT state FROM lessons WHERE id=?", (lid,))["state"] != "paused"
    learning_home.save_position(lid, {"chapter_id": "chapter", "turn_index": 0})
    assert len(learning_home.dashboard()["active_courses"]) == 1


def test_fsrs_memory_ratings_change_next_interval_and_preserve_due_on_enrollment(
    database,
):
    lid = course()
    now = 1700000000
    a = repetition.enroll(lid, "chapter", turn_id="line", now=now)
    same = repetition.enroll(lid, "chapter", turn_id="line", now=now + 10)
    assert same["due"] == now + 86400
    choices = {
        o["rating"]: o["seconds"] for o in repetition.public(a, now + 86400)["options"]
    }
    assert choices["again"] < choices["hard"] < choices["good"] < choices["easy"]
    result = repetition.rate(
        a["id"], "easy", event_id="first", revision=0, now=now + 86400
    )
    assert result["seconds"] > 86400
    r = db.one("SELECT * FROM reviews WHERE id=?", (a["id"],))
    second = repetition.rate(
        a["id"], "good", event_id="second", revision=1, now=result["due"]
    )
    assert second["seconds"] > 86400
    lapse = repetition.rate(
        a["id"], "again", event_id="third", revision=2, now=second["due"]
    )
    assert lapse["seconds"] < second["seconds"]
    assert db.one("SELECT COUNT(*) n FROM review_events")["n"] == 3
    assert r["data"]["fsrs"]["stability"] > 0


def test_retry_is_idempotent_and_stale_tab_cannot_double_rate(database):
    lid = course()
    r = repetition.enroll(lid, "chapter", turn_id="line", now=1700000000)
    result = repetition.rate(
        r["id"], "good", event_id="same", revision=0, now=1700086400
    )
    assert (
        repetition.rate(r["id"], "good", event_id="same", revision=0, now=1700086401)
        == result
    )
    with pytest.raises(ValueError, match="already rated"):
        repetition.rate(r["id"], "hard", event_id="another", revision=0, now=1700086401)
    assert db.one("SELECT COUNT(*) n FROM review_events")["n"] == 1


def test_legacy_due_and_history_are_not_fabricated(database):
    lid = course()
    rid = "chapter:line"
    db.execute(
        "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
        (rid, lid, "chapter", "line", 1700000000, 3, db.dumps({"kind": "read"})),
    )
    r = db.one("SELECT * FROM reviews WHERE id=?", (rid,))
    assert repetition.public(r, 1700000100)["estimated_recall"] is None
    assert db.one("SELECT due FROM reviews WHERE id=?", (rid,))["due"] == 1700000000
    repetition.rate(rid, "hard", now=1700000100)
    after = db.one("SELECT * FROM reviews WHERE id=?", (rid,))
    assert after["data"]["legacy_schedule"] == {"due": 1700000000, "step": 3}
    assert db.one("SELECT COUNT(*) n FROM review_events")["n"] == 1


def test_unscored_recording_does_not_change_memory_schedule(database):
    lid = course()
    practice.add_review(
        {
            "lesson_id": lid,
            "chapter_id": "chapter",
            "turn_id": "line",
            "state": "unscored",
            "data": {},
        }
    )
    assert db.one("SELECT COUNT(*) n FROM reviews")["n"] == 0


def test_archived_or_missing_items_are_not_presented_as_due(database):
    lid = course()
    r = repetition.enroll(lid, "chapter", turn_id="line", now=time.time() - 100000)
    assert len(repetition.due_cards()) == 1
    c = db.one("SELECT * FROM chapters WHERE id=?", ("chapter",))
    c["data"]["turns"] = []
    db.execute(
        "UPDATE chapters SET data=? WHERE id=?", (db.dumps(c["data"]), "chapter")
    )
    assert repetition.due_cards() == []
    assert db.one("SELECT id FROM reviews WHERE id=?", (r["id"],))


def test_study_and_review_api_round_trip(client):
    lid = course()
    assert (
        client.put(
            f"/api/lessons/{lid}/progress", json={"chapter_id": "next", "turn_index": 0}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/lessons/{lid}/chapters/chapter/study", json={"completed": True}
        ).status_code
        == 200
    )
    h = client.get("/api/learning-home").json()
    assert h["active_courses"][0]["resume_chapter_id"] == "next"
    card = client.post(
        f"/api/lessons/{lid}/reviews", json={"chapter_id": "chapter", "turn_id": "line"}
    ).json()
    db.execute("UPDATE reviews SET due=? WHERE id=?", (time.time() - 1, card["id"]))
    due = client.get("/api/reviews").json()[0]
    assert due["cue_ja"] == "元の重みを固定する。"
    rated = client.post(
        f"/api/reviews/{due['id']}/complete",
        json={"rating": "good", "event_id": "api-review", "revision": 0},
    )
    assert rated.status_code == 200 and rated.json()["scheduler"] == repetition.VERSION
    assert client.get("/api/reviews").json() == []
    assert client.get(f"/api/lessons/{lid}").json()["progress"]["chapter_id"] == "next"


def test_home_counts_all_due_items_and_uses_japanese_calendar_dates(database):
    lid = course()
    now = datetime.fromisoformat("2026-10-09T23:50:00+09:00").timestamp()
    chapter = db.one("SELECT * FROM chapters WHERE id='chapter'")
    chapter["data"]["questions"] = [
        {
            "id": f"q{i}",
            "question": "What stays fixed?",
            "sample_answer": "The weights.",
        }
        for i in range(106)
    ]
    db.execute(
        "UPDATE chapters SET data=? WHERE id='chapter'", (db.dumps(chapter["data"]),)
    )
    for i in range(106):
        r = repetition.enroll(lid, "chapter", question_id=f"q{i}", now=now - 100000)
        due = now - 3600 if i < 105 else now + 1200
        db.execute("UPDATE reviews SET due=? WHERE id=?", (due, r["id"]))
    home = learning_home.dashboard(now)
    assert home["reviews_due"] == 105
    assert len(home["review_sample"]) == 3
    assert home["review_calendar"][:2] == [
        {"date": "2026-10-09", "count": 105},
        {"date": "2026-10-10", "count": 1},
    ]
    assert len(repetition.due_cards(now)) == 100


def test_partial_course_is_available_while_generation_stays_paused(database):
    lid = course()
    pid = db.one("SELECT paper_id FROM lessons WHERE id=?", (lid,))["paper_id"]
    db.execute("UPDATE chapters SET state='audio' WHERE id='next'")
    project = {
        "paper_title": "A paper to learn",
        "modes": {"overview": {"lesson_id": lid, "scenes": [{}, {}, {}]}},
    }
    db.execute(
        "INSERT INTO video_projects VALUES (?,?,?,?,?,?,?)",
        (
            "project",
            pid,
            "home-project",
            "building",
            db.dumps(project),
            time.time(),
            time.time(),
        ),
    )
    job = db.enqueue("video_project", "project")
    db.patch_job(job, state="paused")
    home = learning_home.dashboard()
    making = home["making_courses"][0]
    assert making["job"]["state"] == "paused"
    assert making["tracks"][0]["ready_chapters"] == 1
    assert making["tracks"][0]["lesson_id"] == lid
    assert db.one("SELECT state FROM jobs WHERE id=?", (job,))["state"] == "paused"


def test_review_api_rejects_cross_course_and_duplicate_ratings(client):
    lid = course()
    assert (
        client.post(
            "/api/lessons/another-course/reviews",
            json={"chapter_id": "chapter", "turn_id": "line"},
        ).status_code
        == 422
    )
    r = repetition.enroll(lid, "chapter", turn_id="line")
    path = f"/api/reviews/{r['id']}/complete"
    payload = {"rating": "good", "event_id": "network-retry", "revision": 0}
    result = client.post(path, json=payload)
    assert result.status_code == 200
    assert client.post(path, json=payload).json() == result.json()
    assert (
        client.post(path, json=payload | {"event_id": "stale-tab"}).status_code == 409
    )
    lesson = db.one("SELECT data FROM lessons WHERE id=?", (lid,))["data"]
    db.execute(
        "UPDATE lessons SET data=? WHERE id=?",
        (db.dumps(lesson | {"archived": True}), lid),
    )
    assert learning_home.dashboard(result.json()["due"] + 1)["reviews_due"] == 0
    assert db.one("SELECT id FROM reviews WHERE id=?", (r["id"],))
