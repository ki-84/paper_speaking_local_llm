import copy
import hashlib

import pymupdf
import pytest
from paperspeak import awards, config, db, papers, publication, story, thumbnails

PAPER = "Asynchronous Methods for Deep Reinforcement Learning"
URL = "https://proceedings.mlr.press/v48/mniha16.html"


def document(
    title=PAPER, book="International Conference on Machine Learning", year=2016
):
    html = f"<h1>{title}</h1><pre>booktitle = {{{book}}}, year = {{{year}}}</pre>"
    return {
        "html": html,
        "url": URL,
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
    }


def paper(database, **metadata):
    pid = papers.register(
        {"title": PAPER, "source_id": "1602.01783", "version": "v2", **metadata}
    )
    return db.one("SELECT * FROM papers WHERE id=?", (pid,))


def test_test_of_time_date_is_never_the_publication_date(database, monkeypatch):
    prize = {
        "title": PAPER,
        "venue": "ICML",
        "year": 2026,
        "name": "Test of Time Award",
        "verified": True,
        "status": "winner",
        "official_url": "https://blog.icml.cc/2026/07/05/announcing-the-icml-2026-awards/",
        "source_sha256": "a" * 64,
        "paper_url": URL,
    }
    row = paper(database, awards=[prize])
    monkeypatch.setattr(awards, "fetch", lambda url: document())
    result = publication.resolve(row)
    assert result["label"] == "ICML 2016"
    assert result["source_url"] == URL
    assert result["source_sha256"] == document()["sha256"]
    monkeypatch.setattr(awards, "fetch", lambda url: document("A different paper"))
    missing = publication.resolve(row)
    assert missing["status"] == "unconfirmed"
    assert missing["label"] == "学会未確認"


def test_official_paper_title_must_match_before_using_venue():
    assert (
        publication.parse_page(document("An unrelated paper"), {"title": PAPER}) is None
    )


def test_conference_year_takes_priority_over_review_upload_date():
    doc = document()
    doc["html"] = (
        f'<meta name="citation_title" content="{PAPER}">'
        '<meta name="citation_conference_title" content="ICLR 2022">'
        '<meta name="citation_publication_date" content="2021/09/15">'
    )
    assert publication.parse_page(doc, {"title": PAPER})["label"] == "ICLR 2022"


def test_offline_reuses_confirmed_bibliography_and_exposes_it(database, monkeypatch):
    row = paper(database)
    result = publication.parse_page(document(), row)
    row["data"]["publication"] = result
    db.execute(
        "UPDATE papers SET data=? WHERE id=?", (db.dumps(row["data"]), row["id"])
    )
    monkeypatch.setattr(
        awards, "fetch", lambda url: pytest.fail("Offline lookup made a request")
    )
    pid = story.create(row["id"])["project_id"]
    project = db.one("SELECT * FROM video_projects WHERE id=?", (pid,))
    publication.ensure(project)
    story.save(project)
    assert story.get(pid)["data"]["publication"] == result
    assert publication.resolve(row) == result


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Published as a conference paper at ICLR 2022", "ICLR 2022"),
        (
            "Proceedings of the 33rd International Conference on Machine Learning,\nNew York, USA, 2016.",
            "ICML 2016",
        ),
        ("We cite a paper in Proceedings of ICML 2015.", "学会未確認"),
        ("", "学会未確認"),
    ],
)
def test_pdf_publication_header_does_not_use_bibliography(database, header, expected):
    path = config.DATA / "papers" / "original.pdf"
    path.parent.mkdir(exist_ok=True)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), PAPER + "\n" + header)
        doc.new_page().insert_text((40, 40), "References\nProceedings of NeurIPS 2015")
        doc.save(path)
    row = paper(database, pdf_path="papers/original.pdf")
    assert publication.resolve(row)["label"] == expected


def test_corrupt_pdf_does_not_stop_generation(database):
    path = config.DATA / "papers" / "corrupt.pdf"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"invalid PDF")
    assert (
        publication.resolve(paper(database, pdf_path="papers/corrupt.pdf"))["status"]
        == "unconfirmed"
    )


def test_title_and_thumbnail_share_identity_without_mutating_story_hooks(database):
    row = paper(database, journal_ref="ICML 2016")
    project = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(row["id"])["project_id"],),
    )
    publication.ensure(project)
    for mode, track in project["data"]["modes"].items():
        candidates = [
            {"title_ja": f"発想の秘密{i}", "title_en": f"Original angle {i}"}
            for i in range(3)
        ]
        track["outline"] = {"hook_candidates": candidates}
        track["packaging"] = {
            "title": "AIの新しい発想",
            "title_en": "The new idea",
            "candidates": candidates,
        }
        track["scenes"] = [{"utterances": [{"text": "Here is the idea."}]}]
    raw_outlines = copy.deepcopy(
        [t["outline"] for t in project["data"]["modes"].values()]
    )
    publication.package(project)
    once = copy.deepcopy(project)
    publication.package(project)
    assert project == once
    assert [t["outline"] for t in project["data"]["modes"].values()] == raw_outlines
    for mode, track in project["data"]["modes"].items():
        expected = publication.EDITIONS[mode]
        assert track["packaging"]["title"].startswith(
            f"【{expected}】{PAPER}｜ICML 2016"
        )
        for candidate in track["packaging"]["candidates"]:
            assert f"【{expected}】{PAPER}｜ICML 2016" in candidate["title_ja"]
        tid = thumbnails.enqueue(project, mode)
        manifest = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (tid,))["data"][
            "manifest"
        ]
        assert manifest["identity"]["edition"] == expected
        assert manifest["identity"]["conference"] == "ICML 2016"
        assert manifest["identity"]["paper_title"] == PAPER


def test_long_title_preserves_type_venue_and_full_name_in_metadata(database):
    name = "A very long scientific paper name " * 8
    value = publication.title(name, "ICML 2016", "詳細解説", "A catchy hook")
    assert len(value) <= 95
    assert value.startswith("【詳細解説】") and value.endswith("…｜ICML 2016")
    project = {"data": {"paper_title": name}}
    assert publication.identity(project, "deep_dive")["paper_title"] == name
    assert publication.identity(project, "overview")["conference"] == "学会未確認"


def test_arxiv_journal_reference_is_retained_for_publication_lookup():
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:x="http://arxiv.org/schemas/atom">
    <entry><id>https://arxiv.org/abs/1602.01783v2</id><title>Example</title>
    <x:journal_ref>ICML 2016</x:journal_ref><x:comment>Accepted paper</x:comment></entry></feed>"""
    meta = papers.entries(feed)[0]
    assert meta["journal_ref"] == "ICML 2016"
    assert meta["comments"] == "Accepted paper"
