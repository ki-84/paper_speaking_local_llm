"""Checkpointed, entirely local YouTube-ready exports of checked visual lessons."""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import time
import wave

import imageio_ffmpeg

from . import config, db, translation, video_overlay, visuals, voices, youtube
from .quality import QualityHold, speech_match
from .runtime import PracticePreempted

log = logging.getLogger(__name__)
VERSION = "visual-video-5"
SIZE = (1920, 1080)
FPS = 30
SPEAKER_GAP_FRAMES = 24000
ENCODE = {"video_codec": "libx264", "preset": "medium", "crf": 21,
          "gop_frames": 120, "b_frames": 3, "audio_codec": "aac", "audio_bitrate": "128k"}


def digest(value):
    return hashlib.sha256(db.dumps(value).encode()).hexdigest()


def export_title(paper_title, chapter_number=None):
    """Use the same portable title for the YouTube suggestion and MP4 basename."""
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " - ", str(paper_title or ""))
    title = re.sub(r"\s+", " ", title).strip(" .-") or "Paper lesson"
    suffix = f" — 第{chapter_number}章" if chapter_number is not None else " — 全章"
    source = title
    while len(title + suffix) > 100 or len((title + suffix + ".mp4").encode("utf-8")) > 240:
        title = title[:-1]
    if title != source:
        title = title.rstrip(" .-")
        while len(title + "…" + suffix) > 100 or len((title + "…" + suffix + ".mp4").encode("utf-8")) > 240:
            title = title[:-1]
        title = title.rstrip(" .-") + "…"
    return title + suffix


def file_digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def media_duration(path):
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                            capture_output=True, text=True, timeout=20)
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError("The encoded video has no readable duration.")
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])


def _duration_frames(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 24000, "NONE"):
            raise ValueError("A checked sentence has an unexpected audio format.")
        return wav.getnframes()


def chapter_manifest(chapter, lesson):
    if chapter["state"] != "ready" or not visuals.enabled(lesson) or not visuals.ready(chapter):
        raise ValueError("Only a checked visual chapter can become a video.")
    if not translation.complete(chapter, lesson):
        raise ValueError("Japanese subtitles must pass the meaning check first.")
    assets = {asset["key"]: asset for asset in visuals.assets(chapter)}
    turns = []
    current = next((a["key"] for a in assets.values() if a["kind"] != "original"), next(iter(assets), None))
    for turn in chapter["data"]["turns"]:
        if not turn.get("audio_verified") or not turn.get("audio"):
            raise ValueError("Every sentence needs checked speech before video export.")
        path = config.safe_path(turn["audio"])
        frames = _duration_frames(path)
        japanese = translation.translated(chapter, "turn:" + turn["id"], turn["text"])
        if not japanese:
            raise ValueError("A Japanese sentence is missing.")
        cue = turn.get("visual")
        if cue:
            current = cue["key"]
        turns.append({
            "id": turn["id"], "speaker": turn["speaker"], "english": turn["text"],
            "japanese": japanese, "audio": turn["audio"], "frames": frames,
            "visual_key": current, "focus": cue.get("focus", []) if cue else [],
        })
    if not turns:
        raise ValueError("The chapter has no spoken sentences.")
    paper_title = lesson["data"]["title"]
    intro_line = f"Today, we will study the paper titled {paper_title}. Let's begin."
    return {
        "version": VERSION, "lesson_id": lesson["id"], "chapter_id": chapter["id"],
        "ordinal": chapter["ordinal"], "paper_title": paper_title,
        "video_title": export_title(paper_title, chapter["ordinal"] + 1),
        "chapter_title_en": chapter["data"]["title"],
        "chapter_title_ja": translation.translated(chapter, "title", chapter["data"]["title"]),
        "intro": {"english": intro_line,
                  "caption_en": f"Today, we will study the paper titled\n{paper_title}. Let's begin.",
                  "japanese": f"今日は論文「{paper_title}」を学びます。",
                  "voice": voices.HOST_VOICE, "tts_revision": config.manifest()["models"]["tts"]["revision"]},
        "assets": {key: {"kind": asset["kind"], **{k: asset["data"].get(k)
            for k in ("image_path", "sha256", "label", "page", "title_en", "title_ja",
                      "description_en", "description_ja", "regions", "source_ids")}}
            for key, asset in assets.items()},
        "turns": turns, "format": {"width": SIZE[0], "height": SIZE[1], "fps": FPS,
            "video": "H.264 High yuv420p", "audio": "AAC-LC stereo 48 kHz", "encode": ENCODE,
            "speaker_gap_frames": SPEAKER_GAP_FRAMES},
        "characters": video_overlay.character_manifest(),
        "models_used": lesson["data"].get("models_used", {}),
    }


