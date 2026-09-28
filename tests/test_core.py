import concurrent.futures
import time

import pytest
from paperspeak import config, db, lessons, papers
from paperspeak.quality import changed_spoken_number, validate_turns, word_diff


def test_benchmark_and_method_word_breaks_do_not_hide_changed_names_or_numbers():
    from paperspeak.quality import speech_match
    assert speech_match("MultiNLI-matched and WikiSQL.", "Multi NLI matched and Wiki SQL.")[1]
    assert speech_match("PrefixEmbed and PrefixLayer.", "Prefix embed and prefix layer.")[1]
    assert not speech_match("PrefixEmbed uses two tables.", "Prefix layer uses two tables.")[1]
    assert not speech_match("WikiSQL uses two tables.", "Wiki SQL uses three tables.")[1]
    assert not speech_match("MultiNLI-matched.", "Multi NLI mismatched.")[1]


def test_spoken_ordinals_preserve_powers_and_still_reject_wrong_numbers():
    from paperspeak.quality import speech_match
    script = "That position follows because 175 billion is roughly 1.75 times 10 to the 11th."
    assert speech_match(script, script.replace("11th", "eleventh"))[1]
    assert not speech_match(script, script.replace("11th", "twelfth"))[1]
    assert not speech_match(script, script.replace("1.75", "175"))[1]
    assert speech_match("The 21st step.", "The twenty-first step.")[1]
    long = "We reach the eleventh step after we finish each part of the earlier process in the usual order."
    assert not speech_match(long, long.replace("eleventh", "twelfth"))[1]


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.test/abs/1706.03762",
        "https://arxiv.org@evil.test/abs/1706.03762",
        "https://arxiv.org:9999/abs/1706.03762",
        "file:///etc/passwd",
        "https://arxiv.org/abs/../../etc/passwd",
        "https://arxiv.org/abs/1706.03762v0",
    ],
)
def test_reject_unsafe_reference(value):
    with pytest.raises(ValueError):
        papers.parse_reference(value)


def test_arxiv_versions():
    assert papers.parse_reference("https://arxiv.org/pdf/1706.03762v7.pdf") == (
        "1706.03762",
        "v7",
    )
    assert papers.parse_reference("cs/9901001") == ("cs/9901001", "")


def test_arxiv_error_feed_is_not_an_empty_success():
    with pytest.raises(ValueError, match="temporary failure"):
        papers.entries(
            b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors</id><summary>temporary failure</summary></entry></feed>'
        )


def test_spelled_number_change_is_detected_even_when_overall_wer_is_small():
    script = "The model uses two small matrices to learn the new task while its old weights stay fixed."
    diff = word_diff(script, script.replace("two", "three"))
    assert diff["wer"] < 0.08 and changed_spoken_number(diff)
    assert not changed_spoken_number(
        word_diff("There are 2 matrices.", "There are two matrices.")
    )


def test_math_html_keeps_one_number_and_tables():
    raw = b"""<article class="ltx_document"><h2 class="ltx_title">Results</h2><p class="ltx_p">The score is <math alttext="28.4"><mi>28.4</mi><annotation>28.4</annotation></math>.</p><figure class="ltx_table" id="T1"><table><tr><td>Model A</td><td>28.4</td></tr></table></figure></article>"""
    sources = papers.html_sources(raw, "p1", "https://arxiv.org/html/1706.03762v1")
    assert "28.428.4" not in sources[0]["data"]["text"]
    assert sources[1]["kind"] == "table"
    assert sources[1]["data"]["url"].endswith("#T1")


def test_split_does_not_lose_tail():
    text = ("A sentence. More detail.\n" * 900) + "Unique final finding."
    parts = list(papers.split_text(text))
    assert parts[-1].endswith("Unique final finding.")
    assert "".join(parts).replace(" ", "").replace("\n", "") == text.replace(
        " ", ""
    ).replace("\n", "")
    assert max(map(len, parts)) <= 4500


def test_word_match_numbers_contractions_and_missing_words():
    assert (
        word_diff("We don't use 12 layers.", "We do not use twelve layers.")[
            "word_match"
        ]
        == 100
    )
    r = word_diff("The model learns from data.", "The model from data.")
    assert r["word_match"] == 80
    assert next(w for w in r["words"] if w["kind"] == "missing")["expected"] == "learns"
    assert word_diff("Read this.", "")["word_match"] is None
    assert word_diff("One two three.", "Two three one.")["wer"] == pytest.approx(2 / 3)


def test_source_validation_checks_actual_cited_evidence():
    sources = [
        {"id": "a", "data": {"text": "Model A has 12 layers."}},
        {"id": "b", "data": {"text": "Model B has 24 layers."}},
    ]
    turn = {
        "speaker": "guide",
        "text": "Model A has 24 layers.",
        "kind": "paper",
        "source_ids": ["a"],
    }
    assert validate_turns([turn], sources)
    turn["text"] = "Model A has 12 layers."
    assert not validate_turns([turn], sources)
    turn["source_ids"] = ["missing"]
    assert validate_turns([turn], sources)


def test_concurrent_enqueue_and_claim_exactly_once(database):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: db.enqueue("lesson", "same"), range(12)))
        jobs = list(pool.map(lambda i: db.claim(str(i)), range(8)))
    assert len(set(ids)) == 1
    assert len([j for j in jobs if j]) == 1


