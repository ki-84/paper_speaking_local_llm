import time
import wave

import pytest
from paperspeak import config, db, local_network, papers, story, story_video, video
from paperspeak.runtime import PracticePreempted


def paper():
    return papers.register(
        {
            "source_id": "story-paper",
            "version": "v1",
            "title": "A Small Update",
            "url": "https://arxiv.org/abs/2106.09685",
        }
    )


def wav(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b"\0\0" * round(seconds * 24000))


def test_project_creation_is_atomic_idempotent_and_preserves_old_lessons(client):
    pid = paper()
    old = db.uid()
    db.execute(
        "INSERT INTO lessons VALUES (?,?,?,?,?,?)",
        (
            old,
            pid,
            "ready",
            db.dumps({"format": "paper-visual-2"}),
            time.time(),
            time.time(),
        ),
    )
    first = client.post(f"/api/papers/{pid}/video-projects").json()
    second = client.post(f"/api/papers/{pid}/video-projects").json()
    assert first == second
    project = client.get(f"/api/video-projects/{first['project_id']}").json()
    assert set(project["data"]["modes"]) == {"overview", "deep_dive"}
    assert (
        len(
            db.all(
                "SELECT * FROM lessons WHERE json_extract(data,'$.format')=?",
                (story.FORMAT,),
            )
        )
        == 2
    )
    assert db.one("SELECT state FROM lessons WHERE id=?", (old,))["state"] == "ready"
    assert client.post("/api/papers/missing/video-projects").status_code == 404
    assert client.get("/api/video-projects/missing").status_code == 404


def test_project_pause_and_resume_propagates_to_render_jobs(client):
    result = story.create(paper())
    project = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    lid = project["data"]["modes"]["overview"]["lesson_id"]
    eid = db.uid()
    db.execute(
        "INSERT INTO video_exports VALUES (?,?,?,?,?,?,?,?,?)",
        (eid, lid, "", "overview", "digest", "queued", "{}", time.time(), time.time()),
    )
    child = db.enqueue("story_video", eid)
    client.post(f"/api/jobs/{result['job_id']}/pause").raise_for_status()
    assert db.one("SELECT state FROM jobs WHERE id=?", (child,))["state"] == "paused"
    client.post(f"/api/jobs/{result['job_id']}/resume").raise_for_status()
    assert db.one("SELECT state FROM jobs WHERE id=?", (child,))["state"] == "queued"


def test_natural_c1_sentences_are_accepted_but_overview_math_and_unknown_citations_are_rejected():
    u = {
        "speaker": "guide",
        "text": "What makes this approach surprisingly useful is that, although the model still has all its original weights, we only train a small addition whose effects can later be folded into those weights.",
        "kind": "paper",
        "source_ids": ["s1"],
    }
    assert len(u["text"].split()) > 28
    assert story.validate_script({"utterances": [u]}, "overview", {"s1"}) == [u]
    with pytest.raises(ValueError):
        story.validate_script(
            {"utterances": [u | {"source_ids": ["unknown"]}]}, "overview", {"s1"}
        )
    with pytest.raises(ValueError):
        story.validate_script(
            {"utterances": [u | {"text": "The update is ΔW = BA."}]}, "overview", {"s1"}
        )
    assert story.validate_script(
        {"utterances": [u | {"text": "The update is ΔW = BA."}]}, "deep_dive", {"s1"}
    )


def test_repairs_are_bounded_and_practice_interruptions_do_not_consume_attempts(
    database,
):
    result = story.create(paper())
    p = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))

    class Bad:
        def ask(self, *args, **kw):
            return {"bad": True}

    def fail(_):
        raise ValueError("bad shape")

    for _ in range(3):
        assert (
            story.bounded(p, Bad(), "unit", "prompt", fail, lambda _: "fallback")
            is None
        )
    assert (
        story.bounded(p, Bad(), "unit", "prompt", fail, lambda _: "fallback")
        == "fallback"
    )
    assert p["data"]["repairs"]["unit"]["attempts"] == 3

    class Interrupted:
        def ask(self, *args, **kw):
            raise PracticePreempted()

    with pytest.raises(PracticePreempted):
        story.bounded(p, Interrupted(), "interrupt", "prompt", fail, lambda _: None)
    assert p["data"]["repairs"]["interrupt"]["attempts"] == 0


