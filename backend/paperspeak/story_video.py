"""Local bilingual story-film renderer, immutable manifests and finite work units."""

from __future__ import annotations

import json
import re
import subprocess
import time
import wave

import imageio_ffmpeg

from . import config, db, publication, video, video_overlay

VERSION = "story-film-3-original-panels"
RELEASE_VERSION = "verified-video-packaging-1"


def portable_title(title):
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " - ", title)
    result = re.sub(r"\s+", " ", result).strip(" .-") or "PaperSpeak"
    while len(result) > 100 or len((result + ".mp4").encode()) > 240:
        result = result[:-1]
    return result.strip(" .-")


def render_scene(project, mode, index):
    track = project["data"]["modes"][mode]
    scene = track["scenes"][index]
    original = None
    if (
        scene["visual"].get("type") == "original" or scene["visual"].get("image_path")
    ) and not scene["visual"].get("original_asset_id"):
        raise ValueError("Only a checked original figure from this paper may be used")
    if scene["visual"].get("original_asset_id"):
        original = db.one(
            "SELECT * FROM visual_assets WHERE id=? AND paper_id=? AND kind='original'",
            (scene["visual"]["original_asset_id"], project["paper_id"]),
        )
        seen = set()
        while (
            original
            and original["data"].get("story_spec")
            and original["id"] not in seen
        ):
            seen.add(original["id"])
            ancestor = original["data"]["story_spec"].get("original_asset_id")
            original = (
                db.one(
                    "SELECT * FROM visual_assets WHERE id=? AND paper_id=? AND kind='original'",
                    (ancestor, project["paper_id"]),
                )
                if ancestor
                else None
            )
        if not original or not original["data"].get("review", {}).get("passed"):
            raise ValueError(
                "Only a checked original figure from this paper may be used"
            )
        scene["visual"].update(
            type="original",
            image_path=original["data"]["image_path"],
            original_asset_id=original["id"],
            original_source={
                "label": original["data"].get("label", "Paper figure"),
                "page": original["data"].get("page"),
            },
        )
    zoom_cues, zoom_regions = {}, []
    if original:
        regions = original["data"].get("regions", [])
        for ui, u in enumerate(scene["utterances"]):
            region = None
            region_id = u.get("visual_focus_region")
            if region_id:
                region = next((r for r in regions if r.get("id") == region_id), None)
                if region is None:
                    raise ValueError(
                        "Unknown verified original figure region: " + str(region_id)
                    )
            elif re.search(r"heat[ -]?map", u["text"], re.I):
                region = next(
                    (r for r in regions if "zoom" in r.get("label_en", "").lower()),
                    None,
                )
            if region:
                if region not in zoom_regions:
                    zoom_regions.append(region)
                zoom_cues[ui] = zoom_regions.index(region)
    elif any(u.get("visual_focus_region") for u in scene["utterances"]):
        raise ValueError("A verified region cue needs a checked original figure")
    focus_count = max(
        1,
        len(scene["visual"].get("nodes", [])),
        len(scene["visual"].get("equations", []))
        + (1 if original and scene["visual"].get("equations") else 0),
    )
    scene["visual"].update(zoom_regions=zoom_regions, zoom_start=focus_count)
    renderer_hash = video.file_digest(config.ROOT / "scripts/render_story_slide.mjs")
    key = video.digest(
        [
            VERSION,
            renderer_hash,
            project["id"],
            mode,
            index,
            scene["visual"],
            scene["title"],
            scene["title_ja"],
        ]
    )
    root = config.DATA / "visuals" / project["id"] / mode / key[:12]
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for focus in range(focus_count + len(zoom_regions)):
        path = root / f"step-{focus}.png"
        if not path.is_file() or not (root / f"study-{focus}.png").is_file():
            spec_path = root / f"step-{focus}.json"
            spec_path.write_text(
                db.dumps(
                    {
                        "title_en": scene["title"],
                        "title_ja": scene["title_ja"],
                        "mode": mode,
                        "focus": focus,
                        "visual": scene["visual"],
                        "data_root": str(config.DATA),
                        "study_output": str(root / f"study-{focus}.png"),
                    }
                ),
                encoding="utf-8",
            )
            _render(spec_path, path)
        paths.append(str(path.relative_to(config.DATA)))
    asset_id = key[:32]
    asset_data = (dict(original["data"]) if original else {}) | {
        "key": "scene",
        "title_en": scene["title"],
        "title_ja": scene["title_ja"],
        "description_en": scene["visual"].get("caption_en", scene["focus"]),
        "description_ja": scene["visual"].get("caption_ja", scene["title_ja"]),
        "source_ids": list(
            dict.fromkeys(
                sid for u in scene["utterances"] for sid in u.get("source_ids", [])
            )
        ),
        "regions": [],
        "story_spec": scene["visual"],
        "review": {
            "passed": not scene.get("omissions"),
            "records": scene.get("reviews", {}),
        },
    }
    focus_assets = []
    for focus in range(len(paths)):
        ident = asset_id if focus == 0 else video.digest([key, focus])[:32]
        image = root / f"study-{focus}.png"
        kind = (
            "original"
            if original
            and (
                focus == 0
                or focus >= focus_count
                or not scene["visual"].get("equations")
            )
            else "example"
            if scene["visual"]["type"] == "example"
            else "teaching"
        )
        data = asset_data | {
            "key": f"scene-{focus}",
            "image_path": str(image.relative_to(config.DATA)),
            "sha256": video.file_digest(image),
            "source_original_asset_id": original["id"] if original else None,
            "focus_step": focus,
        }
        db.execute(
            "INSERT OR IGNORE INTO visual_assets VALUES (?,?,?,?,?,?)",
            (
                ident,
                project["paper_id"],
                track["lesson_id"],
                kind,
                db.dumps(data),
                time.time(),
            ),
        )
        focus_assets.append({"key": f"scene-{focus}", "asset_id": ident})
    scene.update(
        render_paths=paths,
        asset_id=asset_id,
        focus_assets=focus_assets,
        renderer_sha256=renderer_hash,
    )
    for ui, u in enumerate(scene["utterances"]):
        focus = u.get("visual_focus")
        if not isinstance(focus, int) or not 0 <= focus < len(paths):
            labels = [n.get("en", "") for n in scene["visual"].get("nodes", [])]
            labels += [e.get("en", "") for e in scene["visual"].get("equations", [])][
                len(labels) :
            ]
            text = set(re.findall(r"[a-z]{3,}", u["text"].lower()))
            scores = [
                len(text & set(re.findall(r"[a-z]{3,}", label.lower())))
                for label in labels
            ]
            focus = (
                max(range(len(scores)), key=scores.__getitem__)
                if scores and max(scores)
                else min(len(paths) - 1, ui * len(paths) // len(scene["utterances"]))
            )
            u["visual_focus_method"] = (
                "label match" if scores and max(scores) else "progressive fallback"
            )
        else:
            u["visual_focus_method"] = "script cue"
        if (
            original
            and scene["visual"].get("equations")
            and focus == 0
            and re.search(
                r"\b(?:equation|factorization|multiply|matrix [AB]|delta W|W-zero)\b",
                u["text"],
                re.I,
            )
        ):
            focus = min(1, len(paths) - 1)
            u["visual_focus_method"] = "explicit mathematical explanation"
        if ui in zoom_cues:
            focus = focus_count + zoom_cues[ui]
            u["visual_focus_method"] = "explicit panel cue, verified source region"
        u["visual_focus"] = focus


def _render(input_path, output_path):
    partial = output_path.with_suffix(".partial.png")
    command = [
        str(config.ROOT / ".tools/node/bin/node"),
        str(config.ROOT / "scripts/render_story_slide.mjs"),
        str(input_path),
        str(partial),
    ]
    for attempt in range(3):
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=90)
            if not partial.is_file() or partial.stat().st_size < 5000:
                raise ValueError("An empty story scene was rendered")
            partial.replace(output_path)
            return
        except (subprocess.SubprocessError, ValueError):
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise


