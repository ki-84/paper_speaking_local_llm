"""Optional OAuth-authorized, resumable upload of the complete local video."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import re
import time

import httpx

from . import config, db
from .quality import QualityHold
from .runtime import PracticePreempted

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024


def _secret_dir():
    path = config.DATA / "youtube"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def connected():
    client, token = _secret_dir() / "client.json", _secret_dir() / "token.json"
    if not client.is_file() or not token.is_file():
        return False
    try:
        saved = json.loads(token.read_text())
        expiry = saved.get("refresh_expires_at")
        return bool(saved.get("refresh_token")) and (expiry is None or expiry > time.time() + 120)
    except (OSError, ValueError, TypeError):
        return False


def automatic():
    return connected() and db.settings().get("youtube_auto_upload", False) is True


def _write_private(path, data):
    pending = path.with_name(path.name + ".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pending.chmod(0o600)
    pending.replace(path)


def _credentials():
    if not connected():
        raise ValueError("Connect a Google account before uploading to YouTube.")
    client = json.loads((_secret_dir() / "client.json").read_text())["installed"]
    token = json.loads((_secret_dir() / "token.json").read_text())
    return client, token


def access_token():
    client, token = _credentials()
    if token.get("access_token") and token.get("expires_at", 0) > time.time() + 120:
        return token["access_token"]
    response = httpx.post(TOKEN_URL, data={"client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""), "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token"}, timeout=10)
    response.raise_for_status()
    result = response.json()
    token.update(access_token=result["access_token"], expires_at=time.time() + result.get("expires_in", 3600))
    _write_private(_secret_dir() / "token.json", token)
    return token["access_token"]


def schedule(export):
    if not automatic() or export["kind"] != "full" or export["state"] != "ready":
        return None
    if export["data"].get("youtube", {}).get("video_id"):
        return None
    job = db.one("SELECT * FROM jobs WHERE kind='youtube_upload' AND target=? ORDER BY created DESC LIMIT 1", (export["id"],))
    if job and job["state"] in {"queued", "running", "completed", "failed", "paused"}:
        return job["id"]
    return db.enqueue("youtube_upload", export["id"], priority=10)


def _update(export, **youtube):
    state = export["data"].get("youtube", {}) | youtube
    data = export["data"] | {"youtube": state}
    db.execute("UPDATE video_exports SET data=?,updated=? WHERE id=?",
               (db.dumps(data), time.time(), export["id"]))
    db.event("video", {"id": export["id"], "lesson_id": export["lesson_id"], "youtube": state.get("state")})


def _headers():
    return {"Authorization": "Bearer " + access_token()}


def _video_title(export):
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (export["lesson_id"],))
    title = lesson["data"]["title"] if lesson else "Paper lesson"
    return (title + " | Easy English and Japanese paper lesson")[:100]


def _init_session(export, size):
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (export["lesson_id"],))
    paper = db.one("SELECT * FROM papers WHERE id=?", (lesson["paper_id"],)) if lesson else None
    source = paper["data"].get("url", "") if paper else ""
    metadata = {"snippet": {"title": _video_title(export),
                "description": "An easy-English conversation about this paper, with English and Japanese subtitles burned into the video.\n\nPaper: " + source + "\n\nFigures from the paper are labelled as original figures; extra diagrams are teaching aids.",
                "categoryId": "27", "defaultLanguage": "en"},
                "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    response = httpx.post(UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status", "notifySubscribers": "false"},
        headers=_headers() | {"Content-Type": "application/json; charset=UTF-8",
                              "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
        json=metadata, timeout=10)
    response.raise_for_status()
    uri = response.headers.get("Location")
    if not uri or not uri.startswith("https://www.googleapis.com/"):
        raise RuntimeError("YouTube did not return a trusted resumable upload URL.")
    _update(export, state="uploading", session=uri, size=size, privacy="private", bytes_sent=0)


def _remote_status(uri, size):
    response = httpx.put(uri, headers=_headers() | {"Content-Length": "0",
        "Content-Range": f"bytes */{size}"}, content=b"", timeout=10)
    if response.status_code in {200, 201}:
        return size, response.json().get("id")
    if response.status_code == 404:
        raise QualityHold("YouTube upload session expired; check the channel before starting another upload.")
    if response.status_code != 308:
        response.raise_for_status()
        raise RuntimeError("Unexpected YouTube upload status")
    match = re.fullmatch(r"bytes=0-(\d+)", response.headers.get("Range", ""))
    return (int(match[1]) + 1 if match else 0), None


async def _put_chunk(uri, headers, content, runtime):
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        request = asyncio.create_task(client.put(uri, headers=headers, content=content))
        while not request.done():
            if runtime.practice_waiting():
                request.cancel()
                with suppress(asyncio.CancelledError):
                    await request
                raise PracticePreempted("YouTube upload paused for your recording.")
            await asyncio.sleep(0.5)
        return await request


def upload_step(job, runtime):
    export = db.one("SELECT * FROM video_exports WHERE id=?", (job["target"],))
    if not export or export["kind"] != "full" or export["state"] != "ready":
        raise ValueError("A finished complete video is required for YouTube upload.")
    if not connected():
        raise ValueError("The Google connection is missing.")
    local = config.safe_path(export["data"]["mp4"])
    if not local.is_file() or local.stat().st_size != export["data"]["bytes"]:
        raise QualityHold("The complete video changed after export; upload stopped.")
    if runtime.practice_waiting():
        raise PracticePreempted("YouTube upload paused for your recording.")
    state = export["data"].get("youtube", {})
    if state.get("video_id"):
        return True
    size = local.stat().st_size
    if not state.get("session"):
        _init_session(export, size)
        db.patch_job(job["id"], stage="Opened a private YouTube upload session", progress=0.01)
        return False
    offset, video_id = _remote_status(state["session"], size)
    if video_id:
        _update(export, state="uploaded", video_id=video_id, bytes_sent=size,
                url="https://www.youtube.com/watch?v=" + video_id, uploaded_at=time.time())
        db.patch_job(job["id"], stage="Private YouTube upload complete", progress=1)
        return True
    if offset >= size:
        raise RuntimeError("YouTube received the bytes but has not returned a video ID yet.")
    end = min(size, offset + CHUNK)
    with local.open("rb") as stream:
        stream.seek(offset)
        content = stream.read(end - offset)
    response = asyncio.run(_put_chunk(state["session"],
        _headers() | {"Content-Type": "video/mp4", "Content-Length": str(len(content)),
                      "Content-Range": f"bytes {offset}-{end - 1}/{size}"},
        content, runtime))
    if response.status_code in {200, 201} and response.json().get("id"):
        video_id = response.json()["id"]
        _update(export, state="uploaded", video_id=video_id, bytes_sent=size,
                url="https://www.youtube.com/watch?v=" + video_id, uploaded_at=time.time())
        db.patch_job(job["id"], stage="Private YouTube upload complete", progress=1)
        return True
    if response.status_code != 308:
        response.raise_for_status()
        raise RuntimeError("Unexpected YouTube upload response")
    _update(export, state="uploading", bytes_sent=end)
    db.patch_job(job["id"], stage=f"Uploading privately to YouTube: {round(end / size * 100)}%",
                 progress=end / size)
    return False
