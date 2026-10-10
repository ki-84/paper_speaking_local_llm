import copy
import json
import time
import wave

import numpy as np
import pytest
from paperspeak import audience, config, db, papers, story, video, video_review
from paperspeak.runtime import GPUUnavailable, PracticePreempted


def project(modes=None):
    paper = papers.register(
        {
            "source_id": "finished-review-test",
            "version": "v1",
            "title": "A useful robot idea",
        }
    )
    ident = story.create(paper, modes=modes, legacy=True)["project_id"]
    return db.one("SELECT * FROM video_projects WHERE id=?", (ident,))


def test_request_is_persistent_idempotent_and_does_not_change_generation(database):
    p = project()
    original = db.dumps(p["data"])
    first = video_review.request(p["id"])
    second = video_review.request(p["id"])
    assert first == second
    assert len(db.all("SELECT id FROM video_reviews")) == 1
    assert (
        db.dumps(
            db.one("SELECT data FROM video_projects WHERE id=?", (p["id"],))["data"]
        )
        == original
    )
    job = db.one("SELECT * FROM jobs WHERE id=?", (first["job_id"],))
    assert not video_review.step(job, object())  # no GPU/inference while waiting
    assert (
        db.one("SELECT state FROM video_projects WHERE id=?", (p["id"],))["state"]
        == "building"
    )
    assert (
        db.one("SELECT available FROM jobs WHERE id=?", (job["id"],))["available"]
        > time.time()
    )


@pytest.mark.parametrize("changed", [False, True])
def test_reuse_requires_the_exact_completed_file_hash(database, changed):
    p = project(["overview"])
    export = finish_film(p)
    previous = video_review.get(p["id"])
    previous["state"] = "ready"
    previous["data"]["modes"] = {
        "overview": {
            "export_id": export["id"],
            "mp4": export["data"]["mp4"],
            "sha256": video.file_digest(config.safe_path(export["data"]["mp4"])),
            "scenes": [{"assessment": {"verdict": "good"}}],
        }
    }
    video_review.save(previous)
    db.execute(
        "UPDATE video_reviews SET input_digest='earlier-review-format' WHERE id=?",
        (previous["id"],),
    )
    if changed:
        with config.safe_path(export["data"]["mp4"]).open("ab") as f:
            f.write(b"changed-cut")
    latest = video_review.request(p["id"])
    data = video_review.get(p["id"])["data"]
    assert latest["review_id"] != previous["id"]
    assert bool(data["modes"]) is not changed
    assert (
        db.one("SELECT state FROM video_reviews WHERE id=?", (previous["id"],))["state"]
        == "ready"
    )


@pytest.mark.parametrize("exception", [PracticePreempted, GPUUnavailable])
def test_recording_and_gpu_wait_do_not_use_review_attempts(database, exception):
    p = project()
    requested = video_review.request(p["id"])
    review = video_review.get(p["id"])

    def interrupted():
        raise exception("Wait for recording")

    with pytest.raises(exception):
        video_review.bounded(review, "frames:overview:0", interrupted)
    assert review["data"]["units"]["frames:overview:0"]["attempts"] == 0
    assert video_review.get(p["id"])["id"] == requested["review_id"]


def test_review_errors_are_bounded_and_marked_unknown(database):
    p = project()
    video_review.request(p["id"])
    review = video_review.get(p["id"])

    def bad():
        raise ValueError("Cannot read the frame")

    for _ in range(3):
        assert video_review.bounded(review, "frame", bad) is None
    result = video_review.bounded(review, "frame", bad)
    assert result["verdict"] == "uncertain"
    assert result["assessment_incomplete"]
    assert video_review.get(p["id"])["data"]["units"]["frame"]["attempts"] == 3


