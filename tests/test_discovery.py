import datetime as dt
import json

from paperspeak import db, discovery, papers


def recommendation(pid, day, score=16):
    ident = db.uid()
    db.execute(
        "INSERT INTO recommendations VALUES (?,?,?,?,?,?)",
        (ident, pid, day, "recommended", db.dumps({"total": score}), None),
    )
    return ident


def test_daily_selection_limit_backlog_and_no_suitable_paper(database):
    day = "2026-09-25"
    pids = [
        papers.register(
            {
                "source_id": f"paper{i}",
                "version": "v1",
                "title": f"Paper {i}",
                "categories": ["cs.LG"],
            }
        )
        for i in range(3)
    ]
    for p in pids:
        recommendation(p, day)
    jid = db.enqueue("discover", day)
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    job["checkpoint"] = {"phase": "choose", "day": day, "selected": pids, "started": 1}
    assert discovery.discovery_step(job, None)
    assert (
        db.one("SELECT count(*) n FROM recommendations WHERE state='selected'")["n"]
        == 1
    )
    assert discovery.discovery_step(job, None)
    assert db.one("SELECT count(*) n FROM jobs WHERE kind='lesson'")["n"] == 1
    # The next day's collection may recommend a paper, but does not add another backlog job.
    recommendation(pids[1], "2026-09-26")
    job["checkpoint"]["day"] = "2026-09-26"
    discovery.discovery_step(job, None)
    assert db.one("SELECT count(*) n FROM jobs WHERE kind='lesson'")["n"] == 1
    # No suitable candidate is a valid successful day.
    job["checkpoint"]["day"] = "2026-09-27"
    job["checkpoint"]["selected"] = []
    assert discovery.discovery_step(job, None)


def test_revision_is_distinct_but_repeated_version_is_not(database):
    base = {"source_id": "2106.09685", "version": "v1", "title": "Paper"}
    p1 = papers.register(base)
    assert papers.register(base) == p1
    p2 = papers.register(base | {"version": "v2"})
    assert p2 != p1


def test_read_feedback_and_listening_progress_inform_the_next_selection(database):
    from paperspeak import lessons

    read = papers.register(
        {"source_id": "read", "version": "v1", "title": "Already read"}
    )
    rid = recommendation(read, "2026-09-25")
    db.execute("UPDATE recommendations SET feedback='read' WHERE id=?", (rid,))
    listening = papers.register(
        {"source_id": "listening", "version": "v1", "title": "Listening now"}
    )
    lid = lessons.create(listening)
    db.execute(
        "INSERT INTO chapters VALUES (?,?,?,?,?)",
        ("listening-chapter", lid, 0, "ready", db.dumps({})),
    )
    db.execute(
        "INSERT INTO cursors VALUES (?,?)",
        (
            "lesson:" + lid,
            db.dumps({"chapter_id": "listening-chapter", "turn_index": 2}),
        ),
    )
    untouched = papers.register(
        {"source_id": "untouched", "version": "v1", "title": "Not studied"}
    )
    lessons.create(untouched)
    assert {p["title"] for p in discovery.learning_history()} == {
        "Already read",
        "Listening now",
    }


def test_recommendation_waits_for_plain_english_and_keeps_detailed_evidence(client):
    pid = papers.register(
        {"source_id": "plain", "version": "v1", "title": "A useful idea"}
    )
    rid = recommendation(pid, "2026-09-25")
    data = {
        "why": "Detailed technical assessment",
        "source_ids": ["s1"],
        "full_text_read": True,
        "intended_state": "recommended",
        "reading_notes": [{"quote": "original evidence"}],
        "total": 16,
    }
    db.execute(
        "UPDATE recommendations SET state='needs_explanation',data=? WHERE id=?",
        (db.dumps(data), rid),
    )
    jid = db.enqueue("discover", "plain")
    db.patch_job(
        jid,
        checkpoint={
            "phase": "review",
            "day": "2026-09-25",
            "selected": [pid],
            "review_index": 1,
        },
    )
    assert client.get("/api/recommendations").json() == []

    class Explainer:
        source = "unknown"

        def ask(self, prompt, **kwargs):
            if prompt.startswith("Check this plain-English"):
                return {"passed": True, "issues": []}
            return {
                "why": "A model can learn from its own answers.",
                "learn": "You can see how it checks those answers.",
                "cautions": ["It may learn its own mistakes."],
                "source_ids": [self.source],
            }

    provider = Explainer()
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert discovery.discovery_step(job, provider) is False
    held = db.one("SELECT data FROM recommendations WHERE id=?", (rid,))["data"]
    assert held["presentation_revisions"] == 1
    assert "source IDs" in held["presentation_issues"][0]
    assert client.get("/api/recommendations").json() == []
    provider.source = "s1"
    assert discovery.discovery_step(job, provider) is False
    assert client.get("/api/recommendations").json() == []
    assert discovery.discovery_step(job, provider) is False
    saved = db.one("SELECT * FROM recommendations WHERE id=?", (rid,))
    assert saved["state"] == "recommended" and saved["data"]["easy_english"]
    assert saved["data"]["assessment"]["why"] == "Detailed technical assessment"
    assert saved["data"]["reading_notes"] == data["reading_notes"]
    assert saved["data"]["total"] == 16
    public = client.get("/api/recommendations").json()[0]["data"]
    assert "assessment" not in public and "reading_notes" not in public


