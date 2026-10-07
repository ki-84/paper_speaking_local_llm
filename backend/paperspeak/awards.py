"""Official conference award discovery; never infer an award from an abstract."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
import pymupdf as fitz
from bs4 import BeautifulSoup

from . import config, db, papers

VENUES = (
    "ICLR",
    "ICML",
    "NeurIPS",
    "AAAI",
    "ACL",
    "CVPR",
    "RSS",
    "ICRA",
    "CoRL",
    "IROS",
)
ROBOTICS = {"RSS", "ICRA", "CoRL", "IROS"}
EXCLUDED = re.compile(
    r"honou?rable|runner.?up|finalist|nomina|classic|reviewer|editor|"
    r"competition|challenge|dissertation|position paper",
    re.I,
)
TEST_OF_TIME = re.compile(r"\btest\W+of\W+time\b(?:\W+paper)?\W+awards?\b", re.I)
COLLECTION_VERSION = "official-awards-4"
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
RAS_INDEX = "https://www.ieee-ras.org/awards-recognition/conference-awards/"
RAS_FEATURES = "https://www.ieee-ras.org/category/ras-feature/"
RAS_RETROSPECTIVE = "https://www.ieee-ras.org/awards-recognition/society-awards/ieee-international-conference-on-robotics-and-automation-most-influential-paper-award/"
IROS_RESCUE = "https://www.rescuesystem.org/en/award/"
CVF_AWARDS = "https://www.thecvf.com/?page_id=413"
CMU_PUBLICATIONS = "https://www.cs.cmu.edu/~dpathak/"
SIA_EVENTS = "http://english.sia.cas.cn/news/Events/"


def normalized(title):
    value = unicodedata.normalize("NFKD", title).casefold()
    return "".join(c for c in value if c.isalnum())


def award_kind(name):
    if EXCLUDED.search(name):
        return None
    if TEST_OF_TIME.search(name) or re.search(
        r"\bMost Influential Paper Award\b|\bLonguet-Higgins Prize\b", name, re.I
    ):
        return "test-of-time"
    if re.search(r"\b(best|outstanding)\b.*\bpaper", name, re.I):
        return "research-paper"
    return None


def winner_name(name):
    return award_kind(name) is not None


def award_key(name, venue, year):
    """Ignore venue/year prefixes and award suffixes, retaining prize categories."""
    if TEST_OF_TIME.search(name):
        # Announcement headings can mention the old publication year. They
        # still refer to the same prize as the concise name extracted by AI.
        return "testoftime"
    name = re.sub(
        rf"\b(?:IEEE|{re.escape(venue)}|{year}|awards?)\b", "", name, flags=re.I
    )
    name = re.sub(r"\bpapers\b", "paper", name, flags=re.I)
    name = re.sub(r"\s*\(Part\s+\d+\)", "", name, flags=re.I)
    name = re.sub(r"\b(field and service)(?: robotics)?\b", r"\1", name, flags=re.I)
    return normalized(name)


def trusted(url):
    u = urlparse(url)
    if u.username or u.password or u.port:
        return False
    host = u.hostname or ""
    # Explicit public institutional feeds, not arbitrary personal/lab domains.
    # SIA's legacy news feed is served over HTTP; never disable TLS checks.
    if host == "english.sia.cas.cn" and u.path.startswith("/news/Events/"):
        return u.scheme in {"http", "https"} and not u.query
    if host == "www.cs.cmu.edu" and u.path == "/~dpathak/":
        return u.scheme == "https" and not u.query
    if u.scheme != "https":
        return False
    roots = (
        "iclr.cc",
        "icml.cc",
        "neurips.cc",
        "aaai.org",
        "roboticsconference.org",
        "ieee-icra.org",
        "ieee-iros.org",
        "corl.org",
        "iros25.org",
        "openreview.net",
        "roboticsproceedings.org",
        "proceedings.mlr.press",
        "ieee-ras.org",
        "aclweb.org",
        "aclanthology.org",
        "thecvf.com",
    )
    return any(host == root or host.endswith("." + root) for root in roots) or (
        host in {"rescuesystem.org", "www.rescuesystem.org"}
        and u.path == "/en/award/"
        and not u.query
    )


def fetch(url, *, refresh=False, refreshed_after=None):
    """Cache official pages for a day; validate every redirect before following."""
    if not trusted(url):
        raise ValueError("Not an approved conference/paper host")
    key = hashlib.sha256(url.encode()).hexdigest()
    path = config.DATA / "cache" / ("award-page-" + key + ".json")
    if path.is_file():
        saved = json.loads(path.read_text())
        if (not refresh and time.time() - saved["retrieved_at"] < 86400) or (
            refresh
            and refreshed_after is not None
            and saved["retrieved_at"] >= refreshed_after
        ):
            return saved
    with httpx.Client(
        timeout=25, follow_redirects=False, headers=BROWSER_HEADERS
    ) as client:
        target = url
        for _ in range(5):
            response = client.get(target)
            if response.is_redirect:
                target = urljoin(target, response.headers["location"])
                if not trusted(target):
                    raise ValueError("Untrusted award-page redirect")
                continue
            response.raise_for_status()
            is_pdf = response.content.startswith(b"%PDF-")
            if len(response.content) > (80_000_000 if is_pdf else 5_000_000):
                raise ValueError("Award document exceeds the download size limit")
            result = {
                "url": target,
                "cache_url": url,
                "html": "" if is_pdf else response.text,
                "retrieved_at": time.time(),
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
            if is_pdf:
                try:
                    pdf = fitz.open(stream=response.content, filetype="pdf")
                except (fitz.FileDataError, fitz.EmptyFileError) as exc:
                    raise ValueError("Award PDF could not be read") from exc
                with pdf:
                    if pdf.needs_pass or len(pdf) > 150:
                        raise ValueError("Award PDF is locked or exceeds 150 pages")
                    result["format"] = "pdf"
                    result["pages"] = [
                        {"page": i + 1, "text": p.get_text("text", sort=True)}
                        for i, p in enumerate(pdf)
                    ]
                    result["html"] = (
                        "<main>"
                        + "".join(
                            f"<section><h2>Page {p['page']}</h2><pre>{html.escape(p['text'])}</pre></section>"
                            for p in result["pages"]
                        )
                        + "</main>"
                    )
                raw_path = path.with_suffix(".pdf")
                raw_path.write_bytes(response.content)
                result["pdf_path"] = str(raw_path.relative_to(config.DATA))
            temp = path.with_suffix(".tmp")
            temp.write_text(db.dumps(result))
            temp.replace(path)
            return result
    raise ValueError("Too many award-page redirects")


def sources(year, categories):
    """Current and previous editions; not the arXiv submission-date window."""
    robotics = "cs.RO" in categories
    ai = bool(set(categories) - {"cs.RO"})
    rows = []
    for edition in (year, year - 1):
        for venue in VENUES:
            if (venue in ROBOTICS and not robotics) or (
                venue not in ROBOTICS and not ai
            ):
                continue
            if venue in {"ICLR", "ICML"}:
                url = f"https://blog.{venue.lower()}.cc/category/{venue.lower()}-{edition}/"
            elif venue == "NeurIPS":
                url = f"https://neurips.cc/virtual/{edition}/awards_detail"
            elif venue == "AAAI":
                url = "https://aaai.org/about-aaai/aaai-awards/aaai-conference-paper-awards-and-recognition/"
            elif venue == "RSS":
                url = f"https://roboticsconference.org/{edition}/program/awards/"
            elif venue == "ACL":
                path = "awards" if edition == 2025 else "best_papers"
                url = f"https://{edition}.aclweb.org/program/{path}/"
            elif venue == "CVPR":
                path = "BestPapersDemos" if edition == 2025 else "News/Best_Papers"
                url = f"https://cvpr.thecvf.com/Conferences/{edition}/{path}"
            elif venue == "ICRA":
                url = f"https://{edition}.ieee-icra.org/"
            elif venue == "CoRL":
                url = (
                    "https://www.corl.org/program/awards"
                    if edition == year
                    else f"https://{edition}.corl.org/program/awards"
                )
            else:
                url = (
                    "https://iros25.org/"
                    if edition == 2025
                    else f"https://{edition}.ieee-iros.org/"
                )
            alternate_urls = []
            if venue == "CoRL" and edition == year:
                url = "https://www.corl.org/"
            elif venue == "NeurIPS":
                alternate_urls = [f"https://neurips.cc/Conferences/{edition}"]
            elif venue == "ICRA":
                url = (
                    f"https://{edition}.ieee-icra.org/program/awards-and-finalists/"
                    if edition == 2025
                    else f"https://{edition}.ieee-icra.org/awards/"
                )
                alternate_urls = [
                    RAS_INDEX,
                    RAS_RETROSPECTIVE,
                    RAS_FEATURES,
                    f"https://www.ieee-ras.org/{edition}-ieee-ras-awards-brochure/",
                ]
            elif venue == "IROS":
                if edition != 2025:
                    url = f"https://{edition}.ieee-iros.org/program/awards/"
                alternate_urls = [
                    IROS_RESCUE,
                    CMU_PUBLICATIONS,
                    SIA_EVENTS,
                    SIA_EVENTS + "index_1.html",
                ]
            elif venue == "ACL":
                path = "best_papers" if edition == 2025 else "awards"
                alternate_urls = [f"https://{edition}.aclweb.org/program/{path}/"]
            elif venue == "CVPR":
                alternate_urls = [CVF_AWARDS]
            rows.append(
                {
                    "venue": venue,
                    "year": edition,
                    "url": url,
                    "alternate_urls": alternate_urls,
                }
            )
    return rows


def parse(saved, venue, year):
    """Use explicit winner sections/cards, not a model's recollection of awards."""
    soup = BeautifulSoup(saved["html"], "html.parser")
    title_years = re.findall(
        r"\b20\d{2}\b", soup.title.get_text() if soup.title else ""
    )
    if title_years and str(year) not in title_years:
        return []
    root = soup.select_one(".entry-content, #inner-content, main, article") or soup
    for element in root.select("script, style, nav, header, footer"):
        element.decompose()
    found = {}

    def add(title, name, paper_url=""):
        title = re.sub(r"\s+", " ", title).strip(" *\n")
        if not winner_name(name) or not 12 <= len(title) <= 350:
            return
        if paper_url:
            paper_url = urljoin(saved["url"], paper_url)
        key = (normalized(title), award_key(name, venue, year))
        if key in found:
            return
        found[key] = {
            "title": title,
            "venue": venue,
            "year": year,
            "name": name,
            "kind": award_kind(name),
            "status": "winner",
            "verified": True,
            "official_url": saved["url"],
            "paper_url": paper_url,
            "retrieved_at": saved["retrieved_at"],
            "source_sha256": saved["sha256"],
            "area": "robotics" if venue in ROBOTICS else "ai",
        }

    if saved.get("format") == "pdf":
        # A brochure can contain both society recipients and conference
        # finalists. Only an explicit Winner label plus quoted paper qualifies.
        for page in saved.get("pages", []):
            text = re.sub(r"\s+", " ", page["text"])
            if not re.search(rf"\b{venue}\s+{year}\b", text):
                continue
            for match in re.finditer(
                r"((?:IEEE )?ICRA Best(?: Conference| Student)? Paper Award(?: (?:on|in) [A-Za-z -]+?)?)\s*[:–-]?\s*Winners?\s*:\s*[“\"]([^”\"]+)[”\"]",
                text,
                re.I,
            ):
                add(match[2], match[1])
                if found:
                    key = (normalized(match[2]), award_key(match[1], venue, year))
                    found[key].update(
                        official_url=saved["url"] + f"#page={page['page']}",
                        source_page=page["page"],
                    )
        return list(found.values())

    if saved["url"] == CMU_PUBLICATIONS:
        if venue != "IROS":
            return []
        for node in root.select("td"):
            header = node.find("heading")
            block = header.find_parent("p") if header else None
            text = block.get_text(" ", strip=True) if block else ""
            if (
                not re.search(rf"\b{venue}\s+{year}\b", text)
                or EXCLUDED.search(text)
                or re.search(r"workshop", text, re.I)
            ):
                continue
            for label in block.find_all(["b", "strong"]):
                name = label.get_text(" ", strip=True)
                if not winner_name(name):
                    continue
                links = [
                    a["href"]
                    for a in node.select("a[href]")
                    if re.match(r"https://arxiv\.org/abs/", a["href"])
                ]
                add(
                    header.get_text(" ", strip=True),
                    name,
                    links[0] if len(links) == 1 else "",
                )
                key = (normalized(header.get_text()), award_key(name, venue, year))
                if key in found:
                    found[key].update(
                        evidence_type="institution", evidence_excerpt=text
                    )
        return list(found.values())

    if urlparse(saved["url"]).hostname == "english.sia.cas.cn":
        # Read the institute's own article, not a news-index summary. The
        # publication title and explicit win must occur in the same paragraph.
        if venue != "IROS" or not re.search(
            rf"\b{venue}\s+{year}\b", root.get_text(" ", strip=True)
        ):
            return []
        for node in root.select(".trs_editor_view p"):
            text = node.get_text(" ", strip=True)
            if EXCLUDED.search(text) or re.search(r"workshop", text, re.I):
                continue
            title = node.find("em")
            name = re.search(
                r"\b(?:won|received)\s+(?:the\s+)?(?:only\s+)?((?:Best|Outstanding)(?: Conference| Student)? Paper Award)\b",
                text,
                re.I,
            )
            if (
                title
                and name
                and re.search(r"A paper titled", text, re.I)
                and re.search(rf"\b{venue}\s+{year}\b", text)
            ):
                add(title.get_text(" ", strip=True), name[1])
                key = (normalized(title.get_text()), award_key(name[1], venue, year))
                if key in found:
                    found[key].update(
                        evidence_type="institution", evidence_excerpt=text
                    )
        return list(found.values())

    if venue == "ACL":
        # Main-conference recipients have bold titles, sometimes without
        # paper links. The 2026 first best-paper list has no section heading.
        section = (
            "Best Paper Award"
            if re.search(
                r"Best Paper Awards", soup.title.get_text() if soup.title else ""
            )
            else ""
        )
        industry = False
        for node in root.find_all(["h2", "h3", "li", "p"]):
            text = node.get_text(" ", strip=True)
            if node.name in {"h2", "h3"}:
                if node.name == "h2":
                    industry = "industry track" in text.lower()
                section = (
                    ""
                    if re.search(r"demo|workshop|highlight|\bTACL\b", text, re.I)
                    else text
                    if winner_name(text)
                    else ""
                )
                if section and industry:
                    section = "Industry Track " + section
                continue
            if not section:
                continue
            title = node.find(["strong", "b"])
            if title:
                add(title.get_text(" ", strip=True), section)
            elif industry and node.name == "p" and node.find("br"):
                add(node.get_text("\n", strip=True).split("\n")[0], section)
        return list(found.values())

    if venue == "CVPR":
        if saved["url"] == CVF_AWARDS:
            # This society archive also lists individual and other-conference
            # awards. Only explicitly named CVPR paper-prize tables qualify.
            for heading in root.find_all(["h1", "h2"]):
                name = heading.get_text(" ", strip=True)
                if not (
                    name.startswith("CVPR ") or name == "Longuet-Higgins Prize"
                ) or not winner_name(name):
                    continue
                for sibling in heading.find_next_siblings():
                    if sibling.name in {"h1", "h2"}:
                        break
                    # Flatten cells: the official archive has nested/missing
                    # tr tags, which can otherwise drop an entire recipient.
                    cells = sibling.select("td")
                    for i, cell in enumerate(cells[:-1]):
                        if cell.get_text(strip=True) == str(year):
                            title = cells[i + 1].get_text(" ", strip=True)
                            if re.fullmatch(r'[“"].+[”"]', title):
                                add(title.strip('“”"'), name)
            return list(found.values())
        section = ""
        for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
            text = node.get_text(" ", strip=True)
            label = re.fullmatch(
                r"(?:CVPR\s+\d{4}\s+)?Best(?: Student)? Papers?(?: Awards?)?(?: Honorable Mentions?)?(?:\s*:\s*(?:ID:\s*\d+)?)?",
                text,
            )
            if label:
                section = text.rstrip(" :") if winner_name(text) else ""
                continue
            if node.name.startswith("h"):
                section = ""
                continue
            if not section:
                continue
            if node.name == "p" and text.startswith("Paper Name:"):
                add(text.removeprefix("Paper Name:").strip(), section)
            elif node.name == "li":
                title = next(
                    (
                        n
                        for n in node.find_all(["strong", "b"])
                        if len(n.get_text(strip=True)) >= 12
                    ),
                    None,
                )
                if title:
                    anchor = title.find_parent("a", href=True)
                    link = urljoin(saved["url"], anchor["href"]) if anchor else ""
                    add(
                        title.get_text(" ", strip=True),
                        section,
                        link if link and trusted(link) else "",
                    )
        return list(found.values())

    # RAS award archives have a single prize and explicit year-separated
    # recipients. Only the matching award/year block is evidence, not names
    # or dates in the navigation, eligibility text or other award pages.
    heading = root.find("h1")
    if (
        urlparse(saved["url"]).hostname == "www.ieee-ras.org"
        and venue == "ICRA"
        and heading
        and winner_name(heading.get_text(" ", strip=True))
        and (
            "ICRA" in heading.get_text()
            or "International Conference on Robotics and Automation"
            in heading.get_text()
        )
    ):
        content = root.get_text("\n", strip=True).split("Winners of this Award", 1)
        if len(content) == 2:
            active, lines = None, []
            for line in content[1].split("Related Pages", 1)[0].splitlines():
                edition = re.fullmatch(r"(20\d{2})(?:\s*[–-].*)?", line.strip())
                if edition:
                    active = int(edition[1])
                elif active == year:
                    lines.append(line)
            for title in re.findall(r'[“"]\s*(.+?)\s*[”"]', "\n".join(lines), re.S):
                add(title, heading.get_text(" ", strip=True))
        return list(found.values())

    # The award sponsor keeps a TLS-valid official retrospective when the
    # former IROS conference host's certificate has expired. This is only
    # the named rescue-robotics prize, never the institute's other awards.
    if saved["url"] == IROS_RESCUE and venue == "IROS":
        marker = root.find(
            string=lambda t: t and "Past Winners of the IEEE IROS Best Paper Award" in t
        )
        if marker:
            content = root.get_text("\n", strip=True).split(
                "Past Winners of the IEEE IROS Best Paper Award", 1
            )[1]
            blocks = re.split(r"Winners of the Best Paper Award\s*(20\d{2})", content)
            for i in range(1, len(blocks), 2):
                if int(blocks[i]) != year:
                    continue
                title = re.search(
                    r"[〖【]Winning Paper[〗】]\s*(.+?)(?=[〖【]|$)",
                    blocks[i + 1],
                    re.S,
                )
                if title:
                    add(
                        title[1],
                        "IEEE IROS Best Paper Award on Safety, Security, and Rescue Robotics in memory of Motohiro Kisoi",
                    )
        return list(found.values())

    # RSS has a separate retrospective page: a year-labeled recipient card
    # contains authors and then a quoted title, with no paper hyperlink.
    # Never promote an award-description-only page or a commented old card.
    if (
        venue == "RSS"
        and root.find("h1")
        and TEST_OF_TIME.search(root.find("h1").get_text(" ", strip=True))
    ):
        for heading in root.find_all("h2"):
            if heading.get_text(" ", strip=True) != f"{year} Award Recipient":
                continue
            if not re.search(
                rf"{year}\s+Test of Time Award goes to", root.get_text(" ", strip=True)
            ):
                continue
            for paragraph in heading.find_next_siblings("p"):
                title = re.fullmatch(
                    r'[“"](.+)[”"]', paragraph.get_text(" ", strip=True)
                )
                if title:
                    add(title[1], "Test of Time Award")
                    break

    # NeurIPS/ICML award tables label each paper independently.
    for row in root.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if (
            venue in {"NeurIPS", "ICML"}
            and len(cells) >= 2
            and winner_name(cells[0].get_text(" ", strip=True))
        ):
            a = cells[1].find("a", href=True)
            if a and not winner_name(a.get_text(" ", strip=True)):
                add(
                    a.get_text(" ", strip=True),
                    cells[0].get_text(" ", strip=True),
                    a["href"],
                )
            elif award_kind(cells[0].get_text(" ", strip=True)) == "test-of-time":
                # NeurIPS links the award talk rather than the paper in this
                # card. Its official abstract contains the recipient as a
                # Markdown paper link; do not resolve "Test of Time Award".
                link = re.search(
                    r"\[([^\]\n]+)\]\((https://[^\s)]+)\)",
                    cells[1].get_text(" ", strip=True),
                )
                if link and trusted(link[2]):
                    title = re.sub(
                        r"\s*\(Test\W+of\W+Time(?:\s+Paper)?\s+Award\)\s*$",
                        "",
                        link[1],
                        flags=re.I,
                    )
                    add(title, cells[0].get_text(" ", strip=True), link[2])
    section = ""
    active_year = year
    awaiting_winner = False
    page_finalists = bool(
        re.search(
            r"awards?(?:\s+and)?\s+finalists",
            (soup.find("h1") or soup).get_text(" ", strip=True)[:100],
            re.I,
        )
    ) or bool(
        re.search(
            r"select an award to see its finalists",
            root.get_text(" ", strip=True),
            re.I,
        )
    )
    for node in root.find_all(["h2", "h3", "h4", "p", "li"]):
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        if venue == "AAAI":
            if re.fullmatch(r"20\d\d", text):
                active_year = int(text)
                section = ""
                continue
            if node.name.startswith("h"):
                edition = re.search(r"AAAI[- ](\d{2})(?!\d)", text)
                if edition:
                    active_year = 2000 + int(edition[1])
        if active_year != year:
            continue
        if venue == "ICRA" and re.search(
            r"^Awards? Committee\b|^Other Finalists\b|^In addition.+\bfinalists\b",
            text,
            re.I,
        ):
            section = ""
            awaiting_winner = False
            continue
        is_heading = node.name.startswith("h") or (
            node.name == "p" and len(text) < 120 and text.rstrip().endswith(":")
        )
        if is_heading:
            if re.fullmatch(r"Award Winners?\s*:", text, re.I):
                awaiting_winner = winner_name(section)
            elif EXCLUDED.search(text) or re.search(
                r"selection process|committee|other finalists", text, re.I
            ):
                section = ""
                awaiting_winner = False
            elif winner_name(text):
                section = text.rstrip(" :")
                awaiting_winner = False
            elif node.name in {"h2", "h3", "h4"}:
                section = ""
            continue
        # A winner marker can occur inside the finalists list (RSS); use the
        # marker's actual prize, never the surrounding finalist heading.
        label = node.select_one(".winner-label")
        name = (
            re.sub(r"^Winner\s*:\s*", "", label.get_text(" ", strip=True))
            if label
            else section
        )
        explicit = label is not None or awaiting_winner
        if not winner_name(name) or (page_finalists and not explicit):
            continue
        anchors = [
            a
            for a in node.find_all("a", href=True)
            if re.search(
                r"arxiv\.org|openreview\.net|/virtual/|/program/papers/|roboticsproceedings\.org|mlr\.press",
                a["href"],
            )
        ]
        if anchors:
            a = anchors[0]
            if node.name == "p" and not text.startswith(a.get_text(" ", strip=True)):
                # A linked earlier paper in the committee's explanation is
                # background, not another recipient of the prize.
                continue
            add(a.get_text(" ", strip=True), name, a["href"])
        elif explicit and (
            node.find("em") or node.find("i") or re.match(r'[“"]', text)
        ):
            # ICRA names the winner in plain italic text, followed by Authors.
            title = re.split(r"\bAuthors?\s*:", node.get_text(" ", strip=True))[0]
            title = re.sub(r"\s+([:;,])", r"\1", title)
            add(title, name)
        elif venue == "AAAI" and node.find(["strong", "em", "b"]):
            title = node.find(["strong", "em", "b"]).get_text(" ", strip=True)
            add(title, name)
        elif venue == "CoRL" and node.name == "li":
            add(node.get_text("\n", strip=True).split("\n")[0], name)
        # A plural winner block can contain several paper paragraphs. The
        # next prize/finalist heading ends it; an author paragraph isn't a paper.
    return list(found.values())


