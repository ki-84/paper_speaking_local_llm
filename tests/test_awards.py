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


@pytest.mark.parametrize(
    "heading", ["Award Finalists", "Awards and Finalists", "Awards Finalists"]
)
def test_icra_finalist_page_without_winners_does_not_promote_papers(heading):
    html = f"""<main><h1>ICRA 2026 {heading}</h1><h2>Best Paper Award on Robot Learning</h2>
    <p><a href="https://openreview.net/forum?id=nom">A Useful New Robot Learning Method</a></p></main>"""
    assert not awards.parse(document(html), "ICRA", 2026)


def test_icra_plural_winners_split_markup_and_late_finalist_paragraph():
    html = """<main><h1>ICRA 2025 Awards and Finalists</h1>
    <h3>IEEE ICRA Best Conference Paper Award</h3><h4>Award Winners:</h4>
    <p><b>*</b><b>First Useful Robot Paper</b><br><em>Authors: A, B</em></p>
    <p>For a useful contribution to robotics.</p>
    <p><b>*</b><b>Second Useful Robot Paper</b><br><em>Author: C</em></p>
    <p><strong>Awards Committee</strong>: A, B</p>
    <p><strong>In addition to the papers listed above, the following papers were also finalists for the IEEE ICRA Best Conference Paper Award:</strong></p>
    <p><b>A Finalist Robot Paper</b><br><em>Authors: D</em></p>
    <h3>IEEE ICRA Best Student Paper Award</h3><h4>Award Winners:</h4>
    <p><b>First Useful Robot Paper</b><br><em>Authors: A, B</em></p>
    <p><b>A Different Student Robot Paper</b><br><em>Authors: E</em></p>
    <p><strong>Other Finalists:</strong></p>
    <p><b>Another Finalist Robot Paper</b><br><em>Authors: F</em></p></main>"""
    rows = awards.parse(document(html), "ICRA", 2025)
    assert [r["title"] for r in rows] == [
        "First Useful Robot Paper",
        "Second Useful Robot Paper",
        "First Useful Robot Paper",
        "A Different Student Robot Paper",
    ]
    assert len({r["name"] for r in rows}) == 2


def test_ras_archive_uses_exact_year_and_retrospective_prize():
    html = """<main><h1>IEEE ICRA Best Conference Paper Award</h1>
    <p>Eligibility since 2025: “Not a Winning Paper”</p>
    <h2>Winners of this Award</h2><p><strong>2025</strong></p>
    <p>Authors A: “First Useful Robot Paper”</p><p>Authors B: “Second Useful Robot Paper”</p>
    <p><strong>2024</strong></p><p>“An Older Winning Robot Paper”</p>
    <h2>Related Pages</h2><p>“Not a Winning Robot Paper”</p></main>"""
    saved = document(html, awards.RAS_INDEX + "ieee-icra-best-conference-paper-award/")
    rows = awards.parse(saved, "ICRA", 2025)
    assert [r["title"] for r in rows] == [
        "First Useful Robot Paper",
        "Second Useful Robot Paper",
    ]
    assert not awards.parse(saved, "ICRA", 2026)
    retrospective = document(
        html.replace(
            "IEEE ICRA Best Conference Paper Award",
            "IEEE International Conference on Robotics and Automation Most Influential Paper Award",
        ),
        awards.RAS_RETROSPECTIVE,
    )
    assert all(
        r["kind"] == "test-of-time" for r in awards.parse(retrospective, "ICRA", 2025)
    )


def test_iros_official_sponsor_prize_is_scoped_to_year_and_paper():
    html = """<main><h1>Institute Awards</h1><p>“An Unrelated Institute Prize”</p>
    <h2>Past Winners of the IEEE IROS Best Paper Award</h2>
    <h3>Winners of the Best Paper Award 2025</h3><p>〖Winners〗Alice and Bob</p>
    <p>〖Winning Paper〗A Useful Rescue Robotics Paper</p><p>〖Selection Committee Chairperson〗Charlie</p>
    <h3>Winners of the Best Paper Award 2024</h3><p>〖Winning Paper〗An Older Rescue Robotics Paper</p></main>"""
    rows = awards.parse(document(html, awards.IROS_RESCUE), "IROS", 2025)
    assert [r["title"] for r in rows] == ["A Useful Rescue Robotics Paper"]
    assert "Safety, Security, and Rescue Robotics" in rows[0]["name"]
    assert not awards.parse(document(html, awards.IROS_RESCUE), "IROS", 2026)
    assert awards.trusted(awards.IROS_RESCUE)
    assert not awards.trusted("https://www.rescuesystem.org/en/other/")
    assert not awards.trusted(awards.IROS_RESCUE + "?redirect=elsewhere")


