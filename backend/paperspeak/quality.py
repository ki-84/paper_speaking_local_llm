from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from num2words import num2words


class QualityHold(ValueError):
    """A completed quality check needs attention, not an identical network retry."""


def english_only(text):
    return not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text)


def speech_context(title, glossary, turns=()):
    """Domain vocabulary for ASR, never the target sentence or sample answer."""
    title = re.sub(r"\s+", " ", title).strip()[:180]
    terms = [str(g["term"])[:40] for g in glossary if g.get("term")][:20]
    acronyms = [
        term
        for t in turns
        for term in re.findall(r"(?<!\w)[A-Z][A-Z0-9-]{1,}(?!\w)", t.get("text", ""))
    ]
    terms = list(dict.fromkeys(terms + acronyms))[:40]
    return f"This is a discussion of {title}. Vocabulary: {', '.join(terms)}."


def dialogue_for_model(turns):
    # Audio settings, wave paths and hundreds of timestamps are not teaching
    # evidence and can otherwise exhaust the language model's context window.
    keys = {
        "id",
        "speaker",
        "text",
        "kind",
        "source_ids",
        "numeric_visual_check_required",
        "visual",
    }
    return [{k: v for k, v in turn.items() if k in keys} for turn in turns]


def numbers(text):
    values = set()
    for token in re.findall(
        r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])", text
    ):
        try:
            values.add(str(Decimal(token.replace(",", "")).normalize()))
        except InvalidOperation:
            pass
    return values


def split_spoken_turns(turns):
    """Keep generated wording, evidence and visual cues; split clear sentence boundaries.

    Abbreviations stay untouched for the normal validation/rewriting step.
    This runs before audio or translation, never on a published recording.
    """
    if not isinstance(turns, list):
        return turns
    result = []
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("text", ""), str):
            raise ValueError("Each spoken turn needs a text field.")
        text = turn.get("text", "")
        pieces, start = [], 0
        for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", text):
            before = text[:match.start()]
            if re.search(r"\b(?:[A-Z]\.)+$|\b(?:Dr|Mr|Mrs|Ms|Prof|Fig|Eq|vs|al)\.$|\b(?:e\.g|i\.e)\.$", before):
                continue
            pieces.append(text[start:match.start()].strip())
            start = match.end()
        pieces.append(text[start:].strip())
        for i, piece in enumerate(pieces):
            item = turn | {"text": piece}
            if i and "id" in item:
                item["id"] = None
            result.append(item)
    return result


def validate_turns(turns, sources):
    errors = []
    lookup = {s["id"]: s for s in sources}
    if not turns:
        return ["The chapter is empty."]
    for i, t in enumerate(turns):
        text = t.get("text", "").strip()
        label = f"Sentence {i + 1}"
        if not text or not english_only(text):
            errors.append(f"{label}: use English only.")
        if len(re.findall(r"[.!?]+[\"']?\s+(?=[A-Z])", text)) > 0:
            errors.append(f"{label}: use one sentence per turn.")
        if len(text.split()) > 28:
            errors.append(f"{label}: split this into shorter sentences.")
        if t.get("speaker") not in {"host", "guide"}:
            errors.append(f"{label}: choose host or guide.")
        kind = t.get("kind", "paper")
        if kind not in {"paper", "background", "example", "question"}:
            errors.append(f"{label}: invalid kind.")
        refs = t.get("source_ids", [])
        if any(r not in lookup for r in refs):
            errors.append(f"{label}: unknown source reference.")
        if kind == "paper":
            if not refs:
                errors.append(f"{label}: cite evidence for this paper claim.")
            evidence = "\n".join(lookup[r]["data"]["text"] for r in refs if r in lookup)
            missing_numbers = numbers(text) - numbers(evidence)
            if missing_numbers:
                visual = any(
                    lookup[r]["data"].get("image_path") for r in refs if r in lookup
                )
                if visual:
                    t["numeric_visual_check_required"] = sorted(missing_numbers)
                else:
                    errors.append(f"{label}: a number is not in the cited evidence.")
    return errors


