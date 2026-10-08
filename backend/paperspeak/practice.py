from __future__ import annotations

import json
import re
import subprocess
from difflib import SequenceMatcher
from functools import lru_cache

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

# These are practice cues for the printed target word. They do not assert that
# the learner produced a particular wrong phone: the phone probe is too noisy
# for that conclusion on learner speech.
WORD_SOUND_GUIDES = {
    "HH": ("/h/", "Let out a soft breath before the vowel.", "母音の前で、息を軽く出して /h/ を始めます。"),
    "TH": ("/θ/", "Put your tongue lightly between your teeth and let air pass.", "舌先を上下の歯の間に軽く置き、息を通します。"),
    "DH": ("/ð/", "Use the same tongue position as in thin, and add your voice.", "舌先を歯の間に軽く置き、声を出します。"),
    "R": ("/ɹ/", "Keep your tongue away from the roof of your mouth.", "舌を上あごに付けずに /r/ を出します。"),
    "L": ("/l/", "Touch the ridge behind your top teeth with your tongue.", "舌先を上の前歯のすぐ後ろに付けます。"),
    "V": ("/v/", "Touch your lower lip to your top teeth and add your voice.", "下唇を上の歯に軽く当て、声を出します。"),
    "F": ("/f/", "Touch your lower lip to your top teeth and let air pass.", "下唇を上の歯に軽く当て、息を通します。"),
    "AE": ("/æ/", "Open your mouth wider than for bed.", "「bed」の /e/ より口を少し大きく開きます。"),
    "EH": ("/ɛ/", "Say the short vowel in bed.", "「bed」の短い母音を意識します。"),
    "IH": ("/ɪ/", "Keep this vowel short, as in ship.", "「ship」の短い母音を意識します。"),
    "IY": ("/iː/", "Hold this vowel a little longer, as in sheep.", "「sheep」の母音を少し長めに伸ばします。"),
    "OW": ("/oʊ/", "Let the vowel glide, as in go.", "「go」のように母音を滑らかに変化させます。"),
    "SH": ("/ʃ/", "Round your lips gently, as in she.", "「she」のように唇を少し丸めます。"),
    "Z": ("/z/", "Keep your voice on for the final /z/ sound.", "語尾の /z/ まで声を出し続けます。"),
}


@lru_cache(maxsize=1)
def pronunciation_dictionary():
    import cmudict

    return cmudict.dict()


def dictionary_sounds(word):
    return [
        tuple(re.sub(r"\d", "", phone) for phone in pronunciation)
        for pronunciation in pronunciation_dictionary().get(word.lower(), [])[:4]
    ]


