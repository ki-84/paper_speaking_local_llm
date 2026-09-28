import pymupdf as fitz
import pytest

from paperspeak import config, db, figure_extract, lessons, papers, translation, visual_render, visuals
from paperspeak.quality import QualityHold, dialogue_for_model, split_spoken_turns
from paperspeak.runtime import PracticePreempted


def spec(sid="source"):
    return {
        "kind": "teaching", "layout": "flow", "source_ids": [sid],
        "title_en": "What stays fixed", "title_ja": "固定するもの",
        "description_en": "The old weights stay fixed.", "description_ja": "元の重みを固定します。",
        "nodes": [{"id": "old", "en": "Old weights", "ja": "元の重み"},
                  {"id": "new", "en": "New task", "ja": "新しいタスク"}],
        "edges": [{"from": "old", "to": "new"}],
    }


def test_invalid_japanese_diagram_label_is_saved_for_local_model_repair(database):
    pid = papers.register({"source_id":"repair-plan", "version":"1", "title":"A paper"})
    lid, cid = db.uid(), db.uid()
    db.execute("INSERT INTO lessons VALUES (?,?,?,?,?,?)",
               (lid,pid,"building",db.dumps({"format":"paper-visual-2","model":"qwen-q8"}),1,1))
    db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)",
               (cid,lid,0,"visuals",db.dumps({"title":"The idea","focus":"Learn the idea","turns":[]})))
    evidence = [{"id":"source","data":{"text":"The old weights stay fixed."}}]
    valid = spec()
    invalid = valid | {"nodes":[valid["nodes"][0] | {"ja":"A"},valid["nodes"][1]]}

    class Runtime:
        last_generation = {}
        prompts = []

        def ask(self,prompt,**kwargs):
            self.prompts.append(prompt)
            return {"originals":[],"diagrams":[invalid if len(self.prompts)==1 else valid]}

    runtime = Runtime()
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (cid,))
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    assert visuals.prepare_step(chapter,lesson,evidence,runtime) is False
    retry = db.one("SELECT * FROM chapters WHERE id=?", (cid,))
    assert retry["data"]["visual_plan_attempts"] == 1
    assert retry["data"]["visual_candidate"]["diagrams"][0]["nodes"][0]["ja"] == "A"
    assert visuals.prepare_step(retry,lesson,evidence,runtime) is False
    repaired = db.one("SELECT * FROM chapters WHERE id=?", (cid,))
    assert "PREVIOUS CANDIDATE TO REPAIR" in runtime.prompts[1]
    assert repaired["data"]["visual_stage"] == "assets" and "visual_candidate" not in repaired["data"]


def test_visual_plan_uses_short_evidence_ids_and_saves_real_citations(database):
    pid = papers.register({"source_id": "short-citations", "version": "1", "title": "A paper"})
    lid, cid = db.uid(), db.uid()
    db.execute("INSERT INTO lessons VALUES (?,?,?,?,?,?)",
               (lid, pid, "building", db.dumps({"format": "paper-visual-2", "model": "qwen-q8"}), 1, 1))
    db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)",
               (cid, lid, 0, "visuals", db.dumps({"title": "One idea", "focus": "Learn the idea."})))
    sid = pid + ":H22"
    evidence = [{"id": sid, "data": {"text": "Adapter layers add sequential work."}}]

    class Runtime:
        last_generation = {}

        def ask(self, prompt, **kwargs):
            assert '"id": "S1"' in prompt
            assert '"S1"' in prompt.split("VALID DIAGRAM SOURCE IDS: ", 1)[1]
            return {"originals": [], "diagrams": [spec("S1")]}

    chapter = db.one("SELECT * FROM chapters WHERE id=?", (cid,))
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    visuals.plan(chapter, lesson, evidence, Runtime())
    saved = db.one("SELECT * FROM chapters WHERE id=?", (cid,))
    asset = db.one("SELECT * FROM visual_assets WHERE id=?", (saved["data"]["visuals"][0]["asset_id"],))
    assert asset["data"]["source_ids"] == [sid]


