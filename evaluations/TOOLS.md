# The evaluation tools

How a model is measured: the commands and the code behind them. The method is in
[README.md](README.md), the store's files in [FORMAT.md](FORMAT.md). The tools send the app's own
requests (`docs/development.md`, *Request profiles*), so a result describes what a teacher's run
sends. Every option and its default: `bazel run //tools:<tool> -- <command> --help`. Below,
`store marks` is short for `bazel run //tools:store -- marks`.

## Setup

- **Store:** a clone of the private `teachers-teammate-evals` (here `$EVALS`), passed as `--store`
  to every command; none guesses it. `core/paths.py:store_path()` refuses a store inside this
  repository.
- That repository is a Bazel module that depends on this one (sibling clone,
  `local_path_override`), so the tools also run from there: `bazel run //:report -- …`, `//:store`,
  `//:evaluate`, `//:prepare_gec_testset`, `//:verify_model_catalog`. This is why the
  `ocr_test_dependencies` pip hub is not a dev dependency here.
- **Ollama:** `$OLLAMA_HOST`, else `127.0.0.1`; `--ollama-url` overrides it. From WSL in NAT mode,
  use the Windows host (`ip route show default`, with `OLLAMA_HOST=0.0.0.0` on Windows) or set
  `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig` and run `wsl --shutdown`.
- None of the tools runs in CI.

## Commands

| Tool | Commands | Job |
| --- | --- | --- |
| `verify_model_catalog` | `sizes`, `capabilities`, `fit`, `smoke`, `structured-output`, `recheck` | catalog checks, model sizes, gate 1 |
| `prepare_gec_testset` | — | build a test set from the corpora into `testsets/<id>/` |
| `evaluate` | `run` | gate 2: send a test set through the app's corrector |
| `report` | `table`, `compare`, `gate`, `passes`, `sheet`, `page` | read the store; print or write reports |
| `store` | `convert`, `validate`, `marks`, `accept-marks`, `manifest` | keep the records: marks, accepted alternatives, checks |

The command-line files only parse arguments and print; the work is in `tools/evaluation/`.

## Evaluating a candidate

```bash
# 1. Size on the card at every window
bazel run //tools:verify_model_catalog -- fit --store $EVALS --machine ID \
    --gpu-agent URL --gpu-token-file ~/.config/gpu_agent.token --tag TAG
# 2. Gate 1 (with the smoke check first); keep the answers
bazel run //tools:verify_model_catalog -- structured-output --tag TAG --answers DIR \
    --fit-store $EVALS/model-fit/sizes.jsonl
# 3. Gate 2: three repeats on the test set
bazel run //tools:evaluate -- run --store $EVALS --machine ID --testset gec-2026-12 \
    --model TAG --repeat 3
# 4. Mark a sample of the explanations
bazel run //tools:report -- sheet --store $EVALS --run RUN_NAME > sheet.md
bazel run //tools:store -- marks sheet.md --marker NAME --store $EVALS
# 5. Floors, then the comparison with the current holder
bazel run //tools:report -- gate --store $EVALS --model TAG
bazel run //tools:report -- compare --store $EVALS --along model --baseline HOLDER
```

Then write the gate record in `$EVALS/gates/` and enter the catalog values
(`domain/model_catalog.py`) by hand.

## Gate 1: the structured-output check

**Request.** The one-request review of the evaluation baseline (`tools/evaluation/run/one_request/`):
the B2 system prompt in the text's language, the JSON schema as `format`, `think=False`, the model's
step-① temperature (`--temperature` overrides it) and the proofreading window (`--num-ctx`). A test
pins it to the one-request corrector's request.

**Fixtures** (`checks/gate1_fixtures.py`): plain English, German and French; Markdown; one full
German page; one German page filling the corrector's budget (`structured_check.review_budget`,
pinned to `NativeOllamaCorrector.max_text_chars`). A fixture over budget is reported, not sent; CI
keeps the shipped ones within it.

**Verdict per run**, first failure wins:

| Hard | Soft |
| --- | --- |
| too long, request failed, cut off (`done_reason == "length"`) | invalid, no sentences, unchanged, too few sentences placed, partial coverage of a long fixture, too few explained |

Every fixture runs three times above temperature 0, once at 0. How many soft failures pass, when a
second round runs and the explained share: `CheckRule` (`checks/check_rule.py`).

