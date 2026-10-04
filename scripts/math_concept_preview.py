"""Render equation-as-picture examples without creating jobs or changing finished films."""

from __future__ import annotations

import argparse
import fcntl
import json
import shutil
from pathlib import Path

from paperspeak import config, local_network, math_concepts, story_video, video
from paperspeak.runtime import GPUUnavailable, PracticePreempted, Runtime


def examples():
    return [
        {
            "id": "lora",
            "title_en": "LoRA: keep the model, learn a small change",
            "title_ja": "LoRA：元のモデルに小さな補正を足す",
            "equation": {
                "latex": r"h=W_0x+\frac{\alpha}{r}BAx",
                "en": "Original output plus a scaled learned correction",
                "ja": "元の出力に、倍率を付けた学習済み補正を足す",
            },
            "evidence": "LoRA freezes the original weights W0 and learns the low-rank correction BA, scaled by alpha/r. W0 is d by k, A is r by k and B is d by r. A is the DOWN-projection (k to r); B is the UP-projection (r to d). The SAME input x enters both paths; their output contributions are added. Alpha controls correction scaling and r is the rank. The dots in a diagram are schematic, not real layer dimensions.",
            "concept": math_concepts.lora({"latex": r"h=W_0x+\frac{\alpha}{r}BAx"}),
        },
        {
            "id": "qef-plane",
            "title_en": "Fit a vertex by reducing distances to planes",
            "title_ja": "面との距離を減らすように頂点を動かす",
            "equation": {
                "latex": r"E_{\mathrm{plane}}(v)=\sum_i\left(n_i\cdot(v-q_i)\right)^2",
                "en": "Plane-distance part of the QEF; other terms are not shown",
                "ja": "QEFの面との距離の項。境界と正則化の項は省略",
            },
            "evidence": "Native and Compact Structured Latents for 3D Generation, PDF p.4, Eq.2: the first QEF component sums squared distances from the dual vertex v to planes determined by surface samples qi and unit normals ni: d^2_Pi,i=(ni dot (v-qi))^2. The full method ALSO has boundary-edge and regularization terms; this picture illustrates ONLY the plane-distance component in TWO dimensions. Moving a candidate point reduces the sum of squared perpendicular distances. Dashed residuals are distances, not normals. Do not claim this is the full O-Voxel algorithm or its measured accuracy.",
            "concept": {
                "template": "fit_constraints",
                "parts": [
                    {"en": "Planes from surface samples", "ja": "表面から得た面"},
                    {
                        "en": "A candidate with large distances",
                        "ja": "距離が大きい仮の頂点",
                    },
                    {"en": "Move toward a better fit", "ja": "距離を減らす位置へ"},
                ],
                "symbols": [
                    {
                        "latex": "n_i",
                        "en": "Surface normal",
                        "ja": "面の垂直方向",
                        "part": 0,
                    },
                    {
                        "latex": "q_i",
                        "en": "Surface sample",
                        "ja": "表面上の点",
                        "part": 0,
                    },
                    {
                        "latex": "v",
                        "en": "Adjustable vertex",
                        "ja": "動かす頂点",
                        "part": 1,
                    },
                    {
                        "latex": r"E_{\mathrm{plane}}",
                        "en": "Total plane-distance error",
                        "ja": "面との距離の誤差の合計",
                        "part": 2,
                    },
                ],
            },
        },
    ]


