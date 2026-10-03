import datetime as dt
import hashlib

import pytest
from paperspeak import awards, db, nightly, papers, story


def document(html, url="https://blog.iclr.cc/2026/04/23/awards/"):
    return {
        "html": html,
        "url": url,
        "retrieved_at": 1,
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
    }


def prize(title="A Useful New Robot Learning Method", venue="RSS", year=2026):
    return {
        "title": title,
        "venue": venue,
        "year": year,
        "name": "Outstanding Paper Award",
        "status": "winner",
        "verified": True,
        "source_sha256": "a" * 64,
        "official_url": f"https://roboticsconference.org/{year}/program/awards/",
        "area": "robotics" if venue in awards.ROBOTICS else "ai",
    }


def test_blog_distinguishes_research_and_test_of_time_winners_from_mentions():
    html = """<article><h3>Outstanding Papers</h3>
    <p><a href="https://openreview.net/forum?id=win">A Useful New Learning Method</a>, by Authors</p>
    <p>This work extends <a href="https://openreview.net/forum?id=background">An Earlier Learning Method</a>.</p>
    <h3>Honorable Mention</h3><p><a href="https://openreview.net/forum?id=mention">A Different New Learning Method</a></p>
    <h3>Test of Time Award</h3><p><a href="https://openreview.net/forum?id=old">A Classic Learning Method</a></p></article>"""
    rows = awards.parse(document(html), "ICLR", 2026)
    assert [r["title"] for r in rows] == [
        "A Useful New Learning Method",
        "A Classic Learning Method",
    ]
    assert [r["kind"] for r in rows] == ["research-paper", "test-of-time"]
    assert rows[0]["source_sha256"] == document(html)["sha256"]


@pytest.mark.parametrize("name", ["Test of Time Award", "Test-of-Time Paper Award"])
def test_test_of_time_names_do_not_include_finalists_or_other_awards(name):
    assert awards.award_kind(name) == "test-of-time"
    for invalid in [
        name + " Finalists",
        name + " Honorable Mention",
        name + " Runner-up",
        "Classic Paper Award",
        "An approach that stands the test of time",
    ]:
        assert not awards.winner_name(invalid)


def test_neurips_award_talk_card_uses_actual_recipient_in_markdown():
    html = """<main><table><tr><td>Test of Time Award</td><td>
    <a href="/virtual/2025/test-of-time/10">Test of Time Award</a>
    <details>[Faster R-CNN: Towards Real-Time Object Detection with Region Proposal Networks (Test of Time Award)](https://papers.neurips.cc/paper_files/paper/2015/hash/abc-Abstract.html)</details>
    </td></tr></table></main>"""
    rows = awards.parse(
        document(html, "https://neurips.cc/virtual/2025/awards_detail"),
        "NeurIPS",
        2025,
    )
    assert len(rows) == 1
    assert (
        rows[0]["title"]
        == "Faster R-CNN: Towards Real-Time Object Detection with Region Proposal Networks"
    )
    assert rows[0]["kind"] == "test-of-time"
    assert rows[0]["year"] == 2025
    # A generic award talk with no identified paper must not become a paper.
    assert not awards.parse(document(html.split("<details>")[0]), "NeurIPS", 2025)
    assert not awards.parse(
        document(
            html.replace("https://papers.neurips.cc", "https://unverified.example")
        ),
        "NeurIPS",
        2025,
    )


def test_rss_winners_and_student_prize_under_finalists():
    html = """<main><h2>Award Winners</h2><h3>Outstanding Paper Award</h3>
    <li><a href="/2026/program/papers/1/">A Useful New Robot Learning Method</a></li>
    <h2>Outstanding Paper Award Finalists</h2>
    <li><a href="/2026/program/papers/2/">A Finalist That Did Not Win</a></li>
    <li><a href="/2026/program/papers/3/">A Useful Student Robot Learning Method</a>
    <span class="winner-label">Winner: Outstanding Student Paper Award</span></li></main>"""
    rows = awards.parse(
        document(html, "https://roboticsconference.org/2026/program/awards/"),
        "RSS",
        2026,
    )
    assert len(rows) == 2
    assert rows[1]["name"] == "Outstanding Student Paper Award"
    assert rows[0]["paper_url"].startswith("https://roboticsconference.org/2026/")