def test_crash_recovery_preserves_checkpoint_and_pause(database):
    ident = db.enqueue("lesson", "a")
    db.claim("dead")
    db.patch_job(ident, checkpoint={"page": 7}, heartbeat=time.time() - 100)
    recovered = db.claim("new")
    assert recovered["id"] == ident and recovered["checkpoint"] == {"page": 7}
    db.patch_job(ident, state="paused", heartbeat=time.time() - 100)
    assert db.claim("third") is None


def test_revisions_do_not_overwrite_finished_lessons(database):
    pid = papers.register(
        {"source_id": "1706.03762", "version": "v1", "title": "Test paper"}
    )
    a = lessons.create(pid)
    assert lessons.create(pid) == a
    db.execute("UPDATE lessons SET state='ready' WHERE id=?", (a,))
    b = lessons.create(pid)
    assert a != b
    assert db.one("SELECT state FROM lessons WHERE id=?", (a,))["state"] == "ready"


def test_path_cannot_escape_private_data(database):
    with pytest.raises(ValueError):
        config.safe_path("../../etc/passwd")
    (database / "audio/link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        config.safe_path("audio/link")


def test_html_table_is_linked_to_its_original_image():
    from paperspeak.papers import link_visual_sources

    items = [
        {"id": "html", "kind": "table", "data": {"text": "Table 1: Exact comparison."}},
        {
            "id": "p1",
            "kind": "page",
            "data": {
                "page": 1,
                "text": "We mention Table 1: this is not its caption.",
                "image_path": "papers/p1.jpg",
                "pdf_path": "papers/p.pdf",
            },
        },
        {
            "id": "p2",
            "kind": "page",
            "data": {
                "page": 2,
                "text": "Table 1: Exact comparison.",
                "image_path": "papers/p2.jpg",
                "pdf_path": "papers/p.pdf",
            },
        },
    ]
    link_visual_sources(items)
    assert items[0]["data"]["image_path"] == "papers/p2.jpg"
    assert items[0]["data"]["page_source_id"] == "p2"


def test_spelled_initialisms_match_without_hiding_changed_numbers():
    from paperspeak.quality import changed_spoken_number, word_diff

    assert (
        word_diff(
            "In NL2SQL, x is a question in plain words.",
            "In N L two S Q L, X is a question in plain words.",
        )["wer"]
        == 0
    )
    changed = word_diff("SQL uses two columns.", "S Q L uses three columns.")
    assert changed["wer"] > 0 and changed_spoken_number(changed)
    assert (
        word_diff(
            "And y is the matching SQL command.", "And why is the matching SQL command?"
        )["wer"]
        == 0
    )
    assert word_diff("x has two rows.", "Ex has three rows.")["wer"] > 0
    assert word_diff("We need two rows.", "We need to rows.")["wer"] > 0
    assert word_diff("We need TWO rows.", "We need T W O rows.")["wer"] > 0


def test_a_variable_change_is_not_hidden_in_a_long_sentence():
    from paperspeak.quality import critical_speech_change, word_diff

    script = (
        "In this small example, x represents the input text that we give to the model."
    )
    diff = word_diff(script, script.replace(" x ", " y "))
    assert 0 < diff["wer"] <= 0.08
    assert critical_speech_change(script, diff, [])


def test_pretraining_spelling_does_not_trigger_a_false_audio_repair():
    from paperspeak.quality import word_diff

    assert word_diff("A pretrained model", "A pre-trained model")["wer"] == 0
    assert word_diff("Pre-training uses data", "Pretraining uses data")["wer"] == 0
    assert word_diff("We re-sign today", "We resign today")["wer"] > 0
    assert word_diff("A pretrained model", "A post-trained model")["wer"] > 0
    assert word_diff("dmodel is the size", "D model is the size")["wer"] == 0
    assert word_diff("only half-way", "only halfway")["wer"] == 0
    assert word_diff("four-fold change in d ff", "fourfold change in DFF")["wer"] == 0
    assert word_diff("four-fold change", "threefold change")["wer"] > 0
    assert word_diff("The feedforward table", "The feed-forward table")["wer"] == 0
    assert word_diff("The feedforward table", "The feedback table")["wer"] > 0
    assert word_diff("A non-linear curve", "A nonlinear curve")["wer"] == 0
    assert word_diff("A non-linear curve", "A linear curve")["wer"] > 0
    assert word_diff("per Sterner et al. (2026a)", "per Sterner at all, 2026a")["wer"] == 0
    assert word_diff("per Sterner et al. (2026a)", "per Sterner at all, 2025a")["wer"] > 0
    assert word_diff("two steps", "three steps")["wer"] > 0


def test_audio_spelling_variants_keep_method_names_and_numbers():
    from paperspeak.quality import speech_match

    pairs = [
        ("AdapterH adds thirty point three percent latency compared with the Fine-Tune/LoRA baseline.",
         "Adapter H adds 30.3 percent latency compared with the fine-tuned LoRA baseline."),
        ("So spreading the budget beat stacking it on a single table.",
         "So spreading the budget beats stacking it on a single table."),
        ("The 21.5 value is the r=4 factor, calculated as 6.91 divided by 0.32, not rounded to twenty.",
         "The 21.5 value is the r equals 4 factor, calculated as 6.91 divided by 0.32, not rounded to 20."),
        ("It needs one hundred twenty-five million trainable parameters.",
         "It needs 125 million trainable parameters."),
    ]
    assert all(speech_match(script, heard)[1] for script, heard in pairs)
    assert not speech_match("It needs 125 million parameters.", "It needs 120 million parameters.")[1]
    assert not speech_match("AdapterH is faster.", "AdapterL is faster.")[1]