def test_collect_archive_fallback_and_more_than_three_prizes(database, monkeypatch):
    import httpx

    spec = next(
        s
        for s in awards.sources(2026, ["cs.RO"])
        if s["venue"] == "ICRA" and s["year"] == 2025
    )
    archive_urls = [
        awards.RAS_INDEX + f"ieee-icra-best-paper-award-{i}/" for i in range(5)
    ]
    requested = []

    def fetch(url):
        requested.append(url)
        if url == spec["url"]:
            raise httpx.ConnectError("Primary host unavailable")
        if url == awards.RAS_INDEX:
            return document(
                "<main>"
                + "".join(
                    f'<a href="{u}">IEEE ICRA Best Paper Award category {i}</a>'
                    for i, u in enumerate(archive_urls)
                )
                + "</main>",
                url,
            )
        if url in archive_urls:
            return document(
                f"<main><h1>IEEE ICRA Best Paper Award category {archive_urls.index(url)}</h1><h2>Winners of this Award</h2><p>2025</p><p>“A Useful Robot Paper Number {archive_urls.index(url)}”</p></main>",
                url,
            )
        return document("<main>No winners published</main>", url)

    monkeypatch.setattr(awards, "fetch", fetch)
    result = awards.collect(spec)
    assert result["status"] == "verified winners"
    assert result["winner_count"] == result["paper_count"] == 5
    assert set(archive_urls) <= set(requested)
    assert result["failures"][0]["url"] == spec["url"]
    monkeypatch.setattr(
        awards, "fetch", lambda _: pytest.fail("Catalogue must not fetch")
    )
    catalogue = awards.catalogue(dt.datetime(2026, 10, 4))
    assert catalogue["winner_count"] == 5
    assert len(catalogue["sources"]) == len(awards.VENUES) * 2


def test_upcoming_official_edition_distinguished_from_network_failure(
    database, monkeypatch
):
    spec = next(
        s
        for s in awards.sources(2026, ["cs.RO"])
        if s["venue"] == "CoRL" and s["year"] == 2026
    )
    saved = document(
        "<title>CoRL 2026</title><main><h1>CoRL 2026</h1><p>November 9–12, 2026</p></main>",
        spec["url"],
    )
    assert awards.upcoming_date(saved, 2026, dt.date(2026, 10, 4)) == "2026-11-09"
    assert awards.upcoming_date(saved, 2026, dt.date(2026, 11, 10)) is None
    assert awards.upcoming_date(saved, 2025, dt.date(2025, 10, 4)) is None
    monkeypatch.setattr(awards, "fetch", lambda _: saved)
    monkeypatch.setattr(awards, "upcoming_date", lambda *a: "2026-11-09")
    result = awards.collect(spec)
    assert result["status"] == "not announced" and not result["papers"]

    def failed(_):
        raise ValueError("Certificate validation failed")

    monkeypatch.setattr(awards, "fetch", failed)
    with pytest.raises(ValueError, match="No official award page"):
        awards.collect(spec)
    receipt = next(
        r
        for r in awards.catalogue(dt.datetime(2026, 10, 4))["sources"]
        if r["source"] == spec
    )
    assert receipt["status"] == "unavailable" and receipt["failures"]


def test_award_catalogue_api_reads_only_stored_receipts(client, monkeypatch):
    monkeypatch.setattr(
        awards, "fetch", lambda _: pytest.fail("GET must not access external sites")
    )
    before = db.all("SELECT id FROM jobs")
    response = client.get("/api/conference-awards")
    assert response.status_code == 200
    assert len(response.json()["sources"]) == len(awards.VENUES) * 2
    assert all(r["status"] == "not checked" for r in response.json()["sources"])
    assert db.all("SELECT id FROM jobs") == before


