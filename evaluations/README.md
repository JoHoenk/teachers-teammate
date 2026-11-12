# Evaluations: what is run, on what data, how it is checked, how long it takes

A writing model has to pass two gates before the app recommends it. This page says what each gate
runs, where its input comes from, which checks keep the numbers honest, and how much machine time
to plan. The commands and the metric definitions are in `docs/development.md` (*Choosing writing
models*); this page is the overview that points there.

| Where | Content |
| --- | --- |
| `writing-models-2026-11/` | The candidate survey, and where gate-1 results and raw records go. The early measurements were deleted on 2026-11-12. Numbers and verdicts only. |
| `proofreading/` | Gate 2 runs, one file per run, plus the test set's manifest. Format: `proofreading/FORMAT.md`, usage: `proofreading/README.md`. |
| `model-fit/` | What each model needs on the card, measured at every window of the app's ladder: `sizes.jsonl`, one record per model, server and window, plus the folder's `README.md` with the fields and the machines. Numbers only. |

## 1. The two gates

| | Gate 1 | Gate 2 |
| --- | --- | --- |
| Question | Does the model fill in the app's review answer format reliably, at the size the app sends, for the whole text? | Are its corrections and explanations good, compared with what the tier recommends now? |
| Tool | `bazel run //tools:verify_model_catalog -- structured-output` | `bazel run //tools:compare_writing_models -- run …` |
| Input | Six invented texts that live in the repository (`tools/model_eval/structured_check.py`) | `gec-2026-12`: 22 real learner texts (German, English) and 9 invented French items; the corpora are not in the repository |
| Decides | `structured_output` in the model catalog (the `explanations ✓` badge, and whether the model may be recommended) | Whether a model becomes a tier's star or alternative (`GateTwoRule` and `compare`) |
| Does not decide | Whether a correction is right | Whether the format holds up on tiny inputs |
| State | Built; re-running it on the server for the candidates is open | Tools built; the pass/fail rule (`GateTwoRule`) exists, its floors are calibrated on the baselines |

Everything so far rates the **one-step** approach (corrections and explanations in one JSON
answer). The two-step approach (corrected text first, then an explanation per edit) has its own
checks, planned.

## 2. Gate 1 in detail

### 2.1 What is sent

