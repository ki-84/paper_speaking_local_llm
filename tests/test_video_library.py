import os

from paperspeak import db, lessons, papers, video, video_library


def add_export(
    database, lid, ident, kind, completed, *, state="ready", chapter="", **data
):
    movie = database / "videos" / (ident + ".mp4")
    movie.write_bytes(b"Saved film fixture")
    os.utime(movie, (completed, completed))
    record = {
        "title": ident,
        "mp4": "videos/" + movie.name,
        "duration": 120,
        "manifest": {"private_alignment_trace": "Not needed by the video list"},
        **data,
    }
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (ident, lid, chapter, kind, ident, state, db.dumps(record), 1, 99999),
    )
    return db.one("SELECT * FROM video_exports WHERE id=?", (ident,))


def lesson():
    pid = papers.register(
        {"source_id": "test-paper", "version": "v1", "title": "Useful AI"}
    )
    return lessons.create(pid, force_new=True)


def test_video_timeline_groups_render_revisions_and_preserves_new_scripts(
    client, database
):
    lid = lesson()
    add_export(database, lid, "old-render", "overview", 100)
    add_export(
        database,
        lid,
        "new-render",
        "overview",
        300,
        identity={"conference": "ICML 2026", "awards": [{"name": "Best Paper"}]},
    )
    add_export(database, lid, "deep", "deep_dive", 400)
    add_export(database, lesson(), "new-script", "overview", 200)
    add_export(database, lid, "whole-lesson", "full", 50)
    before_jobs = db.all("SELECT id,state FROM jobs")
    result = client.get("/api/videos").json()
    assert [v["id"] for v in result] == [
        "deep",
        "new-render",
        "new-script",
        "whole-lesson",
    ]
    overview = result[1]
    assert [v["id"] for v in overview["revisions"]] == ["old-render"]
    assert overview["completed_at"] == 300
    assert overview["data"]["conference"] == "ICML 2026"
    assert overview["data"]["awards"] == [{"name": "Best Paper"}]
    assert "manifest" not in overview["data"]
    assert [v["label"] for v in result] == [
        "詳細解説",
        "概要解説",
        "概要解説",
        "全章まとめ",
    ]
    assert (
        client.get("/api/files/" + overview["data"]["mp4"]).content
        == b"Saved film fixture"
    )
    assert db.all("SELECT id,state FROM jobs") == before_jobs


def test_timeline_omits_previews_unfinished_missing_and_unsafe_files(client, database):
    lid = lesson()
    add_export(database, lid, "preview", "overview_preview", 500)
    add_export(database, lid, "busy", "deep_dive", 400, state="checking")
    add_export(database, lid, "missing", "full", 300)
    (database / "videos/missing.mp4").unlink()
    add_export(database, lid, "unsafe", "full", 200, mp4="videos/../../private.mp4")
    add_export(database, lid, "private", "full", 200, mp4="credentials.json")
    assert client.get("/api/videos").json() == []
    client.headers.pop("Authorization")
    assert client.get("/api/videos").status_code == 401


def test_chapter_numbers_and_timeline_survive_poster_edits(database):
    lid = lesson()
    db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)", ("c", lid, 2, "ready", "{}"))
    old = add_export(database, lid, "chapter-old", "chapter", 100, chapter="c")
    add_export(database, lid, "chapter-new", "chapter", 200, chapter="c")
    add_export(database, lid, "overview", "overview", 300, completed_at=250)
    add_export(
        database,
        lid,
        "retained-chapter",
        "chapter",
        50,
        chapter="old-chapter-id",
        manifest={"ordinal": 0},
    )
    db.execute("UPDATE video_exports SET updated=? WHERE id=?", (100000, old["id"]))
    result = video_library.catalogue()
    assert [v["completed_at"] for v in result] == [250, 200, 50]
    assert result[1]["label"] == "第3章"
    assert result[1]["revisions"][0]["id"] == "chapter-old"
    assert result[2]["label"] == "第1章"


def test_completed_at_is_saved_once_for_future_exports(database, monkeypatch):
    lid = lesson()
    export = add_export(database, lid, "new", "full", 100, state="queued")
    monkeypatch.setattr(video.time, "time", lambda: 200)
    video._set_export(export, "ready")
    monkeypatch.setattr(video.time, "time", lambda: 500)
    video._set_export(export, "ready", thumbnail="thumbnails/a.png")
    stored = db.one("SELECT * FROM video_exports WHERE id=?", (export["id"],))
    assert stored["data"]["completed_at"] == 200
    assert stored["updated"] == 500
    assert video_library.catalogue()[0]["completed_at"] == 200


def test_archiving_old_formats_removes_cards_and_exports_without_deleting_work(
    client, database, monkeypatch
):
    old = lesson()
    modern = lesson()
    later_manual = lesson()
    db.execute("UPDATE lessons SET created=10 WHERE id=?", (old,))
    db.execute(
        "UPDATE lessons SET created=20,data=? WHERE id=?",
        (db.dumps({"format": "paper-story-1", "title": "LoRA · 解説編"}), modern),
    )
    db.execute("UPDATE lessons SET created=30 WHERE id=?", (later_manual,))
    movie = add_export(database, old, "legacy", "full", 100)
    add_export(database, modern, "modern", "overview", 200)
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        ("old-chapter", old, 0, "ready", '{"turns":[]}'),
    )
    db.execute(
        "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
        ("old-review", old, "old-chapter", None, 0, 0, "{}"),
    )
    job = db.enqueue("lesson", old)
    db.patch_job(job, state="paused")
    before_job = db.one("SELECT * FROM jobs WHERE id=?", (job,))
    preview = video_library.archive_before_story()
    assert not preview["applied"]
    assert [r["id"] for r in preview["lessons"]] == [old]
    assert len(client.get("/api/videos").json()) == 2
    result = video_library.archive_before_story(apply=True)
    assert [r["id"] for r in result["lessons"]] == [old]
    assert {r["id"] for r in client.get("/api/lessons").json()} == {
        modern,
        later_manual,
    }
    assert [r["id"] for r in client.get("/api/videos").json()] == ["modern"]
    assert client.get("/api/reviews").json() == []
    assert client.get("/api/lessons/" + old).status_code == 200
    assert (
        client.get("/api/files/" + movie["data"]["mp4"]).content
        == b"Saved film fixture"
    )
    assert db.one("SELECT id FROM reviews WHERE id='old-review'")
    assert db.one("SELECT * FROM jobs WHERE id=?", (job,)) == before_job
    assert video_library.archive_before_story(apply=True)["lessons"] == []

    def no_archived_chapter_render(*args):
        raise AssertionError("Archived chapters must not be rescheduled")

    monkeypatch.setattr(video, "chapter_manifest", no_archived_chapter_render)
    video.schedule()
    assert not db.one("SELECT id FROM jobs WHERE kind='chapter_video'")
    db.execute("UPDATE lessons SET state='ready' WHERE id=?", (later_manual,))
    assert (
        lessons.create(
            db.one("SELECT paper_id FROM lessons WHERE id=?", (old,))["paper_id"]
        )
        != old
    )