CONTRACTIONS = {
    "can't": "cannot",
    "won't": "will not",
    "don't": "do not",
    "doesn't": "does not",
    "isn't": "is not",
    "aren't": "are not",
    "it's": "it is",
    "that's": "that is",
    "we're": "we are",
    "they're": "they are",
    "i'm": "i am",
    "you're": "you are",
    "we'll": "we will",
    "they'll": "they will",
    "let's": "let us",
}

NUMBER_WORDS = set(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion trillion point percent half quarter first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth thousandth millionth billionth trillionth minus negative plus".split()
)


def changed_spoken_number(diff):
    return any(
        w["kind"] != "match"
        and (w["expected"] in NUMBER_WORDS or w["heard"] in NUMBER_WORDS)
        for w in diff["words"]
    )


def critical_speech_change(reference, diff, terms):
    protected = {w for term in terms for w in words(term)}
    protected.update(re.findall(r"\b[b-hj-z]\b", reference.lower()))
    for symbol in re.findall(r"\b[A-Z][A-Z0-9-]*\b", reference):
        protected.update(words(symbol))
    return (
        bool(numbers(reference))
        or changed_spoken_number(diff)
        or any(
            w["kind"] != "match" and w["expected"] in protected for w in diff["words"]
        )
    )


def words(text):
    text = text.lower().replace("’", "'")
    # ASR commonly inserts a word break in method variants and reads an
    # equals sign aloud. These changes affect spelling, not the measured fact.
    text = re.sub(r"\badapter[-‐‑ ]?([lh])\b", r"adapter \1", text)
    text = re.sub(r"\bfine[-‐‑ ]?tuned(?=\s+lora\b)", "fine tune", text)
    text = re.sub(r"\bbeats\b", "beat", text)
    text = text.replace("=", " equals ")
    # These ML terms have the same pronunciation with or without a hyphen.
    # Do not remove arbitrary hyphens: re-sign and resign are different words.
    text = re.sub(r"\bpre[-‐‑ ]+(trained|training)\b", lambda m: "pre" + m[1], text)
    # Written math names and ordinary compound spelling can differ from the
    # ASR transcript without any change in the spoken audio.
    text = re.sub(r"\bd[_ -]?model\b", "d model", text)
    text = re.sub(r"\bd[_ -]?ff\b", "dff", text)
    text = re.sub(r"\bfeed[-‐‑ ]?forward\b", "feedforward", text)
    text = re.sub(r"\bnon[-‐‑ ]?linear\b", "nonlinear", text)
    text = re.sub(r"\bhalf[-‐‑ ]?way\b", "halfway", text)
    # These benchmark names are often transcribed with an audible word break.
    # Join only these known names; other task names and numbers stay distinct.
    text = re.sub(r"\bmulti[-‐‑ ]*nli\b", "multinli", text)
    text = re.sub(r"\bwiki[-‐‑ ]*sql\b", "wikisql", text)
    text = re.sub(r"\bprefix[-‐‑ ]*(embed|layer)\b", r"prefix\1", text)
    text = re.sub(
        r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten)[-‐‑ ]?fold\b",
        lambda m: m[1] + " fold",
        text,
    )
    for a, b in CONTRACTIONS.items():
        text = re.sub(r"\b" + re.escape(a) + r"\b", b, text)
    text = text.replace("%", " percent ").replace("&", " and ")
    # Expand an ordinal before the cardinal-number pass: 11th is eleventh,
    # never the two tokens "eleven th". Its numerical value remains protected.
    text = re.sub(
        r"\b(\d+(?:,\d{3})*)(?:st|nd|rd|th)\b",
        lambda m: " " + num2words(m[1].replace(",", ""), to="ordinal") + " ",
        text,
    )

    def expand(m):
        try:
            return " " + num2words(m.group().replace(",", "")) + " "
        except Exception:
            return m.group()

    text = re.sub(r"\d+(?:,\d{3})*(?:\.\d+)?", expand, text)
    text = re.sub(
        r"\b(hundred|thousand|million|billion|trillion)\s+and\s+"
        r"(?=(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)\b)",
        r"\1 ", text,
    )
    return re.findall(r"[a-z]+(?:'[a-z]+)?", text)


