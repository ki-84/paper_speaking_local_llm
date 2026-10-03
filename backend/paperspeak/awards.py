"""Official conference award discovery; never infer an award from an abstract."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from . import config, db, papers

VENUES = ("ICLR", "ICML", "NeurIPS", "AAAI", "RSS", "ICRA", "CoRL", "IROS")
ROBOTICS = {"RSS", "ICRA", "CoRL", "IROS"}
EXCLUDED = re.compile(
    r"honou?rable|runner.?up|finalist|nomina|test.of.time|classic|reviewer|editor|"
    r"competition|challenge|dissertation|position paper",
    re.I,
)


def normalized(title):
    value = unicodedata.normalize("NFKD", title).casefold()
    return "".join(c for c in value if c.isalnum())


def winner_name(name):
    return bool(
        re.search(r"\b(best|outstanding)\b.*\bpaper", name, re.I)
        and not EXCLUDED.search(name)
    )


def trusted(url):
    u = urlparse(url)
    if u.scheme != "https" or u.username or u.password or u.port:
        return False
    host = u.hostname or ""
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
    )
    return any(host == root or host.endswith("." + root) for root in roots)


def fetch(url):
    """Cache official pages for a day; validate every redirect before following."""
    if not trusted(url):
        raise ValueError("Not an approved conference/paper host")
    key = hashlib.sha256(url.encode()).hexdigest()
    path = config.DATA / "cache" / ("award-page-" + key + ".json")
    if path.is_file():
        saved = json.loads(path.read_text())
        if time.time() - saved["retrieved_at"] < 86400:
            return saved
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        target = url
        for _ in range(5):
            response = client.get(target)
            if response.is_redirect:
                target = urljoin(target, response.headers["location"])
                if not trusted(target):
                    raise ValueError("Untrusted award-page redirect")
                continue
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError("Award page exceeds 5 MB")
            result = {
                "url": target,
                "html": response.text,
                "retrieved_at": time.time(),
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
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
            rows.append({"venue": venue, "year": edition, "url": url})
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
        key = normalized(title)
        found[key] = {
            "title": title,
            "venue": venue,
            "year": year,
            "name": name,
            "status": "winner",
            "verified": True,
            "official_url": saved["url"],
            "paper_url": paper_url,
            "retrieved_at": saved["retrieved_at"],
            "source_sha256": saved["sha256"],
            "area": "robotics" if venue in ROBOTICS else "ai",
        }

    # NeurIPS/ICML award tables label each paper independently.
    for row in root.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if (
            venue in {"NeurIPS", "ICML"}
            and len(cells) >= 2
            and winner_name(cells[0].get_text(" ", strip=True))
        ):
            a = cells[1].find("a", href=True)
            if a:
                add(
                    a.get_text(" ", strip=True),
                    cells[0].get_text(" ", strip=True),
                    a["href"],
                )
    section = ""
    active_year = year
    awaiting_winner = False
    page_finalists = bool(
        re.search(
            r"award finalists",
            (soup.find("h1") or soup).get_text(" ", strip=True)[:100],
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
        is_heading = node.name.startswith("h") or (
            node.name == "p" and len(text) < 120 and text.rstrip().endswith(":")
        )
        if is_heading:
            if re.fullmatch(r"Award Winner\s*:", text, re.I):
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
        elif explicit:
            # ICRA names the winner in plain italic text, followed by Authors.
            title = node.get_text("\n", strip=True).split("Authors:")[0].split("\n")[0]
            add(title, name)
        elif venue == "AAAI" and node.find(["strong", "em", "b"]):
            title = node.find(["strong", "em", "b"]).get_text(" ", strip=True)
            add(title, name)
        elif venue == "CoRL" and node.name == "li":
            add(node.get_text("\n", strip=True).split("\n")[0], name)
        awaiting_winner = False
    return list(found.values())


def collect(spec):
    """Discover a bounded number of award announcement links on official sites."""
    try:
        saved = fetch(spec["url"])
    except httpx.HTTPStatusError:
        if spec["venue"] != "ICML":
            raise
        saved = fetch(f"https://icml.cc/virtual/{spec['year']}/awards_detail")
    documents = [saved]
    soup = BeautifulSoup(saved["html"], "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        url = urljoin(saved["url"], a["href"])
        text = a.get_text(" ", strip=True)
        if (
            trusted(url)
            and url != saved["url"]
            and url not in urls
            and re.search(r"award|outstanding", text + " " + url, re.I)
            and not re.search(r"/author/|/category/|#|\.pdf$", url)
            and (str(spec["year"]) in url or spec["venue"] in {"ICRA", "IROS", "CoRL"})
        ):
            urls.append(url)
    failures = []
    for url in urls[:3]:
        try:
            documents.append(fetch(url))
        except (httpx.HTTPError, ValueError) as exc:
            failures.append({"url": url, "reason": str(exc)[:200]})
    winners = {}
    for document in documents:
        for winner in parse(document, spec["venue"], spec["year"]):
            winners[(normalized(winner["title"]), winner["name"])] = winner
    return {
        "papers": list(winners.values()),
        "source": spec,
        "checked_at": time.time(),
        "failures": failures,
        "status": "verified winners" if winners else "no confirmed winners",
    }


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
            result.append(a)
    return result


def resolve(winner):
    """Resolve to arXiv only after an exact normalized title match; skip ambiguity."""
    key = hashlib.sha256(normalized(winner["title"]).encode()).hexdigest()
    path = config.DATA / "cache" / ("award-paper-" + key + ".json")
    if path.is_file():
        saved = json.loads(path.read_text())
        if (
            saved["metadata"] or saved.get("query_version") == 2
        ) and time.time() - saved["retrieved_at"] < (
            7 if saved["metadata"] else 1
        ) * 86400:
            return saved["metadata"]
    # Normalize typography for the query while still checking the full title.
    title = winner["title"].translate(
        str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
    )
    title = re.sub(r'["\r\n]', " ", title)
    matches = papers.entries(
        papers.fetch(
            "https://export.arxiv.org/api/query",
            {"search_query": 'ti:"' + title + '"', "max_results": 10},
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
    temp = path.with_suffix(".tmp")
    temp.write_text(
        db.dumps({"retrieved_at": time.time(), "metadata": meta, "query_version": 2})
    )
    temp.replace(path)
    return meta
