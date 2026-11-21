# How the model recommendations are made

The app recommends writing models per computer type: without a graphics card, with about 8 GB, with
16 GB or more. At most two models are recommended per type, the first as the best choice. This page
describes how a model gets there. The rules and their numbers are in the code; this page says what
they are for and how they fit together.

The records — test sets with their texts, every run, the teachers' marks, the machines, the measured
model sizes and the gate records — live in the private repository `teachers-teammate-evals`
(`~/git/teachers-teammate-evals`, named with `--store`). The texts come from learner corpora that may
not be passed to a third party, so that repository stays private. This repository holds the code
that writes and reads them and the pages that describe them. How to run the tools is in
`docs/development.md` (*The evaluation tools*); the file formats are in `FORMAT.md`.

## The path from candidate to recommendation

1. **Fit.** A candidate must run on the tier's computer. Its size on the card is measured at the
   window the app uses (`verify_model_catalog fit`), and the app's rating rules decide whether it
   fits at full speed (`domain/model_catalog.py`).
2. **Gate 1: does it keep the answer format?** Proofreading with explanations needs answers that
   follow a JSON schema. Six invented texts (three short, one with Markdown, two long) are sent
   repeatedly, and every answer is judged: cut off, invalid, empty, unchanged, too few sentences
   placed, too few explained. A model that never fails hard and rarely fails soft passes and gets
   the `explanations ✓` badge; it is not recommended without it. Rule: `CheckRule`; fixtures:
   `tools/evaluation/checks/gate1_fixtures.py`.
3. **Gate 2: are its corrections good?** The model proofreads real learner texts through the app's
   own corrector, in the app's own long-text path, at the temperatures the app sends. Every text
   is proofread three times, and the answers are scored against the annotators' corrections.
4. **Comparison.** A model takes a place only if it passes the floors and is better than the current
   holder beyond the noise.
5. **Pick.** Done once per screen, by hand, and written down as a gate record in the private
   repository: a verdict, the runs it rests on and the command that reproduces the figures, no
   figure.

## The test sets

`gec-2026-12` holds eleven German and eleven English learner texts, whole and with reference
corrections, in three lengths (short, a page, several pages), plus nine invented French items.
German comes from Falko-MERLIN, English from W&I+LOCNESS. `gec-2026-12-heldout` has the same shape
from splits the first never reads. Texts are drawn with a fixed seed and a rule per set, the set is
pinned by hash, and a set is never rebuilt with other texts. A prompt, a temperature or a text
budget is tuned on the first set and adopted only when it wins on both.

French has no public learner corpus with corrections, so its items are invented and French is
reported, never deciding.

## What is measured

Scores count changes. A model's change counts when it covers the same words with the same
replacement as a reference change.

- **F0.5**, which weighs precision twice as much as recall, because a wrong change costs a teacher
  more than a missed one. The match is exact, so absolute values are low for every model and only
  the comparison counts.
- **GLEU** against the unchanged source, as a guard: a model below its source made the text worse.
- **Left alone** and **coverage**: whether sentences the reference leaves alone stay alone, and
  whether sentences with an error get looked at. Either alone is easy to game.
- **Lost text, added marks, failed and fallback answers**: whether the model dropped a sentence,
  added Markdown, or could not answer.
- **Explained**: whether a change that needs a note carries one.

German and English decide; the mean over both of F0.5 and GLEU is the figure that ranks. Definitions
are in `tools/evaluation/metrics/`.

## Floors and the explanation check

A model must clear every floor before it is compared: no failed texts, no answer that was cut off
twice, at most one other fallback per language, no added marks, no text lost in two or more repeats,
and minimum coverage, left-alone and explained shares. The values are the fields of `GateTwoRule`
(`tools/evaluation/report/gates.py`). A group with fewer than three repeats or with a text missing
is judged incomplete, not passed.

The explanations themselves are checked by a teacher. A sample is drawn from the page-length texts
(ten explanations per language) and marked correct, wrong or vague; a minimum share must be correct.
A model whose explanations nobody has marked yet may take an alternative's place, not the best
choice. Corrections a teacher judges right but the annotators did not make can be accepted as
alternatives, which counts them as correct changes for every model.

## The comparison

Model *a* is better than model *b* when a paired bootstrap over the texts (so a text that is hard
for both is hard on both sides) puts the interval of the F0.5 difference clear of zero and the gap
is at least one point. A model that is ahead on F0.5 but behind on GLEU or on the page-length texts
is a tie, because the F-score alone can prefer a model that fixes less and makes fewer false alarms.
Every measure also gets its own verdict, and a model dominates another when it is worse on none,
better on one and not noticeably slower. Code: `tools/evaluation/report/stats.py`.

## The pick

Per computer type, the eligible models (gate 1, the floors, the fit) are ranked by F0.5. A current
holder keeps its place unless a challenger is better; a place whose holder is not eligible goes to
the highest-ranked eligible model, a tie going to the smaller and then the faster one. A floor that
every model fails, or that the current best fails, is reconsidered before it is used, and its
value, reason and new threshold are recorded.

## How a model is run once it is recommended

The measurements also set how the app asks each model:

- **Temperature.** Step ① of the proofreading, which rewrites the text, runs at a measured value per
  model (0.05 where lower was better, else 0.4). The explanation and unclear steps and the content
  review run at the value the model's maker recommends. Teachers set none.
- **Prompt variants** for step ① exist but are adopted only after a win on both test sets.
- **Thinking** is off, except for a model family that ignores the switch and is asked for its
  lowest level.

## What the figures do not say

The reference is one annotator's version, and a few "correct" sentences contain errors they missed.
British spelling in the English references makes American spelling count as an error. Eleven texts
per language show only large differences, which is why the spread of the repeats is printed next to
every verdict and why a tie is a result. French is indicative only. Speed is a fact about a machine:
runs on different machines are compared for quality, never for speed.

## Where the details are

| What | Where |
| --- | --- |
| Gate 1 rule, verdict order, fixtures | `tools/evaluation/checks/check_rule.py`, `structured_check.py`, `gate1_fixtures.py` |
| Gate 2 floors, measuring and rating | `tools/evaluation/report/gates.py` |
| Comparison, bootstrap, minimum effect | `tools/evaluation/report/stats.py`, `compare.py` |
| Metrics | `tools/evaluation/metrics/` |
| Test-set recipes and corpora | `tools/evaluation/input/recipes/`, `input/corpora/` |
| Request version and fingerprint | `tools/evaluation/core/protocol.json` |
| Commands | `docs/development.md` |
| File formats | `evaluations/FORMAT.md` |
| Records, gate records, candidate list, run times | the private `teachers-teammate-evals` |