def test_diagram_length_error_names_the_field_to_repair():
    diagram = spec() | {"description_en": "x" * 141}
    with pytest.raises(ValueError, match="Diagram description_en/description_ja.*English length 141"):
        visual_render.validate_spec(diagram, ["source"])
    diagram = spec()
    diagram["nodes"][0]["en"] = "x" * 61
    with pytest.raises(ValueError, match="Diagram node old en/ja.*English length 61"):
        visual_render.validate_spec(diagram, ["source"])


def test_visual_number_word_matches_japanese_digit_without_ignoring_real_mismatch():
    visual_render.check_pair("LoRA trains two rank-r factors.", "LoRAはランクrの2因子を学習します。")
    with pytest.raises(ValueError, match="changed a number"):
        visual_render.check_pair("LoRA trains two rank-r factors.", "LoRAはランクrの3因子を学習します。")


def test_spoken_sentence_split_keeps_visual_cues_and_original_evidence():
    turn = {"id":"a", "speaker":"guide", "kind":"paper", "source_ids":["source"],
            "visual":{"key":"V1","focus":["old"]}, "text":"Look here. This value is 3.5 percent."}
    parts = split_spoken_turns([turn])
    assert [p["text"] for p in parts] == ["Look here.", "This value is 3.5 percent."]
    assert parts[0]["id"] == "a" and parts[1]["id"] is None
    assert all(p["visual"] == turn["visual"] and p["source_ids"] == turn["source_ids"] for p in parts)
    assert turn["text"] == "Look here. This value is 3.5 percent."
    for text in ["Dr. Smith reads the paper.", "The U.S. Results are reported separately."]:
        assert split_spoken_turns([turn | {"text":text}])[0]["text"] == text


def test_original_math_symbol_regions_accept_uppercase_and_reject_invalid_coordinates():
    region = {"id":"W", "label_en":"Fixed W", "label_ja":"固定した W", "box":[0.1,0.2,0.3,0.4]}
    assert visuals.valid_regions([region])[0]["id"] == "W"
    for invalid in [region | {"box":[float('nan'),0,0.2,0.2]}, region | {"box":[0.9,0,0.2,0.2]}, "not a region"]:
        with pytest.raises(ValueError):
            visuals.valid_regions([invalid])


def test_scientific_review_maps_original_images_to_citations_and_excludes_teaching_aids(monkeypatch):
    monkeypatch.setattr(visuals, "assets", lambda chapter: [
        {"kind":"teaching", "data":{"image_path":"teaching.png", "source_ids":["s1"]}},
        {"kind":"original", "data":{"image_path":"crop.png", "source_ids":["s1", "unselected"]}},
    ])
    evidence = [{"id":"s1", "data":{"image_path":"page.png"}},
                {"id":"s2", "data":{"image_path":"page.png"}}]
    assert lessons.review_images({}, evidence) == [
        {"path":"crop.png", "source_ids":["s1"]},
        {"path":"page.png", "source_ids":["s1", "s2"]},
    ]


def test_malformed_local_repair_returns_actionable_validation_instead_of_crashing():
    with pytest.raises(ValueError, match="must be an object"):
        lessons.apply_local_repairs({}, {"edits":["wrong shape"]}, set(), [])


def test_wording_repair_preserves_existing_visual_unless_explicitly_changed():
    turn = {"id":"t", "speaker":"guide", "text":"Look at our diagram.", "kind":"background", "source_ids":[],
            "visual":{"key":"V1", "focus":["old"]}}
    chapter = {"turns":[turn], "review":{"issues":[]}}
    replacement = {k:v for k,v in turn.items() if k not in {"id", "visual"}}
    replacement["text"] = "Our diagram shows the old weights."
    lessons.apply_local_repairs(chapter, {"edits":[{"turn_id":"t", "replacement":[replacement]}]}, {"t"}, [])
    assert chapter["turns"][0]["visual"] == turn["visual"]
    replacement["visual"] = None
    lessons.apply_local_repairs(chapter, {"edits":[{"turn_id":"t", "replacement":[replacement]}]}, {"t"}, [])
    assert chapter["turns"][0]["visual"] is None


