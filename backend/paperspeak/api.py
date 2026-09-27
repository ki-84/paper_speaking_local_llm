from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, lessons, papers, practice, recommendation_ja, translation
from .runtime import gpu_info


def credentials():
    path = config.DATA / "credentials.json"
    if not path.exists():
        path.write_text(
            json.dumps(
                {
                    "password": secrets.token_urlsafe(18),
                    "api_key": secrets.token_urlsafe(32),
                },
                indent=2,
            )
            + "\n"
        )
        path.chmod(0o600)
    return json.loads(path.read_text())


def password_required():
    return credentials().get("password_required", True)


@asynccontextmanager
async def lifespan(app):
    db.init()
    credentials()
    yield


app = FastAPI(title="PaperSpeak Linux", version="0.1.0", lifespan=lifespan)
login_attempts = {}


@app.middleware("http")
async def headers(request, call_next):
    # Same-origin cookies, no permissive CORS. Block cross-origin state-changing calls.
    origin = request.headers.get("origin")
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and origin
        and origin.rstrip("/") != str(request.base_url).rstrip("/")
    ):
        return JSONResponse(
            {"detail": "Use the PaperSpeak page to make this request."}, status_code=403
        )
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["Permissions-Policy"] = "microphone=(self), camera=()"
    r.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
    )
    if request.url.path.startswith("/api"):
        r.headers["Cache-Control"] = "no-store"
    return r


def auth(request: Request):
    if not password_required():
        return
    bearer = request.headers.get("authorization", "")
    if bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:], credentials()["api_key"]
    ):
        return
    cookie = request.cookies.get("paperspeak_session", "")
    if cookie:
        key = hashlib.sha256(cookie.encode()).hexdigest()
        if db.one(
            "SELECT * FROM sessions WHERE token_hash=? AND expires>?",
            (key, time.time()),
        ):
            return
    raise HTTPException(401, "Please sign in.")


class Login(BaseModel):
    password: str = Field(max_length=200)


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    if not password_required():
        return {"ok": True}
    host = request.client.host if request.client else "unknown"
    now = time.time()
    history = [t for t in login_attempts.get(host, []) if now - t < 60]
    if len(history) >= 5:
        raise HTTPException(429, "Please wait a minute before trying again.")
    if not secrets.compare_digest(body.password, credentials()["password"]):
        login_attempts[host] = history + [now]
        raise HTTPException(401, "That password did not match.")
    token = secrets.token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires<?", (now,))
    db.execute(
        "INSERT INTO sessions VALUES (?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), now + 30 * 86400),
    )
    response.set_cookie(
        "paperspeak_session",
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=30 * 86400,
    )
    return {"ok": True}


@app.post("/api/logout", dependencies=[Depends(auth)])
def logout(request: Request, response: Response):
    if not password_required():
        return {"ok": True}
    db.execute(
        "DELETE FROM sessions WHERE token_hash=?",
        (
            hashlib.sha256(
                request.cookies.get("paperspeak_session", "").encode()
            ).hexdigest(),
        ),
    )
    response.delete_cookie("paperspeak_session")
    return {"ok": True}


@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status", dependencies=[Depends(auth)])
def status():
    worker = db.one("SELECT * FROM cursors WHERE key='worker'")
    return {
        "password_required": password_required(),
        "gpu": gpu_info(),
        "worker": worker["data"] if worker else None,
        "models": list(config.manifest().get("models", {})),
        "time": time.time(),
    }


@app.get("/api/papers", dependencies=[Depends(auth)])
def list_papers():
    return db.all(
        "SELECT p.*, (SELECT count(*) FROM lessons l WHERE l.paper_id=p.id) AS lesson_count FROM papers p ORDER BY created DESC"
    )


@app.get("/api/papers/{ident}", dependencies=[Depends(auth)])
def paper(ident: str):
    result = db.one("SELECT * FROM papers WHERE id=?", (ident,))
    if not result:
        raise HTTPException(404, "Paper not found")
    result["lessons"] = db.all(
        "SELECT * FROM lessons WHERE paper_id=? ORDER BY created DESC", (ident,)
    )
    return result


class ImportRequest(BaseModel):
    reference: str = Field(max_length=300)
    generate: bool = True


@app.post("/api/papers/import", dependencies=[Depends(auth)])
def import_paper(body: ImportRequest):
    try:
        base, version = papers.parse_reference(body.reference)
    except ValueError as e:
        raise HTTPException(422, str(e))
    ident = db.enqueue(
        "import",
        base + version,
        {"reference": base + version, "generate": body.generate},
        priority=5,
    )
    return {"job_id": ident}


