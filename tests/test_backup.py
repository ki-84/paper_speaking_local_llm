import os
import json
import sqlite3
import subprocess
import sys
import time

from paperspeak import config, db, papers


def test_backup_refuses_missing_referenced_assets_without_publishing(
    database, tmp_path
):
    papers.register(
        {
            "source_id": "test",
            "version": "v1",
            "title": "Backup test",
            "pdf_path": "papers/missing.pdf",
        }
    )
    target = tmp_path / "output.tar.gz"
    r = subprocess.run(
        [
            sys.executable,
            str(config.ROOT / "scripts/manage.py"),
            "backup",
            "--output",
            str(target),
        ],
        env=os.environ | {"PAPERSPEAK_DATA": str(database)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert r.returncode != 0 and "referenced asset is missing" in r.stderr
    assert (
        not target.exists() and not target.with_name(target.name + ".partial").exists()
    )


def test_visual_images_metadata_and_history_survive_backup_restore(database, tmp_path):
    pid = papers.register({"source_id": "visual-backup", "version": "v1", "title": "Visual backup"})
    lesson_id = db.uid()
    db.execute("INSERT INTO lessons VALUES (?,?,?,?,?,?)",
               (lesson_id, pid, "ready", db.dumps({"format": "paper-visual-2"}), time.time(), time.time()))
    (database / "videos" / "complete.mp4").write_bytes(b"checked video bytes")
    db.execute("INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
               ("backup-video", lesson_id, "", "full", "digest", "ready",
                db.dumps({"mp4": "videos/complete.mp4", "duration": 2}), time.time(), time.time()))
    metadata = {"image_path": "visuals/current.png", "svg_path": "visuals/current.svg",
                "history": [{"image_path": "visuals/previous.png"}], "title_ja": "元の図"}
    for name in ("current.png", "current.svg", "previous.png"):
        (database / "visuals" / name).write_bytes(("saved " + name).encode())
    db.execute("INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
               ("visual-test", pid, None, "teaching", db.dumps(metadata), time.time()))
    archive, restored = tmp_path / "visuals.tar.gz", tmp_path / "restored"
    for args in (["manage.py", "backup", "--output", str(archive)],
                 ["restore.py", str(archive), str(restored)]):
        result = subprocess.run([sys.executable, str(config.ROOT / "scripts" / args[0]), *args[1:]],
                                env=os.environ | {"PAPERSPEAK_DATA": str(database)},
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
    with sqlite3.connect(restored / "data/paperspeak.sqlite3") as conn:
        data = json.loads(conn.execute("SELECT data FROM visual_assets WHERE id='visual-test'").fetchone()[0])
        video_data = json.loads(conn.execute("SELECT data FROM video_exports WHERE id='backup-video'").fetchone()[0])
    assert data == metadata
    assert video_data["mp4"] == "videos/complete.mp4"
    assert (restored / "data/videos/complete.mp4").read_bytes() == b"checked video bytes"
    for name in ("current.png", "current.svg", "previous.png"):
        assert (restored / "data/visuals" / name).read_bytes() == (database / "visuals" / name).read_bytes()
