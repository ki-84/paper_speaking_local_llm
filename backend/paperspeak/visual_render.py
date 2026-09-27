"""A small, deterministic bilingual diagram renderer; model output is data only."""
from __future__ import annotations

import hashlib
import html
import re
import subprocess
import unicodedata

from . import config
from .translation import numeric_values

RENDER_VERSION = 2


def check_pair(en, ja, limit=240):
    if not isinstance(en, str) or not isinstance(ja, str) or not en.strip() or not ja.strip():
        raise ValueError("A visual label needs English and Japanese.")
    if len(en) > limit or len(ja) > limit or re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", en):
        raise ValueError("A visual label is too long or its English is not English.")
    if not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", ja):
        raise ValueError("A visual label needs a Japanese explanation.")
    if numeric_values(en) != numeric_values(ja):
        raise ValueError("A visual translation changed a number.")


def validate_spec(spec, source_ids):
    if not isinstance(spec, dict):
        raise ValueError("A diagram must be a structured object.")
    if spec.get("layout") not in {"flow", "structure", "comparison", "matrix", "example"}:
        raise ValueError("Unsupported diagram layout.")
    if spec.get("kind") not in {"teaching", "example"}:
        raise ValueError("Mark a diagram as teaching or a hypothetical example.")
    for key in ("title", "description"):
        check_pair(spec.get(key + "_en"), spec.get(key + "_ja"), 140 if key == "description" else 72)
    if len(spec["title_en"]) > 60:
        raise ValueError("Keep the English diagram title under 60 characters.")
    refs = spec.get("source_ids", [])
    if not isinstance(refs, list) or not refs or any(not isinstance(s, str) for s in refs) or not set(refs) <= set(source_ids):
        raise ValueError("A teaching diagram must link to original paper evidence.")
    nodes = spec.get("nodes", [])
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 8:
        raise ValueError("Use two to eight short diagram nodes.")
    ids = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("A diagram node must be a structured object.")
        key = node.get("id", "")
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,20}", key)
            or key in ids or key == "arrow" or key.startswith("arrow_")):
            raise ValueError("Diagram nodes need distinct short IDs.")
        ids.add(key)
        check_pair(node.get("en"), node.get("ja"), 60)
        if node.get("matrix") is not None:
            matrix = node["matrix"]
            if (not isinstance(matrix, list) or not 1 <= len(matrix) <= 4
                or any(not isinstance(row, list) or not 1 <= len(row) <= 4 for row in matrix)
                or len({len(row) for row in matrix}) != 1
                or any(not isinstance(cell, (str, int, float)) or isinstance(cell, bool)
                       or len(str(cell)) > 8 for row in matrix for cell in row)):
                raise ValueError("Use a small rectangular matrix with short cells.")
    edges = spec.get("edges", [])
    if not isinstance(edges, list) or len(edges) > 10:
        raise ValueError("Too many diagram arrows.")
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("A diagram arrow must be a structured object.")
        if edge.get("from") not in ids or edge.get("to") not in ids or edge["from"] == edge["to"]:
            raise ValueError("An arrow refers to an unknown node.")
        if edge.get("en") or edge.get("ja"):
            check_pair(edge.get("en"), edge.get("ja"), 16)


def wrap(text, width):
    lines, line, size = [], "", 0
    # Break English at spaces, Japanese at character boundaries.
    chunks = list(text) if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text) else text.split(" ")
    japanese = chunks == list(text)
    for chunk in chunks:
        part = chunk if japanese or not line else " " + chunk
        n = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in part)
        if line and size + n > width:
            lines.append(line)
            line, size = chunk, n - (0 if japanese else 1)
        else:
            line += part
            size += n
    if line:
        lines.append(line)
    return lines


def text_block(text, x, y, width, size, color="#233b32", anchor="start"):
    lines = wrap(text, width)
    line_height = int(size * 1.5 + 0.5)  # Noto CJK's font box is taller than its Latin glyphs.
    return "".join(
        f'<text x="{x}" y="{y+i*line_height}" font-size="{size}" text-anchor="{anchor}" fill="{color}">{html.escape(line)}</text>'
        for i, line in enumerate(lines)
    ), len(lines) * line_height


