from paperspeak.planning import compact_fallback_groups, plan_step


def test_duplicate_evidence_keeps_coverage_without_reteaching_the_fact():
    data = {
        "title": "Test",
        "model": "test",
        "notes": [
            {
                "summary": "A shared fact and a new condition",
                "claims": [
                    {"id": "N1C1", "claim": "Weights stay fixed", "source_ids": ["s1"]},
                    {
                        "id": "N2C1",
                        "claim": "Original weights are frozen",
                        "source_ids": ["s2"],
                    },
                    {
                        "id": "N2C2",
                        "claim": "Results differ under a new condition",
                        "source_ids": ["s3"],
                    },
                ],
            }
        ],
    }
    sources = [
        {"id": s, "data": {"text": s, "image_path": s + ".jpg"}}
        for s in ["s1", "s2", "s3"]
    ]

    class Planner:
        def ask(self, prompt, **kwargs):
            if prompt.startswith("Design"):
                return {
                    "goals": [
                        {
                            "title": "Fixed weights",
                            "focus": "Understand what stays fixed",
                        },
                        {
                            "title": "A different condition",
                            "focus": "Compare conditions",
                        },
                    ],
                    "glossary": [],
                }
            return {
                "assignments": [
                    {"claim_id": "N1C1", "goal": 0, "duplicate_of": None},
                    {"claim_id": "N2C1", "goal": 0, "duplicate_of": "N1C1"},
                    {"claim_id": "N2C2", "goal": 1, "duplicate_of": None},
                ]
            }

    planner = Planner()
    assert not plan_step(data, sources, planner)
    assert not plan_step(data, sources, planner)
    assert plan_step(data, sources, planner)
    assert data["coverage"] == {"N1C1": [0], "N2C1": [0], "N2C2": [1]}
    assert data["outline"][0]["evidence_claim_ids"] == ["N1C1"]
    assert data["outline"][1]["evidence_claim_ids"] == ["N2C2"]
    assert [chapter["parts"] for chapter in data["outline"]] == [1, 1]


def test_editorial_planning_keeps_main_facts_and_merges_related_goals():
    claims = [{"id": f"C{i}", "claim": f"Fact {i}", "topic": "result" if i == 1 else "background",
               "source_ids": ["s"]} for i in range(1, 12)]
    claims.append({"id": "C12", "claim": "A connected idea", "topic": "mechanism", "source_ids": ["s"]})
    mapping = {c["id"]: {"goal": 0 if c["id"] != "C12" else 1, "canonical": c["id"]}
               for c in claims}
    data = {"title": "Paper", "model": "test", "planning_version": 3,
            "notes": [{"claims": claims}], "claim_map": mapping, "planning_index": len(claims),
            "learning_goals": [{"title": "First idea", "focus": "Learn the first idea."},
                               {"title": "Connected idea", "focus": "Learn the connection."}],
            "glossary": [], "glossary_review_index": 0}

    class Editor:
        def ask(self, prompt, **kwargs):
            if prompt.startswith("Edit a beginner"):
                return {"claim_ids": [f"C{i}" for i in range(1, 9)]}
            if prompt.startswith("Make a concise"):
                return {"chapters": [{"goals": [0, 1], "title": "The two connected ideas",
                                      "focus": "Learn how the ideas connect."}]}
            raise AssertionError(prompt[:70])

    editor = Editor()
    assert not plan_step(data, [], editor)
    assert not plan_step(data, [], editor)
    assert plan_step(data, [], editor)
    assert len(data["outline"]) == 1
    chapter = data["outline"][0]
    assert chapter["parts"] == 1
    assert chapter["evidence_claim_ids"] == [f"C{i}" for i in range(1, 9)] + ["C12"]
    assert chapter["supporting_claim_ids"] == ["C9", "C10", "C11"]
    assert all(data["coverage"][c["id"]] == [0] for c in claims)


def test_invalid_grouping_fallback_combines_only_adjacent_small_goals():
    data = {"learning_goals": [
        {"title": "Problem", "focus": "Learn the problem."},
        {"title": "Prerequisite", "focus": "Learn the prerequisite."},
        {"title": "Different experiment", "focus": "Learn the experiment."},
    ], "teaching_claims": {"0": list(range(5)), "1": [5], "2": list(range(8))}}
    groups = compact_fallback_groups(data, [0, 1, 2])
    assert [g["goals"] for g in groups] == [[0, 1], [2]]
    assert groups[0]["title"] == "Problem"