@app.post("/api/papers/upload", dependencies=[Depends(auth)])
async def upload_paper(file: UploadFile = File(...)):
    content = await file.read(100 * 1024 * 1024 + 1)
    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(413, "PDF limit is 100 MB.")
    if not content.startswith(b"%PDF-"):
        raise HTTPException(422, "Please choose a PDF.")
    digest = hashlib.sha256(content).hexdigest()
    title = Path(file.filename or "Uploaded paper").stem[:200]
    pid = papers.register(
        {
            "source_id": "pdf:" + digest,
            "version": "1",
            "title": title,
            "authors": [],
            "abstract": "",
            "categories": [],
            "uploaded": True,
        }
    )
    path = config.DATA / "papers" / pid / "paper.pdf"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    p = db.one("SELECT * FROM papers WHERE id=?", (pid,))
    p["data"]["pdf_path"] = str(path.relative_to(config.DATA))
    db.execute("UPDATE papers SET data=? WHERE id=?", (db.dumps(p["data"]), pid))
    lid = lessons.create(pid)
    jid = db.enqueue("lesson", lid)
    return {"paper_id": pid, "lesson_id": lid, "job_id": jid}


@app.post("/api/papers/{ident}/lessons", dependencies=[Depends(auth)])
def generate_lesson(ident: str):
    try:
        lid = lessons.create(ident)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"lesson_id": lid, "job_id": db.enqueue("lesson", lid)}


@app.get("/api/lessons", dependencies=[Depends(auth)])
def list_lessons():
    return db.all(
        "SELECT l.id,l.paper_id,l.state,l.created,l.updated,json_object('title',json_extract(l.data,'$.title'),'phase',json_extract(l.data,'$.phase'),'format',json_extract(l.data,'$.format')) AS data, (SELECT count(*) FROM chapters c WHERE c.lesson_id=l.id AND c.state='ready') AS ready_chapters,(SELECT count(*) FROM chapters c WHERE c.lesson_id=l.id) AS chapter_count,(SELECT state FROM jobs j WHERE j.kind='lesson' AND j.target=l.id ORDER BY created DESC LIMIT 1) AS job_state FROM lessons l ORDER BY created DESC"
    )


@app.get("/api/lessons/{ident}", dependencies=[Depends(auth)])
def get_lesson(ident: str):
    l = db.one("SELECT * FROM lessons WHERE id=?", (ident,))
    if not l:
        raise HTTPException(404, "Lesson not found")
    l["chapters"] = db.all(
        "SELECT * FROM chapters WHERE lesson_id=? ORDER BY ordinal", (ident,)
    )
    l["visuals"] = db.all("SELECT * FROM visual_assets WHERE lesson_id=? ORDER BY created,id", (ident,))
    l["paper"] = db.one("SELECT * FROM papers WHERE id=?", (l["paper_id"],))
    l["attempts"] = db.all(
        "SELECT * FROM attempts WHERE lesson_id=? ORDER BY created DESC", (ident,)
    )
    progress = db.one("SELECT * FROM cursors WHERE key=?", ("lesson:" + ident,))
    l["progress"] = progress["data"] if progress else {}
    l["job"] = db.one(
        "SELECT id,state,stage,error FROM jobs WHERE kind='lesson' AND target=? ORDER BY created DESC LIMIT 1",
        (ident,),
    )
    translation_jobs = db.all(
        "SELECT target,id,state,stage,error,progress FROM jobs WHERE kind='translate' AND target IN (SELECT id FROM chapters WHERE lesson_id=?) ORDER BY created DESC",
        (ident,),
    )
    by_chapter = {}
    for job in translation_jobs:
        by_chapter.setdefault(job["target"], job)
    for chapter in l["chapters"]:
        chapter["translation_job"] = by_chapter.get(chapter["id"])
    return l


@app.post("/api/chapters/{ident}/translation", dependencies=[Depends(auth)])
def translate_chapter(ident: str):
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    if not chapter or chapter["state"] != "ready":
        raise HTTPException(404, "Choose a finished chapter first.")
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (chapter["lesson_id"],))
    if translation.complete(chapter, lesson):
        return {"ready": True, "job_id": None}
    # Use normal priority so long chapter translations alternate with lesson
    # checkpoints. Recording assessments keep their higher priority.
    return {"ready": False, "job_id": db.enqueue("translate", ident, priority=10)}


