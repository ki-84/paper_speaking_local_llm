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