def _insert_export(lesson_id, chapter_id, kind, manifest):
    input_digest = digest(manifest)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id,state FROM video_exports WHERE lesson_id=? AND chapter_id=? AND input_digest=?",
                                (lesson_id, chapter_id, input_digest)).fetchone()
        if existing:
            return existing["id"]
        ident, now = db.uid(), time.time()
        conn.execute("INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
                     (ident, lesson_id, chapter_id, kind, input_digest, "queued",
                      db.dumps({"manifest": manifest}), now, now))
        db.queue_job(conn, kind + "_video", ident, priority=10)
    db.event("video", {"id": ident, "lesson_id": lesson_id})
    return ident


def schedule():
    """Schedule immutable chapter revisions; join only when *every* chapter is ready."""
    for lesson in db.all("SELECT * FROM lessons WHERE json_extract(data,'$.format')=?", (visuals.FORMAT,)):
        chapters = db.all("SELECT * FROM chapters WHERE lesson_id=? ORDER BY ordinal", (lesson["id"],))
        if not chapters:
            continue
        complete = True
        current = []
        for chapter in chapters:
            if chapter["state"] != "ready":
                complete = False
                continue
            try:
                manifest = chapter_manifest(chapter, lesson)
            except (ValueError, OSError, wave.Error) as exc:
                log.warning("Video for chapter %s held: %s", chapter["id"], exc)
                complete = False
                continue
            ident = _insert_export(lesson["id"], chapter["id"], "chapter", manifest)
            export = db.one("SELECT * FROM video_exports WHERE id=?", (ident,))
            current.append(export)
            if export["state"] != "ready" or not (config.DATA / export["data"].get("mp4", "missing")).is_file():
                complete = False
        if complete and len(current) == len(chapters) and lesson["state"] == "ready":
            manifest = {"version": VERSION, "lesson_id": lesson["id"],
                        "video_title": export_title(lesson["data"]["title"]),
                        "encode": ENCODE,
                        "characters": video_overlay.character_manifest(),
                        "chapter_videos": [{"id": row["id"], "digest": row["input_digest"],
                                             "mp4": row["data"]["mp4"]} for row in current]}
            ident = _insert_export(lesson["id"], "", "full", manifest)
            youtube.schedule(db.one("SELECT * FROM video_exports WHERE id=?", (ident,)))


def _set_export(export, state, **data):
    record = export["data"] | data
    db.execute("UPDATE video_exports SET state=?,data=?,updated=? WHERE id=?",
               (state, db.dumps(record), time.time(), export["id"]))
    db.event("video", {"id": export["id"], "lesson_id": export["lesson_id"], "state": state})