class Progress(BaseModel):
    chapter_id: str
    turn_index: int = Field(ge=0)
    role: Literal["both", "host", "guide"] = "both"
    speed: float = Field(default=1.0, ge=0.5, le=1.5)
    subtitles: bool = True
    visual_mode: Literal["auto", "pinned"] = "auto"
    visual_key: str | None = Field(default=None, max_length=20)


@app.put("/api/lessons/{ident}/progress", dependencies=[Depends(auth)])
def progress(ident: str, body: Progress):
    chapter = db.one("SELECT * FROM chapters WHERE id=? AND lesson_id=?", (body.chapter_id, ident))
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    keys = {v["key"] for v in chapter["data"].get("visuals", [])}
    if body.visual_mode == "pinned" and body.visual_key not in keys:
        raise HTTPException(422, "Choose a visual from this chapter.")
    if body.visual_mode == "auto":
        body.visual_key = None
    db.execute(
        "INSERT INTO cursors VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
        ("lesson:" + ident, db.dumps(body.model_dump())),
    )
    return {"ok": True}


@app.get("/api/sources/{ident:path}", dependencies=[Depends(auth)])
def source(ident: str):
    s = db.one("SELECT * FROM sources WHERE id=?", (ident,))
    if not s:
        raise HTTPException(404, "Source not found")
    return s


@app.get("/api/files/{path:path}", dependencies=[Depends(auth)])
def get_file(path: str):
    try:
        target = config.safe_path(path)
    except ValueError:
        raise HTTPException(404, "File not found")
    if not target.is_file() or path.split("/")[0] not in {
        "papers",
        "audio",
        "recordings",
        "visuals",
    }:
        raise HTTPException(404, "File not found")
    return FileResponse(target)


@app.post("/api/attempts", dependencies=[Depends(auth)])
async def record_attempt(
    file: UploadFile = File(...),
    chapter_id: str = Form(...),
    turn_id: str | None = Form(None),
    question_id: str | None = Form(None),
    client_id: str | None = Form(None),
):
    c = db.one("SELECT * FROM chapters WHERE id=?", (chapter_id,))
    if not c or c["state"] != "ready":
        raise HTTPException(422, "Choose a ready chapter first.")
    if bool(turn_id) == bool(question_id):
        raise HTTPException(422, "Choose one sentence or question.")
    if turn_id and not any(t["id"] == turn_id for t in c["data"]["turns"]):
        raise HTTPException(422, "Sentence not found")
    if question_id and not any(
        q["id"] == question_id for q in c["data"].get("questions", [])
    ):
        raise HTTPException(422, "Question not found")
    content = await file.read(25 * 1024 * 1024 + 1)
    if not content or len(content) > 25 * 1024 * 1024:
        raise HTTPException(413, "Recording limit is 25 MB.")
    if client_id:
        import uuid

        try:
            ident = uuid.UUID(client_id).hex
        except ValueError:
            raise HTTPException(422, "Invalid recording identifier")
    else:
        ident = db.uid()
    suffix = (
        ".webm"
        if "webm" in (file.content_type or "")
        else ".mp4"
        if "mp4" in (file.content_type or "")
        else ".bin"
    )
    path = config.DATA / "recordings" / (ident + suffix)
    digest = hashlib.sha256(content).hexdigest()
    data = {
        "audio": str(path.relative_to(config.DATA)),
        "question_id": question_id,
        "phase": "normalize",
        "sha256": digest,
        "model_manifest": config.manifest(),
        "language_model": db.settings()["model_profile"],
    }
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = db.row(
            conn.execute("SELECT * FROM attempts WHERE id=?", (ident,)).fetchone()
        )
        if previous:
            if (
                previous["chapter_id"] != chapter_id
                or previous["turn_id"] != turn_id
                or previous["data"].get("question_id") != question_id
                or previous["data"].get("sha256") != digest
            ):
                raise HTTPException(409, "Recording identifier is already in use")
            return {"attempt_id": ident, "job_id": None}
        path.write_bytes(content)
        conn.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?)",
            (
                ident,
                c["lesson_id"],
                chapter_id,
                turn_id,
                "read" if turn_id else "answer",
                "pending",
                db.dumps(data),
                time.time(),
            ),
        )
        jid = db.queue_job(conn, "practice", ident, priority=0)
    db.event("job", {"id": jid, "state": "queued"})
    return {"attempt_id": ident, "job_id": jid}