def svg_for(spec):
    nodes = spec["nodes"]
    columns = min(3, len(nodes))
    rows = (len(nodes) + columns - 1) // columns
    width, height = 1320, 270 + rows * 330
    boxes = {n["id"]: [65 + (i % columns) * 425, 180 + (i // columns) * 330, 340, 240]
             for i, n in enumerate(nodes)}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<style>text{font-family:"Noto Sans CJK JP",sans-serif}</style>',
           '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="#507b6c"/></marker></defs>',
           f'<rect width="{width}" height="{height}" rx="16" fill="#f8faf4"/>']
    badge = "Hypothetical example · 仮の例" if spec["kind"] == "example" else "Learning diagram · 説明用の補助図"
    out.append(text_block(badge, 65, 35, 100, 17, "#507b6c")[0])
    out.append(text_block(spec["title_en"], 65, 82, 70, 31)[0])
    out.append(text_block(spec["title_ja"], 65, 133, 90, 23)[0])
    arrow_regions = []
    for ei, edge in enumerate(spec.get("edges", [])):
        x, y, w, h = boxes[edge["from"]]
        tx, ty, tw, th = boxes[edge["to"]]
        if y == ty and abs(tx-x) == 425:
            right = tx > x
            sx, ex, cy = (x+w, tx, y+h/2) if right else (x, tx+tw, y+h/2)
            path = f"M {sx} {cy} L {ex} {cy}"
            lx, ly = (sx+ex)/2, cy+30
        elif y == ty:
            sx, ex, sy = x+w/2, tx+tw/2, y+h
            lane = sy+30+(ei % 3)*15
            path = f"M {sx} {sy} L {sx} {lane} L {ex} {lane} L {ex} {sy}"
            lx, ly = (sx+ex)/2, lane+22
        elif ty > y and ty-y == 330:
            sx, ex, sy, ey = x+w/2, tx+tw/2, y+h, ty
            middle = sy + 45
            path = f"M {sx} {sy} L {sx} {middle} L {ex} {middle} L {ex} {ey}"
            lx, ly = (sx+ex)/2, middle-12
        else:
            sx, ex, sy, ey = x+w/2, tx+tw/2, y+h, ty
            lane, upper, gutter = sy+30+(ei % 3)*12, ty-28, 1290+(ei % 3)*8
            path = f"M {sx} {sy} L {sx} {lane} L {gutter} {lane} L {gutter} {upper} L {ex} {upper} L {ex} {ey}"
            lx, ly = sx+75, lane+20
        out.append(f'<path d="{path}" fill="none" stroke="#507b6c" stroke-width="3" marker-end="url(#arrow)"/>')
        values = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", path)]
        points = [[values[i]/width, values[i+1]/height] for i in range(0, len(values), 2)]
        labels = {n["id"]: n for n in nodes}
        arrow_regions.append({"id": f"arrow_{ei+1}",
                              "label_en": edge.get("en") or f"Arrow: {labels[edge['from']]['en']} to {labels[edge['to']]['en']}",
                              "label_ja": edge.get("ja") or f"矢印：{labels[edge['from']]['ja']}から{labels[edge['to']]['ja']}へ",
                              "points": points})
        if edge.get("en"):
            out.append(text_block(edge["en"], lx, ly, 20, 14, anchor="middle")[0])
            out.append(text_block(edge["ja"], lx, ly+20, 20, 13, anchor="middle")[0])
    regions = []
    for node in nodes:
        x, y, w, h = boxes[node["id"]]
        out.append(f'<g id="{node["id"]}" data-node="true"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#e8f0df" stroke="#abc1a1" stroke-width="2"/>')
        english, used = text_block(node["en"], x+20, y+36, 24, 24)
        out.append(english)
        japanese, ja_used = text_block(node["ja"], x+20, y+43+used, 29, 20)
        out.append(japanese)
        if node.get("matrix") is not None:
            matrix = node["matrix"]
            matrix_y = y + 53 + used + ja_used
            if matrix_y + len(matrix)*24 > y+h-12:
                raise ValueError("Matrix labels need to be shorter to fit the diagram.")
            for ri, row in enumerate(matrix):
                for ci, cell in enumerate(row):
                    out.append(text_block(str(cell), x+45+ci*62, matrix_y+ri*24, 10, 18)[0])
        if 43 + used + ja_used > h - 5:
            raise ValueError("A diagram label does not fit; shorten the label.")
        out.append('</g>')
        regions.append({"id": node["id"], "label_en": node["en"], "label_ja": node["ja"],
                        "box": [x/width, y/height, w/width, h/height]})
    out.append(text_block("PaperSpeak · Read the linked paper for evidence. / 根拠は出典の論文を確認してください。", 65, height-24, 125, 15, "#617566")[0])
    out.append('</svg>')
    return "".join(out), regions + arrow_regions


def render(spec):
    svg, regions = svg_for(spec)
    key = hashlib.sha256((str(RENDER_VERSION)+svg).encode()).hexdigest()
    folder = config.DATA / "visuals"
    folder.mkdir(exist_ok=True)
    svg_path, png_path = folder / (key+".svg"), folder / (key+".png")
    svg_tmp = svg_path.with_suffix(".partial.svg")
    svg_tmp.write_text(svg)
    svg_tmp.replace(svg_path)
    if not png_path.exists():
        node = config.ROOT / ".tools/node-v24.21.0-linux-x64/bin/node"
        png_tmp = png_path.with_suffix(".partial.png")
        try:
            result = subprocess.run([str(node), str(config.ROOT / "scripts/render_visual.mjs"), str(svg_path), str(png_tmp)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise ValueError("Diagram rendering failed: " + result.stderr[-800:])
            png_tmp.replace(png_path)
        finally:
            png_tmp.unlink(missing_ok=True)
    return {"image_path": str(png_path.relative_to(config.DATA)),
            "svg_path": str(svg_path.relative_to(config.DATA)), "regions": regions,
            "sha256": hashlib.sha256(png_path.read_bytes()).hexdigest(), "render_version": RENDER_VERSION}
