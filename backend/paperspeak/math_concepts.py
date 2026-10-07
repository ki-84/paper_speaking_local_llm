"""Fixed visual meanings for equations, revealed before mathematical notation."""

from __future__ import annotations

import re

VERSION = "concept-before-symbols-1"
TEMPLATES = {
    "parallel_paths",
    "bottleneck",
    "weighted_sum",
    "fit_constraints",
    "relationship",
    "return_target",
    "policy_tradeoff",
    "moving_average",
    "bellman_error",
    "state_vector",
}
PARTS = {
    "parallel_paths": [
        ("Keep the original path", "元の経路は固定"),
        ("Learn a small correction", "小さな補正を学ぶ"),
        ("Add both contributions", "二つの出力を足す"),
    ],
    "bottleneck": [
        ("Original input", "元の入力"),
        ("Compact representation", "小さな表現へ"),
        ("Reconstruct the output", "出力を復元する"),
    ],
    "weighted_sum": [
        ("First weighted contribution", "一つ目の重み付き入力"),
        ("Second weighted contribution", "二つ目の重み付き入力"),
        ("Combine both contributions", "二つの入力を合わせる"),
    ],
    "fit_constraints": [
        ("Planes from surface samples", "表面から得た面"),
        ("A candidate with large distances", "距離が大きい仮の頂点"),
        ("Move toward a better fit", "距離を減らす位置へ"),
    ],
    "relationship": [
        ("Given quantities", "考える量"),
        ("The relationship", "量の関係"),
        ("What it tells us", "わかること"),
    ],
    "return_target": [
        ("Reward now", "今の報酬"),
        ("Discounted future value", "割り引いた将来の価値"),
        ("Combine into a learning target", "学習の目標値に合わせる"),
    ],
    "policy_tradeoff": [
        ("Action diversity", "行動の多様さ"),
        ("Predicted action value", "行動の予測価値"),
        ("Balance the two goals", "二つの目的を両立する"),
    ],
    "moving_average": [
        ("Previous target weights", "これまでの目標重み"),
        ("Current learned weights", "現在の学習した重み"),
        ("Slowly updated target", "ゆっくり更新する目標"),
    ],
    "bellman_error": [
        ("Value prediction", "価値の予測"),
        ("Reward plus discounted next value", "報酬と割り引いた次の価値"),
        ("Average the squared difference", "差を二乗して平均する"),
    ],
    "state_vector": [
        ("Measured body signals", "身体から計測する信号"),
        ("Command and previous action", "命令と前回の行動"),
        ("Combine into an observation", "観測ベクトルにまとめる"),
    ],
}
BRIEF = (
    "Use bellman_error for a basic squared bootstrapped value loss: a value prediction, immediate reward plus discounted next value, and their squared difference averaged over samples. It is NOT the categorical cross-entropy objective of a distributional critic. "
    "Use state_vector for physical measurements plus command/action history concatenated into one observation. It does not depict learning updates. "
    "Never teach an equation as a formula card alone. First show what its quantities refer to and what changes in a concrete conceptual picture; then reveal the SAME picture with its symbols and equation. "
    "Use parallel_paths for a fixed contribution plus a learned correction, bottleneck for encoding then reconstruction, weighted_sum for combining contributions, fit_constraints for fitting a point to geometric constraints, return_target for immediate reward plus discounted future value, policy_tradeoff for entropy/action diversity versus predicted value, moving_average for a slowly blended target copy, or relationship for other relations. "
    "return_target shows a reward token, future value on a later timeline, and their sum; policy_tradeoff shows diverse action branches, a critic's estimate, and a balance; moving_average shows a previous target and a current model contributing to a smoothed copy. Never use these pictures for an unrelated operation. "
    "These fixed drawings are schematic teaching illustrations, not measured results or literal matrix dimensions. Choose a template only when its operation agrees with the source. "
    "Match these FIXED picture parts and colors: parallel_paths = blue frozen original path, orange learned correction, purple combined output; bottleneck = blue input, orange compact representation, purple reconstruction; weighted_sum = blue first contribution, orange second contribution, purple weighted result; fit_constraints = blue fixed surface samples/normals, orange candidate point with large residuals, purple adjusted point with smaller total squared residuals. Do not relabel the third fitting panel as a graph of the loss; it shows the fitted point. "
    "For each equation provide three short bilingual parts and symbol meanings assigned to part 0, 1 or 2. Part labels must be short noun phrases: at most 44 English characters and 28 Japanese characters. Symbol meanings must be at most 32 English characters and 18 Japanese characters. Do not replace an unmodified original figure. Name what stays fixed, what is learned or moved, and the output. "
    "For geometric fitting, dashed segments mean perpendicular residual distances, not normals or arbitrary connections; an open surface is not automatically non-manifold. "
    "Each symbol key must be ONE variable or named quantity (subscripts allowed), not an entire expression, function call or operator such as sum or a dot product. This lets every variable keep the same color throughout the equation. For fitting, assign the adjustable vertex to orange part 1, fixed samples and normals to blue part 0, and the error objective to purple part 2. "
    "Explain the picture first, define the symbols in everyday language, then walk through the relation. Keep useful analogy boundaries and experimental conditions. "
)
SCHEMA = (
    '"concepts":[{"template":"parallel_paths|bottleneck|weighted_sum|fit_constraints|return_target|policy_tradeoff|moving_average|bellman_error|state_vector|relationship",'
    '"parts":[{"en":"short meaning of first part","ja":"日本語"},{"en":"second meaning","ja":"日本語"},{"en":"third meaning","ja":"日本語"}],'
    '"symbols":[{"latex":"exact symbol from this equation","en":"meaning","ja":"意味","part":0}]}]'
)


