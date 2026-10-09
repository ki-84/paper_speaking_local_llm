# Two films from one paper

Open the library, select a saved paper, and press **解説・詳解動画を作る**.
The same control is available in the paper's learning page. A repeated click
reuses the project for that exact stored paper version and generation profile.

The overview follows a practical problem, historical attempts, an intuitive
reveal, an everyday example, representative evidence, and a payoff. It contains
no equations. The deep dive introduces notation before equations, works through
examples, and distinguishes findings from guarantees. Neither film has a fixed
duration or scene word quota. An engaging, clear explanation determines the
length; the scene beats organize the story rather than divide it into equal
time slots.

The `content-first-1` duration policy is saved in new projects, tracks and export
manifests. Writers and editors preserve useful causal reasoning, concrete
examples, scientific qualifications and humor, while removing duplicated
explanations, filler and unexplained jumps. A short film is not expanded to fill
minutes; a long film is neither trimmed to a word budget nor automatically sped
up. Actual duration is measured after alignment and shown on finished cards.
Media-duration, complete-caption and source checks remain in place.

Unfinished older checkpoints adopt this policy when their worker resumes, with
the adoption recorded. Their scripts, WAVs, alignments and learning IDs are
reused; already applied audio processing is retained rather than undoing it.
Legacy word budgets are omitted from writer/editor input. Editorial reviews
that predate this policy get a fresh bounded review and retain their previous
records. Finished films are unchanged. The per-paragraph 135-word speech limit
and finite local inference token budgets remain technical safeguards, not
film-length targets; a longer explanation uses more conversational turns.

On October 4, 2026, the duration-policy regressions, all 285 Python tests,
16 isolated browser tests, frontend build and changed-file lint checks passed.
The deployed LAN studio displayed measured runtimes for both editions of three
real papers, and all 28 existing ready MP4 hashes remained unchanged. The nightly
02:00 schedule and paused jobs were preserved. No new full film was generated
just to validate this change; the generation regressions use controlled local
runtime fixtures. [content_first_duration_acceptance.json](../evaluation/content_first_duration_acceptance.json)
records these checks and their limits.

Both use natural C1 American English, Maya and Aiden, bilingual burned-in
captions, original pixel characters, and structured local diagrams. A paragraph
is synthesized continuously; subtitle cues and sentence practice clips are
aligned slices of that recording. New films use variable pauses, without
changing the one-second pauses in existing video revisions.

## Sequential audience rehearsal

New projects use `youtube-storyboard-6-audience-rehearsal`. Three local AI
personas represent a curious non-specialist, a Python practitioner without this
field's specialist background, and a Japanese learner of B2 English using
bilingual captions. The first persona emphasizes spoken comprehension; natural
B2/C1 English remains the production target.

Before speech generation, the opening is tested with only an estimated first
30 seconds of words. Each later scene receives only what these simulated
viewers previously understood, rather than the author's summaries or answer
key. Every understanding point and gap cites an actual line. The editor then
receives the gaps and checked sources separately, compares the viewers'
retellings with the intended question, and repairs missing causal steps,
transitions, unexplained terms and reasons to keep watching. Edits are checked
against source text before speech generation. Rehearsals and repairs are saved,
bounded and do not consume attempts during GPU waits or recording preemption.

The running example keeps its named objects across scenes. A change from the
main example to another paper experiment needs an explicit bridge. Fixed local
drawings can now show bananas, plates, lids, doors, grippers and token windows
instead of decorative document icons. Worked examples show the viewer's
question before revealing the takeaway at the final step.

Completed-film reviews repeat these tests using measured MP4 caption times,
actual frames and the saved spoken dialogue. The opening excludes words spoken
after 30 seconds. The report separates clarity, engagement and useful humor,
and records each unanswered question and the specific addition it needs.
Existing frame/audio checks are reused only when the export ID and MP4 hash
both match. These are AI simulations, not human viewing tests or predictions of
YouTube audience retention. No old MP4 or recording is overwritten.

## Saved stages and recovery

`video_projects` groups two `paper-story-1` lessons. Main-paper reading notes
are reused only as evidence; dialogue and audio are new. Up to six historical
references are retrieved from the bibliography and read locally. Sources that
cannot be retrieved are recorded, and unsupported detail is omitted.

The worker saves each outline, script revision, diagram, paragraph WAV,
alignment and caption batch. It prioritizes recordings. Local repairs have
three attempts and a documented fallback. GPU shortages wait without consuming
repair attempts. Video encoding saves 60-second segments, retaining completed
segments across interruption and restart. Actual storage/model failures are
reported; they are not mislabeled as successful output.

**Pause** stops the project and its render jobs at a work boundary. **Resume**
continues them using saved checkpoints. Existing lessons, audio and recordings
are retained. This feature does not enable daily discovery, other paused lesson
jobs, or automatic YouTube upload.

An opening preview appears while generation continues. Finished cards provide
the MP4, thumbnail, three title suggestions and a description with timestamps
and source titles/identifiers without URLs. The MP4 basename uses the recommended title. Upload and
publication use YouTube Studio manually.