def test_rss_separate_test_of_time_card_requires_visible_matching_year():
    card = """<p>It is our pleasure to announce that the 2025 Test of Time Award goes to:</p>
    <div><h2>2025 Award Recipient</h2><p><strong>Nathan Michael and colleagues</strong></p>
    <p>“Cooperative Manipulation and Transportation with Aerial Robots”</p>
    <p>Robotics: Science and Systems V, 2009</p></div>"""
    html = f"<main><h1>Test of Time Award</h1>{card}</main>"
    rows = awards.parse(document(html), "RSS", 2025)
    assert len(rows) == 1
    assert (
        rows[0]["title"]
        == "Cooperative Manipulation and Transportation with Aerial Robots"
    )
    assert rows[0]["kind"] == "test-of-time" and rows[0]["year"] == 2025
    assert not awards.parse(document(html), "RSS", 2026)
    assert not awards.parse(
        document(f"<main><h1>Test of Time Award</h1><!--{card}--></main>"), "RSS", 2026
    )
    assert not awards.parse(
        document(
            html.replace(
                "“Cooperative Manipulation and Transportation with Aerial Robots”",
                "Authors only",
            )
        ),
        "RSS",
        2025,
    )


def test_neurips_table_distinguishes_winner_from_runner_up():
    html = """<main><table>
    <tr><td>Best Paper</td><td><a href="/virtual/2025/poster/1">A Useful New Attention Method</a></td></tr>
    <tr><td>Best Paper Runner-up</td><td><a href="/virtual/2025/poster/2">A Different Attention Method</a></td></tr>
    </table></main>"""
    assert (
        len(
            awards.parse(
                document(html, "https://neurips.cc/virtual/2025/awards_detail"),
                "NeurIPS",
                2025,
            )
        )
        == 1
    )


def test_icra_plain_winner_and_other_finalists():
    html = """<main><h3>Best Paper Award on Robot Learning</h3><h4>Award Winner:</h4>
    <p><em>A Useful New Robot Learning Method</em><br>Authors: A, B</p>
    <h4>Other Finalists:</h4><p><em>A Different New Robot Learning Method</em></p></main>"""
    rows = awards.parse(document(html), "ICRA", 2025)
    assert [r["title"] for r in rows] == ["A Useful New Robot Learning Method"]


def test_icra_finalist_page_without_winners_does_not_promote_papers():
    html = """<main><h1>ICRA 2026 Award Finalists</h1><h2>Best Paper Award on Robot Learning</h2>
    <p><a href="https://openreview.net/forum?id=nom">A Useful New Robot Learning Method</a></p></main>"""
    assert not awards.parse(document(html), "ICRA", 2026)


def test_aaai_archive_is_partitioned_by_edition():
    html = """<main><h3>2026</h3><h4>Outstanding Paper Award</h4>
    <p><strong>Learning A New Useful Idea</strong><br>Authors: A, B</p>
    <p>Alice Smith, Bob Smith, Charlie Smith, David Smith</p>
    <h3>2025</h3><h4>Outstanding Paper Award</h4><p>Learning Another Useful Idea</p></main>"""
    assert [r["title"] for r in awards.parse(document(html), "AAAI", 2026)] == [
        "Learning A New Useful Idea"
    ]


def test_only_recent_verified_prizes_with_matching_titles():
    p = {"title": "A Useful New Robot Learning Method", "awards": [prize()]}
    assert awards.verified(p, year=2026)
    for changed in [
        {"status": "finalist"},
        {"verified": False},
        {"year": 2024},
        {"year": 2027},
        {"official_url": "https://my-awards.example/"},
        {"title": "A Different Useful Paper"},
        {"name": "Outstanding Paper Honorable Mention"},
    ]:
        assert not awards.verified(p | {"awards": [prize() | changed]}, year=2026)


def test_allowlist_rejects_local_hosts_credentials_and_unsafe_redirects(
    database, monkeypatch
):
    for url in [
        "http://icml.cc/",
        "https://127.0.0.1/",
        "https://icml.cc.evil.test/",
        "https://u:p@icml.cc/",
    ]:
        with pytest.raises(ValueError):
            awards.fetch(url)

    class Response:
        is_redirect = True
        headers = {"location": "https://127.0.0.1/private"}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, target):
            return Response()

    monkeypatch.setattr(awards.httpx, "Client", Client)
    with pytest.raises(ValueError, match="redirect"):
        awards.fetch("https://icml.cc/awards")


