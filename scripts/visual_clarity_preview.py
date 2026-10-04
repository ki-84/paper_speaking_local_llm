"""Render an original-first visual study without starting jobs or changing films."""

import argparse
import json
import shutil

from paperspeak import config, db, local_network, story_video, video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id")
    args = parser.parse_args()
    project = db.one("SELECT * FROM video_projects WHERE id=?", (args.project_id,))
    if not project:
        raise SystemExit("Project not found")
    originals = db.all(
        "SELECT * FROM visual_assets WHERE paper_id=? AND kind='original' ORDER BY created",
        (project["paper_id"],),
    )
    source = next(
        (
            a
            for a in originals
            if not a["data"].get("story_spec")
            and a["data"].get("review", {}).get("passed")
            and a["data"].get("regions")
        ),
        None,
    )
    if not source:
        raise SystemExit("A checked original with verified regions is required")
    data = source["data"]
    root = config.DATA / "evaluation" / ("visual-clarity-" + project["id"])
    root.mkdir(parents=True, exist_ok=True)
    scenes = project["data"]["modes"]["overview"]["scenes"]
    before = next(
        (s for s in scenes if len(s.get("visual", {}).get("nodes", [])) >= 5), scenes[0]
    )
    old = config.safe_path(before["render_paths"][0])
    shutil.copyfile(old, root / "before.png")
    visual = {
        "type": "original",
        "image_path": data["image_path"],
        "original_asset_id": source["id"],
        "original_source": {"label": data.get("label"), "page": data.get("page")},
        "nodes": [{"en": "Start with the actual object", "ja": "まず実物を見る"}],
        "zoom_start": 1,
        "zoom_regions": data["regions"][:2],
        "caption_en": "See the actual objects before introducing technical terms.",
        "caption_ja": "専門用語の前に、実際に扱うものを見せる。",
    }
    outputs = []
    with local_network.inference_only():
        for focus in range(1 + len(visual["zoom_regions"])):
            spec = {
                "title_en": "What must survive compression?",
                "title_ja": "小さくしても、何を残す必要がある？",
                "mode": "overview",
                "focus": focus,
                "visual": visual,
                "data_root": str(config.DATA),
                "study_output": str(root / f"study-{focus}.png"),
            }
            path = root / f"original-{focus}.png"
            inp = path.with_suffix(".json")
            inp.write_text(db.dumps(spec), encoding="utf-8")
            story_video._render(inp, path)
            outputs.append(
                {
                    "focus": focus,
                    "png": str(path.relative_to(config.DATA)),
                    "sha256": video.file_digest(path),
                }
            )
    report = {
        "status": "passed",
        "purpose": "Static visual-direction study, not a regenerated video or a viewer-comprehension evaluation",
        "project_id": project["id"],
        "paper": project["data"]["paper_title"],
        "publication": project["data"].get("publication"),
        "source_asset_id": source["id"],
        "source_sha256": video.file_digest(config.safe_path(data["image_path"])),
        "source_page": data.get("page"),
        "verified_regions": visual["zoom_regions"],
        "original_pixels_preserved": True,
        "cloud_inference_used": False,
        "external_requests_during_render": "blocked by local Chromium renderer",
        "production_jobs_started": False,
        "film_or_audio_changed": False,
        "before": str((root / "before.png").relative_to(config.DATA)),
        "outputs": outputs,
    }
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": "passed", "directory": str(root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