def enqueue(project, mode, *, preview=False):
    publication.prepare(project)
    track = project["data"]["modes"][mode]
    scenes = track["scenes"][:1] if preview else track["scenes"]
    kind = mode + ("_preview" if preview else "")
    manifest = {
        "version": VERSION,
        "release_policy": RELEASE_VERSION,
        "duration_policy": track.get("duration_policy", {}),
        "project_id": project["id"],
        "mode": mode,
        "preview": preview,
        "renderer_sha256": video.file_digest(
            config.ROOT / "scripts/render_story_slide.mjs"
        ),
        "title": portable_title(track["packaging"]["title"]),
        "packaging": track["packaging"],
        "characters": video_overlay.character_manifest(),
        "encode": video.ENCODE,
        "source_version": db.one(
            "SELECT version FROM papers WHERE id=?", (project["paper_id"],)
        )["version"],
        "scenes": [
            {
                "title": s["title"],
                "title_ja": s["title_ja"],
                "visual": s["visual"],
                "render_paths": s["render_paths"],
                "utterances": s["utterances"],
                "subtitle_items": s["subtitle_items"],
                "reviews": s.get("reviews", {}),
                "omissions": s.get("omissions", []),
            }
            for s in scenes
        ],
    }
    fingerprint = video.digest(manifest)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM video_exports WHERE lesson_id=? AND chapter_id='' AND input_digest=?",
            (track["lesson_id"], fingerprint),
        ).fetchone()
        if existing:
            return existing["id"]
        ident, now = db.uid(), time.time()
        conn.execute(
            "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ident,
                track["lesson_id"],
                "",
                kind,
                fingerprint,
                "queued",
                db.dumps({"manifest": manifest}),
                now,
                now,
            ),
        )
        job_id = db.queue_job(conn, "story_video", ident, priority=9)
        parent = conn.execute(
            "SELECT state FROM jobs WHERE kind='video_project' AND target=? AND state IN ('paused','cancelled')",
            (project["id"],),
        ).fetchone()
        if parent:
            conn.execute(
                "UPDATE jobs SET state=? WHERE id=?", (parent["state"], job_id)
            )
    db.event("video", {"id": ident, "lesson_id": track["lesson_id"]})
    return ident


