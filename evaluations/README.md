# How the model recommendations are made

The app recommends up to two writing models per computer type (no graphics card, about 8 GB,
16 GB or more), the first being the best choice. This page explains the method; the rules and
their numbers are in the code it names.

Every record lives in the private repository `teachers-teammate-evals` (the `--store` of every
tool), because its texts come from corpora that may not be passed on. This repository holds the
code and these pages: commands in [TOOLS.md](TOOLS.md), file formats in [FORMAT.md](FORMAT.md).

## From candidate to recommendation

1. **Fit.** The model's need on the card is measured at the app's window
   (`verify_model_catalog fit`); `runs_at_full_speed()` in `domain/model_catalog.py` decides
   whether it fits a tier.
2. **Gate 1: answer format.** Six invented texts are proofread repeatedly with the JSON answer
   format; each answer is judged (cut off, invalid, empty, unchanged, too few sentences placed or
   explained). A pass earns the `explanations ✓` badge, without which a model is not recommended.
   Rule: `CheckRule`; fixtures: `tools/evaluation/checks/gate1_fixtures.py`.
3. **Gate 2: correction quality.** Real learner texts go through the app's own corrector, with
   its long-text path and temperatures, three times each, and are scored against the annotators'
   corrections. The model must clear every floor.
4. **Comparison.** A model takes a place only if it beats the current holder beyond the noise.
5. **Pick.** By hand, once per screen, written down as a gate record in the private repository.

## Test sets

`gec-2026-12`: eleven German texts (Falko-MERLIN) and eleven English texts (W&I+LOCNESS), whole,
with reference corrections, in three lengths (short, a page, several pages); plus nine invented
French items. `gec-2026-12-heldout` has the same shape from splits the first never reads.

- A set is drawn by a fixed rule and seed, pinned by hash and never rebuilt with other texts.
- A prompt, temperature or text budget is tuned on the first set and adopted only if it wins on
  both.
- French has no public learner corpus with corrections: reported, never deciding.

## Metrics

A model's change counts when it covers the same words with the same replacement as a reference
change. Definitions: `tools/evaluation/metrics/`.

| Metric | Purpose |
|---|---|
| **F0.5** | Precision weighted twice as much as recall: a wrong change costs a teacher more than a missed one. Exact match, so only comparisons count, not absolute values. |
| **GLEU** | Against the unchanged source as a guard: below the source means the text got worse. |
| **Left alone**, **coverage** | Correct sentences stay unchanged; sentences with an error get looked at. |
| **Lost text, added marks, failed and fallback answers** | Sentences dropped, Markdown added, no usable answer. |
| **Explained** | A change that needs a note carries one. |

German and English decide; the ranking figure is the mean of F0.5 and GLEU over both.

## Floors

Before any comparison a model must pass every floor of `GateTwoRule`
(`tools/evaluation/report/gates.py`): failed texts, cut-off and other fallbacks, added marks, lost
text, and minimum coverage, left-alone and explained shares. A group with fewer than three repeats
or a missing text is *incomplete*, not passed.

A teacher marks a sample of the explanations from the page-length texts as correct, wrong or vague;
a minimum share must be correct. Until marked, a model can be an alternative, not the best choice.
Corrections the annotators missed but a teacher accepts count as correct for every model.

## Comparison and pick

- *a* beats *b* when a paired bootstrap over the texts puts the F0.5 difference clear of zero and
  above the minimum effect (`tools/evaluation/report/stats.py`).
- Ahead on F0.5 but behind on GLEU or on the page-length texts is a tie.
- Per computer type, eligible models (gate 1, floors, fit) are ranked by F0.5. A holder keeps its
  place unless beaten; an ineligible holder's place goes to the best eligible model, ties to the
  smaller, then the faster.
- A floor that every model, or the current best, fails is reconsidered before use; the change and
  its reason go into the gate record.

## How a recommended model is asked

- **Temperature:** step ① (the rewrite) uses a measured value per model; the other steps and the
  content review use the maker's value (`request_profile()`, `review_temperature()`). Teachers set
  none.
- **Prompt variants** for step ① are adopted only after a win on both test sets.
- **Thinking** is off; a family that ignores the switch gets its lowest level.

## Limits

- The reference is one annotator's version and misses some errors.
- British spelling in the English references counts American spelling as an error.
- Eleven texts per language show only large differences; the spread of the repeats is printed with
  every verdict, and a tie is a result.
- Speed is compared only between runs on the same machine.

## Where the details are

| What | Where |
| --- | --- |
| Gate 1 rule, verdict order, fixtures | `tools/evaluation/checks/check_rule.py`, `structured_check.py`, `gate1_fixtures.py` |
| Gate 2 floors | `tools/evaluation/report/gates.py` |
| Comparison, bootstrap, minimum effect | `tools/evaluation/report/stats.py`, `compare.py` |
| Metrics | `tools/evaluation/metrics/` |
| Test-set recipes and corpora | `tools/evaluation/input/recipes/`, `input/corpora/` |
| Protocol versions | `tools/evaluation/core/protocol.json` |
| Records, gate records, candidate list | the private `teachers-teammate-evals` |
