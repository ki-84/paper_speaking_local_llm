#!/usr/bin/env python3
"""Pin, download and verify model artifacts; never download during inference."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import urllib.request

from huggingface_hub import hf_hub_download

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPECS = {
    "image": (
        "black-forest-labs/FLUX.2-klein-base-4B",
        "a3b4f4849157f664bdbc776fd7453c2783562f4d",
    ),
    "phoneme-timit": (
        "vitouphy/wav2vec2-xls-r-300m-timit-phoneme",
        "efb7ae9b88f13db0d42eac8cedbba19739e2a278",
    ),
    "tts": (
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "0c0e3051f131929182e2c023b9537f8b1c68adfe",
    ),
    "tts-design": (
        "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "5ecdb67327fd37bb2e042aab12ff7391903235d3",
    ),
    "asr": ("Qwen/Qwen3-ASR-1.7B", "7278e1e70fe206f11671096ffdd38061171dd6e5"),
    "aligner": (
        "Qwen/Qwen3-ForcedAligner-0.6B",
        "c7cbfc2048c462b0d63a45797104fc9db3ad62b7",
    ),
    "phoneme": (
        "facebook/wav2vec2-xlsr-53-espeak-cv-ft",
        "2c733782da5604684829819a5eb744c193fe9398",
    ),
    "stress": ("slprl/WhiStress", "3641786c97df31786d2ae17bb9307f7776cf7138"),
    "stress-backbone": (
        "openai/whisper-small.en",
        "e8727524f962ee844a7319d92be39ac1bd25655a",
    ),
    "qwen-q8": ("unsloth/Qwen3.8-27B-GGUF", "4ca720788d1e01f1bff70c033e0d0028fd02e502"),
    "qwen-q6": ("unsloth/Qwen3.8-27B-GGUF", "4ca720788d1e01f1bff70c033e0d0028fd02e502"),
    "muse-q6": (
        "unsloth/Muse-Glimmer-30B-GGUF",
        "faa5b025c584459c13febfa5c59883516710ae39",
    ),
}
EXISTING = ROOT.parent / "investigation_llm_models/qwen3.8-27b/models"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(16 * 1024 * 1024):
            h.update(b)
    return h.hexdigest()


def wanted(key, name):
    if key == "image":
        return (
            name == "model_index.json"
            or name.startswith("LICENSE")
            or (
                name.split("/", 1)[0]
                in {"scheduler", "text_encoder", "tokenizer", "transformer", "vae"}
                and pathlib.Path(name).suffix
                in {".json", ".txt", ".safetensors", ".jinja"}
            )
        )
    if key == "qwen-q8":
        return name in ("Qwen3.8-27B-Q8_0.gguf", "mmproj-BF16.gguf")
    if key == "qwen-q6":
        return name in ("Qwen3.8-27B-UD-Q6_K.gguf", "mmproj-BF16.gguf")
    if key == "muse-q6":
        return name in (
            "Muse-Glimmer-30B-UD-Q6_K_XL.gguf",
            "mmproj-Muse-Glimmer-30B-BF16.gguf",
        )
    return pathlib.Path(name).suffix in {
        ".json",
        ".txt",
        ".safetensors",
        ".bin",
        ".pt",
        ".npz",
        ".model",
        ".tiktoken",
    } or name.startswith("LICENSE")


def prepare(keys):
    dest = ROOT / "models"
    dest.mkdir(exist_ok=True)
    lockfile = ROOT / "models.lock.json"
    lock = (
        json.loads(lockfile.read_text())
        if lockfile.exists()
        else {"version": 1, "runtime": {}, "models": {}}
    )
    for key in keys:
        repo, rev = SPECS[key]
        with urllib.request.urlopen(
            f"https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true"
        ) as r:
            meta = json.load(r)
        assert meta["sha"] == rev
        artifacts = []
        out = dest / key
        out.mkdir(exist_ok=True)
        for item in meta["siblings"]:
            name = item["rfilename"]
            if not wanted(key, name):
                continue
            expected = item.get("lfs", {}).get("sha256")
            target = out / name
            reuse = EXISTING / (
                "GGUF/" + name if name == "Qwen3.8-27B-Q8_0.gguf" else name
            )
            if (
                key.startswith("qwen-")
                and not target.exists()
                and reuse.exists()
                and expected
                and sha(reuse) == expected
            ):
                target.symlink_to(reuse)
                print("Reused verified", name, flush=True)
            if not target.exists():
                path = pathlib.Path(hf_hub_download(repo, name, revision=rev))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(path)
            digest = sha(target)
            if expected and digest != expected:
                raise RuntimeError(f"Hash mismatch: {key}/{name}")
            artifacts.append(
                {"file": name, "bytes": target.stat().st_size, "sha256": digest}
            )
            print("Verified", key, name, flush=True)
        if not artifacts:
            raise RuntimeError(f"No artifacts for {key}")
        with open(ROOT / "models.lock.guard", "w") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            lock = json.loads(lockfile.read_text()) if lockfile.exists() else lock
            lock["models"][key] = {"repo": repo, "revision": rev, "files": artifacts}
            temp = lockfile.with_suffix(".tmp")
            temp.write_text(json.dumps(lock, indent=2) + "\n")
            os.replace(temp, lockfile)
    binary = ROOT / ".tools/llama-server"
    if not binary.exists():
        binary.symlink_to("/home/kuwabara/llama.cpp/build/bin/llama-server")
    digest = sha(binary)
    with open(ROOT / "models.lock.guard", "w") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        lock = json.loads(lockfile.read_text())
        lock["runtime"]["llama.cpp"] = {
            "commit": "9d57ce456c94d241dde672b2db9cf18879766568",
            "sha256": digest,
        }
        temp = lockfile.with_suffix(".tmp")
        temp.write_text(json.dumps(lock, indent=2) + "\n")
        os.replace(temp, lockfile)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument(
        "keys",
        nargs="*",
        default=[
            "qwen-q8",
            "qwen-q6",
            "muse-q6",
            "tts",
            "tts-design",
            "image",
            "asr",
            "aligner",
            "phoneme",
            "stress",
            "stress-backbone",
        ],
    )
    prepare(p.parse_args().keys)
