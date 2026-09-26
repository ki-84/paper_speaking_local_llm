"""Pinned voice roles shared by new lessons and the voice refresh worker."""

import hashlib

from . import config

HOST_VOICE = "Aiden"
GUIDE_VOICE = "Maya"
GUIDE_INSTRUCTION = (
    "An adult American woman with a warm, grounded voice and natural fluent "
    "conversation pace. Clear English words, relaxed intonation, no theatrical emphasis."
)
GUIDE_STYLE_VERSION = "maya-warm-1"


def guide_audio_key(turn):
    revision = config.manifest()["models"]["tts-design"]["revision"]
    return hashlib.sha256(
        (turn["id"] + turn["text"] + spoken_text(turn) + GUIDE_STYLE_VERSION + GUIDE_INSTRUCTION + revision).encode()
    ).hexdigest()


def spoken_text(turn):
    """A pronunciation expansion may differ from the written lesson line."""
    return turn.get("spoken_text") or turn["text"]


def guide_request(turn, output, seed):
    return {
        "text": spoken_text(turn),
        "voice": GUIDE_VOICE,
        "instruction": GUIDE_INSTRUCTION,
        "output": str(output),
        "seed": seed,
    }
