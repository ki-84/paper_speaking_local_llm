import json

import pytest
from paperspeak import db, lessons, papers, translation
from paperspeak.quality import QualityHold


def test_compact_parameter_counts_match_japanese_units():
    assert translation.numeric_values("A 175B model") == translation.numeric_values("1750億のモデル")
    assert translation.numeric_values("A 7M model") == translation.numeric_values("700万のモデル")
    assert translation.numeric_values("3 B matrices") == {3}


def chapter_fixture():
    pid = papers.register({"source_id": "translation", "version": "v1", "title": "Paper"})
    lid = lessons.create(pid, format_version="paper-radio-1")
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (lid,))
    lesson["data"]["glossary"] = [{"term": "rank", "meaning": "The number of independent parts."}]
    db.save_lesson(lesson)
    ident = "chapter"
    data = {
        "title": "One idea",
        "focus": "Learn what changed.",
        "turns": [{"id": f"t{i}", "text": f"The method uses {i} layers."} for i in range(9)],
        "questions": [{
            "id": "q1", "question": "What changed?", "hints": ["Think about layers.", "Look at the old model."],
            "sample_answer": "Only one layer changed."
        }],
    }
    db.execute("INSERT INTO chapters VALUES (?,?,?,?,?)", (ident, lid, 0, "ready", db.dumps(data)))
    return ident


def visual_translation_fixture():
    ident = chapter_fixture()
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (chapter["lesson_id"],))
    lesson["data"].update(format="paper-visual-2",glossary=[])
    english = "The value is 1.75 times 10 to the 11th."
    chapter["data"] = {"turns":[{"id":"t", "text":english}], "translation":{"items":{
        "turn:t":{"english":english,"japanese":"値は1.75乗10の11乗です。"}}}}
    db.save_lesson(lesson); db.save_chapter(chapter)
    return chapter, lesson, english


class MeaningError:
    def ask(self, prompt, **kwargs):
        assert prompt.startswith("Check that every Japanese")
        return {"passed":False,"issues":[{"id":"1","reason":"Multiplication was changed into exponentiation."}]}


def test_visual_translation_requires_meaning_review_and_resumes_corrections(database):
    chapter, lesson, english = visual_translation_fixture()
    assert not translation.complete(chapter,lesson)
    assert not translation.translate_batch(chapter,lesson,MeaningError())
    saved = db.one("SELECT * FROM chapters WHERE id=?",(chapter["id"],))
    assert "turn:t" not in saved["data"]["translation"]["items"]
    assert saved["data"]["translation"]["history"][0]["before"]["japanese"] == "値は1.75乗10の11乗です。"

    class Correct:
        def ask(self, prompt, **kwargs):
            if prompt.startswith("Translate"):
                assert "Multiplication was changed" in prompt
                return {"items":[{"id":"1","japanese":"値は1.75×10の11乗です。"}]}
            return {"passed":True,"issues":[]}

    assert not translation.translate_batch(saved,lesson,Correct())
    assert translation.translated(saved,"turn:t",english)
    assert not translation.verified(saved,"turn:t",english,lesson)
    from paperspeak.runtime import PracticePreempted
    class Interrupted:
        def ask(self, *args, **kwargs):
            raise PracticePreempted("Recording first")
    with pytest.raises(PracticePreempted):
        translation.translate_batch(saved,lesson,Interrupted())
    assert saved["data"]["translation"]["meaning_retries"]["turn:t"] == 1
    assert translation.translated(saved,"turn:t",english) == "値は1.75×10の11乗です。"
    assert translation.translate_batch(saved,lesson,Correct())
    saved["data"]["translation"]["items"]["turn:t"]["japanese"] = "値は1.75乗10の11乗です。"
    assert not translation.complete(saved,lesson)
    assert saved["data"]["turns"][0]["text"] == english


def test_visual_translation_holds_after_three_unresolved_meaning_checks(database):
    chapter, lesson, english = visual_translation_fixture()
    for attempt in range(3):
        chapter["data"]["translation"]["items"]["turn:t"] = {"english":english,"japanese":"値は1.75乗10の11乗です。"}
        if attempt < 2:
            assert not translation.translate_batch(chapter,lesson,MeaningError())
        else:
            with pytest.raises(QualityHold,match="Japanese meaning still needs attention"):
                translation.translate_batch(chapter,lesson,MeaningError())
    assert not translation.complete(chapter,lesson)
    assert len(chapter["data"]["translation"]["history"]) == 3


