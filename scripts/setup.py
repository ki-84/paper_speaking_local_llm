#!/usr/bin/env python3
"""Recreate the pinned Python/JS environments. Downloads are explicit, not inference-time."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
p = argparse.ArgumentParser()
p.add_argument("--models", action="store_true")
p.add_argument("--llama-bin", type=Path)
args = p.parse_args()
TOOLS = ROOT / ".tools"
TOOLS.mkdir(exist_ok=True)


def run(*cmd, **kwargs):
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)


def download(url, path, digest):
    if not path.exists():
        urllib.request.urlretrieve(url, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise SystemExit("Checksum mismatch: " + str(path))


if sys.version_info[:2] != (3, 12):
    raise SystemExit("Use Python 3.12 for these locks.")
if not (ROOT / ".venv/bin/python").exists():
    run(sys.executable, "-m", "venv", ROOT / ".venv")
if not (ROOT / ".venv/bin/uv").exists():
    run(
        ROOT / ".venv/bin/python",
        "-m",
        "pip",
        "install",
        "--require-hashes",
        "-r",
        "locks/bootstrap.txt",
    )
uv = ROOT / ".venv/bin/uv"
for env, lock in [
    (".venv", "app"),
    (".venv-tts", "tts"),
    (".venv-asr", "asr"),
    (".venv-image", "image"),
]:
    py = ROOT / env / "bin/python"
    if not py.exists():
        run(uv, "venv", "--python", sys.executable, ROOT / env)
    cmd = [
        uv,
        "pip",
        "install",
        "--python",
        py,
        "--require-hashes",
        "-r",
        f"locks/{lock}.txt",
    ]
    if lock != "app":
        cmd += [
            "--extra-index-url",
            "https://download.pytorch.org/whl/cu128",
            "--index-strategy",
            "unsafe-best-match",
        ]
    run(*cmd)
run(
    uv,
    "pip",
    "install",
    "--python",
    ROOT / ".venv/bin/python",
    "--require-hashes",
    "-r",
    "locks/build.txt",
)
run(
    uv,
    "pip",
    "install",
    "--python",
    ROOT / ".venv/bin/python",
    "--no-deps",
    "--no-build-isolation",
    "-e",
    ".",
)
tools = json.loads((ROOT / "locks/tools.json").read_text())
for name in ["node", "caddy"]:
    spec = tools[name]
    archive = TOOLS / (name + ".tar.gz")
    download(spec["url"], archive, spec["sha256"])
    with tarfile.open(archive) as f:
        if name == "node":
            if not (TOOLS / spec["directory"] / "bin/node").exists():
                f.extractall(TOOLS, filter="data")
        else:
            member = f.getmember("caddy")
            content = f.extractfile(member).read()
            target = TOOLS / "caddy"
            if (
                not target.exists()
                or hashlib.sha256(target.read_bytes()).digest()
                != hashlib.sha256(content).digest()
            ):
                replacement = TOOLS / "caddy.new"
                replacement.write_bytes(content)
                replacement.chmod(0o755)
                replacement.replace(target)
    if name == "node":
        link = TOOLS / "node"
        if not link.exists():
            link.symlink_to(TOOLS / spec["directory"], target_is_directory=True)
(TOOLS / "caddy").chmod(0o755)
llama = args.llama_bin or Path("/home/kuwabara/llama.cpp/build/bin/llama-server")
if not (TOOLS / "llama-server").exists():
    if not llama.is_file():
        raise SystemExit(
            "Build the pinned llama.cpp commit (docs/INSTALL.md), then pass --llama-bin /path/to/llama-server."
        )
    (TOOLS / "llama-server").symlink_to(llama.resolve())
env = os.environ | {"PATH": str(TOOLS / "node/bin") + ":" + os.environ.get("PATH", "")}
run(TOOLS / "node/bin/npm", "ci", "--prefix", "frontend", env=env)
run(
    TOOLS / "node/bin/node",
    "frontend/node_modules/playwright/cli.js",
    "install",
    "chromium",
    env=env,
)
run(TOOLS / "node/bin/npm", "run", "build", "--prefix", "frontend", env=env)
if args.models:
    run(ROOT / ".venv/bin/python", "scripts/prepare_assets.py")
print("Setup complete. Run ./studio start and ./studio access.")