def test_same_prize_on_blog_and_virtual_table_does_not_inflate_counts(
    database, monkeypatch
):
    spec = {
        "venue": "ICML",
        "year": 2026,
        "url": "https://blog.icml.cc/category/icml-2026/",
    }
    blog = document(
        """<main><h3>ICML 2026 Outstanding Paper Award</h3><p><a href="https://openreview.net/forum?id=one">A Useful Machine Learning Paper</a></p><h3>Outstanding Papers</h3><p><a href="https://openreview.net/forum?id=one">A Useful Machine Learning Paper</a></p></main>""",
        spec["url"],
    )
    table = document(
        """<main><table><tr><td>Outstanding Paper Award</td><td><a href="/virtual/2026/poster/1">A Useful Machine Learning Paper</a></td></tr></table></main>""",
        "https://icml.cc/virtual/2026/awards_detail",
    )
    monkeypatch.setattr(
        awards, "fetch", lambda url: blog if url == spec["url"] else table
    )
    result = awards.collect(spec)
    assert result["winner_count"] == result["paper_count"] == 1
    assert result["papers"][0]["official_url"] == spec["url"]
    assert len(result["documents"]) == 2


def test_official_fetch_uses_browser_headers_and_still_verifies_tls(
    database, monkeypatch
):
    import httpx

    arguments = []

    class Client:
        def __init__(self, **kwargs):
            arguments.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, target):
            return httpx.Response(
                200,
                text="<main>Official awards</main>",
                request=httpx.Request("GET", target),
            )

    monkeypatch.setattr(awards.httpx, "Client", Client)
    result = awards.fetch("https://2025.ieee-icra.org/program/awards-and-finalists/")
    assert result["sha256"]
    assert arguments[0]["headers"] == awards.BROWSER_HEADERS
    assert arguments[0].get("verify", True) is True


@pytest.mark.parametrize("with_link", [True, False])
def test_ras_interview_explicit_award_and_full_paper_identity(monkeypatch, with_link):
    intro = "Authors received the ICRA 2026 Best Conference Paper Award for RobotIdea, a robot learning framework."
    link = '<a href="https://arxiv.org/abs/2601.00001">Paper</a>' if with_link else ""
    saved = document(
        f"<h1>Interview about RobotIdea</h1><p>{intro}</p>{link}",
        "https://www.ieee-ras.org/robotidea-interview/",
    )
    requests = []

    def fetch(url, params):
        requests.append(params)
        return b"paper metadata"

    monkeypatch.setattr(papers, "fetch", fetch)
    monkeypatch.setattr(
        papers,
        "entries",
        lambda _: [
            {"title": "RobotIdea: Learning to Manipulate", "source_id": "2601.00001"}
        ],
    )
    rows = awards.announcement_winners(saved, "ICRA", 2026)
    assert [r["title"] for r in rows] == ["RobotIdea: Learning to Manipulate"]
    assert (
        rows[0]["official_url"] == saved["url"]
        and rows[0]["source_sha256"] == saved["sha256"]
    )
    assert requests == (
        [{"id_list": "2601.00001"}]
        if with_link
        else [{"search_query": 'ti:"RobotIdea"', "max_results": 10}]
    )
    assert not awards.announcement_winners(saved, "ICRA", 2025)
    assert not awards.announcement_winners(
        saved | {"html": saved["html"].replace("received", "might receive")},
        "ICRA",
        2026,
    )
    assert not awards.announcement_winners(
        saved | {"url": "https://an-author.example/interview"}, "ICRA", 2026
    )


def test_ras_interview_double_prize_and_ambiguous_paper_rejected(monkeypatch):
    saved = document(
        "<h1>Interview about RobotIdea</h1><p>The RobotIdea research project achieved a double at ICRA 2026, winning both the Best Conference Paper Award and the Best Paper Award on Robot Manipulation and Locomotion.</p>",
        "https://www.ieee-ras.org/robotidea-interview/",
    )
    monkeypatch.setattr(papers, "fetch", lambda *a, **k: b"metadata")
    paper = {"title": "RobotIdea: Learning to Manipulate", "source_id": "2601.00001"}
    monkeypatch.setattr(papers, "entries", lambda _: [paper])
    rows = awards.announcement_winners(saved, "ICRA", 2026)
    assert len(rows) == 2
    assert (
        rows[1]["name"]
        == "IEEE ICRA Best Paper Award on Robot Manipulation and Locomotion"
    )
    monkeypatch.setattr(
        papers, "entries", lambda _: [paper, paper | {"source_id": "2602.00001"}]
    )
    with pytest.raises(ValueError, match="unambiguously"):
        awards.announcement_winners(saved, "ICRA", 2026)


