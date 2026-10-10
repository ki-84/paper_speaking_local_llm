"""A read-only timeline of downloadable films, with earlier renders folded in."""

from __future__ import annotations

import json
import time

from . import config, db

KINDS = {
    "overview": "概要解説",
    "deep_dive": "英語・詳細解説",
    "deep_dive_ja": "日本語解説",
    "full": "全章まとめ",
}


def archive_before_story(*, apply=False):
    """Hide older teaching formats without deleting media, practice or jobs."""
    from .story import FORMAT

    with db.connection() as conn:
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        cutoff = conn.execute(
            "SELECT min(created) AS first FROM lessons WHERE json_extract(data,'$.format')=?",
            (FORMAT,),
        ).fetchone()["first"]
        rows = (
            conn.execute(
                "SELECT id,state,data FROM lessons WHERE created<? AND coalesce(json_extract(data,'$.format'),'')<>? AND coalesce(json_extract(data,'$.archived'),0)=0 ORDER BY created",
                (cutoff, FORMAT),
            ).fetchall()
            if cutoff is not None
            else []
        )
        result = {"applied": apply, "story_started_at": cutoff, "lessons": []}
        for row in rows:
            data = json.loads(row["data"])
            result["lessons"].append(
                {
                    "id": row["id"],
                    "title": data.get("title"),
                    "state": row["state"],
                    "format": data.get("format"),
                }
            )
            if apply:
                data.update(
                    archived=True,
                    archived_at=time.time(),
                    archive_reason="Superseded by overview and deep-dive videos",
                )
                conn.execute(
                    "UPDATE lessons SET data=? WHERE id=?", (db.dumps(data), row["id"])
                )
    if apply and rows:
        db.event("library", {"archived_lessons": [r["id"] for r in result["lessons"]]})
    return result


def catalogue():
    # Select public metadata only: manifests can contain a whole script and
    # alignment traces. Neither previews nor unfinished exports are films.
    exports = db.all("""
        SELECT v.id,v.lesson_id,v.chapter_id,v.kind,v.created,
          l.paper_id,p.title AS paper_title,
          coalesce(c.ordinal,json_extract(v.data,'$.manifest.ordinal')) AS ordinal,
          json_extract(l.data,'$.project_id') AS project_id,
          json_object(
            'title',json_extract(v.data,'$.title'),
            'mp4',json_extract(v.data,'$.mp4'),
            'thumbnail',json_extract(v.data,'$.thumbnail'),
            'thumbnail_jpg',json_extract(v.data,'$.thumbnail_jpg'),
            'description',json_extract(v.data,'$.description'),
            'language',coalesce(json_extract(v.data,'$.language'),'en'),
            'subtitle_languages',json_extract(v.data,'$.subtitle_languages'),
            'voice_profile',json_extract(v.data,'$.voice_profile'),
            'english_lesson_id',(SELECT json_extract(project.data,'$.modes.deep_dive.lesson_id') FROM video_projects project WHERE project.id=json_extract(l.data,'$.project_id')),
            'duration',json_extract(v.data,'$.duration'),
            'bytes',json_extract(v.data,'$.bytes'),
            'completed_at',json_extract(v.data,'$.completed_at'),
            'conference',json_extract(v.data,'$.identity.conference'),
            'awards',json_extract(v.data,'$.identity.awards')
          ) AS data
        FROM video_exports v
        JOIN lessons l ON l.id=v.lesson_id
        JOIN papers p ON p.id=l.paper_id
        LEFT JOIN chapters c ON c.id=v.chapter_id AND c.lesson_id=v.lesson_id
        WHERE v.state='ready' AND v.kind IN ('overview','deep_dive','deep_dive_ja','full','chapter')
          AND coalesce(json_extract(l.data,'$.archived'),0)=0
        """)
    ready = []
    for export in exports:
        data = export["data"]
        try:
            mp4 = data.get("mp4") or ""
            if not mp4.startswith("videos/"):
                continue
            path = config.safe_path(mp4)
            if not path.is_file():
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        # `updated` changes when a thumbnail/title is selected. File mtime is
        # the original render time for older exports, so poster edits cannot
        # move an old film to the top of the timeline.
        export["completed_at"] = data.pop("completed_at") or stat.st_mtime
        export["label"] = KINDS.get(export["kind"], "章別の動画")
        if export["kind"] == "chapter" and export["ordinal"] is not None:
            export["label"] = f"第{export['ordinal'] + 1}章"
        data["title"] = data["title"] or export["paper_title"]
        data["bytes"] = data["bytes"] or stat.st_size
        ready.append(export)

    ready.sort(key=lambda v: (v["completed_at"], v["created"], v["id"]), reverse=True)
    grouped = {}
    for export in ready:
        # A new script/lesson remains a separate video. Only re-renders of the
        # same lesson and edition (or the same chapter) are earlier revisions.
        key = (export["lesson_id"], export["kind"], export["chapter_id"])
        if key not in grouped:
            grouped[key] = export | {"revisions": []}
        else:
            grouped[key]["revisions"].append(export)
    return list(grouped.values())
