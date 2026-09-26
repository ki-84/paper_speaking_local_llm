from __future__ import annotations

import json
import subprocess
import time
from difflib import SequenceMatcher

import imageio_ffmpeg
import numpy as np
import soundfile as sf

from . import config, db
from .quality import dialogue_for_model, english_only, speech_context, word_diff, words


def normalize_audio(source, target):
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            "91",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(target),
        ],
        check=True,
        timeout=40,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    info = sf.info(target)
    if info.duration > 90.1:
        raise ValueError("Please record less than 90 seconds.")
    return info.duration


def audio_quality(path):
    y, sr = sf.read(path, dtype="float32")
    duration = len(y) / sr
    rms = float(np.sqrt(np.mean(y * y))) if len(y) else 0
    peak = float(np.max(np.abs(y))) if len(y) else 0
    clipping = float(np.mean(np.abs(y) >= 0.995)) if len(y) else 0
    status = "ok" if duration >= 0.35 and rms >= 0.002 else "too_quiet"
    if clipping > 0.08:
        status = "clipped"
    flatness = 0.0
    if len(y) >= 400 and rms >= 0.002:
        frames = np.lib.stride_tricks.sliding_window_view(y, 400)[::160]
        power = abs(np.fft.rfft(frames * np.hanning(400), axis=1)) ** 2 + 1e-12
        active = np.sqrt(np.mean(frames * frames, axis=1)) > max(0.001, rms * 0.2)
        if active.any():
            flatness = float(
                np.median(
                    np.exp(np.mean(np.log(power[active]), axis=1))
                    / np.mean(power[active], axis=1)
                )
            )
            tone = float(
                np.median(np.max(power[active], axis=1) / np.sum(power[active], axis=1))
            )
            if flatness > 0.45 or tone > 0.6:
                status = "noise"
    return {
        "duration": duration,
        "rms": rms,
        "peak": peak,
        "clipping": clipping,
        "status": status,
        "spectral_flatness": flatness,
    }


def save_attempt(a):
    db.execute(
        "UPDATE attempts SET state=CASE WHEN state='cancelled' THEN state ELSE ? END,data=? WHERE id=?",
        (a["state"], db.dumps(a["data"]), a["id"]),
    )
    db.event("attempt", {"id": a["id"]})


SOUND_TIPS = {
    "θ": {
        "tip": "Put your tongue lightly between your teeth and let air pass.",
        "words": ["think", "thin", "three"],
    },
    "ð": {
        "tip": "Use the same tongue position as in thin, but add your voice.",
        "words": ["this", "that", "those"],
    },
    "ɹ": {
        "tip": "Keep your tongue away from the roof of your mouth. Round your lips a little.",
        "words": ["red", "read", "right"],
    },
    "l": {
        "tip": "Touch the ridge behind your top teeth with your tongue.",
        "words": ["light", "learn", "little"],
    },
    "v": {
        "tip": "Touch your lower lip to your top teeth and add your voice.",
        "words": ["very", "voice", "view"],
    },
    "f": {
        "tip": "Touch your lower lip to your top teeth. Let air pass.",
        "words": ["fine", "four", "feel"],
    },
    "æ": {
        "tip": "Open your mouth a little wider than in bed.",
        "words": ["cat", "map", "back"],
    },
    "ɪ": {
        "tip": "Keep this vowel short and relaxed. Compare ship with sheep.",
        "words": ["ship", "sit", "big"],
    },
    "iː": {
        "tip": "Hold the vowel a little longer. Compare sheep with ship.",
        "words": ["sheep", "see", "green"],
    },
    "ʃ": {
        "tip": "Round your lips a little. Let air pass, as when you ask for quiet.",
        "words": ["she", "show", "share"],
    },
}