def _work(export):
    path = config.DATA / "jobs" / ("video-" + export["id"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _srt_time(milliseconds):
    h, remain = divmod(milliseconds, 3600000)
    m, remain = divmod(remain, 60000)
    s, ms = divmod(remain, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _ass_time(milliseconds):
    centiseconds = round(milliseconds / 10)
    h, remain = divmod(centiseconds, 360000)
    m, remain = divmod(remain, 6000)
    s, cs = divmod(remain, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def _ass_text(value):
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N").replace("\r", " ")


def _captions(turns, *, animate=False):
    lines = {"en": [], "ja": []}
    events = []
    cumulative = 0
    index = 0
    character_config = video_overlay.character_manifest()["layout"] if animate else None
    for turn in turns:
        start_frame = cumulative
        start = round(cumulative / 24)
        cumulative += turn["frames"]
        end = round(cumulative / 24)
        if turn.get("silence"):
            continue
        index += 1
        layout = video_overlay.layout_captions(turn["english"], turn["japanese"])
        for lang, key in (("en", "english"), ("ja", "japanese")):
            wrapped = "\n".join(layout["english" if lang == "en" else "japanese"])
            lines[lang].append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{wrapped}\n")
            top = layout["en_top" if lang == "en" else "ja_top"]
            size = layout["en_size" if lang == "en" else "ja_size"]
            events.append(f"Dialogue: 10,{_ass_time(start)},{_ass_time(end)},{'English' if lang == 'en' else 'Japanese'},,0,0,0,,"
                          f"{{\\an8\\pos(960,{top})\\fs{size}\\q2}}{_ass_text(wrapped)}")
        if animate:
            role = turn["speaker"]
            if role not in {"host", "guide"}:
                raise ValueError("Video animation has an unknown speaking role.")
            events.extend(video_overlay.highlight_events(role, start, end, character_config))
            events.extend(video_overlay.mouth_events(role, config.safe_path(turn["audio"]),
                                                      turn["frames"], start_frame, character_config))
    if animate:
        events.extend(video_overlay.blink_events(round(cumulative / 24), character_config))
    ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: English,Noto Sans,45,&H00FFFFFF,&H000000FF,&H00332115,&H00000000,1,0,0,0,100,100,0,0,1,2,0,8,280,280,0,1
Style: Japanese,Noto Sans CJK JP,37,&H00E4F6EA,&H000000FF,&H00332115,&H00000000,1,0,0,0,100,100,0,0,1,2,0,8,280,280,0,1
Style: Pixel,Noto Sans,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n"
    return {lang: "\n".join(content) + "\n" for lang, content in lines.items()}, ass, cumulative / 24000


def _quote(path):
    return "'" + str(path).replace("'", "'\\''") + "'"


def _speaker_timeline(turns, silence_path):
    """Keep the previous figure on screen during each one-second speaker change."""
    timeline = []
    previous = None
    for turn in turns:
        if previous and previous["speaker"] != turn["speaker"]:
            timeline.append({"frames": SPEAKER_GAP_FRAMES, "audio": silence_path,
                             "scene": previous["scene"], "silence": True})
        timeline.append(turn)
        previous = turn
    return timeline


def _silence_file(work):
    path = work / "speaker-gap.wav"
    if not path.is_file() or _duration_frames(path) != SPEAKER_GAP_FRAMES:
        partial = work / "speaker-gap.partial.wav"
        try:
            with wave.open(str(partial), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(b"\0\0" * SPEAKER_GAP_FRAMES)
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    return str(path.relative_to(config.DATA))


def _render_slide(scene_path, image, partial):
    """Retry a transient Chromium capture failure without restarting the lesson job."""
    command = [str(config.ROOT / ".tools/node/bin/node"),
               str(config.ROOT / "scripts/render_video_slide.mjs"),
               str(scene_path), str(partial)]
    for attempt in range(3):
        partial.unlink(missing_ok=True)
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=90)
            if not partial.is_file() or partial.stat().st_size < 10000:
                raise RuntimeError("A video figure did not render completely.")
            partial.replace(image)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as error:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                detail = getattr(error, "stderr", None) or str(error)
                raise RuntimeError("Local slide rendering failed after three tries: " + detail[-800:]) from error
            log.warning("Retrying video figure after Chromium capture failed (%s/3).", attempt + 1)
            time.sleep(attempt + 1)


def _ffmpeg(args, runtime, partial):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", "-loglevel", "warning", *args]
    with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) as proc:
        try:
            while proc.poll() is None:
                if runtime.practice_waiting():
                    proc.terminate()
                    proc.wait(timeout=10)
                    raise PracticePreempted("Video paused for your recording.")
                time.sleep(1)
            error = proc.stderr.read()
            if proc.returncode:
                raise RuntimeError("Local video encoding failed: " + error[-1200:])
        except BaseException:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            partial.unlink(missing_ok=True)
            raise


def _intro_step(job, runtime, manifest, work):
    """Generate and independently check the spoken title before encoding."""
    checkpoint = job["checkpoint"]
    path = work / "title.wav"
    intro = manifest["intro"]
    if (not checkpoint.get("intro_audio_ready") and not path.is_file()
            and manifest.get("lesson_id") and manifest.get("chapter_id")):
        for prior in db.all("SELECT * FROM video_exports WHERE lesson_id=? AND chapter_id=? AND kind='chapter' ORDER BY created DESC",
                            (manifest["lesson_id"], manifest["chapter_id"])):
            if prior["id"] == job["target"] or prior["data"].get("manifest", {}).get("intro") != intro:
                continue
            older = db.one("SELECT checkpoint FROM jobs WHERE kind='chapter_video' AND target=? ORDER BY created DESC LIMIT 1",
                           (prior["id"],))
            old_path = config.DATA / "jobs" / ("video-" + prior["id"]) / "title.wav"
            old_cp = older["checkpoint"] if older else {}
            if not old_cp.get("intro_audio_verified") or not old_path.is_file():
                continue
            shutil.copy2(old_path, path)
            frames = _duration_frames(path)
            if frames != old_cp.get("intro_frames"):
                path.unlink(missing_ok=True)
                continue
            checkpoint = checkpoint | {key: old_cp[key] for key in
                ("intro_audio_ready", "intro_audio_verified", "intro_frames", "intro_transcript",
                 "intro_wer", "intro_tts_settings", "intro_asr_settings") if key in old_cp}
            db.patch_job(job["id"], stage="Reusing checked spoken title", progress=0.1,
                         checkpoint=checkpoint)
            return False
    if not checkpoint.get("intro_audio_ready") or not path.is_file():
        attempt = checkpoint.get("intro_attempts", 0)
        seed = (int(digest([intro["english"], intro["tts_revision"]])[:8], 16) + attempt) % (2**31)
        partial = work / "title.partial.wav"
        partial.unlink(missing_ok=True)
        try:
            result = runtime.speech("tts", {"text": intro["english"], "voice": intro["voice"],
                                            "seed": seed, "output": str(partial)})
            frames = _duration_frames(partial)
            if frames < 24000:
                raise ValueError("The spoken title is too short.")
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
        checkpoint = checkpoint | {"intro_audio_ready": True, "intro_audio_verified": False,
                                   "intro_frames": frames, "intro_tts_settings": result.get("generation_settings", {})}
        db.patch_job(job["id"], stage=f"Speaking the title of chapter {manifest['ordinal'] + 1}",
                     progress=0.05, checkpoint=checkpoint)
        return False
    if not checkpoint.get("intro_audio_verified"):
        result = runtime.speech("asr", {"audio": str(path), "context": manifest["paper_title"]})
        diff, acceptable = speech_match(intro["english"], result["text"])
        if not acceptable:
            attempts = checkpoint.get("intro_attempts", 0) + 1
            path.unlink(missing_ok=True)
            checkpoint = checkpoint | {"intro_attempts": attempts, "intro_audio_ready": False,
                                       "intro_transcript": result["text"], "intro_wer": diff["wer"]}
            db.patch_job(job["id"], stage="Trying the spoken title again", progress=0.05,
                         checkpoint=checkpoint)
            if attempts >= 3:
                raise QualityHold("The spoken paper title did not match the script after three attempts.")
            return False
        checkpoint = checkpoint | {"intro_audio_verified": True, "intro_transcript": result["text"],
                                   "intro_wer": diff["wer"],
                                   "intro_asr_settings": result.get("generation_settings", {})}
        db.patch_job(job["id"], stage="Spoken paper title checked", progress=0.1,
                     checkpoint=checkpoint)
        return False
    return True


def _chapter_step(job, runtime, export):
    manifest = export["data"]["manifest"]
    work = _work(export)
    if not _intro_step(job, runtime, manifest, work):
        return False
    checkpoint = db.one("SELECT checkpoint FROM jobs WHERE id=?", (job["id"],))["checkpoint"]
    intro_turn = {"id": "title", "speaker": "host", "english": manifest["intro"]["caption_en"],
                  "japanese": manifest["intro"]["japanese"],
                  "audio": str((work / "title.wav").relative_to(config.DATA)),
                  "frames": checkpoint["intro_frames"], "title_card": True}
    turns, assets = [intro_turn, *manifest["turns"]], manifest["assets"]
    scenes = {}
    for turn in turns:
        if turn.get("title_card"):
            turn["scene"] = "title-card"
            scenes.setdefault("title-card", {"title_card": True, "asset": None, "focus": []})
        else:
            key = digest([turn["visual_key"], turn["focus"]])[:16]
            turn["scene"] = key
            scenes.setdefault(key, {"asset": assets.get(turn["visual_key"]), "focus": turn["focus"]})
    turns = _speaker_timeline(turns, _silence_file(work))
    scene_items = list(scenes.items())
    index = job["checkpoint"].get("scene_index", 0)
    if index < len(scene_items):
        key, scene = scene_items[index]
        image = work / (key + ".png")
        if not image.is_file():
            scene_path = work / (key + ".json")
            partial_image = work / (key + ".partial.png")
            scene_path.write_text(db.dumps({"paper_title": manifest["paper_title"],
                "chapter_title_en": manifest["chapter_title_en"],
                "chapter_title_ja": manifest["chapter_title_ja"],
                "ordinal": manifest["ordinal"], "data_root": str(config.DATA), **scene}), encoding="utf-8")
            _render_slide(scene_path, image, partial_image)
        cp = job["checkpoint"] | {"scene_index": index + 1}
        db.patch_job(job["id"], stage=f"Drawing figure {index + 1}/{len(scene_items)} for chapter {manifest['ordinal'] + 1}",
                     progress=(index + 1) / (len(scene_items) + 2), checkpoint=cp)
        return False

    output_dir = config.DATA / "videos" / export["lesson_id"] / export["input_digest"][:12]
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = manifest["video_title"]
    paths = {"mp4": output_dir / (stem + ".mp4"), "en_srt": output_dir / (stem + ".en.srt"),
             "ja_srt": output_dir / (stem + ".ja.srt")}
    captions, ass, duration = _captions(turns, animate=True)
    paths["en_srt"].write_text(captions["en"], encoding="utf-8")
    paths["ja_srt"].write_text(captions["ja"], encoding="utf-8")
    ass_path = work / "bilingual.ass"
    ass_path.write_text(ass, encoding="utf-8")
    video_list = work / "scenes.ffconcat"
    audio_list = work / "speech.ffconcat"
    video_list.write_text("ffconcat version 1.0\n" + "".join(
        f"file {_quote(work / (turn['scene'] + '.png'))}\nduration {turn['frames'] / 24000:.9f}\n"
        for turn in turns) + f"file {_quote(work / (turns[-1]['scene'] + '.png'))}\n", encoding="utf-8")
    audio_list.write_text("ffconcat version 1.0\n" + "".join(
        f"file {_quote(config.safe_path(turn['audio']))}\n" for turn in turns), encoding="utf-8")
    partial = work / "video.partial.mp4"
    db.patch_job(job["id"], stage=f"Encoding chapter {manifest['ordinal'] + 1} with burned English and Japanese subtitles",
                 progress=0.9)
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(video_list),
             "-f", "concat", "-safe", "0", "-i", str(audio_list),
             "-vf", f"fps={FPS},scale={SIZE[0]}:{SIZE[1]},format=yuv420p,ass={ass_path}",
             "-c:v", "libx264", "-preset", str(ENCODE["preset"]), "-crf", str(ENCODE["crf"]), "-profile:v", "high",
             "-g", str(ENCODE["gop_frames"]), "-bf", str(ENCODE["b_frames"]), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", str(ENCODE["audio_bitrate"]), "-ar", "48000", "-ac", "2",
             "-movflags", "+faststart", "-shortest", str(partial)], runtime, partial)
    if not partial.is_file() or partial.stat().st_size < 100000:
        raise RuntimeError("The encoded video is empty.")
    actual_duration = media_duration(partial)
    if abs(actual_duration - duration) > 0.15:
        raise RuntimeError(f"Video/audio length mismatch: {actual_duration:.2f}s vs {duration:.2f}s")
    partial.replace(paths["mp4"])
    _set_export(export, "ready", **{name: str(path.relative_to(config.DATA)) for name, path in paths.items()},
                title=manifest["video_title"], encode_settings=ENCODE,
                characters=manifest["characters"],
                duration=duration, media_duration=actual_duration, bytes=paths["mp4"].stat().st_size,
                sha256=file_digest(paths["mp4"]),
                title_narration={"text": manifest["intro"]["english"],
                                 "transcript": checkpoint["intro_transcript"],
                                 "duration": checkpoint["intro_frames"] / 24000,
                                 "tts_settings": checkpoint.get("intro_tts_settings", {}),
                                 "asr_settings": checkpoint.get("intro_asr_settings", {})},
                encoder=imageio_ffmpeg.get_ffmpeg_version())
    db.patch_job(job["id"], stage=f"Chapter {manifest['ordinal'] + 1} video ready", progress=1)
    return True


def _full_step(job, runtime, export):
    manifest = export["data"]["manifest"]
    chapters = [db.one("SELECT * FROM video_exports WHERE id=?", (item["id"],)) for item in manifest["chapter_videos"]]
    if any(not item or item["state"] != "ready" or item["input_digest"] != expected["digest"]
           or (manifest.get("version") and
               item["data"].get("manifest", {}).get("version") != manifest["version"])
           for item, expected in zip(chapters, manifest["chapter_videos"])):
        raise ValueError("All chapter videos must be ready before joining them.")
    work = _work(export)
    output_dir = config.DATA / "videos" / export["lesson_id"] / export["input_digest"][:12]
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = manifest.get("video_title") or export_title(
        db.one("SELECT * FROM lessons WHERE id=?", (export["lesson_id"],))["data"]["title"])
    paths = {"mp4": output_dir / (stem + ".mp4"), "en_srt": output_dir / (stem + ".en.srt"),
             "ja_srt": output_dir / (stem + ".ja.srt")}
    playlist = work / "chapters.ffconcat"
    playlist.write_text("ffconcat version 1.0\n" + "".join(
        f"file {_quote(config.safe_path(chapter['data']['mp4']))}\n" for chapter in chapters), encoding="utf-8")
    for lang in ("en", "ja"):
        pieces, offset, count = [], 0, 0
        for chapter in chapters:
            content = config.safe_path(chapter["data"][lang + "_srt"]).read_text(encoding="utf-8")
            for match in re.finditer(r"(?m)^(\d+)\n(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})\n([\s\S]*?)(?=\n\n|\Z)", content):
                def millis(value):
                    h, m, rest = value.split(":")
                    s, ms = rest.split(",")
                    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)
                count += 1
                pieces.append(f"{count}\n{_srt_time(offset + millis(match[2]))} --> {_srt_time(offset + millis(match[3]))}\n{match[4].strip()}\n")
            offset += round(chapter["data"].get("media_duration", chapter["data"]["duration"]) * 1000)
        paths[lang + "_srt"].write_text("\n".join(pieces) + "\n", encoding="utf-8")
    partial = work / "complete.partial.mp4"
    db.patch_job(job["id"], stage="Joining every chapter into one upload video", progress=0.7)
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(playlist),
             "-c", "copy", "-movflags", "+faststart", str(partial)], runtime, partial)
    if not partial.is_file() or partial.stat().st_size < 100000:
        raise RuntimeError("The complete video is empty.")
    expected_duration = sum(chapter["data"].get("media_duration", chapter["data"]["duration"]) for chapter in chapters)
    actual_duration = media_duration(partial)
    if abs(actual_duration - expected_duration) > max(2, 0.06 * len(chapters)):
        raise RuntimeError("A chapter is missing or audio/video length changed in the complete video.")
    partial.replace(paths["mp4"])
    _set_export(export, "ready", **{name: str(path.relative_to(config.DATA)) for name, path in paths.items()},
                title=stem, encode_settings=ENCODE,
                duration=expected_duration, media_duration=actual_duration,
                chapter_count=len(chapters), bytes=paths["mp4"].stat().st_size,
                sha256=file_digest(paths["mp4"]),
                encoder=imageio_ffmpeg.get_ffmpeg_version())
    db.patch_job(job["id"], stage="Complete upload video ready", progress=1)
    return True


def video_step(job, runtime):
    export = db.one("SELECT * FROM video_exports WHERE id=?", (job["target"],))
    if not export:
        raise ValueError("Video export not found")
    if export["state"] == "ready" and config.safe_path(export["data"]["mp4"]).is_file():
        return True
    if export["state"] != "running":
        _set_export(export, "running")
    if job["kind"] == "chapter_video":
        return _chapter_step(job, runtime, export)
    return _full_step(job, runtime, export)