def local_pdf(kind="vector"):
    pid = papers.register({"source_id": "pdf-fixture-"+kind, "version": "1", "title": "Local PDF"})
    folder = config.DATA / "papers" / pid
    folder.mkdir()
    path = folder / "paper.pdf"
    doc = fitz.open()
    page = doc.new_page()
    if kind == "vector":
        page.draw_rect(fitz.Rect(90, 180, 210, 270))
        page.draw_rect(fitz.Rect(290, 180, 410, 270))
        page.draw_line(fitz.Point(210, 230), fitz.Point(290, 230))
        page.insert_text((100, 220), "Inputs")
        page.insert_text((300, 220), "Outputs")
    elif kind in {"photo", "clipped-photo"}:
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), False)
        pix.clear_with(130)
        for x in (90, 290):
            rect = fitz.Rect(x, 180, x+120, 280)
            if kind == "clipped-photo" and x == 290:
                rect = fitz.Rect(290, 180, 610, 500)
            page.insert_image(rect, stream=pix.tobytes("png"))
    if kind != "no-caption":
        page.insert_text((90, 310), "Figure 1: Two panels showing the method.")
    doc.save(path)
    doc.close()
    paper = db.one("SELECT * FROM papers WHERE id=?", (pid,))
    paper["data"]["pdf_path"] = str(path.relative_to(config.DATA))
    db.execute("UPDATE papers SET data=?,state='ready' WHERE id=?", (db.dumps(paper["data"]), pid))
    return pid


@pytest.mark.parametrize("kind", ["vector", "photo", "clipped-photo", "no-caption"])
def test_pdf_only_extraction_is_local_preserves_panels_and_is_repeatable(database, monkeypatch, kind):
    monkeypatch.setattr(papers, "fetch", lambda *a, **k: pytest.fail("Unexpected network access"))
    pid = local_pdf(kind)
    first = figure_extract.extract(pid)
    second = figure_extract.extract(pid)
    assert [a["id"] for a in first] == [a["id"] for a in second]
    assert db.one("SELECT count(*) n FROM visual_assets")["n"] == 1
    data = first[0]["data"]
    assert config.safe_path(data["image_path"]).read_bytes().startswith(b"\x89PNG")
    assert data["source_ids"] and data["pdf_sha256"]
    if kind in {"no-caption", "clipped-photo"}:
        assert data["extraction"] == "page_fallback"
    else:
        assert data["extraction"] == "candidate"
        assert data["crop_box"][0] <= 90 and data["crop_box"][2] >= 410


def test_adjacent_caption_numbers_survive_merged_pdf_blocks_without_prose_duplicates(database):
    pid = local_pdf()
    paper = db.one("SELECT data FROM papers WHERE id=?", (pid,))
    path = config.safe_path(paper["data"]["pdf_path"])
    with fitz.open(path) as doc:
        page = doc.new_page()
        page.insert_text((60, 300), "Figure 2: Left samples. Figure 3: Right samples.")
        page.insert_text((60, 380), "Table 1 shows the results discussed in the text.")
        doc.saveIncr()
    found = figure_extract.extract(pid)
    assert [a["data"]["label"] for a in found] == ["Figure 1", "Figure 2", "Figure 3"]
    assert all(a["data"]["extraction"] == "page_fallback" for a in found[1:])
    assert found[1]["data"]["caption_en"] == "Figure 2: Left samples."
    assert found[2]["data"]["caption_en"] == "Figure 3: Right samples."


