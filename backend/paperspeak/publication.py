"""Verified publication identity, separate from later conference awards."""

from __future__ import annotations

import hashlib
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import awards, config, db

VERSION = "paper-venue-edition-awards-2"
LOOKUP_VERSION = "official-records-4-refresh"
DESCRIPTION_VERSION = "paper-awards-no-urls-2"
EDITIONS = {"overview": "概要解説", "deep_dive": "詳細解説"}
ALIASES = {
    "ICML": r"\bICML\b|International Conference on Machine Learning",
    "ICLR": r"\bICLR\b|International Conference on Learning Representations",
    "NeurIPS": r"\b(?:NeurIPS|NIPS)\b|Neural Information Processing Systems",
    "AAAI": r"\bAAAI\b|AAAI Conference on Artificial Intelligence",
    "RSS": r"\bRSS\b|Robotics\s*:?\s*Science and Systems",
    "ICRA": r"\bICRA\b|International Conference on Robotics and Automation",
    "CoRL": r"\bCoRL\b|Conference on Robot Learning",
    "IROS": r"\bIROS\b|International Conference on Intelligent Robots and Systems",
    "CVPR": r"\bCVPR\b|Conference on Computer Vision and Pattern Recognition",
    "ICCV": r"\bICCV\b|International Conference on Computer Vision",
    "ECCV": r"\bECCV\b|European Conference on Computer Vision",
    "ACL": r"\bACL\b|Annual Meeting of the Association for Computational Linguistics",
    "EMNLP": r"\bEMNLP\b|Empirical Methods in Natural Language Processing",
    "NAACL": r"\bNAACL\b",
}


def venue(text):
    return next(
        (v for v, pattern in ALIASES.items() if re.search(pattern, text, re.I)), None
    )


def confirmed(paper, name, year, source_url, source_text, **provenance):
    return {
        "status": "verified",
        "paper_title": paper["title"],
        "venue": name,
        "year": int(year),
        "label": f"{name} {year}",
        "source_url": source_url,
        "source_text": source_text[:1200],
        "checked_at": time.time(),
        **provenance,
    }


def parse_page(saved, paper):
    soup = BeautifulSoup(saved["html"], "html.parser")
    meta = {
        m.get("name", "").lower(): m.get("content", "") for m in soup.find_all("meta")
    }
    url = urlparse(saved["url"])
    virtual = re.fullmatch(
        r"/virtual/((?:19|20)\d{2})/(?:oral|poster|spotlight)/\d+/?", url.path
    )
    official_venue = {
        "icml.cc": "ICML",
        "iclr.cc": "ICLR",
        "neurips.cc": "NeurIPS",
    }.get((url.hostname or "").removeprefix("www."))
    if virtual and official_venue and url.scheme == "https":
        edition = virtual[1]
        matching_title = any(
            awards.normalized(h.get_text(" ", strip=True))
            == awards.normalized(paper["title"])
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
        )
        # These virtual pages can contain both a paper title and an event title.
        # Require the actual event heading to agree with the official program URL.
        event = next(
            (
                t.get_text(" ", strip=True)
                for t in soup.find_all("title")
                if re.fullmatch(
                    re.escape(official_venue) + r"\s+" + edition,
                    t.get_text(" ", strip=True),
                    re.I,
                )
            ),
            None,
        )
        if matching_title and event:
            return confirmed(
                paper,
                official_venue,
                edition,
                saved["url"],
                event,
                source_sha256=saved["sha256"],
                source_kind="official-conference-program",
            )
    program = re.fullmatch(r"/((?:19|20)\d{2})/program/papers/\d+/?", url.path)
    if (
        url.hostname in {"roboticsconference.org", "www.roboticsconference.org"}
        and program
    ):
        edition = program[1]
        description = meta.get("description", "")
        matching_title = any(
            awards.normalized(h.get_text(" ", strip=True))
            == awards.normalized(paper["title"])
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
        )
        if (
            matching_title
            and soup.title
            and venue(soup.title.get_text()) == "RSS"
            and re.search(r"\bRSS\s+" + edition + r"\b", description)
        ):
            return confirmed(
                paper,
                "RSS",
                edition,
                saved["url"],
                description,
                source_sha256=saved["sha256"],
                source_kind="official-conference-program",
            )
    title = meta.get("citation_title") or (
        soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    )
    if awards.normalized(title) != awards.normalized(paper["title"]):
        return None
    book = meta.get("citation_conference_title", "")
    year = meta.get("citation_publication_date", "") or meta.get("citation_year", "")
    text = soup.get_text(" ", strip=True)
    b = re.search(r"\bbooktitle\s*=\s*\{([^{}]+)\}", text, re.I)
    y = re.search(r"\byear\s*=\s*\{((?:19|20)\d{2})\}", text, re.I)
    if not book and b:
        book = b[1]
    # Conference edition and proceedings year take priority over a review's
    # upload date, which can be in the preceding calendar year.
    book_year = re.search(r"(?:19|20)\d{2}", book)
    year = book_year[0] if book_year else y[1] if y else year
    name, date = venue(book), re.search(r"(?:19|20)\d{2}", year)
    if name and date:
        return confirmed(
            paper,
            name,
            date[0],
            saved["url"],
            book,
            source_sha256=saved["sha256"],
            source_kind="official-proceedings",
        )
    return None