def test_sentence_clips_preserve_complete_original_wav_and_times(database):
    source = database / "audio/full.wav"
    wav(source, 4)
    stamps = [
        {"word": "One", "start": 0.1, "end": 0.4},
        {"word": "idea", "start": 0.5, "end": 0.8},
        {"word": "Another", "start": 2, "end": 2.4},
        {"word": "idea", "start": 2.5, "end": 2.9},
    ]
    ranges = story.sentence_ranges("One idea. Another idea.", stamps, 4)
    assert [(start, end) for _, start, end in ranges] == [(0, 2), (2, 4)]
    for i, (_, start, end) in enumerate(ranges):
        story.clip_audio(source, database / f"audio/{i}.wav", start, end)
    assert sum(
        video._duration_frames(database / f"audio/{i}.wav") for i in range(2)
    ) == video._duration_frames(source)


def scene_data(database, equation=False):
    audio = database / "audio/u.wav"
    wav(audio, 2)
    spec = {
        "type": "equation" if equation else "flow",
        "nodes": [
            {"en": "Frozen weights", "ja": "固定された重み"},
            {"en": "Small update", "ja": "小さな更新"},
        ],
        "caption_en": "Change only the addition.",
        "caption_ja": "追加部分だけを変更します。",
    }
    if equation:
        spec["equations"] = [
            {
                "latex": r"h=W_0x+BAx",
                "en": "Two outputs are added",
                "ja": "二つの出力を足します",
            }
        ]
    return {
        "title": "A smaller change",
        "title_ja": "小さな変更",
        "focus": "Understand the update",
        "claim_ids": [],
        "visual": spec,
        "reviews": {},
        "utterances": [
            {
                "id": "u",
                "speaker": "guide",
                "text": "A small update.",
                "kind": "background",
                "source_ids": [],
                "audio": "audio/u.wav",
                "duration": 2,
                "aligned": True,
                "sentence_ranges": [["A small update.", 0, 2]],
                "audio_check": {"timestamps": []},
                "tts_settings": {},
            }
        ],
        "subtitle_items": {"0:0": {"japanese": "小さな更新です。"}},
    }


def test_local_equations_bilingual_visuals_and_mp4_with_burned_captions(database):
    result = story.create(paper())
    p = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    t = p["data"]["modes"]["deep_dive"]
    t["scenes"] = [scene_data(database, True)]
    t["packaging"] = {
        "title": "小さな更新｜詳解・英語学習",
        "title_en": "A smaller change",
        "thumbnail_text": "小さな更新",
        "description": "Bilingual scientific English.",
    }
    story_video.render_scene(p, "deep_dive", 0)
    eid = story_video.enqueue(p, "deep_dive")
    job = db.one("SELECT * FROM jobs WHERE kind='story_video' AND target=?", (eid,))

    class CPU:
        def practice_waiting(self):
            return False

    with local_network.inference_only():
        while not story_video.step(job, CPU()):
            job = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    export = db.one("SELECT * FROM video_exports WHERE id=?", (eid,))
    assert export["state"] == "ready"
    assert config.safe_path(export["data"]["mp4"]).stat().st_size > 10000
    assert abs(export["data"]["media_duration"] - 2) < 0.2
    assert export["data"]["acceptance"]["equation_scenes"] == 1
    assert "小さな更新です。" in config.safe_path(export["data"]["ja_srt"]).read_text()
    assert config.safe_path(export["data"]["thumbnail"]).is_file()


def test_paragraph_timeline_has_no_sentence_gaps_and_variable_speaker_pauses(database):
    s = scene_data(database)
    s["render_paths"] = ["scene.png"]
    second = dict(s["utterances"][0], id="v", speaker="host")
    s["utterances"].append(second)
    s["subtitle_items"]["1:0"] = {"japanese": "小さな更新です。"}
    speech, captions, _, _ = story_video.timeline({"scenes": [s]}, database / "jobs")
    pauses = [t for t in speech if t.get("silence")]
    assert len(pauses) == 1 and pauses[0]["frames"] == 14400
    assert sum(t["frames"] for t in captions) == sum(t["frames"] for t in speech)
    s["utterances"][1]["speaker"] = "guide"
    speech, _, _, _ = story_video.timeline({"scenes": [s]}, database / "jobs")
    assert [t["frames"] for t in speech if t.get("silence")] == [7200]