**Second try.** As in the app, a cut-off answer is asked once more; `RecordingClient` sends
`seed + k` for the *k*-th identical request. Cut off twice is `TRUNCATED` (hard), cut off once then
answered is `CUT_OFF_ONCE` (soft).

**Output.** Per run: placed, explained, orphans, covered, tokens, margin (what the window had left),
`done_reason`, seconds. The summary warns when the smallest margin on the budget fixture is below a
tenth of the window. The header names what the verdict is valid for: tool commit, Ollama version,
window, temperature, eval version and fingerprint, `ANSWER_FORMAT_VERSION`, and per model its digest,
quantisation and size. It notes when the app would run the model at a larger window than checked.

**Re-judging.** `--answers DIR` saves every answer (kept local). `recheck FILE` judges them again
with the current rule, without a server; it refuses another fingerprint or a changed text.

**A verdict belongs to the exact tag.** The app sends only the temperature and `num_ctx`; `top_k`,
`top_p`, penalties and the chat template come from the tag's packaging, which the check prints.
Check a Hugging Face build as its own tag.

### The smoke check

Gate 1's first stage, for one *setup* (tag, request profile, temperature, window, exchange, steps).
A pass means "no sign of a systematic fault", not "passes gate 1". `--no-smoke` skips it;
`evaluate run` smokes each setup once before its first session.

- **Size limit first.** Above `--fit-limit` (`off` skips; otherwise `--fit-store` is required) the
  setup fails without a request. A model without a size record at that window is measured first,
  as `fit` does.
- **Fixtures:** gate 1's, cheap to expensive, without the budget fixture; one run each.
- **Fails** when the same fixture fails twice in a row, or at once when a second try cannot help: a
  refused request, or a collapse to one sentence on the full page.
- **Timeouts:** `--timeout` bounds the wait for the first token (the first request gets longer, it
  loads the model). `--deadline` bounds the whole answer (`0`: none), since a model on Ollama's
  llama.cpp runner can run past the window and the app sends no `num_predict`; past it the stream
  is closed and the run counts as cut off.
- **Per exchange:** `one-request-review` gets gate 1's own requests; `text-first` step ①'s request
  on the plain fixtures; `one-request-text` its request on the short fixtures.
- **Continuing:** the smoke runs are gate 1 runs. `structured-output --continue FILE` sends only the
  missing ones. A failed smoke check is continued only with `--force`, never after a hard failure.

## Model sizes and the memory agent

**Record**, one per model, machine, device and window: Ollama's `/api/ps` figures (`size`,
`size_vram`, loaded window) and the memory agent's card readings (`idle`, `peak`, `end`, total), all
raw bytes. Nothing derived is stored, so a new rule costs no measuring.

