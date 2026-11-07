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
- `suitability()` / `models_for()` — the rating used by the Downloads dialog and the
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

The check sends exactly what the app sends: `DEFAULTS["correction_temperature"]` and
`proofreading_options()` (see *Soft failures and per-file warnings*). It covers five fixture
texts — plain English, plain German, plain French, Markdown with formatting marks, and one
invented full page of dense German prose (~350 words, the worst case for the context size
because German tokenises longest). A run passes when the answer is valid JSON for the schema,
not empty, changes something (an untouched text on an error-dense fixture was not proofread),
at least 90 % of its sentences can be placed in the text, at least `_EXPLAINED_FLOOR` (0.40) of the
changes that need an explanation carry one with non-blank text, and `done_reason` is not
`length`. At a temperature above 0 every fixture runs three times. A model passes when at most
`_ALLOWED_FAILED_RUNS` (1) of its runs fail, none of them was cut off, crashed or too long for the
window, and the explained share over all of its runs together reaches `_EXPLAINED_OVERALL` (0.75):
one stray miss in 15 (the empty answer the Qwen models give about once in 15 runs) does not take
the `explanations ✓` badge away, a pattern does.
Each run prints placed / explained / orphans / prompt and answer tokens / `done_reason` /
seconds.

**How the explanation rule was set.** The short fixtures have only 3 to 7 changes, so one change
moves a run's ratio by about 0.2, and a single threshold "just below the default" (the plan's first
idea, about 0.70) would have failed every candidate. The first full check (2026-11-07, seven models,
15 runs each, `evaluations/writing-models-2026-11/model-check-results-2026-11-07.md`) gave the
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
`--structured-output` and skips the catalog tally:

```bash
bazel run //tools:verify_model_catalog -- --tag ministral-3:8b --tag gemma4:e4b
bazel run //tools:verify_model_catalog -- --tag qwen3.5:9b --temperature 0.2
```

A writing model that can also read scanned pages lists `also_uses=frozenset({"ocr"})`;
`catalog_uses()`, `suitability()` and `models_for()` then treat it as a reading model as
well (the Downloads dialog still lists it once, under its primary category). Entries
whose tag starts with `hf.co/` are pulled from Hugging Face, so
`tools/verify_model_catalog.py` checks them against the Hugging Face file list instead
of the Ollama registry.

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
| Widgets | `gui/_hardware_profile_selector.py`, `gui/_model_guidance.py`, `gui/_downloads_dialog.py` | Presentation only. They rate models through `rate_ollama_models(..., cached_only=True)` — never contacting Ollama on the GUI thread. |

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

The proofreading and content review dialogs share one base class
(`_AiServiceDialog` in `gui/_settings_dialog.py`); the subclasses only name their settings
keys and wording.  What it relies on:

- **Each provider module declares its package** in `PROVIDER_INFO["package"]`
  (`{"module": "langchain_mistralai", "pip": "langchain-mistralai"}`); Ollama's connector is
  `package_optional` because the app talks to Ollama directly.  The service answers
  `missing_provider_package()` and `list_provider_packages()` (providers sharing a package
  appear once), so the dialogs, Downloads and the run preflight need no list of their own.
  A missing package is a `RequirementIssue(provider=...)`; the app then opens Downloads on
  the providers tab with `provider:<name>` in `preselect`.
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
list in Privacy Settings and "Ask again" all use it.  Records from earlier versions hold the
fingerprint only; `domain/data_location.py:describe_fingerprint()` turns it into words.

