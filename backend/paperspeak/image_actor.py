"""Isolated, offline FLUX inference; JSON-lines stdout, diagnostics stderr."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

CACHE = {}


def run(request):
    import torch
    from diffusers import Flux2KleinPipeline
    from PIL import Image

    root = Path(request["root"])
    data_root = Path(request.get("data_root", root / "data")).resolve()
    torch.set_num_threads(8)
    if "pipe" not in CACHE:
        pipe = Flux2KleinPipeline.from_pretrained(
            str(root / "models/image"),
            torch_dtype=torch.bfloat16,
            local_files_only=True,
        )
        pipe.enable_model_cpu_offload()
        pipe.vae.enable_tiling()
        CACHE["pipe"] = pipe
    output = Path(request["output"])
    if not output.resolve().is_relative_to(data_root):
        raise ValueError("Image output must be inside PaperSpeak data")
    refs = []
    for name in request.get("references", []):
        path = Path(name).resolve()
        if not path.is_relative_to(data_root):
            raise ValueError("Unsafe image reference")
        refs.append(Image.open(path).convert("RGB"))
    settings = {
        "width": request.get("width", 1280),
        "height": request.get("height", 720),
        "num_inference_steps": 50,
        "guidance_scale": 4.0,
    }
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    kwargs = dict(
        prompt=request["prompt"],
        **settings,
        generator=torch.Generator("cuda").manual_seed(request["seed"]),
    )
    if refs:
        kwargs["image"] = refs
    with torch.inference_mode():
        image = CACHE["pipe"](**kwargs).images[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".partial.png")
    image.save(partial)
    partial.replace(output)
    manifest = json.loads((root / "models.lock.json").read_text())["models"]["image"]
    return {
        "path": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "seconds": round(time.monotonic() - started, 2),
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1048576, 1),
        "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1048576, 1),
        "settings": settings
        | {
            "seed": request["seed"],
            "dtype": "bfloat16",
            "repo": manifest["repo"],
            "revision": manifest["revision"],
            "prompt_sha256": hashlib.sha256(request["prompt"].encode()).hexdigest(),
        },
    }


if __name__ == "__main__":
    from local_network import inference_only

    for line in sys.stdin:
        try:
            with contextlib.redirect_stdout(sys.stderr), inference_only():
                result = run(json.loads(line))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            result = {"error": str(exc)}
        print(json.dumps(result), flush=True)
