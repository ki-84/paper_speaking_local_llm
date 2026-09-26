"""Basic script checks for Japanese paper-list copy."""

import re
from decimal import Decimal

from . import translation

KANA = re.compile(r"[ぁ-ゟァ-ヿ]")
# These simplified Chinese forms are distinct from their usual Japanese forms.
SIMPLIFIED = re.compile(r"[计较驱滤训验论应过这让们语发实术统带从经输为]")
EDITOR_TERMS = (
    "例: grasping=把持、grasp=つかむ動作／把持、robotic=ロボットの、"
    "robotic grasping=ロボットによる把持、one-grasp adaptation=1回の把持例からの適応、"
    "manipulation=操作、hand-eye coordination=視覚と手の協調、"
    "force closure=力閉鎖。文脈に合う自然な日本語に直してください。"
)
WRITTEN_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
}
WRITTEN_NUMBER_PATTERN = re.compile(r"\b(?:" + "|".join(WRITTEN_NUMBERS) + r")\b", re.I)
ABBREVIATED_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)([kKMB])\b")
ABBREVIATED_NUMBER_UNITS = {"k": 1000, "K": 1000, "M": 1_000_000, "B": 1_000_000_000}
SUBSCRIPT_NUMBER_PATTERN = re.compile(r"_(\d+)\b")


def polish(value):
    """Correct a few known mistransliterations without changing paper claims."""
    if not isinstance(value, str):
        return value
    return (
        value.replace("ロボティック・グレイピング", "ロボットによる把持")
        .replace("ロボティックグレイピング", "ロボットによる把持")
        .replace("グレイピング", "把持")
        .replace("グラスピング", "把持")
        .replace("成功把持", "把持成功")
        .replace("デモレーション", "デモンストレーション")
        .replace("点対準", "点ごとの位置合わせ")
        .replace(" motivate する", "を導く")
    )


def readable(value):
    return (
        isinstance(value, str)
        and bool(KANA.search(value))
        and not SIMPLIFIED.search(value)
    )


def source_numbers(text):
    """Arabic values plus English numbers a Japanese summary may write as digits."""
    return translation.numeric_values(text) | {
        Decimal(WRITTEN_NUMBERS[match.group().lower()])
        for match in WRITTEN_NUMBER_PATTERN.finditer(text)
    } | {
        Decimal(match.group(1)) * ABBREVIATED_NUMBER_UNITS[match.group(2)]
        for match in ABBREVIATED_NUMBER_PATTERN.finditer(text)
    } | {
        Decimal(match.group(1)) for match in SUBSCRIPT_NUMBER_PATTERN.finditer(text)
    }