def _collection_path(spec):
    key = hashlib.sha256(
        db.dumps([COLLECTION_VERSION, spec["venue"], spec["year"]]).encode()
    ).hexdigest()
    return config.DATA / "cache" / ("award-collection-" + key + ".json")


def announcement_winners(saved, venue, year):
    """RAS recipient interviews can precede the annual prize archive update.

    An explicit award declaration identifies the project; its linked arXiv ID
    or an unambiguous title-prefix match supplies the full paper title. A model
    never guesses the recipient, the prize or the conference year.
    """
    if venue != "ICRA" or urlparse(saved["url"]).hostname != "www.ieee-ras.org":
        return []
    soup = BeautifulSoup(saved["html"], "html.parser")
    if not soup.find("h1") or "/awards-recognition/" in saved["url"]:
        return []
    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        match = re.search(
            rf"(?:received|won)\s+(?:the\s+)?ICRA\s+{year}\s+[^.]*?\bAward\s+for\s+([A-Z][\w-]+)",
            text,
        )
        if not match:
            match = re.search(
                rf"\bThe ([A-Z][\w-]+) research project achieved[^.]*?\bICRA\s+{year}\s*,?\s*winning\b",
                text,
            )
        if not match or EXCLUDED.search(text):
            continue
        token = match[1]
        names = re.findall(
            r"(?:Best|Outstanding)(?: Conference| Student)? Paper Award(?: on [A-Za-z -]+)?",
            text,
        )
        if not names:
            continue
        identifiers = {
            m[1]
            for a in soup.find_all("a", href=True)
            if (
                m := re.fullmatch(
                    r"https://arxiv\.org/abs/(\d{4}\.\d{4,5})(?:v\d+)?/?", a["href"]
                )
            )
        }
        params = (
            {"id_list": next(iter(identifiers))}
            if len(identifiers) == 1
            else {"search_query": f'ti:"{token}"', "max_results": 10}
        )
        rows = papers.entries(
            papers.fetch("https://export.arxiv.org/api/query", params)
        )
        matches = [
            r
            for r in rows
            if normalized(r["title"]).startswith(normalized(token))
            and (len(identifiers) != 1 or r["source_id"] in identifiers)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Official award declaration for {token}; full paper title could not be matched unambiguously"
            )
        paper = matches[0]
        return [
            {
                "title": paper["title"],
                "venue": venue,
                "year": year,
                "name": "IEEE ICRA " + name.strip(),
                "kind": "research-paper",
                "status": "winner",
                "verified": True,
                "official_url": saved["url"],
                "paper_url": "https://arxiv.org/abs/" + paper["source_id"],
                "retrieved_at": saved["retrieved_at"],
                "source_sha256": saved["sha256"],
                "area": "robotics",
            }
            for name in names
        ]
    return []