def resolve(paper):
    existing = paper["data"].get("publication")
    if (
        existing
        and existing.get("status") == "verified"
        and existing.get("paper_title") == paper["title"]
    ):
        return existing
    # A Test of Time year is deliberately never used as publication evidence.
    for prize in awards.verified(paper["data"] | {"title": paper["title"]}):
        url = prize.get("paper_url", "")
        if not url or not awards.trusted(url):
            continue
        try:
            result = parse_page(awards.fetch(url), paper)
            if result:
                return result
        except Exception:
            # Missing bibliographic data must not stop content creation.
            continue
    journal = paper["data"].get("journal_ref") or ""
    name, date = venue(journal), re.search(r"(?:19|20)\d{2}", journal)
    if name and date:
        return confirmed(
            paper,
            name,
            date[0],
            paper["data"].get("url", ""),
            journal,
            source_kind="arxiv-journal-reference",
        )
    path = paper["data"].get("pdf_path")
    if path and config.safe_path(path).is_file():
        import pymupdf

        try:
            with pymupdf.open(config.safe_path(path)) as pdf:
                text = pdf[0].get_text() if len(pdf) else ""
        except (OSError, ValueError, RuntimeError):
            text = ""
        # Do not search the bibliography: its proceedings describe other papers.
        if awards.normalized(paper["title"]) in awards.normalized(text):
            patterns = [
                r"^\s*Published as a conference paper at ([^\n]{1,100})",
                r"^\s*(Proceedings of [\s\S]{1,180}?\b(?:19|20)\d{2})",
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.I | re.M):
                    name = venue(match[1])
                    date = re.search(r"(?:19|20)\d{2}", match[1])
                    if name and date:
                        return confirmed(
                            paper,
                            name,
                            date[0],
                            paper["data"].get("url", ""),
                            match[1],
                            source_kind="paper-publication-header",
                        )
    return {
        "status": "unconfirmed",
        "paper_title": paper["title"],
        "label": "学会未確認",
        "checked_at": time.time(),
        "reason": "No matching official publication record or explicit publication header; award dates are not publication dates.",
    }


def ensure(project):
    paper = db.one("SELECT * FROM papers WHERE id=?", (project["paper_id"],))
    context = project["data"].setdefault("award_context", {})
    prizes = awards.verified(
        {
            "title": paper["title"],
            "awards": [*context.get("awards", []), *paper["data"].get("awards", [])],
        }
    )
    context["awards"] = list(
        {(a["venue"], a["year"], a["name"]): a for a in prizes}.values()
    )
    saved = paper["data"].get("publication", {})
    if saved.get("status") == "verified" and saved.get("paper_title") == paper["title"]:
        project["data"]["publication"] = saved
        return
    evidence = [
        paper["title"],
        paper["data"].get("journal_ref"),
        paper["data"].get("pdf_path"),
        [a.get("paper_url") for a in prizes],
    ]
    path = paper["data"].get("pdf_path")
    if path:
        try:
            stat = config.safe_path(path).stat()
            evidence.append([stat.st_size, stat.st_mtime_ns])
        except (OSError, ValueError):
            pass
    evidence_digest = hashlib.sha256(db.dumps(evidence).encode()).hexdigest()
    existing = project["data"].get("publication", {})
    if existing and (
        existing.get("status") == "verified"
        or (
            existing.get("lookup_version") == LOOKUP_VERSION
            and existing.get("lookup_evidence") == evidence_digest
        )
    ):
        return
    result = resolve(paper)
    if result["status"] == "unconfirmed":
        result["lookup_version"] = LOOKUP_VERSION
        result["lookup_evidence"] = evidence_digest
    project["data"]["publication"] = result
    if result["status"] == "verified":
        paper["data"]["publication"] = result
        db.execute(
            "UPDATE papers SET data=? WHERE id=?",
            (db.dumps(paper["data"]), paper["id"]),
        )