def test_network_guard_allows_loopback_and_blocks_external_hosts():
    import socket

    original = socket.socket.connect
    with local_network.inference_only():
        assert socket.getaddrinfo("127.0.0.1", 8191)
        with pytest.raises(OSError):
            socket.getaddrinfo("huggingface.co", 443)
        with socket.socket() as s:
            with pytest.raises(OSError):
                s.connect(("8.8.8.8", 443))
    assert socket.socket.connect is original


def test_long_native_sentence_subtitles_keep_every_word_and_use_alignment_despite_asr_insertion():
    text = (
        "Although "
        + " ".join(["careful"] * 40)
        + ", the model retains its original weights and the learned correction adds a new direction without promising universal performance."
    )
    chunks = story.caption_units(text)
    assert " ".join(chunks) == text
    assert max(len(c.split()) for c in chunks) <= 38
    stamps = [
        {"word": "Unexpected", "start": 0, "end": 0.2},
        {"word": "One", "start": 0.3, "end": 0.5},
        {"word": "idea", "start": 0.5, "end": 0.9},
        {"word": "Another", "start": 2, "end": 2.3},
        {"word": "idea", "start": 2.3, "end": 3},
    ]
    assert story.sentence_ranges("One idea. Another idea.", stamps, 4)[1][1] == 2
    bad = [
        {"word": "One", "start": 99, "end": 100},
        {"word": "Another", "start": 100, "end": 101},
    ]
    ranges = story.sentence_ranges("One idea. Another idea.", bad, 4)
    assert [r[0] for r in ranges] == ["One idea.", "Another idea."]
    assert [(r[1], r[2]) for r in ranges] == [(0, 2), (2, 4)]


def test_speech_check_reads_the_real_recognizer_contract_and_varies_retry_seed(
    database,
):
    result = story.create(paper())
    p = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    t = p["data"]["modes"]["overview"]
    s = scene_data(database)
    t["scenes"] = [s]
    u = s["utterances"][0]
    u.pop("aligned")

    class Recognizer:
        def speech(self, *args):
            return {
                "text": "A small update.",
                "timestamps": [
                    {"word": "A", "start": 0.1, "end": 0.2},
                    {"word": "small", "start": 0.3, "end": 0.5},
                    {"word": "update", "start": 0.6, "end": 1.1},
                ],
            }

    story._align_step(p, Recognizer(), "overview")
    assert u["aligned"] and u["audio_check"]["wer"] == 0
    u.pop("aligned")
    u["text"] = "The result improves by 3 percent."

    class WrongNumber:
        def speech(self, *args):
            return {"text": "The result improves by 4 percent.", "timestamps": []}

    story._align_step(p, WrongNumber(), "overview")
    assert u["audio_retries"] == 1 and "audio" not in u and t["phase"] == "tts"
    requests = []

    class Synthesizer:
        def speech(self, op, r):
            from pathlib import Path

            requests.append(r)
            wav(Path(r["output"]), 2)
            return {"duration": 2, "generation_settings": {"seed": r["seed"]}}

    story._tts_step(p, Synthesizer(), "overview")
    first = u["audio"]
    u.pop("audio")
    u["audio_retries"] = 2
    story._tts_step(p, Synthesizer(), "overview")
    assert u["audio"] != first and requests[0]["seed"] != requests[1]["seed"]


def test_duration_expansion_keeps_payoff_last_and_preserves_learning_ids(database):
    result = story.create(paper())
    p = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    t = p["data"]["modes"]["overview"]
    a = scene_data(database)
    b = scene_data(database)
    b["title"] = "The payoff"
    b["title_ja"] = "結論"
    for s in [a, b]:
        s.update(subtitles_ready=True, clips_ready=True, word_budget=400)
    t.update(scenes=[a, b], preview_id="existing")
    previous = story.materialize_scene(p, "overview", 1)["id"]
    story._align_step(p, None, "overview")
    assert t["scenes"][-1]["title"] == "The payoff" and len(t["scenes"]) == 3
    assert story.materialize_scene(p, "overview", 2)["id"] == previous
    assert (
        db.one("SELECT ordinal FROM chapters WHERE id=?", (previous,))["ordinal"] == 2
    )
    assert story.materialize_scene(p, "overview", 1)["id"] != previous


