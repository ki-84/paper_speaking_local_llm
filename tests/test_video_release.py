import copy
import json
import time

from paperspeak import (
    config,
    db,
    local_network,
    nightly,
    publication,
    story,
    story_video,
    thumbnails,
    video,
)
from test_award_packaging import project


def export(row, mode="overview", *, preview=False):
    lid = row["data"]["modes"][mode]["lesson_id"]
    root = config.DATA / "videos" / mode
    root.mkdir(parents=True, exist_ok=True)
    movie = root / "movie.mp4"
    movie.write_bytes(
        b"Previously generated video/audio bytes; never reencoded in this metadata test"
    )
    copy_file = root / "description.txt"
    old = "An old description\nhttps://example.com/paper\n\n0:00 Introduction\n1:12 The idea"
    copy_file.write_text(old)
    manifest = {
        "project_id": row["id"],
        "mode": mode,
        "preview": preview,
        "packaging": copy.deepcopy(row["data"]["modes"][mode]["packaging"]),
    }
    ident = db.uid()
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            ident,
            lid,
            "",
            mode + ("_preview" if preview else ""),
            ident,
            "ready",
            db.dumps(
                {
                    "manifest": manifest,
                    "mp4": str(movie.relative_to(config.DATA)),
                    "description": old,
                    "description_file": str(copy_file.relative_to(config.DATA)),
                    "thumbnail": "videos/old-missing.png",
                    "title": "Old title",
                }
            ),
            time.time(),
            time.time(),
        ),
    )
    row["data"]["modes"][mode]["export_id"] = ident
    story.save(row)
    return ident, movie


def test_film_and_thumbnail_snapshots_share_verified_fields_even_if_caller_omits_them(
    database,
):
    row = project()
    track = row["data"]["modes"]["overview"]
    track["packaging"] = {
        "title": "Caller forgot the conference",
        "description": "https://example.com",
    }
    track["scenes"] = [
        {
            "title": "Opening",
            "title_ja": "導入",
            "visual": {},
            "render_paths": [],
            "utterances": [{"text": "An original C1 sentence."}],
            "subtitle_items": {},
        }
    ]
    ident = story_video.enqueue(row, "overview", preview=True)
    film = db.one("SELECT * FROM video_exports WHERE id=?", (ident,))["data"][
        "manifest"
    ]
    tid = thumbnails.enqueue(row, "overview")
    thumb = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (tid,))["data"][
        "manifest"
    ]
    assert film["packaging"]["identity"] == thumb["identity"]
    assert "ICML 2016" in film["title"]
    assert (
        film["packaging"]["identity"]["awards"][0]["label"]
        == "ICML 2026 Test of Time賞"
    )
    assert not publication.description_issues(
        row, "overview", film["packaging"]["description"]
    )
    assert film["release_policy"] == story_video.RELEASE_VERSION
    assert film["scenes"][0]["utterances"][0]["text"] == "An original C1 sentence."


def test_checked_fallback_contains_real_rendered_venue_and_award_before_image_model_finishes(
    database,
):
    row = project()
    with local_network.inference_only():
        for mode in ("overview", "deep_dive"):
            candidate = thumbnails.fallback(row, mode)
            assert not thumbnails.candidate_issues(row, mode, candidate)
            png = config.safe_path(candidate["png"])
            receipt = json.loads(png.with_suffix(".verification.json").read_text())
            assert receipt["rendered"]["conference"] == "ICML 2016"
            assert receipt["rendered"]["award_name"] == "Test of Time Award"
            assert receipt["rendered"]["award_label"] == "ICML 2026 Test of Time賞"
            assert receipt["rendered"]["edition"] == publication.EDITIONS[mode]
            assert receipt["png_sha256"] == video.file_digest(png)
            assert receipt["jpg_sha256"] == video.file_digest(png.with_suffix(".jpg"))
    assert not db.all("SELECT id FROM jobs WHERE kind='thumbnail'")


def test_ready_export_rechecks_and_repairs_copy_and_poster_without_reencoding(database):
    row = project()
    ident, movie = export(row, preview=True)
    before = video.file_digest(movie)
    manifest = copy.deepcopy(
        db.one("SELECT * FROM video_exports WHERE id=?", (ident,))["data"]["manifest"]
    )
    with local_network.inference_only():
        assert story_video.step({"target": ident}, None)
    record = db.one("SELECT * FROM video_exports WHERE id=?", (ident,))["data"]
    assert record["release_check"]["status"] == "passed"
    assert record["release_check"]["repairs"]
    assert not publication.description_issues(row, "overview", record["description"])
    assert record["description"].endswith("0:00 Introduction\n1:12 The idea")
    assert record["title"].endswith(" — Preview")
    assert not thumbnails.image_issues(row, "overview", record["thumbnail"])
    assert record["manifest"] == manifest and video.file_digest(movie) == before
    png = config.safe_path(record["thumbnail"])
    mtime = png.stat().st_mtime_ns
    with local_network.inference_only():
        story_video.finalize_packaging(ident)
    assert png.stat().st_mtime_ns == mtime
    assert (
        len(
            db.one("SELECT * FROM video_exports WHERE id=?", (ident,))["data"][
                "packaging_history"
            ]
        )
        == 1
    )