def upcoming_date(document, year, now=None):
    """A dated official conference home page can distinguish an upcoming edition."""
    url = urlparse(document["url"])
    if url.path not in {"/", f"/Conferences/{year}", f"/Conferences/{year}/"}:
        return None
    soup = BeautifulSoup(document["html"], "html.parser")
    if not soup.title or str(year) not in soup.title.get_text():
        return None
    for node in soup.select("script,style,nav,header,footer"):
        node.decompose()
    text = soup.get_text(" ", strip=True)[:1500]
    months = {
        name.lower(): i
        for i, name in enumerate(
            [
                "January",
                "February",
                "March",
                "April",
                "May",
                "June",
                "July",
                "August",
                "September",
                "October",
                "November",
                "December",
            ],
            1,
        )
    }
    matches = re.finditer(
        r"\b("
        + "|".join(name + "|" + name[:3] for name in months)
        + r")\.?\s+(\d{1,2})(?!\d)",
        text,
        re.I,
    )
    today = now or dt.datetime.now(ZoneInfo("Asia/Tokyo")).date()
    for match in matches:
        month = next(
            i for name, i in months.items() if name.startswith(match[1].lower())
        )
        try:
            day = dt.date(year, month, int(match[2]))
        except ValueError:
            continue
        if day > today:
            return day.isoformat()
        return None
    return None