def silence_file(root, frames):
    path = root / f"pause-{frames}.wav"
    if not path.is_file():
        partial = path.with_suffix(".partial.wav")
        with wave.open(str(partial), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\0\0" * frames)
        partial.replace(path)
    return str(path.relative_to(config.DATA))


def timeline(manifest, root):
    """Paragraph audio is continuous; captions use slices of that same WAV."""
    from .story import clip_audio

    speech, captions, starts, animations = [], [], [], []
    previous = None
    frame = 0
    for index, scene in enumerate(manifest["scenes"]):
        starts.append((frame / 24000, scene["title_ja"]))
        for ui, u in enumerate(scene["utterances"]):
            focus = u.get(
                "visual_focus",
                ui * len(scene["render_paths"]) // max(1, len(scene["utterances"])),
            )
            image = scene["render_paths"][
                min(len(scene["render_paths"]) - 1, max(0, focus))
            ]
            if previous:
                frames = pause_frames(previous, u, ui == 0, scene["visual"]["type"])
                if frames:
                    pause = {
                        "audio": silence_file(root, frames),
                        "frames": frames,
                        "silence": True,
                        "scene": speech[-1]["scene"],
                    }
                    speech.append(pause)
                    captions.append(pause)
                    frame += frames
            total = video._duration_frames(config.safe_path(u["audio"]))
            speech.append({"audio": u["audio"], "frames": total, "scene": image})
            ranges = u.get("caption_ranges", u["sentence_ranges"])
            used = 0
            for si, (text, start, end) in enumerate(ranges):
                # Last clip takes rounding remainder; no sample is lost at sentence boundaries.
                n = total - used if si == len(ranges) - 1 else round(end * 24000) - used
                path = root / f"{u['id']}-{si}.wav"
                if not path.is_file():
                    clip_audio(
                        config.safe_path(u["audio"]),
                        path,
                        used / 24000,
                        (used + n) / 24000,
                    )
                captions.append(
                    {
                        "english": text,
                        "japanese": scene["subtitle_items"][f"{ui}:{si}"]["japanese"],
                        "speaker": u["speaker"],
                        "audio": str(path.relative_to(config.DATA)),
                        "frames": n,
                    }
                )
                used += n
            animations.extend(
                _diagram_events(scene["visual"], frame / 24000, total / 24000)
            )
            frame += total
            previous = u
    return speech, captions, starts, animations


def pause_frames(previous, u, scene_boundary, visual_type):
    gap = 0.6 if previous["speaker"] != u["speaker"] else 0.3
    if scene_boundary:
        gap = 1.5 if visual_type in {"equation", "matrix"} else 1.0
    stamps = previous.get("audio_check", {}).get("timestamps", [])
    trailing = max(0, previous["duration"] - stamps[-1]["end"]) if stamps else 0
    return max(0, round((gap - min(gap, trailing)) * 24000))


def spoken_duration(scenes):
    frames = 0
    previous = None
    for scene in scenes:
        for ui, u in enumerate(scene["utterances"]):
            if previous:
                frames += pause_frames(previous, u, ui == 0, scene["visual"]["type"])
            frames += video._duration_frames(config.safe_path(u["audio"]))
            previous = u
    return frames / 24000


def _diagram_events(spec, start, duration):
    """A moving signal illustrates directed flow; no movement on measured charts."""
    if (
        spec["type"] not in {"flow", "timeline", "matrix"}
        or len(spec.get("nodes", [])) < 2
    ):
        return []
    count = len(spec["nodes"])
    cols = min(count, 3)
    width = (1720 - (cols - 1) * 40) / cols
    y = 339
    x1, x2 = 100 + width, 100 + width + 40
    events = []
    for offset in range(0, max(1, int(duration)), 4):
        end = min(start + duration, start + offset + 1.5)
        if end <= start + offset:
            continue
        events.append(
            f"Dialogue: 3,{video._ass_time(round((start + offset) * 1000))},{video._ass_time(round(end * 1000))},Pixel,,0,0,0,,"
            f"{{\\an7\\move({round(x1)},{y},{round(x2)},{y},0,1200)\\c&H50B9F4&\\p1}}m 0 0 l 12 0 l 12 12 l 0 12{{\\p0}}"
        )
    return events


def _thumbnail(manifest, work):
    from . import thumbnails

    project = _export_project(manifest)
    chosen = thumbnails.ensure_labels(project, manifest["mode"]) or thumbnails.fallback(
        project, manifest["mode"]
    )
    return config.safe_path(chosen["png"])


def _export_project(manifest):
    project = db.one(
        "SELECT * FROM video_projects WHERE id=?", (manifest["project_id"],)
    )
    if not project:
        raise ValueError("Video project not found")
    project["data"]["modes"][manifest["mode"]].setdefault(
        "packaging", manifest["packaging"]
    )
    publication.prepare(project)
    return project


def finalize_packaging(export_id, *, project=None):
    """Check actual download artifacts and repair copy/posters without reencoding."""
    from . import thumbnails

    export = db.one("SELECT * FROM video_exports WHERE id=?", (export_id,))
    manifest = export["data"]["manifest"]
    mode = manifest["mode"]
    project = project or _export_project(manifest)
    publication.prepare(project)
    if (
        not export["data"].get("mp4")
        or not config.safe_path(export["data"]["mp4"]).is_file()
    ):
        raise ValueError("The video file must exist before its release check")
    repairs = publication.description_issues(
        project, mode, export["data"].get("description", "")
    )
    if not export["data"].get("description_file"):
        export["data"]["description_file"] = str(
            config.safe_path(export["data"]["mp4"])
            .with_name("description.txt")
            .relative_to(config.DATA)
        )
    publication.refresh_export(project, mode, export)
    chosen = thumbnails.ensure_labels(project, mode)
    # A correct fixed poster permits finished films and English practice while
    # the image-model job is still making its three richer alternatives.
    if not chosen:
        chosen = thumbnails.fallback(project, mode)
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT data FROM video_exports WHERE id=?", (export_id,)
        ).fetchone()
        record = json.loads(current["data"])
        current_candidate = {
            "png": record.get("thumbnail", ""),
            "jpg": record.get("thumbnail_jpg")
            or str(
                config.safe_path(record.get("thumbnail", "missing.png"))
                .with_suffix(".jpg")
                .relative_to(config.DATA)
            ),
        }
        issues = thumbnails.candidate_issues(project, mode, current_candidate)
        if issues:
            repairs += issues
            prior = record.get("thumbnail")
            if prior and prior != chosen["png"]:
                history = record.setdefault("thumbnail_history", [])
                if prior not in history:
                    history.append(prior)
            record.update(thumbnail=chosen["png"], thumbnail_jpg=chosen["jpg"])
            if "id" not in chosen:
                record.pop("thumbnail_set_id", None)
                record.pop("thumbnail_candidate_id", None)
        else:
            record["thumbnail_jpg"] = current_candidate["jpg"]
        problems = publication.description_issues(
            project, mode, record["description"]
        ) + thumbnails.candidate_issues(
            project, mode, {"png": record["thumbnail"], "jpg": record["thumbnail_jpg"]}
        )
        if (
            not record.get("description_file")
            or config.safe_path(record["description_file"]).read_text()
            != record["description"]
        ):
            problems.append("Description download does not match its displayed copy")
        if problems:
            raise ValueError("Video release check failed: " + "; ".join(problems))
        record["release_check"] = {
            "version": RELEASE_VERSION,
            "status": "passed",
            "time": time.time(),
            "identity": publication.identity(project, mode),
            "description_url_free": True,
            "thumbnail_sha256": video.file_digest(
                config.safe_path(record["thumbnail"])
            ),
            "repairs": repairs,
        }
        record.setdefault(
            "completed_at", config.safe_path(record["mp4"]).stat().st_mtime
        )
        conn.execute(
            "UPDATE video_exports SET state='ready',data=?,updated=? WHERE id=?",
            (db.dumps(record), time.time(), export_id),
        )
    db.execute(
        "UPDATE video_projects SET data=?,updated=? WHERE id=?",
        (db.dumps(project["data"]), time.time(), project["id"]),
    )
    db.event(
        "video", {"id": export_id, "lesson_id": export["lesson_id"], "state": "ready"}
    )
    return record["release_check"]