def chapter_fixture():
    pid = papers.register({"source_id": "visual-fixture", "version": "v1", "title": "Paper"})
    db.execute("INSERT INTO sources VALUES (?,?,?,?)", ("source", pid, "text", db.dumps({"text": "The old weights stay fixed."})))
    lid = lessons.create(pid)
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    lesson["data"].update(phase="chapters", notes=[])
    db.save_lesson(lesson)
    db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)", ("chapter", lid, 0, "visuals", db.dumps({
        "title": "What stays fixed", "focus": "Learn what stays fixed.", "turns": [], "questions": [],
        "claim_ids": [], "draft_parts": 0, "revision_round": 0,
    })))
    return db.one("SELECT * FROM chapters WHERE id='chapter'"), lesson


class VisualProvider:
    def ask(self, prompt, **kwargs):
        if prompt.startswith("Plan the visuals"):
            return {"originals": [], "diagrams": [spec()]}
        if prompt.startswith("Review a scientific teaching visual"):
            assert kwargs["images"] and kwargs["images"][0].is_file()
            return {"passed": True, "issues": [], "regions": []}
        raise AssertionError(prompt[:100])


def prepare(chapter, lesson):
    for _ in range(6):
        current = db.one("SELECT * FROM chapters WHERE id=?", (chapter["id"],))
        if current["state"] == "draft":
            return current
        visuals.prepare_step(current, lesson, db.all("SELECT * FROM sources"), VisualProvider())
    raise AssertionError("Visual preparation did not finish")


def test_visual_preparation_resumes_and_publishes_only_reviewed_images(database):
    chapter, lesson = chapter_fixture()
    current = prepare(chapter, lesson)
    assert db.one("SELECT count(*) n FROM visual_assets")["n"] == 1
    asset = visuals.assets(current)[0]
    assert asset["data"]["review"]["passed"]
    assert asset["data"]["regions"][0]["label_ja"] == "元の重み"
    assert not visuals.ready(current)
    assert config.safe_path(asset["data"]["image_path"]).is_file()
    assert db.one("SELECT count(*) n FROM sources")["n"] == 1


def test_multiline_bilingual_labels_render_without_overlap(database):
    diagram = spec()
    diagram["nodes"][0].update(en="Gradients and optimizer states for all weights", ja="全パラメータの勾配と最適化状態")
    visual_render.validate_spec(diagram, ["source"])
    rendered = visual_render.render(diagram)
    assert config.safe_path(rendered["image_path"]).stat().st_size > 1000
    arrow = next(r for r in rendered["regions"] if r["id"] == "arrow_1")
    assert len(arrow["points"]) == 2
    assert all(0 <= coordinate <= 1 for point in arrow["points"] for coordinate in point)