def category_coverage(documents, spec, winners):
    """Known prize categories, not an assertion that all prizes were found."""
    categories = {}
    for document in documents:
        soup = BeautifulSoup(document["html"], "html.parser")
        root = soup.select_one(".entry-content, main, article") or soup
        for element in root.select("script,style,nav,header,footer"):
            element.decompose()
        # Historic society archives do not establish which prizes were
        # offered this edition. The conference's own page/PDF does.
        if not re.search(
            rf"{spec['venue']}\s+{spec['year']}\b", root.get_text(" ", strip=True)
        ):
            continue
        labels = [n.get_text(" ", strip=True) for n in root.select("h2,h3,h4")]
        if document.get("format") == "pdf":
            for page in document["pages"]:
                lines = page["text"].splitlines()
                labels += [
                    " ".join(lines[i : i + 3])
                    for i, line in enumerate(lines[:8])
                    if re.match(r"^(?:IEEE )?ICRA Best", line)
                    and "Finalists" in " ".join(lines[i : i + 3])
                ]
        for name in labels:
            name = re.split(r"\s*[-–]?\s*Finalists\b", name, flags=re.I)[0].strip()
            name = re.sub(r"^Best Best\b", "Best", name)
            prefixless = re.sub(
                rf"^(?:IEEE\s+)?{spec['venue']}(?:\s+{spec['year']})?\s+", "", name
            )
            if (
                not winner_name(name)
                or not re.match(
                    r"^(?:Best|Outstanding|Test of Time|Longuet-Higgins)", prefixless
                )
                or len(name) > 150
                or re.search(r"committee|recipients|workshop|demo", name, re.I)
            ):
                continue
            key = award_key(name, spec["venue"], spec["year"])
            categories.setdefault(
                key, {"name": name, "winner_count": 0, "official_url": document["url"]}
            )
    for winner in winners:
        key = award_key(winner["name"], spec["venue"], spec["year"])
        categories.setdefault(
            key,
            {
                "name": winner["name"],
                "winner_count": 0,
                "official_url": winner["official_url"],
            },
        )["winner_count"] += 1
    missing = [v for v in categories.values() if not v["winner_count"]]
    return {
        "categories": list(categories.values()),
        "missing_categories": missing,
        "confirmed_categories": sum(v["winner_count"] > 0 for v in categories.values()),
        "category_count": len(categories),
        "partial": bool(missing),
    }


