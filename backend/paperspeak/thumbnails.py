"""Local AI illustrations, stable pixel portraits and deterministic Japanese typography."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import time

from . import config, db, publication, video, video_overlay
from .runtime import GPUUnavailable, PracticePreempted

VERSION = "surprised-pixel-3-awards"
CHECK_VERSION = "thumbnail-dom-check-1"


def paper_claim_context(project):
    """Give thumbnail writers the paper's claims, separately from catchy packaging."""
    prefix = project["paper_id"] + ":"
    return [
        {"claim": row.get("claim", ""), "source_ids": row["source_ids"]}
        for row in project["data"].get("evidence", [])
        if any(s.startswith(prefix) for s in row.get("source_ids", []))
    ][:40]


def get(project_id, mode):
    row = db.one(
        "SELECT * FROM thumbnail_sets WHERE project_id=? AND mode=? ORDER BY created DESC LIMIT 1",
        (project_id, mode),
    )
    if row:
        row["job"] = db.one(
            "SELECT id,state,stage,progress,error FROM jobs WHERE kind='thumbnail' AND target=? ORDER BY created DESC LIMIT 1",
            (row["id"],),
        )
    return row


def enqueue(project, mode, *, regenerate=False):
    if mode not in {"overview", "deep_dive", "deep_dive_ja"}:
        raise ValueError("Unknown film mode")
    track = project["data"]["modes"][mode]
    if not track.get("packaging") or not track.get("scenes"):
        raise ValueError("The film needs its script and title before making thumbnails")
    if any(not s.get("utterances") for s in track["scenes"]):
        raise ValueError("Finish the film script before making thumbnails")
    publication.prepare(project)
    manifest = {
        "version": VERSION,
        "image_model": config.manifest().get("models", {}).get("image"),
        "characters": video_overlay.character_manifest(),
        "renderer": video.file_digest(config.ROOT / "scripts/render_thumbnail.mjs"),
        "project_id": project["id"],
        "mode": mode,
        "identity": publication.identity(project, mode),
        "packaging": track["packaging"],
        "script_hash": video.digest(
            [u["text"] for s in track["scenes"] for u in s["utterances"]]
        ),
    }
    if regenerate:
        manifest["revision_nonce"] = db.uid()
    fingerprint = video.digest(manifest)
    with db.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        old = c.execute(
            "SELECT id FROM thumbnail_sets WHERE input_digest=?", (fingerprint,)
        ).fetchone()
        if old:
            ident = old["id"]
        else:
            ident, now = db.uid(), time.time()
            c.execute(
                "INSERT INTO thumbnail_sets VALUES (?,?,?,?,?,?,?,?)",
                (
                    ident,
                    project["id"],
                    mode,
                    fingerprint,
                    "building",
                    db.dumps(
                        {
                            "manifest": manifest,
                            "phase": "plan",
                            "repairs": {},
                            "warnings": [],
                            "candidates": [],
                        }
                    ),
                    now,
                    now,
                ),
            )
        existing = c.execute(
            "SELECT state FROM thumbnail_sets WHERE id=?", (ident,)
        ).fetchone()
        if existing["state"] != "ready":
            db.queue_job(c, "thumbnail", ident, priority=9)
    db.event("thumbnail", {"project_id": project["id"], "mode": mode})
    return ident


def save(row, *, preserve_selection=True):
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT data FROM thumbnail_sets WHERE id=?", (row["id"],)
        ).fetchone()
        if current and preserve_selection:
            latest = json.loads(current["data"])
            if latest.get("selection_source") == "manual":
                row["data"].update(
                    selected_id=latest["selected_id"], selection_source="manual"
                )
        conn.execute(
            "UPDATE thumbnail_sets SET state=?,data=?,updated=? WHERE id=?",
            (row["state"], db.dumps(row["data"]), time.time(), row["id"]),
        )
    db.event("thumbnail", {"project_id": row["project_id"], "mode": row["mode"]})