def _label(value):
    if not isinstance(value, dict) or any(
        not isinstance(value.get(k), str) or not value[k].strip() for k in ("en", "ja")
    ):
        raise ValueError("Concept labels need English and Japanese")
    if len(value["en"]) > 44 or len(value["ja"]) > 28:
        raise ValueError(
            f"Shorten this label to <=44 English and <=28 Japanese characters: {value['en']!r} / {value['ja']!r}"
        )


def normalize_parts(spec):
    """Fixed drawings use fixed bilingual role headings, not contradictory relabeling."""
    changes = []
    for concept in spec.get("concepts", []) or []:
        if not isinstance(concept, dict) or concept.get("template") not in PARTS:
            continue
        old = concept.get("parts")
        if not isinstance(old, list) or len(old) != 3:
            continue
        canonical = [{"en": en, "ja": ja} for en, ja in PARTS[concept["template"]]]
        if old != canonical:
            concept.setdefault("planned_parts", old)
            concept["parts"] = canonical
            changes.append(concept["template"])
    return changes


def normalize_symbols(spec):
    """Keep a named function separate from arguments with their own colors."""
    changes = []
    for concept in spec.get("concepts", []) or []:
        if not isinstance(concept, dict) or not isinstance(
            concept.get("symbols"), list
        ):
            continue
        kept = []
        for symbol in concept["symbols"]:
            if not isinstance(symbol, dict) or not isinstance(symbol.get("latex"), str):
                kept.append(symbol)
                continue
            tex = symbol["latex"]
            function = re.fullmatch(r"([^()=+*/\-]+)\([^()]*\)", tex)
            if function and not re.search(r"\\(?:cdot|sum|frac|prod)\b", function[1]):
                symbol["latex"] = function[1]
                changes.append(
                    {
                        "from": tex,
                        "to": function[1],
                        "reason": "Color the function name separately from its arguments",
                    }
                )
            elif re.search(r"[=+*/()\-]|\\(?:sum|prod|frac|cdot|left|right)\b", tex):
                changes.append(
                    {
                        "from": tex,
                        "reason": "Keep this operation in the equation; do not override the colors of its variables",
                    }
                )
                continue
            kept.append(symbol)
        concept["symbols"] = kept
    if changes:
        spec.setdefault("symbol_simplifications", []).extend(changes)
    return changes