def collect(spec, *, refresh=False, refreshed_after=None, check=None):
    """Check official primary and archive pages; save a readable coverage receipt."""
    documents, failures = [], []

    def retrieve(url):
        if check:
            check()
        return (
            fetch(url, refresh=True, refreshed_after=refreshed_after)
            if refresh
            else fetch(url)
        )

    seeds = [spec["url"], *spec.get("alternate_urls", [])]
    if spec["venue"] == "ICML":
        seeds.append(f"https://icml.cc/virtual/{spec['year']}/awards_detail")
    for url in dict.fromkeys(seeds):
        if (
            spec["venue"] == "ACL"
            and documents
            and parse(documents[0], "ACL", spec["year"])
        ):
            break
        try:
            documents.append(retrieve(url))
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"url": url, "reason": str(exc)[:240]})
    urls = []
    for document in list(documents):
        if (
            urlparse(document["url"]).hostname == "www.ieee-ras.org"
            and document["url"] not in {RAS_INDEX, RAS_FEATURES}
            and "brochure" not in document["url"]
        ):
            continue
        soup = BeautifulSoup(document["html"], "html.parser")
        # Official programs can link their separate Test of Time page only
        # in navigation. Discover those links, but parse winners from content.
        for element in soup.select("script, style"):
            element.decompose()
        for a in soup.find_all("a", href=True):
            url = urljoin(document["url"], a["href"])
            text = a.get_text(" ", strip=True)
            archive = document["url"] == RAS_INDEX
            if document["url"] == RAS_FEATURES:
                card = a.find_parent(class_="e-loop-item")
                relevant = bool(
                    card
                    and str(spec["year"]) in card.get_text(" ", strip=True)
                    and a.find_parent("h2")
                )
            elif archive:
                relevant = (
                    "ICRA" in text
                    and winner_name(text)
                    and "/awards-recognition/conference-awards/" in url
                )
            elif (
                urlparse(document["url"]).hostname == "www.ieee-ras.org"
                and "brochure" in document["url"]
            ):
                relevant = bool(
                    urlparse(url).path.lower().endswith(".pdf")
                    and str(spec["year"]) in url
                )
            elif urlparse(document["url"]).hostname == "english.sia.cas.cn":
                relevant = bool(
                    re.search(rf"\b{spec['venue']}\s+{spec['year']}\b", text)
                    and re.search(
                        r"\b(?:wins?|won|receives?|received)\b.*\bpaper award\b",
                        text,
                        re.I,
                    )
                )
            else:
                relevant = (
                    bool(
                        re.search(r"award|outstanding|brochure", text + " " + url, re.I)
                    )
                    and not re.search(r"/author/|/category/|#", url)
                    and (
                        str(spec["year"]) in url
                        or spec["venue"] in {"ICRA", "IROS", "CoRL"}
                    )
                    and not re.search(
                        r"travel|nomination|registration|reviewer",
                        text + " " + url,
                        re.I,
                    )
                )
            if (
                relevant
                and trusted(url)
                and url not in urls
                and url not in {d["url"] for d in documents}
            ):
                urls.append(url)
    # Twelve current ICRA paper prizes can live on separate archive pages.
    for url in urls[: 30 if spec["venue"] == "ICRA" else 8]:
        try:
            documents.append(retrieve(url))
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"url": url, "reason": str(exc)[:240]})
    winners = {}
    for document in documents:
        if check:
            check()
        rows = parse(document, spec["venue"], spec["year"])
        try:
            rows.extend(announcement_winners(document, spec["venue"], spec["year"]))
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"url": document["url"], "reason": str(exc)[:240]})
        for winner in rows:
            key = (
                normalized(winner["title"]),
                award_key(winner["name"], spec["venue"], spec["year"]),
            )
            winners.setdefault(key, winner)
    upcoming = next(
        (day for d in documents if (day := upcoming_date(d, spec["year"]))), None
    )
    finalists = any(
        re.search(
            r"awards?(?:\s+and)?\s+finalists|select an award to see its finalists|ICRA Best.+?Finalists",
            BeautifulSoup(d["html"], "html.parser").get_text(" ", strip=True),
            re.I,
        )
        for d in documents
    )
    status = (
        "verified winners"
        if winners
        else "not announced"
        if upcoming
        else "finalists only"
        if finalists
        else "no confirmed winners"
        if documents
        else "unavailable"
    )
    result = {
        "version": COLLECTION_VERSION,
        "papers": list(winners.values()),
        "source": spec,
        "checked_at": time.time(),
        "failures": failures,
        "status": status,
        "conference_start": upcoming,
        "winner_count": len(winners),
        "paper_count": len({normalized(w["title"]) for w in winners.values()}),
        "coverage": category_coverage(documents, spec, list(winners.values())),
        "documents": [
            {
                k: d[k]
                for k in (
                    "url",
                    "cache_url",
                    "retrieved_at",
                    "sha256",
                    "format",
                    "pdf_path",
                )
                if k in d
            }
            for d in documents
        ],
    }
    path = _collection_path(spec)
    if not documents and path.is_file():
        try:
            previous = json.loads(path.read_text())
        except (OSError, ValueError):
            previous = {}
        if previous.get("papers"):
            result.update(
                papers=previous["papers"],
                winner_count=previous["winner_count"],
                paper_count=previous["paper_count"],
                documents=previous.get("documents", []),
                stale=True,
                last_successful_check=previous.get("last_successful_check")
                or previous.get("checked_at"),
                coverage=previous.get("coverage", {}),
            )
    pending = path.with_suffix(".tmp")
    pending.write_text(db.dumps(result), encoding="utf-8")
    pending.replace(path)
    if not documents:
        # Keep the failure receipt for the UI, while the nightly worker's
        # bounded retry loop still gets a chance to recover a network outage.
        raise ValueError(
            "No official award page could be retrieved: " + failures[0]["reason"]
        )
    return result


