from __future__ import annotations

import fcntl
import hashlib
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
import pymupdf as fitz
from bs4 import BeautifulSoup
from lxml import etree

from . import config, db

ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(v[1-9]\d*)?$")


def parse_reference(value):
    value = value.strip()
    if "://" in value:
        u = urlparse(value)
        if (
            u.scheme not in {"https", "http"}
            or u.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
            or u.username
            or u.password
            or u.port
        ):
            raise ValueError("Use an arXiv URL or paper ID.")
        value = re.sub(r"^/(abs|pdf|html)/", "", u.path)
        if value.endswith(".pdf"):
            value = value[:-4]
    m = ARXIV_ID.fullmatch(value)
    if not m:
        raise ValueError("This is not an arXiv paper ID.")
    return m.group(1), m.group(2) or ""


def fetch(url, params=None, cache=True, cache_scope=""):
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in {
        "arxiv.org",
        "export.arxiv.org",
        "www.arxiv.org",
    }:
        raise ValueError("Only arXiv downloads are supported.")
    key = hashlib.sha256(
        (url + repr(sorted((params or {}).items())) + cache_scope).encode()
    ).hexdigest()
    cached = config.DATA / "cache" / key
    if cache and cached.exists():
        return cached.read_bytes()
    # A process-wide and cross-process throttle for all arXiv traffic.
    with open(config.DATA / "cache/arxiv.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stamp = config.DATA / "cache/arxiv.last"
        for retry in range(4):
            last = float(stamp.read_text()) if stamp.exists() else 0
            time.sleep(max(0, 3.1 - (time.time() - last)))
            stamp.write_text(str(time.time()))
            try:
                with httpx.Client(
                    timeout=90,
                    follow_redirects=False,
                    headers={
                        "User-Agent": "PaperSpeakLinux/0.1 (personal research reader)"
                    },
                ) as client:
                    r = client.get(url, params=params)
                    # Follow only trusted arXiv redirects, preserving the allowlist.
                    if r.is_redirect:
                        location = urljoin(url, r.headers["location"])
                        if urlparse(location).hostname not in {
                            "arxiv.org",
                            "export.arxiv.org",
                            "www.arxiv.org",
                        }:
                            raise ValueError("Untrusted redirect")
                        time.sleep(3.1)
                        stamp.write_text(str(time.time()))
                        r = client.get(location)
                    if r.status_code in {429, 500, 502, 503, 504}:
                        time.sleep(min(60, 5 * 2**retry))
                        continue
                    r.raise_for_status()
                    if len(r.content) > 100 * 1024 * 1024:
                        raise ValueError("Paper is too large (100 MB limit).")
                    if cache:
                        temp = cached.with_suffix(".tmp")
                        temp.write_bytes(r.content)
                        temp.replace(cached)
                    return r.content
            except httpx.TransportError:
                if retry == 3:
                    raise
                time.sleep(3 * 2**retry)
    raise RuntimeError("arXiv is busy. Please try again later.")


def entries(content):
    root = etree.fromstring(
        content, etree.XMLParser(resolve_entities=False, no_network=True)
    )
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    result = []
    for e in root.findall("a:entry", ns):
        identifier = e.findtext("a:id", "", ns)
        if "/api/errors" in identifier:
            raise ValueError(
                "arXiv returned an API error: "
                + e.findtext("a:summary", "Unknown error", ns)[:300]
            )
        try:
            base, version = parse_reference(identifier)
        except ValueError:
            continue
        clean = lambda s: re.sub(r"\s+", " ", s or "").strip()
        result.append(
            {
                "source_id": base,
                "version": version,
                "title": clean(e.findtext("a:title", "", ns)),
                "abstract": clean(e.findtext("a:summary", "", ns)),
                "authors": [
                    clean(a.findtext("a:name", "", ns))
                    for a in e.findall("a:author", ns)
                ],
                "categories": [a.get("term") for a in e.findall("a:category", ns)],
                "published": e.findtext("a:published", "", ns),
                "updated": e.findtext("a:updated", "", ns),
                "url": f"https://arxiv.org/abs/{base}{version}",
            }
        )
    return result


def register(meta):
    ident = db.uid()
    now = time.time()
    with db.connection() as c:
        c.execute(
            "INSERT OR IGNORE INTO papers VALUES (?,?,?,?,?,?,?,?)",
            (
                ident,
                meta["source_id"],
                meta["version"],
                meta["title"],
                db.dumps(meta),
                "new",
                now,
                now,
            ),
        )
        return c.execute(
            "SELECT id FROM papers WHERE source_id=? AND version=?",
            (meta["source_id"], meta["version"]),
        ).fetchone()[0]


def register_arxiv(ref):
    base, version = parse_reference(ref)
    items = entries(
        fetch(
            "https://export.arxiv.org/api/query",
            {"id_list": base + version},
            cache=bool(version),
        )
    )
    if not items:
        raise ValueError("This paper was not found on arXiv.")
    return register(items[0])


def split_text(text, maximum=4500):
    # Retain all content; split on paragraph/sentence boundaries where possible.
    while len(text) > maximum:
        at = max(text.rfind("\n", 0, maximum), text.rfind(". ", 0, maximum))
        if at < maximum // 2:
            at = maximum
        else:
            at += 1
        yield text[:at].strip()
        text = text[at:].strip()
    if text.strip():
        yield text.strip()


def html_sources(raw, paper_id, url):
    soup = BeautifulSoup(raw, "lxml")
    root = soup.select_one(".ltx_document")
    if root is None:
        return []
    for el in root.select(
        "script,style,nav,.ltx_bibliography,.ltx_page_header,.ltx_page_footer"
    ):
        el.decompose()
    for m in root.find_all("math"):
        alt = m.get("alttext")
        if alt:
            m.replace_with(" " + alt + " ")
        else:
            for a in m.find_all(["annotation", "annotation-xml"]):
                a.decompose()
    results = []
    heading = "Introduction"
    for el in root.select(
        "h2.ltx_title,h3.ltx_title,h4.ltx_title,p.ltx_p,figure.ltx_figure,figure.ltx_table,.ltx_equation"
    ):
        if el.name.startswith("h"):
            heading = el.get_text(" ", strip=True)
            continue
        if el.find_parent(["figure"]) is not None:
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        kind = (
            "figure"
            if "ltx_figure" in el.get("class", [])
            else "table"
            if "ltx_table" in el.get("class", [])
            else "equation"
            if "ltx_equation" in el.get("class", [])
            else "text"
        )
        anchor = el.get("id") or (el.parent.get("id", "") if el.parent else "")
        image = el.find("img")
        for part in split_text(text):
            sid = f"{paper_id}:H{len(results) + 1}"
            data = {
                "label": heading,
                "text": part,
                "url": url + ("#" + anchor if anchor else ""),
                "anchor": anchor,
            }
            if image and image.get("src"):
                data["image_url"] = urljoin(url, image["src"])
            results.append({"id": sid, "kind": kind, "data": data})
    return results


def link_visual_sources(sources):
    pages = {}
    for source in sources:
        if source["kind"] == "page":
            pages.setdefault(source["data"]["page"], []).append(source)
    for source in sources:
        if source["kind"] not in {"figure", "table"}:
            continue
        label = re.match(r"(Figure|Table)\s+(\d+)\s*:", source["data"]["text"])
        if not label:
            continue
        pattern = rf"(?m)^\s*{label[1]}\s+{label[2]}\s*:"
        for page, parts in pages.items():
            if re.search(pattern, "\n".join(p["data"]["text"] for p in parts)):
                source["data"].update(
                    image_path=parts[0]["data"]["image_path"],
                    page=page,
                    pdf_path=parts[0]["data"]["pdf_path"],
                    page_source_id=parts[0]["id"],
                )
                break


def ingest(paper_id):
    paper = db.one("SELECT * FROM papers WHERE id=?", (paper_id,))
    if not paper:
        raise ValueError("Paper not found")
    if paper["state"] == "ready":
        return paper
    meta = paper["data"]
    folder = config.DATA / "papers" / paper_id
    folder.mkdir(exist_ok=True)
    sources = []
    if not meta.get("uploaded"):
        ref = paper["source_id"] + paper["version"]
        try:
            raw = fetch(f"https://arxiv.org/html/{ref}")
            sources = html_sources(raw, paper_id, f"https://arxiv.org/html/{ref}")
        except (httpx.HTTPStatusError, ValueError):
            pass
        pdf = folder / "paper.pdf"
        if not pdf.exists():
            pdf.write_bytes(fetch(f"https://arxiv.org/pdf/{ref}"))
    else:
        pdf = config.safe_path(meta["pdf_path"])
    with fitz.open(pdf) as document:
        if document.needs_pass:
            raise ValueError("Please upload an unlocked PDF.")
        if len(document) > 500:
            raise ValueError("Please split PDFs longer than 500 pages.")
        meta.update(pdf_path=str(pdf.relative_to(config.DATA)), pages=len(document))
        for index, page in enumerate(document):
            text = page.get_text("text", sort=True).strip()
            image_path = folder / f"page-{index + 1}.jpg"
            if not image_path.exists():
                page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False).save(
                    image_path
                )
            # Page evidence preserves references even when richer HTML is available.
            pieces = list(split_text(text)) or [
                "[Image-only page: read the attached page image.]"
            ]
            for j, part in enumerate(pieces):
                sources.append(
                    {
                        "id": f"{paper_id}:P{index + 1}.{j + 1}",
                        "kind": "page",
                        "data": {
                            "label": f"Page {index + 1}",
                            "page": index + 1,
                            "text": part,
                            "image_path": str(image_path.relative_to(config.DATA)),
                            "pdf_path": meta["pdf_path"],
                            "image_only": len(text) < 80,
                            "url": meta.get("url", ""),
                        },
                    }
                )
    if not sources:
        raise ValueError("No readable content was found.")
    link_visual_sources(sources)
    with db.connection() as c:
        c.execute("DELETE FROM sources WHERE paper_id=?", (paper_id,))
        c.executemany(
            "INSERT INTO sources VALUES (?,?,?,?)",
            [(s["id"], paper_id, s["kind"], db.dumps(s["data"])) for s in sources],
        )
        c.execute(
            "UPDATE papers SET data=?,state='ready',updated=? WHERE id=?",
            (db.dumps(meta), time.time(), paper_id),
        )
    db.event("paper", {"id": paper_id})
    return db.one("SELECT * FROM papers WHERE id=?", (paper_id,))