def award_identity(project):
    context = project["data"].get("award_context", {})
    verified = awards.verified(context | {"title": project["data"]["paper_title"]})
    result = []
    for prize in verified:
        name = re.sub(
            r"^" + re.escape(prize["venue"]) + r"\s+" + str(prize["year"]) + r"\s+",
            "",
            prize["name"],
            flags=re.I,
        )
        # Official award pages often group their winners under plural headings.
        name = {
            "Outstanding Papers": "Outstanding Paper Award",
            "Best Papers": "Best Paper Award",
        }.get(name, name)
        japanese = {
            "Outstanding Paper Award": "優秀論文賞",
            "Best Paper Award": "最優秀論文賞",
            "Test of Time Award": "Test of Time賞",
            "Outstanding Systems Paper in Memory of Seth Teller Award": "優秀システム論文賞",
        }.get(name, name)
        item = prize | {
            "name": name,
            "source_name": prize["name"],
            "name_ja": japanese,
            "label": f"{prize['venue']} {prize['year']} {japanese}",
        }
        if not any(
            (a["venue"], a["year"], a["name"]) == (item["venue"], item["year"], name)
            for a in result
        ):
            result.append(item)
    return result


def identity(project, mode):
    return {
        "version": VERSION,
        "paper_title": project["data"]["paper_title"],
        "conference": project["data"].get("publication", {}).get("label", "学会未確認"),
        "edition": EDITIONS[mode],
        "publication": project["data"].get("publication", {}),
        "awards": award_identity(project),
    }


def title(name, conference, edition, hook=""):
    # YouTube accepts 100 characters; keep edition and venue intact even for an
    # unusually long name, while recording the full name separately.
    prefix, suffix = f"【{edition}】", f"｜{conference}"
    room = 95 - len(prefix) - len(suffix)
    shown = name if len(name) <= room else name[: room - 1].rstrip() + "…"
    base = prefix + shown + suffix
    hook_room = 95 - len(base) - 1
    return base + ("｜" + hook if hook and len(hook) <= hook_room else "")


def package(project):
    for mode, track in project["data"]["modes"].items():
        p = track.get("packaging")
        if not p:
            continue
        meta = identity(project, mode)
        p.setdefault("hook_title_ja", p["title"])
        p.setdefault("hook_title_en", p.get("title_en", ""))
        p.update(
            identity=meta,
            paper_title=meta["paper_title"],
            conference=meta["conference"],
            edition=meta["edition"],
            awards=meta["awards"],
        )
        p["title"] = title(
            meta["paper_title"], meta["conference"], meta["edition"], p["hook_title_ja"]
        )
        p["title_en"] = (
            f"{'Overview' if mode == 'overview' else 'Detailed explanation'}: {meta['paper_title']} ({meta['conference']})"
        )
        candidates = []
        for original in p.get("candidates", []):
            # Planning keeps its original hook titles. Do not mutate an outline
            # through the shared candidates list or repeatedly wrap its titles.
            candidate = dict(original)
            candidate.setdefault("hook_title_ja", candidate["title_ja"])
            candidate["title_ja"] = title(
                meta["paper_title"],
                meta["conference"],
                meta["edition"],
                candidate["hook_title_ja"],
            )
            candidate.update(
                paper_title=meta["paper_title"],
                conference=meta["conference"],
                edition=meta["edition"],
            )
            candidates.append(candidate)
        p["candidates"] = candidates


def without_urls(text):
    """Keep readable reference labels, but never export clickable URL targets."""
    text = re.sub(r"\[([^\]]+)\]\((?:https?://|www\.)[^)]+\)", r"\1", text)
    text = re.sub(r"<(?:https?://|www\.)[^>]+>", "", text)
    text = re.sub(r"(?:https?://|ftp://|www\.)[^\s<>]+", "", text, flags=re.I)
    return re.sub(
        r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.splitlines())
    ).strip()


def prepare(project):
    """Apply the same verified identity before every film/thumbnail snapshot."""
    ensure(project)
    package(project)
    for mode, track in project["data"]["modes"].items():
        if track.get("packaging"):
            track["packaging"]["description"] = description(project, mode)
            track["description_version"] = DESCRIPTION_VERSION


def description_issues(project, mode, text):
    meta = identity(project, mode)
    issues = []
    for field, value in [
        ("論文名：", meta["paper_title"]),
        ("発表学会：", meta["conference"]),
    ]:
        if field + value not in text.splitlines():
            issues.append("Missing or incorrect " + field)
    expected = [
        f"受賞：{a['venue']} {a['year']} — {a['name']}（{a['name_ja']}）"
        for a in meta["awards"]
    ]
    if [line for line in text.splitlines() if line.startswith("受賞：")] != expected:
        issues.append("Missing, incorrect or unverified awards")
    if without_urls(text) != text:
        issues.append("URL or unnormalized description")
    return issues


