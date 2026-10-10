"""Local character art, readable bilingual captions, and WAV-driven video animation."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import wave
from pathlib import Path

import numpy as np

from . import config

ROOT = config.ROOT / "assets" / "video"
CAPTION_WIDTH = 1240  # leaves the two 225 px portraits clear, with a safety margin
CAPTION_TOP = 835
CAPTION_HEIGHT = 225
EN_SIZES = (45, 43, 41, 39, 37, 35, 33, 31, 29, 27, 25)
JA_SIZES = (37, 35, 33, 31, 29, 27, 25)
CAPTION_POLICY = "adaptive-bilingual-pages-1"


def character_manifest():
    """Fingerprint every design input so old video revisions remain immutable."""
    layout = json.loads((ROOT / "characters.json").read_text(encoding="utf-8"))
    return {
        "layout": layout,
        "asset_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("characters.json", layout["guide"]["sprite"], layout["host"]["sprite"])
        },
        "animation": {"rms_window_ms": 80, "states": ("closed", "half", "open"),
                      "blink_interval_s": 5.3},
    }


def _char_width(char: str, size: int) -> float:
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return size
    if char.isspace():
        return size * .35
    if char in "ilI1.,:;!'|`":
        return size * .33
    if char in "MW@%&":
        return size * .86
    if char in "-_/()[]{}?":
        return size * .43
    return size * .65


def _width(value: str, size: int) -> float:
    return sum(_char_width(char, size) for char in value)


def _wrap_english(value: str, size: int) -> list[str]:
    result, line = [], ""
    for word in re.findall(r"\S+", value):
        candidate = (line + " " + word).strip()
        if _width(candidate, size) <= CAPTION_WIDTH:
            line = candidate
            continue
        if line:
            result.append(line)
            line = ""
        # Very long paper identifiers cannot be silently cropped.
        for char in word:
            if line and _width(line + char, size) > CAPTION_WIDTH:
                result.append(line)
                line = ""
            line += char
    if line:
        result.append(line)
    return result


def _wrap_japanese(value: str, size: int) -> list[str]:
    value = value.replace("\n", " ")
    result = []
    while value:
        end = 0
        while end < len(value) and _width(value[:end + 1], size) <= CAPTION_WIDTH:
            end += 1
        if end == len(value):
            result.append(value)
            break
        if end == 0:
            raise ValueError("One subtitle character is wider than the caption area.")
        # Prefer a natural clause ending near the current width limit.
        breaks = [pos + 1 for pos, char in enumerate(value[:end])
                  if char in "。、！？,.;:」』" and pos + 1 >= end * .55]
        if breaks:
            end = breaks[-1]
        while end > 1 and value[end] in "。、！？,.;:」』）)]":
            end -= 1
        def same_word(a: str, b: str) -> bool:
            # Hiragana often surrounds a loanword; treating both scripts as
            # one word moved the boundary into ビデオゲーム instead of before it.
            return (0x30a0 <= ord(a) <= 0x30ff and 0x30a0 <= ord(b) <= 0x30ff) or (
                a.isascii() and b.isascii() and a.isalnum() and b.isalnum())
        original_end = end
        while end > 0 and end < len(value) and same_word(value[end - 1], value[end]):
            end -= 1
        if end == 0:
            # An identifier longer than a whole line must still be displayed.
            end = original_end
        if value[end] in "はがをにでとへもの" and _width(value[:end + 1], size) <= CAPTION_WIDTH:
            end += 1
        result.append(value[:end])
        value = value[end:]
    return result


def layout_captions(english: str, japanese: str, *, languages=None) -> dict:
    """Choose a bounded, untruncated two-language layout for the 270 px footer."""
    if languages == ["ja"]:
        for max_lines in (2, 3):
            for size in (64, 62, 60, 58, 56, 54):
                lines = _wrap_japanese(japanese, size)
                height = math.ceil(len(lines) * size * 1.17)
                if len(lines) <= max_lines and height <= CAPTION_HEIGHT:
                    return {"english": [], "japanese": lines, "en_size": 0, "ja_size": size,
                            "en_top": CAPTION_TOP, "ja_top": CAPTION_TOP + (CAPTION_HEIGHT - height) // 2}
        raise ValueError("Split Japanese captions into meaningful timed clauses")
    candidates = []
    for en_size in EN_SIZES:
        en = _wrap_english(english, en_size)
        if len(en) > 3:
            continue
        for ja_size in JA_SIZES:
            ja = _wrap_japanese(japanese, ja_size)
            if len(ja) > 3:
                continue
            en_height = math.ceil(len(en) * en_size * 1.14)
            ja_height = math.ceil(len(ja) * ja_size * 1.17)
            if en_height + ja_height + 12 > CAPTION_HEIGHT:
                continue
            candidates.append((en_size + ja_size, en_size, ja_size, en, ja, en_height))
    if not candidates:
        raise ValueError(
            "The English and Japanese subtitles do not fit the video footer."
        )
    _, en_size, ja_size, en, ja, en_height = max(candidates)
    return {
        "english": en,
        "japanese": ja,
        "en_size": en_size,
        "ja_size": ja_size,
        "en_top": CAPTION_TOP,
        "ja_top": CAPTION_TOP + en_height + 12,
    }


def _bisect_caption(text: str, japanese: bool) -> tuple[str, str]:
    if len(text) < 2:
        return text, ""
    midpoint = len(text) / 2
    if japanese:
        clauses = [m.end() for m in re.finditer(r"[。、！？,;:.!?]", text)]
        boundaries = [
            i
            for i in range(1, len(text))
            if not (
                (
                    0x30A0 <= ord(text[i - 1]) <= 0x30FF
                    and 0x30A0 <= ord(text[i]) <= 0x30FF
                )
                or (
                    text[i - 1].isascii()
                    and text[i].isascii()
                    and text[i - 1].isalnum()
                    and text[i].isalnum()
                )
            )
        ]
    else:
        clauses = [m.end() for m in re.finditer(r"[,;:.!?]\s+", text)]
        boundaries = [m.end() for m in re.finditer(r"\s+", text)]
    natural = [i for i in clauses if len(text) * 0.3 <= i <= len(text) * 0.7]
    choices = natural or [i for i in boundaries if 0 < i < len(text)]
    cut = (
        min(choices, key=lambda i: abs(i - midpoint))
        if choices
        else max(1, int(midpoint))
    )
    return text[:cut], text[cut:]


def caption_pages(english: str, japanese: str, *, languages=None) -> list[dict]:
    """Split overflow by clauses; keep every character and readable font bounds."""
    pending = [(english, japanese)]
    pages = []
    while pending:
        en, ja = pending.pop()
        try:
            layout = layout_captions(en, ja, languages=languages)
        except ValueError:
            en_a, en_b = _bisect_caption(en, False)
            ja_a, ja_b = _bisect_caption(ja, True)
            if (en_a, ja_a) == (en, ja):
                raise
            pending.extend([(en_b, ja_b), (en_a, ja_a)])
        else:
            pages.append({"english": en, "japanese": ja, "layout": layout})
    return pages


def mouth_states(path: Path, expected_frames: int) -> list[tuple[int, int, int]]:
    """Return coalesced (start sample, end sample, openness) from checked 24 kHz WAV."""
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 24000, "NONE"):
            raise ValueError("Character animation needs a checked 24 kHz mono WAV.")
        if wav.getnframes() != expected_frames:
            raise ValueError("The spoken sentence changed during video export.")
        samples = np.frombuffer(wav.readframes(expected_frames), dtype="<i2")
    windows = [(start, min(start + 1920, expected_frames))
               for start in range(0, expected_frames, 1920)]
    levels = [float(np.sqrt(np.mean(samples[start:end].astype(np.float32) ** 2)))
              for start, end in windows]
    if not levels:
        return []
    speech = float(np.percentile(levels, 70))
    gate = max(150.0, speech * .17)
    wide = max(gate * 2.1, speech * .75)
    states = [0 if rms < gate else (2 if rms >= wide else 1) for rms in levels]
    # A single noisy 80 ms window should not snap the mouth open in a pause.
    for index in range(1, len(states) - 1):
        if states[index - 1] == states[index + 1] == 0 and states[index] != 0:
            states[index] = 0
    result = []
    current, start = states[0], windows[0][0]
    for index in range(1, len(states)):
        if states[index] != current:
            result.append((start, windows[index][0], current))
            current, start = states[index], windows[index][0]
    result.append((start, expected_frames, current))
    return result


def _ass_color(rgb: str) -> str:
    return f"&H{rgb[5:7]}{rgb[3:5]}{rgb[1:3]}&"


def _rect(start_ms: int, end_ms: int, layer: int, x: float, y: float,
          width: float, height: float, color: str) -> str:
    def at(ms):
        centiseconds = round(ms / 10)
        h, remain = divmod(centiseconds, 360000)
        m, remain = divmod(remain, 6000)
        s, cs = divmod(remain, 100)
        return f"{h}:{m:02}:{s:02}.{cs:02}"
    x, y, width, height = (round(value) for value in (x, y, width, height))
    drawing = f"m 0 0 l {width} 0 {width} {height} 0 {height}"
    return (f"Dialogue: {layer},{at(start_ms)},{at(end_ms)},Pixel,,0,0,0,,"
            f"{{\\an7\\pos({x},{y})\\p1\\1c{_ass_color(color)}\\bord0\\shad0}}{drawing}")


def _position(config_: dict, role: str) -> tuple[float, float, float]:
    layout = config_["layout"]
    x = (layout["avatar_inset"] if role == "guide" else
         1920 - layout["avatar_inset"] - layout["avatar_width"])
    x += (layout["avatar_width"] - layout["portrait_size"]) / 2
    # Absolutely positioned children start below the footer's top border.
    y = layout["footer_top"] + layout["footer_border"] + layout["avatar_top"]
    return x, y, layout["portrait_size"] / 64


def mouth_events(role: str, path: Path, frames: int, offset_frames: int, config_: dict) -> list[str]:
    person = config_[role]
    base_x, base_y, scale = _position(config_, role)
    bx, by, bw, bh = person["mouth_box"]
    x, y = base_x + bx * scale, base_y + by * scale
    result = []
    for first, last, state in mouth_states(path, frames):
        if state == 0:
            continue
        start, end = round((offset_frames + first) / 24), round((offset_frames + last) / 24)
        if end <= start:
            continue
        result.append(_rect(start, end, 2, x, y, bw * scale, bh * scale, person["skin"]))
        result.append(_rect(start, end, 3, x + scale, y + (1.2 if state == 2 else 1.9) * scale,
                            (bw - 2) * scale, (2.3 if state == 2 else 1.1) * scale, person["mouth"]))
    return result


def highlight_events(role: str, start_ms: int, end_ms: int, config_: dict) -> list[str]:
    layout = config_["layout"]
    x = layout["avatar_inset"] if role == "guide" else 1920 - layout["avatar_inset"] - layout["avatar_width"]
    return [_rect(start_ms, end_ms, 1, x + 17, layout["name_top"], 190, 3, "#f2c77b")]


def blink_events(duration_ms: int, config_: dict) -> list[str]:
    result = []
    for role, delay in (("guide", 1100), ("host", 3600)):
        person = config_[role]
        bx, by, scale = _position(config_, role)
        start = delay
        index = 0
        while start < duration_ms:
            end = min(start + 140, duration_ms)
            for eye_x, eye_y in person["eyes"]:
                x, y = bx + eye_x * scale, by + eye_y * scale
                result.append(_rect(start, end, 4, x, y, 3 * scale, 3 * scale, person["skin"]))
                result.append(_rect(start, end, 5, x, y + scale, 3 * scale, scale, person["mouth"]))
            index += 1
            start += 5300 + (index % 3 - 1) * 350
    return result