def run(local_ai=False):
    root = config.DATA / "evaluation" / "math-concepts"
    root.mkdir(parents=True, exist_ok=True)
    report = {
        "policy": math_concepts.VERSION,
        "examples": [],
        "local_ai": local_ai,
        "creates_generation_jobs": False,
    }
    runtime = Runtime() if local_ai else None
    lock = (config.DATA / "work-step.lock").open("a")
    if runtime:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime.job_kind = "video_project"
    try:
        for sample in examples():
            concept = sample["concept"]
            generation = None
            if runtime:
                previous_error = ""
                for attempt in range(3):
                    try:
                        with local_network.inference_only():
                            answer = runtime.ask(
                                'Design ONLY the conceptual picture and symbol meanings for this supplied equation. Return a JSON object with key "concepts", an array containing exactly one concept matching this schema. '
                                + math_concepts.BRIEF
                                + math_concepts.SCHEMA
                                + "\nSOURCE: "
                                + sample["evidence"]
                                + "\nEQUATION: "
                                + json.dumps(sample["equation"])
                                + "\nREPAIR THIS PREVIOUS PROBLEM: "
                                + previous_error,
                                profile="qwen-q8",
                                max_tokens=1500,
                                thinking=False,
                            )
                        candidate = answer["concepts"][0]
                        draft = {
                            "equations": [sample["equation"]],
                            "concepts": [candidate],
                        }
                        part_changes = math_concepts.normalize_parts(draft)
                        simplifications = math_concepts.normalize_symbols(draft)
                        math_concepts.validate(
                            draft,
                            required=True,
                        )
                        # The trusted operation is fixed by the example's source, not a popularity vote.
                        if candidate["template"] != sample["concept"]["template"]:
                            raise ValueError(
                                "The template does not show the source operation"
                            )
                        if sample["id"] == "lora":
                            corrected = math_concepts.normalize_lora_symbols(
                                {
                                    "equations": [sample["equation"]],
                                    "concepts": [candidate],
                                }
                            )
                            if corrected:
                                report.setdefault("source_checks", []).append(
                                    "LoRA symbol meanings normalized to the primary-paper convention: A reduces, B expands"
                                )
                        if sample["id"] == "qef-plane":
                            expected = {
                                s["latex"]: s["part"]
                                for s in sample["concept"]["symbols"]
                            }
                            for symbol in candidate["symbols"]:
                                if (
                                    symbol["latex"] in expected
                                    and symbol["part"] != expected[symbol["latex"]]
                                ):
                                    raise ValueError(
                                        "Match source colors: n_i and q_i belong to blue part 0; v is orange part 1; the error is purple part 2"
                                    )
                        concept, generation = (
                            candidate,
                            getattr(runtime, "last_generation", {}),
                        )
                        if simplifications:
                            report.setdefault("symbol_simplifications", []).append(
                                {"example": sample["id"], "changes": simplifications}
                            )
                        if part_changes:
                            report.setdefault("source_checks", []).append(
                                sample["id"]
                                + ": bilingual part headings matched to the fixed drawing's actual roles"
                            )
                        break
                    except (GPUUnavailable, PracticePreempted):
                        raise
                    except (ValueError, KeyError, TypeError) as exc:
                        previous_error = str(exc)
                        report.setdefault("repairs", []).append(
                            {
                                "example": sample["id"],
                                "attempt": attempt + 1,
                                "reason": str(exc),
                            }
                        )
                else:
                    report.setdefault("fallbacks", []).append(sample["id"])
            spec = {
                "type": "equation",
                "nodes": concept["parts"],
                "equations": [sample["equation"]],
                "concepts": [concept],
                "caption_en": sample["equation"]["en"],
                "caption_ja": sample["equation"]["ja"],
            }
            math_concepts.validate(spec, required=True)
            files = []
            for focus in (0, 1):
                source = root / (sample["id"] + f"-{focus}.json")
                output = source.with_suffix(".png")
                source.write_text(
                    json.dumps(
                        {
                            "mode": "deep_dive",
                            "title_en": sample["title_en"],
                            "title_ja": sample["title_ja"],
                            "focus": focus,
                            "visual": spec,
                            "data_root": str(config.DATA),
                        },
                        ensure_ascii=False,
                    )
                )
                story_video._render(source, output)
                public = config.DATA / "visuals" / "math-concept-previews" / output.name
                public.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(output, public)
                files.append(
                    {
                        "path": str(output.relative_to(config.DATA)),
                        "public_path": str(public.relative_to(config.DATA)),
                        "sha256": video.file_digest(output),
                        "layout": json.loads(
                            Path(str(output) + ".layout.json").read_text()
                        ),
                    }
                )
            report["examples"].append(
                {
                    "id": sample["id"],
                    "source_scope": sample["evidence"],
                    "concept": concept,
                    "generation": generation,
                    "files": files,
                }
            )
        (root / "acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        return report
    finally:
        if runtime:
            runtime.close()
        lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-ai", action="store_true")
    print(json.dumps(run(parser.parse_args().local_ai), ensure_ascii=False, indent=2))