def description(project, mode, *, references=None):
    """YouTube copy without URLs; the original URLs remain in source records."""
    if references is None:
        ids = [
            project["paper_id"],
            *[
                r["paper_id"]
                for r in project["data"].get("references", [])
                if r.get("paper_id")
            ],
        ]
        references = [
            db.one("SELECT * FROM papers WHERE id=?", (pid,))
            for pid in dict.fromkeys(ids)
        ]
    meta = identity(project, mode)
    lines = [
        project["data"]["modes"][mode]["packaging"]["title"],
        "",
        "論文名：" + meta["paper_title"],
        "発表学会：" + meta["conference"],
    ]
    for prize in meta["awards"]:
        lines.append(
            f"受賞：{prize['venue']} {prize['year']} — {prize['name']}（{prize['name_ja']}）"
        )
    topic = (
        "背景・課題・発想を数式なしで学びます。"
        if mode == "overview"
        else "原理・数式・具体例・実験を詳しく学びます。"
    )
    lines += [
        "",
        "図解とMaya・Aidenの自然な英語の会話で、AI論文の"
        + topic
        + "英語・日本語の字幕付きです。英語表現を聞き取り、動画を止めて声に出したり、自分の言葉で説明したりしてみてください。",
        "",
        "参考文献",
    ]
    for paper in references:
        if not paper:
            continue
        ident = paper.get("source_id", "")
        suffix = (
            f"（arXiv: {ident}{paper.get('version', '')}）"
            if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+/\d{7})", ident)
            else ""
        )
        lines.append(paper["title"] + suffix)
    lines += ["", "#AI論文 #英語学習 #機械学習"]
    return without_urls("\n".join(lines))


def refresh_export(project, mode, export):
    """Repair a completed preview/film's copy even while its parent is building."""
    from . import story_video, video

    record = export["data"]
    old = record.get("description", "")
    chapters = re.findall(r"^\d{1,3}:\d{2}(?::\d{2})?\s+.+$", old, re.M)
    new = without_urls(description(project, mode) + "\n\n" + "\n".join(chapters))
    issues = description_issues(project, mode, new)
    if issues:
        raise ValueError("Video description check failed: " + "; ".join(issues))
    new_title = story_video.portable_title(
        project["data"]["modes"][mode]["packaging"]["title"]
    )
    if record.get("manifest", {}).get("preview"):
        new_title += " — Preview"
    changed = old != new or record.get("title") != new_title
    if changed:
        record.setdefault("packaging_history", []).append(
            {
                "title": record.get("title"),
                "description": old,
                "description_version": record.get("description_version"),
                "replaced_at": time.time(),
            }
        )
    if record.get("description_file"):
        path = config.safe_path(record["description_file"])
        if path.is_file() and path.read_text() != new:
            history = path.with_name(
                "description-" + video.file_digest(path)[:16] + ".txt"
            )
            if not history.is_file():
                history.write_bytes(path.read_bytes())
        if not path.is_file() or path.read_text() != new:
            pending = path.with_suffix(".pending.txt")
            pending.write_text(new, encoding="utf-8")
            pending.replace(path)
    record.update(
        description=new,
        title=new_title,
        description_version=DESCRIPTION_VERSION,
        identity=identity(project, mode),
    )
    # Thumbnail selection can arrive while this check is preparing the copy.
    # Merge only copy fields into the latest record; preserve the current poster.
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = db.row(
            conn.execute(
                "SELECT * FROM video_exports WHERE id=?", (export["id"],)
            ).fetchone()
        )
        latest = current["data"]
        for field in (
            "description",
            "description_file",
            "title",
            "description_version",
            "identity",
            "packaging_history",
        ):
            if field in record:
                latest[field] = record[field]
        conn.execute(
            "UPDATE video_exports SET data=?,updated=? WHERE id=?",
            (db.dumps(latest), time.time(), export["id"]),
        )
    return changed


def refresh_completed(project):
    """Repair downloadable metadata, leaving immutable film manifests untouched."""
    if project["state"] != "ready":
        raise ValueError("Only completed projects can be refreshed")
    ensure(project)
    package(project)
    changed = []
    for mode, track in project["data"]["modes"].items():
        if not track.get("packaging"):
            continue
        track["packaging"]["description"] = description(project, mode)
        track["description_version"] = DESCRIPTION_VERSION
        lesson = db.one("SELECT * FROM lessons WHERE id=?", (track["lesson_id"],))
        lesson["data"]["packaging"] = track["packaging"]
        db.execute(
            "UPDATE lessons SET data=?,updated=? WHERE id=?",
            (db.dumps(lesson["data"]), time.time(), lesson["id"]),
        )
        for export in db.all(
            "SELECT * FROM video_exports WHERE lesson_id=? AND state='ready'",
            (lesson["id"],),
        ):
            if refresh_export(project, mode, export):
                changed.append(export["id"])
    db.execute(
        "UPDATE video_projects SET data=?,updated=? WHERE id=?",
        (db.dumps(project["data"]), time.time(), project["id"]),
    )
    db.event("video_project", {"id": project["id"]})
    return changed