@pytest.mark.parametrize(
    "titles",
    [
        ["A Similar But Wrong Robot Learning Method"],
        ["A Useful New Robot Learning Method"] * 2,
    ],
)
def test_resolver_skips_wrong_titles_and_ambiguous_matches(
    database, monkeypatch, titles
):
    monkeypatch.setattr(papers, "fetch", lambda *a, **k: b"mock")
    monkeypatch.setattr(
        papers,
        "entries",
        lambda b: [
            {"source_id": f"2601.0000{i}", "title": t} for i, t in enumerate(titles)
        ],
    )
    assert awards.resolve(prize()) is None


def test_resolver_caches_exact_title_without_inference(database, monkeypatch):
    count = []

    def fetch(*a, **k):
        count.append(1)
        return b"mock"

    monkeypatch.setattr(papers, "fetch", fetch)
    monkeypatch.setattr(
        papers,
        "entries",
        lambda b: [
            {"source_id": "2601.00001", "title": "A Useful New Robot Learning Method"}
        ],
    )
    assert awards.resolve(prize())["source_id"] == "2601.00001"
    assert awards.resolve(prize())["source_id"] == "2601.00001"
    assert len(count) == 1


def test_recent_award_beats_trending_paper_even_with_old_arxiv_date(database):
    now = dt.datetime(2026, 10, 3, tzinfo=dt.timezone.utc)
    awarded = {
        "source_id": "2401.00001",
        "version": "v1",
        "title": prize()["title"],
        "published": "2024-01-01T00:00:00Z",
        "categories": ["cs.RO"],
        "awards": [prize()],
    }
    fresh = {
        "source_id": "2610.00001",
        "title": "A Trending New Language Paper",
        "published": "2026-10-02T00:00:00Z",
        "categories": ["cs.CL"],
    }
    hot = {fresh["source_id"]: {"rank": 1, "upvotes": 10000}}
    rows = nightly.shortlist([fresh, awarded], hot, now=now, days=7, awards_first=True)
    assert rows[0]["source_id"] == awarded["source_id"]
    assert rows[0]["awards"]
    pid = papers.register(awarded)
    story.create(pid)
    assert all(
        r["source_id"] != awarded["source_id"]
        for r in nightly.shortlist(
            [awarded | {"version": "v2"}, fresh],
            hot,
            now=now,
            days=7,
            awards_first=True,
        )
    )
    assert not nightly.shortlist([awarded], {}, now=now, days=7, awards_first=False)


def test_award_checkpoint_survives_repeated_steps_and_merges_metadata(
    database, monkeypatch
):
    now = dt.datetime(2026, 10, 3, tzinfo=nightly.ZONE)
    run_id = nightly.start(now=now)
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run_id,))
    run["data"]["phase"] = "award_sources"
    nightly.save(run)
    monkeypatch.setattr(
        awards,
        "sources",
        lambda *a: [{"venue": "RSS", "year": 2026, "url": prize()["official_url"]}],
    )
    monkeypatch.setattr(
        awards,
        "collect",
        lambda s: {"source": s, "papers": [prize()], "status": "verified winners"},
    )
    monkeypatch.setattr(
        awards,
        "resolve",
        lambda a: {"source_id": "2601.00001", "title": prize()["title"]},
    )
    job = db.one("SELECT * FROM jobs WHERE target=?", (run_id,))
    for _ in range(4):
        nightly.step(job, None)
    updated = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run_id,))
    assert updated["data"]["phase"] == "collect"
    assert updated["data"]["award_source_index"] == 1
    assert updated["data"]["award_candidates"]["2601.00001"]["awards"] == [prize()]


def test_no_confirmed_award_records_fallback_reason(database):
    run_id = nightly.start()
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run_id,))
    run["data"]["phase"] = "award_resolve"
    nightly.save(run)
    job = db.one("SELECT * FROM jobs WHERE target=?", (run_id,))
    nightly.step(job, None)
    updated = nightly.get(run_id)
    assert updated["data"]["award_fallback_reason"]
    assert updated["data"]["phase"] == "collect"