def catalogue(now=None):
    """Read stored official receipts only; UI polling never fetches external pages."""
    now = now or dt.datetime.now(ZoneInfo("Asia/Tokyo"))
    rows = []
    for spec in sources(now.year, db.settings()["nightly_video_categories"]):
        path = _collection_path(spec)
        try:
            result = json.loads(path.read_text())
        except (OSError, ValueError):
            result = {
                "source": spec,
                "status": "not checked",
                "papers": [],
                "winner_count": 0,
                "paper_count": 0,
                "failures": [],
            }
        rows.append(result)
    return {
        "version": COLLECTION_VERSION,
        "sources": rows,
        "winner_count": sum(r["winner_count"] for r in rows),
        "paper_count": len({normalized(w["title"]) for r in rows for w in r["papers"]}),
        "refresh_job": db.one(
            "SELECT id,state,stage,progress,error FROM jobs WHERE kind='award_refresh' ORDER BY created DESC LIMIT 1"
        ),
    }


def start_refresh():
    """A separate acquisition job never starts videos or changes paused lessons."""
    now = dt.datetime.now(ZoneInfo("Asia/Tokyo"))
    return db.enqueue(
        "award_refresh",
        "conference-awards",
        {
            "sources": sources(now.year, db.settings()["nightly_video_categories"]),
        },
        priority=8,
    )


