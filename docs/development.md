# Development

## Scope

This guide focuses on contributor workflows and codebase structure.
For runtime tuning and provider/model strategy, see the
{doc}`advanced user guide <advanced_user_guide>`.

## Prerequisites

- Python 3.12
- [Bazel](https://bazel.build/) — used as the build and test harness; all `bazel` commands below assume it is on `PATH`
- [uv](https://docs.astral.sh/uv/) (optional — speeds up venv creation)

### Set up the development environment

```bash
# Create a virtualenv and install all dependencies (including dev and test extras)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,test,llm]"
```

If you have `uv` installed, use `uv venv` and `uv pip install -e ".[dev,test,llm]"` instead.

## Development checks

### Linting

```bash
bazel test //:py_ruff
bazel test //:py_pylint
```

### Type checking

```bash
bazel test //:py_ty
```

### Formatting

```bash
# Project formatters (ruff + buildifier)
bazel run //:format.fix
bazel run //:format.check

# Direct buildifier command
bazel run //:buildifier.fix -- -r .
```

### Tests

```bash
bazel test //tests/...
```

## Documentation build

```bash
bazel build //docs:html
bazel run //docs:html.serve
```

## Dependency lock updates

Run these after adding, removing, or upgrading a dependency in `pyproject.toml` to regenerate the pinned lock files that Bazel uses:

```bash
bazel run //:requirements_dev.update
bazel run //:requirements_test.update
```


## Architecture

The codebase is split into four layers. Each layer may only depend on layers below it:

- **Presentation** (`gui/`, `cli.py`) — translates user interactions into application calls and renders results. Contains no business logic.
- **Application** (`application/service.py`, `application/commands.py`) — coordinates use cases; the only entry point for the GUI and CLI.
- **Domain** (`domain/`) — pure business concepts (stage names, freshness policy); no I/O.
- **Infrastructure** (`infrastructure/`) — concrete implementations: OCR engines, LLM providers, file I/O, DOCX builder, and the pipeline orchestration in `infrastructure/workflow/`.

```{uml} assets/component_diagram.puml
:align: center
:alt: Teacher's Teammate component architecture
:width: 100%
```

### Runtime sequence diagrams

The diagrams below show the three main runtime flows.

#### GUI initialisation

```{uml} assets/sequence_gui_init.puml
:align: center
:alt: GUI initialisation sequence
:width: 100%
```

#### Open input folder

```{uml} assets/sequence_open_folder.puml
:align: center
:alt: Open input folder sequence
:width: 100%
```

#### Run selected documents

```{uml} assets/sequence_run_documents.puml
:align: center
:alt: Run selected documents sequence
:width: 100%
```

### Benchmark app

A second GUI app, **Benchmark** (`teachers-teammate-benchmark`, entry point
`teachers_teammate.gui.benchmark:main_benchmark`), helps judge OCR quality for a
single document. It runs the document through an OCR configuration, stores the
result in an append-only history, and lets the user compare two stored runs
side-by-side (image · text A · text B · diff + similarity). It follows the same
layering:

- **Domain** `domain/benchmark.py` — pure pairwise comparison metrics (similarity
  via stdlib `difflib`, text statistics); no I/O.
- **Infrastructure** `infrastructure/benchmark/` — `BenchmarkRunStore` (the
  append-only run store, see below) and `runner.py` (an OCR-only path that reuses
  `StageBuilder`, `PreprocessService`, and `OCRStageService`).
- **Application** `application/benchmark_service.py` — `BenchmarkApplicationService`
  composes `ProcessingApplicationService` for OCR discovery queries and adds
  `run_and_store` / `list_runs` / `delete_run` / `compare`. The GUI talks only to
  this service.
- **Presentation** `gui/benchmark/` — a thin window reusing shared widgets
  (`_ocr_config_selector.OcrConfigSelector`, `_preview_panel` `ZoomableImageView`
  /`DiffWidget`, `_log_widget.LogWidget`, `_app_bootstrap.create_app`,
  `_qt_stream.QtStream`). The main window's proofreading tab uses
  `_review_views.ReviewDiffView` / `ReviewNotesView` instead (see *Correction review:
  schema, splice, align* below); plain `DiffWidget` is still what the benchmark compare
  view and the privacy preview dialog show.

The OCR-only fields live in the nested `Config.ocr` value object
(`config.py:OcrConfig`), which both the pipeline and the benchmark vary.

```{uml} assets/sequence_benchmark_compare.puml
:align: center
:alt: Benchmark run and compare sequence
:width: 100%
```

## Ollama model catalog

`domain/model_catalog.py` is the single source of truth for which Ollama models the app
knows, recommends, and considers suitable for a computer type (hardware profile):

- `OLLAMA_CATALOG` — every catalogued model with its category (`"ocr"` reading model /
  `"general"` writing model), approximate running memory, download size, and a
  plain-language note shown as a tooltip.
- `RECOMMENDED` — the ordered best-model lists per category and profile (`cpu`, `8gb`,
  `16gb`); the first entry is the "best choice".
- `suitability()` / `models_for()` — the rating used by Settings → Add-ons and the
  model-selection dialogs, computed from the computer's own memory (see *Rating a model against
  the computer's memory* below).
- `choose_profile()` — the decision table mapping detected GPUs / Apple unified memory /
  remote Ollama URLs to a hardware profile.

To add a model: add one `OllamaModelSpec` to `OLLAMA_CATALOG` and (when it should be
recommended) its tag to the matching `RECOMMENDED` entries. Consistency tests in
`tests/unit/test_model_catalog.py` check that every recommended tag exists with the
right category and that every best pick runs at full speed on its profile — new entries must
keep them green.

The static defaults (`DEFAULTS["ocr_model"]` / `correction_model` / `evaluate_model`,
the per-task provider defaults, and the Ollama provider's `default_model`) all come from
`DEFAULT_OCR_MODEL` / `DEFAULT_WRITING_MODEL` in the catalog module. They are independent
of the hardware profile — the per-profile `RECOMMENDED` lists only drive the GUI
recommendations — so a default may not fit a small computer.

A writing model needs `structured_output=True` to be offered for proofreading with
explanations: the review is bound to a JSON schema, and a model that cannot
answer it would fall back to *Corrections only* on every file. `DEFAULT_WRITING_MODEL` and
the first entry of every `RECOMMENDED["general"]` list must carry the flag — a test enforces
it. The flag is not guessed: `tools/verify_model_catalog.py --structured-output` runs the
real review prompt against every `general` model that is reachable through a local Ollama
server and reports the value to enter in the catalog by hand, like the download sizes. A
mismatch against the catalog counts as a failure. A model without the flag (`gpt-oss:20b`) is
offered and used like any other model, but has no badge and is not recommended.

The check sends the app's request — `proofreading_options()` (see *Soft failures and per-file
warnings*) — at the temperature the app sends that model: the value its maker recommends, which is
the card's value for Gemma (1.0), Qwen's non-thinking row (0.7, because the app sends
`think=False`), Ministral's "below 0.1" (0.05), Mistral NeMo's 0.3. A model whose maker states
nothing gets 0.2. The table is `RECOMMENDED_TEMPERATURES` in `teachers_teammate/domain/model_catalog.py`
(teachers set no temperature; the app reads that table), and `--temperature` overrides it for every
model named, to compare one model at two temperatures with `compare`. It covers six fixture texts — plain English, plain German,
plain French, Markdown with formatting marks, one invented full page of dense German prose (~350
words), and one invented German page of 6,500 characters that is sent as the longest prefix of
whole sentences the app's own budget allows. German tokenises longest of the three languages, so
the two long fixtures are the worst case for the size the app sends.

**The size is the app's, not half the window.** The check asks the app's own corrector how many
characters one review request carries (`NativeOllamaCorrector.max_text_chars`, with the prompt
taken out of the window first and the rest shared with an answer seven times the text), so the
gate and the app cannot disagree about what fits. At 8192 tokens that is about 2,790 characters
of German, at 16384 about 5,860. A fixture that is longer than its budget is reported as too
long instead of being sent; CI keeps the shipped fixtures within it.

A run is judged in this order: too long for the window, request failed, cut off (`done_reason` is
`length` — a cut-off answer is cut off whether or not what arrived still validates), answer not
valid JSON for the schema, no sentences, nothing changed, then the thresholds: at least 90 % of
its sentences can be placed in the text, at least 80 % of a long fixture's error sentences are
touched and at least one of those that start in its last third, and at least
`CheckRule.explained_floor` (0.40) of the changes that need an explanation carry one with
non-blank text. The coverage verdict is `ANSWERED ONLY PART OF THE TEXT` (soft): a model that
stops halfway through a page is not a model that cannot fill in the format, but it is not one to
recommend either.

At a temperature above 0 every fixture runs three times, so 18 runs. A model passes with at most
`CheckRule.allowed_failed_runs` (1) soft failure in those 18; with exactly `CheckRule.retry_at`
(2) the check runs 18 more and passes with at most `CheckRule.allowed_after_retry` (4) over both
rounds. At temperature 0 a repeat would be the same answer again, so each fixture runs once and
the second round never happens. Any hard failure — cut off, request failed, too long — fails the
model in either round. A pass also needs the explained share over all of its runs together to
reach `CheckRule.explained_overall` (0.75): one stray miss (the empty answer the Qwen models give
about once in 15 runs) does not take the `explanations ✓` badge away, a pattern does.

Each run prints placed / explained / orphans / covered / prompt and answer tokens / margin /
`done_reason` / seconds. The **margin** is what the window had left after the request and the
answer; it is not a verdict, but the summary names the smallest one on the budget fixture and
warns below a tenth of the window, because that is a model that passes today and may not
tomorrow.

**The record and the re-run.** The command prints its own header: date, command line, tool
commit, Ollama version, window, temperature, eval version and fingerprint, and
`ANSWER_FORMAT_VERSION` — a verdict is only valid for the answer format it was made on. Per model
it adds the digest, the quantisation and the parameter size, and notes when the app would run
that model at a larger window than the one checked. `--answers DIR` saves every answer to a
JSON-lines file (they stay local: the fixtures are invented, but the answers are large), and
`verify_model_catalog recheck FILE` judges those answers again with the current rule, without a
server. It refuses a file whose fingerprint differs from the current one, and a run whose text no
longer hashes to what was sent.

**The smoke check, before the eighteen runs.** A *setup* is one model asked one way: tag, request
profile (compact JSON on or off), temperature, window and answer mode. Every new setup — a
candidate, a quantisation, the compact rule, a temperature refinement — used to cost a full gate-1 run to
find out whether it works at all. `verify_model_catalog smoke` sends gate 1's own requests, cheap
to expensive, one run each, and stops at the first fixture that fails twice: at most ten requests,
a few seconds to a few minutes. It is gate 1's first stage, not a verdict — a pass means "no sign
of a systematic fault", never "passes gate 1".

**The size limit comes before the first request.** A setup whose model does not fit the card cannot
be judged on quality, so the check asks what the model needs at the window it is about to use (see
*Model sizes*): above `--fit-limit` (default `16gb`, `off` skips the check) the setup fails with no
request sent, and a model with no record at that window is measured on the spot, exactly as `fit`
does — unload, settle, one short request, `/api/ps` — so the screen never decides on a guess, and
the smoke check's own first request then finds the model loaded. A server that cannot be measured
is a warning, not a failure: the screen is worth more than the figure. The header of the run and of
the answers file names the limit, so a gate-1 record says under which limit it was screened.

The fixtures are plain DE, Markdown, plain EN, plain FR and the full page DE; the budget fixture
is left out because it shows the same faults as the full page at twice the cost. A fixture fails
the setup only when **the same fixture fails twice in a row** — the app's own "ask once more", so
one unlucky run never rejects a model. At a 5 % soft-failure rate per run a good model is rejected
0.25 % of the time per fixture; a setup that fails 70 % of the time or more is caught at least
49 % per fixture it fails on. Two failures a second try cannot cure fail at once: a request the
server refused, and the collapse to one sentence on the full page (fewer than 20 % of its error
sentences covered and none in the last third — `qwen3.5:9b` with the compact rule), whose line
names the likely cause and the rule to try instead. One `NO_SENTENCES`, one `CUT_OFF_ONCE`, a
fixture that needed its retry, a partial memory split and a window other than the one asked are
warnings, not failures. A request that waits for its first token in vain (`OCRTimeoutError`) is
retried like any failure and counts as a soft failure when it happens twice. The timeout (120 s)
bounds only that wait — a model that has begun to answer may take minutes — and the first request
waits 600 s longer, because it loads the model. **The window does not stop a runaway**: a model on
Ollama's llama.cpp runner shifts its context and keeps writing (`mistral:latest` wrote 81,920 tokens
in 937 s; a 3B Granite held a run for minutes on a fixture that takes 5 s), and the app sends no
`num_predict`. So `--deadline` (240 s, 600 s for the two long fixtures) bounds the whole answer:
past it the request is abandoned through `total_timeout` of `OllamaClient.chat_with_stats` (the
stream is closed, so the server stops generating), the run counts as cut off, and a setup that
runs away twice fails. `--deadline 0` sets no limit. The app itself sets no deadline and no
`num_predict`; capping the answer there would change the request and is a separate decision. Before the
first fixture the check also reports the setup itself: the
system text carries `COMPACT_ANSWER_RULE` exactly when the profile says on, and `/api/ps` shows
the window and whether the model sits on the card.

**The smoke runs are gate 1's runs.** `--answers DIR` saves them like any other answer, so
`structured-output --continue FILE` judges them with the current rule, sends only the runs still
missing (13 of 18 for a setup that passed every fixture first time) and prints one record that
holds all of it. A setup that failed the smoke check is not continued unless `--force` is given,
and never when a second try could not have cured it (a hard failure is a gate-1 FAIL as it stands;
a soft one is "no verdict"). A file that goes on past the failing fixture is an interrupted gate-1
run, not a smoke check, and is continued. A request that timed out is not saved as a run, so gate 1
asks it again under its own timeout. `--no-smoke` skips the stage, and
`compare_writing_models run` smokes each model once per mode before its first session.

The window is `--num-ctx`, 8192 by default: the window the app uses for proofreading, always. A
model the automatic rule would lift above the checked window on a tier's card is noted in the
header; with proofreading capped at the base window that note never fires today, and it is the
reminder to check the larger window when the cap is raised.

**How the explanation rule was set.** The short fixtures have only 3 to 7 changes, so one change
moves a run's ratio by about 0.2, and a single threshold "just below the default" (the obvious
first choice, about 0.70) would have failed every candidate. The first full check (2026-11-07, seven models,
15 runs each; the record has since been deleted) gave the
numbers: the then-default `qwen3.5:9b` reaches 0.86 over all runs, the weakest accepted candidate
(`ministral-3:8b`) 0.82 with a weakest run of 2 of 5 (0.40), while the unsloth build of Qwen3.5
had runs with 0 of 5 (it wrote no notes at all). So the floor sits at 0.40 and catches "wrote no
explanations"; the overall share, 0.75, does not depend on one unlucky run. Revisit both if a
model change moves them.

