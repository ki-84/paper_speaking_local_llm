from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("PAPERSPEAK_DATA", ROOT / "data")).resolve()
MODELS = ROOT / "models"
LLAMA_PORT = int(os.environ.get("PAPERSPEAK_LLAMA_PORT", "8191"))
API_PORT = int(os.environ.get("PAPERSPEAK_PORT", "8190"))
LLAMA_BIN = Path(os.environ.get("PAPERSPEAK_LLAMA_BIN", ROOT / ".tools/llama-server"))
DEFAULTS = {
    "nightly_video_enabled": False,
    "nightly_video_hour": 2,
    "nightly_video_minute": 0,
    "nightly_video_categories": ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO"],
    "discovery_enabled": True,
    "schedule_hour": 3,
    "schedule_minute": 0,
    "timezone": "Asia/Tokyo",
    "daily_limit": 1,
    "categories": ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.SD", "stat.ML"],
    "interests": "Learn important ideas across AI, including language, vision, audio, robotics and learning.",
    "model_profile": "qwen-q8",
    "speed": 1.0,
    "subtitles": True,
    "role": "both",
    "max_auto_backlog": 1,
    "discovery_last_success": None,
    "pronunciation_calibrated": False,
    "youtube_auto_upload": False,
}


def init_dirs():
    for name in (
        "papers",
        "audio",
        "visuals",
        "videos",
        "thumbnails",
        "recordings",
        "jobs",
        "logs",
        "cache",
        "evaluation",
        "tls",
    ):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    DATA.chmod(0o700)


def manifest():
    path = ROOT / "models.lock.json"
    return (
        json.loads(path.read_text()) if path.exists() else {"models": {}, "runtime": {}}
    )


def safe_path(relative: str) -> Path:
    path = (DATA / relative).resolve()
    if not path.is_relative_to(DATA):
        raise ValueError("Invalid file path")
    return path