def test_missing_visual_explanation_is_added_once_before_review(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    chapter["state"] = "visual_draft"

    class Explain:
        def ask(self, prompt, **kwargs):
            assert kwargs["images"][0].is_file()
            return {"turns":[{"speaker":"guide", "text":"Look at our diagram. The old weights stay fixed. Look at the left box.",
                              "kind":"paper", "source_ids":["source"], "visual":{"key":"V1","focus":["old"]}}]}

    evidence = db.all("SELECT * FROM sources")
    visuals.draft_missing_step(chapter, lesson, evidence, Explain())
    chapter = db.one("SELECT * FROM chapters WHERE id='chapter'")
    assert len(chapter["data"]["turns"]) == 3
    visuals.draft_missing_step(chapter, lesson, evidence, Explain())
    saved = db.one("SELECT * FROM chapters WHERE id='chapter'")
    assert saved["state"] == "review" and len(saved["data"]["turns"]) == 3


def test_unknown_links_and_text_edits_invalidate_visual_review(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    turn = {"id": "t", "speaker": "guide", "kind": "paper", "text": "Look at the old weights in our diagram.",
            "source_ids": ["source"], "visual": {"key": "V1", "focus": ["old"]}}
    chapter["data"]["turns"] = [turn]
    assert not visuals.validate_links([turn], chapter, require_all=True)
    assert dialogue_for_model([turn])[0]["visual"] == turn["visual"]
    for invalid in [None, {"key": "V9", "focus": []}, {"key": "V1", "focus": ["missing"]}]:
        assert visuals.validate_links([turn | {"visual": invalid}], chapter)
    assert visuals.validate_links([turn | {"visual": None, "text": "Look at the upper left box."}], chapter)
    assert visuals.validate_links([turn | {"visual": None, "text": "LoRA stays near the top in both panels."}], chapter)
    chapter["data"]["visual_dialogue_review"] = {"passed": True, "digest": visuals.dialogue_digest(chapter)}
    assert visuals.ready(chapter)
    asset = visuals.assets(chapter)[0]
    old_description = asset["data"]["description_en"]
    asset["data"]["description_en"] = "A different explanation of the diagram."
    visuals.save_asset(asset)
    assert not visuals.ready(chapter)
    asset["data"]["description_en"] = old_description
    visuals.save_asset(asset)
    turn["text"] = "The new weights are on the left."
    assert not visuals.ready(chapter)


def test_a_numbered_paper_figure_cannot_point_to_a_different_picture(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    asset = visuals.assets(chapter)[0]
    asset["kind"] = "original"
    asset["data"]["label"] = "Figure 1"
    visuals.save_asset(asset)
    turn = {"text":"Look at Figure 1.", "visual":{"key":"V1","focus":[]}}
    assert not visuals.validate_links([turn], chapter)
    assert visuals.validate_links([turn | {"text":"Look at Figure 2."}], chapter)
    wrong = turn | {"visual":{"key":"wrong","focus":["old"]}}
    visuals.bind_numbered_refs([wrong], chapter)
    assert wrong["visual"] == {"key":"V1","focus":[]}
    assert not visuals.validate_links([wrong], chapter)


def test_contradictory_visual_review_neither_publishes_nor_spends_a_correction(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    chapter["state"] = "visual_dialogue_review"
    chapter["data"]["turns"] = [{"id":"t", "speaker":"guide", "kind":"paper", "source_ids":["source"],
                                  "text":"Our diagram keeps the old weights fixed.", "visual":{"key":"V1","focus":["old"]}}]
    db.save_chapter(chapter)

    class Contradictory:
        def ask(self, *args, **kwargs):
            return {"passed":False, "issues":[{"turn_id":"t", "reason":"This matches the diagram. No discrepancy.",
                                               "suggestion":"No change required."}]}

    with pytest.raises(ValueError, match="contradictory verdict"):
        visuals.dialogue_review_step(chapter, lesson, Contradictory())
    saved = db.one("SELECT * FROM chapters WHERE id='chapter'")
    assert saved["state"] == "visual_dialogue_review"
    assert saved["data"].get("visual_dialogue_attempts", 0) == 0
    assert not visuals.ready(saved)
    assert saved["data"]["visual_review_format_error"]["issues"]


def test_translation_completion_cannot_publish_unreviewed_visual_dialogue(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    chapter["state"] = "translation"
    chapter["data"]["turns"] = [{"id": "t", "speaker": "guide", "kind": "paper", "text": "The diagram shows the old weights.",
                                  "source_ids": ["source"], "visual": {"key": "V1", "focus": []}}]
    chapter["data"]["translation"] = {"items": {key: {"english": en, "japanese": "日本語です", "meaning_digest":translation.meaning_digest(en,"日本語です")} for key, en in translation.items_for(chapter, lesson)}}
    db.save_chapter(chapter)
    jid = db.enqueue("lesson", lesson["id"])
    lessons.lesson_step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), VisualProvider())
    current = db.one("SELECT * FROM chapters WHERE id=?", (chapter["id"],))
    assert current["state"] == "visual_dialogue_review"
    assert current["data"]["visual_after_review"] == "translation"


def test_questions_with_overstated_scope_are_held_before_audio(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter,lesson)
    chapter["state"] = "question_review"
    chapter["data"]["questions"] = [{"question":"How many matrices?", "sample_answer":"Only two in the whole model."}]
    jid = db.enqueue("lesson",lesson["id"])
    job = db.one("SELECT * FROM jobs WHERE id=?",(jid,))

    class Reviewer:
        def ask(self, prompt, **kwargs):
            assert prompt.startswith("Review these comprehension questions")
            assert "adapted weight matrix" in prompt
            return {"passed":False,"issues":["One pair is needed per adapted weight matrix, not for the whole model."]}

    for attempt in range(3):
        chapter["state"] = "question_review"
        db.save_chapter(chapter)
        if attempt < 2:
            assert not lessons.lesson_step(job,Reviewer())
        else:
            with pytest.raises(QualityHold,match="understanding questions"):
                lessons.lesson_step(job,Reviewer())
        chapter = db.one("SELECT * FROM chapters WHERE id=?",(chapter["id"],))
        assert chapter["state"] == "questions" and not lessons.questions_ready(chapter)


def test_visual_plan_and_asset_failures_stop_after_three_attempts(database):
    chapter, lesson = chapter_fixture()

    class InvalidPlan:
        def ask(self, *args, **kwargs):
            return {"originals": [{"id": "not-in-paper"}], "diagrams": []}

    for _ in range(2):
        visuals.prepare_step(chapter, lesson, [], InvalidPlan())
        chapter = db.one("SELECT * FROM chapters WHERE id=?", (chapter["id"],))
    with pytest.raises(QualityHold):
        visuals.prepare_step(chapter, lesson, [], InvalidPlan())
    assert db.one("SELECT state FROM chapters WHERE id=?", (chapter["id"],))["state"] == "visuals"


def test_rejected_optional_diagram_falls_back_to_checked_original(database):
    chapter, lesson = chapter_fixture()
    chapter["data"].update(visual_stage="assets", visuals=[
        {"key": "V1", "asset_id": "original"}, {"key": "V2", "asset_id": "bad-diagram"},
    ])
    db.save_chapter(chapter)
    for ident, kind, review, attempts in [
        ("original", "original", {"passed": True}, 1),
        ("bad-diagram", "teaching", {"passed": False, "issues": ["Unsupported claim"]}, 3),
    ]:
        db.execute("INSERT INTO visual_assets VALUES (?,?,?,?,?,?)",
                   (ident, lesson["paper_id"], lesson["id"], kind,
                    db.dumps({"review": review, "review_attempts": attempts}), 1))
    assert not visuals.prepare_step(chapter, lesson, [], object())
    saved = db.one("SELECT * FROM chapters WHERE id=?", (chapter["id"],))
    assert [v["key"] for v in saved["data"]["visuals"]] == ["V1"]
    assert saved["data"]["visual_omissions"][0]["key"] == "V2"
    assert db.one("SELECT * FROM visual_assets WHERE id='bad-diagram'")["data"]["review"]["passed"] is False
    assert not visuals.prepare_step(saved, lesson, [], object())
    assert db.one("SELECT state FROM chapters WHERE id=?", (chapter["id"],))["state"] == "draft"


def test_interrupted_repair_reuses_checkpoint_without_consuming_review_attempt(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    asset = visuals.assets(chapter)[0]
    asset["data"].update(review_attempts=1, review={"passed": False, "issues": ["Fix a label"], "version": 1})
    visuals.save_asset(asset)

    class Interrupted:
        def ask(self, prompt, **kwargs):
            assert prompt.startswith("Repair this teaching diagram")
            raise PracticePreempted("Recording first")

    with pytest.raises(PracticePreempted):
        visuals.review_asset(asset, lesson, Interrupted())
    saved = db.one("SELECT * FROM visual_assets WHERE id=?", (asset["id"],))
    assert saved["data"]["review_attempts"] == 1 and saved["data"]["version"] == 1


def test_rejected_diagram_is_held_after_three_reviews(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    asset = visuals.assets(chapter)[0]
    asset["data"].update(review=None, review_attempts=0)
    visuals.save_asset(asset)

    class Rejecting:
        def ask(self, prompt, **kwargs):
            if prompt.startswith("Repair this teaching diagram"):
                return spec()
            return {"passed": False, "issues": ["Unsupported relationship"], "regions": []}

    for _ in range(4):
        asset = db.one("SELECT * FROM visual_assets WHERE id=?", (asset["id"],))
        visuals.review_asset(asset, lesson, Rejecting())
    asset = db.one("SELECT * FROM visual_assets WHERE id=?", (asset["id"],))
    with pytest.raises(QualityHold, match="unpublished"):
        visuals.review_asset(asset, lesson, Rejecting())
    saved = db.one("SELECT * FROM visual_assets WHERE id=?", (asset["id"],))
    assert saved["data"]["review_attempts"] == 3
    assert len(saved["data"]["history"]) == 2


def test_model_cannot_choose_arbitrary_image_files(database):
    chapter, lesson = chapter_fixture()

    class InjectedFields:
        def ask(self, *args, **kwargs):
            return {"originals": [], "diagrams": [spec() | {"image_path": "models/private.txt", "review": {"passed": True}}]}

    visuals.plan(chapter, lesson, db.all("SELECT * FROM sources"), InjectedFields())
    asset = visuals.assets(chapter)[0]
    assert "image_path" not in asset["data"]
    assert asset["data"]["review"] is None


def test_diagram_content_is_data_and_translation_keeps_quantities():
    diagram = spec()
    visual_render.validate_spec(diagram, ["source"])
    diagram["nodes"][0]["en"] = "<script>alert(1)</script>"
    svg, _ = visual_render.svg_for(diagram)
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    diagram = spec()
    diagram["nodes"][0].update(en="2 weights", ja="3個の重み")
    with pytest.raises(ValueError, match="number"):
        visual_render.validate_spec(diagram, ["source"])
    diagram = spec()
    diagram["edges"][0]["to"] = "unknown"
    with pytest.raises(ValueError, match="unknown"):
        visual_render.validate_spec(diagram, ["source"])


def test_new_format_does_not_resume_paused_legacy_lesson(client):
    pid = papers.register({"source_id": "legacy", "version": "v1", "title": "Paper"})
    old = lessons.create(pid, format_version="paper-radio-1")
    jid = db.enqueue("lesson", old)
    db.patch_job(jid, state="paused")
    response = client.post(f"/api/papers/{pid}/lessons")
    assert response.status_code == 200
    new = response.json()["lesson_id"]
    assert new != old and lessons.create(pid) == new
    assert db.one("SELECT state FROM jobs WHERE id=?", (jid,))["state"] == "paused"


def test_visual_api_and_pin_are_scoped_to_the_current_chapter(client):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    response = client.get(f"/api/lessons/{lesson['id']}").json()
    assert len(response["visuals"]) == 1
    path = response["visuals"][0]["data"]["image_path"]
    assert client.get("/api/files/"+path).content.startswith(b"\x89PNG")
    progress = {"chapter_id": chapter["id"], "turn_index": 0, "visual_mode": "pinned", "visual_key": "V1"}
    assert client.put(f"/api/lessons/{lesson['id']}/progress", json=progress).status_code == 200
    restored = client.get(f"/api/lessons/{lesson['id']}").json()["progress"]
    assert restored["visual_key"] == "V1"
    progress["visual_key"] = "other-chapter-key"
    assert client.put(f"/api/lessons/{lesson['id']}/progress", json=progress).status_code == 422


def test_changed_image_cannot_pass_final_publication_check(database):
    chapter, lesson = chapter_fixture()
    chapter = prepare(chapter, lesson)
    chapter["data"]["turns"] = [{"id": "t", "text": "Our diagram shows weights.", "visual": {"key": "V1", "focus": []}}]
    chapter["data"]["visual_dialogue_review"] = {"passed": True, "digest": visuals.dialogue_digest(chapter)}
    path = config.safe_path(visuals.assets(chapter)[0]["data"]["image_path"])
    path.write_bytes(b"different image")
    with pytest.raises(QualityHold, match="changed"):
        visuals.ready(chapter)
