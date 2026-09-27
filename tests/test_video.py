import asyncio
import subprocess
import time

import httpx
import imageio_ffmpeg
import pytest

from paperspeak import config, db, papers, video, youtube
from paperspeak.runtime import PracticePreempted


def lesson_row(name="video-test"):
    paper_id = papers.register({"source_id": name, "version": "1", "title": "Test paper"})
    ident = db.uid()
    now = time.time()
    db.execute("INSERT INTO lessons VALUES (?,?,?,?,?,?)",
               (ident, paper_id, "ready", db.dumps({"format": "paper-visual-2", "title": "Test paper"}), now, now))
    return ident


def test_burned_subtitles_and_download_tracks_follow_exact_voice_frames():
    turns = [{"frames": 24000, "english": "Look at A.", "japanese": "Aを見てください。"},
             {"frames": 36000, "english": "Now see B.", "japanese": "次にBを見ます。"}]
    tracks, ass, duration = video._captions(turns)
    assert duration == 2.5
    assert "00:00:00,000 --> 00:00:01,000" in tracks["en"]
    assert "00:00:01,000 --> 00:00:02,500" in tracks["ja"]
    assert "Look at A." in ass and "Aを見てください。" in ass
    assert "Style: English" in ass and "Style: Japanese" in ass


def test_chapter_exports_are_idempotent_and_complete_video_waits_for_every_chapter(database, monkeypatch):
    ident = lesson_row()
    for ordinal, state in ((0, "ready"), (1, "visuals")):
        db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)",
                   (f"ch{ordinal}", ident, ordinal, state, db.dumps({"title": f"Part {ordinal}"})))
    monkeypatch.setattr(video, "chapter_manifest", lambda chapter, lesson: {"ordinal": chapter["ordinal"], "version": video.VERSION})
    video.schedule()
    video.schedule()
    assert len(db.all("SELECT * FROM video_exports WHERE lesson_id=?", (ident,))) == 1
    assert not db.one("SELECT * FROM video_exports WHERE kind='full'")


def test_complete_video_joins_chapters_and_offsets_both_srt_tracks(database):
    ident = lesson_row("join-video")
    clips = []
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for ordinal, color in enumerate(("red", "blue")):
        path = database / "videos" / f"part-{ordinal}.mp4"
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"testsrc2=s=1920x1080:r=30:d=2", "-f", "lavfi",
                        "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", "-y", str(path)],
                       check=True, timeout=60)
        tracks, _, _ = video._captions([{"frames": 48000, "english": color, "japanese": "赤" if ordinal == 0 else "青"}])
        for lang in ("en", "ja"):
            (database / "videos" / f"part-{ordinal}.{lang}.srt").write_text(tracks[lang], encoding="utf-8")
        export_id = f"chapter-export-{ordinal}"
        db.execute("INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
                   (export_id, ident, f"ch{ordinal}", "chapter", f"digest-{ordinal}", "ready",
                    db.dumps({"mp4": f"videos/part-{ordinal}.mp4", "en_srt": f"videos/part-{ordinal}.en.srt",
                              "ja_srt": f"videos/part-{ordinal}.ja.srt", "duration": 2.0}), time.time(), time.time()))
        clips.append({"id": export_id, "digest": f"digest-{ordinal}", "mp4": f"videos/part-{ordinal}.mp4"})
    full_id = "complete-export"
    db.execute("INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
               (full_id, ident, "", "full", "complete-digest", "queued",
                db.dumps({"manifest": {"lesson_id": ident, "chapter_videos": clips}}), time.time(), time.time()))
    job_id = db.enqueue("full_video", full_id)
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert video.video_step(job, type("Runtime", (), {"practice_waiting": lambda self: False})())
    export = db.one("SELECT * FROM video_exports WHERE id=?", (full_id,))
    assert export["state"] == "ready" and export["data"]["chapter_count"] == 2
    assert config.safe_path(export["data"]["mp4"]).stat().st_size > 100000
    subtitles = config.safe_path(export["data"]["ja_srt"]).read_text(encoding="utf-8")
    assert "00:00:02,000 --> 00:00:04,000" in subtitles and "赤" in subtitles and "青" in subtitles