def pronunciation_feedback(learner, reference):
    a = reference.get("phones", [])
    b = learner.get("phones", [])
    pairs = []
    for tag, i, j, k, l in SequenceMatcher(
        a=[x["phone"] for x in a], b=[x["phone"] for x in b], autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        conf = min([x["confidence"] for x in a[i:j] + b[k:l]] or [0])
        pairs.append(
            {
                "expected": " ".join(x["phone"] for x in a[i:j]),
                "heard": " ".join(x["phone"] for x in b[k:l]),
                "start": b[k]["start"] if k < len(b) else None,
                "end": b[l - 1]["end"] if l > k else None,
                "reference_start": a[i]["start"] if i < len(a) else None,
                "reference_end": a[j - 1]["end"] if j > i else None,
                "confidence": conf,
                "status": "compare" if conf >= 0.8 else "uncertain",
            }
        )
    for pair in pairs:
        pair["practice"] = next(
            (
                tip
                for sound, tip in SOUND_TIPS.items()
                if sound in pair["expected"].split()
            ),
            None,
        )
    return {
        "status": "estimated",
        "learner": learner,
        "reference": reference,
        "differences": pairs[:12],
        "message": "Listen to these sounds side by side. These are estimates; a different sound can also be a valid pronunciation.",
    }


def practice_step(job, runtime):
    attempt = db.one("SELECT * FROM attempts WHERE id=?", (job["target"],))
    if not attempt:
        raise ValueError("Recording not found")
    a = attempt["data"]
    chapter = db.one("SELECT * FROM chapters WHERE id=?", (attempt["chapter_id"],))
    phase = a.get("phase", "normalize")

    def stage(s, p):
        db.patch_job(job["id"], stage=s, progress=p)

    if phase == "normalize":
        stage("Checking your recording", 0.05)
        source = config.safe_path(a["audio"])
        path = source.with_suffix(".wav")
        if source != path:
            normalize_audio(source, path)
        a["wav"] = str(path.relative_to(config.DATA))
        a["quality"] = audio_quality(path)
        if a["quality"]["status"] != "ok":
            a.update(
                phase="done",
                feedback={
                    "too_quiet": "We could not hear you clearly. Try a little closer to the microphone.",
                    "clipped": "The recording is too loud. Move a little farther from the microphone and try again.",
                    "noise": "We heard too much background sound. Try again in a quieter place.",
                }.get(a["quality"]["status"], "Please try recording again."),
                word_match=None,
            )
            attempt["state"] = "unscored"
            save_attempt(attempt)
            return True
        a["phase"] = "transcribe"
        save_attempt(attempt)
        return False
    if phase == "transcribe":
        stage("Listening to your words", 0.2)
        lesson_data = db.one(
            "SELECT data FROM lessons WHERE id=?", (attempt["lesson_id"],)
        )["data"]
        result = runtime.speech(
            "asr",
            {
                "audio": str(config.safe_path(a["wav"])),
                "context": speech_context(
                    lesson_data["title"],
                    lesson_data.get("glossary", []),
                    chapter["data"]["turns"],
                ),
            },
        )
        a["transcript"] = result["text"]
        a["timestamps"] = result["timestamps"]
        a["asr_settings"] = result.get("generation_settings", {})
        if not result["text"].strip():
            a.update(
                feedback="We could not understand this recording. Please try again.",
                word_match=None,
            )
            attempt["state"] = "unscored"
            save_attempt(attempt)
            return True
        a["pace_wpm"] = round(
            len(words(result["text"])) * 60 / max(0.1, a["quality"]["duration"])
        )
        stamps = result["timestamps"]
        a["pauses"] = [
            {"start": x["end"], "end": y["start"]}
            for x, y in zip(stamps, stamps[1:])
            if y["start"] - x["end"] >= 0.35
        ]
        if attempt["kind"] == "read":
            turn = next(
                t for t in chapter["data"]["turns"] if t["id"] == attempt["turn_id"]
            )
            a.update(word_diff(turn["text"], result["text"]))
            a["reference_text"] = turn["text"]
            a["reference_audio"] = turn["audio"]
            a["reference_timestamps"] = turn.get("audio_check", {}).get(
                "timestamps", []
            )
            a["phase"] = "phones"
        else:
            a["phase"] = "understand"
        save_attempt(attempt)
        return False
    if phase == "phones":
        stage("Comparing the sounds", 0.45)
        learner = runtime.speech("phoneme", {"audio": str(config.safe_path(a["wav"]))})
        reference = runtime.speech(
            "phoneme", {"audio": str(config.safe_path(a["reference_audio"]))}
        )
        a["pronunciation"] = pronunciation_feedback(learner, reference)
        import cmudict

        dictionary = cmudict.dict()
        a["word_stress"] = [
            {"word": w, "pronunciations": dictionary[w][:2]}
            for w in words(a["reference_text"])
            if w in dictionary
            and sum(any(ch.isdigit() for ch in p) for p in dictionary[w][0]) > 1
        ]
        a["syllable_energy"] = {
            "learner": vowel_energy(
                config.safe_path(a["wav"]), learner, a["timestamps"]
            ),
            "reference": vowel_energy(
                config.safe_path(a["reference_audio"]),
                reference,
                a.get("reference_timestamps", []),
            ),
            "status": "estimated",
            "message": "Compare the vowel peaks inside a word. Louder does not always mean stressed.",
        }
        a["phase"] = "stress"
        save_attempt(attempt)
        return False
    if phase == "stress":
        stage("Listening to rhythm and emphasis", 0.7)
        if (config.MODELS / "stress-backbone/config.json").exists():
            a["stress"] = runtime.speech(
                "stress",
                {"audio": str(config.safe_path(a["wav"])), "text": a["transcript"]},
            )
            a["reference_stress"] = runtime.speech(
                "stress",
                {
                    "audio": str(config.safe_path(a["reference_audio"])),
                    "text": a["reference_text"],
                },
            )
        else:
            raise RuntimeError(
                "Stress model backbone is not installed. Run prepare_assets.py stress-backbone."
            )
        a["feedback"] = (
            "Listen again, then try one small change. Keep your speech clear and comfortable."
        )
        a["phase"] = "done"
        attempt["state"] = "ready"
        save_attempt(attempt)
        add_review(attempt)
        return True
    if phase == "understand":
        stage("Checking the idea in your answer", 0.65)
        q = next(q for q in chapter["data"]["questions"] if q["id"] == a["question_id"])
        result = runtime.ask(
            "Give kind, short feedback to an English learner answering a question about a paper. "
            "Evaluate understanding separately from English expression. Accept correct paraphrases and do not require exact sample wording. "
            "The transcript may contain recognition errors; if a key word is ambiguous, ask for another try instead of asserting the learner is wrong. "
            'Return {"understanding":"clear|partly_clear|try_again","content_feedback":"one simple sentence","english_feedback":"one useful simple tip","better_answer":"a short easy-English answer","next_step":"one small action"}.'
            "\nQUESTION AND RUBRIC: "
            + json.dumps(q)
            + "\nLESSON: "
            + json.dumps(dialogue_for_model(chapter["data"]["turns"]))
            + "\nLEARNER TRANSCRIPT: "
            + a["transcript"],
            profile=a.get("language_model", db.settings()["model_profile"]),
            thinking=False,
            max_tokens=2000,
        )
        if result.get("understanding") not in {
            "clear",
            "partly_clear",
            "try_again",
        } or any(
            not isinstance(result.get(key), str)
            or not result[key].strip()
            or not english_only(result[key])
            for key in [
                "content_feedback",
                "english_feedback",
                "better_answer",
                "next_step",
            ]
        ):
            raise ValueError(
                "The answer feedback was incomplete; your recording is saved for another try."
            )
        a["comprehension"] = result
        a["phase"] = "done"
        attempt["state"] = "ready"
        save_attempt(attempt)
        add_review(attempt)
        return True
    return True


def add_review(attempt):
    rid = f"{attempt['chapter_id']}:{attempt['turn_id'] or attempt['data'].get('question_id', 'chapter')}"
    existing = db.one("SELECT * FROM reviews WHERE id=?", (rid,))
    if existing:
        return
    db.execute(
        "INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
        (
            rid,
            attempt["lesson_id"],
            attempt["chapter_id"],
            attempt["turn_id"],
            time.time() + 86400,
            0,
            db.dumps(
                {
                    "kind": attempt["kind"],
                    "question_id": attempt["data"].get("question_id"),
                }
            ),
        ),
    )


def complete_review(ident, again=False):
    r = db.one("SELECT * FROM reviews WHERE id=?", (ident,))
    if not r:
        raise ValueError("Review not found")
    step = 0 if again else min(4, r["step"] + 1)
    intervals = [1, 3, 7, 14, 30]
    db.execute(
        "UPDATE reviews SET step=?,due=? WHERE id=?",
        (step, time.time() + intervals[step] * 86400, ident),
    )


def vowel_energy(path, phones, timestamps):
    # CTC peaks are approximate positions, not precise phoneme boundaries.
    y, sr = sf.read(path, dtype="float32")
    if y.ndim > 1:
        y = y.mean(axis=1)
    vowels = set("aeiouɑæɐəɛɚɜɪɔʊʌ")
    output = []
    for word in timestamps:
        selected = [
            p
            for p in phones.get("phones", [])
            if any(v in p["phone"] for v in vowels)
            and word["start"] <= p["start"] < word["end"]
            and p["confidence"] >= 0.8
        ]
        if len(selected) < 2:
            continue
        peaks = []
        for p in selected:
            center = (p["start"] + p["end"]) / 2
            clip = y[
                max(0, int((center - 0.035) * sr)) : min(
                    len(y), int((center + 0.035) * sr)
                )
            ]
            energy = float(np.sqrt(np.mean(clip * clip))) if len(clip) else 0
            peaks.append(
                {
                    "phone": p["phone"],
                    "start": max(0, center - 0.035),
                    "end": center + 0.035,
                    "energy": energy,
                }
            )
        maximum = max(p["energy"] for p in peaks) or 1
        for p in peaks:
            p["relative_energy"] = round(p["energy"] / maximum, 3)
        output.append({"word": word["word"], "vowels": peaks, "status": "estimated"})
    return output