def select(ident, candidate_id, *, automatic=False):
    row = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (ident,))
    if not row:
        raise ValueError("Thumbnail set not found")
    if automatic and row["data"].get("selection_source") == "manual":
        candidate_id = row["data"]["selected_id"]
    candidate = next(
        (c for c in row["data"]["candidates"] if c["id"] == candidate_id), None
    )
    if (
        not candidate
        or not candidate.get("png")
        or not config.safe_path(candidate["png"]).is_file()
    ):
        raise ValueError("That thumbnail is not ready")
    row["data"]["selected_id"] = candidate_id
    row["data"]["selection_source"] = "automatic" if automatic else "manual"
    save(row, preserve_selection=False)
    project = db.one("SELECT * FROM video_projects WHERE id=?", (row["project_id"],))
    lid = project["data"]["modes"][row["mode"]]["lesson_id"]
    with db.connection() as conn:
        for export in conn.execute(
            "SELECT id,data FROM video_exports WHERE lesson_id=?", (lid,)
        ).fetchall():
            record = json.loads(export["data"])
            prior = record.get("thumbnail")
            if prior and prior != candidate["png"]:
                history = record.setdefault("thumbnail_history", [])
                if prior not in history:
                    history.append(prior)
            record.update(
                thumbnail=candidate["png"],
                thumbnail_jpg=candidate["jpg"],
                thumbnail_set_id=ident,
                thumbnail_candidate_id=candidate_id,
            )
            # Earlier films kept their poster in a private work directory.
            # Preserve a public, backed-up copy when switching to a new candidate.
            for i, name in enumerate(record.get("thumbnail_history", [])):
                old_path = config.safe_path(name)
                if (
                    name.startswith("jobs/")
                    and old_path.name == "thumbnail.png"
                    and old_path.is_file()
                ):
                    history = (
                        config.DATA
                        / "thumbnails"
                        / "history"
                        / (video.file_digest(old_path) + ".png")
                    )
                    history.parent.mkdir(parents=True, exist_ok=True)
                    if not history.is_file():
                        shutil.copy2(old_path, history)
                    record["thumbnail_history"][i] = str(
                        history.relative_to(config.DATA)
                    )
            conn.execute(
                "UPDATE video_exports SET data=?,updated=? WHERE id=?",
                (db.dumps(record), time.time(), export["id"]),
            )
    db.event("video_project", {"id": project["id"]})
    return row


def selected(project_id, mode):
    for row in db.all(
        "SELECT data FROM thumbnail_sets WHERE project_id=? AND mode=? ORDER BY created DESC",
        (project_id, mode),
    ):
        candidate = next(
            (
                c
                for c in row["data"]["candidates"]
                if c["id"] == row["data"].get("selected_id")
            ),
            None,
        )
        if candidate:
            return candidate


