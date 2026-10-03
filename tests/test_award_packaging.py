import copy
import hashlib
import json
import time

import pytest
from paperspeak import awards, config, db, papers, publication, story, thumbnails

NAME = "Asynchronous Methods for Deep Reinforcement Learning"


def project():
    prize = {
        "title": NAME,
        "venue": "ICML",
        "year": 2026,
        "name": "Test of Time Award",
        "verified": True,
        "status": "winner",
        "official_url": "https://blog.icml.cc/2026/07/05/announcing-the-icml-2026-awards/",
        "source_sha256": "a" * 64,
    }
    pid = papers.register(
        {
            "title": NAME,
            "source_id": "1602.01783",
            "version": "v2",
            "journal_ref": "ICML 2016",
            "awards": [prize],
        }
    )
    row = db.one(
        "SELECT * FROM video_projects WHERE id=?", (story.create(pid)["project_id"],)
    )
    row["state"] = "ready"
    publication.ensure(row)
    for track in row["data"]["modes"].values():
        track.update(
            packaging={"title": "AIを学ぼう"},
            scenes=[{"utterances": [{"text": "One original recording."}]}],
        )
    publication.package(row)
    story.save(row)
    return row


@pytest.mark.parametrize(
    "host,year,event,title,kind,expected",
    [
        ("icml.cc", 2026, "ICML 2026", NAME, "oral", "ICML 2026"),
        ("iclr.cc", 2025, "ICLR 2025", NAME, "poster", "ICLR 2025"),
        ("neurips.cc", 2025, "NeurIPS 2025", NAME, "spotlight", "NeurIPS 2025"),
        ("icml.cc", 2026, "ICML 2025", NAME, "oral", None),
        ("icml.cc", 2026, "ICML 2026", "Unrelated work", "oral", None),
        ("example.com", 2026, "ICML 2026", NAME, "oral", None),
        ("icml.cc", 2026, "ICML 2026", NAME, "awards", None),
    ],
)
def test_virtual_program_requires_matched_title_official_host_path_and_event_year(
    host, year, event, title, kind, expected
):
    saved = {
        "url": f"https://{host}/virtual/{year}/{kind}/71086",
        "sha256": "a" * 64,
        "html": f"<title>ICML Oral {title}</title><title>{event}</title><h2>{title}</h2>",
    }
    result = publication.parse_page(saved, {"title": NAME})
    assert (result["label"] if result else None) == expected


def test_only_verified_winners_are_badged_with_award_year_not_publication_year(
    database,
):
    row = project()
    context = row["data"]["award_context"]
    winner = context["awards"][0]
    context["awards"] += [
        winner | {"status": "finalist"},
        winner | {"verified": False},
        winner | {"title": "Other work"},
    ]
    meta = publication.identity(row, "overview")
    assert meta["conference"] == "ICML 2016"
    assert len(meta["awards"]) == 1
    assert meta["awards"][0]["label"] == "ICML 2026 Test of Time賞"
    assert meta["awards"][0]["name"] == "Test of Time Award"
    winner["name"] = "Outstanding Papers"
    meta = publication.identity(row, "overview")
    assert meta["awards"][0]["name"] == "Outstanding Paper Award"
    assert meta["awards"][0]["source_name"] == "Outstanding Papers"


def test_descriptions_keep_full_award_references_and_english_learning_without_urls(
    database,
):
    row = project()
    text = publication.description(row, "overview")
    assert "発表学会：ICML 2016" in text
    assert "受賞：ICML 2026 — Test of Time Award" in text
    assert "arXiv: 1602.01783v2" in text
    assert "英語・日本語の字幕" in text and "声に出し" in text
    assert "http" not in text and "www." not in text
    assert awards.verified(row["data"]["award_context"] | {"title": NAME})[0][
        "official_url"
    ].startswith("https://")
    assert (
        publication.without_urls(
            "References: [A paper](https://example.com/paper)\nhttps://arxiv.org/abs/1\n0:00 Intro <https://example.com>\nwww.example.com"
        )
        == "References: A paper\n\n0:00 Intro"
    )
    row["data"]["award_context"] = {}
    assert "受賞" not in publication.description(row, "overview")