def word_diff(reference, heard):
    # "et al." in a written citation is pronounced "et all"; ASR often
    # writes the equally sounding English words "at all".
    if re.search(r"\bet\s+al\b", reference, flags=re.I):
        heard = re.sub(r"\b(?:et|at)[,\s]+(?:al|all)\b", "et al", heard, flags=re.I)
    a, b = words(reference), words(heard)
    # ASR can spell an initialism with spaces (SQL -> S Q L). Merge only
    # isolated letters for initialisms actually present in either text. Number
    # words stay separate, so this cannot turn "three" into "two" in NL2SQL.
    initialisms = {
        m.lower()
        for text in (reference, heard)
        for m in re.findall(r"(?<![A-Za-z])[A-Z]{2,}(?![A-Za-z])", text)
        if m.lower() not in NUMBER_WORDS
    }
    # A spoken variable Y is acoustically the word "why". Restrict these
    # spelling equivalents to letters present in the reference; do not fold
    # number homophones such as two/to or four/for.
    letter_forms = {
        "a": ("ay",),
        "b": ("bee", "be"),
        "c": ("see", "sea"),
        "d": ("dee",),
        "e": ("ee",),
        "f": ("ef",),
        "g": ("gee",),
        "h": ("aitch",),
        "i": ("eye",),
        "j": ("jay",),
        "k": ("kay",),
        "l": ("ell", "el"),
        "m": ("em",),
        "n": ("en",),
        "o": ("oh",),
        "p": ("pee",),
        "q": ("cue", "queue"),
        "r": ("are",),
        "s": ("ess", "es"),
        "t": ("tee", "tea"),
        "u": ("you",),
        "v": ("vee",),
        "x": ("ex",),
        "y": ("why",),
        "z": ("zee",),
    }
    reference_letters = {w for w in a if len(w) == 1} | {
        c for term in initialisms for c in term
    }
    aliases = {
        form: letter
        for letter, forms in letter_forms.items()
        if letter in reference_letters
        for form in forms
    }
    a = [aliases.get(w, w) for w in a]
    b = [aliases.get(w, w) for w in b]

    def merge_initialisms(tokens):
        merged = []
        i = 0
        while i < len(tokens):
            match = next(
                (
                    term
                    for term in sorted(initialisms, key=len, reverse=True)
                    if tokens[i : i + len(term)] == list(term)
                ),
                None,
            )
            if match:
                merged.append(match)
                i += len(match)
            else:
                merged.append(tokens[i])
                i += 1
        return merged

    a, b = merge_initialisms(a), merge_initialisms(b)
    # True Levenshtein alignment, not SequenceMatcher's heuristic matching blocks.
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(
                d[i - 1][j] + 1,
                d[i][j - 1] + 1,
                d[i - 1][j - 1] + (a[i - 1] != b[j - 1]),
            )
    i, j = len(a), len(b)
    diff = []
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            diff.append(
                {
                    "expected": a[i - 1],
                    "heard": b[j - 1],
                    "kind": "match" if a[i - 1] == b[j - 1] else "replace",
                }
            )
            i -= 1
            j -= 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            diff.append({"expected": a[i - 1], "heard": "", "kind": "missing"})
            i -= 1
        else:
            diff.append({"expected": "", "heard": b[j - 1], "kind": "extra"})
            j -= 1
    return {
        "word_match": round(100 * max(0, 1 - d[-1][-1] / max(1, len(a))))
        if a and b
        else None,
        "wer": d[-1][-1] / max(1, len(a)),
        "words": list(reversed(diff)),
    }


def speech_match(reference, transcript, terms=()):
    """Return the normalized ASR difference and whether it is safe to publish."""
    diff = word_diff(reference, transcript)
    acceptable = diff["wer"] <= 0.08 and bool(transcript.strip())
    if diff["wer"] and critical_speech_change(reference, diff, terms):
        acceptable = False
    return diff, acceptable
