"""Refresh completed film labels and URL-free copy, reusing saved local art."""

import argparse
import json
import time

from paperspeak import (
    config,
    db,
    local_network,
    publication,
    story_video,
    thumbnails,
    video,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("project_ids", nargs="+", help="Completed video project IDs")
parser.add_argument(
    "--thumbnails",
    action="store_true",
    help="Recompose three saved candidates for each film; no image inference",
)
args = parser.parse_args()
report = {
    "time": time.time(),
    "description_version": publication.DESCRIPTION_VERSION,
    "projects": [],
}
for ident in args.project_ids:
    project = db.one("SELECT * FROM video_projects WHERE id=?", (ident,))
    if not project or project["state"] != "ready":
        raise SystemExit("A completed project is required: " + ident)
    before = {}
    for track in project["data"]["modes"].values():
        for export in db.all(
            "SELECT * FROM video_exports WHERE lesson_id=? AND state='ready'",
            (track["lesson_id"],),
        ):
            before[export["id"]] = video.file_digest(
                config.safe_path(export["data"]["mp4"])
            )
    # Resolve bibliographic sources before disabling outside connections.
    publication.ensure(project)
    publication.package(project)
    with local_network.inference_only():
        changed = publication.refresh_completed(project)
        sets = (
            {
                mode: thumbnails.recompose(project, mode)
                for mode in project["data"]["modes"]
            }
            if args.thumbnails
            else {}
        )
        for export_id in before:
            story_video.finalize_packaging(export_id, project=project)
    exports = []
    for export_id, digest in before.items():
        export = db.one("SELECT * FROM video_exports WHERE id=?", (export_id,))
        record = export["data"]
        assert video.file_digest(config.safe_path(record["mp4"])) == digest
        assert publication.without_urls(record["description"]) == record["description"]
        assert (
            config.safe_path(record["description_file"]).read_text()
            == record["description"]
        )
        exports.append(
            {
                "id": export_id,
                "sha256": digest,
                "mp4_bytes_unchanged": True,
                "title": record["title"],
                "description_url_free": True,
                "release_check": record["release_check"],
            }
        )
    report["projects"].append(
        {
            "id": ident,
            "paper": project["data"]["paper_title"],
            "publication": project["data"]["publication"],
            "awards": publication.award_identity(project),
            "changed_exports": changed,
            "thumbnail_sets": sets,
            "exports": exports,
        }
    )
report["status"] = "passed"
path = config.DATA / "evaluation" / f"packaging-refresh-{int(report['time'])}.json"
path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {
            "status": report["status"],
            "projects": len(report["projects"]),
            "report": str(path),
        },
        ensure_ascii=False,
    )
)
