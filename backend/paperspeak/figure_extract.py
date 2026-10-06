"""Offline PDF figure candidates. Crops are reviewed before use in a lesson."""

from __future__ import annotations

import hashlib
import re
import time

import pymupdf as fitz

from . import config, db

EXTRACT_VERSION = 5
CAPTION_TEXT = r"(Figure|Fig\.?|Table)\s+([A-Z]?\d+(?:\.\d+)?)\s*[:.\u2013\u2014-]\s*"
CAPTION = re.compile(r"^\s*" + CAPTION_TEXT, re.I)
CAPTION_ANY = re.compile(r"(?<!\w)" + CAPTION_TEXT, re.I)
CAPTION_PLAIN = re.compile(
    r"^\s*(Figure|Fig\.?|Table)\s+([A-Z]?\d+(?:\.\d+)?)(?=\s|[:.\u2013\u2014-])", re.I
)


def caption_matches(text):
    """Accept unpunctuated captions, exclude prose references such as Figure 10.(b) compares."""
    first = CAPTION_PLAIN.match(text)
    if not first:
        return []

    def caption_tail(match):
        tail = text[match.end() :].lstrip(" .:–—-")
        tail = re.sub(r"^\([a-z]\)\s*", "", tail, flags=re.I)
        return bool(tail) and not re.match(
            r"(?:shows?|compares?|demonstrates?|illustrates?|presents?|summarizes?|reports?|depicts?|contains?|provides?|is|are|was|were)\b",
            tail,
            re.I,
        )

    if not caption_tail(first):
        return []
    matches = {first.start(): first}
    for match in CAPTION_ANY.finditer(text):
        if caption_tail(match):
            matches.setdefault(match.start(), match)
    return [matches[k] for k in sorted(matches)]


def digest(value):
    return hashlib.sha256(db.dumps(value).encode()).hexdigest()


def render_crop(page, rect, path):
    # Read labels at a useful resolution even for a small diagram in a column.
    scale = min(5.0, max(2.0, 1600 / max(rect.width, rect.height)))
    save_pixmap(
        page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False), path
    )