def test_japanese_translation_resumes_from_saved_batches(database, client):
    ident = chapter_fixture()
    response = client.post(f"/api/chapters/{ident}/translation")
    assert response.status_code == 200 and not response.json()["ready"]
    job = db.one("SELECT * FROM jobs WHERE id=?", (response.json()["job_id"],))

    class Translator:
        def ask(self, prompt, **kwargs):
            values = json.loads(prompt.split("ITEMS: ", 1)[1])
            assert kwargs["thinking"] is False
            assert [x["id"] for x in values] == [str(i) for i in range(1, len(values) + 1)]
            return {"items": [{"id": x["id"], "japanese": "日本語訳: " + x["english"]} for x in values]}

    runtime = Translator()
    assert not translation.translation_step(job, runtime)
    first = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    assert len(first["data"]["translation"]["items"]) == 8
    assert "turn:t0" in first["data"]["translation"]["items"]
    while not translation.translation_step(job, runtime):
        pass
    complete = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (complete["lesson_id"],))
    assert translation.complete(complete, lesson)
    assert client.post(f"/api/chapters/{ident}/translation").json()["ready"]
    complete["data"]["turns"][0]["text"] = "The method uses 99 layers."
    db.save_chapter(complete)
    assert not translation.complete(db.one("SELECT * FROM chapters WHERE id=?", (ident,)), lesson)


def test_new_chapter_is_not_published_before_japanese_is_complete(database):
    ident = chapter_fixture()
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (chapter["lesson_id"],))
    lesson["data"]["phase"] = "chapters"
    db.save_lesson(lesson)
    db.execute("UPDATE chapters SET state='translation' WHERE id=?", (ident,))
    jid = db.enqueue("lesson", lesson["id"])

    class Translator:
        def ask(self, prompt, **kwargs):
            values = json.loads(prompt.split("ITEMS: ", 1)[1])
            return {"items": [{"id": x["id"], "japanese": "訳です。" + x["english"]} for x in values]}

    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert not lessons.lesson_step(job, Translator())
    partial = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    assert partial["state"] == "translation"
    assert not translation.complete(partial, lesson)
    for _ in range(4):
        lessons.lesson_step(job, Translator())
        current = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
        if current["state"] == "ready":
            break
    assert current["state"] == "ready"
    assert translation.complete(current, lesson)


def test_existing_ready_chapters_are_queued_once_for_translation(database):
    ident = chapter_fixture()
    first = translation.schedule_backfill()
    second = translation.schedule_backfill()
    assert len(first) == len(second) == 1
    assert first[0] == second[0]
    assert db.one("SELECT kind,target,state FROM jobs WHERE id=?", (first[0],))["target"] == ident


def test_bad_translation_cannot_replace_english_or_saved_work(database):
    ident = chapter_fixture()
    job_id = db.enqueue("translate", ident)
    job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    class BadTranslator:
        def ask(self, prompt, **kwargs):
            values = json.loads(prompt.split("ITEMS: ", 1)[1])
            return {"items": [{"id": x["id"], "japanese": "訳です"} for x in values]}

    with pytest.raises(ValueError, match="number"):
        translation.translation_step(job, BadTranslator())
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    assert "translation" not in chapter["data"]
    assert chapter["data"]["turns"][0]["text"] == "The method uses 0 layers."


def test_japanese_number_next_to_kanji_is_not_missing():
    result = {"items": [{"id": "title", "japanese": "第1部です。"}]}
    assert translation.check_result(result, [("title", "Part 1")]) == {"title": "第1部です。"}
    with pytest.raises(ValueError, match="number"):
        translation.check_result(result, [("title", "Part 2")])


def test_japanese_large_number_unit_must_keep_the_value():
    english = "Take GPT-3, about 175 billion parameters."
    good = {"items": [{"id": "turn", "japanese": "GPT-3は約1750億個のパラメータを使います。"}]}
    assert translation.check_result(good, [("turn", english)])
    bad = {"items": [{"id": "turn", "japanese": "GPT-3は約175億個のパラメータを使います。"}]}
    with pytest.raises(ValueError, match="number"):
        translation.check_result(bad, [("turn", english)])


def test_japanese_compound_units_match_english_quantities():
    english = "Base has 125 million and large has 355 million parameters."
    good = {"items": [{"id": "turn", "japanese": "baseは1億2500万、largeは3億5,500万個のパラメータです。"}]}
    assert translation.check_result(good, [("turn", english)])
    bad = {"items": [{"id": "turn", "japanese": "baseは1億2500万、largeは3億5000万個のパラメータです。"}]}
    with pytest.raises(ValueError, match="number"):
        translation.check_result(bad, [("turn", english)])


def test_hyphenated_english_large_number_matches_japanese_unit():
    english = "That is huge for a 175-billion parameter model."
    good = {"items": [{"id": "turn", "japanese": "1750億パラメータのモデルには大きいです。"}]}
    assert translation.check_result(good, [("turn", english)])
    bad = {"items": [{"id": "turn", "japanese": "175億パラメータのモデルには大きいです。"}]}
    with pytest.raises(ValueError, match="number"):
        translation.check_result(bad, [("turn", english)])
