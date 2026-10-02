"""Isolated local speech inference. JSON-lines on stdout, all diagnostics on stderr."""

from __future__ import annotations

import contextlib
import dataclasses
import gc
import json
import sys
import traceback
from pathlib import Path

CACHE = {}


def clear_for(key):
    if CACHE.get("key") != key:
        CACHE.clear()
        gc.collect()
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        CACHE["key"] = key


def load_audio(path):
    import librosa

    y, sr = librosa.load(path, sr=16000, mono=True)
    return y, sr


def run(request):
    import numpy as np
    import soundfile as sf
    import torch

    torch.set_num_threads(8)
    root = Path(request["root"])
    models = root / "models"
    op = request["operation"]
    phoneme_profile = request.get("phoneme_profile", "phoneme")
    if phoneme_profile not in {"phoneme", "phoneme-timit"}:
        raise ValueError("Unknown phoneme profile")
    clear_for(op + ":" + phoneme_profile if op == "phoneme" else op)
    if op in {"tts", "tts_design"}:
        from qwen_tts import Qwen3TTSModel

        seed = int(request.get("seed", 2026))
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        instruction = request.get("instruction") or "Speak in clear, warm American English. Use a calm teaching pace, natural phrasing, and gentle expression. Do not rush. No background sounds."
        model_key = "tts" if op == "tts" else "tts-design"
        if "model" not in CACHE:
            CACHE["model"] = Qwen3TTSModel.from_pretrained(
                str(models / model_key),
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation="sdpa",
            )
        common = {
            "text": request["text"],
            "language": "English",
            "instruct": instruction,
            "non_streaming_mode": True,
            "max_new_tokens": 2048,
        }
        if op == "tts":
            wavs, sr = CACHE["model"].generate_custom_voice(
                speaker=request.get("voice", "Aiden"), **common
            )
        else:
            wavs, sr = CACHE["model"].generate_voice_design(**common)
        path = Path(request["output"])
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, wavs[0], sr, subtype="PCM_16")
        settings = json.loads((models / model_key / "generation_config.json").read_text())
        model_record = json.loads((root / "models.lock.json").read_text())["models"][model_key]
        settings.update(
            seed=seed,
            max_new_tokens=2048,
            non_streaming_mode=True,
            speaker=request.get("voice", "Aiden"),
            model=model_key,
            model_revision=model_record["revision"],
            model_sha256=next(
                f["sha256"] for f in model_record["files"] if f["file"] == "model.safetensors"
            ),
            language="English",
            instruction=instruction,
            dtype="bfloat16",
            attention="sdpa",
        )
        return {
            "sample_rate": sr,
            "duration": len(wavs[0]) / sr,
            "path": str(path),
            "generation_settings": settings,
        }
    if op == "asr":
        from qwen_asr import Qwen3ASRModel

        if "model" not in CACHE:
            CACHE["model"] = Qwen3ASRModel.from_pretrained(
                str(models / "asr"),
                dtype=torch.bfloat16,
                device_map="cuda:0",
                attn_implementation="sdpa",
                max_inference_batch_size=1,
                max_new_tokens=512,
                forced_aligner=str(models / "aligner"),
                forced_aligner_kwargs={
                    "dtype": torch.bfloat16,
                    "device_map": "cuda:0",
                    "attn_implementation": "sdpa",
                },
            )
        results = CACHE["model"].transcribe(
            audio=request["audio"],
            context=request.get("context", ""),
            language="English",
            return_time_stamps=True,
        )
        r = results[0]
        stamps = r.time_stamps
        if dataclasses.is_dataclass(stamps):
            stamps = dataclasses.asdict(stamps)
        if isinstance(stamps, dict):
            stamps = stamps.get("items", stamps.get("words", []))
        timestamps = []
        for s in stamps or []:
            if dataclasses.is_dataclass(s):
                s = dataclasses.asdict(s)
            if not isinstance(s, dict):
                s = vars(s)
            timestamps.append(
                {
                    "word": s.get("text", s.get("word", "")),
                    "start": float(s.get("start_time", s.get("start", 0))),
                    "end": float(s.get("end_time", s.get("end", 0))),
                }
            )
        return {
            "text": r.text,
            "language": r.language,
            "timestamps": timestamps,
            "generation_settings": {
                "language": "English",
                "dtype": "bfloat16",
                "attention": "sdpa",
                "max_new_tokens": 512,
                "forced_alignment": True,
                "vocabulary_context": request.get("context", ""),
            },
        }
    if op == "phoneme":
        from transformers import AutoModelForCTC, AutoProcessor

        if "model" not in CACHE:
            CACHE["processor"] = AutoProcessor.from_pretrained(
                models / phoneme_profile, local_files_only=True
            )
            CACHE["model"] = (
                AutoModelForCTC.from_pretrained(
                    models / phoneme_profile, local_files_only=True
                )
                .to("cuda")
                .eval()
            )
        y, sr = load_audio(request["audio"])
        inputs = CACHE["processor"](y, sampling_rate=sr, return_tensors="pt")
        with torch.inference_mode():
            probs = (
                CACHE["model"](**{k: v.to("cuda") for k, v in inputs.items()})
                .logits[0]
                .softmax(-1)
            )
        scores, ids = probs.max(-1)
        ids = ids.cpu().tolist()
        scores = scores.cpu().tolist()
        tokenizer = CACHE["processor"].tokenizer
        units = []
        i = 0
        scale = len(y) / sr / max(1, len(ids))
        while i < len(ids):
            j = i + 1
            while j < len(ids) and ids[j] == ids[i]:
                j += 1
            if ids[i] != tokenizer.pad_token_id:
                text = tokenizer.convert_ids_to_tokens(ids[i])
                if (
                    text.strip()
                    and text not in tokenizer.all_special_tokens
                    and text != "|"
                ):
                    units.append(
                        {
                            "phone": text,
                            "start": round(i * scale, 3),
                            "end": round(j * scale, 3),
                            "confidence": round(float(np.mean(scores[i:j])), 3),
                        }
                    )
            i = j
        return {"phones": units, "ipa": tokenizer.decode(ids)}
    if op == "stress":
        # The architecture and token shift follow the official WhiStress implementation.
        # Only inference layers are loaded; torch.load uses weights_only=True.
        from torch import nn
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        from transformers.models.whisper.modeling_whisper import WhisperDecoderLayer

        if "model" not in CACHE:
            backbone = models / "stress-backbone"
            CACHE["processor"] = WhisperProcessor.from_pretrained(
                backbone, local_files_only=True
            )
            CACHE["model"] = (
                WhisperForConditionalGeneration.from_pretrained(
                    backbone, local_files_only=True
                )
                .to("cuda")
                .eval()
            )
            cfg = CACHE["model"].config
            cfg._attn_implementation = "eager"
            CACHE["block"] = WhisperDecoderLayer(cfg).to("cuda").eval()

            class Head(nn.Module):
                def __init__(self):
                    super().__init__()
                    self.fc1 = nn.Linear(cfg.d_model, 2 * cfg.d_model)
                    self.fc2 = nn.Linear(2 * cfg.d_model, 2)

                def forward(self, x):
                    return self.fc2(torch.relu(self.fc1(x)))

            CACHE["head"] = Head().to("cuda").eval()
            CACHE["block"].load_state_dict(
                torch.load(
                    models / "stress/additional_decoder_block.pt",
                    map_location="cuda",
                    weights_only=True,
                )
            )
            CACHE["head"].load_state_dict(
                torch.load(
                    models / "stress/classifier.pt",
                    map_location="cuda",
                    weights_only=True,
                )
            )
            CACHE["layer"] = json.loads((models / "stress/metadata.json").read_text())[
                "layer_for_head"
            ]
        y, sr = load_audio(request["audio"])
        if len(y) / sr > 30:
            return {
                "status": "too_long",
                "words": [],
                "message": "Use a clip shorter than 30 seconds for stress feedback.",
            }
        p = CACHE["processor"]
        model = CACHE["model"]
        features = p.feature_extractor(y, sampling_rate=sr, return_tensors="pt")[
            "input_features"
        ].to("cuda")
        ids = p.tokenizer(
            request["text"], return_tensors="pt", truncation=True, max_length=224
        )["input_ids"].to("cuda")
        with torch.inference_mode():
            output = model(
                input_features=features,
                decoder_input_ids=ids,
                output_hidden_states=True,
            )
            hidden = CACHE["block"](
                hidden_states=output.decoder_hidden_states[CACHE["layer"]],
                encoder_hidden_states=output.encoder_hidden_states[CACHE["layer"]],
            )[0]
            probs = CACHE["head"](hidden).softmax(-1)[0, :, 1].roll(1).cpu().tolist()
        units = []
        for token, score in zip(ids[0].cpu().tolist(), probs):
            if token in p.tokenizer.all_special_ids:
                continue
            text = p.tokenizer.decode([token])
            if not units or text.startswith(" "):
                units.append({"word": text.strip(), "probability": float(score)})
            else:
                units[-1]["word"] += text
                units[-1]["probability"] = max(units[-1]["probability"], float(score))
        return {
            "status": "estimated",
            "words": [w | {"emphasized": w["probability"] >= 0.5} for w in units],
        }
    raise ValueError("Unsupported speech operation")


if __name__ == "__main__":
    # This script runs in the speech virtualenv, with its own directory on sys.path.
    # Enforce local-only inference in the child process as well as the coordinator.
    from local_network import inference_only

    output = sys.stdout
    for line in sys.stdin:
        try:
            with contextlib.redirect_stdout(sys.stderr), inference_only():
                result = run(json.loads(line))
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            result = {"error": f"{type(e).__name__}: {e}"}
        output.write(json.dumps(result) + "\n")
        output.flush()