def save_pixmap(pixmap, path):
    temporary = path.with_name(path.stem + ".partial" + path.suffix)
    try:
        pixmap.save(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def candidate_box(page, caption):
    """Use nearby vector/raster bounds; leave uncertain layouts as full pages."""
    shapes = list(page.cluster_drawings())
    rasters = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    shapes += rasters
    nearby = [
        r
        for r in shapes
        if r.width > 12
        and r.height > 12
        and 0 <= caption.y0 - r.y1 < page.rect.height * 0.5
        and min(r.x1, caption.x1) - max(r.x0, caption.x0) > 15
    ]
    if not nearby:
        return page.rect, "page_fallback"
    bottom = max(r.y1 for r in nearby)
    chosen = [r for r in nearby if bottom - r.y1 < 100]
    prose = [fitz.Rect(b[:4]) for b in page.get_text("blocks") if len(str(b[4])) > 150]
    content_right = max((r.x1 for r in prose), default=page.rect.x1) + 15
    content_left = min((r.x0 for r in prose), default=page.rect.x0) - 15
    # Captions can be shorter than a multi-panel figure. Include neighbouring
    # panels on the same row, while excluding off-margin PDF drawing remnants.
    for _ in range(3):
        for r in shapes:
            if (
                r in chosen
                or r.width < 12
                or r.height < 12
                or r.x0 < content_left
                or r.x1 > content_right
            ):
                continue
            if r.y1 <= caption.y0 + 5 and any(
                min(r.y1, c.y1) - max(r.y0, c.y0) > min(r.height, c.height) * 0.5
                and max(r.x0 - c.x1, c.x0 - r.x1) < 120
                for c in chosen
            ):
                chosen.append(r)
    rect = fitz.Rect(caption)
    for r in chosen:
        rect |= r
    # Include labels protruding from boxes/plots, but never neighbouring prose.
    for block in page.get_text("blocks"):
        b = fitz.Rect(block[:4])
        if b.y0 >= rect.y0 - 15 and b.y1 <= caption.y0 and b.intersects(rect):
            rect |= b
    rect = (rect + (-10, -12, 10, 8)) & page.rect
    # A PDF can clip a raster with a much larger underlying image transform.
    # Its raw bbox may end below the caption or beyond the prose margin. Never
    # silently drop that adjacent panel (e.g. DDPM Figure 1's CIFAR grid).
    for raster in rasters:
        if not rect.contains(raster & page.rect) and any(
            min(raster.y1, c.y1) - max(raster.y0, c.y0)
            > min(raster.height, c.height) * 0.5
            and max(raster.x0 - c.x1, c.x0 - raster.x1) < 120
            for c in chosen
        ):
            return page.rect, "page_fallback"
    if (
        rect.width < 35
        or rect.height < 35
        or rect.get_area() > page.rect.get_area() * 0.85
    ):
        return page.rect, "page_fallback"
    return rect, "candidate"


def extract(paper_id):
    paper = db.one("SELECT * FROM papers WHERE id=?", (paper_id,))
    path = config.safe_path(paper["data"]["pdf_path"])
    pdf_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    signature = digest([pdf_hash, EXTRACT_VERSION])
    saved = paper["data"].get("figure_extraction", {})
    if saved.get("signature") == signature:
        assets = [
            db.one("SELECT * FROM visual_assets WHERE id=?", (i,)) for i in saved["ids"]
        ]
        if all(
            a and config.safe_path(a["data"]["image_path"]).is_file() for a in assets
        ):
            return assets
    folder = config.DATA / "papers" / paper_id / "figures"
    folder.mkdir(parents=True, exist_ok=True)
    sources = db.all("SELECT * FROM sources WHERE paper_id=?", (paper_id,))
    figures = []
    with fitz.open(path) as document:
        candidates = []
        for index, page in enumerate(document):
            for block in page.get_text("blocks"):
                if not isinstance(block[4], str):
                    continue
                text = re.sub(r"\s+", " ", block[4]).strip()
                matches = caption_matches(text)
                if not matches:
                    continue
                # Prose such as "Table 1 shows ..." is not another caption.
                # Adjacent captions may be merged into one PDF text block
                # (DDPM Figures 3/4). Keep both identities and the complete page
                # until the selected figure's crop can be reviewed separately.
                for n, match in enumerate(matches):
                    label = (
                        ("Table" if match[1].lower() == "table" else "Figure")
                        + " "
                        + match[2]
                    )
                    end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
                    caption = text[match.start() : end].strip()
                    rect, method = (
                        (page.rect, "page_fallback")
                        if len(matches) > 1
                        else candidate_box(page, fitz.Rect(block[:4]))
                    )
                    candidates.append((index, label, caption, rect, method))
        if not candidates:
            candidates = [
                (i, f"Page {i + 1}", "Original paper page", p.rect, "page_fallback")
                for i, p in enumerate(document)
            ]
        for index, label, caption, rect, method in candidates:
            page = document[index]
            ident = "original-" + digest([signature, index, label, list(rect)])[:24]
            image = folder / (ident + ".png")
            if not image.exists():
                render_crop(page, rect, image)
            full = config.DATA / "papers" / paper_id / f"page-{index + 1}.jpg"
            if not full.exists():
                save_pixmap(
                    page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False), full
                )
            refs = [
                s["id"]
                for s in sources
                if s["kind"] in {"figure", "table"}
                and re.match(re.escape(label) + r"[.:\s]", s["data"]["text"], re.I)
            ]
            page_refs = [
                s["id"]
                for s in sources
                if s["kind"] == "page" and s["data"]["page"] == index + 1
            ]
            if not refs:
                sid = f"{paper_id}:F{ident.removeprefix('original-')}"
                db.execute(
                    "INSERT OR IGNORE INTO sources VALUES (?,?,?,?)",
                    (
                        sid,
                        paper_id,
                        "figure",
                        db.dumps(
                            {
                                "label": label,
                                "text": caption,
                                "page": index + 1,
                                "image_path": str(image.relative_to(config.DATA)),
                                "pdf_path": paper["data"]["pdf_path"],
                            }
                        ),
                    ),
                )
                refs = [sid]
            data = {
                "label": label,
                "caption_en": caption,
                "page": index + 1,
                "source_ids": refs,
                "page_source_ids": page_refs,
                "pdf_path": paper["data"]["pdf_path"],
                "pdf_sha256": pdf_hash,
                "image_path": str(image.relative_to(config.DATA)),
                "full_page_path": str(full.relative_to(config.DATA)),
                "crop_box": list(rect),
                "page_box": list(page.rect),
                "extraction": method,
                "extract_version": EXTRACT_VERSION,
                "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "version": 1,
            }
            db.execute(
                "INSERT OR IGNORE INTO visual_assets VALUES (?,?,?,?,?,?)",
                (ident, paper_id, None, "original", db.dumps(data), time.time()),
            )
            figures.append(db.one("SELECT * FROM visual_assets WHERE id=?", (ident,)))
    paper["data"]["figure_extraction"] = {
        "signature": signature,
        "ids": [f["id"] for f in figures],
    }
    db.execute(
        "UPDATE papers SET data=? WHERE id=?", (db.dumps(paper["data"]), paper_id)
    )
    return figures
