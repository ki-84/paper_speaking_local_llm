import time
import uuid

import numpy as np
import pytest
import soundfile as sf
from paperspeak import config, db, lessons, papers
from paperspeak.benchmark import equivalent
from paperspeak.practice import audio_quality


def ready_chapter():
    pid = papers.register(
        {"source_id": "2106.09685", "version": "v1", "title": "Example"}
    )
    lid = lessons.create(pid)
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        ("c", lid, 0, "ready", db.dumps({"turns": [{"id": "t"}]})),
    )
    return lid


def test_recording_retry_is_idempotent_and_cannot_overwrite(client):
    ready_chapter()
    cid = str(uuid.uuid4())
    data = {"chapter_id": "c", "turn_id": "t", "client_id": cid}
    files = {"file": ("a.webm", b"saved recording", "audio/webm")}
    first = client.post("/api/attempts", data=data, files=files)
    second = client.post("/api/attempts", data=data, files=files)
    assert first.status_code == second.status_code == 200
    assert first.json()["attempt_id"] == second.json()["attempt_id"]
    assert db.one("SELECT COUNT(*) AS n FROM attempts")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='practice'")["n"] == 1
    different = client.post(
        "/api/attempts", data=data, files={"file": ("a.webm", b"changed", "audio/webm")}
    )
    assert different.status_code == 409
    a = db.one("SELECT * FROM attempts")
    assert config.safe_path(a["data"]["audio"]).read_bytes() == b"saved recording"


def test_noise_silence_and_clipping_are_not_learning_errors(database):
    sr = 16000
    rng = np.random.default_rng(2026)
    for name, samples in [
        ("silence", np.zeros(sr)),
        ("noise", rng.normal(0, 0.05, sr * 2)),
        ("tone", 0.1 * np.sin(np.arange(sr) * 2 * np.pi * 440 / sr)),
        ("clipping", np.ones(sr)),
    ]:
        path = database / (name + ".wav")
        sf.write(path, samples, sr)
        assert audio_quality(path)["status"] != "ok"


def test_recording_and_evaluation_job_commit_together(client, monkeypatch):
    ready_chapter()
    original = db.queue_job

    def interrupted(*args, **kwargs):
        raise RuntimeError("Interrupted before queuing")

    monkeypatch.setattr(db, "queue_job", interrupted)
    data = {"chapter_id": "c", "turn_id": "t", "client_id": str(uuid.uuid4())}
    files = {"file": ("a.webm", b"preserved locally for retry", "audio/webm")}
    with pytest.raises(RuntimeError, match="Interrupted before queuing"):
        client.post("/api/attempts", data=data, files=files)
    assert db.one("SELECT count(*) n FROM attempts")["n"] == 0
    monkeypatch.setattr(db, "queue_job", original)
    response = client.post("/api/attempts", data=data, files=files)
    assert response.status_code == 200
    saved = response.json()
    assert (
        db.one("SELECT target FROM jobs WHERE id=?", (saved["job_id"],))["target"]
        == saved["attempt_id"]
    )


def test_recording_priority_and_paused_job_survive_recovery(database):
    a = db.enqueue("lesson", "long")
    db.claim("dead")
    db.patch_job(a, state="paused", heartbeat=time.time() - 1000)
    b = db.enqueue("lesson", "next")
    p = db.enqueue("practice", "voice", priority=0)
    assert db.claim("live")["id"] == p
    assert db.one("SELECT state FROM jobs WHERE id=?", (a,))["state"] == "paused"
    assert db.claim("live")["id"] == b


def test_long_manual_lesson_does_not_starve_discovery_or_recordings(database):
    manual = db.enqueue("lesson", "long", priority=10)
    discovery = db.enqueue("discover", "waiting", priority=30)
    db.execute("UPDATE jobs SET created=? WHERE id=?", (time.time() - 1000, discovery))
    recording = db.enqueue("practice", "voice", priority=0)
    assert db.claim("live")["id"] == recording
    db.patch_job(recording, state="completed", owner=None)
    assert db.claim("live")["id"] == discovery
    db.patch_job(discovery, state="queued", owner=None)
    assert db.claim("live")["id"] == manual