def finish_film(p, mode="overview"):
    root = config.DATA / "videos" / "fixture"
    root.mkdir(parents=True, exist_ok=True)
    wav_path = config.DATA / "audio" / f"{mode}.wav"
    samples = (np.sin(np.arange(24000 * 3) * 2 * np.pi * 220 / 24000) * 3000).astype(
        np.int16
    )
    with wave.open(str(wav_path), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(samples.tobytes())
    mp4 = root / f"{mode}.mp4"
    video_review.command(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=1920x1080:r=15:d=3",
            "-i",
            str(wav_path),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(mp4),
        ]
    )
    text = "Notice the robot and the next action."
    s = {
        "title": "Observe and act",
        "title_ja": "観察して動く",
        "visual": {"type": "example", "nodes": []},
        "render_paths": ["visuals/fixture.png"],
        "utterances": [
            {
                "id": "u",
                "speaker": "guide",
                "text": text,
                "source_ids": [],
                "audio": f"audio/{mode}.wav",
                "duration": 3,
                "visual_focus": 0,
                "sentence_ranges": [[text, 0, 3]],
                "audio_check": {"timestamps": [], "wer": 0},
            }
        ],
        "subtitle_items": {
            "0:0": {"japanese": "ロボットと次の行動に注目してください。"}
        },
    }
    track = p["data"]["modes"][mode]
    ident = f"export-{mode}"
    track.update(scenes=[s], export_id=ident)
    data = {
        "mp4": str(mp4.relative_to(config.DATA)),
        "title": "A completed film",
        "duration": 3,
        "manifest": {"preview": False, "mode": mode, "scenes": [s]},
    }
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (
            ident,
            track["lesson_id"],
            "",
            mode,
            ident,
            "ready",
            db.dumps(data),
            time.time(),
            time.time(),
        ),
    )
    story.save(p)
    return db.one("SELECT * FROM video_exports WHERE id=?", (ident,))


def test_waits_for_every_full_film_and_never_resumes_a_paused_review(
    database, monkeypatch
):
    p = project()
    request = video_review.request(p["id"])
    finish_film(p)
    assert video_review.finished_exports(p, ["overview", "deep_dive"]) is None
    finish_film(p, "deep_dive")
    db.patch_job(request["job_id"], state="paused")
    video_review.schedule()
    assert (
        db.one("SELECT state FROM jobs WHERE id=?", (request["job_id"],))["state"]
        == "paused"
    )
    db.patch_job(request["job_id"], state="queued")
    video_review.schedule()
    assert (
        db.one("SELECT priority FROM jobs WHERE id=?", (request["job_id"],))["priority"]
        == 7
    )
    # A missing full MP4 must not be mistaken for a completed film.
    config.safe_path("videos/fixture/deep_dive.mp4").unlink()
    assert video_review.finished_exports(p, ["overview", "deep_dive"]) is None