def test_small_pitch_preserving_pace_adjustment_keeps_original_audio_and_scales_cues(
    database,
):
    from paperspeak import video_overlay

    r = scene_data(database)["utterances"][0]
    original = r["audio"]
    r["caption_ranges"] = r["sentence_ranges"].copy()
    r["audio_check"]["timestamps"] = [{"word": "update", "start": 0.5, "end": 1.5}]

    class CPU:
        def practice_waiting(self):
            return False

    story.pace_audio(r, 1.05, CPU())
    assert r["audio"] != original and config.safe_path(original).is_file()
    assert 1.8 < r["duration"] < 2
    assert r["sentence_ranges"][-1][2] == r["duration"]
    assert r["audio_check"]["timestamps"][0]["end"] < 1.5
    assert r["tts_settings"]["postprocess"]["pitch"] == "preserved"
    assert video_overlay.mouth_states(
        config.safe_path(r["audio"]),
        video._duration_frames(config.safe_path(r["audio"])),
    )


def test_editor_corrections_use_stable_ids_instead_of_ambiguous_numbering(
    database, monkeypatch
):
    p = {
        "data": {
            "model": "qwen-q8",
            "modes": {"overview": {"scenes": []}},
            "records": [],
        }
    }
    s = {
        "title": "Problem",
        "focus": "Understand the cost",
        "visual": {},
        "utterances": [
            {
                "id": "first",
                "speaker": "host",
                "text": "What is the cost?",
                "kind": "question",
                "source_ids": [],
            },
            {
                "id": "second",
                "speaker": "guide",
                "text": "It requires a saved model for each task.",
                "kind": "paper",
                "source_ids": ["source"],
            },
        ],
    }
    monkeypatch.setattr(story, "context_for", lambda *_: {})
    monkeypatch.setattr(story, "source_lookup", lambda *_: {"source": {}})

    class Editor:
        def ask(self, prompt, **kwargs):
            assert "exact utterance ID" in prompt
            return {
                "issues": [
                    {
                        "utterance_id": "second",
                        "reason": "Clarify the baseline",
                        "replacement": "Full fine-tuning saves a separate model for each task.",
                        "source_ids": ["source"],
                    }
                ],
                "notes": "One correction.",
            }

    story._review_scene(p, Editor(), "overview", s, 0, "content")
    assert s["utterances"][0]["text"] == "What is the cost?"
    assert s["utterances"][1]["text"].startswith("Full fine-tuning")


def test_expression_fallback_uses_exact_spoken_patterns_and_saved_japanese():
    scene = {
        "utterances": [
            {
                "id": "u",
                "text": "That sounds convenient, but what is the catch?",
                "caption_ranges": [
                    ["That sounds convenient, but what is the catch?", 0, 2]
                ],
            }
        ],
        "subtitle_items": {"0:0": {"japanese": "便利そうだけど、落とし穴は何？"}},
    }
    rows = story.fallback_expressions({"scenes": [scene]}, None)
    assert rows[0]["phrase"] == scene["utterances"][0]["text"]
    assert rows[0]["meaning_ja"] == scene["subtitle_items"]["0:0"]["japanese"]


def test_resumable_video_keeps_audio_and_subtitle_timing_across_segment_boundary(
    database,
):
    result = story.create(paper())
    p = db.one("SELECT * FROM video_projects WHERE id=?", (result["project_id"],))
    t = p["data"]["modes"]["overview"]
    s = scene_data(database)
    wav(database / "audio/u.wav", 62)
    s["utterances"][0].update(
        duration=62,
        sentence_ranges=[["First cue.", 0, 60], ["The final cue is here.", 60, 62]],
    )
    s["subtitle_items"] = {
        "0:0": {"japanese": "最初の字幕です。"},
        "0:1": {"japanese": "最後の字幕です。"},
    }
    t["scenes"] = [s]
    t["packaging"] = {
        "title": "Segment boundary test",
        "title_en": "Boundary test",
        "thumbnail_text": "時間を確認",
        "description": "Timing test",
    }
    story_video.render_scene(p, "overview", 0)
    eid = story_video.enqueue(p, "overview")
    job = db.one("SELECT * FROM jobs WHERE kind='story_video' AND target=?", (eid,))

    class CPU:
        def practice_waiting(self):
            return False

    with local_network.inference_only():
        assert story_video.step(job, CPU()) is False
        assert story_video.step(job, CPU()) is False
        assert story_video.step(job, CPU()) is True
    export = db.one("SELECT * FROM video_exports WHERE id=?", (eid,))
    assert abs(export["data"]["media_duration"] - 62) < 0.3
    assert (
        "00:01:00,000 --> 00:01:02,000"
        in config.safe_path(export["data"]["en_srt"]).read_text()
    )
    # Both parts exist; restarting the renderer reuses these instead of starting over.
    work = database / "jobs" / ("story-video-" + eid)
    assert (work / "part-000.mp4").is_file() and (work / "part-001.mp4").is_file()