def test_ras_feature_index_discovers_dated_interviews_without_award_in_heading(
    database, monkeypatch
):
    spec = {"venue": "ICRA", "year": 2026, "url": awards.RAS_FEATURES}
    article_url = "https://www.ieee-ras.org/robotidea-interview/"
    requested = []

    def fetch(url):
        requested.append(url)
        if url == awards.RAS_FEATURES:
            return document(
                f'<div class="e-loop-item"><h2><a href="{article_url}">Interview about RobotIdea</a></h2><p>5 August 2026</p></div><div class="e-loop-item"><h2><a href="https://www.ieee-ras.org/old-interview/">An older interview</a></h2><p>5 August 2024</p></div>',
                url,
            )
        return document(
            "<h1>Interview about RobotIdea</h1><p>Authors received the ICRA 2026 Best Conference Paper Award for RobotIdea.</p>",
            url,
        )

    monkeypatch.setattr(awards, "fetch", fetch)
    monkeypatch.setattr(papers, "fetch", lambda *a, **k: b"metadata")
    monkeypatch.setattr(
        papers,
        "entries",
        lambda _: [
            {"title": "RobotIdea: Learning to Manipulate", "source_id": "2601.00001"}
        ],
    )
    result = awards.collect(spec)
    assert requested == [awards.RAS_FEATURES, article_url]
    assert result["winner_count"] == 1


def test_program_navigation_can_link_a_separate_test_of_time_page(
    database, monkeypatch
):
    spec = {
        "venue": "RSS",
        "year": 2025,
        "url": "https://roboticsconference.org/2025/program/awards/",
    }
    retrospective = "https://roboticsconference.org/2025/program/testoftimeaward/"

    def fetch(url):
        if url == spec["url"]:
            return document(
                f'<nav><a href="{retrospective}">Test of Time Award</a></nav><main>No main prize winners here</main>',
                url,
            )
        return document(
            "<main><h1>Test of Time Award</h1><p>The 2025 Test of Time Award goes to:</p><h2>2025 Award Recipient</h2><p>“A Useful Older Robotics Paper”</p></main>",
            url,
        )

    monkeypatch.setattr(awards, "fetch", fetch)
    rows = awards.collect(spec)["papers"]
    assert [r["title"] for r in rows] == ["A Useful Older Robotics Paper"]
    assert rows[0]["kind"] == "test-of-time"


def test_aaai_archive_is_partitioned_by_edition():
    html = """<main><h3>2026</h3><h4>Outstanding Paper Award</h4>
    <p><strong>Learning A New Useful Idea</strong><br>Authors: A, B</p>
    <p>Alice Smith, Bob Smith, Charlie Smith, David Smith</p>
    <h3>2025</h3><h4>Outstanding Paper Award</h4><p>Learning Another Useful Idea</p></main>"""
    assert [r["title"] for r in awards.parse(document(html), "AAAI", 2026)] == [
        "Learning A New Useful Idea"
    ]


def test_acl_bold_unlinked_papers_and_headingless_best_papers_exclude_other_tracks():
    html = """<title>Best Paper Awards - ACL 2026</title><article>
    <nav><h2>Best Resource Papers</h2><li>A navigation label, not a paper</li></nav>
    <ul><li><strong>A Useful Main Conference Paper</strong><br><em>Alice and Bob</em></li></ul>
    <h2>Best Resource Papers</h2><li><p><strong>A Useful Resource Benchmark</strong><br><em>Charlie</em></p></li>
    <h2>Outstanding Papers</h2><li><strong>A Useful Language Learning Paper</strong><br><em>Diana</em></li>
    <h2>Best Demonstration Paper</h2><li><strong>An Interesting Demo Paper</strong></li>
    <h2>Student Research Workshop Best Papers</h2><li><strong>A Useful Workshop Paper</strong></li>
    <h2>SAC Highlights</h2><li><strong>A Highlighted Language Paper</strong></li>
    <h2>TACL Best Paper</h2><li><strong>A Journal Award Paper</strong></li></article>"""
    saved = document(html, "https://2026.aclweb.org/program/best_papers/")
    rows = awards.parse(saved, "ACL", 2026)
    assert [p["title"] for p in rows] == [
        "A Useful Main Conference Paper",
        "A Useful Resource Benchmark",
        "A Useful Language Learning Paper",
    ]
    assert rows[0]["name"] == "Best Paper Award"
    assert len({(p["title"], p["name"]) for p in rows}) == 3
    assert not awards.parse(saved, "ACL", 2025)
    assert all(
        awards.verified({"title": r["title"], "awards": [r]}, year=2026) for r in rows
    )