@app.get("/api/attempts/{ident}", dependencies=[Depends(auth)])
def get_attempt(ident: str):
    a = db.one("SELECT * FROM attempts WHERE id=?", (ident,))
    if not a:
        raise HTTPException(404, "Recording not found")
    return a


@app.delete("/api/attempts/{ident}", dependencies=[Depends(auth)])
def delete_attempt(ident: str):
    a = db.one("SELECT * FROM attempts WHERE id=?", (ident,))
    if not a:
        raise HTTPException(404, "Recording not found")
    if db.one(
        "SELECT id FROM jobs WHERE kind='practice' AND target=? AND state IN ('queued','running','paused')",
        (ident,),
    ):
        raise HTTPException(409, "Stop the recording check before deleting it.")
    for k in ("audio", "wav"):
        if a["data"].get(k):
            config.safe_path(a["data"][k]).unlink(missing_ok=True)
    db.execute("DELETE FROM attempts WHERE id=?", (ident,))
    return {"ok": True}


@app.get("/api/reviews", dependencies=[Depends(auth)])
def reviews():
    return db.all(
        "SELECT r.*,json_object('title',json_extract(l.data,'$.title')) AS lesson_data FROM reviews r JOIN lessons l ON l.id=r.lesson_id WHERE r.due<=? ORDER BY r.due",
        (time.time(),),
    )


class ReviewResult(BaseModel):
    again: bool = False


@app.post("/api/reviews/{ident}/complete", dependencies=[Depends(auth)])
def finish_review(ident: str, body: ReviewResult):
    try:
        practice.complete_review(ident, body.again)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.get("/api/jobs", dependencies=[Depends(auth)])
def jobs():
    # Checkpoints can contain tens of thousands of collected abstracts or audio
    # evaluation results. Status refreshes only need their small public summary.
    return db.all(
        "SELECT id,kind,target,state,stage,progress,error,priority,created,updated FROM jobs ORDER BY created DESC LIMIT 100"
    )


@app.post("/api/jobs/{ident}/{action}", dependencies=[Depends(auth)])
def control_job(ident: str, action: Literal["pause", "resume", "retry", "cancel"]):
    job = db.one("SELECT * FROM jobs WHERE id=?", (ident,))
    if not job:
        raise HTTPException(404, "Job not found")
    state = {
        "pause": "paused",
        "resume": "queued",
        "retry": "queued",
        "cancel": "cancelled",
    }[action]
    if job["state"] == "completed":
        raise HTTPException(409, "This job is already complete.")
    if action in {"resume", "retry"} and job["state"] == "running":
        raise HTTPException(409, "This job is already running.")
    cp = job["checkpoint"]
    cp.pop("_failures", None)
    db.patch_job(ident, state=state, checkpoint=cp, error=None, available=0)
    if job["kind"] == "practice" and action in {"resume", "retry"}:
        db.execute("UPDATE attempts SET state='pending' WHERE id=?", (job["target"],))
    elif job["kind"] == "practice" and action == "cancel":
        db.execute("UPDATE attempts SET state='cancelled' WHERE id=?", (job["target"],))
    if job["kind"] == "lesson" and action == "cancel":
        db.execute(
            "UPDATE lessons SET state='cancelled' WHERE id=? AND state!='ready'",
            (job["target"],),
        )
    elif job["kind"] == "lesson" and action in {"resume", "retry"}:
        db.execute(
            "UPDATE lessons SET state='building' WHERE id=? AND state!='ready'",
            (job["target"],),
        )
        for chapter in db.all(
            "SELECT * FROM chapters WHERE lesson_id=? AND state!='ready'",
            (job["target"],),
        ):
            chapter["data"]["revision_round"] = 0
            chapter["data"]["audio_rephrase_rounds"] = 0
            for turn in chapter["data"].get("turns", []):
                if not turn.get("audio_verified"):
                    turn["audio_retries"] = 0
            db.save_chapter(chapter)
    return {"ok": True}


@app.get("/api/recommendations", dependencies=[Depends(auth)])
def recommendations():
    rows = db.all(
        "SELECT r.*,p.title,p.data AS paper_data FROM recommendations r JOIN papers p ON p.id=r.paper_id WHERE r.state NOT IN ('needs_explanation','explanation_failed') ORDER BY r.day DESC,r.rowid DESC LIMIT 100"
    )
    for row in rows:
        row["data"].pop("reading_notes", None)
        row["data"].pop("selection_notes", None)
        row["data"].pop("assessment", None)
        ja = row["data"].get("ja")
        if ja and ja.get("source_digest") and ja["source_digest"] != recommendation_ja.source_digest(row):
            row["data"].pop("ja", None)
    return rows