The fingerprint is versioned.  Version 2 starts with `v2|` and adds the data region, so an
answer given before the region was shown no longer matches and the user is asked once more
(correct: the region was not part of that answer).  Parts are separated by `|` and their
fields by `:` — the host may itself contain a colon and is always the field before the
last one, which is why the age of a record is a prefix rather than another field.
`describe_fingerprint()` reads both formats.
`PrivacySettingsDialog` (`gui/_privacy_settings_dialog.py`) extends the anonymizer dialog
with a first tab for these answers; its changes are applied on OK only.

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
```

### Choosing writing models: the quality comparison

A writing model must pass two gates before it is recommended.  **Gate 1** is the
structured-output check above (`verify_model_catalog.py`): can it fill in the review format
reliably?  **Gate 2** (`compare_writing_models.py`) asks how good its corrections are.  Both
tools send what the app sends (gate 2 goes through `NativeOllamaCorrector` itself, with the
app's own tokeniser and word diff), so a result describes the real request.

```bash
# 1. Build the test set from the public corpora (kept outside the repository)
python3 -I tools/prepare_gec_testset.py --falko-merlin ~/gec-corpora/fm/data \
    --wi-locness ~/gec-corpora/bea/wi+locness --out ~/gec-testset/set
# 2. Send every text to the models (results are appended; an interrupted run resumes)
bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set \
    --out ~/gec-testset/results --model gemma4:e4b --temperature 0.2
# 3. Print the table / write the list of explanations a teacher marks
bazel run //tools:compare_writing_models -- report --out ~/gec-testset/results
bazel run //tools:compare_writing_models -- sheet --out ~/gec-testset/results \
    --model gemma4:e4b --temperature 0.2 > spotcheck.md
```

**Test set.** Per language eight texts of 60 to 155 words with reference corrections, plus ten
sentences that need no correction (given to the model as one text).  German: Falko-MERLIN GEC
corpus (test split; Falko CC BY 3.0, MERLIN CC BY-SA 4.0), MERLIN letters whole and Falko essays as
excerpts of consecutive sentences.  English: W&I+LOCNESS v2.1 (BEA-2019 dev split, levels A, B, C;
sentences without edits from the native-speaker essays; non-commercial use only).  French: **no
public learner corpus with corrections exists**, so `tools/gec_fr_invented.jsonl` holds eight
invented texts and ten correct sentences written by an AI model, not a teacher: French results
are indicative only.  The corpora are real learner text and licensed for research, so neither they
nor the built set belong in the repository.  Limits to keep in mind: the reference is one annotator's
version; a few "correct" sentences contain errors the annotators missed; British spelling in the
English references makes American spelling count as an error; eight texts per language show only
large differences.

**Metrics** (per language, summed over the texts).  *Precision / recall / F0.5*: a change counts
when the model's word diff against the source has a hunk covering the same words with the same
replacement as the reference's (exact match, so absolute values are low for every model and only
the comparison counts; F0.5 weighs precision twice as much as recall because a wrong change costs
a teacher more than a missed one).  *Left alone*: how many of the ten correct sentences come back
unchanged.  *Added marks*: texts whose proofread text contains Markdown marks (`**`, `__`, `~~`)
the source did not have; **these marks are ignored when scoring** (`report --count-marks` counts
them), the column only shows how often they occur.  *Failed / fallback*: reviews that failed or fell back to corrections only.
*Explained*: changes that need an explanation and carry one.

**Results, 2026-11-07** (Ollama 0.40.13 on an RTX 3070 Laptop with 8 GB, official Ollama tags;
mean F0.5 over German, English, French; numbers only):

| Model | T | F0.5 de | F0.5 en | F0.5 fr | Mean | Left alone de / en / fr (of 10) | Texts with added marks | Failed or fallback | Seconds per German text |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma4:12b` | 0.7 | 0.47 | 0.46 | 0.97 | **0.635** | 5 / 4 / 10 | 0/27 | 0/27 | 54 |
| `gemma4:12b` | 0.2 | 0.46 | 0.45 | 0.97 | **0.628** | 5 / 5 / 10 | 0/27 | 0/27 | 54 |
| `gemma4:e4b` | 0.2 | 0.46 | 0.40 | 0.98 | **0.612** | 4 / 4 / 10 | 0/27 | 0/27 | 19 |
| `gemma4:e4b` | 0.7 | 0.49 | 0.40 | 0.94 | **0.611** | 3 / 5 / 10 | 0/27 | 0/27 | 17 |
| `qwen3.5:9b` | 0.2 | 0.33 | 0.37 | 0.87 | **0.527** | 0 / 4 / 10 | 0/27 | 0/27 | 42 |
| `qwen3:4b` | 0.7 | 0.42 | 0.37 | 0.67 | **0.486** | 6 / 5 / 8 | 0/27 | 0/27 | 13 |
| `ministral-3:14b` | 0.7 | 0.37 | 0.36 | 0.69 | **0.476** | 0 / 5 / 8 | 17/27 | 0/27 | 219 |
| `qwen3.5:9b` | 0.7 | 0.28 | 0.27 | 0.77 | **0.440** | 0 / 0 / 10 | 0/27 | 0/27 | 45 |
| `ministral-3:14b` | 0.2 | 0.39 | 0.36 | 0.56 | **0.434** | 3 / 3 / 4 | 17/27 | 0/27 | 212 |
| `ministral-3:8b` | 0.2 | 0.31 | 0.26 | 0.67 | **0.411** | 2 / 2 / 7 | 1/27 | 0/27 | 46 |
| `ministral-3:8b` | 0.7 | 0.23 | 0.20 | 0.65 | **0.358** | 2 / 2 / 9 | 7/27 | 0/27 | 48 |

