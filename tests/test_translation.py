import json

import pytest
from paperspeak import db, lessons, papers, translation


def chapter_fixture():
    pid = papers.register({"source_id": "translation", "version": "v1", "title": "Paper"})
    lid = lessons.create(pid)
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


def test_japanese_translation_resumes_from_saved_batches(database, client):
    ident = chapter_fixture()
    response = client.post(f"/api/chapters/{ident}/translation")
    assert response.status_code == 200 and not response.json()["ready"]
    job = db.one("SELECT * FROM jobs WHERE id=?", (response.json()["job_id"],))

    class Translator:
        def ask(self, prompt, **kwargs):
            values = json.loads(prompt.split("ITEMS: ", 1)[1])
            assert kwargs["thinking"] is False
            return {"items": [{"id": x["id"], "japanese": "日本語訳: " + x["english"]} for x in values]}

    runtime = Translator()
    assert not translation.translation_step(job, runtime)
    first = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    assert len(first["data"]["translation"]["items"]) == 8
    while not translation.translation_step(job, runtime):
        pass
    complete = db.one("SELECT * FROM chapters WHERE id=?", (ident,))
    lesson = db.one("SELECT * FROM lessons WHERE id=?", (complete["lesson_id"],))
    assert translation.complete(complete, lesson)
    assert client.post(f"/api/chapters/{ident}/translation").json()["ready"]
    complete["data"]["turns"][0]["text"] = "The method uses 99 layers."
    db.save_chapter(complete)
    assert not translation.complete(db.one("SELECT * FROM chapters WHERE id=?", (ident,)), lesson)


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