def refresh_step(job, runtime=None):
    """Checkpoint one conference edition; exhaust three retries then continue."""
    specs = job["payload"]["sources"]
    cp = job["checkpoint"]
    index = cp.get("source_index", 0)
    if index >= len(specs):
        db.patch_job(job["id"], stage="受賞情報の更新が完了", progress=1)
        return True
    spec = specs[index]
    db.patch_job(
        job["id"],
        stage=f"受賞情報を更新 · {spec['venue']} {spec['year']} · {index + 1}/{len(specs)}",
    )

    def check():
        from .runtime import PracticePreempted

        current = db.one("SELECT state FROM jobs WHERE id=?", (job["id"],))
        if not current or current["state"] not in {"running", "queued"}:
            raise PracticePreempted("受賞情報の更新を中断しました")
        if db.one(
            "SELECT id FROM jobs WHERE kind='practice' AND state IN ('queued','running') AND available<=? LIMIT 1",
            (time.time(),),
        ):
            raise PracticePreempted(
                "録音の評価を優先します。受賞情報の更新は続きから再開します"
            )

    if cp.get("collected") and runtime is not None:
        from . import research

        state = cp.setdefault("local_ai", {})
        complete = research.repair_step(spec, runtime, state, check=check)
        db.patch_job(
            job["id"],
            checkpoint=cp,
            stage=f"ローカルAIで受賞情報を補完 · {spec['venue']} {spec['year']} · {state.get('index', 0)}出典確認",
        )
        if not complete:
            return False
    else:
        try:
            collect(spec, refresh=True, refreshed_after=job["created"], check=check)
        except (httpx.HTTPError, ValueError) as exc:
            count = cp.get("source_failures", 0) + 1
            cp["source_failures"] = count
            cp["last_error"] = str(exc)[:240]
            if count < 3:
                db.patch_job(
                    job["id"],
                    checkpoint=cp,
                    available=time.time() + 5,
                    stage=f"{spec['venue']} {spec['year']} · 再試行 {count}/3",
                )
                return False
            cp.setdefault("skipped", []).append(
                {"source": spec, "reason": cp.pop("last_error")}
            )
        else:
            if runtime is not None:
                cp["collected"] = True
                db.patch_job(job["id"], checkpoint=cp)
                return False
    cp.pop("source_failures", None)
    cp.pop("last_error", None)
    cp.pop("collected", None)
    cp.pop("local_ai", None)
    cp["source_index"] = index + 1
    db.patch_job(job["id"], checkpoint=cp, progress=(index + 1) / len(specs))
    return False