def word_sound_guide(expected, heard):
    target = next(iter(dictionary_sounds(expected)), ())
    comparison = next(iter(dictionary_sounds(heard)), ())
    if not target or not comparison:
        return None
    for tag, start, end, _, _ in SequenceMatcher(
        a=target, b=comparison, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        for phone in target[start:end]:
            if phone in WORD_SOUND_GUIDES:
                sound, english, japanese = WORD_SOUND_GUIDES[phone]
                return {
                    "sound": sound,
                    "tip": f"Try {sound} in ‘{expected}’. {english}",
                    "tip_ja": f"「{expected}」の {sound} を練習しましょう。{japanese}",
                }
    return None


def indexed_word_times(stamps):
    return [
        {"start": stamp.get("start"), "end": stamp.get("end")}
        for stamp in stamps
        for _ in words(stamp.get("word", ""))
    ]


def pronunciation_focus(diff, reference_stamps, learner_stamps):
    """Give word-level practice cues without turning uncertain phones into errors."""
    changes = diff.get("words", [])
    reference_count = sum(bool(item.get("expected")) for item in changes)
    learner_count = sum(bool(item.get("heard")) for item in changes)
    reference_times = indexed_word_times(reference_stamps)
    learner_times = indexed_word_times(learner_stamps)
    if len(reference_times) != reference_count:
        reference_times = []
    if len(learner_times) != learner_count:
        learner_times = []
    result = []
    ri = li = 0
    for item in changes:
        expected, heard = item.get("expected", ""), item.get("heard", "")
        reference = reference_times[ri] if expected and reference_times else {}
        learner = learner_times[li] if heard and learner_times else {}
        ri += bool(expected)
        li += bool(heard)
        if item.get("kind") == "match":
            continue
        same_sound = bool(
            expected and heard and set(dictionary_sounds(expected)) & set(dictionary_sounds(heard))
        )
        if same_sound:
            message = f"The recognizer wrote ‘{heard}’; the line says ‘{expected}’. These can sound the same. The spelling difference alone does not show a pronunciation error."
            message_ja = f"音声認識は「{heard}」と書きましたが、お手本は「{expected}」です。発音が同じ場合があり、表記の違いだけでは発音の誤りとは言えません。"
        elif expected and heard:
            message = f"The recognizer heard ‘{heard}’ where the line says ‘{expected}’. Compare the two recordings."
            message_ja = f"音声認識は「{heard}」と聞き取りました。お手本の「{expected}」と聞き比べてください。"
        elif expected:
            message = f"The recognizer missed ‘{expected}’. Listen for this word, then try it again."
            message_ja = f"「{expected}」が聞き取られませんでした。お手本を聞いて、もう一度試してください。"
        else:
            message = f"The recognizer heard an extra ‘{heard}’. Check this short part of your recording."
            message_ja = f"「{heard}」という単語が余分に聞き取られました。録音のこの部分を確認してください。"
        guide = word_sound_guide(expected, heard) if expected and heard and not same_sound else None
        result.append({
            "kind": "same_sound" if same_sound else item["kind"],
            "expected": expected, "heard": heard,
            "message": message, "message_ja": message_ja,
            "sound": guide["sound"] if guide else None,
            "tip": guide["tip"] if guide else None,
            "tip_ja": guide["tip_ja"] if guide else None,
            "reference_start": reference.get("start"), "reference_end": reference.get("end"),
            "start": learner.get("start"), "end": learner.get("end"),
        })
    # A few clear next steps are more useful than a long list of model guesses.
    actionable = [item for item in result if item["kind"] != "same_sound"][:3]
    if not actionable:
        actionable = [item for item in result if item["kind"] == "same_sound"][:1]
    return {
        "status": "word_recognition_only",
        "items": actionable,
        "message": "A word recognizer can make mistakes. These are words to check, not proof of a pronunciation error.",
        "message_ja": "音声認識には誤りもあります。これは聞き比べる候補であり、発音の間違いを断定するものではありません。",
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
            a["pronunciation_focus"] = pronunciation_focus(
                a, a["reference_timestamps"], a["timestamps"]
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
        lesson = db.one("SELECT data FROM lessons WHERE id=?", (attempt["lesson_id"],))
        expression_target = ("Use natural C1 English in better_answer, retaining useful idioms and complex sentences where appropriate. "
                             if lesson["data"].get("format") == "paper-story-1" else "Use short easy English in better_answer. ")
        result = runtime.ask(
            "Give kind, short feedback to an English learner answering a question about a paper. "
            "Evaluate understanding separately from English expression. Accept correct paraphrases and do not require exact sample wording. "
            "The transcript may contain recognition errors; if a key word is ambiguous, ask for another try instead of asserting the learner is wrong. "
            + expression_target
            + 'Return {"understanding":"clear|partly_clear|try_again","content_feedback":"one clear sentence","english_feedback":"one useful tip","better_answer":"a concise answer at the requested English level","next_step":"one small action"}.'
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
    from . import repetition

    if attempt.get("state") != "ready":
        return
    try:
        repetition.enroll(
            attempt["lesson_id"],
            attempt["chapter_id"],
            turn_id=attempt.get("turn_id"),
            question_id=attempt["data"].get("question_id"),
        )
    except ValueError:
        # Regeneration may remove a sentence while its recording is checked.
        # Retain the recording feedback even when that review target vanished.
        return


def complete_review(ident, again=False):
    from . import repetition

    return repetition.rate(ident, "again" if again else "good")


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
