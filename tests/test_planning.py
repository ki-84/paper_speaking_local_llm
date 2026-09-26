from paperspeak.planning import plan_step


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
