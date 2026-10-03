"""Inspect finished local story films and save reproducible media evidence."""

import argparse
import json
import re
import subprocess
from difflib import SequenceMatcher

import imageio_ffmpeg
import numpy as np
import pymupdf
from paperspeak import config, db, story, story_video, video


def media_sync_check(mp4, speech, ass_path, timestamp, frame_path):
    """Compare rendered media with the independently positioned source audio/frame."""
    cursor = 0.0
    for item in speech:
        seconds = item["frames"] / 24000
        if cursor <= timestamp < cursor + seconds:
            break
        cursor += seconds
    else:
        raise ValueError("Sample lies beyond the master timeline")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    expected = frame_path.with_name(frame_path.stem + "-expected.png")
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(config.safe_path(item["scene"])),
            "-vf",
            f"format=yuv420p,setpts=PTS+{timestamp}/TB,ass={ass_path}",
            "-frames:v",
            "1",
            str(expected),
        ],
        check=True,
        timeout=40,
    )
    pixels = []
    for path in (frame_path, expected):
        pix = pymupdf.Pixmap(str(path))
        pixels.append(
            np.frombuffer(pix.samples, dtype=np.uint8)
            .reshape(pix.height, pix.width, pix.n)[..., :3]
            .astype(float)
        )
    image_error = float(np.abs(pixels[0] - pixels[1]).mean())
    length = min(2.0, cursor + seconds - timestamp)
    decoded = []
    for path, offset in (
        (mp4, timestamp),
        (config.safe_path(item["audio"]), timestamp - cursor),
    ):
        raw = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                str(offset),
                "-i",
                str(path),
                "-t",
                str(length),
                "-ar",
                "24000",
                "-ac",
                "1",
                "-f",
                "s16le",
                "-",
            ],
            capture_output=True,
            check=True,
            timeout=40,
        ).stdout
        decoded.append(np.frombuffer(raw, dtype="<i2").astype(float))
    count = min(map(len, decoded))
    actual, reference = (a[:count] for a in decoded)
    lag = 0
    correlation = None
    if np.std(reference) > 10:
        # Measure AAC priming explicitly rather than mistaking a few milliseconds
        # of decoded delay for different spoken audio.
        window = min(2400, count // 4)
        size = 1 << (2 * count - 1).bit_length()
        cross = np.fft.irfft(
            np.fft.rfft(actual, size) * np.conj(np.fft.rfft(reference, size)), size
        )
        lag = (
            int(np.argmax(np.concatenate((cross[-window:], cross[: window + 1]))))
            - window
        )
        if lag >= 0:
            actual, reference = actual[lag:], reference[: count - lag]
        else:
            actual, reference = actual[: count + lag], reference[-lag:]
        correlation = float(np.corrcoef(actual, reference)[0, 1])
    return {
        "time": round(timestamp, 3),
        "rgb_mean_error": round(image_error, 3),
        "audio_correlation": round(correlation, 5) if correlation is not None else None,
        "audio_offset_ms": round(lag / 24, 3),
        "passed": image_error < 5
        and abs(lag / 24) < 80
        and (correlation is None or correlation > 0.98),
        "frame": str(frame_path.relative_to(config.DATA)),
    }


def inspect(ident, screenshots=False, sync=False):
    project = db.one("SELECT * FROM video_projects WHERE id=?", (ident,))
    if not project:
        raise ValueError("Video project not found")
    report = {
        "project_id": ident,
        "format": story.FORMAT,
        "state": project["state"],
        "films": {},
        "discovery_enabled": db.settings()["discovery_enabled"],
        "youtube_auto_upload": db.settings()["youtube_auto_upload"],
    }
    root = config.DATA / "evaluation" / ("story-" + ident)
    root.mkdir(parents=True, exist_ok=True)
    scripts = []
    for mode, track in project["data"]["modes"].items():
        words = [
            w
            for s in track["scenes"]
            for u in s.get("utterances", [])
            for w in re.findall(r"[a-z]+", u["text"].lower())
        ]
        scripts.append(words)
        export = db.one(
            "SELECT * FROM video_exports WHERE id=?", (track.get("export_id"),)
        )
        if not export or export["state"] != "ready":
            report["films"][mode] = {"state": "pending", "phase": track["phase"]}
            continue
        data = export["data"]
        manifest = data["manifest"]
        mp4 = config.safe_path(data["mp4"])
        chapters = db.all(
            "SELECT * FROM chapters WHERE lesson_id=? ORDER BY ordinal",
            (track["lesson_id"],),
        )
        work = config.DATA / "jobs" / ("story-video-" + export["id"])
        speech, _, starts, _ = _timeline(
            manifest, config.DATA / "jobs" / ("story-video-" + export["id"])
        )
        checks = {
            "duration_positive": data["media_duration"] > 0,
            "duration_error_small": abs(data["duration"] - video.media_duration(mp4))
            < 1,
            "equation_policy": data["acceptance"]["equation_scenes"] == 0
            if mode == "overview"
            else data["acceptance"]["equation_scenes"] >= 3,
            "equations_actually_selected": mode == "overview"
            or sum(
                bool(s["visual"].get("equations"))
                and any(
                    not s["visual"].get("image_path")
                    or 0 < u.get("visual_focus", 0) < s["visual"].get("zoom_start", 999)
                    for u in s["utterances"]
                )
                for s in manifest["scenes"]
            )
            >= 3,
            "chapters_ready": all(c["state"] == "ready" for c in chapters),
            "sentence_clips_exist": all(
                config.safe_path(t["audio"]).is_file()
                for c in chapters
                for t in c["data"]["turns"]
            ),
            "japanese_for_every_sentence": all(
                c["data"]["translation"]["items"]["turn:" + t["id"]]["japanese"]
                for c in chapters
                for t in c["data"]["turns"]
            ),
            "expressions_count": 8 <= len(track.get("expressions", [])) <= 12,
            "questions_available": all(c["data"].get("questions") for c in chapters),
            "burned_subtitles": data["acceptance"]["burned_subtitles"] == ["en", "ja"],
            "thumbnail_exists": config.safe_path(data["thumbnail"]).is_file(),
            "current_renderer": manifest["version"] == story_video.VERSION,
        }
        paths = []
        sync_samples = []
        if screenshots or sync:
            for i, (start, _) in enumerate(starts):
                path = root / f"{mode}-{i + 1:02}.png"
                timestamp = min(start + 12, data["duration"] - 0.5)
                subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        str(timestamp),
                        "-i",
                        str(mp4),
                        "-frames:v",
                        "1",
                        str(path),
                    ],
                    check=True,
                    timeout=40,
                )
                paths.append(str(path.relative_to(config.DATA)))
                if sync:
                    sync_samples.append(
                        media_sync_check(
                            mp4, speech, work / "captions.ass", timestamp, path
                        )
                    )
            if sync:
                for timestamp in (60.7, 121.7):
                    path = root / f"{mode}-boundary-{timestamp}.png"
                    subprocess.run(
                        [
                            imageio_ffmpeg.get_ffmpeg_exe(),
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-y",
                            "-ss",
                            str(timestamp),
                            "-i",
                            str(mp4),
                            "-frames:v",
                            "1",
                            str(path),
                        ],
                        check=True,
                        timeout=40,
                    )
                    sync_samples.append(
                        media_sync_check(
                            mp4, speech, work / "captions.ass", timestamp, path
                        )
                    )
            offset = 0.0
            previous = None
            for index, scene in enumerate(manifest["scenes"]):
                sampled = set()
                for ui, u in enumerate(scene["utterances"]):
                    if previous:
                        offset += (
                            story_video.pause_frames(
                                previous, u, ui == 0, scene["visual"]["type"]
                            )
                            / 24000
                        )
                    focus = u.get("visual_focus", 0)
                    zoom = bool(scene["visual"].get("image_path")) and focus >= scene[
                        "visual"
                    ].get("zoom_start", 999)
                    if (
                        zoom
                        or (
                            scene["visual"].get("equations")
                            and (
                                not scene["visual"].get("image_path")
                                or 0 < focus < scene["visual"].get("zoom_start", 999)
                            )
                        )
                    ) and focus not in sampled:
                        sampled.add(focus)
                        kind = "zoom" if zoom else "math"
                        path = root / f"{mode}-{kind}-{index + 1:02}-{focus}.png"
                        subprocess.run(
                            [
                                imageio_ffmpeg.get_ffmpeg_exe(),
                                "-hide_banner",
                                "-loglevel",
                                "error",
                                "-y",
                                "-ss",
                                str(offset + min(3, u["duration"] / 2)),
                                "-i",
                                str(mp4),
                                "-frames:v",
                                "1",
                                str(path),
                            ],
                            check=True,
                            timeout=40,
                        )
                        paths.append(str(path.relative_to(config.DATA)))
                        if sync:
                            sync_samples.append(
                                media_sync_check(
                                    mp4,
                                    speech,
                                    work / "captions.ass",
                                    offset + min(3, u["duration"] / 2),
                                    path,
                                )
                            )
                    offset += u["duration"]
                    previous = u
        if sync:
            checks["rendered_audio_caption_figure_sync"] = bool(sync_samples) and all(
                s["passed"] for s in sync_samples
            )
        report["films"][mode] = {
            "state": "ready",
            "export_id": export["id"],
            "mp4": data["mp4"],
            "minutes": round(data["media_duration"] / 60, 2),
            "megabytes": round(data["bytes"] / 1e6, 2),
            "sha256": data["sha256"],
            "checks": checks,
            "frames": paths,
            "sync_samples": sync_samples,
            "spoken_words": len(words),
            "practice_sentences": sum(len(c["data"]["turns"]) for c in chapters),
            "audio_wer_max": max(
                u["audio_check"]["wer"]
                for s in track["scenes"]
                for u in s["utterances"]
            ),
            "local_model": project["data"]["model"],
            "references": project["data"]["references"],
            "review_caveat": "Bounded local-model reviews are recorded; they are not independent accuracy certification.",
        }
    match = SequenceMatcher(None, *scripts, autojunk=False).find_longest_match()
    report["longest_shared_word_sequence"] = match.size
    report["shared_sequence"] = " ".join(scripts[0][match.a : match.a + match.size])
    report["media_checks_pass"] = len(report["films"]) == 2 and all(
        f.get("state") == "ready" and all(f["checks"].values())
        for f in report["films"].values()
    )
    (root / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    return report


def _timeline(manifest, root):
    root.mkdir(parents=True, exist_ok=True)
    return story_video.timeline(manifest, root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id")
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()
    report = inspect(args.project_id, args.screenshots, args.sync)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["media_checks_pass"] else 1)