def test_repeated_explanation_format_failure_does_not_stop_the_daily_batch(client):
    pid = papers.register(
        {"source_id": "long", "version": "v1", "title": "Long explanation"}
    )
    rid = recommendation(pid, "2026-09-25")
    db.execute(
        "UPDATE recommendations SET state='needs_explanation',data=? WHERE id=?",
        (
            db.dumps(
                {
                    "full_text_read": True,
                    "source_ids": ["s1"],
                    "intended_state": "recommended",
                }
            ),
            rid,
        ),
    )
    jid = db.enqueue("discover", "format-failure")
    db.patch_job(
        jid,
        checkpoint={
            "phase": "review",
            "day": "2026-09-25",
            "selected": [],
            "review_index": 0,
        },
    )

    class LongExplanation:
        prompts = []

        def ask(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return {
                "why": "A useful idea.",
                "learn": "word " * 45,
                "cautions": ["Some questions remain."],
                "source_ids": ["s1"],
            }

    provider = LongExplanation()
    for _ in range(3):
        assert (
            discovery.discovery_step(
                db.one("SELECT * FROM jobs WHERE id=?", (jid,)), provider
            )
            is False
        )
    saved = db.one("SELECT * FROM recommendations WHERE id=?", (rid,))
    assert saved["state"] == "explanation_failed"
    assert saved["data"]["presentation_blocked"]
    assert "learn has 45 words" in provider.prompts[1]
    assert "word word word" in provider.prompts[1]
    assert client.get("/api/recommendations").json() == []
    assert (
        discovery.discovery_step(
            db.one("SELECT * FROM jobs WHERE id=?", (jid,)), provider
        )
        is False
    )
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert job["checkpoint"]["phase"] == "choose" and job["state"] != "failed"


def test_scheduler_three_days_and_restart_are_idempotent(database, monkeypatch):
    original = dt.datetime

    class Clock(original):
        current = original(2026, 9, 25, 3, 0, tzinfo=dt.timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(discovery.dt, "datetime", Clock)
    for day in [25, 26, 27]:
        Clock.current = original(2026, 9, day, 3, 0, tzinfo=dt.timezone.utc)
        discovery.schedule()
        discovery.schedule()
        db.execute("UPDATE jobs SET state='completed' WHERE kind='discover'")
    assert db.one("SELECT count(*) n FROM jobs WHERE kind='discover'")["n"] == 3


def test_scheduler_does_not_replace_a_failed_manual_batch_on_the_same_day(
    database, monkeypatch
):
    original = dt.datetime

    class Clock(original):
        @classmethod
        def now(cls, tz=None):
            return original(2026, 9, 25, 3, 0, tzinfo=dt.timezone.utc).astimezone(tz)

    monkeypatch.setattr(discovery.dt, "datetime", Clock)
    jid = db.enqueue("discover", "manual:2026-09-25")
    db.patch_job(jid, state="failed", checkpoint={"day": "2026-09-25"})
    discovery.schedule()
    assert db.one("SELECT count(*) n FROM jobs WHERE kind='discover'")["n"] == 1


def long_paper_notes(monkeypatch):
    pid = papers.register(
        {"source_id": "long-notes", "version": "v1", "title": "A long paper"}
    )
    sid = pid + ":H1"
    db.execute(
        "INSERT INTO sources VALUES (?,?,?,?)",
        (sid, pid, "section", db.dumps({"text": "The method changes a few weights."})),
    )
    monkeypatch.setattr(
        papers, "ingest", lambda _: {"id": pid, "title": "A long paper"}
    )
    notes = [
        {
            "findings": [
                {"point": "Important measured condition. " * 350, "source_ids": [sid]}
            ]
        }
        for _ in range(6)
    ]
    jid = db.enqueue("discover", "long-notes")
    db.patch_job(
        jid,
        checkpoint={
            "phase": "review",
            "day": "2026-09-25",
            "selected": [pid],
            "review_index": 0,
            "group_index": 1,
            "summaries": notes,
        },
    )
    return jid, sid, notes


def test_long_paper_rollup_keeps_original_notes_and_leaves_generation_room(
    database, monkeypatch
):
    jid, sid, notes = long_paper_notes(monkeypatch)

    class Provider:
        def ask(self, prompt, **kwargs):
            assert kwargs["max_tokens"] == 6000 and kwargs["thinking"] is False
            assert len(json.loads(prompt.split("\n", 1)[1])) == 4
            return {
                "findings": [
                    {
                        "point": "The method changes fewer weights under the same test conditions.",
                        "source_ids": [sid],
                    }
                ],
                "concerns": ["The tests cover only one task."],
                "learning_value": "Compare test conditions.",
            }

    assert not discovery.discovery_step(
        db.one("SELECT * FROM jobs WHERE id=?", (jid,)), Provider()
    )
    cp = db.one("SELECT checkpoint FROM jobs WHERE id=?", (jid,))["checkpoint"]
    assert cp["raw_reading_notes"] == notes
    assert cp["summaries"][1:] == notes[4:] and len(cp["summaries"]) == 3


def test_rollup_budget_failure_shrinks_then_skips_without_recommending_partial_reading(
    database, monkeypatch
):
    from paperspeak.runtime import ModelBudgetError

    jid, _, notes = long_paper_notes(monkeypatch)

    class LimitedProvider:
        sizes = []

        def ask(self, prompt, **kwargs):
            self.sizes.append(len(json.loads(prompt.split("\n", 1)[1])))
            raise ModelBudgetError("output too long")

    provider = LimitedProvider()
    for _ in range(2):
        assert not discovery.discovery_step(
            db.one("SELECT * FROM jobs WHERE id=?", (jid,)), provider
        )
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert provider.sizes == [4, 2] and job["state"] != "failed"
    assert job["checkpoint"]["review_index"] == 1
    assert job["checkpoint"]["unavailable"] and not job["checkpoint"]["summaries"]
    assert db.one("SELECT count(*) n FROM recommendations")["n"] == 0


def test_discovery_can_retry_a_failure_before_first_checkpoint(database, monkeypatch):
    jid = db.enqueue("discover", "retry-before-save")
    db.patch_job(jid, checkpoint={"_failures": 1})
    job = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    monkeypatch.setattr(
        papers,
        "fetch",
        lambda *a, **k: b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>',
    )
    assert discovery.discovery_step(job, None) is False
    saved = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert (
        saved["checkpoint"]["phase"] == "collect"
        and saved["checkpoint"]["category_index"] == 1
    )


def test_metadata_collection_yields_priority_before_full_reading(database):
    jid = db.enqueue("discover", "collection", priority=8)
    db.patch_job(
        jid,
        checkpoint={
            "phase": "collect",
            "category_index": len(db.settings()["categories"]),
            "candidates": {},
        },
    )
    assert (
        discovery.discovery_step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), None)
        is False
    )
    saved = db.one("SELECT * FROM jobs WHERE id=?", (jid,))
    assert saved["priority"] == 30 and saved["checkpoint"]["phase"] == "review"