@pytest.mark.parametrize(
    "feedback",
    [
        {"understanding": "clear"},
        {
            "understanding": "clear",
            "content_feedback": "正しいです",
            "english_feedback": "Try a shorter sentence.",
            "better_answer": "The old weights stay fixed.",
            "next_step": "Try again.",
        },
    ],
)
def test_invalid_answer_feedback_preserves_recording_without_a_grade(client, feedback):
    from paperspeak import practice

    ready_chapter()
    db.execute(
        "UPDATE chapters SET data=? WHERE id='c'",
        (
            db.dumps(
                {
                    "turns": [],
                    "questions": [{"id": "q", "question": "What stays fixed?"}],
                }
            ),
        ),
    )
    response = client.post(
        "/api/attempts",
        data={"chapter_id": "c", "question_id": "q", "client_id": str(uuid.uuid4())},
        files={"file": ("answer.webm", b"original answer", "audio/webm")},
    )
    assert response.status_code == 200
    saved = response.json()
    attempt = db.one("SELECT * FROM attempts WHERE id=?", (saved["attempt_id"],))
    attempt["data"].update(phase="understand", transcript="The old weights.")
    practice.save_attempt(attempt)

    class IncompleteProvider:
        def ask(self, *args, **kwargs):
            return feedback

    with pytest.raises(ValueError, match="feedback was incomplete"):
        practice.practice_step(
            db.one("SELECT * FROM jobs WHERE id=?", (saved["job_id"],)),
            IncompleteProvider(),
        )
    current = db.one("SELECT * FROM attempts WHERE id=?", (saved["attempt_id"],))
    assert current["state"] == "pending" and "comprehension" not in current["data"]
    assert config.safe_path(current["data"]["audio"]).read_bytes() == b"original answer"
    assert db.one("SELECT count(*) n FROM reviews")["n"] == 0


def test_reference_scalar_types_do_not_change_scientific_grade():
    assert equivalent("3.17", 3.17)
    assert equivalent("false", False)
    assert not equivalent("true", False)
    assert not equivalent(True, 1)


def test_unverified_chapter_never_becomes_ready(database, monkeypatch):
    pid = papers.register(
        {"source_id": "2106.09685", "version": "v1", "title": "Paper"}
    )
    lid = lessons.create(pid)
    l = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    l["data"].update(
        phase="chapters",
        notes=[{"claims": [{"id": "N1C1", "claim": "A claim", "source_ids": ["S1"]}]}],
    )
    db.save_lesson(l)
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        ("S1", pid, "page", db.dumps({"text": "The original claim."})),
    )
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        (
            "c",
            lid,
            0,
            "review",
            db.dumps(
                {
                    "title": "Idea",
                    "claim_ids": ["N1C1"],
                    "turns": [{"id": "t", "text": "False claim"}],
                    "revision_round": lessons.MAX_SCIENTIFIC_REVISIONS,
                }
            ),
        ),
    )
    jid = db.enqueue("lesson", lid)
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))

    class Reviewer:
        def ask(self, *a, **k):
            return {
                "passed": False,
                "issues": [{"turn_id": "t", "reason": "Not in paper"}],
            }

    import pytest

    with pytest.raises(ValueError, match="unresolved"):
        lessons.lesson_step(job, Reviewer())
    assert db.one("SELECT state FROM chapters WHERE id='c'")["state"] == "review"
    assert db.one("SELECT state FROM lessons WHERE id=?", (lid,))["state"] == "building"


def test_cancelled_recording_stays_cancelled_during_inflight_save(client):
    from paperspeak.practice import save_attempt

    ready_chapter()
    r = client.post(
        "/api/attempts",
        data={"chapter_id": "c", "turn_id": "t"},
        files={"file": ("a.webm", b"recording", "audio/webm")},
    ).json()
    cached = db.one("SELECT * FROM attempts WHERE id=?", (r["attempt_id"],))
    assert client.post("/api/jobs/" + r["job_id"] + "/cancel").status_code == 200
    cached["data"]["phase"] = "transcribe"
    save_attempt(cached)
    assert (
        db.one("SELECT state FROM attempts WHERE id=?", (r["attempt_id"],))["state"]
        == "cancelled"
    )
    assert client.post("/api/jobs/" + r["job_id"] + "/retry").status_code == 200
    assert (
        db.one("SELECT state FROM attempts WHERE id=?", (r["attempt_id"],))["state"]
        == "pending"
    )


def test_cancelled_lesson_can_be_replaced_without_losing_its_revision(client):
    lid = ready_chapter()
    jid = db.enqueue("lesson", lid)
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200
    prior = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    assert prior["state"] == "cancelled"
    new = lessons.create(prior["paper_id"])
    assert new != lid
    assert (
        db.one("SELECT state FROM chapters WHERE lesson_id=?", (lid,))["state"]
        == "ready"
    )
    assert client.post(f"/api/jobs/{jid}/retry").status_code == 200
    assert db.one("SELECT state FROM lessons WHERE id=?", (lid,))["state"] == "building"