def step(job, runtime):
    export = db.one("SELECT * FROM video_exports WHERE id=?", (job["target"],))
    if not export:
        raise ValueError("Story export not found")
    if export["state"] == "ready":
        finalize_packaging(export["id"])
        return True
    manifest = export["data"]["manifest"]
    work = config.DATA / "jobs" / ("story-video-" + export["id"])
    work.mkdir(parents=True, exist_ok=True)
    speech, captions, starts, animation = timeline(manifest, work)
    preview = manifest["preview"]
    if preview:
        remaining = 90 * 24000
        clipped = []
        for cue in captions:
            if remaining <= 0:
                break
            if cue["frames"] > remaining and clipped:
                break
            n = min(remaining, cue["frames"])
            clipped.append(cue | {"frames": n})
            remaining -= n
        captions = clipped
    tracks, ass, expected = video._captions(captions, animate=True)
    ass += "\n".join(animation) + "\n"
    duration = min(90, expected) if preview else expected
    output = config.DATA / "videos" / export["lesson_id"] / export["input_digest"][:12]
    output.mkdir(parents=True, exist_ok=True)
    title = manifest["title"] + (" — Preview" if preview else "")
    paths = {
        "mp4": output / (title + ".mp4"),
        "en_srt": output / (title + ".en.srt"),
        "ja_srt": output / (title + ".ja.srt"),
    }
    # Encode finite 60-second segments so recording priority/restarts never discard a whole film.
    ass_path = work / "captions.ass"
    ass_path.write_text(ass, encoding="utf-8")
    image_list = work / "images.ffconcat"
    image_list.write_text(
        "ffconcat version 1.0\n"
        + "".join(
            f"file {video._quote(config.safe_path(t['scene']))}\nduration {t['frames'] / 24000:.9f}\n"
            for t in speech
        )
        + f"file {video._quote(config.safe_path(speech[-1]['scene']))}\n",
        encoding="utf-8",
    )
    audio_list = work / "audio.ffconcat"
    audio_list.write_text(
        "ffconcat version 1.0\n"
        + "".join(
            f"file {video._quote(config.safe_path(t['audio']))}\n" for t in speech
        ),
        encoding="utf-8",
    )
    parts = math_ceil(duration / 60)
    for index in range(parts):
        part = work / f"part-{index:03}.mp4"
        if part.is_file():
            continue
        partial = work / f"part-{index:03}.partial.mp4"
        start = index * 60
        length = min(60, duration - start)
        db.patch_job(
            job["id"],
            stage=f"Rendering {manifest['mode']} {'preview' if preview else 'film'} · {index + 1}/{parts}",
            progress=index / parts,
        )
        # Timestamp shift after ASS evaluation keeps all cues on the master timeline.
        video._ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(image_list),
                "-ss",
                str(start),
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(audio_list),
                "-vf",
                # Dense frames before trimming retain the image covering a seek point.
                # Seeking the sparse PNG concat directly can skip to the next paragraph,
                # shifting both ASS captions and the visible diagram by many seconds.
                f"fps=30,trim=start={start}:duration={length},setpts=PTS-STARTPTS,format=yuv420p,setpts=PTS+{start}/TB,ass={ass_path},setpts=PTS-STARTPTS",
                "-t",
                f"{length:.6f}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "21",
                "-profile:v",
                "high",
                "-g",
                "120",
                "-bf",
                "3",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                str(partial),
            ],
            runtime,
            partial,
        )
        if abs(video.media_duration(partial) - length) > 0.2:
            partial.unlink(missing_ok=True)
            raise ValueError("A story segment has a duration mismatch")
        partial.replace(part)
        db.patch_job(
            job["id"],
            stage=f"Rendered {index + 1}/{parts} story segments",
            progress=(index + 1) / parts,
        )
        return False
    concat = work / "parts.ffconcat"
    concat.write_text(
        "ffconcat version 1.0\n"
        + "".join(
            f"file {video._quote(work / f'part-{i:03}.mp4')}\n" for i in range(parts)
        ),
        encoding="utf-8",
    )
    partial = output / "joined.partial.mp4"
    video._ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(partial),
        ],
        runtime,
        partial,
    )
    actual = video.media_duration(partial)
    if abs(actual - duration) > max(0.3, parts * 0.05):
        partial.unlink(missing_ok=True)
        raise ValueError("Final story audio/video duration mismatch")
    partial.replace(paths["mp4"])
    for lang in ("en", "ja"):
        paths[lang + "_srt"].write_text(tracks[lang], encoding="utf-8")
    thumbnail = _thumbnail(manifest, work)

    def stamp(seconds):
        seconds = round(seconds)
        return f"{seconds // 60}:{seconds % 60:02}"

    project = _export_project(manifest)
    description = publication.without_urls(
        publication.description(project, manifest["mode"])
        + "\n\n"
        + "\n".join(stamp(t) + " " + label for t, label in starts if t < duration)
    )
    description_file = output / "description.txt"
    description_file.write_text(description, encoding="utf-8")
    acceptance = {
        "version": VERSION,
        "preview": preview,
        "burned_subtitles": ["en", "ja"],
        "resolution": [1920, 1080],
        "duration_error_s": round(actual - duration, 3),
        "paragraphs": sum(len(s["utterances"]) for s in manifest["scenes"]),
        "sentence_pause": "within original recording",
        "same_speaker_gap_s": 0.3,
        "speaker_change_gap_s": 0.6,
        "local_render": True,
        "equation_scenes": sum(
            bool(s["visual"].get("equations")) for s in manifest["scenes"]
        ),
        "omissions": [o for s in manifest["scenes"] for o in s.get("omissions", [])],
    }
    video._set_export(
        export,
        "checking",
        **{k: str(p.relative_to(config.DATA)) for k, p in paths.items()},
        title=manifest["title"],
        thumbnail=str(thumbnail.relative_to(config.DATA)),
        description=description,
        description_file=str(description_file.relative_to(config.DATA)),
        description_version=publication.DESCRIPTION_VERSION,
        duration=duration,
        media_duration=actual,
        bytes=paths["mp4"].stat().st_size,
        sha256=video.file_digest(paths["mp4"]),
        encode_settings=video.ENCODE,
        characters=manifest["characters"],
        acceptance=acceptance,
        encoder=imageio_ffmpeg.get_ffmpeg_version(),
    )
    finalize_packaging(export["id"], project=project)
    db.patch_job(
        job["id"],
        stage=f"{manifest['mode']} {'preview' if preview else 'film'} ready",
        progress=1,
    )
    return True


def math_ceil(value):
    return int(-(-value // 1))
