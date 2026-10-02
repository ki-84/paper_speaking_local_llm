# LoRA two-film verification

Verified on 2026-10-03 JST against the running LAN service. The full structured
record is [story_lora_acceptance.json](../evaluation/story_lora_acceptance.json).
Project: `8ab4bf9ac3274635925c609fc9860165`; paper: `2106.09685v2`;
lesson format: `paper-story-1`; renderer: `story-film-2`.

| Output | Actual length | MP4 size | Sentence practice | Spoken expressions |
| --- | --- | --- | --- | --- |
| Overview, no equations | 17:48 | 51.19 MB | 244 clips | 10 |
| Deep dive, equations and worked example | 33:40 | 94.53 MB | 424 clips | 12 |

Both MP4s are complete, 1920×1080 H.264/AAC, with English and Japanese captions
burned into the frames. The library's two-film panel provides playback,
download, thumbnails, three titles per film, descriptions and timestamps.
The same audio is available through **Practice English**. These are two newly
written scripts, not concatenations of the old sixteen chapters. Their longest
exact shared sequence is eight words; that statistic does not measure all
semantic repetition.

## Media and browser checks

Thirty-five actual movie frames were compared with independently positioned
source slides and ASS overlays, including segment boundaries, mathematical
reveals, the numerical example and original Figure 3's heat-map zoom. Mean RGB
error stayed below 0.43/255. Decoded movie audio was compared with the source
paragraph WAV: correlation exceeded 0.998, with the measured AAC priming offset
of 21.33 ms. Source audio was retained throughout the final re-encoding.

The sparse-PNG seek defect discovered during visual inspection is fixed. The
renderer now creates dense frames before trimming a segment; it cannot jump
ahead to the following diagram and evaluate subtitles at that later time.
New manifests preserve the old MP4s. A real 62-second regression render checks
the burned subtitle after the segment boundary.

Pixel comparisons at Maya's and Aiden's mouth positions confirmed that only
the speaking side changed. The heat-map enlargement retains original pixels
and labels. The worked example explicitly uses hypothetical values:
`Ax=7`, `BAx=[7,14]`, final output `[8,15]`; the merged matrix gives the same
answer. The source figure and mathematical teaching frames remain separate
assets in both the movie and practice page.

Production Chromium checks used `https://192.168.10.112:8443`: both complete
movies played, a ranged MP4 download succeeded, title/description/timestamps
were available, Japanese help appeared initially, expression navigation played
the corresponding sentence, and the worked-example practice displayed its
matching diagram. A file-fed browser microphone exercised MediaRecorder,
upload and local ASR/phoneme/stress feedback to completion. Its QA recording
was removed and previous learning/review state restored.

This checks capture and evaluation mechanics in Linux Chromium. It does not
test a physical Mac microphone or establish human pronunciation accuracy.

## Sources, local inference and recovery

Four retrievable primary references were read along with LoRA; an unavailable
reference was logged and omitted. Source-based inspection covered matrix
dimensions, zero initialization, scaling, parameter/memory/checkpoint
distinctions, selected GLUE/E2E results and the subspace-overlap interpretation.
Content and editorial repair histories, omissions and bounded fallback reasons
remain accessible in the project. These checks and local-model reviews are
not complete scientific accuracy or formal CEFR certification.

Generation used the installed free local reader, Qwen TTS, Qwen ASR and aligner.
After source acquisition, Python worker stages and isolated speech actors
blocked external connections/DNS; slide Chromium blocked network requests.
KaTeX CSS/fonts were embedded locally. This is application-level isolation,
not an operating-system firewall for unrelated processes.

The pipeline checkpoints scripts, speech, captions, visuals, sentence clips
and finite 60-second encode segments. Automated tests exercise pause/resume
propagation, interrupted segment reuse, bounded repairs, local-only networking,
variable pauses and recording priority. Actual service restarts retained the
project and generated assets. The final project/root job are ready/completed.

Validation: **174 Python tests passed**, **11 frontend browser tests passed**,
TypeScript/Vite production build passed, and changed Python files passed Ruff.
The Python run has one existing Starlette test-client deprecation warning.
Daily discovery and automatic YouTube upload remain disabled. Three previously
paused lesson jobs remain paused; old lessons, recordings and MP4s are retained.

Reproduce installed-media checks:

```bash
PYTHONPATH=backend .venv/bin/python scripts/story_acceptance.py \
  8ab4bf9ac3274635925c609fc9860165 --screenshots --sync
.tools/node/bin/node scripts/story_browser_acceptance.mjs \
  8ab4bf9ac3274635925c609fc9860165 --require-complete
```

Raw frames, traces and reports are retained in
`data/evaluation/story-8ab4bf9ac3274635925c609fc9860165/` and
`data/evaluation/story-browser-complete.json`. Large movies/models are local
runtime artifacts and are intentionally excluded from Git.