The learning links open the same spoken content, reusable C1 expressions,
Japanese meanings, comprehension questions and recording practice. Scientific
understanding and English expression receive separate feedback.

## APIs and local dependencies

- `POST /api/papers/{paper_id}/video-projects`: idempotent creation; returns
  `project_id` and `job_id`.
- `GET /api/video-projects`: project summaries.
- `GET /api/video-projects/{project_id}`: both tracks, scripts, progress,
  references, review notes and preview/full exports.
- Existing `/api/jobs/{job_id}/{pause|resume|retry|cancel}` and SSE `/api/events`
  provide control and updates.

The schema migration is additive (`user_version=4`). It runs at startup.
Inference uses the pinned local Qwen reader, Qwen speech models and aligner.
Source retrieval is allowed only during acquisition. Subsequent Python worker
stages reject non-loopback sockets and DNS; speech actors also set Hugging Face
offline flags. Chromium rejects network requests. KaTeX 0.19.0, its fonts and
CSS are installed locally through the npm lockfile. No cloud image or LLM API
is involved.

The socket guard also runs inside each isolated speech actor, including model
loading. This is an application-level network restriction, not an operating
system firewall for unrelated programs. Acquisition deliberately remains online.
For newly imported papers, PDF figure candidates are extracted locally and up
to six early figures are reviewed with bounded attempts; uncertain crops are
excluded. Already checked source figures are reused, and rendered lesson slides
are never admitted to the original-figure catalogue.

Original figures and mathematical teaching frames are distinct assets. A scene
can first show the unchanged paper figure and then reveal its equations. The
LoRA fallback contains a checked, explicitly hypothetical input calculation and
checks matrix dimensions against the retrieved primary text. Mathematical
checks do not certify every possible scientific claim generated by the model.

The hook selection follows the [YouTube intro guidance](https://support.google.com/youtube/answer/9314415?hl=en):
title, thumbnail and the first 30 seconds promise the same insight. Formula
rendering follows the [KaTeX Node API](https://katex.org/docs/node), with its CSS
and font bytes embedded rather than fetched from a CDN.

Both films begin with a short 15–25-second conversation introducing today's
paper or research topic and what viewers will understand. Maya gives one or two
specific sentences, then Aiden's question leads naturally into the hook. The
overview introduces the practical idea; the deep dive introduces the principle
it will unpack. They reach the substantive question within the first 30 seconds.
Only the first scene has this orientation, so later scenes continue the
conversation. Planning, hook selection, scripts and editorial review all share
this policy; reviews preserve the introduction rather than treating it as filler.
The track records `opening_policy.version = topic-before-hook-2`. A short,
paper-specific joke or playful misunderstanding in the opening sets up a
callback in the ending. The spoken
introduction uses the same local speech, Japanese translation, captions and
sentence-practice pipeline as the rest of the conversation.

The final scene gives a concise, satisfying recap of the paper's problem, key
idea, supported result and a remaining limitation, without a fixed time budget.
Aiden adds his own takeaway, the two call back to the actual opening joke or
analogy, and finish with a friendly spoken goodbye. The final writer and editor
receive the opening's actual first exchanges, rather than only its outline,
to make the callback consistent. Both the general and LoRA deep-dive plans use
this ending, and intermediate scenes do not say goodbye. The track records
`closing_policy.version = summary-callback-farewell-1`. A short, non-factual
two-voice farewell is retained if bounded editorial repairs remove the
authored goodbye; this fallback is recorded and never adds research claims.
All closing speech uses the existing local TTS, translation, subtitle,
mouth-animation and same-audio English practice pipeline.

Run `PYTHONPATH=backend .venv/bin/pytest -q` and the frontend build before
deployment. `tests/test_story.py` covers project idempotency, job controls,
bounded repairs, C1 text/citation policies, alignment, caption splitting,
local-only sockets, and a real equation/subtitle MP4 render with no external
connections. Synthetic audio in that test verifies media mechanics, not speech
recognition or pronunciation accuracy. Actual LoRA output is recorded separately
in the acceptance artifact after generation.

For installed media, run:

```bash
PYTHONPATH=backend .venv/bin/python scripts/story_acceptance.py PROJECT_ID --screenshots --sync
.tools/node/bin/node scripts/story_browser_acceptance.mjs PROJECT_ID --require-complete
```

The second script uses LAN HTTPS to check video playback, range download,
packaging, Japanese help and practice expression navigation. Its optional
`--microphone` uses a pre-created `data/evaluation/story-microphone.wav` as
Chromium's microphone input and exercises capture, upload and local evaluation.
It removes its QA recording and restores the previous study position. A real
Mac microphone and human learner pronunciation accuracy remain separate checks.

The installed LoRA films and measured synchronization results are documented
in [story_lora_verification.md](story_lora_verification.md). `--sync` compares
actual MP4 frames/audio with the source master timeline and records AAC offset;
it checks the current export rather than silently accepting an older revision.
