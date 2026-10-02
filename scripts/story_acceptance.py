"""Inspect finished local story films and save reproducible media evidence."""

import argparse
import json
import re
import subprocess
from difflib import SequenceMatcher

import imageio_ffmpeg
from paperspeak import config, db, story, story_video, video


def inspect(ident, screenshots=False):
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
            "SELECT * FROM video_exports WHERE lesson_id=? AND kind=? AND state='ready' ORDER BY created DESC LIMIT 1",
            (track["lesson_id"], mode),
        )
        if not export:
            report["films"][mode] = {"state": "pending", "phase": track["phase"]}
            continue
        data = export["data"]
        manifest = data["manifest"]
        mp4 = config.safe_path(data["mp4"])
        chapters = db.all(
            "SELECT * FROM chapters WHERE lesson_id=? ORDER BY ordinal",
            (track["lesson_id"],),
        )
        _, _, starts, _ = _timeline(
            manifest, config.DATA / "jobs" / ("story-video-" + export["id"])
        )
        checks = {
            "duration_in_range": story.MODES[mode]["range"][0] * 60
            <= data["media_duration"]
            <= story.MODES[mode]["range"][1] * 60,
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
        }
        paths = []
        if screenshots:
            for i, (start, _) in enumerate(starts):
                path = root / f"{mode}-{i + 1:02}.png"
                subprocess.run(
                    [
                        imageio_ffmpeg.get_ffmpeg_exe(),
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        str(min(start + 12, data["duration"] - 0.5)),
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
                    if (
                        scene["visual"].get("equations")
                        and (
                            not scene["visual"].get("image_path")
                            or 0 < focus < scene["visual"].get("zoom_start", 999)
                        )
                        and focus not in sampled
                    ):
                        sampled.add(focus)
                        path = root / f"{mode}-math-{index + 1:02}-{focus}.png"
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
                    offset += u["duration"]
                    previous = u
        report["films"][mode] = {
            "state": "ready",
            "export_id": export["id"],
            "mp4": data["mp4"],
            "minutes": round(data["media_duration"] / 60, 2),
            "megabytes": round(data["bytes"] / 1e6, 2),
            "sha256": data["sha256"],
            "checks": checks,
            "frames": paths,
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
    args = parser.parse_args()
    report = inspect(args.project_id, args.screenshots)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["media_checks_pass"] else 1)