def validate(spec, *, required=False):
    equations, concepts = spec.get("equations", []), spec.get("concepts")
    if concepts is None:
        if required and equations:
            raise ValueError(
                "Every mathematical scene needs a conceptual picture before its equation"
            )
        return
    if not isinstance(concepts, list) or len(concepts) != len(equations):
        raise ValueError("Match exactly one conceptual picture to each equation")
    for eq, concept in zip(equations, concepts):
        if not isinstance(concept, dict) or concept.get("template") not in TEMPLATES:
            raise ValueError("Choose a supported conceptual picture template")
        parts = concept.get("parts")
        if not isinstance(parts, list) or len(parts) != 3:
            raise ValueError("A conceptual picture needs three meaningful parts")
        for part in parts:
            _label(part)
        symbols = concept.get("symbols")
        if not isinstance(symbols, list) or not 0 <= len(symbols) <= 8:
            raise ValueError("Map up to eight symbols onto the picture")
        if not symbols and not concept.get("fallback"):
            raise ValueError("Define the equation's symbols on its conceptual picture")
        source = re.sub(r"[\s{}]", "", eq["latex"])
        for symbol in symbols:
            _label(symbol)
            if len(symbol["en"]) > 32 or len(symbol["ja"]) > 18:
                raise ValueError(
                    f"Shorten this symbol meaning to <=32 English and <=18 Japanese characters: {symbol['en']!r} / {symbol['ja']!r}"
                )
            latex = symbol.get("latex")
            if not isinstance(latex, str) or not latex or len(latex) > 55:
                raise ValueError("Use a short exact symbol from the equation")
            if re.search(r"[=+*/()\-]|\\(?:sum|prod|frac|cdot|left|right)\b", latex):
                raise ValueError(
                    "Map one variable or named quantity per symbol key, not a composite expression or operator"
                )
            token = re.sub(r"[\s{}]", "", latex)
            searchable = (
                source
                if "\\" in token
                else re.sub(r"[\s{}]", "", re.sub(r"\\[A-Za-z]+", "", eq["latex"]))
            )
            if token not in searchable:
                raise ValueError("A concept symbol is absent from its equation")
            if type(symbol.get("part")) is not int or symbol["part"] not in (0, 1, 2):
                raise ValueError("Attach each symbol to a picture part")


def focus_count(spec):
    if spec.get("concepts"):
        return int(bool(spec.get("original_asset_id"))) + 2 * len(spec["equations"])
    return max(1, len(spec.get("nodes", [])), len(spec.get("equations", [])))


def equation_index(spec, focus):
    if spec.get("concepts"):
        offset = int(bool(spec.get("original_asset_id")))
        if not isinstance(focus, int) or not offset <= focus < focus_count(spec):
            return None
        return (focus - offset) // 2
    return max(0, focus - int(bool(spec.get("image_path"))))


def phase(spec, focus):
    index = equation_index(spec, focus) if spec.get("concepts") else None
    if index is None:
        return None
    return "symbols" if focus == formula_focus(spec, index) else "intuition"


def formula_focus(spec, equation=0):
    return int(bool(spec.get("original_asset_id"))) + equation * 2 + 1


def _part(en, ja):
    return {"en": en, "ja": ja}


def fallback(eq):
    """Recognize an actual operation before choosing a neutral last resort."""
    tex = re.sub(r"\s+", "", eq["latex"])
    if (
        "Q_" in tex
        and r"\gamma" in tex
        and "r+" in tex
        and ("^{2}" in tex or "^2" in tex)
    ):
        q = re.search(r"Q_(?:\{\\[A-Za-z]+\}|\\[A-Za-z]+)", tex)
        symbols = [
            {
                "latex": r"\gamma",
                "en": "Future discount",
                "ja": "将来の価値の割引",
                "part": 1,
            },
            {"latex": "r", "en": "Reward now", "ja": "今の報酬", "part": 1},
        ]
        if q:
            symbols.insert(
                0,
                {
                    "latex": q[0],
                    "en": "Value prediction",
                    "ja": "価値の予測",
                    "part": 0,
                },
            )
        return {
            "template": "bellman_error",
            "parts": [{"en": en, "ja": ja} for en, ja in PARTS["bellman_error"]],
            "symbols": symbols,
            "fallback": True,
        }
    if r"\omega" in tex and r"\dot{q}" in tex and "a}" in tex:
        symbols = []
        for raw, en, ja, part in [
            (r"\textbf{o}_{t}", "Observation vector", "観測ベクトル", 2),
            (r"\boldsymbol{\omega}_{t}", "Angular velocity", "角速度", 0),
            (r"\textbf{g}_{t}", "Gravity direction", "重力の方向", 0),
            (r"\textbf{c}_{t}", "Velocity command", "速度の命令", 1),
            (r"\boldsymbol{q}_{t}", "Joint angles", "関節の角度", 0),
            (r"\boldsymbol{\dot{q}}_{t}", "Joint velocity", "関節の速度", 0),
            (r"\textbf{a}_{t-1}", "Previous action", "前回の行動", 1),
        ]:
            options = [
                raw,
                raw.replace(r"\textbf", r"\mathbf"),
                raw.replace(r"\boldsymbol", r"\bm"),
            ]
            found = next((s for s in options if s in tex), None)
            if found:
                symbols.append({"latex": found, "en": en, "ja": ja, "part": part})
        return {
            "template": "state_vector",
            "parts": [{"en": en, "ja": ja} for en, ja in PARTS["state_vector"]],
            "symbols": symbols,
            "fallback": True,
        }
    return {
        "template": "relationship",
        "parts": [
            _part("Given quantities", "考える量"),
            _part("The relationship", "量の関係"),
            _part("What it tells us", "わかること"),
        ],
        "symbols": [],
        "fallback": True,
    }