def render(spec, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    source = output.with_suffix(".json")
    source.write_text(db.dumps(spec), encoding="utf-8")
    try:
        subprocess.run(
            [
                str(config.ROOT / ".tools/node/bin/node"),
                str(config.ROOT / "scripts/render_thumbnail.mjs"),
                str(source),
                str(output),
            ],
            cwd=config.ROOT,
            check=True,
            timeout=90,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(
            "Local thumbnail renderer failed: "
            + (exc.stderr or b"").decode(errors="replace")[-1500:]
        ) from exc


def image_issues(project, mode, name):
    """Validate the actual rendered fields and the hashes of their PNG and input."""
    issues = []
    try:
        png = config.safe_path(str(name))
        source = png.with_suffix(".json")
        receipt = json.loads(png.with_suffix(".verification.json").read_text())
        meta = publication.identity(project, mode)
        award = meta["awards"][0] if meta["awards"] else None
        expected = {key: meta[key] for key in ("paper_title", "conference", "edition")}
        expected.update(
            award_name=award["name"] if award else None,
            award_label=award["label"] if award else None,
        )
        if receipt.get("rendered") != expected:
            issues.append("Rendered paper, conference, edition or award does not match")
        if receipt.get("version") != CHECK_VERSION or not receipt.get(
            "no_text_overflow"
        ):
            issues.append("Thumbnail layout has not passed its current check")
        if receipt.get("renderer_sha256") != video.file_digest(
            config.ROOT / "scripts/render_thumbnail.mjs"
        ):
            issues.append("Thumbnail uses an older renderer")
        if receipt.get("png_sha256") != video.file_digest(png) or receipt.get(
            "input_sha256"
        ) != video.file_digest(source):
            issues.append("Thumbnail or render input changed after verification")
        with png.open("rb") as handle:
            header = handle.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or struct.unpack(
            ">II", header[16:24]
        ) != (1280, 720):
            issues.append("Thumbnail is not a 1280×720 PNG")
        if png.stat().st_size >= 2_000_000:
            issues.append("Thumbnail exceeds 2 MB")
    except (OSError, ValueError, TypeError, KeyError, struct.error):
        issues.append("Thumbnail or verification receipt is unavailable")
    return issues


def candidate_issues(project, mode, candidate):
    if not candidate or not candidate.get("png"):
        return ["Thumbnail candidate is unavailable"]
    issues = image_issues(project, mode, candidate["png"])
    try:
        jpg = config.safe_path(candidate["jpg"])
        if not jpg.is_file() or not 0 < jpg.stat().st_size < 2_000_000:
            issues.append("Thumbnail JPEG is unavailable or too large")
        else:
            receipt = json.loads(
                config.safe_path(candidate["png"])
                .with_suffix(".verification.json")
                .read_text()
            )
            if receipt.get("jpg_sha256") != video.file_digest(jpg):
                issues.append("Thumbnail JPEG changed after verification")
    except (OSError, KeyError, ValueError):
        issues.append("Thumbnail JPEG is unavailable")
    return issues


def jpeg(png):
    subprocess.run(
        [
            __import__("imageio_ffmpeg").get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(png),
            "-q:v",
            "2",
            str(png.with_suffix(".jpg")),
        ],
        check=True,
        timeout=30,
    )
    path = png.with_suffix(".verification.json")
    receipt = json.loads(path.read_text())
    receipt["jpg_sha256"] = video.file_digest(png.with_suffix(".jpg"))
    pending = path.with_suffix(".pending.json")
    pending.write_text(db.dumps(receipt), encoding="utf-8")
    pending.replace(path)


def fallback(project, mode):
    """A checked local poster is available before the image model finishes."""
    identity = publication.identity(project, mode)
    key = video.digest(
        [
            "fixed-surprise-1",
            video_overlay.character_manifest(),
            video.file_digest(config.ROOT / "scripts/render_thumbnail.mjs"),
        ]
    )[:16]
    chars = config.DATA / "thumbnails" / "characters" / key
    for role in ("guide", "host"):
        path = chars / (role + ".png")
        if not path.is_file():
            render({"portrait": True, "role": role, "surprise": True}, path)
    root = config.DATA / "thumbnails" / "fallbacks" / video.digest([identity, key])[:24]
    png = root / "thumbnail.png"
    candidate = {
        "png": str(png.relative_to(config.DATA)),
        "jpg": str(png.with_suffix(".jpg").relative_to(config.DATA)),
        "identity": identity,
    }
    if candidate_issues(project, mode, candidate):
        plan = plans(project, mode)[0]
        render(
            {
                "identity": identity,
                "lines": plan["lines"],
                "palette": plan["palette"],
                "maya": str(chars / "guide.png"),
                "aiden": str(chars / "host.png"),
                "topic": "AI × NEW IDEA",
            },
            png,
        )
        jpeg(png)
    issues = candidate_issues(project, mode, candidate)
    if issues:
        raise ValueError("Fallback thumbnail check failed: " + "; ".join(issues))
    candidate["sha256"] = video.file_digest(png)
    return candidate


def ensure_labels(project, mode):
    row = get(project["id"], mode)
    if not row or row["state"] != "ready":
        return None
    if len(row["data"]["candidates"]) != 3:
        raise ValueError("A thumbnail set needs three completed candidates")
    if any(candidate_issues(project, mode, c) for c in row["data"]["candidates"]):
        recompose(project, mode)
    candidate = selected(project["id"], mode)
    issues = candidate_issues(project, mode, candidate)
    if issues:
        raise ValueError(
            "Thumbnail check failed after local correction: " + "; ".join(issues)
        )
    return candidate


def recompose(project, mode):
    """Refresh fixed labels from saved local art, retaining every previous set."""
    publication.ensure(project)
    publication.package(project)
    previous = get(project["id"], mode)
    if not previous or previous["state"] != "ready":
        raise ValueError("A completed thumbnail set is required")
    identity = publication.identity(project, mode)
    renderer = video.file_digest(config.ROOT / "scripts/render_thumbnail.mjs")
    old = previous["data"]
    if (
        old["manifest"].get("version") == VERSION
        and old["manifest"].get("identity") == identity
        and old["manifest"].get("renderer") == renderer
        and all(not candidate_issues(project, mode, c) for c in old["candidates"])
    ):
        return previous["id"]
    sources = []
    for candidate in old["candidates"]:
        source = config.safe_path(candidate["png"]).with_suffix(".json")
        try:
            spec = json.loads(source.read_text())
        except (OSError, ValueError):
            checked = fallback(project, mode)
            spec = json.loads(
                config.safe_path(checked["png"]).with_suffix(".json").read_text()
            )
            spec["lines"] = candidate.get("lines", spec["lines"])
        for key in ("maya", "aiden", "background"):
            if spec.get(key):
                try:
                    asset = config.safe_path(spec[key])
                    present = asset.is_file()
                except ValueError:
                    present = False
                if not present:
                    if key == "background":
                        spec.pop(key)
                        continue
                    checked = fallback(project, mode)
                    replacement = json.loads(
                        config.safe_path(checked["png"])
                        .with_suffix(".json")
                        .read_text()
                    )
                    spec[key] = replacement[key]
                    asset = config.safe_path(spec[key])
                # Preserve provenance of the actual illustrations being reused.
                spec[key] = str(asset)
        spec["identity"] = identity
        sources.append((candidate, spec))
    manifest = old["manifest"] | {
        "version": VERSION,
        "identity": identity,
        "packaging": project["data"]["modes"][mode]["packaging"],
        "renderer": renderer,
        "recomposed_from": previous["id"],
        "saved_art": [
            {
                key: video.file_digest(config.safe_path(spec[key]))
                for key in ("maya", "aiden", "background")
                if spec.get(key)
            }
            for _, spec in sources
        ],
    }
    fingerprint = video.digest(manifest)
    ident = fingerprint[:32]
    root = config.DATA / "thumbnails" / ident
    mapping, candidates = {}, []
    for index, (original, spec) in enumerate(sources):
        candidate = json.loads(db.dumps(original))
        candidate["id"] = video.digest([fingerprint, original["id"]])[:32]
        mapping[original["id"]] = candidate["id"]
        png = root / f"thumbnail-{index}.png"
        jpg = png.with_suffix(".jpg")
        # Stable paths permit a retry to reuse successfully composed candidates.
        if candidate_issues(project, mode, {"png": str(png), "jpg": str(jpg)}):
            render(spec, png)
            jpeg(png)
        candidate.update(
            identity=identity,
            lines=spec["lines"],
            palette=spec.get("palette", "teal"),
            generation=original.get(
                "generation",
                {"fallback": "Saved inputs unavailable; checked local composition"},
            ),
            png=str(png.relative_to(config.DATA)),
            jpg=str(jpg.relative_to(config.DATA)),
            sha256=video.file_digest(png),
            recomposed_from=original["id"],
        )
        candidates.append(candidate)
    data = json.loads(db.dumps(old))
    selected_id = mapping.get(old.get("selected_id"), candidates[0]["id"])
    data.update(
        manifest=manifest,
        candidates=candidates,
        phase="complete",
        selected_id=selected_id,
        recommended_id=mapping.get(old.get("recommended_id"), selected_id),
        label_review={
            "identity": identity,
            "renderer_overflow_check": True,
            "saved_art_reused": True,
        },
    )
    now = time.time()
    with db.connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO thumbnail_sets VALUES (?,?,?,?,?,?,?,?)",
            (
                ident,
                project["id"],
                mode,
                fingerprint,
                "ready",
                db.dumps(data),
                now,
                now,
            ),
        )
    select(ident, selected_id, automatic=old.get("selection_source") != "manual")
    return ident


def _attempt(row, key, action, fallback):
    state = row["data"]["repairs"].setdefault(key, {"attempts": 0})
    if state["attempts"] >= 3:
        row["data"]["warnings"].append(
            {
                "unit": key,
                "reason": state.get("error"),
                "action": "Saved portrait/simple composition fallback",
            }
        )
        return fallback()
    try:
        return action()
    except (GPUUnavailable, PracticePreempted):
        raise
    except Exception as exc:
        state.update(attempts=state["attempts"] + 1, error=str(exc)[:500])
        save(row)
        return None


def plans(project, mode):
    paper = db.one("SELECT * FROM papers WHERE id=?", (project["paper_id"],))
    lora = paper["source_id"] == "2106.09685"
    if lora:
        titles = (
            [
                ["巨大AI、", "これだけで変わる!?"],
                ["LoRAの発想", "小さな追加で大変化!"],
                ["AIの調整", "全部変えなくていい!?"],
            ]
            if mode == "overview"
            else [
                ["LoRA", "完全解説！"],
                ["二つの行列で", "AIが変わる!?"],
                ["LoRAの仕組み", "図と計算で理解！"],
            ]
        )
    else:
        titles = [
            ["AIの新発想", "仕組みを解説！"],
            ["何が変わる!?", "論文の発想を図解！"],
            ["知りたいAIの原理", "実験と限界まで！"],
        ]
    return [
        {
            "id": db.uid(),
            "identity": publication.identity(project, mode),
            "lines": lines,
            "palette": ["teal", "violet", "orange"][i],
            "concept": "A giant AI blueprint and a tiny glowing modular adjustment, with a clear visual comparison, no text or experimental charts"
            if lora
            else "A clear, engaging visual metaphor for " + paper["title"],
        }
        for i, lines in enumerate(titles)
    ]


def step(job, runtime):
    row = db.one("SELECT * FROM thumbnail_sets WHERE id=?", (job["target"],))
    if row["state"] == "ready":
        project = db.one(
            "SELECT * FROM video_projects WHERE id=?", (row["project_id"],)
        )
        publication.prepare(project)
        ensure_labels(project, row["mode"])
        return True
    data = row["data"]
    project = db.one("SELECT * FROM video_projects WHERE id=?", (row["project_id"],))
    mode = row["mode"]
    identity = data["manifest"].get("identity") or publication.identity(project, mode)
    root = config.DATA / "thumbnails" / row["id"]
    root.mkdir(parents=True, exist_ok=True)
    character_key = video.digest(
        [
            VERSION,
            data["manifest"]["characters"],
            data["manifest"]["image_model"],
            data["manifest"]["renderer"],
        ]
    )[:16]
    chars = config.DATA / "thumbnails" / "characters" / character_key
    chars.mkdir(parents=True, exist_ok=True)
    if data["phase"] == "plan":
        candidates = plans(project, mode)
        if (
            db.one("SELECT source_id FROM papers WHERE id=?", (project["paper_id"],))[
                "source_id"
            ]
            != "2106.09685"
        ):

            def draft():
                result = runtime.ask(
                    'Make three catchy Japanese YouTube thumbnail ideas matching this actual film. Titles have exactly two lines, each at most 12 Japanese characters (short paper/model names may be English). No unsupported result promises or made-up statistics. A speed comparison with 2D image generation, another model, or a particular GPU needs direct support in PAPER CLAIMS; an entertaining line spoken in the film is not benchmark evidence. Prefer a truthful visual idea over an unverified performance comparison. Maya and Aiden are composited separately at the left and right; NEVER include or describe them in concept. Describe ONLY the central scientific object/metaphor, without humans, faces, text, numbers or charts. Return {"candidates":[{"lines":["line1","line2"],"concept":"English image description of objects only"}]}.\nFILM: '
                    + db.dumps(project["data"]["modes"][mode]["packaging"])
                    + "\nPAPER CLAIMS: "
                    + db.dumps(paper_claim_context(project)),
                    profile=project["data"]["model"],
                    max_tokens=1600,
                    thinking=False,
                )
                result = result.get("candidates", [])
                if len(result) != 3 or any(
                    len(c.get("lines", [])) != 2
                    or any(
                        not isinstance(s, str) or not s or len(s) > 12
                        for s in c["lines"]
                    )
                    or not c.get("concept")
                    or any(name in c["concept"].lower() for name in ("maya", "aiden"))
                    for c in result
                ):
                    raise ValueError("Need three complete thumbnail ideas")
                return [base | c for base, c in zip(candidates, result)]

            candidates = _attempt(row, "plan", draft, lambda: candidates)
            if candidates is None:
                return False
        data.update(candidates=candidates, phase="characters")
    elif data["phase"] == "characters":
        for role, name in [("guide", "Maya"), ("host", "Aiden")]:
            portrait = chars / (role + ".png")
            if portrait.is_file():
                continue
            reference = chars / (role + "-reference.png")
            render({"portrait": True, "role": role, "reference": True}, reference)
            generated = chars / (role + "-generated.png")

            def generate():
                result = runtime.image(
                    {
                        "prompt": f"Edit only the eyes and mouth of this original 16-bit pixel art engineer {name}: a charming exaggerated surprised expression, wide eyes and an open round mouth. Preserve the exact pixel grid, face position, hair, glasses, clothes, colors, upper-body pose, plain white background and composition. Do not add text. Do not redesign the character.",
                        "references": [str(reference)],
                        "output": str(generated),
                        "width": 512,
                        "height": 512,
                        "seed": int(character_key[:8], 16) + int(role == "host"),
                    }
                )
                render(
                    {"portrait": True, "role": role, "generated": str(generated)},
                    portrait,
                )
                return result

            def fallback():
                render({"portrait": True, "role": role, "surprise": True}, portrait)
                return {"fallback": "Fixed original pixel art with surprised mouth"}

            result = _attempt(row, "portrait:" + role, generate, fallback)
            if result is not None:
                data.setdefault("portraits", {})[role] = result
            save(row)
            return False
        data["character_assets"] = {
            role: {
                "path": str((chars / (role + ".png")).relative_to(config.DATA)),
                "sha256": video.file_digest(chars / (role + ".png")),
            }
            for role in ("guide", "host")
        }
        data["phase"] = "images"
    elif data["phase"] == "images":
        for i, candidate in enumerate(data["candidates"]):
            if candidate.get("png"):
                continue
            bg, png = root / f"idea-{i}.png", root / f"thumbnail-{i}.png"

            def generate():
                result = runtime.image(
                    {
                        "prompt": "Original colorful pixel-art science illustration for a YouTube thumbnail, strongly readable central subject, retro 16-bit visual style, high contrast and luminous amber/cyan highlights. "
                        + candidate["concept"]
                        + ". Keep main content centered. No people, no lettering, no numbers, no logos, no fake measurement charts.",
                        "output": str(bg),
                        "seed": int(row["input_digest"][:8], 16)
                        + i
                        + 31 * candidate.get("seed_offset", 0),
                    }
                )
                return result

            result = _attempt(
                row,
                "image:" + str(i),
                generate,
                lambda: {"fallback": "Simple topic illustration"},
            )
            if result is None:
                return False
            render(
                {
                    "lines": candidate["lines"],
                    "identity": identity,
                    "palette": candidate["palette"],
                    "background": str(bg) if bg.is_file() else None,
                    "maya": str(chars / "guide.png"),
                    "aiden": str(chars / "host.png"),
                    "topic": project["data"]["paper_title"].split(":")[0][:30],
                },
                png,
            )
            jpg = png.with_suffix(".jpg")
            # FFmpeg is already pinned and available; no network or extra image library in API env.
            jpeg(png)
            candidate.update(
                png=str(png.relative_to(config.DATA)),
                jpg=str(jpg.relative_to(config.DATA)),
                sha256=video.file_digest(png),
                generation=result,
            )
            save(row)
            return False
        data["phase"] = "review"
    elif data["phase"] == "review":

        def review():
            result = runtime.ask(
                'Compare three YouTube thumbnails for the supplied film. Check recognizable surprised Maya on left and Aiden on right, catchy readable large Japanese text at phone size, and truthful correspondence to PAPER CLAIMS. A speed comparison with 2D image generation, another model, or a GPU requires direct benchmark support in PAPER CLAIMS; dialogue and catchy packaging are not benchmark evidence. Choose a supported alternative when available. Also check the fixed paper name, publication conference/year, Japanese edition badge, and verified award name/year match IDENTITY exactly and remain readable. No award may be claimed unless present in IDENTITY.awards. The publication year is not the later award year. Pick the clearest, most compelling candidate. issues must describe problems in the SELECTED candidate only; ignore imperfections in alternatives. Return {"selected_index":0,"notes_ja":"reason","issues":[]}. No popularity/view-count guarantees.\nIDENTITY: '
                + db.dumps(identity)
                + "\nFILM: "
                + db.dumps(project["data"]["modes"][mode]["packaging"])
                + "\nPAPER CLAIMS: "
                + db.dumps(paper_claim_context(project)),
                images=[config.safe_path(c["png"]) for c in data["candidates"]],
                profile=project["data"]["model"],
                max_tokens=1400,
                thinking=False,
            )
            if (
                not isinstance(result.get("selected_index"), int)
                or not 0 <= result["selected_index"] < 3
            ):
                raise ValueError("Select one of the three thumbnails")
            return result

        result = _attempt(
            row,
            "review:" + str(data.get("review_round", 0)),
            review,
            lambda: {
                "selected_index": 0,
                "notes_ja": "自動確認の再試行後、基本構図を採用。",
                "fallback": True,
            },
        )
        if result is None:
            return False
        data["review"] = result
        if result.get("issues"):
            data.setdefault("review_history", []).append(result)
            rounds = data.get("review_round", 0)
            if rounds < 3:
                data["review_round"] = rounds + 1
                candidate = data["candidates"][result["selected_index"]]
                candidate["concept"] += (
                    ". Simplify for tiny screens: one central object, no complicated symbols, retain truthful meaning. Address: "
                    + db.dumps(result["issues"])[:600]
                )
                candidate.pop("png", None)
                candidate.pop("jpg", None)
                # Distinct seeds ensure each correction changes the image.
                candidate["seed_offset"] = rounds + 1
                data["phase"] = "images"
                save(row)
                return False
            data["warnings"].append(
                {
                    "unit": "visual_review",
                    "reason": result["issues"],
                    "action": "Use the checked fixed portraits and a simple local composition",
                }
            )
            candidate = data["candidates"][result["selected_index"]]
            png = config.safe_path(candidate["png"])
            render(
                {
                    "lines": candidate["lines"],
                    "identity": identity,
                    "palette": candidate["palette"],
                    "maya": str(chars / "guide.png"),
                    "aiden": str(chars / "host.png"),
                    "topic": project["data"]["paper_title"].split(":")[0][:30],
                },
                png,
            )
            jpeg(png)
            candidate["sha256"] = video.file_digest(png)
            candidate["generation"]["fallback"] = (
                "Simple composition after three visual corrections"
            )
        data["recommended_id"] = data["candidates"][result["selected_index"]]["id"]
        data["phase"] = "complete"
        row["state"] = "ready"
        save(row)
        select(row["id"], data["recommended_id"], automatic=True)
        publication.prepare(project)
        ensure_labels(project, mode)
        return True
    save(row)
    db.patch_job(
        job["id"],
        stage=f"{mode} thumbnail · {data['phase']}",
        progress={"plan": 0.05, "characters": 0.15, "images": 0.4, "review": 0.9}.get(
            data["phase"], 1
        ),
    )
    return False
