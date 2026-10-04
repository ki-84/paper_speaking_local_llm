import json

from paperspeak import config, db, lessons, papers


def test_authentication_and_private_file_boundary(client):
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/files/credentials.json").status_code == 404
    assert client.get("/api/files/paperspeak.sqlite3").status_code == 404
    client.headers.pop("Authorization")
    assert client.get("/api/papers").status_code == 401
    assert client.get("/health").status_code == 200


def test_password_free_mode_keeps_private_files_and_origin_boundary(client):
    from paperspeak.api import credentials

    path = config.DATA / "credentials.json"
    data = credentials()
    data["password_required"] = False
    path.write_text(json.dumps(data))
    client.headers.pop("Authorization")
    assert client.get("/api/status").json()["password_required"] is False
    assert client.get("/api/papers").status_code == 200
    assert client.get("/api/files/credentials.json").status_code == 404
    assert (
        client.post(
            "/api/papers/import",
            json={"reference": "1706.03762"},
            headers={"Origin": "https://another-site.example"},
        ).status_code
        == 403
    )


def test_cross_origin_write_is_blocked(client):
    r = client.post(
        "/api/papers/import",
        json={"reference": "1706.03762"},
        headers={"Origin": "https://malicious.example"},
    )
    assert r.status_code == 403


def test_import_duplicate_requests_queue_once(client):
    a = client.post("/api/papers/import", json={"reference": "1706.03762"})
    b = client.post(
        "/api/papers/import", json={"reference": "https://arxiv.org/abs/1706.03762"}
    )
    assert a.status_code == 200 and a.json() == b.json()
    assert (
        client.post(
            "/api/papers/import", json={"reference": "http://localhost:8190/private"}
        ).status_code
        == 422
    )


def test_story_import_is_idempotent_and_does_not_reuse_legacy_import(
    client, monkeypatch
):
    from paperspeak.worker import import_step

    legacy = client.post("/api/papers/import", json={"reference": "2106.09685"}).json()
    body = {"reference": "2106.09685", "generate": False, "create_video": True}
    response = client.post("/api/papers/import", json=body)
    assert response.status_code == 200
    assert response.json() == client.post("/api/papers/import", json=body).json()
    assert response.json()["job_id"] != legacy["job_id"]
    pid = papers.register({"source_id": "2106.09685", "version": "v1", "title": "LoRA"})
    monkeypatch.setattr(papers, "register_arxiv", lambda reference: pid)
    monkeypatch.setattr(papers, "ingest", lambda ident: None)
    job = db.one("SELECT * FROM jobs WHERE id=?", (response.json()["job_id"],))
    assert import_step(job)
    first = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))["checkpoint"]
    assert first["paper_id"] == pid and first["project_id"]
    # A crash after creating the project reuses its saved identity on retry.
    assert import_step(job)
    assert db.one("SELECT count(*) AS n FROM video_projects")["n"] == 1
    assert db.one("SELECT count(*) AS n FROM lessons")["n"] == 2
    assert db.one("SELECT count(*) AS n FROM jobs WHERE kind='lesson'")["n"] == 0
    result = next(j for j in client.get("/api/jobs").json() if j["id"] == job["id"])
    assert result["paper_id"] == pid
    assert "checkpoint" not in result


def test_pdf_upload_can_create_two_story_lessons_without_legacy_job(client):
    files = {"file": ("A new idea.pdf", b"%PDF-1.4\nTest paper", "application/pdf")}
    response = client.post(
        "/api/papers/upload", data={"create_video": "true"}, files=files
    )
    assert response.status_code == 200
    result = response.json()
    assert result["project_id"] and result["paper_id"]
    assert (
        result
        == client.post(
            "/api/papers/upload", data={"create_video": "true"}, files=files
        ).json()
    )
    assert db.one("SELECT count(*) AS n FROM jobs WHERE kind='lesson'")["n"] == 0
    assert {l["data"]["format"] for l in db.all("SELECT * FROM lessons")} == {
        "paper-story-1"
    }
    assert (
        client.post(
            "/api/papers/upload",
            data={"create_video": "true"},
            files={"file": ("bad.pdf", b"invalid")},
        ).status_code
        == 422
    )


def test_cannot_record_against_unpublished_chapter(client):
    pid = papers.register({"source_id": "1706.03762", "version": "v1", "title": "Test"})
    lid = lessons.create(pid)
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        ("c", lid, 0, "draft", db.dumps({"turns": [{"id": "t"}]})),
    )
    r = client.post(
        "/api/attempts",
        data={"chapter_id": "c", "turn_id": "t"},
        files={"file": ("r.webm", b"audio", "audio/webm")},
    )
    assert r.status_code == 422


def test_recording_saved_before_processing_and_preserved_on_retry(client):
    pid = papers.register({"source_id": "1706.03762", "version": "v1", "title": "Test"})
    lid = lessons.create(pid)
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        ("c", lid, 0, "ready", db.dumps({"turns": [{"id": "t"}]})),
    )
    r = client.post(
        "/api/attempts",
        data={"chapter_id": "c", "turn_id": "t"},
        files={"file": ("r.webm", b"real test bytes", "audio/webm")},
    )
    assert r.status_code == 200
    a = client.get("/api/attempts/" + r.json()["attempt_id"]).json()
    assert a["state"] == "pending"
    assert client.get("/api/files/" + a["data"]["audio"]).content == b"real test bytes"
    job = r.json()["job_id"]
    db.patch_job(job, state="failed", checkpoint={"_failures": 3})
    assert client.post(f"/api/jobs/{job}/retry").status_code == 200
    assert db.one("SELECT * FROM jobs WHERE id=?", (job,))["checkpoint"] == {}


def test_settings_reject_unknown_category_and_timezone(client):
    s = client.get("/api/settings").json()
    s["timezone"] = "Not/AZone"
    assert client.put("/api/settings", json=s).status_code == 422
    s["timezone"] = "Asia/Tokyo"
    s["categories"] = ["all:secret"]
    assert client.put("/api/settings", json=s).status_code == 422


def test_scheduler_does_not_duplicate_after_restart(database):
    from paperspeak.discovery import schedule

    db.set_setting("schedule_hour", 0)
    db.set_setting("schedule_minute", 0)
    schedule()
    schedule()
    assert db.one("SELECT count(*) AS n FROM jobs WHERE kind='discover'")["n"] == 1