A verdict belongs to the **exact tag** it was measured on. The app sends only the temperature and
`num_ctx`; `top_k`, `top_p`, penalties and the chat template come from how the tag was packaged
(the official `qwen3.5:9b` ships `top_k 20`, `top_p 0.95`, `presence_penalty 1.5`; the same model
imported from a `hf.co/unsloth/…` GGUF ships none of them). The check prints the settings a tag
brings along, so a result can be traced. Check a Hugging Face build as its own tag; do not
transfer a verdict between packagings.

The check talks to `$OLLAMA_HOST` (else `127.0.0.1`); `--ollama-url` overrides it. From WSL in
its default NAT mode `127.0.0.1` is WSL itself, so point it at the Windows host
(`ip route show default`), or set `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig` and run
`wsl --shutdown` so `127.0.0.1` reaches Windows. Ollama on Windows must listen on the network
(`OLLAMA_HOST=0.0.0.0`) for the first option.

To check a candidate **before** adding it to the catalog, pass its tag; `--tag` implies
`--structured-output`. A list may mix both: a tag the catalog knows is checked against its
`structured_output` flag and counted in the tally, the others are candidates that only get a pass
or fail:

```bash
bazel run //tools:verify_model_catalog -- --tag ministral-3:8b --tag gemma4:e4b
bazel run //tools:verify_model_catalog -- --tag qwen3.5:9b --temperature 0.2
bazel run //tools:verify_model_catalog -- structured-output --answers ~/gec-testset/gate1 \
    --tag qwen3:4b --tag gemma4:e2b
bazel run //tools:verify_model_catalog -- recheck ~/gec-testset/gate1/*.jsonl
```

A writing model that can also read scanned pages lists `also_uses=frozenset({"ocr"})`;
`catalog_uses()`, `suitability()` and `models_for()` then treat it as a reading model as
well (Settings → Add-ons still lists it once, under its primary category). Entries
whose tag starts with `hf.co/` are pulled from Hugging Face, so
`tools/verify_model_catalog.py` checks them against the Hugging Face file list instead
of the Ollama registry.

### Model sizes: what a model needs on the card

`OllamaModelSpec.memory_gb` and `context_gb_per_8k` say how much a model needs at 8,192 tokens and
how much it grows per further 8,192. They were estimates, or figures from an 8 GB NVIDIA card.
`evaluations/model-fit/sizes.jsonl` holds the measurements that replace them: one JSON object per
line, append-only, numbers only. `evaluations/model-fit/README.md` describes the fields and the
machines; the module is `tools/model_eval/model_fit.py`.

**One record per model, server and window.** It carries what Ollama reports from `/api/ps`
(`size`, `size_vram`, the window it really loaded) and what the card really held, from the memory
agent's window around the load (`idle`, `peak`, `end`, the card's total). Every figure is a raw
byte count; the reports divide by 10⁹, the unit of the catalog's sizes. Nothing derived is stored:
the card's increase, the need, the growth and the tier windows are computed when the file is read,
so a change of the rule costs no new measurement.

**The need is `max(Ollama's size, the card's increase)`.** Ollama's figure leaves out the driver's
and the runtime's share; the card's increase cannot exceed the physical card. Taking the larger is
cautious in both directions. A step that spills — Ollama's `size_vram` below `size`, or the card's
peak within 2 % of its total — is kept and flagged: it is a real figure, but its need is a lower
bound, so it does not feed the growth. A record made with `--no-agent` has Ollama's figure as its
need and is flagged `no card reading`.

**Measuring.** `verify_model_catalog fit` loads the model at every step of `CONTEXT_STEPS` with the
app's own proofreading options, and empties the server before each step so the model is loaded at
exactly the window asked for:

```bash
bazel run //tools:verify_model_catalog -- fit --server rx9070xt \
    --ollama-url http://gpu-server.example:11434 --gpu-agent http://gpu-server.example:11436 \
    --gpu-token-file ~/.config/gpu_agent.token --tag gemma4:e4b --tag qwen3:4b
```

`--server` is a short label for the machine; what it stands for is written once in the folder's
README, because Ollama's API cannot report it. A step already stored for the same build and version
is skipped (`--again` measures it again). The ladder stops for a tag when Ollama loaded a smaller
window than asked for (the model's own maximum) or when the request fails; a failing tag does not
stop the next one. Exit codes: 0 every step measured or already stored, 1 a failure, 2 the server
or the agent cannot be reached.

**Reading it back, without a server.** `fit --report` prints, per model and server, the need at
each window, Ollama's figure next to the card's increase, the growth per 8k (short and long span,
flagged when not linear), the difference to the catalog's `memory_gb` (flagged above
`MEMORY_TOLERANCE`, 10 %), and the largest window that still fits a card of a given size
(`--fit-limit`, repeatable; default `8gb` and `16gb`). `--compare A B` puts two machines side by
side and marks each figure that disagrees (need within 10 %, growth within 20 % or 0.1 GB).
`--catalog` prints the two values to enter by hand, taking the larger of two servers that disagree.

**The limit is the app's own rule.** A tier's limit is `card_budget_gb(tier) / FULL_SPEED_SHARE`:
15.11 GB for `16gb`, 7.56 GB for `8gb`. `runs_at_full_speed()` in `domain/model_catalog.py` is the
one place that decides whether a need fits a card at full speed, and `context_budget` uses it, so
the screen and the app cannot disagree about a tier. A number (`--fit-limit 14.5`) is that many GB;
`off` skips the check.

**How the catalog's figures come from it.** `memory_gb` is the need at 8,192 tokens and
`context_gb_per_8k` the growth, both from `fit --report --catalog`, entered by hand in one commit
that names the store lines' dates. Until a model has a measured growth it stays at 8192. The
figures are measured on one backend and cross-checked on another; where the two disagree, the
larger one is entered, because the model has to fit the machine that needs more.

### Memory agent: what the card really holds

Ollama's `/api/ps` reports what it *thinks* it put on the card. The memory agent
(`tools/model_eval/gpu_agent.py`) reports what the card really holds, sampled from the driver, so a
measurement can say how far Ollama's bookkeeping is from the hardware. It is a small stdlib-only
HTTP server that travels alone: it is copied to the machine with the card and runs there, because
the card is on that machine and not on the one that runs the tools.

**What it reports.** `GET /info` returns the platform, the vendor and the source of the reading
(`sysfs` for AMD on Linux, `nvidia-smi` for NVIDIA), the cards with their totals, the card's use
right now, and the main memory's total and available. `POST /window` opens a window and returns its
number; `POST /window/<n>/close` returns the window's statistics: the card's use at the start, the
peak, the mean, the minimum and the end, the main memory's lowest available, the number of samples
and the seconds it was open. A window older than an hour is dropped, and at most eight are kept.

**Starting it.** On the server it runs as a user service, so it survives a logout and starts with
the machine:

```bash
python3 tools/model_eval/gpu_agent.py --token-file ~/.config/gpu_agent.token --port 11436
```

On Windows it runs in the Windows session, not in WSL: WSL's `nvidia-smi` sees the card but reports
0 MiB used. The token file holds one secret; the agent refuses every request without it
(`X-Token`), and the tools read the same file (`--gpu-token-file`). The port has to be reachable
from the machine that runs the tools, so a firewall rule for it is part of the setup. The agent
never writes anything, never loads or unloads a model, and never touches the server: it only reads
the driver and the main memory.

`AgentClient` (`tools/model_eval/agent_client.py`) is the other side: `info()`, `open_window()`,
`close_window(window)` and `settle()`, which waits until the card's use has stayed within a
tolerance for several readings in a row (and at least a short while) before a measurement
starts; the driver can give memory back a second after the server reports a model unloaded. `fit` and the smoke check's size limit are its two users.

### Rating a model against the computer's memory

`suitability()` / `rate_model()` rate a catalogued model for a task on a computer, from what
the app detected there (graphics memory, total main memory) or, when the profile was chosen by
hand, from the size the profile stands for.  `memory_gb` of a catalog entry is the total memory
the model needs at the proofreading window of 8192 tokens (card and main memory together),
measured where possible (`docs/development.md`, *Choosing writing models*).

| Quantity | Rule (constants in `domain/model_catalog.py`) |
| --- | --- |
| Card budget | `CARD_USABLE_SHARE` (85 %) of the card's memory; Apple Silicon 66 % of the unified memory (`HardwareDetection.card_gb`); none without a card |
| Reserve | the larger of `SYSTEM_RESERVE_MIN_GB` (8 GB) and `SYSTEM_RESERVE_SHARE` (20 %) of the total main memory; swapping to disk is never counted as memory |
| Works (`WORKS`) | at least `FULL_SPEED_SHARE` (0.9) of the model fits the card; without a card, up to `CPU_COMFORT_GB` (4 GB) |
| Slow (`SLOW`) | the part that does not fit (the *spill*) fits in main memory beyond the reserve; `ModelRating.very_slow` when under `SLOWER_SHARE` (0.7) of the model fits the card, or there is no card |
| Does not fit (`TOO_LARGE`) | the spill does not fit; also whenever the main memory is unknown (a remote server) and the model is not within the card |
| Recommended (`BEST` / `GOOD`) | the tier's list, but never for a model that does not fit the computer's known memory |

The two share thresholds come from speeds measured on an 8 GB card (a share of 0.97 gave 43
tokens per second, 0.73 gave 13, 0.66 gave 6.6); they are ratios, so they scale with the
computer, but they come from one card and one processor.