def test_actual_encoded_frames_audio_and_report_survive_restart(database, monkeypatch):
    p = project(["overview"])
    requested = video_review.request(p["id"])
    export = finish_film(p)
    original_project = db.dumps(p["data"])

    class Reviewer:
        last_generation = {"profile": "local-test"}
        synthesis_calls = 0

        def ask(self, prompt, *, images=None, **kwargs):
            if "heard_and_seen" in prompt:
                line = json.loads(prompt.rsplit("\n", 1)[-1])["heard_and_seen"][0]
                return {
                    "personas": [
                        {
                            "id": p["id"],
                            "retell_en": "Look at the robot's next action.",
                            "understood": [
                                {
                                    "point_ja": "次の動きを見る",
                                    "utterance_id": line["id"],
                                    "quote": line["text"],
                                }
                            ],
                            "gaps": [],
                            "keep_watching": "yes",
                            "reason_ja": "次の行動を知りたい。",
                            "next_question_ja": "次は何？",
                            "scores": {"clarity": 4, "engagement": 3, "humor": 2},
                        }
                        for p in audience.PERSONAS
                    ]
                }
            if images:
                assert all(path.is_file() for path in images)
                return {
                    "verdict": "needs_work",
                    "summary_ja": "図を具体的にすると理解しやすくなります。",
                    "strengths_ja": [],
                    "issues": [
                        {
                            "category": "visuals",
                            "severity": "minor",
                            "sample": 0,
                            "reason_ja": "<script>alert(1)</script>",
                            "fix_ja": "動きを表す図を加える",
                            "source_ids": [],
                        }
                    ],
                }
            Reviewer.synthesis_calls += 1
            return {
                "summary_ja": "実動画の確認を完了。",
                "priority_fixes_ja": ["説明と図の関係を強める"],
                "overlap_ja": "この試験は1本のみ。",
                "limitations_ja": "実視聴者による評価ではありません。",
            }

        def speech(self, mode, request):
            assert mode == "asr"
            # This WAV really came from decoding AAC in the finished MP4.
            with wave.open(request["audio"]) as wav:
                assert wav.getnframes() > 60000 and wav.getframerate() == 24000
            return {"text": "Notice the robot and the next action."}

    original_publisher = video_review.publish_report
    publication_calls = 0

    def failed_disk_once(review):
        nonlocal publication_calls
        publication_calls += 1
        if publication_calls == 1:
            raise OSError("Report disk temporarily unavailable")
        return original_publisher(review)

    monkeypatch.setattr(video_review, "publish_report", failed_disk_once)
    for _ in range(12):
        # New instances read the committed checkpoint, as after a worker restart.
        job = db.one("SELECT * FROM jobs WHERE id=?", (requested["job_id"],))
        try:
            if video_review.step(job, Reviewer()):
                break
        except OSError:
            assert video_review.get(p["id"])["data"]["summary"]["summary_ja"]
    else:
        pytest.fail("Review did not finish")
    result = video_review.get(p["id"])
    assert Reviewer.synthesis_calls == 1
    assert result["state"] == "ready"
    info = result["data"]["modes"]["overview"]
    assert info["sha256"] == video.file_digest(config.safe_path(export["data"]["mp4"]))
    assert info["scenes"][0]["actual_resolution"] == [1920, 1080]
    issue = info["scenes"][0]["assessment"]["issues"][0]
    assert issue["at_seconds"] == info["scenes"][0]["sample_times"][0]
    assert info["audio_samples"][0]["assessment"]["matches_script"]
    report = config.safe_path(result["data"]["report_html"]).read_text()
    assert "&lt;script&gt;" in report and "<script>alert" not in report
    assert config.safe_path(result["data"]["report_json"]).is_file()
    # A subsequent review can reuse actual frames from the earlier review's
    # directory. Publishing must not assume that every image is under its root.
    reused = copy.deepcopy(result)
    reused["id"] = "later-review-reusing-frames"
    video_review.publish_report(reused)
    reused_html = config.safe_path(reused["data"]["report_html"]).read_text()
    assert "/api/files/videos/reviews/" in reused_html
    assert (
        db.dumps(
            db.one("SELECT data FROM video_projects WHERE id=?", (p["id"],))["data"]
        )
        == original_project
    )
    assert video_review.request(p["id"])["review_id"] == requested["review_id"]
    result["data"]["verified_assessment"] = {
        "scope_ja": "実際のフレームと脚本を追加照合しました。",
        "summary_ja": "無音中の切替を同期不良とする指摘を訂正。",
        "findings": [],
        "automatic_review_corrections_ja": "無音を新しい場面の発話として扱わない。",
    }
    video_review.publish_report(result)
    corrected = config.safe_path(result["data"]["report_html"]).read_text()
    assert "追加照合済み" in corrected
    assert "<details><summary>初回ローカルAIレビュー" in corrected
    assert "&lt;script&gt;" in corrected