def test_cvpr_plain_paper_names_and_society_archive_ignore_mentions_and_other_venues():
    html = """<title>CVPR 2025 Awards</title><main><h2>Best Papers</h2>
    <p><strong>Best Paper:</strong></p><p>Paper Name: A Useful Visual Geometry Paper</p><p>Authors: Alice</p>
    <p><strong>Best Student Paper:</strong></p><p>Paper Name: A Useful Inverse Rendering Paper</p>
    <p><strong>Best Paper Honorable Mention: ID: 123</strong></p><p>Paper Name: A Useful Mentioned Paper</p>
    <h1>Best Demos</h1><li><strong>A Useful Demo Project</strong></li></main>"""
    saved = document(html, "https://cvpr.thecvf.com/Conferences/2025/BestPapersDemos")
    rows = awards.parse(saved, "CVPR", 2025)
    assert [r["title"] for r in rows] == [
        "A Useful Visual Geometry Paper",
        "A Useful Inverse Rendering Paper",
    ]
    archive = document(
        """<main><h1>CVPR Best Paper Award</h1><table><tr><td>2026</td><td>“A Useful New Vision Paper”</td></tr><tr><td>2025</td><td>“A Useful Visual Geometry Paper”</td></tr></table><h1>CVPR Best Paper Honorable Mention Award</h1><table><tr><td>2025</td><td>“A Useful Mentioned Paper”</td></tr></table><h1>ICCV Best Paper Award</h1><table><tr><td>2025</td><td>“A Different Conference Paper”</td></tr></table></main>""",
        awards.CVF_AWARDS,
    )
    assert [r["title"] for r in awards.parse(archive, "CVPR", 2025)] == [
        "A Useful Visual Geometry Paper"
    ]


def test_cvpr_news_article_has_separate_student_prize_and_ignores_tracking_links():
    html = """<title>CVPR 2026 Awards</title><main><p><strong>CVPR 2026 Best Paper</strong></p><li><a href="https://openaccess.thecvf.com/content/CVPR2026/paper.html"><strong>A Useful Scene Reconstruction Paper</strong></a>, Authors: Alice</li><p><strong>CVPR 2026 Best Student Paper</strong></p><li><strong><em> </em></strong><a href="https://tracking.example/redirect"><strong>A Useful Three Dimensional Paper</strong></a>, Authors: Bob</li><p><strong>CVPR 2026 Best Paper Honorable Mentions</strong></p><li><strong>A Useful Mentioned Paper</strong></li></main>"""
    rows = awards.parse(
        document(html, "https://cvpr.thecvf.com/Conferences/2026/News/Best_Papers"),
        "CVPR",
        2026,
    )
    assert len(rows) == 2
    assert rows[0]["paper_url"].startswith("https://openaccess.thecvf.com/")
    assert not rows[1]["paper_url"]


def test_award_refresh_api_is_separate_deduplicated_and_supports_pause_resume(
    client, monkeypatch
):
    monkeypatch.setattr(
        awards, "fetch", lambda *a, **k: pytest.fail("API must only queue")
    )
    first = client.post("/api/conference-awards/refresh").json()
    again = client.post("/api/conference-awards/refresh").json()
    assert first == again
    job = db.one("SELECT * FROM jobs WHERE id=?", (first["job_id"],))
    assert (
        job["kind"] == "award_refresh"
        and len(job["payload"]["sources"]) == len(awards.VENUES) * 2
    )
    assert {r["kind"] for r in db.all("SELECT kind FROM jobs")} == {"award_refresh"}
    assert not db.all("SELECT id FROM nightly_video_runs") and not db.all(
        "SELECT id FROM video_projects"
    )
    assert client.post(f"/api/jobs/{job['id']}/pause").status_code == 200
    assert awards.catalogue()["refresh_job"]["state"] == "paused"
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 200
    client.headers.pop("Authorization")
    assert client.post("/api/conference-awards/refresh").status_code == 401