def test_refresh_preserves_timestamps_original_manifests_movies_and_description_history(
    database,
):
    row = project()
    lid = row["data"]["modes"]["overview"]["lesson_id"]
    manifest = {
        "preview": False,
        "packaging": {"description": "Original source https://example.com"},
        "scenes": [{"utterances": [{"text": "Original."}]}],
    }
    movie = config.DATA / "videos/movie.mp4"
    movie.write_bytes(b"Original immutable MP4 and audio")
    path = config.DATA / "videos/description.txt"
    old = "Old\nhttps://example.com/paper\n\n0:00 The opening\n10:12 The conclusion"
    path.write_text(old)
    record = {
        "manifest": manifest,
        "mp4": "videos/movie.mp4",
        "description": old,
        "description_file": "videos/description.txt",
        "title": "Old title",
    }
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "export",
            lid,
            "",
            "overview",
            "v",
            "ready",
            db.dumps(record),
            time.time(),
            time.time(),
        ),
    )
    before = hashlib.sha256(movie.read_bytes()).hexdigest()
    assert publication.refresh_completed(row) == ["export"]
    saved = db.one("SELECT * FROM video_exports WHERE id='export'")["data"]
    assert saved["manifest"] == manifest
    assert saved["description"].endswith("0:00 The opening\n10:12 The conclusion")
    assert saved["packaging_history"][0]["description"] == old
    assert "https://" not in saved["description"]
    assert path.read_text() == saved["description"]
    assert next(path.parent.glob("description-*.txt")).read_text() == old
    assert hashlib.sha256(movie.read_bytes()).hexdigest() == before
    assert publication.refresh_completed(row) == []
    assert (
        len(
            db.one("SELECT * FROM video_exports WHERE id='export'")["data"][
                "packaging_history"
            ]
        )
        == 1
    )
    assert not db.all("SELECT id FROM jobs WHERE kind='story_video'")


def test_recomposition_reuses_art_keeps_manual_choice_and_prior_sets_without_ai_or_video_jobs(
    database, monkeypatch
):
    row = project()
    ident = thumbnails.enqueue(row, "overview")
    old = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    root = config.DATA / "thumbnails" / ident
    root.mkdir(parents=True)
    portrait = root / "portrait.png"
    portrait.write_bytes(b"original saved pixel portrait")
    old["data"]["candidates"] = []
    for i in range(3):
        png = root / f"thumbnail-{i}.png"
        png.write_bytes(b"old thumbnail")
        png.with_suffix(".jpg").write_bytes(b"old jpeg")
        png.with_suffix(".json").write_text(
            json.dumps(
                {
                    "maya": str(portrait),
                    "aiden": str(portrait),
                    "lines": ["Saved", "Headline"],
                    "identity": {},
                }
            )
        )
        old["data"]["candidates"].append(
            {
                "id": str(i),
                "png": str(png.relative_to(config.DATA)),
                "jpg": str(png.with_suffix(".jpg").relative_to(config.DATA)),
            }
        )
    old["state"] = "ready"
    old["data"].update(selected_id="1", recommended_id="2", selection_source="manual")
    old["data"]["manifest"]["version"] = "old-version"
    thumbnails.save(old, preserve_selection=False)
    untouched = copy.deepcopy(old["data"])
    rendered = []

    def render(spec, output):
        assert spec["identity"]["conference"] == "ICML 2016"
        assert spec["identity"]["awards"][0]["year"] == 2026
        rendered.append(spec)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"New labels only")

    def jpeg(png):
        png.with_suffix(".jpg").write_bytes(b"jpeg")

    def checked_output(project, mode, candidate):
        # This test mocks rendering to exercise revisions and manual selection.
        # Real rendered receipts and bytes are covered by release-check tests.
        return (
            []
            if config.safe_path(candidate["png"]).is_file()
            and config.safe_path(candidate["jpg"]).is_file()
            else ["Missing output"]
        )

    monkeypatch.setattr(thumbnails, "render", render)
    monkeypatch.setattr(thumbnails, "jpeg", jpeg)
    monkeypatch.setattr(thumbnails, "candidate_issues", checked_output)
    jobs = len(db.all("SELECT id FROM jobs"))
    new_id = thumbnails.recompose(row, "overview")
    new = thumbnails.get(row["id"], "overview")
    assert new_id != ident and new["state"] == "ready"
    assert new["data"]["candidates"][1]["id"] == new["data"]["selected_id"]
    assert new["data"]["selection_source"] == "manual"
    assert (
        db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))["data"] == untouched
    )
    assert len(rendered) == 3
    assert thumbnails.recompose(row, "overview") == new_id
    assert len(rendered) == 3 and len(db.all("SELECT id FROM jobs")) == jobs
