import asyncio
import subprocess
import time
import wave
from urllib.parse import quote

import httpx
import imageio_ffmpeg
import pytest
from paperspeak import config, db, papers, video, video_overlay, youtube
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


def test_every_sentence_pair_has_one_second_without_subtitles(database):
    turns = [
        {"speaker":"host", "scene":"title", "frames":24000, "english":"Paper title.", "japanese":"論文の題名。"},
        {"speaker":"host", "scene":"figure-a", "frames":24000, "english":"Look here.", "japanese":"ここを見てください。"},
        {"speaker":"guide", "scene":"figure-b", "frames":24000, "english":"I see it.", "japanese":"見えました。"},
        {"speaker":"host", "scene":"figure-c", "frames":24000, "english":"Why?", "japanese":"なぜですか。"},
    ]
    silence = video._silence_file(database / "jobs")
    timeline = video._speaker_timeline(turns, silence)
    assert [turn.get("silence", False) for turn in timeline] == [False,True,False,True,False,True,False]
    assert [turn["scene"] for turn in timeline if turn.get("silence")] == ["title","figure-a","figure-b"]
    assert [turn["pause_kind"] for turn in timeline if turn.get("silence")] == [
        "same_speaker", "speaker_change", "speaker_change"]
    assert video._duration_frames(config.safe_path(silence)) == 24000
    subtitles, ass, duration = video._captions(timeline)
    assert duration == 7
    assert "00:00:02,000 --> 00:00:03,000" in subtitles["en"]
    assert "00:00:04,000 --> 00:00:05,000" in subtitles["ja"]
    assert "00:00:06,000 --> 00:00:07,000" in subtitles["en"]
    assert "00:00:01,000 --> 00:00:02,000" not in subtitles["en"]
    assert "00:00:03,000 --> 00:00:04,000" not in subtitles["ja"]
    assert ass.count("Dialogue:") == 8


def test_long_bilingual_captions_fit_between_the_characters_without_truncation():
    english = ("This careful experiment compares the small update matrices with full fine-tuning "
               "on the same model, task, and training setup, so the chart supports only this narrower claim.")
    japanese = ("この慎重な実験では、同じモデル、同じ課題、同じ学習条件で、小さな更新行列とモデル全体の再学習を比較します。"
                "したがって、この図が裏付けるのは、ここで述べた範囲に限られます。")
    layout = video_overlay.layout_captions(english, japanese)
    assert len(layout["english"]) <= 3 and len(layout["japanese"]) <= 3
    assert " ".join(layout["english"]) == english
    assert "".join(layout["japanese"]) == japanese
    assert all(video_overlay._width(line, layout["en_size"]) <= video_overlay.CAPTION_WIDTH
               for line in layout["english"])
    assert all(video_overlay._width(line, layout["ja_size"]) <= video_overlay.CAPTION_WIDTH
               for line in layout["japanese"])
    assert layout["ja_top"] + len(layout["japanese"]) * layout["ja_size"] * 1.17 <= 1070


def test_japanese_captions_keep_fitting_loanwords_whole_and_preserve_all_text():
    text = "だいたい、とても高価で、しかも非常にぼやけたビデオゲームのキャラクターみたいな見た目です。"
    for size in video_overlay.JA_SIZES:
        lines = video_overlay._wrap_japanese(text, size)
        assert "".join(lines) == text
        assert any("ビデオゲーム" in line for line in lines)
        assert any("キャラクター" in line for line in lines)
        assert all(video_overlay._width(line, size) <= video_overlay.CAPTION_WIDTH for line in lines)
    # A single overlong identifier must not cause a loop or silently lose text.
    for text in ("A" * 150, "カ" * 100, "あ" * 100):
        lines = video_overlay._wrap_japanese(text, 37)
        assert "".join(lines) == text
        assert all(line and video_overlay._width(line, 37) <= video_overlay.CAPTION_WIDTH for line in lines)


def test_wav_lip_sync_closes_during_pauses_and_only_marks_the_speaker(database):
    import numpy as np

    path = database / "audio" / "speech.wav"
    samples = np.zeros(24000 * 3, dtype="<i2")
    first = np.arange(24000, dtype=np.float32)
    samples[24000:48000] = (6500 * np.sin(first * 2 * np.pi * 230 / 24000)).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(samples.tobytes())
    states = video_overlay.mouth_states(path, 72000)
    assert states[0][2] == 0 and states[-1][2] == 0
    assert any(state > 0 and 24000 <= start < 48000 for start, _, state in states)
    config_ = video_overlay.character_manifest()["layout"]
    events = video_overlay.mouth_events("guide", path, 72000, 0, config_)
    assert events and all("\\pos(" in event for event in events)
    assert all(float(event.split(",")[1].rsplit(":", 1)[1]) >= .90 for event in events)
    host_events = video_overlay.mouth_events("host", path, 72000, 0, config_)
    assert events[0] != host_events[0]


def test_animated_mouth_is_on_the_sprites_lip_pixels(tmp_path, monkeypatch):
    import re

    import numpy as np

    scene = tmp_path / "scene.json"
    scene.write_text(db.dumps({"title_card": True, "paper_title": "A paper",
                               "chapter_title_en": "A chapter", "chapter_title_ja": "章", "ordinal": 0}))
    png = tmp_path / "characters.png"
    video._render_slide(scene, png, tmp_path / "characters.partial.png")
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                              "-i", str(png), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             check=True, capture_output=True, timeout=30).stdout
    pixels = np.frombuffer(decoded, dtype=np.uint8).reshape(1080, 1920, 3)
    config_ = video_overlay.character_manifest()["layout"]
    monkeypatch.setattr(video_overlay, "mouth_states", lambda *_: [(0, 24000, 2)])
    for role, color in (("guide", (186, 114, 95)), ("host", (140, 85, 70))):
        x, y, scale = video_overlay._position(config_, role)
        lip_x, lip_y = round(x + 32 * scale), round(y + 38 * scale)
        assert tuple(pixels[lip_y, lip_x]) == color
        dark_event = video_overlay.mouth_events(role, png, 24000, 0, config_)[1]
        match = re.search(r"\\pos\((\d+),(\d+)\)", dark_event)
        assert match and abs(int(match[2]) - lip_y) <= 1
        assert abs(int(match[1]) - round(x + 29 * scale)) <= 1


def test_transient_chromium_capture_retries_without_leaving_partial_file(tmp_path, monkeypatch):
    scene = tmp_path / "scene.json"
    scene.write_text("{}")
    image, partial = tmp_path / "slide.png", tmp_path / "slide.partial.png"
    calls = []

    def render(*args, **kwargs):
        calls.append(1)
        assert not partial.exists()
        partial.write_bytes(b"complete image" * 1000)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(1, args[0], stderr="Capture failed")

    monkeypatch.setattr(video.subprocess, "run", render)
    monkeypatch.setattr(video.time, "sleep", lambda *_: None)
    video._render_slide(scene, image, partial)
    assert len(calls) == 2 and image.stat().st_size > 10000 and not partial.exists()


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
                        "-i", "testsrc2=s=1920x1080:r=30:d=2", "-f", "lavfi",
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