def verified(meta, *, year=None):
    result = []
    for a in meta.get("awards", []):
        if (
            a.get("verified") is True
            and a.get("status") == "winner"
            and a.get("venue") in VENUES
            and winner_name(a.get("name", ""))
            and trusted(a.get("official_url", ""))
            and a.get("source_sha256")
            and isinstance(a.get("year"), int)
            and (year is None or year - 1 <= a["year"] <= year)
            and normalized(a.get("title", "")) == normalized(meta.get("title", ""))
        ):
            result.append(a | {"kind": award_kind(a["name"])})
    return result


def resolve(winner):
    """Resolve to arXiv only after an exact normalized title match; skip ambiguity."""
    key = hashlib.sha256(normalized(winner["title"]).encode()).hexdigest()
    path = config.DATA / "cache" / ("award-paper-" + key + ".json")
    if path.is_file():
        saved = json.loads(path.read_text())
        if (
            saved["metadata"] or saved.get("query_version") == 3
        ) and time.time() - saved["retrieved_at"] < (
            7 if saved["metadata"] else 1
        ) * 86400:
            return saved["metadata"]
    # Normalize typography for the query while still checking the full title.
    title = winner["title"].translate(
        str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
    )
    title = re.sub(r'["\r\n]', " ", title)
    params = {"search_query": 'ti:"' + title + '"', "max_results": 10}
    try:
        base, version = papers.parse_reference(winner.get("paper_url", ""))
    except ValueError:
        pass
    else:
        params = {"id_list": base + version}
    matches = papers.entries(
        papers.fetch(
            "https://export.arxiv.org/api/query",
            params,
            cache_scope=time.strftime("%Y-%m-%d", time.gmtime()),
            attempts=1,
            timeout=45,
        )
    )
    exact = {
        m["source_id"]: m
        for m in matches
        if normalized(m["title"]) == normalized(title)
    }
    meta = next(iter(exact.values())) if len(exact) == 1 else None
    cache_match(winner, meta)
    return meta


def cache_match(winner, meta, *, query=None):
    """Retain verified broader-search identity for the next unattended run."""
    if meta and normalized(meta["title"]) != normalized(winner["title"]):
        raise ValueError("Paper cache requires an exact normalized title match")
    key = hashlib.sha256(normalized(winner["title"]).encode()).hexdigest()
    path = config.DATA / "cache" / ("award-paper-" + key + ".json")
    temp = path.with_suffix(".tmp")
    temp.write_text(
        db.dumps(
            {
                "retrieved_at": time.time(),
                "metadata": meta,
                "query_version": 3,
                "query": query,
            }
        )
    )
    temp.replace(path)