**Need** = `max(Ollama's size, the card's increase)`. A step that spills (`size_vram < size`, or the
card's peak near its total) is flagged and its need is a lower bound. `--no-agent` records only
Ollama's figure, flagged *no card reading*. Trust the card reading: `/api/ps` can be far off.

**`fit`** loads the model at every step of `CONTEXT_STEPS` with the app's proofreading options,
emptying the server before each step; it also records the generation speed.

- `--machine` (an id from `machines/`) is required. A stored step is skipped unless `--again`.
- A tag stops when Ollama loads a smaller window than asked or a request fails.
- `--cpu-only` measures the processor at the base window; size lookups skip those records.
- Exit: 0 all measured or stored, 1 a failure, 2 server or agent unreachable.

**`fit --report`** (no server): need per window, Ollama's vs the card's figure, growth per 8k,
difference to the catalog's `memory_gb` (above `MEMORY_TOLERANCE`), and the largest window that fits
each `--fit-limit`. `--compare A B` sets two machines side by side; `--catalog` prints the values to
enter.

**Limit.** A tier's limit is `card_budget_gb(tier) / FULL_SPEED_SHARE`; `runs_at_full_speed()` in
`domain/model_catalog.py` decides, for the app and the tools alike. A number (`--fit-limit 14.5`)
is GB.

### Memory agent

`tools/evaluation/run/gpu_agent.py`: a stdlib-only HTTP server on the machine with the card that
reports what the card really holds (AMD via sysfs, NVIDIA via `nvidia-smi`). It only reads; it never
touches Ollama.

```bash
python3 tools/evaluation/run/gpu_agent.py --token-file ~/.config/gpu_agent.token --port 11436
```

- Run it as a user service; open the port in the firewall for the machine that runs the tools.
- Every request needs the token (`X-Token`); the tools read the same file (`--gpu-token-file`).
- On Windows run it in the Windows session: WSL's `nvidia-smi` reports 0 MiB used.
- `GET /info`: platform, cards, use, main memory. `POST /window` opens a sampling window;
  `POST /window/<n>/close` returns start, peak, mean, minimum, end.

`AgentClient` (`run/agent_client.py`) is the client; `settle()` waits until the card's use is stable
before a measurement. Used by `fit` and the smoke check's size limit.

## Gate 2: `evaluate run`

Builds the app's own corrector (the exchange inside `ChunkingCorrector`, or the one-request corrector)
and sends what the app sends, including the long-text path; the answer records the `pieces`. Answers
are appended, so an interrupted run resumes.

```bash
bazel run //tools:prepare_gec_testset -- --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json --store $EVALS
bazel run //tools:evaluate -- run --store $EVALS --machine ID --testset gec-2026-12 --model TAG
```

**Exchanges and steps.** `--exchange text-first` (default) is the app's exchange: ① corrected text,
② explanations, ③ unclear check. `one-request-review` asks for corrections with explanations in one
JSON answer; `one-request-text` for the corrected text only. `--steps correct` sends step ① alone
(the exchange's own code, with steps ② and ③ refused by `OnlyStepsPort`): use it for every question
about the corrections, and `text-first` for explanations, unclear flags and page times.

**Variants.** Each writes its own file and is part of the compare key.

- `--option NAME=VALUE`: sampling values for step ① (`SAMPLING_OPTIONS`). Without it the tag decides,
  as in the app.
- `--prompt keep|find|shot`: a step-① prompt variant (`CORRECT_VARIANTS` in
  `domain/correction_prompts.py`); `--prompt standard` sends `CORRECT_SYSTEM` to a model with an
  adopted variant. `text-first` only. Adopted variants are listed in `STEP_ONE_PROMPTS`
  (`model_catalog.py`). A variant's text never changes once measured; another text is another id.

**Measuring.** `--seed` (the app sends none), `--repeat N` (repeat *k* uses seed + k − 1),
`--isolate` (unload all models first and after; only on your own server), `--gpu-sampler`,
`--gpu-ssh` or `--gpu-agent` (graphics memory once a second). Per run: unload and check, start the
sampler, one unscored warm-up, then per text record what is loaded. A text during which another model
was loaded is *shared* and left out of speed and memory figures (`run/measure.py`).

**Checks.** `--dry-run` prints what the run would do (sessions, complete files, refusals, request count) and sends
only the server probe. `--log FILE` writes one JSON line per event. A run that skipped setups lists
them at the end and still exits 0.

**Protocol version.** `run` fingerprints the request side (every request the probe text produces,
the text budget, the repair message, the answer format version) and exits 2 when
`core/protocol.json` does not accept it, committed or not. Then add an entry with the new
fingerprint, the date and the reason, and set `current`. A resume is refused when the sessions
differ in protocol version, test set, machine, model digest, request settings or a language's
`instructions` hash. Scoring changes raise `scorer_version` instead (FORMAT.md, *Versions*).

## Reports

```bash
bazel run //tools:report -- table --store $EVALS [--performance] [--by-error category]
bazel run //tools:report -- gate --store $EVALS --model TAG
bazel run //tools:report -- compare --store $EVALS --testset gec-2026-12 --along model --baseline TAG
bazel run //tools:report -- compare --store $EVALS --model TAG --exchange text-first \
    --steps correct --along temperature --baseline 0.05 --where step_one_prompt=standard
bazel run //tools:report -- passes --store $EVALS --testset gec-2026-12
bazel run //tools:report -- page --store $EVALS --report-out ~/gec-testset/reports/NAME
```

**Selection.** Commands name a model, not a file; `--model`, `--temperature`, `--exchange`,
`--steps`, `--digest`, `--compact-json`, `--machine`, `--testset` narrow the runs. Machine and test
set default to the newest run's, and the command says so. Every axis in which the runs differ must be
named in `--along` or fixed with `--where` (`--where sampling.presence_penalty=` keeps runs without
that option); anything ambiguous is refused.

- **`gate`**: one line per floor (`✓`, `✗`, `·` diagnostic), then the outcome. Reads the step-①
  temperature unless given, and the marks files. Exit 0 `PASS` / `PASS_PENDING`, 1 `FAIL` /
  `INCOMPLETE`, 2 refused.
- **`compare`**: `--along` the varying axes (`core/axis_table.py`), `--baseline` the reference value
  (default: first row). `--corrections` counts `text-first` and `text-first/correct` as one. Prints
  per row the deciding F0.5, recall, precision, left alone, coverage, false alarms, spread, lost
  text, speed, and the difference to the baseline with its 95 % interval; then the best row and its
  ties. A tie goes to the cheaper configuration. Exit 0, or 2 refused.
- **`passes`**: what a second and third pass would add if merged by vote: single pass vs edits made
  by two of three and by all repeats, the precision per vote count, and the time cost.
- **`page`**: one static page (opens from `file://`) with scorecard, heat map, every variant's
  sentences beside the reference, and error types; Python computes every number. The output must lie
  outside a git work tree, since it holds corpus text.

**Reading the figures.** Compare speed only on the same machine. Seconds per 100 words is the
teacher's wait, not tokens per second. The samplers read `amd-smi` and `nvidia-smi` output; a
sampler started over SSH must be killed on the remote machine.

## Marks and accepted alternatives

`report sheet --run RUN_NAME` prints the sample to mark; `store marks SHEET.md --marker NAME` writes
the marks file; `store accept-marks --testset ID --marker NAME` turns that marker's `valid` edits into
the set's accepted alternatives (`report table --no-accepted` scores without them). Rules: FORMAT.md,
*Marks*.

## Checking the store

```bash
bazel run //tools:store -- validate --store $EVALS        # the store, without the corpora
bazel run //tools:store -- manifest --manifest $EVALS/testsets/gec-2026-12/manifest.json \
    --corpora ~/gec-testset/corpora.json                  # the set still matches the corpora
```

Runs are committed in the store as written; nothing is converted on the way in.

## Where the code lives

`tools/evaluation/`, one package per layer; a layer imports only those below it
(`test_evaluation_session.py` checks the run layer):

```
report  -> store -> input -> core, metrics
run     -> store, input, core, metrics
checks  -> run, core, metrics
viewer  -> report, run, store, input, core, metrics
convert -> store, input, core, metrics
```

| Package | Holds |
| --- | --- |
| `core/` | records and formats, axes and keys (`axis_table`, `axes`), machines, modes, labels, languages, performance, protocol table |
| `metrics/` | scores of one text: word diff and edit matching (`quality`), GLEU, text loss |
| `store/` | layout, reader and writer, validation, marks and accepted alternatives |
| `input/` | test sets (`testset`), one module per corpus (`corpora/`) and per test set (`recipes/`) |
| `run/` | run loop, measuring, sessions, fingerprint, the one-request baseline (`one_request/`, not imported by the app), memory agent and client |
| `report/` | scoring (`score`, `SCORER_VERSION`), loading, summaries, groups, statistics, comparison, gates, spot check, selection, tables, the page (`viewer/`) |
| `checks/` | checks that run a model: gate 1, smoke, fit, download sizes |
| `convert/` | the one-off converter from format 1 |

## Extending

**An axis.** One `Axis(...)` in `AXES` (`core/axis_table.py`) and one field on `Protocol`; codec,
file-name tags, labels and `compare --along` follow, and a test checks the two agree. An axis at its
default moves no key, so old runs keep theirs. `compare_role`: `MODEL` (values are models to
compare), `FIXED` (may not vary in a comparison) or `DERIVED` (set by another axis).

**A corpus.** One module in `input/corpora/` (corpus name, `-` as `_`), found automatically by its
`ADAPTER`. It declares `NAME`, `LICENCE` (copied into every manifest), `RESTRICTED` (store no words),
`LANGUAGES`, `HINT`, `SPLITS` (default first) and optionally `DEFAULT_ROOT`, and implements `files()`
and `resolve(item)`. It reads only the files it pins.

**A test set.** One module in `input/recipes/` per test-set id, setting
`RECIPE = Recipe(testset, seed, corpora, select)`. `prepare_gec_testset --testset ID` requires the
id. A recipe never changes once its manifest is committed.

Tests for both: `tests/tools/test_evaluation_input.py`.