@pytest.mark.parametrize("name", ["Outstanding Paper Award", "Test of Time Award"])
def test_choice_prefers_suitable_award_over_a_higher_scoring_unawarded_paper(
    database, name
):
    now = dt.datetime(2026, 10, 3, tzinfo=nightly.ZONE)
    run_id = nightly.start(now=now)
    run = db.one("SELECT * FROM nightly_video_runs WHERE id=?", (run_id,))
    old = {
        "source_id": "2501.00001",
        "version": "v1",
        "title": prize()["title"],
        "published": "2025-01-01T00:00:00Z",
        "categories": ["cs.RO"],
        "awards": [prize() | {"name": name}],
    }
    new = {
        "source_id": "2610.00001",
        "version": "v1",
        "title": "A Popular New Unawarded Method",
        "published": "2026-10-02T00:00:00Z",
        "categories": ["cs.CL"],
        "awards": [],
    }
    candidates = []
    for meta, score in [(old, 3), (new, 5)]:
        pid = papers.register(meta)
        candidates.append(
            meta
            | {
                "paper_id": pid,
                "area": nightly.category(meta),
                "attention": None,
                "retrieval_score": 0 if meta is old else 300,
                "reading_seconds": 1,
                "notes": [{"claim": "A verified mechanism", "source_ids": ["s1"]}],
                "assessment": {
                    "suitable": True,
                    "content_quality": score,
                    "story_value": score,
                    "why_ja": "本文の発想を学べます",
                    "source_ids": ["s1"],
                },
            }
        )
    run["data"].update(phase="choose", reading=candidates)
    nightly.save(run)
    nightly.step(db.one("SELECT * FROM jobs WHERE target=?", (run_id,)), None)
    result = nightly.get(run_id)
    assert result["data"]["selected"]["source_id"] == old["source_id"]
    assert result["data"]["selected"]["awards"] == old["awards"]
    project = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    context = project["data"]["award_context"]
    assert context["published"] == old["published"]
    assert context["awards"][0]["kind"] == awards.award_kind(name)
    assert result["data"]["selected"]["published"] == old["published"]
    if name == "Test of Time Award":
        prompt = story.award_context_prompt(project)
        assert "2025-01-01" in prompt and '"year": 2026' in prompt
        assert "not a newly published advance" in prompt
        assert "supplied sources support" in prompt
    else:
        assert story.award_context_prompt(project) == ""


def test_classic_survives_large_award_pool_but_uses_recent_award_year(database):
    now = dt.datetime(2026, 10, 3, tzinfo=nightly.ZONE)
    recent = [
        {
            "source_id": f"2609.000{i:02}",
            "title": f"A Useful Recent Learning Method Number {i}",
            "published": "2026-09-01T00:00:00Z",
            "categories": ["cs.LG"],
            "awards": [
                prize(f"A Useful Recent Learning Method Number {i}", venue="ICML")
            ],
        }
        for i in range(12)
    ]
    classic = {
        "source_id": "1506.00001",
        "title": "An Enduring Classic Learning Method",
        "published": "2015-06-01T00:00:00Z",
        "categories": ["cs.LG"],
        "awards": [
            prize("An Enduring Classic Learning Method", venue="NeurIPS", year=2025)
            | {"name": "Test of Time Award"}
        ],
    }
    rows = nightly.shortlist(recent + [classic], {}, now=now, days=7, awards_first=True)
    assert len(rows) == 10
    assert any(p["source_id"] == classic["source_id"] for p in rows)
    assert not nightly.shortlist([classic], {}, now=now, days=7, awards_first=False)
    assert not nightly.shortlist(
        [classic | {"awards": [classic["awards"][0] | {"year": 2024}]}],
        {},
        now=now,
        days=7,
        awards_first=True,
    )
    assert not nightly.shortlist(
        [classic],
        {},
        now=now,
        days=7,
        awards_first=True,
        excluded=[classic["source_id"]],
    )


def test_moving_current_conference_site_cannot_claim_the_wrong_year():
    html = """<html><head><title>CoRL 2026 - Awards</title></head><body><h3>Best Paper Awards</h3>
    <li>A Useful New Robot Learning Method</li></body></html>"""
    assert not awards.parse(
        document(html, "https://www.corl.org/program/awards"), "CoRL", 2025
    )