class Feedback(BaseModel):
    value: Literal["interested", "not_interested", "read"]


@app.post("/api/recommendations/{ident}/feedback", dependencies=[Depends(auth)])
def feedback(ident: str, body: Feedback):
    if not db.execute(
        "UPDATE recommendations SET feedback=? WHERE id=?", (body.value, ident)
    ):
        raise HTTPException(404, "Recommendation not found")
    return {"ok": True}


class PaperSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=600)


@app.post("/api/paper-searches", dependencies=[Depends(auth)])
def start_paper_search(body: PaperSearchRequest):
    query = body.query.strip()
    if len(query) < 3:
        raise HTTPException(422, "読みたい論文について日本語で入力してください。")
    target = hashlib.sha256(query.encode()).hexdigest()
    active = db.one(
        "SELECT * FROM jobs WHERE kind='paper_search' AND state IN ('queued','running','paused') ORDER BY created DESC LIMIT 1"
    )
    if active:
        if active["target"] == target:
            return {"job_id": active["id"]}
        raise HTTPException(409, "先の論文検索が進行中です。完了後に次の検索を始められます。")
    return {"job_id": db.enqueue("paper_search", target, {"query": query}, priority=7)}


@app.get("/api/paper-searches", dependencies=[Depends(auth)])
def paper_searches():
    rows = db.all(
        "SELECT * FROM jobs WHERE kind='paper_search' ORDER BY created DESC LIMIT 8"
    )
    return [
        {
            "id": row["id"],
            "query": row["payload"]["query"],
            "state": row["state"],
            "stage": row["stage"],
            "progress": row["progress"],
            "error": row["error"],
            "results": [r for r in row["checkpoint"].get("results", []) if r.get("title_ja")],
            "found": row["checkpoint"].get("found"),
            "created": row["created"],
        }
        for row in rows
    ]


@app.post("/api/discover", dependencies=[Depends(auth)])
def discover():
    return {
        "job_id": db.enqueue(
            "discover", "manual:" + time.strftime("%Y-%m-%d"), priority=8
        )
    }


@app.get("/api/settings", dependencies=[Depends(auth)])
def settings():
    return db.settings()


class Settings(BaseModel):
    discovery_enabled: bool
    schedule_hour: int = Field(ge=0, le=23)
    schedule_minute: int = Field(ge=0, le=59)
    timezone: str = Field(max_length=80)
    daily_limit: int = Field(ge=0, le=3)
    categories: list[str] = Field(min_length=1, max_length=12)
    interests: str = Field(max_length=2000)
    model_profile: Literal["qwen-q8", "qwen-q6", "muse-q6"]
    max_auto_backlog: int = Field(ge=1, le=5)


@app.put("/api/settings", dependencies=[Depends(auth)])
def update_settings(body: Settings):
    try:
        ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError:
        raise HTTPException(422, "Unknown time zone")
    allowed = {
        "cs.AI",
        "cs.LG",
        "cs.CL",
        "cs.CV",
        "cs.RO",
        "cs.SD",
        "stat.ML",
        "cs.NE",
        "cs.HC",
        "eess.AS",
        "eess.IV",
    }
    if not set(body.categories) <= allowed:
        raise HTTPException(422, "Choose supported AI categories")
    for key, value in body.model_dump().items():
        db.set_setting(key, value)
    return db.settings()


@app.get("/api/events", dependencies=[Depends(auth)])
async def events(request: Request):
    latest = db.one("SELECT COALESCE(MAX(seq),0) AS seq FROM events")["seq"]
    try:
        last = int(
            request.headers.get(
                "last-event-id", request.query_params.get("after", str(latest))
            )
        )
    except ValueError:
        last = 0

    async def stream():
        nonlocal last
        while not await request.is_disconnected():
            rows = db.all(
                "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 50", (last,)
            )
            for e in rows:
                last = e["seq"]
                yield f"id: {last}\nevent: update\ndata: {db.dumps({'type': e['type'], 'data': e['data']})}\n\n"
            if not rows:
                yield ": keep-alive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


dist = config.ROOT / "frontend/dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Endpoint not found")
        return FileResponse(dist / "index.html")