Seconds are for this 8 GB card; the 14B model runs partly on the processor.  Gate 1 for the same
models: see `evaluations/writing-models-2026-11/model-check-results-2026-11-07.md`.

What the numbers say:

* Every model changes sentences that were already correct (the typical weakness of small
  models); only `gemma4` and `qwen3:4b` leave a useful share alone in German and English.
* **`gemma4:e4b` and `gemma4:12b` beat `qwen3.5:9b`, the default at the time of the check, in
  all three languages** at both temperatures, and are barely affected by the temperature.
  `gemma4:e4b` takes about 40 % of the time per text.
* **`qwen3.5:9b` is better at 0.2** than at the app's 0.7 (mean 0.527 against 0.440, better F0.5
  in all three languages); its gate-1 result is 14 of 15 runs at both temperatures.
* **Both Ministral models mark their changes** with `**bold**` inside the proofread text (17 of 27
  texts for the 14B model), which the format does not ask for.  It is ignored in the scores above;
  with the marks counted as errors the means fall to 0.240 / 0.222 (`ministral-3:14b`) and 0.275
  (`ministral-3:8b` at 0.7).  Scored by words only they are mid-field: in German and English
  `ministral-3:14b` (0.37 and 0.36) beats `qwen3.5:9b` at 0.7 and matches it at 0.2, in French
  (0.56 to 0.69) it is clearly behind, and in all three languages it trails both `gemma4` models.
  The vendor ships temperature 0.15; 0.2 helps the 8B model (0.358 to 0.411), not the 14B one.
* `qwen3:4b`, the no-graphics-card model, scores 0.486 at 0.7 and 13 s per text.