def test_receipts_detect_wrong_award_modified_input_and_stale_or_corrupt_image(
    database,
):
    row = project()
    candidate = thumbnails.fallback(row, "overview")
    png = config.safe_path(candidate["png"])
    receipt_path = png.with_suffix(".verification.json")
    original = json.loads(receipt_path.read_text())
    for field, value in [
        ("conference", "ICML 2026"),
        ("award_name", None),
        ("edition", "詳細解説"),
    ]:
        changed = copy.deepcopy(original)
        changed["rendered"][field] = value
        receipt_path.write_text(json.dumps(changed))
        assert (
            "Rendered paper, conference, edition or award does not match"
            in thumbnails.image_issues(row, "overview", candidate["png"])
        )
    receipt_path.write_text(json.dumps(original))
    with png.with_suffix(".jpg").open("ab") as handle:
        handle.write(b"corrupted")
    assert "Thumbnail JPEG changed after verification" in thumbnails.candidate_issues(
        row, "overview", candidate
    )
    source = png.with_suffix(".json")
    source.write_text(source.read_text() + " ")
    assert (
        "Thumbnail or render input changed after verification"
        in thumbnails.image_issues(row, "overview", candidate["png"])
    )
    with png.open("ab") as handle:
        handle.write(b"corrupted")
    assert thumbnails.image_issues(row, "overview", candidate["png"])


def test_unconfirmed_cache_refreshes_once_after_new_paper_evidence(
    database, monkeypatch
):
    row = project()
    paper = db.one("SELECT * FROM papers WHERE id=?", (row["paper_id"],))
    paper["data"].pop("publication", None)
    paper["data"].pop("journal_ref", None)
    paper["data"]["awards"] = []
    row["data"]["award_context"] = {}
    row["data"]["publication"] = {}
    db.execute(
        "UPDATE papers SET data=? WHERE id=?", (db.dumps(paper["data"]), paper["id"])
    )
    attempts = []
    real = publication.resolve

    def lookup(paper):
        attempts.append(1)
        return real(paper)

    monkeypatch.setattr(publication, "resolve", lookup)
    publication.ensure(row)
    publication.ensure(row)
    assert len(attempts) == 1 and row["data"]["publication"]["status"] == "unconfirmed"
    paper["data"]["journal_ref"] = "ICML 2016"
    db.execute(
        "UPDATE papers SET data=? WHERE id=?", (db.dumps(paper["data"]), paper["id"])
    )
    publication.ensure(row)
    publication.ensure(row)
    assert len(attempts) == 2 and row["data"]["publication"]["label"] == "ICML 2016"
    row["data"]["publication"] = {"status": "unconfirmed"}
    publication.ensure(row)
    assert len(attempts) == 2 and row["data"]["publication"]["label"] == "ICML 2016"


def test_copy_repair_preserves_thumbnail_choice_after_its_snapshot_was_read(database):
    row = project()
    ident, movie = export(row)
    stale = db.one("SELECT * FROM video_exports WHERE id=?", (ident,))
    manual = copy.deepcopy(stale["data"])
    manual.update(
        thumbnail="thumbnails/manual.png",
        thumbnail_jpg="thumbnails/manual.jpg",
        thumbnail_set_id="manual-set",
        thumbnail_candidate_id="manual-candidate",
    )
    db.execute("UPDATE video_exports SET data=? WHERE id=?", (db.dumps(manual), ident))
    publication.prepare(row)
    publication.refresh_export(row, "overview", stale)
    current = db.one("SELECT * FROM video_exports WHERE id=?", (ident,))["data"]
    assert current["thumbnail"] == "thumbnails/manual.png"
    assert current["thumbnail_set_id"] == "manual-set"
    assert current["thumbnail_candidate_id"] == "manual-candidate"
    assert not publication.description_issues(row, "overview", current["description"])
    assert config.safe_path(current["mp4"]) == movie


def test_nightly_completion_runs_release_checks_for_both_films(database):
    row = project()
    before = {}
    for mode in row["data"]["modes"]:
        ident, movie = export(row, mode)
        before[ident] = video.file_digest(movie)
        # Simulate old completed thumbnail metadata with no usable saved inputs.
        stamp = time.time()
        data = {
            "manifest": {"version": "legacy"},
            "candidates": [
                {
                    "id": str(i),
                    "png": f"thumbnails/missing-{mode}-{i}.png",
                    "jpg": f"thumbnails/missing-{mode}-{i}.jpg",
                }
                for i in range(3)
            ],
            "selected_id": "1",
            "recommended_id": "2",
            "selection_source": "manual",
            "warnings": [],
            "review": {},
        }
        db.execute(
            "INSERT INTO thumbnail_sets VALUES (?,?,?,?,?,?,?,?)",
            (db.uid(), row["id"], mode, mode, "ready", db.dumps(data), stamp, stamp),
        )
    rid = nightly.start(manual=True)
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (rid,))
    run.update(state="building", project_id=row["id"])
    run["data"].update(
        phase="production",
        production_started=time.time(),
        started=time.time(),
        timings={},
    )
    nightly.save(run)
    job = db.one("SELECT * FROM jobs WHERE kind='nightly_video' AND target=?", (rid,))
    assert nightly.step(job, None)
    saved = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (rid,))
    assert saved["state"] == "ready"
    assert set(saved["data"]["release_checks"]) == {"overview", "deep_dive"}
    for ident, sha in before.items():
        record = db.one("SELECT * FROM video_exports WHERE id=?", (ident,))["data"]
        assert record["release_check"]["status"] == "passed"
        assert video.file_digest(config.safe_path(record["mp4"])) == sha
    for mode in row["data"]["modes"]:
        current = thumbnails.get(row["id"], mode)
        assert current["data"]["selection_source"] == "manual"
        assert all(
            not thumbnails.candidate_issues(row, mode, c)
            for c in current["data"]["candidates"]
        )