def test_original_figure_does_not_hide_math_or_become_a_recursive_source(database):
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    t = p["data"]["modes"]["deep_dive"]
    s = scene_data(database, True)
    t["scenes"] = [s]
    story_video.render_scene(p, "deep_dive", 0)
    original_image = db.one(
        "SELECT data FROM visual_assets WHERE id=?", (s["asset_id"],)
    )["data"]["image_path"]
    db.execute(
        "INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
        (
            "source-figure",
            p["paper_id"],
            None,
            "original",
            db.dumps(
                {
                    "image_path": original_image,
                    "review": {"passed": True},
                    "label": "Figure 1",
                }
            ),
            time.time(),
        ),
    )
    s["visual"].update(type="original", original_asset_id="source-figure")
    s["utterances"][0].update(
        text="This equation multiplies matrix A and matrix B.", visual_focus=0
    )
    story_video.render_scene(p, "deep_dive", 0)
    assert s["utterances"][0]["visual_focus"] == 1
    rows = [
        db.one("SELECT * FROM visual_assets WHERE id=?", (a["asset_id"],))
        for a in s["focus_assets"]
    ]
    assert [a["kind"] for a in rows] == ["original", "teaching"]
    assert rows[0]["data"]["sha256"] != rows[1]["data"]["sha256"]
    assert [a["asset_id"] for a in story.original_catalogue(p)] == ["source-figure"]
    source = db.one("SELECT * FROM visual_assets WHERE id=?", ("source-figure",))
    source["data"]["regions"] = [
        {
            "id": "p3",
            "label_en": "Verified zoom panel",
            "label_ja": "確認済みの拡大部分",
            "box": [0.1, 0.1, 0.4, 0.6],
        }
    ]
    db.execute(
        "UPDATE visual_assets SET data=? WHERE id=?",
        (db.dumps(source["data"]), source["id"]),
    )
    s["utterances"][0]["text"] = "Look at this heat map."
    story_video.render_scene(p, "deep_dive", 0)
    focus = s["utterances"][0]["visual_focus"]
    assert focus == s["visual"]["zoom_start"]
    zoom = db.one(
        "SELECT * FROM visual_assets WHERE id=?",
        (s["focus_assets"][focus]["asset_id"],),
    )
    assert zoom["kind"] == "original"
    assert zoom["data"]["source_original_asset_id"] == "source-figure"


def test_source_shape_correction_invalidates_stale_audio_without_resetting_repairs(
    database, monkeypatch
):
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    p["data"]["paper_title"] = "LoRA"
    monkeypatch.setattr(
        story,
        "source_lookup",
        lambda _: {"primary": {"data": {"text": "W0 ∈ R d×k; B ∈ R d×r; A ∈ R r×k"}}},
    )
    s = scene_data(database)
    s["utterances"][0]["text"] = (
        "The layer has d inputs and k outputs. Delta W isn’t a full-sized block."
    )
    s["reviews"] = {"content": {"attempts": 3}}
    story.normalize_lora_conventions(p, s)
    assert "k inputs and d outputs" in s["utterances"][0]["text"]
    assert "same full shape" in s["utterances"][0]["text"]
    assert "audio" not in s["utterances"][0]
    assert s["utterances"][0]["audio_history"][0]["audio"] == "audio/u.wav"
    assert s["reviews"]["content"]["attempts"] == 3
    story.normalize_lora_conventions(p, s)
    assert len(s["independent_checks"]) == 1
    story.ensure_lora_math_visual(p, s)
    assert s["visual"]["equations"]