def test_summary_uses_findings_without_repeating_memory_or_model_manifests(database):
    p = project(["overview"])
    export = finish_film(p)
    info = video_review.prepare_mode(video_review.get(p["id"]), "overview", export)
    info["audience"] = [
        {
            "title": "Opening",
            "start": 0,
            "assessment": {
                "memory": {"curious": ["UNNEEDED_MEMORY_MARKER" * 5000]},
                "generation": {"manifest": "UNNEEDED_MODEL_MANIFEST" * 5000},
                "personas": [
                    {
                        "id": "curious",
                        "scores": {"clarity": 2, "humor": 3, "engagement": 4},
                        "retell_en": "The task is clear but the cause is not.",
                        "keep_watching": "yes",
                        "reason_ja": "理由を知りたい。",
                        "gaps": [
                            {
                                "question_ja": "何を見て判定する？",
                                "add_ja": "入力から判定までの図を追加。",
                            }
                        ],
                    }
                ],
            },
        }
    ]
    r = video_review.get(p["id"])
    r["data"]["modes"] = {"overview": info}

    class Local:
        def ask(self, prompt, **kwargs):
            assert "UNNEEDED_MEMORY_MARKER" not in prompt
            assert "UNNEEDED_MODEL_MANIFEST" not in prompt
            assert "何を見て判定する" in prompt
            assert len(prompt) < 15000
            return {
                "summary_ja": "原因の説明を補う。",
                "priority_fixes_ja": ["判定の図"],
            }

    assert video_review.overall_review(r, p, Local())["summary_ja"]


def test_review_request_and_status_api(client):
    p = project()
    automatic = client.get(f"/api/video-projects/{p['id']}/reviews").json()
    assert automatic["state"] == "waiting"
    response = client.post(f"/api/video-projects/{p['id']}/reviews")
    assert response.status_code == 200
    report = client.get(f"/api/video-projects/{p['id']}/reviews").json()
    assert (
        report["id"] == automatic["id"]
    )  # explicit request reuses the automatic review
    assert report["state"] == "waiting"
    assert (
        client.get(f"/api/video-projects/{p['id']}").json()["review"]["id"]
        == report["id"]
    )
    assert client.post("/api/video-projects/missing/reviews").status_code == 404


def test_declared_math_that_never_appears_in_film_is_reported(database):
    p = project(["overview"])
    requested = video_review.request(p["id"])
    export = finish_film(p)
    scene = export["data"]["manifest"]["scenes"][0]
    scene["visual"].update(
        original_asset_id="original",
        type="equation",
        equations=[{"latex": "y=x", "en": "The same value", "ja": "同じ値"}],
        concepts=[{"template": "relationship"}],
    )
    scene["render_paths"] = [
        "visuals/original.png",
        "visuals/intuition.png",
        "visuals/symbols.png",
    ]
    # The compiled sequence still requests only the original, despite declaring math.
    info = video_review.prepare_mode({"id": requested["review_id"]}, "overview", export)
    assert info["equation_scenes"] == 1
    assert info["scenes"][0]["symbolic_frames_in_timeline"] == 0
    assert info["scenes"][0]["technical_issues_ja"]


def test_transition_pause_is_not_sampled_as_current_scene_content(
    database, monkeypatch
):
    p = project(["overview"])
    requested = video_review.request(p["id"])
    export = finish_film(p)
    monkeypatch.setattr(
        video_review.story_video,
        "timeline",
        lambda *_: (
            [
                {"frames": 24000, "scene": "previous.jpg", "silence": True},
                {"frames": 48000, "scene": "current.jpg"},
            ],
            [
                {"frames": 24000, "silence": True},
                {
                    "frames": 48000,
                    "audio": "audio/u-0.wav",
                    "english": "Current explanation",
                    "japanese": "今の説明",
                },
            ],
            [(0, "Current scene")],
            [],
        ),
    )
    info = video_review.prepare_mode({"id": requested["review_id"]}, "overview", export)
    assert all(at >= 1 for at in info["scenes"][0]["sample_times"])