def test_youtube_upload_is_private_resumable_and_never_repeats_after_success(database, monkeypatch):
    ident = lesson_row("upload-video")
    path = database / "videos" / "full.mp4"
    path.write_bytes(b"abcdefgh")
    export_id = "youtube-export"
    db.execute("INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
               (export_id, ident, "", "full", "digest", "ready",
                db.dumps({"mp4": "videos/full.mp4", "bytes": 8}), time.time(), time.time()))
    job_id = db.enqueue("youtube_upload", export_id)
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    monkeypatch.setattr(youtube, "connected", lambda: True)
    monkeypatch.setattr(youtube, "access_token", lambda: "token")
    monkeypatch.setattr(youtube, "CHUNK", 4)
    posts, puts, chunks = [], [], []

    def post(url, **kwargs):
        posts.append(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url),
                              headers={"Location": "https://www.googleapis.com/upload/test"}, json={})

    def put(url, **kwargs):
        puts.append(kwargs)
        return httpx.Response(308, headers={"Range": "bytes=0-3"} if chunks else {})

    async def upload_chunk(url, headers, content, runtime):
        chunks.append((headers, content))
        return httpx.Response(201, json={"id": "youtube123"}) if len(chunks) == 2 else httpx.Response(308)

    monkeypatch.setattr(youtube.httpx, "post", post)
    monkeypatch.setattr(youtube.httpx, "put", put)
    monkeypatch.setattr(youtube, "_put_chunk", upload_chunk)
    runtime = type("Runtime", (), {"practice_waiting": lambda self: False})()
    assert youtube.upload_step(job, runtime) is False
    assert youtube.upload_step(job, runtime) is False
    assert youtube.upload_step(job, runtime) is True
    assert youtube.upload_step(job, runtime) is True
    export = db.one("SELECT * FROM video_exports WHERE id=?", (export_id,))
    assert export["data"]["youtube"]["video_id"] == "youtube123"
    assert posts[0]["json"]["status"]["privacyStatus"] == "private"
    assert [headers["Content-Range"] for headers, _ in chunks] == ["bytes 0-3/8", "bytes 4-7/8"]
    assert len(posts) == 1


def test_youtube_chunk_yields_to_recording(database, monkeypatch):
    class SlowClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def put(self, *_args, **_kwargs):
            await asyncio.Event().wait()

    class Runtime:
        checks = 0

        def practice_waiting(self):
            self.checks += 1
            return self.checks > 1

    monkeypatch.setattr(youtube.httpx, "AsyncClient", SlowClient)
    with pytest.raises(PracticePreempted):
        asyncio.run(youtube._put_chunk("https://www.googleapis.com/upload/test", {}, b"data", Runtime()))


def test_video_api_exposes_download_and_requires_google_connection_for_upload(client, database):
    ident = lesson_row("api-video")
    target = database / "videos" / "sample.mp4"
    target.write_bytes(b"a small video fixture")
    db.execute("INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
               ("api-export", ident, "", "full", "digest", "ready",
                db.dumps({"mp4": "videos/sample.mp4", "manifest": {"private": "input"},
                          "youtube": {"session": "https://www.googleapis.com/private-session", "state": "uploading"}}),
                time.time(), time.time()))
    lesson = client.get(f"/api/lessons/{ident}")
    assert lesson.status_code == 200 and lesson.json()["videos"][0]["data"]["mp4"] == "videos/sample.mp4"
    assert "session" not in lesson.json()["videos"][0]["data"]["youtube"]
    assert "manifest" not in lesson.json()["videos"][0]["data"]
    download = client.get("/api/files/videos/sample.mp4", headers={"Range": "bytes=0-4"})
    assert download.status_code == 206 and download.content == b"a sma"
    assert client.put("/api/youtube/auto-upload", json={"enabled": True}).status_code == 409
