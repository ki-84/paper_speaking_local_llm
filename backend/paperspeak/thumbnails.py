"""Local AI illustrations, stable pixel portraits and deterministic Japanese typography."""

from __future__ import annotations

import json
import shutil
import subprocess
import time

from . import config, db, video, video_overlay
from .runtime import GPUUnavailable, PracticePreempted

VERSION = "surprised-pixel-1"


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
    if mode not in {"overview", "deep_dive"}:
        raise ValueError("Unknown film mode")
    track = project["data"]["modes"][mode]
    if not track.get("packaging") or not track.get("scenes"):
        raise ValueError("The film needs its script and title before making thumbnails")
    if any(not s.get("utterances") for s in track["scenes"]):
        raise ValueError("Finish the film script before making thumbnails")
    manifest = {
        "version": VERSION,
        "image_model": config.manifest().get("models", {}).get("image"),
        "characters": video_overlay.character_manifest(),
        "renderer": video.file_digest(config.ROOT / "scripts/render_thumbnail.mjs"),
        "project_id": project["id"],
        "mode": mode,
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
        return True
    data = row["data"]
    project = db.one("SELECT * FROM video_projects WHERE id=?", (row["project_id"],))
    mode = row["mode"]
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
                    'Make three catchy Japanese YouTube thumbnail ideas matching this actual film. Titles have exactly two lines, each at most 12 Japanese characters (short paper/model names may be English). No unsupported result promises or made-up statistics. Maya and Aiden are composited separately at the left and right; NEVER include or describe them in concept. Describe ONLY the central scientific object/metaphor, without humans, faces, text, numbers or charts. Return {"candidates":[{"lines":["line1","line2"],"concept":"English image description of objects only"}]}.\n'
                    + db.dumps(project["data"]["modes"][mode]["packaging"]),
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
                    str(jpg),
                ],
                check=True,
                timeout=30,
            )
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
                'Compare three YouTube thumbnails for the supplied film. Check recognizable surprised Maya on left and Aiden on right, catchy readable large Japanese text at phone size, and truthful correspondence to the film. Pick the clearest, most compelling candidate. issues must describe problems in the SELECTED candidate only; ignore imperfections in alternatives. Return {"selected_index":0,"notes_ja":"reason","issues":[]}. No popularity/view-count guarantees.\n'
                + db.dumps(project["data"]["modes"][mode]["packaging"]),
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
                    "palette": candidate["palette"],
                    "maya": str(chars / "guide.png"),
                    "aiden": str(chars / "host.png"),
                    "topic": project["data"]["paper_title"].split(":")[0][:30],
                },
                png,
            )
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
            candidate["sha256"] = video.file_digest(png)
            candidate["generation"]["fallback"] = (
                "Simple composition after three visual corrections"
            )
        data["recommended_id"] = data["candidates"][result["selected_index"]]["id"]
        data["phase"] = "complete"
        row["state"] = "ready"
        save(row)
        select(row["id"], data["recommended_id"], automatic=True)
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