def test_worked_example_is_bounded_explicit_and_arithmetically_correct(
    database, monkeypatch
):
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    monkeypatch.setattr(story, "lora_anchor", lambda *_: "primary")
    t = p["data"]["modes"]["deep_dive"]
    s = scene_data(database)
    s["utterances"][0]["text"] = "Add the output coordinate-wise."
    t["scenes"] = [s]
    story.ensure_lora_worked_example(p, t)
    example = s["visual"]["worked_example"]
    ax = sum(a * x for a, x in zip(example["A"][0], example["x"]))
    actual = [
        sum(w * x for w, x in zip(row, example["x"])) + example["scale"] * b[0] * ax
        for row, b in zip(example["W0"], example["B"])
    ]
    assert actual == example["output"] == [8, 15]
    assert all(u["kind"] == "example" for u in s["utterances"][1:])
    assert "hypothetical example" in s["utterances"][2]["text"]
    story.ensure_lora_worked_example(p, t)
    assert len(s["utterances"]) == 5
    story.validate_visual(s["visual"], "deep_dive")


def test_new_paper_original_review_exhaustion_omits_crop_and_continues(
    database, monkeypatch
):
    from paperspeak import visuals

    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        ("primary", p["paper_id"], "text", db.dumps({"text": "Source excerpt"})),
    )
    db.execute(
        "INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
        (
            "crop",
            p["paper_id"],
            None,
            "original",
            db.dumps({"label": "Figure 1"}),
            time.time(),
        ),
    )
    p["data"]["figure_candidates"] = ["crop"]

    def fail(*_):
        raise ValueError("Uncertain crop")

    monkeypatch.setattr(visuals, "review_asset", fail)
    for _ in range(4):
        story._sources_step(p, object())
    assert p["data"]["figure_checks"]["crop"]["attempts"] == 3
    assert p["data"]["figure_index"] == 1
    assert "Omit this crop" in p["data"]["warnings"][-1]["action"]


def test_fallback_titles_are_distinct_and_generic_plan_does_not_invent_lora(database):
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    p["data"]["paper_title"] = "LoRA"
    for mode in story.MODES:
        hooks = story.fallback_hooks(p, mode)
        assert len({h["title_en"] for h in hooks}) == 3
        assert all("英語学習" in h["title_ja"] for h in hooks)
    p["data"]["paper_title"] = "Diffusion"
    p["data"]["evidence"] = [
        {"id": "C1", "topic": "mechanism", "claim": "Forward and reverse process"}
    ]
    plan = story._fallback_plan(p, "deep_dive")
    assert not any(
        "Matrices" in s["title"] or "rank" in s["title"] for s in plan["scenes"]
    )


def test_exact_number_reading_does_not_waste_voice_retries(database):
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    t = p["data"]["modes"]["deep_dive"]
    s = scene_data(database)
    u = s["utterances"][0]
    u.update(text="The matrix is 100 by 4.", aligned=False)
    t["scenes"] = [s]
    t["phase"] = "align"

    class Recognizer:
        def speech(self, *_, **__):
            return {"text": "The matrix is one hundred by four.", "timestamps": []}

    story._align_step(p, Recognizer(), "deep_dive")
    assert u["aligned"] and u["audio"] == "audio/u.wav"
    assert u["audio_check"]["wer"] == 0
    assert not u.get("audio_retries") and t["phase"] == "align"


def test_all_disputed_claims_are_omitted_after_repair_budget(database, monkeypatch):
    p = db.one(
        "SELECT * FROM video_projects WHERE id=?",
        (story.create(paper())["project_id"],),
    )
    s = scene_data(database)
    old = s["utterances"][0]["text"]
    s["reviews"] = {
        "content": {"attempts": 3, "history": [{"issues": [{"utterance_id": "u"}]}]}
    }
    monkeypatch.setattr(story, "context_for", lambda *_: {})
    assert story._review_scene(p, object(), "overview", s, 0, "content")
    assert old not in [u["text"] for u in s["utterances"]]
    assert s["omissions"][0]["text"] == old
    assert s["reviews"]["content"]["status"] == "best_effort"
    story.validate_script({"utterances": s["utterances"]}, "overview", set())