def test_award_refresh_checkpoints_and_moves_on_after_three_failures(
    database, monkeypatch
):
    ident = awards.start_refresh()
    job = db.one("SELECT * FROM jobs WHERE id=?", (ident,))
    job["payload"]["sources"] = awards.sources(2026, ["cs.AI"])[:2]
    db.execute(
        "UPDATE jobs SET payload=? WHERE id=?", (db.dumps(job["payload"]), ident)
    )
    calls = []

    def collect(spec, **kwargs):
        calls.append((spec["venue"], kwargs))
        if spec["venue"] == "ICLR":
            raise ValueError("Host unavailable")
        return {"papers": []}

    monkeypatch.setattr(awards, "collect", collect)
    for _ in range(3):
        assert not awards.refresh_step(
            db.one("SELECT * FROM jobs WHERE id=?", (ident,))
        )
    saved = db.one("SELECT * FROM jobs WHERE id=?", (ident,))
    assert (
        saved["checkpoint"]["source_index"] == 1
        and len(saved["checkpoint"]["skipped"]) == 1
    )
    assert not awards.refresh_step(saved)
    assert awards.refresh_step(db.one("SELECT * FROM jobs WHERE id=?", (ident,)))
    assert [v for v, k in calls] == ["ICLR"] * 3 + ["ICML"]
    assert all(
        k["refresh"] and callable(k["check"]) and k["refreshed_after"] == job["created"]
        for v, k in calls
    )
    assert not db.all("SELECT id FROM video_projects")


def test_network_outage_keeps_previous_verified_awards_visible(database, monkeypatch):
    spec = {
        "venue": "ICLR",
        "year": 2026,
        "url": "https://blog.iclr.cc/category/iclr-2026/",
    }
    saved = document(
        '<main><h2>Outstanding Paper Award</h2><p><a href="https://openreview.net/forum?id=one">A Useful Verified Learning Paper</a></p></main>',
        spec["url"],
    )
    monkeypatch.setattr(awards, "fetch", lambda *a, **k: saved)
    first = awards.collect(spec)

    def outage(*args, **kwargs):
        raise ValueError("Offline")

    monkeypatch.setattr(awards, "fetch", outage)
    with pytest.raises(ValueError):
        awards.collect(spec, refresh=True)
    result = next(
        r
        for r in awards.catalogue(dt.datetime(2026, 10, 4))["sources"]
        if r["source"]["venue"] == "ICLR" and r["source"]["year"] == 2026
    )
    assert result["status"] == "unavailable" and result["stale"]
    assert result["papers"] == first["papers"] and result["winner_count"] == 1
    assert result["last_successful_check"] == first["checked_at"]


def test_refresh_recording_interrupt_does_not_spend_a_failure_attempt(
    database, monkeypatch
):
    from paperspeak.runtime import PracticePreempted

    ident = awards.start_refresh()
    db.enqueue("practice", "recording-to-evaluate", priority=0)
    monkeypatch.setattr(
        awards,
        "fetch",
        lambda *a, **k: pytest.fail("Recording must be served before network requests"),
    )
    with pytest.raises(PracticePreempted, match="録音"):
        awards.refresh_step(db.one("SELECT * FROM jobs WHERE id=?", (ident,)))
    saved = db.one("SELECT * FROM jobs WHERE id=?", (ident,))
    assert not saved["checkpoint"].get("source_failures")
    assert not saved["checkpoint"].get("source_index")
    assert db.claim("recording-test-worker")["kind"] == "practice"


def test_interrupted_refresh_reuses_pages_fetched_since_the_job_started(
    database, monkeypatch
):
    import time

    import httpx

    url = "https://2026.aclweb.org/program/best_papers/"
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, target):
            calls.append(target)
            return httpx.Response(
                200,
                text="<main>Official paper awards</main>",
                request=httpx.Request("GET", target),
            )

    monkeypatch.setattr(awards.httpx, "Client", Client)
    started = time.time()
    first = awards.fetch(url, refresh=True, refreshed_after=started)
    assert awards.fetch(url, refresh=True, refreshed_after=started) == first
    assert len(calls) == 1
    awards.fetch(url, refresh=True, refreshed_after=first["retrieved_at"] + 1)
    assert len(calls) == 2


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