The app's own review request: its system prompt (level B2, in the text's language), its user
message, the JSON schema as `format`, `think=False`, temperature 0.2 (the app's default) and
`num_ctx` 8192. A test builds the app's corrector and compares the request with the gate's, so the
two cannot drift apart.

Six fixtures, all invented, so they can be in the repository:

| Fixture | Language | Characters | Role |
| --- | --- | --- | --- |
| plain EN | English | 114 | Smallest case: two sentences, two errors |
| plain DE | German | 104 | Same, German |
| plain FR | French | 105 | Same, French |
| Markdown | English | 106 | Formatting marks (`**`, `_`) must survive |
| full page DE | German | 1,984 (335 words) | A dense page; 22 sentences, 10 of them with an error |
| page DE at budget | German | 2,756 at 8192 (5,827 at 16384) | The largest text the app sends in **one** request. The invented source is 6,533 characters; the fixture is its longest prefix of whole sentences that fits the app's budget |

German is the worst case for size because it tokenises longest of the three languages.

**Why the size is the app's, not half the window.** The gate asks the app's own corrector how many
characters one request carries (`NativeOllamaCorrector.max_text_chars`: prompt taken out of the
window, the rest shared with an answer seven times the text). At 8192 that is 2,787 characters of
German. A text longer than that is split into pieces by the app, so the gate tests the longest text
the app would ever send whole. Both long fixtures are written so that their last third contains
at least two error sentences, which lets the check see a model that stops halfway.

### 2.2 How a single run is judged

First failing step wins:

| # | Verdict | Meaning | Kind |
| --- | --- | --- | --- |
| 1 | too long | Fixture longer than the app's budget (CI prevents it for the shipped fixtures) | hard |
| 2 | request failed | Timeout or server error | hard |
| 3 | cut off | `done_reason = length`, whether or not what arrived still validates | hard |
| 4 | invalid | Answer does not validate against the schema | soft |
| 5 | no sentences | Valid, but lists nothing | soft |
| 6 | unchanged | Corrected text equals the source | soft |
| 7 | too few placed | Fewer than 90 % of the answer's sentences can be found in the source text (the app could not put the correction in place) | soft |
| 8 | partial | Long fixtures only: fewer than 80 % of the error sentences touched, or none of those in the last third | soft |
| 9 | too few explained | Fewer than 40 % of the changes that need a note carry one with text | soft |

"Explained" checks that an explanation exists, not that it is right. That is gate 2, and for
explanations a teacher marking a sample (10 per language, 8 must be correct).

### 2.3 How a model is judged

Above temperature 0 every fixture runs three times (18 runs).

* Any hard failure: fail, in either round.
* At most 1 soft failure: pass.
* Exactly 2: a second round of 18 runs; pass with at most 4 soft failures over the 36.
* 3 or more in the first round: fail.
* A pass also needs the explained share over all runs to reach 0.75.

At temperature 0 a repeat gives the same answer, so every fixture runs once (6 runs) with the "at
most 1" rule and no second round.

Chances for a model whose runs fail independently: a 5 % failure rate passes 93 % of the time, a
10 % rate 66 %, a 2 % rate 99 %.

### 2.4 Quality checks of the check itself

* **Offline tests** (`tests/tools/test_check_rule.py`, `test_structured_check.py`,
  `test_verify_model_catalog.py`; every test has a Given/When/Then docstring): the verdict order,
  the 18/36 rule, the coverage verdict with a scripted answer that stops halfway, the margin warning,
  the second round, saved answers and `recheck`.
* **Fixtures are pinned:** the full page and the four short texts are tested byte for byte; sentence
  spans must tile the text; every fixture without `fill_budget` must fit the budget at 8192 and
  16384; the budget fixture must fill at least 90 % of the budget.
* **Protocol fingerprint:** the record carries the eval version, the fingerprint of the request
  (prompts, schema, window) and the answer format version. The tool refuses to run if the fingerprint
  is not the recorded one, so a verdict is only ever made on the request it names.
* **Header:** every record starts with date, command, tool commit, Ollama version, window,
  temperature, and per model the digest, quantisation and size. A verdict belongs to the exact tag
  and digest it was made on.
* **Saved answers:** `--answers DIR` keeps every answer (local only). `recheck FILE…` judges them
  again with the current rule, without a server; it refuses files with another fingerprint or a text
  that no longer hashes to what was sent.
* **Margin:** the tokens the window had left. Not a verdict, but the summary warns when the budget
  fixture leaves under 10 % of the window.

### 2.5 What gate 1 does not tell you

How good the corrections are, how fast the model is on a user's computer, how it behaves at 16384
(no model reaches that window by the automatic rule today), or anything about the two-step approach.
Fixtures are invented, so real pages may produce longer answers than the fixture does.

## 3. Gate 2 in detail

### 3.1 Where the input comes from

The test set `gec-2026-12` is built by `tools/prepare_gec_testset.py` from the recipe
`tools/model_eval/recipes/gec_2026_12.py`, from two public corpora that stay outside the repository:
Falko-MERLIN (German) and W&I+LOCNESS (English). French is an invented set of eight texts and ten
correct sentences, so French is reported but never decides.

| Language | Short (80 to 150 words) | Page (200 to 400) | Pages (600 to 1,200) | Words in total |
| --- | --- | --- | --- | --- |
| German | 4 | 5 | 2 | 3,984 |
| English | 4 | 5 | 2 | 3,978 |
| French | 8 invented texts + 1 correct-only item | | | 596 |

* The bands are in whitespace words. The `pages` texts are longer than the app's budget, so the app
  splits them; one text per language fits in one request, so both paths are measured on real texts.
* Each band is drawn from the corpus's default split with a fixed seed. Filters: at least 3
  reference changes, at most 40 per 100 words (a near-rewrite measures agreement with one annotator),
  labels that fit, sentence spans found.
* The manifest (`proofreading/testsets/gec-2026-12/manifest.json`) pins every text by hash, with
  positions and error-label codes, but contains **no corpus text**. It is immutable once committed.

### 3.2 What a run does

Every text goes through the app's own `NativeOllamaCorrector` inside its `ChunkingCorrector`, so
long texts are proofread in pieces exactly as in the app. Per model: three repeats, review mode and
text mode (corrected text only), temperature 0.2. Runs are appended, so an interrupted run resumes.

### 3.3 What is measured

Strict F0.5 (exact match with the reference change), GLEU against the unchanged source, share of
correct sentences left alone, coverage of the sentences the reference changes, recall in the first
and second half of the text (catches models that stop early), added Markdown marks, failed and
fallback answers (and how many were cut off), explained share. Metric definitions:
`docs/development.md`, *Choosing writing models*.

### 3.4 Quality checks of the test set and the runs

* **Hash-pinned set:** the manifest fixes source and reference by SHA-256; a run header records
  the manifest hash, so a different set cannot be compared by accident.
* **No corpus text in the repository:** `proofreading_store validate` enforces it, and CI runs it
  over every committed run file.
* **Error labels** (`R:DET:FORM` and so on) are matched to the text by position: on `gec-2026-12`
  all 631 German and 350 English reference changes got a label, and a random sample of 24 was read
  by hand.
* **Protocol version** (`tools/model_eval/protocol.json`): `run` stops with exit code 2 when the
  prompts, the answer format or the window changed without a new version entry. Runs are grouped by
  version, never by commit.
* **Known limits:** the reference is one annotator's version; a few "correct" sentences contain
  errors the annotators missed; eleven texts per language show only large differences; the spread
  of repeats decides what counts as a difference.
* **Floors (starting values, calibrated on the full screen):** no failed text, no cut-off answer, at most one other
  fallback per language, coverage ≥ 0.90, explained share ≥ 0.85, GLEU above the source's, left alone
  not worse than the star's. Winning needs a better mean F0.5 beyond the spread of the repeats.

## 4. Which models are evaluated

Reasons, download sizes and what is unverified: `writing-models-2026-11/candidate-models-2026-11-09.md`.
Nobody is left out for weak scores or slowness while the screen runs; the choice comes at the end. A model enters the catalog only after gate 1 and gate 2, and only to replace a tier's star or its
alternative (at most two recommended models per tier).

| Group | Models | State |
| --- | --- | --- |
| Current catalog stars (baselines) | no card `qwen3:4b`; 8 GB `gemma4:e4b` (default), alternative `qwen3.5:9b`; 16 GB `gemma4:12b`, alternative `qwen3.5:9b` | Gate 2 of 2026-11-07 was on the old test set; the full screen repeats it on `gec-2026-12` |
| Kept in the catalog, not recommended | `mistral:latest`, `mistral-nemo:12b`, `gpt-oss:20b` | Gate 1 re-run. `gpt-oss:20b` was cut off on a full page before and is again today, so it stays without the badge unless it passes |
| Chunk 1, no card | `gemma4:e2b`, `qwen3.5:4b`, `ministral-3:3b` (passes or near-misses under the old rule), `granite4.1:3b`, `lfm2.5:8b` (failed clearly) | Gate 1 under the old rule on 2026-11-09; the first three are re-run. `gemma4:e2b` against `qwen3:4b` in gate 2 decides the no-card star |
| Chunk 2, 8 GB | EuroLLM 9B, `granite4.1:8b`, Apertus 1.5 8B, `nemotron-3.5-bolt:e4b` | Being pulled; gate 1 not run yet |
| Chunk 3, 16 GB | EuroLLM 22B (`IQ4_XS`), `lfm2.5:24b`; then 8-bit builds of the winners | Open |
| Stretch | `gemma4:26b-a4b-it-qat`, `qwen3.6:35b-a3b` | Only after the rest; they need a lot of main memory |
| Back in (2026-11-11) | `ministral-3:8b`, `ministral-3:14b` | Were dropped for scores on the old test set (mean F0.5 0.41 and 0.43 against 0.61 and 0.63 for `gemma4`; the 14B is about four times slower, weak in French). Rule now: nobody is left out for scores or speed before all are rated; the choice comes at the end |
| Dropped | The Unsloth Qwen3.5-9B build | Ships none of the official sampling settings; a different packaging of `qwen3.5:9b`, not a different model |

The gate-1 re-run covers: `qwen3:4b`, `qwen3.5:9b`, `gemma4:e4b`,
`gemma4:12b`, `mistral:latest`, `mistral-nemo:12b`, `gpt-oss:20b`, `gemma4:e2b`, `qwen3.5:4b`,
`ministral-3:3b`, `ministral-3:8b`, `ministral-3:14b`, plus any chunk-2 model that passed under the old rule.

## 5. How long it takes (rough, for planning chunks)

### 5.1 Measured: gate 1 on the server (2026-11-11, 18 runs per round, temperature 0.2)

Server: AMD RX 9070 XT with 16 GB. Source: the saved answers of the re-run
(`~/gec-testset/gate1-2026-11/`, written 08:41 UTC). Runs include model loading and other load on
the server.

| Model | Runs | Time | Speed (answer tokens/s) | Slowest run | Remarks |
| --- | --- | --- | --- | --- | --- |
| `gemma4:e2b` | 18 | 1.3 min | 220 | 28 s | 1 cut-off run |
| `ministral-3:3b` | 18 | 2.4 min | 153 | 23 s | |
| `qwen3.5:4b` | 18 | 3.3 min | 107 | 46 s | |
| `gemma4:e4b` | 36 | 4.5 min | 94 | 21 s | second round |
| `qwen3:4b` | 36 | 5.8 min | 128 | 30 s | second round |
| `gpt-oss:20b` | 18 | 11.9 min | 102 | 74 s | thinking tokens; 6 cut-off runs |

### 5.2 Rule of thumb

One round of gate 1 produces about 20,000 to 25,000 answer tokens when no run runs away:

```
minutes per round  ≈  22,000 / (tokens per second × 60)  +  1 for loading
```

| Model class | Tokens/s | Per round | With a second round |
| --- | --- | --- | --- |
| 2 to 4B | 100 to 220 | 2 to 5 min | 4 to 10 min |
| 8 to 12B, dense | 45 to 60 (estimate, not yet measured here) | 7 to 10 min | 14 to 20 min |
| 20B with thinking | 100 (measured) | 12 min | 24 min |

A model that fails hard still runs all 18 runs. The worst case is a run that loops until the window
is full: about 7,000 tokens, or 1 min at 150 tokens/s and 2 to 3 min at 45. A loop that shifts the
context can be far worse: `ministral-3:3b` once generated 81,920 tokens in a single run (691 s).
The timeout (900 s) bounds one run, so 18 looping runs of a slow model could take up to 4.5 h.

### 5.3 Gate 2 (estimate; the first real measurement is the full screen)

Per repeat the set has about 8,560 words, i.e. 43,000 to 86,000 answer tokens in review mode (5 to
10 tokens per word, from the gate-1 full-page answers) and about 13,000 in text mode.

| Per model | Review, 3 repeats | Text, 3 repeats |
| --- | --- | --- |
| 100+ tokens/s | 20 to 45 min | 5 to 10 min |
| 45 to 60 tokens/s | 45 to 90 min | 10 to 20 min |

So eight models in two modes come to roughly 6 to 10 hours, which fits two overnight chunks.
Plan them by model class: small models first (cheap, and they decide the no-card tier), then the
8 to 12B models. Replace these figures by measured ones after the first model of each class.

### 5.4 Not built (proposal)

* **Stop a model early** once the outcome is settled (a hard failure, or three soft failures in the
  first round). It saves most of the time on models that fail, at the price of fewer runs in the
  record for them.
* **Progress and forecast in the command:** after each run, print runs done, elapsed time and a
  forecast from the seconds per run so far; before the run, print the number of runs and models.