def test_incremental_collection_includes_revised_old_papers(database, monkeypatch):
    old = {"source_id": "2106.09685", "version": "v1", "title": "Old paper"}
    papers.register(old)
    revised = old | {
        "version": "v2",
        "updated": "2026-09-24T12:00:00Z",
        "published": "2021-06-17T00:00:00Z",
    }
    jid = db.enqueue("discover", "revisions")
    db.patch_job(
        jid,
        checkpoint={
            "phase": "collect",
            "collection_mode": "updates",
            "category_index": 0,
            "start": 0,
            "since": "202609230000",
            "until": "202609250000",
            "candidates": {},
        },
    )

    def fetch(url, params, **kwargs):
        assert params["sortBy"] == "lastUpdatedDate"
        assert "submittedDate" not in params["search_query"]
        assert kwargs["cache_scope"] == "202609250000"
        return b""

    monkeypatch.setattr(papers, "fetch", fetch)
    monkeypatch.setattr(
        papers,
        "entries",
        lambda _: [revised, old | {"updated": "2020-01-01T00:00:00Z"}],
    )
    discovery.discovery_step(db.one("SELECT * FROM jobs WHERE id=?", (jid,)), None)
    saved = db.one("SELECT checkpoint FROM jobs WHERE id=?", (jid,))["checkpoint"]
    assert saved["candidates"]["2106.09685"]["version"] == "v2"
    assert saved["category_index"] == 1
