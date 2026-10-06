# FlashSAC video: visual storytelling audit

The published FlashSAC videos did not explain the ideas well enough visually.
Adding conversational jokes to a nearly static paper page did not solve this.
The pipeline finished, but its fallback behavior lost the distinctions that the
viewer needed to see.

## Evidence from the completed videos

Project: `a641225d9e9b439e8e6889e08da08977`.
Pipeline: `youtube-storyboard-3-concept-math`.

Actual MP4 frames, the saved storyboard reviews, and the scripts were inspected.
Contact sheets and their source data are retained under
`data/evaluation/flashsac-visual-audit/` and
`data/evaluation/flashsac-visual-audit-source.json`.

- Four of six overview scenes used Figure 10. All ten deep-dive scenes used it.
  Repeated full-page plots were too small to read and did not show the mechanism
  being discussed. One enlarged panel did not make the other topics visible.
- The deep dive contained no equations, despite its planned mathematical scenes.
- An unrelated surface-cell drawing was relabeled as an RL limitations diagram.
- The renderer could draw text boxes, but the writer and reviewer sometimes
  assumed those boxes contained vehicles, trajectories, distributions, or photos.
- The overview mixed trucks, diets, suspension, and maps. The deep dive added
  mirrors, cameras, naps, and cooking. These substitutions frequently replaced
  causal explanation rather than helping it. Some dialogue conflated fewer
  optimizer updates with larger learning-rate steps.
- A source-ID validation failure could collapse a whole experimental scene into
  one surviving evidence sentence. This removed the question and explanation.

The original extraction regex required punctuation after a figure number.
FlashSAC's actual captions use `Figure 1 Results Overview` without punctuation.
As a result, it skipped Figures 1–9 and incorrectly found prose such as
`Figure 10.(b) compares ...`. After three storyboard failures, the fallback reused
the only checked original, regardless of the scene's topic. Original choice also
allowed a zero-relevance keyword match. These were implementation errors, not
simply a need for more decorative images.

## Corrections

The new pipeline is `youtube-storyboard-4-purposeful-pictures`, with renderer
`story-film-8-purposeful-pictures`.

1. Extract unpunctuated captions and reject ordinary prose references. Keep the
   current extraction catalogue separate from retired false-positive crops.
   Actual FlashSAC Figures 1–10 were extracted. The real robot sequence in Figure
   6 and the data-coverage comparison in Figure 7 were inspected directly.
2. Describe the renderer's actual capabilities to the local writer. Add fixed
   pictures for observations/actions, replay memory, update frequency, norm
   bounds, exploratory trajectories, return distributions, and a generic signal
   path. Source results and photographs still come from checked originals.
3. Require an appropriate domain for special templates. A robot-control paper
   cannot use the surface-cell drawing. Fallbacks must choose a relevant original
   or an applicable schematic; a generic keyword is insufficient for automatic
   original selection. A fallback also uses a relevant verified panel after the
   whole original, instead of leaving a readable crop unused on a tiny page.
4. Preserve exact source equations even when reused reading notes omitted them.
   Supply nearby definitions. Add distinct conceptual pictures for reward plus
   future value, policy diversity/value tradeoffs, and target-weight smoothing.
   A planned math scene cannot silently become an unrelated original figure.
5. Expand explicitly supplied claim aliases to their actual source IDs. Preserve
   substantive two-speaker drafts instead of accepting a lone surviving sentence.
6. Audit the whole film before speech generation. If most scenes reuse the same
   original without a new verified panel, perform one bounded repair of the
   pictures **and** dialogue. Save the previous version and repair reason. A real
   renderer failure similarly rewrites the conversation for the replacement
   picture; stale panel pointers alone are repaired without changing the image.
   Each changed picture gets a new bounded script attempt record, so an exhausted
   old attempt cannot immediately restore the incompatible old dialogue.
7. Keep a concrete problem through the story. Humor should reveal a mistaken
   prediction or pay off an opening detail. Maya must correct Aiden's mistake,
   rather than agree with it. Keep frequency, step size, batch size, and capacity
   distinct, and attribute comparisons to tested conditions.

This is not an image-count quota. Reusing an original to inspect different
verified panels is valid. The important checks are visible operations, relevant
evidence, readable labels, and matching narration.

## Validation and regeneration

Regression checks cover real local 1920×1080 rendering of the pictorial and math
templates, caption/prose discrimination, source alias resolution, preservation
of exact equations, applicable fallback choice, bounded film-wide repair, and
dialogue regeneration after renderer failure. Chromium blocks external requests.
The existing subtitle, audio-timing, original-zoom, interruption, and worker
recovery tests are included in the related test run.

Rendered examples with real bilingual labels are saved under
`data/visuals/flashsac-visual-repair-preview/`. These are renderer examples, not
claims that the rebuilt full videos have passed editorial inspection.

A new FlashSAC project, `2330dc5cf98a4b0f94c41588559c3d72`, regenerates both films
through the free local-model pipeline. The old MP4s and recordings are retained.
The saved job is `4436b8d9ed204b84b6e7d74585618ce0`.

Completion and audience enjoyment must not be inferred from passing software
tests. Inspect the new film's actual frames and dialogue, especially its opening,
mechanism, experimental comparison, math, and ending, before calling the content
successful. The API exposes the saved per-film `visual_audit` for that review.