`ProcessingApplicationService.memory_warning_for_run()` is the run-time counterpart, used by
`check_run_preflight()` (GUI) and the command line before a run: for an Ollama on this computer,
the part of every catalogued model that does not fit the card (all of it on Apple Silicon) has to
fit the main memory that is free *right now*, minus `RUN_MEMORY_MARGIN_GB` (2 GB).  It reads
`gpu_detector.detect_available_memory_gb()` (`psutil`'s `available`, never swap).  It is a
**warning**, not a refusal: free memory changes from run to run, so the GUI offers "Run anyway" and
the command line needs `--ignore-memory-check`.  `check_run_preflight()` therefore runs the memory
check last, after the stage requirements (reading, anonymizing, correcting, evaluating), and reports
a memory warning and a blocking issue separately.  A model that is not catalogued, a remote server
and an unreadable memory give `""`.  It rates the models through
`resolve_hardware_profile()`, so a hand-picked profile counts as it will at run time, and at the
window the run really gives the model (`memory_at_context_gb`).  The wording of all this is in
`gui/_model_wording.py`.

Two measurements are still open here.  (1) **Memory of a model Ollama already has loaded**: whether
the processor-side part of a loaded model is taken out of `available` (anonymous memory) or counts
as reclaimable (memory-mapped weights) has not been measured.  The check therefore behaves as it
would after Ollama unloaded everything, which is the case it has to warn about anyway, since Ollama
unloads idle models to make room.  Measure it with a correction running, comparing
`psutil.virtual_memory().available` with `ollama ps` (`size`, `size_vram`), on Linux/WSL and on
Windows; if the memory is taken out, count the RAM part of other loaded models as free.
(2) **Apple Silicon**: the unified memory may make the spill look larger than it is.  No code change
for now — "Run anyway" takes the sting out of a false warning.

### Who owns what (hardware detection and recommendations)

| Piece | Where | Rule |
|---|---|---|
| Decisions (best model, suitability, hardware profile, `rate_model`) | `domain/model_catalog.py` | Pure functions, no I/O — table-test them. |
| Caches (local hardware probe, model classification) | `application/model_recommendations.py` | One owner. Call `on_ollama_models_changed(url)` after every pull; it evicts the model list *and* the classifications. Only definite classifications are cached. |
| Texts a teacher reads | `gui/_model_wording.py` | Qt-free; widgets never build these sentences themselves. |
| Widgets | `gui/_hardware_profile_selector.py`, `gui/_model_guidance.py`, `gui/settings/_addons_page.py` | Presentation only. They rate models through `rate_ollama_models(..., cached_only=True)` — never contacting Ollama on the GUI thread. |

Two details that are easy to get wrong:

- **The local probe describes this computer, not the Ollama URL.** It runs at most once
  per process (`detect_hardware(refresh=True)` re-runs it); a remote URL never probes
  at all.  It reads the graphics cards and the computer's memory; both are logged at
  `INFO` with the resulting profile, and a failed check is logged at `WARNING` with its
  traceback and shown as such in "My computer" (never as "no suggestion").
- **A chosen profile still goes through the service** (`resolve_hardware_profile`): it
  starts no graphics probe, but carries the memory of a local computer so `SLOW` works
  there too.
- **Ollama model lists are always fetched in a background task.** Classifying a custom
  model needs a server round trip, so the dialogs have no synchronous "cache hit"
  shortcut for Ollama.

### AI services: providers, model lists and install hints

Proofreading and content review share one widget, `AiServiceSection`
(`gui/settings/_ai_service_section.py`); an `AiServiceSpec` only names the settings keys of
the step.  What it relies on:

- **Each provider module declares its package** in `PROVIDER_INFO["package"]`
  (`{"module": "langchain_mistralai", "pip": "langchain-mistralai"}`); Ollama's connector is
  `package_optional` because the app talks to Ollama directly.  The service answers
  `missing_provider_package()` and `list_provider_packages()` (providers sharing a package
  appear once), so the pages, Add-ons and the run preflight need no list of their own.
  A missing package is a `RequirementIssue(provider=...)`; the app then opens Settings →
  Add-ons on the online-services tab with `provider:<name>` in `preselect`.
- **A model list says where it came from** (`domain/model_listing.py:ModelListing`):
  `live`, `builtin` (the service cannot be asked, by design) or `failed` (with the reason:
  no key, key refused, no connection).  Providers that can be asked implement
  `list_models_checked()` (Google asks its REST endpoint like Mistral does, because the SDK
  behind `langchain-google-genai` changes between versions); `list_provider_models_checked()` in `llm_factory.py` covers the
  rest.  Only a live or by-design built-in answer is cached, so a key entered a moment
  later is noticed.
- **Choosing another provider asks that provider.** The change handler shows the built-in
  names and starts a fetch tagged with the provider; an answer for any other provider is
  dropped.  A model name from the previous provider is kept only if the new one offers it.
- **Every provider declares where it processes data** in `PROVIDER_INFO["data_region"]`:
  `"eu"`, `"non_eu"` or `"unknown"`.  A missing key counts as `"unknown"`, which is shown
  like "outside the EU" — a school can only rely on what a vendor actually declares, so
  "not declared" must never read as "EU".  `PROVIDER_INFO["vendor_country"]` is optional
  and only fills the tooltip.  A provider whose region depends on the settings (a custom
  base URL, a regional endpoint such as `api.eu.mistral.ai`) adds a module function
  `data_region(**settings) -> str`; `llm_factory.get_provider_data_region()` prefers it
  over the key and returns `"unknown"` for anything missing, unrecognised or raising.
  `application/data_flow.py` turns the answer into `StageLocation.region`
  (`domain/data_location.py:DataRegion`), which feeds `PrivacyLevel` and the
  "don't ask again" fingerprint.

### Remembered "don't ask again" answers

`gui/_privacy_settings.py:FlowAcknowledgements` is the only code that reads or writes them
(QSettings key `privacy/acknowledged_data_flows`, one line per answer:
`<fingerprint>` or `<fingerprint>\t<readable summary>`).  The question before a run, the
list in Settings → Privacy and "Ask again" all use it.  Records from earlier versions hold the
fingerprint only; `domain/data_location.py:describe_fingerprint()` turns it into words.

The fingerprint is versioned.  Version 2 starts with `v2|` and adds the data region, so an
answer given before the region was shown no longer matches and the user is asked once more
(correct: the region was not part of that answer).  Parts are separated by `|` and their
fields by `:` — the host may itself contain a colon and is always the field before the
last one, which is why the age of a record is a prefix rather than another field.
`describe_fingerprint()` reads both formats.
`PrivacyPage` (`gui/settings/_privacy_page.py`) puts a first tab for these answers
(`SendingDataTab`) in front of the anonymizer rules; its changes are applied by the Settings
window's OK only (`apply()`).

### "What is sent for this file": who builds it and who shows it

`application/sent_text.py` answers the one question the status box leaves open — *which text
does the service read?* — without spaCy, a model or a widget:

- `describe_sent_texts(flow, *, raw_text, correction_text, anonymize, preview_extracted,
  correction_prompt, evaluation_prompt)` walks `DataFlow.outbound` (the steps that leave the
  computer) and returns a frozen `SentText` per step: proofreading gets the extracted text,
  content review the proofread text, each anonymized first when the step says names are
  hidden.  A step whose names are hidden needs `anonymize`; without it the text is reported
  as written, because hiding it is impossible.
- The function keeps an internal `hidden()` memo, so every distinct text is anonymized at
  most once.  The proofreading section, the content-review section and the comparison view
  therefore share one spaCy pass, and the shared cache behind `anonymize`
  (`application/service.py`, see *Shared anonymization cache*) answers the second call.
- `preview_extracted` asks for the anonymized extracted text as well, for the dialog's
  original-vs-anonymized tab; the service passes `config.anonymization_enabled`, so the tab
  appears exactly when names are actually being hidden.
- `ProcessingApplicationService.describe_sent_texts()` supplies `anonymize` (the caching
  preview anonymizer, `None` when removal is off) and builds the prompts with the same code
  the stages use (`_sent_correction_prompt` / `_sent_evaluation_prompt`), so the dialog cannot
  drift from what a run sends.  `SentTexts` carries the sections, `flow.level`, the raw text
  and the anonymized raw text.

`gui/_what_is_sent_dialog.py` only presents that report: one `_SentSection` per step (title
and badge, the names note, the text with placeholders highlighted by
`highlighted_html()`, the instructions in a collapsed block), a *Personal information* tab
built from `DiffWidget`, and — for a local run — `Everything stays on this computer.`  The
main window fetches the report in a `_SentTextsThread` (a small "Working out what is sent…"
dialog is shown meanwhile) and shows it modally.

### Cell tooltips in the results table

The results table shows where a stage's text went without adding a column:
`data_flow_changed` on the config panel (emitted by `refresh_data_flow()`, which caches the
`DataFlow`) and the main window's `_on_data_flow_changed` push
`{stage.stage: stage_tooltip(stage)}` into `ResultsTable.set_stage_tooltips()`.
`stage_tooltip()` (`gui/_data_flow_summary.py`) is the one-line wording, e.g.
*Sent to Mistral (EU), names hidden*.

A cell can already carry its own message (an unclear count, a warning, an error).  That text
lives in `_ROLE_NOTE` and is written through `_set_note()`; `_compose_tooltip()` joins the
stage line and the cell's note with a blank line, so neither replaces the other.
`_refresh_cell_tooltips()` recomposes every proofreading and content-review cell after the
tooltips change and at the end of `set_queue()`.

### Background tasks in the GUI

New background work uses `gui/_background.py` instead of a `QThread` subclass:

```python
self._task = LatestTask(owner=self)            # "latest wins" per widget
self._task.submit(lambda: service.slow_call(), self._on_done)   # on_done(result, error)
```

Tasks run on a shared `QThreadPool`; results arrive on the GUI thread through a queued
signal, results for a destroyed `owner` are dropped, and superseded results of a
`LatestTask` are ignored. There is no thread object to destroy, so closing a dialog while
a task runs cannot abort the process. The main window waits a bounded moment for running
tasks when it closes (`wait_for_background_tasks`). The older `QThread` subclasses remain
until someone touches them; migrate them one at a time. In GUI tests, patch
`LatestTask.submit` to keep a smoke test offline (see `neutralize_gui_threads`).

Catalog values can drift as Ollama tags change. The developer tool
`tools/verify_model_catalog.py` (needs network, not run in CI) compares the catalog
against the real registry manifests:

```bash
bazel run //tools:verify_model_catalog            # check tags exist and sizes match
bazel run //tools:verify_model_catalog -- --ollama  # also inspect capabilities via a local server
bazel run //tools:verify_model_catalog -- --structured-output  # run the review check (see above)
bazel run //tools:verify_model_catalog -- --tag ministral-3:8b  # check a candidate before adding it
bazel run //tools:verify_model_catalog -- --structured-output --temperature 0.2  # compare a temperature
bazel run //tools:verify_model_catalog -- --structured-output --answers DIR  # keep the answers
bazel run //tools:verify_model_catalog -- smoke --answers DIR --tag qwen3:4b  # screen a setup first
bazel run //tools:verify_model_catalog -- structured-output --continue DIR/*.jsonl  # finish it
bazel run //tools:verify_model_catalog -- recheck DIR/*.jsonl  # judge them again, no server
```

### Choosing writing models: the quality comparison

A writing model must pass two gates before it is recommended.  **Gate 1** is the
structured-output check above (`verify_model_catalog.py`): can it fill in the review format
reliably?  Its first stage is the smoke check (see above): a setup that fails it is not sent the
eighteen runs, and one that passes it is continued from its own answers.  **Gate 2**
(`compare_writing_models.py`) asks how good its corrections are.  Both tools send what the app
sends, at the temperature the model's vendor recommends (see above; gate 2 goes through
`NativeOllamaCorrector` itself, inside the app's `ChunkingCorrector`, with the app's own tokeniser
and word diff), so a result describes the real request — including the app's long-text path: a
text longer than the window's budget is proofread in pieces, one request each, and the row records
how many (`outcome.pieces`).

**The order is measure, screen, gate 1, gate 2.**  A model that does not fit the card cannot be
judged on quality, so its size comes first: `fit` measures it (see *Model sizes*), and the smoke
check applies the limit before its first request — a model above `--fit-limit` (default `16gb`)
fails the setup without a request, and one with no record is measured on the spot.  The header of
the run and of the answers file names the limit, so a gate-1 record says under which limit it was
screened.  Only then do the eighteen runs and gate 2 follow.

```bash
# 1. Build the test set from the public corpora (kept outside the repository)
bazel run //tools:prepare_gec_testset -- --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json --out ~/gec-testset/set-2026-12
# 2. Send every text to the models (results are appended; an interrupted run resumes)
bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set-2026-12 \
    --out ~/gec-testset/results --model gemma4:e4b --temperature 0.2
#    ... and ask for the corrected text only (no JSON, no explanations) on the same texts
bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set-2026-12 \
    --out ~/gec-testset/results --model gemma4:e4b --temperature 0.2 --mode text
# 3. Print the table / which kinds of error were found / the explanations a teacher marks
bazel run //tools:proofreading_store -- report --cache ~/gec-testset/results
bazel run //tools:proofreading_store -- report --cache ~/gec-testset/results \
    --testset ~/gec-testset/set-2026-12 --by-error category
bazel run //tools:proofreading_store -- sheet --run RUN_ID --cache ~/gec-testset/results \
    > spotcheck.md
```

**Test set.** Per language eleven whole texts with reference corrections, in three bands of
whitespace words: `short` 80 to 150, `page` 200 to 400, `pages` 600 to 1,200 — four short, five page
and two pages.  German: Falko-MERLIN GEC corpus (test split; Falko CC BY 3.0, MERLIN CC BY-SA 4.0),
MERLIN letters and Falko essays whole.  English: W&I+LOCNESS v2.1 (BEA-2019 dev split, levels A, B,
C; non-commercial use only), whole essays.  Every `pages` text is longer than the app's review
budget at 8192 (about 2,800 characters) and is proofread in pieces, while at
least one `page` text still fits one request, so both paths are measured on real texts.  French: **no public learner
corpus with corrections exists**, so
`tools/gec_fr_invented.jsonl` holds eight invented texts and ten correct sentences written by an AI
model, not a teacher: French results are indicative only.  German and English no longer carry a
glued group of correct sentences; the sentences the reference leaves alone sit inside the texts
(`unchanged_sentences` in the manifest) and are scored as *left alone*.  The
corpora are real learner text and licensed for research, so neither they nor the built set belong
in the repository.

**Error labels.**  Both corpora come with an M2 file that gives every correction a label
(`R:DET:FORM`, `M:PUNCT`…).  `prepare_gec_testset.py` finds the annotated tokens again in the text
(`tools/model_eval/error_labels.py`) and stores, for every change the reference makes, the label
*code* next to it (`TestRecord.labels`; the codes only, no corpus text).  The labels let the report
ask which kinds of error a model finds.  Matching is by position, so a label is as reliable as that
match.  In the build of `gec-2026-12` (2026-11-10) all 631 German and 350 English reference
changes got a label.  On `gec-2026-11` (2026-11-09) all 118 German and 125 English changes did;
98 % (German) and 99 % (English) of the distinct positions lie directly on an annotated edit, the
rest within one word of it, and 24 labels drawn at random fitted their words on a read-through.
A test set built before the labels existed still works; the by-error report then says so.  The
French texts are invented and have no labels.  Limits to keep in mind: the reference is one
annotator's version; a few "correct" sentences contain errors the annotators missed; British
spelling in the English references makes American spelling count as an error; eleven texts per
language show only large differences.

**Metrics** (per language and band, summed over the texts).  Every number is a
count, not a share: a language's figure is pooled over its texts, so a share computed per text and
then averaged would not be the same number.  `tools/model_eval/item_scores.py` computes them for one
text, at run time and again when the report re-scores a stored row, so the two cannot drift apart.

* *Precision / recall / F0.5*: a change counts when the model's word diff against the source has a
  hunk covering the same words with the same replacement as the reference's (exact match, so
  absolute values are low for every model and only the comparison counts; F0.5 weighs precision
  twice as much as recall because a wrong change costs a teacher more than a missed one).
* *GLEU*, printed next to the GLEU of the unchanged source (`0.62 (src 0.55)`): the corpus score of
  the GEC shared tasks, computed from counts that add up over texts (`tools/model_eval/gleu.py`,
  the formula of Napoles' `compute_gleu`, one text being one line of the official script).  A model
  below its source made the text worse.  It is a guard, not a decision: it says "not lower".
* *Left alone*: of the sentences the reference leaves alone (`unchanged_sentences` in the manifest),
  those the model's diff leaves alone too, by the same rule on both sides
  (`quality.untouched_spans`).  The French `correct` item adds its `unchanged` and
  `sentences_total`, so French keeps the column it had.
* *Coverage*: of the sentences some reference change touches, those some model change touches too.
  It is "looked at", not "fixed": a different replacement still covers the sentence.  Read it
  together with left alone — a model that returns half a page leaves much alone and covers little,
  and either number alone is easy to game.
* *Recall 1st / 2nd half*: the exact matches and misses of the reference's changes in each half of
  the text, a change belonging to the half its start lies in.  It shows a model that stops early or
  drifts towards the end, which one recall figure hides.  The halves are of the text, not of the
  pieces a long text was sent in, so text mode, review mode and every budget compare alike.
* *Added marks*: texts whose proofread text contains Markdown marks (`**`, `__`, `~~`) the source did
  not have; **these marks are ignored when scoring** — the scores are computed on the text without
  them, the same text the edits, the notes and the export use, and `report --count-marks` re-scores
  the raw answer locally — the column only shows how often they occur.
* *Failed / fallback*: reviews that failed or fell back to corrections only, and how many of the
  fallbacks the window cut off (`done_reason: "length"`).  A review-mode fallback keeps its
  corrections in F0.5 and GLEU, because the teacher gets them, but its changes count as
  unexplained, so falling back cannot improve the table.
* *Explained*: changes that need an explanation and carry one.

A language where nothing was scored at all (no reference edit and no model edit) has no F0.5: the
table prints "—" and the mean leaves it out, instead of counting it as a zero.

**Bands and what decides.**  The report prints one row per configuration and language over all its
texts, and a second table that splits each language into the bands the recipe drew the texts for
(`short`, `page`, `pages`).  A band row is a diagnostic, not a decision: four, five and two texts per
language, so one text moves it by a lot, and the `pages` band is marked as such.  What decides is
the mean over German and English of F0.5 and of GLEU (`report.DECIDING_LANGUAGES`); French is
reported only, because it has no bands and its texts are invented.  The rule that uses these numbers
is gate 2's, and it reads the `page` band, not `pages`.

**Gate 2: the floors, the comparison and the explanations.**  Gate 2 asks two questions, and they
are two tools (`tools/model_eval/gate_two.py`):

* **The floors** (`GateTwoRule.judge`) — does this model, on its own numbers, meet the bar for being
  offered at all?  Pass or fail per model, no other model involved, no baseline needed.  Every floor
  is a hard stop, and the values are data, so a calibration changes a value and not a branch:

  | Floor | Review | Text | How it is counted | Starting value |
  | --- | --- | --- | --- | --- |
  | Failed texts | ✓ | ✓ | `failed` per language over all runs | 0 |
  | Cut-offs that cost the explanations | ✓ | ✓ | `cut_off` per language over all runs: a fallback whose requests include a `length`, so both tries were cut | 0 |
  | Other fallbacks | ✓ | — | `fallback − cut_off` per language over all runs | ≤ 1 per language |
  | Added marks | ✓ | ✓ | `added_marks` per language over all runs | 0 |
  | Coverage | ✓ | ✓ | Σ`cover` / Σ`cover_of` per language, pooled over the repeats | ≥ 0.90 |
  | Explained share | ✓ | — | Σ`explained` / Σ`needed` per language, pooled | ≥ 0.85 |
  | GLEU above the source | ✓ | ✓ | per language, in every run | strict |
  | Left alone | ✓ | ✓ | Σ`alone` / Σ`alone_of` per language, pooled | ≥ 0.50 |

  Events count over all runs, rates are pooled over the repeats (a rate on one repeat is noisy), and
  the GLEU guard holds in every run.  *Left alone* is the share of the sentences the reference leaves
  unchanged that the model leaves untouched too: a model that rewrites correct sentences costs the
  teacher a check per sentence.  It pairs with coverage — a model that changes nothing leaves
  everything alone and fails coverage, one that rewrites everything covers everything and fails this
  floor.

  **Measuring and rating are two steps.**  `GateTwoRule.measure(group)` returns one check per floor
  *and language*, for every language the runs hold, and pools nothing across languages; French is
  measured like the others.  `GateTwoRule.rate(measurement, marks)` decides which of them enter the
  outcome: `GateTwoRule.deciding` is data (German and English), so a calibration on the full
  screen changes a value and not a branch, and a check of another language keeps its ✓/✗ with "(not
  deciding)".  `judge` is `rate(measure(group), marks)`, so the command line and its exit codes do
  not change, and the record is grouped by language (`## de`, `## en`, `## fr`, then the
  diagnostics), so each language reads as its own evaluation.  A group with no text in one of the
  deciding languages is `INCOMPLETE`, not a pass.  Printed with the floors, and never
  decisive: recall by half, the `pages` band, the cut-offs the second try absorbed, seconds per 100
  words.