**Decision rules of the plan, applied** (the writing-model plan, no longer in the tree; the
catalog, the recommended lists and the defaults follow them).  R1: the 8 GB alternative is
`gemma4:e4b` (mean F0.5 0.611 against at most 0.411 for `ministral-3:8b`); the 16 GB alternative
is `gemma4:12b` (0.635 against at most 0.476 for `ministral-3:14b`, which also needs about four
times as long per text on this card).  R2 is met: lowering `qwen3.5:9b` from 0.7 to 0.2 improves
F0.5 in all three languages and gate 1 gives the same result as at 0.7 (14 of 15 runs; no
repetition loops), and the temperature is neutral for `gemma4`, so the app's default can become
0.2.  R3 is met for 16 GB: `gemma4:12b` wins F0.5 over `qwen3.5:9b` in all three languages.  The
same holds for `gemma4:e4b` on 8 GB, which the plan's target state did not provide for.  The
teacher spot-check of explanations is still missing; a provisional reading by an AI model of ten
explanations per language and model found at least 8 of 10 correct for both `gemma4` models in
German and English and 10 of 10 in French, against 6 of 10 for `qwen3.5:9b` in French (two wrong:
`rester` called pronominal, `au jardin` explained as "outside the garden").

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
window stores the choice and the Privacy Settings checkbox follows.  `_on_ocr_text_changed` / `_on_correction_text_changed` also keep
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
*Advanced Settings → Privacy Settings → Personal info: detection* and the eye button both end in
`MainWindow._apply_display_anonymized`,
which stores `privacy/display_anonymized` (`gui/_privacy_settings.py`,
`display_anonymized()` / `set_display_anonymized()`) and calls
`PreviewPanel.set_display_anonymized()`.  It defaults to **on** (`DISPLAY_ANONYMIZED_DEFAULT`):
only a stored answer shows the names, and without the add-on the checkbox is greyed out and the
setting takes effect once it is installed.  The checkbox is enabled only while
`ProcessingApplicationService.anonymizer_available(language)` holds — spaCy importable and the
model of the document language installed — so a missing add-on greys it out with a tooltip
pointing at the Downloads dialog instead of failing later.  The answer is cached per document
language and refreshed on `data_flow_changed` (the language may have changed) and after the
privacy add-on is installed.

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
- **Proofreading in review mode** answers with more than the text: per changed sentence the
  original, the corrected wording and a list of changes with explanations, plus the JSON keys.
  `NativeOllamaCorrector.max_text_chars()` therefore sizes the pieces with
  `answer_factor=REVIEW_ANSWER_FACTOR` (2.0, an estimate): the request may take a third of the
  window, so the answer is not cut off on an error-dense text.
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
  larger window can never turn "Works" into "Slower". Caps: 16384 for proofreading (a review
  answer repeats the text and small models drift on long answers), 32768 for content review.
- `memory_at_context_gb()` needs the measured `context_gb_per_8k` of the catalog entry. A model
  without that figure, a custom model, a computer without a card, a remote server and a manual
  profile with an unknown card size keep 8192.
- The `proofreading_context` setting (`"auto"` by default, or a fixed step) overrides the rule —
  for advanced users and remote servers. An unknown value falls back to `"auto"` with a warning,
  as `hardware_profile` does.
- `run_windows()` resolves both stages once per run, each from **its own** model
  (`proofreading_spec`, `evaluation_spec`), so a larger review model is not sized from the
  smaller proofreading model; `stage_builder.resolve_run_windows()` applies it to a `Config`, and
  a stage whose provider is online reports the base window (the app does not set one). When
  proofreading and content review use the same Ollama model, both get the smaller window (Q4): one reload per file between the stages would cost
  seconds on every run, and content review still gains from its own answer allowance.

The resolved window is passed down (`CorrectionSettings.num_ctx`, `StageBuilder.resolved_windows()`)
and used by `proofreading_options()`, `ContextBudget`, the chunk budget below, the correction
cache fingerprint and the memory rating and run-start warning (`memory_at_context_gb`, per
model via `stage_builder.window_of_model()`). It is not a module constant any more. The
fingerprint part `num-ctx-{…}` exists only for an Ollama corrector and holds
`window_from_setting()` — the pinned window up to the proofreading cap, so `16384` and `32768`
share a key — until the card-derived window is threaded in (see below).

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

Two tables are still empty; both need Ollama, so neither was run during the review:

| Measurement | How |
|---|---|
| Memory of an already loaded model (WP2.2) | `psutil.virtual_memory().available` against `ollama ps` (`size`, `size_vram`) after a correction, on Linux/WSL and on Windows |
| Context-cache growth (WP4.5) | `ollama ps` after one request at `num_ctx` 8192, 16384 and 32768, for `qwen3:4b`, `gemma4:e4b`, `gemma4:12b` and `qwen3.5:9b` |

