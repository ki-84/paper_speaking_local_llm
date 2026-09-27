import asyncio
import subprocess
import time
import wave
from urllib.parse import quote

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


def test_speaker_change_adds_one_second_of_silence_without_subtitles(database):
    turns = [
        {"speaker":"host", "scene":"title", "frames":24000, "english":"Paper title.", "japanese":"論文の題名。"},
        {"speaker":"host", "scene":"figure-a", "frames":24000, "english":"Look here.", "japanese":"ここを見てください。"},
        {"speaker":"guide", "scene":"figure-b", "frames":24000, "english":"I see it.", "japanese":"見えました。"},
        {"speaker":"host", "scene":"figure-c", "frames":24000, "english":"Why?", "japanese":"なぜですか。"},
    ]
    silence = video._silence_file(database / "jobs")
    timeline = video._speaker_timeline(turns, silence)
    assert [turn.get("silence", False) for turn in timeline] == [False,False,True,False,True,False]
    assert [turn["scene"] for turn in timeline if turn.get("silence")] == ["figure-a","figure-b"]
    assert video._duration_frames(config.safe_path(silence)) == 24000
    subtitles, ass, duration = video._captions(timeline)
    assert duration == 6
    assert "00:00:03,000 --> 00:00:04,000" in subtitles["en"]
    assert "00:00:05,000 --> 00:00:06,000" in subtitles["ja"]
    assert "00:00:02,000 --> 00:00:03,000" not in subtitles["en"]
    assert ass.count("Dialogue:") == 8


def test_mp4_basename_matches_a_portable_youtube_title():
    title = video.export_title("LoRA: Low-Rank Adaptation of Large Language Models", 1)
    assert title == "LoRA - Low-Rank Adaptation of Large Language Models — 第1章"
    assert video.export_title("A paper", None) == "A paper — 全章"
    long = video.export_title("日本語の長い論文名" * 30, 42)
    assert len(long) <= 100 and len((long + ".mp4").encode()) <= 240
    assert long.endswith(" — 第42章")


def test_title_narration_is_checked_and_precedes_chapter_subtitles(database):
    title = "Today, we will study the paper titled A Small Paper. Let's begin."
    manifest = {"ordinal": 0, "paper_title": "A Small Paper", "intro": {
        "english": title, "japanese": "今日は小さな論文を学びます。",
        "voice": "Aiden", "tts_revision": "test"}}
    job_id = db.enqueue("chapter_video", "title-test")

    class Runtime:
        def speech(self, mode, request):
            if mode == "tts":
                with wave.open(request["output"], "wb") as output:
                    output.setnchannels(1)
                    output.setsampwidth(2)
                    output.setframerate(24000)
                    output.writeframes(b"\0\0" * 48000)
                return {"generation_settings": {"model": "local-test"}}
            return {"text": title, "generation_settings": {"model": "asr-test"}}

    work = database / "jobs" / "title-test"
    work.mkdir()
    assert not video._intro_step(db.one("SELECT * FROM jobs WHERE id=?", (job_id,)), Runtime(), manifest, work)
    assert not video._intro_step(db.one("SELECT * FROM jobs WHERE id=?", (job_id,)), Runtime(), manifest, work)
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    assert video._intro_step(job, Runtime(), manifest, work)
    assert job["checkpoint"]["intro_frames"] == 48000
    assert job["checkpoint"]["intro_transcript"] == title
    tracks, _, duration = video._captions([
        {"frames": 48000, "english": title, "japanese": manifest["intro"]["japanese"]},
        {"frames": 24000, "english": "The first idea.", "japanese": "最初の考えです。"},
    ])
    assert duration == 3
    assert "00:00:00,000 --> 00:00:02,000" in tracks["en"]
    assert "00:00:02,000 --> 00:00:03,000" in tracks["ja"]


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


def test_complete_video_is_queued_in_chapter_order_once_every_video_is_ready(database, monkeypatch):
    ident = lesson_row("auto-join-video")
    for ordinal in range(2):
        db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)",
                   (f"auto-ch{ordinal}", ident, ordinal, "ready", db.dumps({"title": f"Part {ordinal}"})))
    monkeypatch.setattr(video, "chapter_manifest", lambda chapter, lesson: {
        "ordinal": chapter["ordinal"], "version": video.VERSION})
    video.schedule()
    chapters = db.all("SELECT * FROM video_exports WHERE lesson_id=? AND kind='chapter' ORDER BY created,id", (ident,))
    assert len(chapters) == 2
    for chapter in chapters:
        path = database / "videos" / (chapter["id"] + ".mp4")
        path.write_bytes(b"complete chapter")
        db.execute("UPDATE video_exports SET state='ready',data=? WHERE id=?",
                   (db.dumps(chapter["data"] | {"mp4": str(path.relative_to(database))}), chapter["id"]))
    video.schedule()
    video.schedule()
    full = db.all("SELECT * FROM video_exports WHERE lesson_id=? AND kind='full'", (ident,))
    assert len(full) == 1 and full[0]["state"] == "queued"
    assert full[0]["data"]["manifest"]["video_title"] == "Test paper — 全章"
    assert [part["id"] for part in full[0]["data"]["manifest"]["chapter_videos"]] == [
        chapter["id"] for chapter in chapters]


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
    assert config.safe_path(export["data"]["mp4"]).name == "Test paper — 全章.mp4"
    assert export["data"]["title"] == "Test paper — 全章"
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
    named = database / "videos" / "LoRA - paper — 第1章.mp4"
    named.write_bytes(b"chapter file")
    named_download = client.get("/api/files/videos/" + quote(named.name), headers={"Range": "bytes=0-6"})
    assert named_download.status_code == 206 and named_download.content == b"chapter"
    assert client.put("/api/youtube/auto-upload", json={"enabled": True}).status_code == 409