* **The comparison** (`compare(a, b)`) — is model *a* better than model *b* beyond the noise?  It is
  used once, at the end of the screen, to fill each tier's places, and it is not a gate.  `BETTER`
  needs all four: the range of the deciding mean F0.5 per repeat lies entirely above the other's
  (with three repeats each that is a proper test at 5 %), a paired bootstrap over the texts rejects a
  zero difference at 5 % (2,000 draws, `random.Random(20261111)`, so every machine gets the same
  answer), and GLEU and the `page` band are not worse.  The two groups may differ in model, digest,
  request profile and temperature — each model starts at its vendor's temperature, and a refinement
  compares one model at two — and in nothing else; both temperatures are printed.  It is split the
  same way: `measure_pair(a, b)` builds the per-language ranges and bootstraps (French's too, a real
  figure), `rate_pair` combines them with the deciding mean, and the per-language figures are
  printed above the verdict and never decide.

```bash
# the floors and the explanations of one model
bazel run //tools:proofreading_store -- gate --model ministral-3:14b \
    [--mode review] [--temperature X] [--machine rx9070xt-server] [--testset gec-2026-12] \
    --out evaluations/proofreading
# one model against another
bazel run //tools:proofreading_store -- compare --model ministral-3:14b --against gemma4:e4b
```

