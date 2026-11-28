# How the model recommendations are made

The app recommends up to two writing models per computer type (no graphics card, about 8 GB,
16 GB or more): **Best quality**, and **Fastest** where one is clearly faster. This page explains
the method; the rules and their numbers are in the code it names.

Every record lives in the private repository `teachers-teammate-evals` (the `--store` of every
tool), because its texts come from corpora that may not be passed on. This repository holds the
code and these pages: commands in [TOOLS.md](TOOLS.md), file formats in [FORMAT.md](FORMAT.md).

## From candidate to recommendation

1. **Fit.** The model's need on the card is measured at the app's window
   (`verify_model_catalog fit`); `domain/model_catalog.py` decides whether it fits a tier (rule and
   share: *Fit and speed* below).
2. **Gate 1: answer format.** Six invented texts are proofread repeatedly with the JSON answer
   format; each answer is judged (cut off, invalid, empty, unchanged, too few sentences placed or
   explained). A pass earns the `explanations ✓` badge, without which a model is not recommended.
   Rule: `CheckRule`; fixtures: `tools/evaluation/checks/gate1_fixtures.py`.
3. **Gate 2: correction quality.** Real learner texts go through the app's own corrector, with
   its long-text path and temperatures, three times each, and are scored against the annotators'
   corrections. The model must clear every floor.
4. **Comparison.** A model takes a place only if it beats the current holder beyond the noise.
5. **Choice.** The two roles of the tier, by the rule in *Comparison and pick* below, by hand. The
   verdict, the runs it rests on and the command that reproduces the figures go into a gate record
   in the private repository (no figure).

## Test sets

`gec-2026-12`: eleven German texts (Falko-MERLIN) and eleven English texts (W&I+LOCNESS), whole,
with reference corrections, in three lengths (short, a page, several pages); plus nine invented
French items. `gec-2026-12-heldout` has the same shape from splits the first never reads.

- A set is drawn by a fixed rule and seed, pinned by hash and never rebuilt with other texts.
- A prompt, temperature or text budget is tuned on the first set and adopted only if it wins on
  both.
- French has no public learner corpus with corrections: reported, never deciding.

## Fit and speed

The first two criteria decide whether a model can be used at all; quality comes after them.

**Fit.** A model has to sit on the tier's graphics card. Its size is measured on a card of that
tier, at the window the app asks for (`verify_model_catalog fit`), never read from Ollama's own
report (it was off by more than a gigabyte for some models): what counts is the memory the card
holds while the model answers. A small part may sit in main memory and the model still fits, somewhat
slower; the share is `FULL_SPEED_SHARE` in `domain/hardware_profile.py`, and below a second share the
model is *very slow*. A model beyond the first share does not fit and is not compared on quality.
Without a card the model must be small enough for the processor (`CPU_COMFORT_GB`).

**Speed.** A page is `TYPICAL_PAGE_WORDS` words of student text through the whole exchange (correct,
explain, unclear), asked as the app asks it (its temperatures, prompt, sampling and window, the card
to the run alone) and measured on the tier's reference machine. The app's bands (`SpeedBand` in
`domain/performance_estimate.py`) word the result: *fast*, *ok*, *slow*, *very slow*. On a card only
a model that reaches at most *ok* can be a tier's quality model; without one the limit is
`CPU_COMFORT_GB`, a page in under five minutes, *slow* at most. A model no full exchange has been run
on gets the page time its measured writing rate gives, marked as computed in `report table --tiers`
until a run replaces it.

Speeds of different machines are compared only inside a tier (each tier has one reference
machine); quality is compared across machines freely.

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
- A change (a prompt variant, a temperature, another quantisation) is adopted only when it has the
  same sign and no loss beyond the noise on the held-out set, and wins beyond the noise on both sets
  pooled. A per-language gain counts for nothing on its own; both languages of both sets are read.

Two roles per computer type, **Best quality** and **Fastest**, each held by an eligible model: it
passes gate 1, the floors and the fit, and a teacher has marked its explanations.

- **Best quality** is the eligible model with the highest F0.5 among those whose page time on the
  tier's reference machine is within the limit of *Fit and speed*. A tie goes to the smaller model,
  then the faster one.
- **Fastest** is the eligible model with the shortest page time, of those no more than a tenth of an
  F0.5 point behind the quality model, and it has to be at least a quarter faster than that model.
  Otherwise the tier has none: a second name for the same wait helps nobody. The limits are
  `SPEED_ROLE_MAX_F05_GAP` and `SPEED_ROLE_FASTER_SHARE` in `domain/model_fit.py`.

A holder keeps its place unless a challenger is better beyond the noise; a place whose holder is no
longer eligible goes to the next one by the rule above. A floor that every model, or the current
holder, fails is reconsidered before use; the change and its reason go into the gate record. The
roles are read by hand from `report table --tiers` and the model tables; no tool chooses them.

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

## Where the details are

| What | Where |
| --- | --- |
| Fit and speed, per tier | `report table --tiers` (`tools/evaluation/report/tables/fit.py`); rule: `domain/model_fit.py` |
| Model sizes and rates | `model-fit/sizes.jsonl` in the store, written by `verify_model_catalog fit` |
| Gate 1 rule, verdict order, fixtures | `tools/evaluation/checks/check_rule.py`, `structured_check.py`, `gate1_fixtures.py` |
| Gate 2 floors | `tools/evaluation/report/gates.py` |
| Comparison, bootstrap, minimum effect | `tools/evaluation/report/stats.py`, `compare.py` |
| Metrics | `tools/evaluation/metrics/` |
| Test-set recipes and corpora | `tools/evaluation/input/recipes/`, `input/corpora/` |
| Protocol versions | `tools/evaluation/core/protocol.json` |
| Records, gate records, candidate list | the private `teachers-teammate-evals` |