The first decides whether the run-start check counts a loaded model twice (see above). The second
fills `OllamaModelSpec.context_gb_per_8k`; until a model has a figure, it stays at 8192. How fast a
context cache grows depends on the attention design (sliding-window or linear-attention layers grow
much more slowly), so it cannot be derived from the model size. `EVALUATION_ANSWER_TOKENS = 1536`
is an estimate until one real review's `eval_count` has been read.

OCR is the exception on purpose: it limits a page with `num_predict`, which also ends with
`done_reason == "length"`, but a partial page is still the best result. `OllamaOCRProcessor`
passes `allow_truncated=True` and keeps the text read so far; an answer that is empty *and* was
stopped at its output limit raises `OCRError` instead, because there is nothing to keep. OCR requests
keep Ollama's own window. Costs of a fixed window: a server started with a larger
`OLLAMA_CONTEXT_LENGTH` is overridden downwards, and a model that both reads scans and proofreads
is reloaded on each switch between the two stages. `prompt_eval_count` counts only tokens Ollama
had to evaluate, so it can be lower than the prompt when a prefix is cached; read it as a lower
bound.

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

- `_ModelFetchThread` (in `_settings_dialog.py`) emits `error = Signal(str)` if
  the network call fails; the receiving slot sets a tooltip on the status label.
- `_SpacyModelFetchThread` (in `_anonymizer_config_dialog.py`) emits the same
  `error` signal; the slot makes a red status label visible and sets its text.

This prevents silent failures where the combobox simply stays empty and the user
has no indication of what went wrong.

## Downloads and installation

:::{note}
This section is only relevant if you are working on the in-app download or addon-installation feature. For day-to-day development you can skip it.
:::

This section explains how the in-app "Downloads & Packages" dialog installs packages and
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
(spaCy, PaddleOCR, GPU monitoring).  `_PipInstallThread` in `_downloads_dialog.py` also
uses it when `sys.frozen` is `True`.

In development (non-frozen) builds, both code paths fall back to the standard
`[sys.executable, "-m", "pip", "install", "--upgrade", ...]` without `--target`, so packages
land in the active venv as usual.

All pip installs run one at a time (`_PIP_LOCK` in `addon_manager.py`), so the automatic
install below and the Downloads dialog never write to the packages directory together.

### GPU monitoring package (automatic, with consent)

The System Stats panel needs `pynvml` (NVIDIA) or `pyamdgpuinfo` (AMD, Linux only).  In a
frozen build the main window asks the service (`pending_gpu_monitoring_addon`, background
thread) whether this computer has a GPU whose package is missing.  If so, it asks the user
**once** before downloading it (answer kept in QSettings under `gpu_monitoring/download`),
then installs it with a short network timeout (`install_gpu_monitoring`) and refreshes the
panel without a restart.  A failed install is remembered per app version
(`gpu_monitoring/failed_in_version`) and is not retried automatically; the Downloads dialog
remains the manual path.  Source checkouts are never touched (`can_install_addons`).

GPU *detection* (`infrastructure/gpu_detector.py`) does not need that package: it reads the
Windows display-adapter registry keys or Linux sysfs first and runs `nvidia-smi` /
`rocm-smi` / `amd-smi` only to complete a name or memory size.  `nvidia-smi` is tried on
`PATH` first and then at its usual Windows locations (a packaged app's `PATH` can lack the
driver folder); if it still gives no answer, the optional `pynvml` package is asked.  AMD integrated graphics
(under 1 GB of dedicated memory) is never reported.

### spaCy library

spaCy is an optional dependency (the privacy/anonymization feature).  It is installed from
pip like any other package — via the Downloads dialog's spaCy tab or, for frozen builds, via
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

Ollama models are pulled via the **Ollama HTTP API**, not pip.  The Downloads dialog's
"Ollama Models" tab calls `ProcessingApplicationService.pull_ollama_model()`, which uses
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