def lora(eq):
    """Source-convention diagram for the familiar frozen-plus-correction relation."""
    tex = re.sub(r"\s+", "", eq["latex"]).replace("W_{0}", "W_0")
    if not all(term in tex for term in ("W_0", "BA", "x")):
        return fallback(eq)
    symbols = [
        {"latex": "W_0", "en": "Frozen weights", "ja": "固定する重み", "part": 0},
        {"latex": "x", "en": "Input features", "ja": "入力の特徴", "part": 0},
        {"latex": "A", "en": "Reduce directions", "ja": "少ない方向へ", "part": 1},
        {
            "latex": "B",
            "en": "Expand the correction",
            "ja": "補正を元の大きさへ",
            "part": 1,
        },
    ]
    if r"\alpha" in tex:
        symbols.append(
            {
                "latex": r"\alpha",
                "en": "Correction strength",
                "ja": "補正の強さ",
                "part": 1,
            }
        )
    if r"\frac{\alpha}{r}" in tex:
        symbols.append(
            {
                "latex": "r",
                "en": "Number of directions",
                "ja": "学習する方向の数",
                "part": 1,
            }
        )
    if "h=" in tex:
        symbols.append(
            {"latex": "h", "en": "Combined output", "ja": "合わせた出力", "part": 2}
        )
    return {
        "template": "parallel_paths",
        "parts": [
            _part("Keep the original path", "元の経路は固定"),
            _part("Learn a small correction", "小さな補正を学ぶ"),
            _part("Add both contributions", "二つの出力を足す"),
        ],
        "symbols": symbols,
    }


def normalize_lora_symbols(spec):
    """Independent, source-specific meanings keep A/B from being swapped by a draft."""
    changed = False
    for eq, concept in zip(spec.get("equations", []), spec.get("concepts", [])):
        canonical = lora(eq)
        if canonical["template"] != "parallel_paths":
            continue
        if concept.get("symbols") != canonical["symbols"]:
            concept["symbols"] = canonical["symbols"]
            changed = True
    if changed:
        spec["symbol_source_check"] = (
            "LoRA: A reduces k to r; B expands r to d; W0 stays fixed"
        )
    return changed


def ensure(scene, *, lora_paper=False):
    """Keep late canonical formulas and bounded fallback diagrams paired."""
    spec = scene["visual"]
    equations = spec.get("equations", [])
    if not equations:
        spec.pop("concepts", None)
        return
    previous = spec.get("concepts", [])
    if len(previous) == len(equations):
        if normalize_parts(spec):
            scene.pop("visual_ready", None)
        if lora_paper and normalize_lora_symbols(spec):
            scene.pop("visual_ready", None)
        validate(spec)
        return
    spec["concepts"] = [lora(eq) if lora_paper else fallback(eq) for eq in equations]
    validate(spec)
    scene.pop("visual_ready", None)
    scene["math_concept_policy"] = VERSION
