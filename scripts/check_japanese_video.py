"""Render an offline acceptance clip from saved native-Japanese probe audio."""

import json

from paperspeak import config, db, japanese_story, story_video, video
from paperspeak.runtime import Runtime


def main():
    db.init()
    report = config.DATA / "evaluation/japanese-details-probe/voice_checks.json"
    samples = json.loads(report.read_text())["samples"]
    paper = db.one(
        "SELECT id FROM papers WHERE source_id='2610.10515' AND version='v1'"
    )
    originals = db.all(
        "SELECT * FROM visual_assets WHERE paper_id=? AND kind='original'",
        (paper["id"],),
    )
    figure = next(
        (
            r
            for r in originals
            if r["data"].get("review", {}).get("passed")
            and r["data"].get("label") == "Figure 3"
        ),
        None,
    )
    if not figure:
        figure = next(r for r in originals if r["data"].get("review", {}).get("passed"))
    current = db.one(
        "SELECT data FROM video_projects WHERE paper_id=? ORDER BY created DESC LIMIT 1",
        (paper["id"],),
    )
    lesson_id = current["data"]["modes"][japanese_story.MODE]["lesson_id"]
    scenes = []
    for index, sample in enumerate(samples):
        ranges = japanese_story.aligned_ranges(
            japanese_story.chunks(sample["text"]),
            sample["timestamps"],
            sample["duration"],
        )
        scene = {
            "title": "Japanese speech and caption check",
            "title_ja": "日本語音声・字幕の確認",
            "focus": "Native Japanese speech",
            "visual": {
                "type": "original",
                "original_asset_id": figure["id"],
                "nodes": [{"en": "Original paper figure", "ja": "論文の原図"}],
                "caption_en": "Original paper figure",
                "caption_ja": "論文の原図を使った音声・字幕表示の確認",
            },
            "utterances": [
                sample
                | {
                    "id": f"ja-probe-{index}",
                    "source_ids": [],
                    "kind": "narration",
                    "visual_focus": 0,
                    "sentence_ranges": ranges,
                    "caption_ranges": ranges,
                    "audio_check": {"timestamps": sample["timestamps"]},
                }
            ],
            "subtitle_items": {
                f"0:{i}": {"japanese": text} for i, (text, _, _) in enumerate(ranges)
            },
        }
        scenes.append(scene)
    p = {
        "id": "ja-details-acceptance",
        "paper_id": paper["id"],
        "data": {
            "modes": {
                japanese_story.MODE: {
                    "lesson_id": lesson_id,
                    "scenes": scenes,
                }
            }
        },
    }
    for index in range(len(scenes)):
        story_video.render_scene(p, japanese_story.MODE, index)
    root = config.DATA / "videos/evaluation/japanese-details-probe"
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"language": "ja", "scenes": scenes}
    speech, captions, _, animation = story_video.timeline(manifest, root)
    _, ass, duration = video._captions(captions, animate=True, languages=["ja"])
    subtitle = root / "captions.ass"
    subtitle.write_text(ass + "\n".join(animation) + "\n")
    images, audio = root / "images.ffconcat", root / "audio.ffconcat"
    images.write_text(
        "ffconcat version 1.0\n"
        + "".join(
            f"file {video._quote(config.safe_path(t['scene']))}\nduration {t['frames'] / 24000:.9f}\n"
            for t in speech
        )
        + f"file {video._quote(config.safe_path(speech[-1]['scene']))}\n"
    )
    audio.write_text(
        "ffconcat version 1.0\n"
        + "".join(
            f"file {video._quote(config.safe_path(t['audio']))}\n" for t in speech
        )
    )
    output = root / "日本語字幕・音声の確認.mp4"
    runtime = Runtime()
    video._ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(images),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(audio),
            "-vf",
            f"fps=25,scale=1920:1080,format=yuv420p,ass={subtitle}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "25",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(output),
        ],
        runtime,
        output,
    )
    actual = video.media_duration(output)
    assert abs(actual - duration) < 0.15
    receipt = {
        "language": "ja",
        "subtitle_languages": ["ja"],
        "subtitle_size": [54, 64],
        "duration": actual,
        "expected_duration": duration,
        "bytes": output.stat().st_size,
        "mp4": str(output.relative_to(config.DATA)),
        "sha256": video.file_digest(output),
        "all_voice_checks_accepted": all(s["accepted"] for s in samples),
        "offline_render": True,
        "purpose": "Acceptance clip, not the completed documentary",
        "role_changes": 2,
    }
    report.with_name("video_check.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