A command names a model, not a file: the runs are found below `--out`, and the parts of the key the
command does not name come from the newest run of the model — the newest one that matches the mode,
the temperature (each model's own starting temperature unless `--temperature` is given) and the
eval version, so an older run of another mode cannot decide which test set
is read.  A comparison takes the test set and the machine from `--model`, never from `--against`, so
the two groups are always the same evaluation.  What is left ambiguous is refused rather than
guessed — two digests of one tag, or two request profiles, ask for `--digest` or `--compact-json`.  `gate` reads `marks/<run-id>.json` of repeat 1 when it is there, and prints a note
for a catalog model in review mode whose `structured_output` flag is not set (the gate-1 records are
prose, so the flag is set by hand).  One line per check (`✓`, `✗`, `·` for a diagnostic), then the
outcome.  Exit codes: `gate` 0 for `PASS` / `PASS_PENDING`, 1 for `FAIL`, 2 for `INCOMPLETE`;
`compare` 0 for `BETTER`, 1 for `TIE` / `WORSE`, 2 for `NOT_COMPARABLE` or an incomplete group.  The
output is the record: `evaluations/writing-models-2026-11/gate2-<date>.md`, numbers only.

**The explanation check.**  Before a model takes a star place, a teacher marks a sample of its
explanations.  The sample is defined by the *committed* run, not by the cache: from the run of repeat
1, per deciding language, the notes linked to an edit with a non-blank explanation, from `page`-band
texts only, at most two per text, drawn with `random.Random(f"{run_id}/{language}")` — ten per
language, fewer when a text has fewer such notes, and the threshold then scales (80 %, rounded up).
`proofreading_store sheet --run RUN_ID --cache DIR` prints the sheet with the source sentences
from the local cache and refuses a row whose output or explanation differs from the committed run;
`proofreading_store marks SHEET.md --marker NAME` writes `marks/<run-id>.json` (no text, hashes
only; `--marker` is required, because the name goes into the repository), and `validate` checks it
against its run — `gate` reads a marks file only when that check passes, so a mark of another sample
or of an explanation that changed since counts as unmarked.  A mark is `correct`, `wrong` or `vague`; only `correct` counts.
No marks is `PASS_PENDING`, which is enough for an alternative's place, not for a star's.

**The pick** happens once per screen, by hand, and is written down with the `compare` outputs it
rests on: per tier, the eligible models (gate 1 for review mode, the floors, and the tier's memory)
are ranked by the deciding mean F0.5; a current star or alternative that is eligible keeps its place
unless a challenger is `BETTER`; a place whose holder is not eligible goes to the highest-ranked
eligible model, a tie going to the smaller and then the faster model.  At most two models per tier.
The floors are to be calibrated after the runs of the full screen: a floor that a current star fails, or that every
model fails, is reconsidered before it is used, and its value, reason and new threshold are recorded.

**Deferred: splitting equal-length edits.**  Two reference edits of equal length in one block are not split token by
token.  Measured on the smoke run of 2026-11-10, the split would turn 631 German reference
edits into 634 and 350 English ones into 351, and move F0.5 by at most 0.006.  It is not worth
rebuilding the committed manifest, re-pinning the regression tests and relabelling; it moves to the
ERRANT work item, which cuts edits properly on both sides anyway.

Two further views need the labels (`report --testset … --by-error category|type`):

* *Found exactly (found in place) / errors* per kind of error: of that many reference errors, the
  model made exactly the reference change for the first number and changed at least the same
  words for the second.  The gap is "noticed it, fixed it differently", which the F-score counts as
  a miss; a large gap means the F-score underrates the model (or the single reference is narrow).
  Kinds are the app's six categories (`--by-error category`; `other` is every label with no clear
  counterpart, and `unlabelled` the changes no annotated edit was near) or the corpus word types
  (`--by-error type`).
* *Same category*: of the errors a model fixed exactly and explained, how often its category is the
  one the label stands for (`error_labels.category_of`).  A rough diagnostic only: the corpus labels
  were not made for these categories, and `other` is not checked.  It is stored per row at run time
  (from the review), so rows from before 2026-11-09 show nothing there.

**Answer mode.**  `run --mode text` asks for the corrected text only, the app's `text` mode, on the
same texts; the default is the app's `review` mode.  Rows record their mode, the table keeps the
modes apart, and text-mode results go to files of their own (`…__text.jsonl`).  Text-mode rows have
no explanations, so "Explained", orphans and "Same category" stay empty for them.  It answers two
questions: what the JSON format costs in correction quality, and (since step 1 of a two-step design
*is* text mode) what the corrections of a two-step design would be before anyone builds it.

**The request per model, and a second try.**  The request the app sends is not the same for every
model.  `RequestProfile` (`domain/model_catalog.py`) holds the two knobs that differ, the catalog
carries one per model, and `request_profile(tag)` resolves it — the default for a model the catalog
does not know:

* `compact_json` (default **off**) adds `COMPACT_ANSWER_RULE` to the fixed part of a review request.
  It saves `gemma4` 25 to 30 % of its answer tokens, leaves `mistral` alone, and collapsed
  `qwen3.5:9b` to one sentence in 9 of 10 runs, so it is a per-model decision and not a global one.
* `temperature` is the model's proofreading temperature: the one its maker recommends
  (`RECOMMENDED_TEMPERATURES`), else `DEFAULT_CORRECTION_TEMPERATURE` (0.2), and always 0.2 for an
  online provider.  Teachers set none; the content review resolves its own the same way
  (`review_temperature`, default 0.7) and text recognition is fixed at 0.0.

Sampling is *not* in the profile: top-k, top-p and the penalties come from the tag, which is where
the vendors put their recommendations, and the app already uses them.  The three readers of the
fixed part — the corrector, the dialog that shows "what is sent for this file", and the cache key —
resolve the same object, so the dialog shows the request that is sent and a profile change
invalidates only that model's cached corrections.  `ANSWER_FORMAT_VERSION` is 1 (no release has shipped the format yet).

A review answer the window cut off is asked **once more**, unchanged, before the app falls back to
text mode; a second cut-off falls back as today, and a cut-off *repair* answer is not retried (the
repair request is the longest of all).  A runaway costs one more request and about twice the time of
that page, and keeps its explanations.  The evaluation mirrors it: `RecordingClient` sends `seed + k`
for the *k*-th identical request, so a repeat samples afresh as the app does, and gate 1 sends the
second try and stores both attempts (`attempt` 1 and 2).  Its verdicts: cut off twice is
`TRUNCATED` (hard), cut off once and then answered is `CUT_OFF_ONCE` (soft), and a second answer
that fails another test gets that test's verdict.

**Protocol version.**  What a run measured and how — the prompts, the answer format, the window, the
level and the long-text budget — is versioned in `tools/model_eval/protocol.json`, not by commit.
`run` computes the fingerprint of that request side (captured from the app's own corrector: system
and user message, answer schema, options, text budgets, repair message) before the first request
and stops with exit code 2 when it differs from the entry of `current`, so an uncommitted prompt edit is caught like a
committed one.  Raising the version means adding an entry with the new fingerprint, the date and a
line saying why, and setting `current` to it; do it when the check refuses, and also when the
scoring, the caps or the edit extraction change (the fingerprint stays the same then, but the golden
numbers of the tests fail).  Old entries are never edited or removed.  A run resumed after a tool,
Ollama or model change is refused as well, before the first request of any combination: the
sessions of one run have to agree on the protocol, the test set (by hash), the machine, the model
digest and the request settings including the timeout, and a results file without a header is
refused.  The export copies the
protocol from the run header and refuses a run whose version pair `protocol.json` does not record.
`tool_revision` stays in the header as a pointer for a human reader; nothing checks it.

**Early results (2026-11-07) are gone.**  The first comparison of writing models ran on
`gec-2026-11`, a set of eight short texts per language that is retired, and its numbers were
deleted with it.  The catalog's alternatives (`gemma4:e4b` on 8 GB, `gemma4:12b` on 16 GB) and
the temperatures follow that exploratory comparison and have not yet been confirmed on
`gec-2026-12`: the first gate-2 run there is the evidence the recommendations stand on.

**Does a model fit the graphics card at the base window (`PROOFREADING_NUM_CTX`, 8192), and how
fast is it?**  Measured with `nvidia-smi`, `ollama ps` and Windows' own memory counters on one
computer (RTX 3070 Laptop with 8 GB, 47 GB of main memory), window 8192 tokens, 250 generated
tokens, nothing else on the card:

| Model | Tokens per second | Video memory used | Free main memory used | On the card at 4096 → 8192 (Ollama's report) |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | 98 | 3.8 GB | 0.8 GB | 100 % → 100 % |
| `gemma4:e4b` | 77 | 4.4 GB | 1.1 GB | 100 % → 100 % |
| `qwen3.5:9b` | 43 | 6.4 GB | 1.3 GB | 100 % → 88 % |
| `ministral-3:8b` | 32 | 6.2 GB | 1.5 GB | 92 % → 84 % |
| `gemma4:12b` | 13 | 6.9 GB | 3.6 GB | not reliable |
| `ministral-3:14b` | 6.6 | 6.3 GB | 4.5 GB | 61 % → 56 % |

*Free main memory used* is the drop of free memory while the model is loaded; about 1 GB of it is
loading overhead that every model has, even those that sit entirely on the card, so the share that
really runs on the processor is roughly the value minus 1 GB (`gemma4:12b` about 2.6 GB,
`ministral-3:14b` about 3.5 GB).  Two consequences: `gemma4:12b` does **not** run natively on an 8 GB
card (it needs about 9.5 GB in total, which is the catalog's `memory_gb`, and generates at a sixth of
the speed of `gemma4:e4b`), and Ollama's size report is wrong for it (about 1 GB), so speed is the
reliable signal.  On this card Ollama keeps at most about 5.6 GB of weights on the GPU; the fixed window
of 8192 tokens moves part of `qwen3.5:9b` and `ministral-3:8b` to the processor.  `memory_gb` in the
catalog is the total a model needs at that window (card plus main memory).

**Speed, memory and repeatable runs.**  Every row of a `run` records what its text cost
(`ResultRow.performance`, `tools/model_eval/performance.py`): each request with Ollama's token
counts and durations, the models the server had loaded, the graphics-memory peak, the seed.  The
report shows it with `report --performance`: seconds per 100 words of student text (what a teacher
waits), answer tokens per second (how fast the machine runs the model), answer tokens per 100
words, requests per text (a repair or fallback adds one), graphics memory added over the idle
baseline.  Options of `run`:

| Option | Effect |
| --- | --- |
| `--seed N` (default 20261109), `--no-seed` | The app sends no seed; the evaluation sends one so a run can be repeated on the same machine and software.  `--no-seed` asks as the app does. |
| `--repeat N` | Run every text N times; repeat k uses seed + k - 1 and a file `…__rk.jsonl` |
| `--isolate` | Unload every model first, refuse a server that will not become idle, unload the model afterwards.  Only on a server you own |
| `--gpu-sampler amd\|nvidia`, `--gpu-ssh "ssh.exe user@host"` | Sample the graphics memory in use once a second, on this machine or through SSH (from WSL the Windows client `ssh.exe` holds the key) |
| `--machine NAME` | Written into the header |

Each results file gets a `….header.json` (a list, one entry per invocation) with the tool commit,
Ollama version, the model's digest, quantisation and size, window, seed, whether the server was
idle, and the idle graphics memory.  The protocol (`tools/model_eval/measure.py`): unload and check,
start the sampler, one unscored warm-up request that loads the model, then per text note what is
loaded before and after.  A text is **shared** when another model was loaded at either point; shared
texts are left out of every speed and memory figure.  Samples are stamped when they arrive here,
because the server's clock can be seconds off (it was 2 s ahead), and a one-second sampling
interval is the limit of the memory peak.  The speed runs still sample with the local process or
through SSH; the memory agent (see *Memory agent*) is the reading the fit check uses, and it is
where the speed runs' sampler is going — it reads the card on the machine that has it, without a
key and without a remote command.

**Committing a run as evidence.**  The cache above stays outside the repository (it holds the
source and reference texts).  `proofreading_store export` turns one run of that cache into a run
file below `evaluations/proofreading/` and checks it against the test-set manifest and the
corpora: scores and per-error outcomes are numbers, the model's edits are stored as positions and
short replacements, a replacement or explanation that would copy the corpus is cut down (at most 8
words per replacement, no run of 5 copied source words, no words at all from a restricted corpus),
and everything is hashed, so a committed run holds no corpus sentence.  `validate` checks a file
without the corpora (as the test suite does for every committed one), `index` regenerates the
checksums in `INDEX.md`, and `manifest` checks that the test set there is still the one the corpora
give — every corpus file and every item — and that it was built from a clean tree:

```bash
bazel run //tools:proofreading_store -- export --cache ~/gec-testset/results \
    --testset gec-2026-12 --manifest evaluations/proofreading/testsets/gec-2026-12/manifest.json \
    --machine rx9070xt-server --corpora ~/gec-testset/corpora.json
bazel run //tools:proofreading_store -- index --check
```

The rules, the file format and how to read a run back are in
`evaluations/proofreading/README.md` and `FORMAT.md`; the test-set manifest and the machine files
are written once and then pinned.

Things to know when reading the figures:

* **Compare speed only between runs on the same machine.**  The server has an AMD Radeon RX 9070
  XT, the laptop an NVIDIA RTX 3070; backends, memory and drivers differ.
* **Ollama's own size is unreliable for some models.**  `/api/ps` reported 0.39 GB, fully on the
  card, for `gemma4:e2b` while `amd-smi` showed the model adding 3.4 GB.  The report's *On the
  card* column is that estimate; trust the graphics-memory column.
* **Tokens per second is not waiting time.**  Models write different amounts for the same text:
  `gemma4:e2b` wrote 139 answer tokens per 100 words in text mode and 1039 in review mode, at the
  same 224 to 231 tokens per second, so a text took 0.7 s per 100 words against 4.8 s.
* **The AMD sampler was checked on a Radeon RX 9070 XT (`amd-smi` 27.1.0) and the NVIDIA one on a
  GeForce RTX 3070 Laptop.**  Other cards and versions may print other columns.
* A sampler started over SSH is killed by process id on the remote machine; stopping the local
  `ssh.exe` alone leaves `amd-smi` running there.

**Where the code lives.**  `tools/model_eval/` is one module per job, and the two command lines
(`compare_writing_models.py`, `proofreading_store.py`) only parse arguments and print:

| Module | Job |
| --- | --- |
| `answer_mode` | How a model is asked to proofread: `review`, `text`, `two-step`. |
| `languages` | The language codes of the test set and the names the corrector is asked in. |
| `redaction` | The caps that keep corpus text out of the repository, and what a "word" is. |
| `performance` | What one text cost: requests, tokens, seconds, memory. |
| `error_labels` | The corpus labels of the reference edits and the category agreement. |
| `scoring` | What the reports compute from counts: one F-beta, pooled. |
| `results` | The typed, versioned rows of the local cache and their JSONL format. |
| `run_format` | One committed run file: its schema, its typed items, read and write. |
| `layout` | Where the parts of the repository format live, and what a run is called. |
| `validation` | Format mode and strict mode of checking a run file. |
| `export` | A local run becomes a committed run file. |
| `index` | `INDEX.md`, one row per run. |
| `protocol` | The version file of the request side and its fingerprint. |
| `session` | The sessions of one run and whether it may be resumed. |
| `app_corrector` | The app's own corrector, as the tools build it. |
| `comparison` | Runs a test set through that corrector and turns the answers into rows. |
| `measure` | The requests, the server and the graphics memory of a measured run. |
| `runner` | The run loop: plan, resume, warm-up, header, sampler. |
| `summary` | The numbers a report is made of: one summary per model, temperature, language and band. |
| `report` | The results table, the band table, the by-error view and the performance table. |
| `grouping` | Which runs may be compared: the group key, the runs of one key, and the spread of a number over the repeats. |
| `gate_two` | Gate 2: the floors of one model, the comparison of two, the paired bootstrap. |
| `spot_check` | The explanation check: the sample of a run, the sheet, the marks. |
| `selection` | Which committed runs a gate-2 command reads, and what it refuses as ambiguous. |
| `testset` | The test set: its manifest, its items and the corpora they come from. |
| `corpora` | One module per corpus: what it may be used for, which files it is, how a pointer becomes a text. |
| `recipes` | One module per test-set id: the selection rule, its seed and the corpora it needs. |

`report` is a leaf that only the command lines import — the numbers it prints come from `summary`,
which `grouping` and `gate_two` read instead — and `measure` and `runner` are reached only through
`runner` and the comparison command line; a test in `test_session.py` reads the imports of every
module and refuses anything else.

### Adding a corpus or a test set

A corpus is one module in `tools/model_eval/corpora/`, named after the corpus with `-` replaced by
`_`. Nothing lists it: the package finds every module whose name does not start with `_` and reads
its `ADAPTER`, the way the app finds its providers. The class declares what a manifest needs —
`NAME`, `LICENCE` (copied word for word into every manifest that uses it), `RESTRICTED` (the licence
forbids passing on any part, so a run over it stores no words at all), `LANGUAGES`, `HINT` (what
`--corpus NAME=PATH` expects, for refusals and `--help`), `SPLITS` (the default first) and, for a
corpus that lives in the repository, `DEFAULT_ROOT` — and it implements two things: `files()`, every
file it reads keyed by the name the manifest records, and `resolve(item)`, which turns a pointer into
a text. **An adapter reads only the files it pins**: that is what makes `corpus_problems` meaningful,
and a test resolves every item of the invented set while recording the paths opened.

The selection *rule* is not the corpus's: one corpus serves several sets and one set draws on
several corpora. It lives in `tools/model_eval/recipes/`, one module per test-set id, found the same
way, setting `RECIPE` to a `Recipe(testset, seed, corpora, select)`. `prepare_gec_testset --testset ID`
picks the recipe, and `ID` is required: with two recipes a default would build the wrong set sooner
or later. A recipe never changes once its manifest is committed — another selection is another
test-set id.

Each needs its tests: `test_corpora.py` for the declarations, the refusals and the splits,
`test_corpus_extension.py` for the pattern (it defines a toy corpus inside the test file, adds it
with `monkeypatch.setitem(corpora.adapters(), …)` and takes it through build, check, export and both
modes of `validate`, without touching anything outside the test), and `test_corpus_regression.py`
for the committed set, which runs only with `GEC_CORPORA` set to a `{name: path}` file.

## Storage, hashing & caching

All persistent data lives under a single **storage root**, resolved by
`infrastructure/storage_root.py:resolve_storage_root()` — the
`TEACHERS_TEAMMATE_TMPDIR` environment variable when set, otherwise the per-user
OS cache directory (`~/.cache/teachers_teammate` on Linux).

```
<storage_root>/
├── ocr.toml                                   # configuration (the pipeline config)
├── artifacts/
│   └── <output_dir_key>/                      # key = sha256(output_dir.resolve())[:16]
│       ├── <stem>_preprocessed.png            # preview image (one per source stem)
│       ├── <stem>_original.<ext>              # page before preprocessing (only if steps changed it)
│       └── state/<stem>_<source_key>.json     # CACHE: one DocumentState per (output_dir, path)
└── benchmark/
    └── <document_hash>/                       # compute_file_hash(document)
        ├── document.json                      # {document_hash, display_name, last_path}
        ├── <UTC-ts>-<ocr_cfg_hash[:8]>.json   # StoredRun (self-describing)
        └── images/<UTC-ts>-<ocr_cfg_hash[:8]>.png
```

### Hashing scheme

- `compute_file_hash(path)` (`state_repository.py`) — SHA-256 of the file
  contents; the **identity of a document** regardless of its path or name.
- `compute_ocr_config_hash(config, ocr_prompt)` (`workflow/cache_service.py`) — a
  fingerprint of the OCR stage configuration (engine, model, preprocess
  method, language, prompt, temperature). The engine is hashed twice: a "provider"
  slot used to sit next to it, and keeping the shape keeps existing cached OCR results
  valid. Used by the cache to detect stale stages and by the benchmark store to label
  runs.
- `compute_correction_config_hash` / `compute_eval_config_hash` include an
  `"anonymized"` part while anonymisation is on (both stages see its text), so
  switching it on or off re-runs proofreading and content review for cached files.
  The part is absent while it is off, so results cached before the flag was hashed stay
  valid.  While it is on, the part also holds the resolved anonymizer settings —
  primary and secondary spaCy model plus every regex pattern (`tag=pattern`) — so a
  changed pattern no longer leaves a proofreading in place that was made with the old
  placeholders.  `installed_models` is deliberately not part of it: the hash is
  computed without importing spaCy.
- `compute_cache_key(value)` (`storage_root.py`) — a short (16-char) stable key
  used to map a path (output dir / source file) to an on-disk file name.

### Shared anonymization cache

`ProcessingApplicationService` keeps two caches under one `RLock`
(`application/service.py`):

- `_anonymizer_cache` — one `SpacyAnonymizer` per `(language, resolved settings)`;
  building one loads the spaCy models.
- `_anonymization_cache` — an `AnonymizationResultCache` (LRU, a few hundred texts,
  memory only) holding the anonymized text and its placeholder map, keyed by language
  plus resolved settings plus the SHA-256 of the text.

A pipeline run gets its anonymizer from the service: `run_selected()` hands the
`PipelineDependencies` a `PipelineComponentFactory` whose `build_anonymizer` is
`ProcessingApplicationService.build_cached_anonymizer`.  That returns a
`CachedAnonymizer` over the same two caches, so a run, `anonymize_preview()` and
`export_text(anonymize=True)` analyse a given text once per session and settings —
whichever came first fills the cache for the others.  `FileProcessor` no longer keeps a
per-file memo: it anonymizes the extracted text once, right after recognition, and hands
that result to proofreading and to the `_anonymized.txt` output.

Nothing anonymized is written to disk.  `clear_anonymizer_cache()` empties both caches
(switching the privacy settings or the language invalidates the entries anyway, because
the settings are part of the key).  The GUI's `OCRWorker` receives the main window's
service, so a run fills the cache that the window's previews and exports read; a worker
created without one builds its own service and shares nothing.

### Hiding names in the preview (`domain/anonymization.py`)

`domain/anonymization.py` is pure — no spaCy, no Qt, no I/O — and holds both directions of
the placeholder map (placeholder → original surface text):

- `restore(text, mapping)` — literal placeholder → original; what a run does with the text a
  proofreader returns.  `SpacyAnonymizer.restore` delegates to it.
- `mask(text, mapping)` — the inverse, used to show the *other* tabs with the stand-ins the
  extracted text got.  Longest original first (one regex alternation, so `"Anna Müller"` is
  not cut by `"Anna"`), and an original is only replaced where it stands as a word
  (`(?<!\w)` / `(?!\w)` only when the value starts or ends with a word character, so
  `"+49 30 …"` still matches as a whole).  A name the proofreader respelled is not found;
  that is a documented limitation, not a guess.

The panel (`gui/_preview_panel.py`) keeps the texts *unmasked* in `_stored_raw`,
`_stored_correction`, `_stored_evaluation` and `_stored_review` and refills the views through
`_refill_texts()` — masked when the setting is on, and read-only while it is (an edit would be
saved with the stand-ins).  The note above the texts (`_PrivacyNote`) is clickable only in that
locked state: a click emits `display_anonymized_changed(False)`, the eye button's route, so the
window stores the choice and the Settings → Privacy checkbox follows.  `_on_ocr_text_changed` / `_on_correction_text_changed` also keep
the stored text current, so switching the setting off shows the teacher's edits.  Placeholders
are marked as chips: `gui/_formatted_text.py:chip_placeholders()` wraps them in the rendered
Markdown HTML (markup is skipped, so a stand-in that reads like a tag cannot break the
document), and `_placeholder_selections()` gives the plain-text views the same highlight via
`setExtraSelections`.  No tooltip ever names the original.

`PreviewPanel` asks for the stand-ins with `anonymization_needed(text)` — once per text
(`_anon_requested`) — and the window answers through `_AnonymizePreviewThread`, which calls
`ProcessingApplicationService.anonymize_preview()` off the GUI thread and therefore reuses the
shared cache above (a run then needs no second spaCy pass, and vice versa).  A result that
belongs to a text the panel no longer shows is dropped (`set_anonymization(..., raw_text=…)`),
and a failure keeps the names hidden behind a note instead of showing them.  Changing the
anonymizer settings in the dialog calls `invalidate_anonymization()`.

The setting itself is a GUI preference, not part of `Config`: the checkbox under
*Settings → Privacy → Personal info: detection* and the eye button both end in
`MainWindow._apply_display_anonymized`,
which stores `privacy/display_anonymized` (`gui/_privacy_settings.py`,
`display_anonymized()` / `set_display_anonymized()`) and calls
`PreviewPanel.set_display_anonymized()`.  It defaults to **on** (`DISPLAY_ANONYMIZED_DEFAULT`):
only a stored answer shows the names, and without the add-on the checkbox is greyed out and the
setting takes effect once it is installed.  The checkbox is enabled only while
`ProcessingApplicationService.anonymizer_available(language)` holds — spaCy importable and the
model of the document language installed — so a missing add-on greys it out with a tooltip
pointing at Settings → Add-ons instead of failing later.  The answer is cached per document
language and refreshed on `data_flow_changed` (the language may have changed) and after the
privacy add-on is installed.

### Settings window

One modal window (`gui/settings/_window.py:SettingsWindow`) holds everything set up once;
the main window only holds what changes per batch.  The sidebar lists six pages
(`SettingsPage`): AI models, This computer (`AI_SETUP`: computer profile, local server) and Online services (`ONLINE_SERVICES`: the list of API keys), both drawn indented under AI models because they hold no choice of their own (`SUB_PAGES`), Scan quality, Privacy and Add-ons.  The main window
opens it through the single entry point `MainWindow.open_settings(page, focus=..., preselect=...)`
and, on OK, applies the result once (`_apply_settings_result`: update the panel, the
anonymizer rules and the "hide names" view setting, save, check the services, refresh the
queue); on "Restore defaults › All settings" it resets and saves (`_restore_all_settings`, which also resets the hide-names view and forgets the remembered "don't ask again" answers).

**Page protocol** (`gui/settings/_page.py:SettingsPageBase`).  A page is a widget that shows
a set of settings and never talks to another page:

| Method | Meaning |
|---|---|
| `load(values)` / `values()` | show / return **only the keys the page owns** |
| `owned_keys()` | those keys; "Restore defaults › This page" resets exactly them (`restore_defaults()`) |
| `apply()` | save what lives outside the settings dict (API keys, remembered answers), called by OK |
| `is_valid()` / `validity_changed` | OK is disabled while a page cannot be saved (an invalid regex) |
| `focus(focus, preselect)` | deep link: bring a part into view (`FOCUS_*` constants), tick entries |
| `on_shown()` / `on_packages_changed()` | refresh when shown / after an install finished |
| `set_hardware_setting()` / `set_ollama_url()` | follow what *This computer* changed |
| `close_blocker()` / `shutdown()` | why the window cannot close now / stop background work |

A new setting is added to the page that shows it: put the widget there and its key in
`load()`, `values()` and `owned_keys()` (a test checks that no key is owned twice and that every
page returns exactly its own keys).  Keys of the main window (languages, which steps run,
Results) stay in `ConfigPanel`.

**Pages are built lazily.**  Each page starts background work (model lists, hardware
detection, installed-package detection), so a page is built the first time it is shown, and
`SettingsWindow.values()` merges only the pages that were built.  An unvisited page changes
nothing.  Settings that other pages change meanwhile (computer profile, server address, an
engine's image preparation) are kept in the window's working copy, so a page built later
starts from them.

**Shared state is wired by the window**, not by the pages: the editable profile on *Where the AI
runs* feeds the read-only profile lines of AI models and Add-ons; its server address
(announced on `editingFinished`, never while typing) feeds the model lists; a finished
install on Add-ons calls `on_packages_changed()` on every built page and is re-emitted as
`SettingsWindow.packages_changed`; choosing a recognition method applies its default image
preparation on Scan quality.  **Deep links** between pages (a package hint's "Install…", "Download
a model…", "change…" next to the profile) are `navigate_requested` signals that the window turns
into `show_page(page, focus, preselect)`; `OpenDownloads` callbacks therefore return at once.

**API keys** are one `ApiKeyDrafts` (`gui/settings/_credentials.py`) owned by the window and passed
to pages as `SettingsContext.key_drafts`; the key row under the AI service and the list on *Where
the AI runs* are both views of it (`ApiKeyRow`).  A key goes into `os.environ` as soon as it is
committed — on `editingFinished` and 800 ms after the last keystroke — because providers read
the key from there when they list their models, and that list is the check that the key works.
It goes into the keychain/settings file only on OK (`apply()`, which also reports keys the
keychain refused); every other way out of `done()` (Cancel, Esc, close, Restore › All settings)
calls `discard()`, which puts the environment back, after the pages' `shutdown()` stopped any
commit timer.  Whether a key was set outside the app is recorded once at start-up
(`_credential_store.set_outside_app`), never read from the keychain.

**One model, an override, and the privacy line.**  The AI models page has one proofreading/review
block.  There is no setting for "different review model": the box starts ticked when the
*effective* pairs differ (`review_has_own_model`; an empty model means the task default), and
with it unticked `values()` writes the proofreading choice into the review keys.  The review
section is loaded only while ticked, and an unloaded section asks no service.  The context
window row (`proofreading_context`) is shown only while `context_choice(...).applies`, and
only after the computer has been checked.  Choosing an online service while names would be sent
as written is set to tick "Remove personal info": `privacy_line_state` decides the line, a
transition from not online to online sets the turn-on, and the window hands it to the main window
as `SettingsResult.turn_on_anonymization` — the stage guard and the confirmation before sending
stay the authority.

**Closing.**  `done()` calls `shutdown()` on every built page (a running model fetch is parked in
`_model_fetch._RETIRED_THREADS`, never destroyed while running).  A pip install or
language-model download on Add-ons blocks closing (`close_blocker()`), because the new package
must be made importable first; an Ollama pull belongs to the main window's
`OllamaDownloadManager` and keeps going.  Dialogs opened from inside the window (the
preprocessing preview, the add-on installers, message boxes) take the window — or its page — as
parent, so they cannot open behind it.

Tests: `tests/gui/test_gui_settings_pages.py` (pages and sections),
`tests/gui/test_gui_settings_window.py` (lazy pages, shared state, deep links, restore,
closing), `tests/gui/test_gui_addons_page.py`; the main window's side is tested with
`ScriptedSettingsWindow` (`tests/gui/_gui_harness.py`), a stand-in that returns a scripted
result.

### Cache vs benchmark store

These two stores have deliberately opposite shapes:

| | Pipeline **cache** (`StateRepository`) | Benchmark **run store** (`BenchmarkRunStore`) |
|---|---|---|
| Purpose | memoize/resume the latest result | keep an experiment history to compare |
| Cardinality | one entry per `(output_dir, path)`, **overwritten** | **many** entries per document, **append-only** |
| Keyed by | file *path* (+ output dir) | document *content hash* (global) |
| Config awareness | stores only a *hash* to detect staleness | stores the full `OcrConfig` to label runs |
| Expiry | **none** — grows unbounded, never garbage-collected | keep-last-N cap + manual deletion |

> **Note:** the pipeline cache currently has **no automatic expiry**. Re-running
> the same file overwrites its single entry in place, so it does not grow per
> run, but nothing is ever evicted (e.g. deleting a source document leaves its
> state behind). Only intermediate images (`*_step*.jpg`, `*_page*.png`) are
> cleaned, and a preview image is removed when its state is invalidated by a
> config change.

### Benchmark run retention

Because the benchmark store is append-only, it bounds growth two ways:

- **Manual deletion** — delete a single run, or all runs for a document, from the
  GUI (`delete_run` / `delete_all_runs`).
- **Keep-last-N cap** — `BENCHMARK_KEEP_LAST_N` (default 20, in
  `infrastructure/benchmark/run_store.py`). On each save, runs for that document
  beyond the newest N are evicted (both the JSON and its preview image).

## PDF embedded text

`PdfInputProvider` (`infrastructure/input_providers.py`) checks a PDF's text layer
before rendering pages for OCR.  When it is trusted, the provider returns one text
unit — the same path TXT files take, so `FileProcessor` skips OCR — and renders only
page 1 as the preview/source image.

The decision is a pure policy in `domain/pdf_text_layer.py`, controlled by
`ocr.pdf_text_layer` (`--pdf-text-layer`, TOML key `pdf_text_layer`):

| Mode | Embedded text is used when … |
|---|---|
| `auto` (default) | every page has ≥ 50 clean characters, images cover at most 5 % of the page, there are no content annotations (ink, text boxes, form fields, stamps …) and no curved/diagonal vector paths (pen strokes) |
| `always` | every page has some printable text |
| `never` | never — always OCR (the previous behaviour) |

The checks guard against a junk scanner OCR layer and against handwriting kept as an
image, annotation or vector strokes on top of typed text; such documents fall back to OCR
as a whole (no per-page mixing).  The limits are deliberately strict: a wrongly rejected
text layer only costs OCR time, a wrongly trusted one silently drops student work.  Control
characters in the embedded text are removed before it is stored.  Pages are measured lazily
and only up to the first one that rules the text layer out.

Where the setting applies:

- Input options travel as one `InputOptions` object (`infrastructure/input_provider_factory.py`);
  builders pick what they need.  The factory also answers which options affect a suffix
  (`input_settings_fingerprint`), so the OCR config hash only contains the mode for PDF
  sources and only when it is not `auto` — caches from before the setting existed, and every
  image/TXT result, stay valid.
- `InputPayload.text_origin` says whether text came from a TXT file or a PDF text layer; the
  `FileProcessor` status message uses it.
- `BenchmarkApplicationService` forces `never` (embedded text would make every engine return
  the same text), and the preprocessing preview always renders an image.

## OCR output formats & formatted text

OCR engines answer in different notations: plain text (Tesseract, Paddle), Markdown (many
vision-language models) or layout HTML (Chandra: `<div data-bbox=… data-label=…>` with inline
`<b>`, `<sup>`, `<del>`). There is no standard for this, so the pipeline normalises every
answer once, right after OCR, and everything downstream works on one canonical form.

```text
engine output ─► clean_ocr_text ─► OcrOutputPostprocessor ─► text + text_format  (stored)
                 (think blocks)    detect → parse → filter
```

| Piece | Where | Role |
|---|---|---|
| `FormattedDocument` (`Block`, `Span`, `Style`) | `domain/formatted_text.py` | format-neutral model: paragraphs, headings, lists, tables, figures; inline bold/italic/underline/strike/super-/subscript |
| `OcrOutputParser` | `interfaces.py` | one notation → `FormattedDocument` (`can_parse`, `parse`) |
| `ChandraHtmlParser`, `MarkdownParser`, `PlainTextParser` | `infrastructure/ocr_formats/` | tried in that order; plain always matches |
| `DocumentFilter` (`DropPageFurniture`) | `infrastructure/ocr_formats/filters.py` | drops page numbers from headers/footers (other header/footer text is content and stays) |
| `MarkdownRenderer`, `PlainTextRenderer` | `infrastructure/ocr_formats/renderers.py` | canonical Markdown for storage; markup-free text for diffs and metrics |
| `TextFormatter` | `application/formatting.py` | what adapters (GUI/CLI) call; they never import parsers |

**Stored form.** `DocumentState.raw_text` is either the engine's text verbatim
(`raw_text_format == "plain"`, e.g. Tesseract output, `.txt` input) or canonical Markdown
(`"markdown"`: CommonMark + GFM strikethrough/tables + inline `<sup>`/`<sub>`/`<u>`).
Text is only treated as Markdown when the engine's output carried real formatting, so plain
OCR output containing `- ` or `*` is never reinterpreted. The correction is written in the
notation of the raw text, so one `raw_text_format` field covers both. The field has a
default, so existing cache entries and benchmark runs load as plain text without a schema
bump — except entries written before the field existed whose text *is* raw model markup:
`StateRepository.load` drops those (the document is processed again) and
`StoredRun.from_json_dict` upgrades them to Markdown in memory, so markup is never shown or
compared as literal text. A model answer wrapped in one code fence (```` ```markdown ````)
is looked through before parsing, and the fence is dropped even when it wraps plain text.
Beside Chandra's `data-*` HTML, HTML a model writes on its own (`<table>`, `<p>`, `<br>`,
`<strike>`, …) is recognised as markup; a `# ` before a number (`# 3 Äpfel`) is not a heading.

**Answers of the proofreading and review models** pass through the same cleaning before they
are stored (`normalize_llm_answer` / `clean_llm_answer` in `ocr_formats/__init__.py`, applied
by the correctors and evaluators via `tidy_answer`): reasoning blocks, control tokens and a
wrapping code fence are removed. A correction of a Markdown text is read as Markdown and
written back canonically; for a plain text, markup the model added on its own is removed. A
failed call returns its fallback untouched. In the DOCX comments layout a word whose
formatting the correction changed (a crossed-out word that lost its strikethrough) gets a
comment like a changed word.

**Escaping.** `MarkdownRenderer` escapes only what CommonMark would interpret (`snake_case`
words and e-mail addresses stay free of backslashes, so the anonymizer's patterns still
match). Inline styles use Markdown delimiters when their flanking rules allow it and fall
back to `<b>`/`<i>`/`<s>` tags next to punctuation or other delimiters, so parsing the stored
text always yields the same styled spans. Ordered lists keep their numbers (`Block.number`
is recorded only where counting from 1 would be wrong).

**Who sees what.**

| Consumer | Receives |
|---|---|
| Corrector / evaluator | the stored text; for Markdown the system prompt gains a notation hint (`domain/notation.py`): crossed-out words are context, not to be corrected; inserted words belong to the sentence |
| Diff tab, benchmark similarity/word counts | `PlainTextRenderer` output — formatting marks never count as differences, crossed-out words are kept |
| DOCX | Word runs (bold, italic, underline, strikethrough, super-/subscript), headings, indented lists, nested tables |
| Preview panel | formatted rich text (`gui/_formatted_text.py`); the **Source** toggle shows the stored Markdown |

**Editing.** The formatted view is editable (Ctrl+B / I / U, Ctrl+Shift+X crossed out,
Ctrl+Shift+= inserted above the line). Edits are converted back to Markdown
(`document_from_qt` → `MarkdownRenderer`) and saved like any other manual edit. Bounding
boxes of layout engines are not kept.

**Adding a notation** (hOCR, DocTags, …): implement `OcrOutputParser` in
`infrastructure/ocr_formats/`, add it to `default_parsers()` ahead of `PlainTextParser`, and
add the module to `_OCR_SRCS` in `teachers_teammate/BUILD`. Parsers must be total: an
exception falls through to the next parser, ending at plain text.

## Correction review: schema, splice, align

Proofreading answers with a *review*, not a text: the corrected sentence plus one
explanation per edit, and a table of unclear or ambiguous expressions. Three pieces
make it work.

**Schema, answer format, message** (`infrastructure/correction_schema.py`,
`domain/answer_format.py`). `CorrectionAnswer` (sentences → changes, plus `unclear`) is the
Pydantic model both provider paths are bound to: Ollama through
`OllamaClient.chat(format=CorrectionAnswer.model_json_schema())`, online providers through
`with_structured_output(..., include_raw=True)`. The system message has two parts. The
*instructions* (the seven presets in `domain/correction_prompts.py`) say what to correct and
are per language. The *answer format* (`REVIEW_FORMAT` / `TEXT_FORMAT`) says how to fill in the
reply: one English text per mode, **no language names, no placeholders**. It describes the
structure field by field, because Ollama's `format=` only constrains the keys and never shows
the model the schema's field descriptions (online providers see them as well). The languages
travel in the message that carries the text (`Text language: …`, `Explanation language: …`;
`correction.py:_human_template`), so the answer format is plain text a teacher can edit.
Never put output-format rules into a preset. A test keeps `REVIEW_FIELDS` and the schema in
step: every field name must appear in `REVIEW_FORMAT`. On the native Ollama path
`OllamaClient.chat(system=...)` sends the instructions as a real `system` message (it used to
send everything as one user message). `ANSWER_FORMAT_VERSION` (= `CONTRACT_VERSION`) is part
of the correction cache hash and is bumped with every change of the formats or the schema.

**Editable answer format.** `answer_format_review` / `answer_format_text` (`""` = built-in)
are settings in `DEFAULTS`, `Config`, the config file and the GUI settings dict, stored with
the `answer_format_version` they were written for. `config.py:migrate_answer_formats()` —
called by `load_config_file` and `ConfigPanel.load_from_dict` — drops an edit written for
another version (with a warning on stderr), so an old edit never describes another structure
than the one the app asks for. The editor (`gui/_prompt_editor.py:_FormatBox`) shows a hint
when an edited review format no longer names a field (`domain/answer_format.py:missing_fields`).
There is no CLI flag; set them in the config file.

**What the cache is keyed on.** `compute_correction_config_hash()` hashes the *resolved*
instructions and answer formats (from `instruction_parts()`), not the settings: `""` means
"the built-in text", which can change with the app, and editing a preset must not serve results
made with the old text.

**Storing prompts as references** (`domain/correction_prompts.py`, `config.py`,
`gui/_config_panel.py`). A saved `correction_prompt` of `""` means "the built-in prompt of
the document language" — the GUI never stores preset *text*. Copies that older versions
saved are recognised by `is_preset_prompt_text()` (against `PRESET_PROMPTS` and
`LEGACY_PRESET_PROMPTS`, the presets of every released version, compared after whitespace
normalisation) and reset to `""` on load, in `ConfigPanel.load_from_dict` and in the CLI's
`load_config_file`; `evaluate_prompt` gets the same check against its default. The dialog
policy and the answer format come from `instruction_parts()` via
`ProcessingApplicationService.correction_instructions()` — the same code path the
corrector uses, so what the dialog shows is what is sent. The prompt editor is **one editable
box that always shows the text in use** (`prompt or builtin`); *Reset to default* puts the
built-in text back, and leaving the editor with the built-in text unchanged stores `""`, not a
copy (`PromptEditor.prompt()`). The unreleased texts of the review window
(`SUPERSEDED_PRESET_PROMPTS`) are recognised like the released ones. `normalize_output_mode()` / `normalize_level()` in
`domain/correction_policy.py` give an unknown setting the same meaning in every layer, and
the saved output mode is used as it is for every model: there is no per-model block, because
Ollama's `format=` is enforced by the engine and works with all local models (a model that
still fails falls back to corrections only per file). `structured_output` in the catalog only
decides the `explanations ✓` badge; to earn it, run
`tools/verify_model_catalog.py --structured-output` against the model and enter the result.
The same run decides whether a model's answers are cut off at the context size, so a badge
never sits next to a model that truncates (see *Soft failures and per-file warnings*).

**Splice** (`domain/correction_review.py:splice`). The model answers only the sentences it
changed, so the full text is rebuilt by walking the anonymized source with a cursor and
replacing each `original` with its `corrected`: exact match, then whitespace-normalised
(OCR line breaks), then fuzzy. The fuzzy lookup compares the anchor with whole
sentence-like pieces of the text after the cursor (up to four in a row; `difflib` ratio
≥ 0.9, the earliest best one wins). It must return a complete piece, never a fragment:
replacing a fragment would leave the rest of the old sentence in the text. It searches the
rest of the text, not a window next to the cursor, because the model returns only changed
sentences, which are rarely adjacent. Text between anchors is copied verbatim, so line
breaks, paragraphs and Markdown marks survive. A sentence that matches nowhere is *unplaced*: not applied, its
index recorded, and the caller falls back to text mode when nothing at all could be placed.

**Align** (`domain/correction_review.py:align`, entered through
`application/correction_review.py:CorrectionReviewPresenter.aligned`). The local word diff
stays the ground truth: `align()` diffs raw against corrected and attaches notes to hunks.
A note belongs to a hunk when it *changes* the hunk's tokens (`change_tokens()`: what only
one side of the note has, punctuation included, so `Tom → Tom,` is the comma). Notes of the
hunk's own sentence come first; a note may serve several hunks, because the word diff cuts
`his friend → her friends` in two, and a note not used yet wins a tie. A hunk without a
note is either the model's change without a reason (`by_teacher=False`, shown as "no
explanation") or the teacher's own edit (`by_teacher=True`, "your change"); it is the
teacher's when its new words are not in the model's version of its sentence. A hunk outside
every changed sentence never borrows a note from another sentence (only from a sentence whose
wording could not be found in the text at all). Alignment is
recomputed from `(raw, corrected, review)` on every render and needs no extra state. A note
whose hunk is gone becomes an *orphan* ("not in the text"). Unclear expressions are
searched independently of each other, because the model lists them in any order. Every
count shown anywhere (`review_summary()`) is the number of *listed* hunks of the aligned review:
spelling and punctuation hunks (`AlignedChange.is_minor`, `MINOR_CATEGORIES`; a note-less hunk
that touches no word character counts too) are marked in the text but listed only as one
collapsed line in the notes view and left out of the DOCX *Explanations* list.
Hunks come from `diff_opcodes()`, which the GUI diff uses as well so the numbers agree; it
reports a whitespace-only difference (an OCR line break the model joined into a space) as
`equal`, so it is never listed as an unexplained change.
`align()` takes plain text and strips Markdown marks from the anchors itself, so the DOCX
builder can call it without importing the application layer. The DOCX comments layout cuts
the text into whitespace words, which line up with the hunks only roughly, so it asks for
the note that changes most of a part's tokens instead of looking up exact hunks.

**Failures** (`infrastructure/_llm_invoke.py:StructuredResult`). A schema-bound request
ends as success or as one of three failures. `transport`: the provider call raised; the
source text is kept and the warning is the complete "Correction failed (…)" sentence.
`unsupported`: the model or provider cannot bind a schema at all; no repair is attempted.
`invalid`: it answered, but not in the schema. An invalid answer with some text gets one
repair request that quotes the validation error. Unsupported, invalid-and-empty and
still-invalid answers all end in the corrections-only fallback for that file, with the
fixed fallback warning; the raw validation error is only ever sent back to the model.
A `transport` failure sets `CorrectionOutcome.failed`, and `FileProcessor` then does not store
the unchanged source as a finished correction; a corrections-only fallback is stored. An
Ollama server that answers the `format` request with HTTP 400/422 counts as `unsupported`
(`OCRError.status_code`), not as a network failure.

In the diff the changed and unclear words themselves are the links (`note:N`,
`unclear:N`); a superscript `?` appears only for an unclear expression a change covers
completely, since links cannot nest. Every highlight sets its text colour as well as its
background: the app runs on a dark theme, whose light text is unreadable on light colours.

Link tooltips in the diff are shown from a `ToolTip` event handler, not through an HTML
`title` attribute: Qt does not decode entities in attribute values, so a quote in an
explanation would show as `&quot;`.

`pydantic` is imported by `correction_schema.py`, so it is declared in `pyproject.toml`
(it already arrives with `ollama`).

Both functions are pure and table-tested in `tests/unit/test_correction_review.py`; the
review views are tested with pytest-qt in `tests/gui/test_gui_review_views.py`.

## Error handling

### Design goals

The codebase follows three principles for error handling:

#. **Typed, descriptive exceptions** — each failure mode has its own class so
   callers can catch exactly what they need without inspecting message strings.
#. **Hard failures propagate; soft failures return warnings** — unrecoverable
   problems (missing provider package, unreachable Ollama service, unreadable
   input) raise exceptions and abort the affected file.  Recoverable problems
   in optional stages (LLM correction, evaluation) are captured as warning
   strings and passed to the caller so processing can continue.
#. **Structured logging instead of bare `print`** — all diagnostic output uses
   `logging.warning` / `logging.error` so the GUI's log panel and the CLI's
   stderr stream both receive the same messages without any special-casing.

### Exception hierarchy

All custom exceptions live in `teachers_teammate/exceptions.py` and are
re-exported from the package root for convenience.

```{uml} assets/error_handling.puml
:align: center
:alt: Exception hierarchy and raising sites
:width: 100%
```

| Exception | Base | Raised by |
|---|---|---|
| `OCRError` | `Exception` | OCR processor implementations |
| `ConfigFileNotFoundError` | `FileNotFoundError` | `config.resolve_config_path` |
| `ConfigFileParseError` | `ValueError` | `config._load_config_section` |
| `StorageResolutionError` | `RuntimeError` | `storage_root.resolve_storage_root` |
| `ProviderNotAvailableError` | `RuntimeError` | provider modules, `ocr_processor`, `stage_builder` |
| `OllamaConnectionError` | `RuntimeError` | `ollama_utils.check_model` |
| `ResponseTruncatedError` | `OCRError` | `ollama_utils.OllamaClient.chat` |
| `PipelineInputError` | `ValueError` | `input_provider_factory`, `preprocess_service` |

Inheriting from the appropriate built-in (`ValueError` for bad input,
`RuntimeError` for infrastructure failures, `FileNotFoundError` for missing
files) lets callers that do not import the custom class still fall through to a
sensible `except` branch.

### Soft failures and per-file warnings

The `Corrector.correct()` and `Evaluator.evaluate()` ABCs return
`tuple[str, str | None]` — the processed text plus an optional warning message.
Implementations never raise on LLM communication errors; they return
`(original_text, "Correction failed: …")` instead.  `FileProcessor` collects
all non-`None` warning strings into a `warnings: tuple[str, ...]` field on
`ProcessingResult` so the application layer can surface them to the user without
interrupting the batch run.

```python
# interfaces.py (simplified)
class Corrector(ABC):
    def correct(self, text: str, language: str) -> tuple[str, str | None]:
        """Return (corrected_text, warning_or_None)."""
        ...

class Evaluator(ABC):
    def evaluate(self, text: str, language: str) -> tuple[str, str | None]:
        """Return (evaluation_text, warning_or_None)."""
        ...
```

**A truncated answer is never used.** Ollama has two ways of running out of context, and only
one of them is visible afterwards:

- *The answer is too long.* The last stream chunk reports `done_reason == "length"`.
  `OllamaClient.chat_with_stats()` returns that chunk's `ChatStats`, and `chat()` — the entry
  point every caller uses — raises `ResponseTruncatedError` instead of handing the fragment
  on. (Prompt and answer then add up to exactly `num_ctx`; verified on Ollama 0.40.)
- *The prompt is too long.* Ollama silently drops part of the prompt and still reports
  `done_reason == "stop"`, with a `prompt_eval_count` below the real prompt size. Nothing in
  the answer shows it, and the `ollama` client library (0.6) cannot send `truncate=false`,
  which would make the server refuse instead. So `chat()` estimates the request size before
  sending (`ensure_prompt_fits()`) and raises the same error. It applies only to requests that
  set `num_ctx` and carry no image; the estimate errs towards refusing.

`_llm_invoke.py` turns the error into the failure kind `"truncated"`, with the warning
`TRUNCATED_MESSAGE` (*"The text was too long for the model's working memory."*). In text mode
the original text is kept. In review mode a cut-off answer — the first one, or a **repair**
request that is cut off or that `_ReviewCorrector._repair_fits()` already knows will not fit —
only gives up on the structured answer: `_correct_review()` returns `None` and the caller falls
back to text mode, which needs less room (shorter system prompt, and an answer about as long as
the text, where a review answer is longer). The file fails only if text mode is cut off too. The
fit of a repair is checked *before* sending, so the doomed round trip is skipped. A provider
error (`"transport"`) still fails the file at once: text mode would meet the same server.

#### The context-budget rule

One pure module, `domain/context_budget.py`, owns both decisions:

```python
@dataclass(frozen=True)
class ContextBudget:
    num_ctx: int
    chars_per_token: float = 3.0
    def request_fits(self, request_chars: int, *, answer_tokens: int) -> bool: ...
    def max_text_chars(self, overhead_chars: int, *, answer_factor: float = 0.0,
                       answer_tokens: int = 0) -> int: ...
```

- `chars_per_token` is 3.0, a cautious estimate (German clean prose is about 4, misspelt words
  fewer), so the check refuses a little early rather than too late.
- **Proofreading in text mode** answers with the text again, so the request may take half of the
  window (`answer_factor=1.0`, `answers_with_the_same_text()`); that is the check
  `ensure_prompt_fits()` makes before every request.
- **Proofreading in review mode** answers with several times the text: per changed sentence the
  original, the corrected wording and a list of changes with explanations, plus the JSON keys.
  `NativeOllamaCorrector.max_text_chars()` therefore sizes the pieces with
  `answer_factor=REVIEW_ANSWER_FACTOR` (7.0, measured on 2026-11-10 with the compact answer
  format: 5.7 and 6.5 times the text's tokens on `gemma4:e4b`). The answer grows with the text,
  not with the request, so the fixed part of the request is taken out of the window first and
  only the rest is shared: `text_tokens = (num_ctx − overhead_tokens) / (1 + answer_factor)`.
  At 8192 that is about 2,800 characters of text per review request, about 5,870 at 16384; a
  page of 200 to 400 words goes out whole, a longer text in page-sized pieces.
- **Content review** answers short and of a known length, so it passes
  `answer_tokens=EVALUATION_ANSWER_TOKENS` (1536, an estimate, measured not yet). That removes
  the artificial half — a review can use about (8192 − 1536) × 3 ≈ 19 900 characters minus its
  prompt.

`ensure_prompt_fits()` delegates here, and `OllamaClient.chat()` takes the allowance from the
caller (`answer_tokens`, `None` means "as large as the request"). Over the limit a proofreading
request warns `TRUNCATED_MESSAGE`; a content review warns its own sentence,
`TOO_LONG_FOR_REVIEW_WARNING` (*"The text is too long for a content review with a local model."*).
Both stages use the same `num_ctx` (see below), so no reload happens between them.

#### How large the window is

`domain/context_budget.py` also decides the window, from a fixed ladder
`CONTEXT_STEPS = (8192, 16384, 32768)` — a ladder rather than an arbitrary size, because a
window that changes between files makes Ollama reload the model for every file.

- `automatic_num_ctx(spec, card_gb, cap=…)` returns the largest step at which
  `memory_at_context_gb(spec, step)` still reaches `FULL_SPEED_SHARE` of the graphics card, so a
  larger window can never turn "Works" into "Slower". Caps: `PROOFREADING_NUM_CTX_CAP` (8192, the
  base window) for proofreading, `EVALUATION_NUM_CTX_CAP` (32768) for content review.
- **Proofreading stays at 8192.** A window of 16384 sends a long text in one request instead of
  two, and the answer is then about twice as long — exactly the case small models drift on, and
  the reason the cap exists. No gate measures that trade, so the app does not take it: both gates
  run at 8192, and proofreading does too. The cap is raised together with a gate run at 16384.
- `memory_at_context_gb()` needs the measured `context_gb_per_8k` of the catalog entry. A model
  without that figure, a custom model, a computer without a card, a remote server and a manual
  profile with an unknown card size keep 8192.
- The `proofreading_context` setting (`"auto"` by default, or a fixed step) overrides the rule —
  for advanced users and remote servers. An unknown value falls back to `"auto"` with a warning,
  as `hardware_profile` does. With the proofreading cap at the base window it no longer changes
  proofreading: it sizes a content review that runs on **its own** Ollama model, which can still
  get 16384 or 32768. A content review on the same model as proofreading gets 8192 as well, so in
  the default setup the setting changes nothing. It keeps its name, so existing config files stay
  valid.
- `run_windows()` resolves both stages once per run, each from **its own** model
  (`proofreading_spec`, `evaluation_spec`), so a larger review model is not sized from the
  smaller proofreading model; `stage_builder.resolve_run_windows()` applies it to a `Config`, and
  a stage whose provider is online reports the base window (the app does not set one). When
  proofreading and content review use the same Ollama model, both get the smaller window (Q4): one reload per file between the stages would cost
  seconds on every run, and content review still gains from its own answer allowance.
- The window is sized for **one model at a time**. `run_files()` runs every stage of one file
  before the next file, so OCR and proofreading take turns and never run in parallel; a larger
  window can therefore make Ollama unload the idle OCR model and load the writing model on every
  file. That is an accepted cost (decided 2026-11-12), the same one `ollama_utils.py` already
  names for a small card: nothing slows down or fails, the models only swap.

The resolved window is passed down (`CorrectionSettings.num_ctx`, `StageBuilder.resolved_windows()`)
and used by `proofreading_options()`, `ContextBudget`, the chunk budget below, the correction
cache fingerprint and the memory rating and run-start warning (`memory_at_context_gb`, per
model via `stage_builder.window_of_model()`). It is not a module constant any more. The
fingerprint part `num-ctx-{…}` exists only for an Ollama corrector and holds the window the run
really uses (`RunWindows.proofreading`, resolved once and passed to the cache service), so results
made at 8192 and at 16384 cannot share a cache entry; the evaluation hash carries
`num-ctx-{windows.evaluation}` for the same reason — a review that failed with "too long" can
succeed at a larger window. Without resolved windows the key falls back to
`window_from_setting()`, the pinned window up to the proofreading cap.

Adding the evaluation part changed the content-review hash of every Ollama setup once, so after
that update each cached content review counts as stale and is made again (proofreading results
are untouched). The GUI asks `ProcessingApplicationService.resolved_windows()` for the keys it
expects; that never probes the graphics card (the probe runs `nvidia-smi` and must stay off the
GUI thread), so until this computer has been probed it returns the base windows, and the main
window probes in a background task and shows the queue again.

#### Long texts are proofread in pieces

`ChunkingCorrector` (`infrastructure/correction.py`) decorates the native Ollama corrector in
`stage_builder.py`; LangChain providers (online, large windows) are not wrapped. On **every call**
it asks its inner corrector for `max_text_chars(language, text_format=…)` — computed from that
corrector's own system prompt and the fixed part of the human message, so the figure cannot drift
from what is really sent, and per call because the Markdown notation hint makes the system prompt
longer — and:

- a text within that budget goes to the inner corrector unchanged, one request, as before;
- a longer text is split by `domain/text_chunks.py` and each piece corrected on its own. The
  pieces are joined with their original separators, the reviews are concatenated, and the
  `unplaced` indices are shifted by the number of sentences before them.

`split_for_budget(text, max_chars, text_format)` keeps the invariant
`"".join(c.text + c.separator for c in chunks) == text`: every character is in exactly one chunk,
in its `text` or in the `separator` that follows it. It cuts at paragraph boundaries (blank lines),
packing paragraphs greedily; a paragraph that is still too long is cut at sentence ends (`.`, `!`,
`?`, `…` followed by whitespace), because a model can only fix a sentence it sees whole. A single
sentence longer than `max_chars` is cut at line breaks, and as the last resort between two
words (unpunctuated handwriting, a list or table with one entry per line). Only a single word
longer than `max_chars` cannot be split; it becomes its own chunk and its request reports "too
long". In Markdown a closed fenced code block is never split, however long: a fence opened in one
piece and closed in the next would be a different document, so an over-long fence is its own chunk
and reports "too long". A fence that is never closed is no code block (stray OCR noise) and is
cut like any other text. A table or list without blank lines is one paragraph, so it stays whole
as long as it fits, and is cut at line breaks when it does not.

Failure is all-or-nothing: **any piece failed → the whole file fails** and keeps the original text
(`CHUNK_FAILURE`), because a half-corrected file must never be cached. The pieces after the failed
one are not sent (their answers would be discarded, and each would wait out the timeout on an
unreachable server). A piece that fell back to
text mode does not fail the file; it costs the explanations of that piece and the merged result
says `MISSING_REVIEW_WARNING` (*"Explanations are missing for part of the text."*). Chunking is
deterministic for a text and a window, so the cache fingerprint carries the window and a later
change of it proofreads the file again.

Known costs: there is no context across a cut (a change that spans a boundary cannot be made,
which cutting at sentence ends keeps rare), and one request per piece is slower in total than one
big request would have been — if one could fit.

#### Not yet measured

Both figures this section needs come from `fit` (see *Model sizes*), and neither has been measured
yet — that needs Ollama on the machine with the card:

| Measurement | How |
|---|---|
| Memory of an already loaded model (WP2.2) | `fit` records the drop in available main memory around a load next to Ollama's `size`; a drop that matches it means the run-start check counts a loaded model once |
| Context-cache growth (WP4.5) | `fit` reads `ollama ps` and the card at `num_ctx` 8192, 16384 and 32768 for one model |

One pass does both:

```bash
bazel run //tools:verify_model_catalog -- fit --server LABEL \
    --ollama-url http://HOST:11434 --gpu-agent http://HOST:11436 --tag TAG
```

It appends to `evaluations/model-fit/sizes.jsonl`, and `fit --report` prints the figures without a
server. The growth fills `OllamaModelSpec.context_gb_per_8k`; until a model has a figure, it stays at
8192. How fast a context cache grows depends on the attention design (sliding-window or
linear-attention layers grow much more slowly), so it cannot be derived from the model size. The
report puts the need at 8192 next to the catalog's `memory_gb`, gives the growth per 8192 tokens from
two spans (they must agree, or the growth is not linear) and flags a step that ran partly on the
processor. The figures go into the catalog by hand, from `fit --catalog`.

`EVALUATION_ANSWER_TOKENS = 1536` is an estimate until one real review's `eval_count` has been read.

OCR is the exception on purpose: it limits a page with `num_predict`, which also ends with
`done_reason == "length"`, but a partial page is still the best result. `OllamaOCRProcessor`
passes `allow_truncated=True` and keeps the text read so far; an answer that is empty *and* was
stopped at its output limit raises `OCRError` instead, because there is nothing to keep. OCR requests
keep Ollama's own window on purpose: a page is bounded by `num_predict`, and a window the app set
would only make the OCR model reload when the writing model's window differs. Costs of a fixed
window: a server started with a larger `OLLAMA_CONTEXT_LENGTH` is overridden downwards, and a model
that both reads scans and proofreads is reloaded on each switch between the two stages.
`prompt_eval_count` counts only tokens Ollama had to evaluate, so it can be lower than the prompt
when a prefix is cached; read it as a lower bound.

The evaluation tools (`tools/verify_model_catalog.py`, `tools/compare_writing_models.py`) pin
`PROOFREADING_NUM_CTX` (8192) on purpose: their verdicts are compared across models and across
months, and a window that followed the card of the machine would make two runs incomparable.

### Logging

Every module that emits diagnostic output creates a module-level logger:

```python
import logging
_logger = logging.getLogger(__name__)
```

The two entry points configure the root logger once:

- **CLI** (`cli.py:run_cli`): `logging.basicConfig(level=WARNING, stream=sys.stderr, …)`
- **GUI** (`gui/_main_window.py:main_gui`): same `basicConfig` call; the GUI
  worker additionally redirects `sys.stderr` to a Qt signal so `WARNING` and
  `ERROR` messages from any module reach the in-app log panel automatically.

The convention is:
- `_logger.warning(…)` — non-fatal, processing continues (e.g. DPI metadata
  could not be restored, a cleanup file could not be deleted).
- `_logger.error(…)` — file could not be processed but the batch continues.
- `print(f"ERROR: …")` — reserved for CLI user-facing status lines where the
  message is part of the normal output contract (not a log event).

### GUI error feedback

Long-running background fetches (model lists, spaCy model availability) use Qt
signals to report failures to the UI thread without blocking:

- `_ModelFetchThread` (in `_model_fetch.py`) emits `error = Signal(str)` if
  the network call fails; the receiving slot sets a tooltip on the status label.
- `_SpacyModelFetchThread` (in `_anonymizer_config_widget.py`) emits the same
  `error` signal; the slot makes a red status label visible and sets its text.

This prevents silent failures where the combobox simply stays empty and the user
has no indication of what went wrong.

## Downloads and installation

:::{note}
This section is only relevant if you are working on the in-app download or addon-installation feature. For day-to-day development you can skip it.
:::

This section explains how the in-app Settings → Add-ons page installs packages and
models, and how those installations survive application restarts.

### Addon packages directory

Optional packages that are installed at runtime — spaCy, PaddleOCR, LLM provider connectors,
GPU monitoring libraries — all land in a **persistent user-writable directory** that is separate
from the application bundle or the development venv:

```
Windows:  %LOCALAPPDATA%\teachers_teammate\packages\
Linux:    ~/.local/share/teachers_teammate/packages/
macOS:    ~/Library/Application Support/teachers_teammate/packages/
```

The location is computed by `addon_manager.get_packages_dir()` using
`platformdirs.user_data_dir`. This directory is prepended to `sys.path` by
`inject_packages_dir()` so anything installed there is importable.

`inject_packages_dir()` is called:

- At application startup (`run_gui.py`, `gui/__main__.py`, `__main__.py`), so packages
  installed during a previous session are immediately available.
- After every successful install or model download, so the newly installed package is
  usable in the same session without a restart.

The function is idempotent — safe to call multiple times.

### pip package install (frozen builds)

The frozen Windows/macOS binary bundles pip via PyInstaller's `--collect-all pip`.  Because
`sys.executable` in a frozen build is the application `.exe`, running
`[sys.executable, "-m", "pip", ...]` re-enters the frozen entry script without a recognised
dispatch flag and launches the GUI again instead of pip.

To avoid this, every pip install in a frozen build re-invokes the exe with the custom flag
`--_pip_install_mode` followed by the normal pip arguments, plus `--target <packages_dir>`.
The entry script (`tools/build/run_gui.py`) intercepts this flag before any GUI code is loaded
and calls `pip._internal.cli.main()` directly:

```python
# tools/build/run_gui.py
if "--_pip_install_mode" in sys.argv:
    _pip_args = sys.argv[sys.argv.index("--_pip_install_mode") + 1:]
    from pip._internal.cli.main import main as _pip_main
    raise SystemExit(_pip_main(_pip_args))
```

`addon_manager.install_addon_subprocess()` uses this mechanism for the addon packages
(spaCy, PaddleOCR, GPU monitoring).  `_PipInstallThread` in `settings/_addons_page.py` also
uses it when `sys.frozen` is `True`.

In development (non-frozen) builds, both code paths fall back to the standard
`[sys.executable, "-m", "pip", "install", "--upgrade", ...]` without `--target`, so packages
land in the active venv as usual.

All pip installs run one at a time (`_PIP_LOCK` in `addon_manager.py`), so the automatic
install below and Settings → Add-ons never write to the packages directory together.

### GPU monitoring package (automatic, with consent)

The System Stats panel needs `pynvml` (NVIDIA) or `pyamdgpuinfo` (AMD, Linux only).  In a
frozen build the main window asks the service (`pending_gpu_monitoring_addon`, background
thread) whether this computer has a GPU whose package is missing.  If so, it asks the user
**once** before downloading it (answer kept in QSettings under `gpu_monitoring/download`),
then installs it with a short network timeout (`install_gpu_monitoring`) and refreshes the
panel without a restart.  A failed install is remembered per app version
(`gpu_monitoring/failed_in_version`) and is not retried automatically; Settings → Add-ons
remains the manual path.  Source checkouts are never touched (`can_install_addons`).

GPU *detection* (`infrastructure/gpu_detector.py`) does not need that package: it reads the
Windows display-adapter registry keys or Linux sysfs first and runs `nvidia-smi` /
`rocm-smi` / `amd-smi` only to complete a name or memory size.  `nvidia-smi` is tried on
`PATH` first and then at its usual Windows locations (a packaged app's `PATH` can lack the
driver folder); if it still gives no answer, the optional `pynvml` package is asked.  AMD integrated graphics
(under 1 GB of dedicated memory) is never reported.

### spaCy library

spaCy is an optional dependency (the privacy/anonymization feature).  It is installed from
pip like any other package — via the Add-ons page's name-detection tab or, for frozen builds, via
the dedicated "Install Privacy Addon" flow in `AddonInstallerDialog`.

### spaCy language models

spaCy NER models (e.g. `en_core_web_sm`) are distributed as Python wheels on
**GitHub Releases**, not on PyPI.  A plain `pip install en_core_web_sm` fails because PyPI
does not host them.

`addon_manager.download_spacy_model_subprocess()` handles the download in two steps:

1. **URL resolution** — `spacy.cli.download.get_compatibility()`, `get_version()`, and
   `get_model_filename()` compute the exact wheel URL for the installed spaCy version.
2. **Wheel install** — `pip install --target <packages_dir> --prefer-binary <url>` downloads
   and unpacks the wheel into the addon packages directory.

In frozen builds, URL resolution must happen inside the frozen Python interpreter
(which has spaCy bundled), so the exe is re-invoked with `--_spacy_download_mode <model>
--target <packages_dir>`:

```python
# tools/build/run_gui.py
if "--_spacy_download_mode" in sys.argv:
    _model = sys.argv[idx + 1]
    # resolve URL, then call pip._internal.cli.main(["install", "--target", ..., url])
```

After a model download, `inject_packages_dir()` ensures the model is immediately usable.
`spacy.load("en_core_web_sm")` finds the model because `packages_dir` is on `sys.path`.

### Ollama models

Ollama models are pulled via the **Ollama HTTP API**, not pip.  The Add-ons page's
"AI models for this computer" tab calls `ProcessingApplicationService.pull_ollama_model()`, which uses
the `ollama` Python client to stream a `POST /api/pull` request to the configured Ollama
server (default `http://127.0.0.1:11434`).

Ollama stores models in its own data directory (OS-managed, outside this application).
No `sys.path` manipulation is involved.  The Ollama server must be running before a pull
can start.

### Summary table

| What | How downloaded | Where stored | Required at startup? |
|---|---|---|---|
| pip packages (spaCy, PaddleOCR, …) | pip via `--_pip_install_mode` (frozen) or `-m pip` (source) | packages_dir (frozen) / venv (source) | `inject_packages_dir()` |
| spaCy language models | pip wheel from GitHub Releases | packages_dir | `inject_packages_dir()` |
| Ollama models | Ollama HTTP API (`/api/pull`) | Ollama data dir | Ollama server running |
| Tesseract | OS package manager (not in-app) | System PATH | Tesseract binary on PATH |

**Focus in the Changes tab** (`gui/_review_views.py`). Choosing a change or an unclear
expression — by clicking its words in the text or its number in the list — marks it in both
views. The text uses Qt *extra selections* (`ReviewDiffView._apply_focus`, found through the
anchor name of the links), not HTML, so a click neither re-renders the page nor loses the
scroll position; the list marks the entry with the focus background and opens the collapsed
minor-changes line when the entry is in it. List numbers run 1…n over the listed changes while
the links keep the hunk number of the diff. Tests click with `qtbot.mouseClick` on a position
found with `anchorAt()`; emitting `anchorClicked` by hand would not show what a person sees.
