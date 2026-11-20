# Development

## Scope

This guide focuses on contributor workflows and codebase structure.
For runtime tuning and provider/model strategy, see the
{doc}`advanced user guide <advanced_user_guide>`.

## Prerequisites

- Python 3.14
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
bazel test //:lint          # all three: ruff, pylint and ty
bazel test //:py_ruff
bazel test //:py_pylint
```

ruff and ty come with `aspect_rules_lint` (see `MODULE.bazel`), the same ruff the formatter
uses; a version bump is a rules_lint bump. Run them through Bazel only — a `ruff`/`ty` from a
local `.venv` can be a different version and report different results.

The linters (ruff, ty, pylint, shellcheck for the `.sh` scripts and PSScriptAnalyzer for the `.ps1`
scripts, all from `aspect_rules_lint`; `bazel test //:lint` runs them; the PowerShell one is skipped
off Linux, and its rules are in `third_party/lint/PSScriptAnalyzerSettings.psd1`) cover the whole first-party tree: the app, the tests and every folder of
`tools/` (the build, packaging and release scripts decide what ships, so they get the same gate as
the app). A new script in an existing folder is picked up by that folder's `all_lib` /
`scripts_lib` / `scripts` target; a new folder under `tools/` has to be added to `//:py_ruff`,
`//:py_ty`, `//:first_party_sources` (or `//:sh_shellcheck`) in the root `BUILD`.

The workflow files have their own linters, because rules_lint has none for them (`lint-ci` in
`.github/workflows/format_lint.yml` runs them):

```bash
python -m venv /tmp/ci-lint && /tmp/ci-lint/bin/pip install -r tools/ci/requirements.txt
/tmp/ci-lint/bin/actionlint                    # syntax, expressions and the shell of every run: block
/tmp/ci-lint/bin/zizmor --offline .            # security; exceptions and reasons: .github/zizmor.yml
```

A workflow holds no logic. What a `run:` block would decide (a tag's form, the release files, an
install test) is a script in `tools/` with a unit test, so it runs and fails the same way on a
laptop.

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

How tests are written, run and given their Bazel dependencies: see *Testing Guide*
(`docs/testing.md`).

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

The processing stages and the choices at each stage:

```{uml} assets/pipeline_stages.puml
:align: center
:alt: Teacher's Teammate processing pipeline stages and options
:width: 100%
```

### Where text lives

Every sentence a teacher reads is *defined* in `teachers_teammate/texts/` and *used* by
reference everywhere else. The package is Qt-free, so `gui/`, `cli.py`, `application/` and
`infrastructure/` can all import it; one module per surface:

| Module | Owns |
|---|---|
| `texts/_types.py` | The shapes: `Notice`, `Question`, `Choice`, `FilePrompt`, `Answer`. Text and nothing else. |
| `texts/dialogs.py` | Every message box, question and file picker. |
| `texts/settings.py` | Settings page titles, the `WHERE_*` "where do I change this" sentences, tab names. |
| `texts/privacy.py` | The "what is sent for this file" wording. |
| `texts/check.py` | What the Check tab's widgets say around its text: the two editable views' tooltips, the ⋯ button, the source dialog's button, the scan switch, the strips. |
| `texts/setup.py` | Every sentence of the Setup Assistant's pages. |
| `texts/servers.py` | How a found Ollama server is described (the card). |

`texts/__init__.py` is the map — a docstring naming which module owns what, no logic.

Rules:

- **Dependencies point inward.** `texts` may import `domain` ids and enums; `domain` and
  `application` never import `texts`. Where a layer holds a sentence today, the sentence
  moves out and the layer keeps the id.
- **Dynamic parts are functions**, never `%`-formatting at the call site:
  `dlg.no_files_found(folder)`, not `"No files found in %s" % folder`.
- **One helper shows them.** `gui/_dialogs.py` is the only module that touches
  `QMessageBox` or `QFileDialog`:

  ```python
  from ..texts import dialogs as dlg
  from . import _dialogs

  _dialogs.notify(self, dlg.no_files_found(str(folder)))
  if _dialogs.ask(self, dlg.replace_existing_files(count, shown)):
      ...
  folder = _dialogs.pick_folder(self, dlg.select_output_folder(start))
  ```

  `notify(parent, notice)`, `choose(parent, question) -> Answer`,
  `ask(parent, question) -> bool`, `pick_folder` / `pick_file` / `pick_files` /
  `save_file(parent, prompt)`. A prompt carries where the dialog opens in `start` (for
  `save_file`, the path it proposes); build it with that argument — `dlg.save_export_to_folder(label, start)` —
  or the picker opens wherever Qt last was. A test patches this module once instead of every window.

**The ratchet test.** `tests/unit/test_user_text_centralised.py` parses every module under
`gui/` and `cli.py` with `ast` and fails on a string literal (or f-string) passed to a
user-facing sink — `QMessageBox`/`QFileDialog` calls, `setText`/`setToolTip`/
`setPlaceholderText`/`setWindowTitle`/`setTitle`, widget constructors, `addTab`/`addMenu`/
`addAction`, `print` and argparse `help` in `cli.py`, `raise …Error("…")`, and a literal handed to a
function of `dlg` / `privacy` (`dlg.copy_failed("Could not copy")` — the sentence belongs in
the function). A sentence that is only *returned* or kept in a local variable is not a sink, so
the test cannot see it: build it in `texts/` and call it. The
allowlist is `tools/user_text/allowlist.py`, keyed by file and literal text (not line
number, which shifts on every edit). It may only shrink: a second test fails on an entry
whose literal is gone, so moving a text means deleting its entry in the same change.
`tools/user_text/inventory.py` scans the same sources and writes a CSV of what it found
(`bazel run //tools/user_text:inventory -- --out user_text.csv`); the allowlist itself is edited
by hand.

**Out of scope, and must stay where it is:** the LLM prompts in
`domain/correction_prompts.py` (versioned and hashed into the protocol fingerprint and the
cache key — moving text changes cache keys), log lines, `diagnostics.log`, developer
exceptions that never reach a teacher, `tools/`, tests and docs. The Word output's en/de
label tables follow the *document* language, not the UI language, and stay in
`docx_builder.py` / `docx_comments.py`.

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
`teachers_teammate.gui.benchmark:main_benchmark`; also **Tools → Benchmark…** in the main
window), helps judge OCR quality for a
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
  (`_ocr_config_selector.OcrConfigSelector`, `_image_view.ZoomableImageView`,
  `_diff_widget.DiffWidget`, `_log_widget.LogWidget`, `_app_bootstrap.create_app`,
  `_qt_stream.QtStream`). The main window's Check tab has its own views
  (`gui/check/_text_views.py`, see *The window tabs and the Check page* below); plain
  `DiffWidget` is what the benchmark compare view and the privacy preview dialog show.

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
mismatch against the catalog counts as a failure. A model without the flag (`mistral:latest`) is
offered and used like any other model, but has no badge and is not recommended.

How the flag, the sizes and the quality comparison are measured — gate 1, `fit`, the memory
agent, gate 2 — is in `tools/model_eval/README.md`.

A writing model that can also read scanned pages lists `also_uses=frozenset({"ocr"})`;
`catalog_uses()`, `suitability()` and `models_for()` then treat it as a reading model as
well (Settings → Add-ons still lists it once, under its primary category). Entries
whose tag starts with `hf.co/` are pulled from Hugging Face, so
`tools/verify_model_catalog.py` checks them against the Hugging Face file list instead
of the Ollama registry.

Every entry also names the terms its weights come under (`OllamaModelSpec.licence`, a
`ModelLicence` with the name, a link and — only when the licence restricts *use* — one sentence).
The app never ships a model, so a permissive licence (MIT, Apache-2.0) adds nothing a teacher has
to know; a restrictive one does.  Today that is the default reading model only: Chandra OCR 2's
weights are under a modified OpenRAIL-M licence: free for personal use and research, and for
other use only below $2M in revenue or funding; schools are not named, so the note asks a school to
check the licence.  `licence_note(tag)` returns the
sentence; the Add-ons model tree shows it in its *License* column and the Setup Assistant in the
tooltip of the download box.  The facts were read from each model's licence on 2026-11-18 — check
them again when a model is added or a tag moves to a new version.

### Rating a model against the computer's memory

`suitability()` / `rate_model()` rate a catalogued model for a task on a computer, from what
the app detected there (graphics memory, total main memory) or, when the profile was chosen by
hand, from the size the profile stands for.  `memory_gb` of a catalog entry is the total memory
the model needs at the proofreading window of 8192 tokens (card and main memory together),
measured where possible (`tools/model_eval/README.md`, *Model sizes*).

| Quantity | Rule (constants in `domain/model_catalog.py`) |
| --- | --- |
| Card budget | `CARD_USABLE_SHARE` (85 %) of the card's memory; Apple Silicon 66 % of the unified memory (`HardwareDetection.card_gb`); none without a card |
| Reserve | the larger of `SYSTEM_RESERVE_MIN_GB` (8 GB) and `SYSTEM_RESERVE_SHARE` (20 %) of the total main memory; swapping to disk is never counted as memory |
| Works (`WORKS`) | at least `FULL_SPEED_SHARE` (0.9) of the model fits the card; without a card, up to `CPU_COMFORT_GB` (5 GB: a page in under five minutes on an ordinary processor, measured with `fit --cpu-only`) |
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

### Speed estimates: the test, the real runs and the time left

Three questions, three answers, one owner each:

| Question | Answered by | Where |
| --- | --- | --- |
| How fast is this computer? | the speed test, when the teacher asks for it | `infrastructure/performance_probe.py` |
| How long will a run take? | the estimate from the store | `application/speed_estimates.py` |
| How long is left? | the pace of the run so far | `gui/_run_status.py` |

No widget computes a duration: `gui/_user_text.py` formats the figures the service returns, and
`gui/_run_status.py` turns them into the note on the progress bar.

#### Where the teacher sees it

The speed of a computer is a fact about the computer, so the **Test speed** button, the rows per
step and the warnings are on *Settings → This computer* (`gui/settings/_speed_panel.py`, group
"Speed on this computer"), next to the computer's power, its graphics card and its server.  The
test measures the models chosen on *AI models*: the window hands the panel those settings
(`SettingsWindow._speed_settings`), unsaved choices included, and redraws it when the choice
changes (`AiModelsPage.speed_choices_changed`).  *AI models* only shows the result, read-only:
one line with the class-set time, in the "This computer" box at the top of the page
(`gui/settings/_computer_box.py`).
The panel owns no setting, so it adds no key to any page's `owned_keys`.

`SpeedChoices` has two entry points.  `from_settings` (the Settings window) includes every step
whose provider is Ollama, ticked or not, because the test measures models, not the next run.
`from_config` (the time left of a run, `ProcessingApplicationService.speed_choices_for_run`) leaves
out the steps the run switches off, so a re-run of text recognition alone is not charged for
proofreading.  An empty `review_model` means the content review does not run on Ollama; it never
means "same as proofreading" (a review on the proofreading model names it).

The proofreading estimate follows the exchange.  A full run sends three requests and every one
of them reads the text again — step ② reads the student's text and the corrected one — so the
reading time is the text times the copies the steps carry, and the writing time is the answers of
all three together.  *Corrections only* sends step ① alone: one copy, one answer.  The figures
come from the exchange's `cost_model()` until three samples of the mode the run is set to
(`exchange` or `text`) say how long this class's answers are.

#### What is measured, and with which request

The test sends the app's own requests, so a figure means what a run will cost:

| Step | Request | Answer length |
| --- | --- | --- |
| Reading | the OCR prompt with a drawn A4 page | tokens per page |
| Proofreading | step ① of the exchange on a test text | tokens per 100 words |
| Review | the content-review request on the same text | tokens per 100 words |

The proofreading step measures step ① because it is the request every run sends, and the test
measures rates, not answer lengths: a plain rewrite of the paragraph, at that step's temperature
and thinking, with no answer schema.

`MEASURE_ANSWER_TOKENS` (200) caps the two writing steps: enough for a steady rate, short enough
that the whole test stays under a minute and a half on a card.  `MEASURE_TIMEOUT_S` (90) is the
limit per request; a step that hits it is `TOO_SLOW`, and its figure is a lower bound at best.  A
warm-up request (`WARMUP_PROMPT`, one token) loads the model first, so the load time is reported
separately instead of being counted as speed.

The test page is drawn (`draw_test_page`, `TEST_PAGE_WORDS` 350) and then preprocessed exactly as
a run preprocesses a scan, because the image the model sees decides the image token count.

#### The maths (`domain/performance_estimate.py`, pure)

| Piece | Meaning |
| --- | --- |
| `ModelSpeed(read_tps, write_tps, image_tokens)` | one model on one machine: request tokens per second, answer tokens per second, tokens of one page image |
| `TimeRange(low_s, high_s, typical_s)` | a duration as a range; `scaled` and `__add__` keep the three figures together |
| `AnswerLength(low, typical, high)` | how long a model's answers are: per 100 words for a writing model, per page for a reading one |
| `ExchangeCost(requests_per_page, answer_tokens_per_100_words, text_copies)` | what an exchange costs per step; `DEFAULT_EXCHANGE_COST` holds exchange `T`'s provisional figures (① 1 copy, ② 2, ③ 1) |
| `proofreading_time(speed, words, cost, steps, *, answer=None)` | a proofreading run: the text times the copies of the steps that run, plus their answers |
| `band(seconds_per_page)` | `SpeedBand`: `FAST` up to `FAST_LIMIT_S` (30 s), `OK` up to `OK_LIMIT_S` (90), `SLOW` up to `SLOW_LIMIT_S` (300), else `VERY_SLOW` |
| `machine_factor(measured, reference)` | how much slower this computer is than the reference machine; `None` when either figure is missing, so "unknown" is never read as "the same" |
| `median_factor`, `percentile` | the middle of several models' factors; the 10th and 90th percentile of a set of figures |
| `MachineFactors(writing, reading)` | the two factors, with `for_category` picking one: a card can be better at reading a page than at writing an answer |
| `expected_speed`, `expected_band` | a model's reference speed carried to this computer by the factor, and the band of the middle of the resulting range |
| `SpeedSource` | `MEASURED` (this model, this computer) or `EXPECTED` (from the reference machine) |

The constants that stand in for a measurement, each with where its value comes from:

| Constant | Value | From |
| --- | --- | --- |
| `TYPICAL_PAGE_WORDS` | 250 | a handwritten A4 page of an essay holds 200 to 300 words |
| `CHARS_PER_WORD` | 6.0 | German prose is the longest of the three languages (about 6.2), English the shortest (about 5.5) |
| `DEFAULT_CHARS_PER_TOKEN` | 4.0 | between the window budget's cautious 3 and the 4.3 to 5 measured on German prose at page length |
| `DEFAULT_IMAGE_TOKENS` | 1500 | a full A4 page at 300 DPI; how many tokens a vision model makes of it depends on its own encoder |
| `TEXT_MODE_ANSWER` | 120 / 141 / 151 | `gemma4:e2b` over 9 texts on 2026-11-13; text mode answers with about the text's own length, so one figure serves every model |
| `CONTENT_REVIEW_ANSWER` | 80 / 120 / 200 | a review answers with a short assessment; deliberately wide |
| `DEFAULT_READING_ANSWER` | 350 / 600 / 1100 | a page of about 250 words plus the markup a reading model adds |

A model without an `answer_length` gets the median over the catalogued models of its kind, not a
constant (`default_answer_length`).

#### The store (`infrastructure/speed_store.py`)

Two files, both `SCHEMA` 1, both keyed by what makes a figure go out of date:

| Store | Key | Holds |
| --- | --- | --- |
| `SpeedStore` | `SpeedKey`: server, tag, digest, Ollama version, graphics card | the test's result per step, with its outcome and message |
| `SampleStore` | `SampleKey`: server, tag, step, mode | the last `KEEP_SAMPLES` (20) samples of real runs |

The mode is part of the sample key because it decides how long the answer is: `exchange` is the
three steps' answers together, `text` is step ① alone, which is about the text's length.  Mixing
the two would make the answer length meaningless.  Samples of the old one-request review are
still in the file under `review`; nothing reads them any more.

Both files are read once per change: the parsed content is kept with the file's modification
time, size and inode, so a model list that asks for fifty tags does not parse them fifty times
and a write by the run's worker is still seen.  A file with another schema is read as empty, but
it is moved to `<name>.bak` before this version writes over it, so an older app never destroys
what a newer one measured.  Server addresses are normalised on write and on read.

`RunStatsCollector` (`infrastructure/run_stats.py`) writes the samples: the pipeline hands it to
the Ollama client as `stats_sink`, so every request of a run is seen.  It sums the requests of one
page or one document into one `RunSample`, and counts a load only from `LOAD_THRESHOLD_S` (1 s) up,
so a model that was in memory does not look like one that had to be loaded.  Collecting statistics
never fails a document: a sink that cannot write (full or read-only disk) is logged and skipped.
A step that failed, or whose answer came back without a review (`CorrectionOutcome.fell_back`: a
report of step ② or ③ failed, so the answer lacks part of what was asked), is discarded: the
requests of the failed attempt would be counted into the answer length of the mode that finally
answered.

#### Real runs win over the test

`speed_summary` prefers the samples to the stored test, per step and per model:

- from `MIN_ANSWER_SAMPLES` (3) samples the run figures replace the catalog's answer length: they
  are this class's error level with this model and these instructions, which no test can know.
- from `PERCENTILE_SAMPLES` (10) the range is the 10th to the 90th percentile, so one runaway
  answer no longer widens it.
- a step whose samples are missing falls back to the test, and a step with neither keeps its row
  in `SpeedSummary.unmeasured`, with the band the catalog expects here.  `class_set` is `None`
  while any step is unmeasured: a sum that left one out would promise a time that is too short.

`SpeedSummary.sample_count` and `sample_pages` say which of the two a row rests on, and the panel
says it in words ("Based on your last 12 proofread documents").

#### The time left

`RunPace` (`gui/_run_status.py`) starts from the store's seconds per page.  After the first file
the run's own pace takes over, blended with the estimate for the first `BLEND_FILES` (3) files: one
fast file is not a pace.  `pages_left` counts the pages of the files that are not done yet, from
the run's page counts when they are known and from the average of the files done so far otherwise.
Nothing known at all gives no figure, which is better than a guess the teacher would plan around.

#### What is still open

Four Ollama behaviours the test relies on, none of them measured yet:

1. Does `prompt_eval_count` / `prompt_eval_duration` leave out a reused prompt start?  Send the
   same request twice.  The answer decides whether the warm-up has to differ from the measuring
   request.
2. Is the time spent encoding the image inside `prompt_eval_duration`, or only in
   `total_duration`?  Compare `total - load - prompt_eval - eval`.
3. Is the writing rate of a 200-token answer within 10 % of a full answer's?
4. Is `size_vram` 0 when Ollama is forced onto the processor (`num_gpu: 0`)?

And the figures themselves: only `qwen3:4b`, `gemma4:e4b`, `gemma4:12b` and `gpt-oss:20b` have a
`reference_speed`, and none of them has a `read_tps`.  A reading model therefore gets no expected
band, and `MachineFactors.reading` is `None` in practice until the reading models are measured on
the reference machine.  `tools/ocr_page_tokens.py` measures them, and
`report --performance --catalog-lines` prints the `answer_length` lines for the writing models.

### Who owns what (hardware detection and recommendations)

| Piece | Where | Rule |
|---|---|---|
| Decisions (best model, suitability, hardware profile, `rate_model`) | `domain/model_catalog.py` | Pure functions, no I/O — table-test them. |
| Caches (local hardware probe, model classification) | `application/model_recommendations.py` | One owner. Call `on_ollama_models_changed(url)` after every pull; it evicts the model list *and* the classifications. Only definite classifications are cached. |
| Texts a teacher reads | `gui/_model_wording.py` (model wording), `teachers_teammate/texts/` (everything else) | Qt-free; widgets never build these sentences themselves. See "Where text lives" above. |
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
list in Settings → Privacy and "Ask again" all use it.  A record without a summary is put into
words by `domain/data_location.py:describe_fingerprint()`.

The fingerprint is versioned (`v2|` …, with the data region), so an answer whose fingerprint has
another version does not match and the user is asked again.  Parts are separated by `|` and their
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
  and the anonymized raw text.  Its `stored` argument is the file's `DocumentStateView.sent`
  (see *How a stored result was sent*), so the dialog can put what is already stored next to
  what the next run would send.

`gui/_what_is_sent_dialog.py` only presents that report: one `_SentSection` per step (title
and badge, the names note, the text with placeholders highlighted by
`highlighted_html()`, the instructions in a collapsed block), a *Personal information* tab
built from `DiffWidget`, and — for a local run — `Everything stays on this computer.`  The
main window works the report out on a background task (`_sent_texts_task`; a small "Working out what is sent…"
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
stage line, how the stored result was sent (`_ROLE_SENT`) and the cell's note with blank
lines, so none replaces another.  `_refresh_cell_tooltips()` recomposes every proofreading
and content-review cell after the tooltips change and at the end of `set_queue()`.

### How a stored result was sent

The tooltip and the dialog answer two different questions: *where would the text go now*
(`DataFlow`, from the current settings) and *where did the stored result really go*
(`SentRecord`, from the run that made it).  The second one has to survive a settings change,
so it is stored with the result:

- `domain/data_location.py:SentRecord` holds the **inputs** of the classification, not the
  verdict: `provider`, `model`, `endpoint` (the Ollama host; empty for a provider, none of
  which has a base-URL setting today), `names_hidden` (the number of placeholders), `hiding_on` (whether hiding was switched on,
  which tells "nothing found to hide" from "names as written") and `privacy` (the fingerprint
  of the anonymizer settings).  A changed rule therefore changes
  the wording, not the stored data.  `from_dict()` returns `None` for anything that is not
  one of ours — including a record without a provider, which would otherwise read as an
  unnamed online service.
- `SentStatus` pairs the record with `other_privacy_settings`, the comparison with today's
  `privacy_fingerprint(config)`.  The comparison is by settings, not by text: working out the
  text would need a spaCy pass per document.
- `cache_service.py:sent_record(config, stage, *, names_hidden)` builds it (it holds the
  `Config`); `application/data_flow.py:sent_location(record)` classifies it through the same
  `llm_location()` the box uses, so the two cannot disagree.
- `DocumentState` keeps `correction_sent_text`, `correction_sent_map`, `correction_sent` and
  `evaluation_sent`; `DocumentStateView.sent` exposes them keyed by stage name.
  `ProcessingApplicationService._sent_verdict()` adds the comparison in `load_views()`,
  `load_views_reconciled()` and `load_view()` — the state repository does not know the
  settings.  The current fingerprint is computed lazily, only for a view that has a record.
  `sent_location()` writes a bare IPv6 host (`::1`) in brackets before it asks `llm_location()`.
- The record belongs to its result: every path that clears a result clears its record too
  (`record_ocr_result`, `invalidate_from_stage`, `record_correction_result` for the review).
  The teacher's edits never touch it: they go to `TeacherWorkStore`.  `load_valid()` drops sent fields
  whose result is not stored (`_without_orphan_sent`) and keeps the recognised text, so a
  stray record never costs a new reading; `DocumentStateView.sent` only lists stages that are
  done, so a stage `load_views_reconciled()` marks stale shows no line.
- The wording lives in `gui/_user_text.py` (`_how_sent` builds the phrase once; `sent_lines`
  and `stored_result_line` frame it for the tooltip and the dialog);
  `GUI` writes the cells' lines from `view.sent` after every load, including `_on_file_done`; the dialog shows one line per stored result in a grey block at the
  top of the *What is sent* tab (`stored_lines()`, `objectName="stored_results"`).

### Stopping a run and run outcomes

A run has two stop events, both owned by `OCRWorker` and set through `request_stop(now=...)`:
`stop_after_file_event` ("Stop": the file being processed finishes completely, no new file
starts) and `stop_event` ("Stop now": also cancels the Ollama request being made and stops
the file at its next step).  The Stop button is two-step (`_StopStage` in `_main_window.py`).

The result of a run is `domain/run_summary.py:RunSummary`: `succeeded`, `failed`, `skipped`,
`stopped` (interrupted by Stop now), `not_started` and `error` (the run could not start).
`status` is failed > stopped > done, and `exit_code` is `1` only for failed.  `run_files()`
(`workflow/coordinator.py`), `OCRPipeline.run()` and `ProcessingApplicationService.run_selected()`
return it; `OCRWorker.run_finished` carries it to the GUI, and `gui/_run_status.py:describe_run`
holds all the wording (progress text, tray message).  A stopped file is neither a success nor
a failure, like a skipped one: `ProcessingResult.stopped` -> `FileDoneResult.stopped` ->
`FileDoneEvent.stopped`, shown as a grey row with "stopped" in the cut-short step.

Rules that keep a stop honest:

* **A finished step is never thrown away.**  Each step stores its result as soon as it has
  it, so a stopped file keeps it and the next run reuses it.  A request cut off by the stop is
  never stored as a result: `RunStoppedError` leaves the step before anything is recorded.
* **The event is the truth, `RunStoppedError` is the fast exit.**  It is not an `OCRError`
  on purpose.  The Ollama wrappers in `_llm_invoke` re-raise it instead of degrading it to a
  warning (any other error still becomes one, and is a failure even when Stop now was pressed
  at the same moment); a new wrapper that catches every exception must do the same.
  `FileProcessor` checks the event between steps (`_stopping()`) and **before** a slow
  anonymization pass, and the steps raise `_stopped_abort(message, stage, ...)` themselves.
  The abort carries the stage (`ProcessingResult.stopped_stage`, one of `ocr`, `correction`,
  `evaluation`), so the results table marks the cut-short cell even for the review, which
  has no "running" mark; it also carries the stored correction's review and warning.
* **Without a later step nothing is "stopped".**  A recognition-only run whose text is stored
  when the stop arrives is a success.
* **Cancelling an Ollama request.**  `OllamaClient(url, cancel_event=...)` makes the waiting
  `_consume_iterator_with_first_token_timeout` poll the event every 0.2 s (`_next_item`); on
  cancel the reader thread closes the stream, and `on_abandon` (`_cut_connection`) closes
  the request's own `ollama.Client`, which also ends a request that has not produced a chunk
  yet (model loading, a long prompt).  An answer already in the queue is handed over first
  and never thrown away.  The pipeline
  passes its stop event to the shared client, and to the built-in reader, which checks it before
  every line (`RunStoppedError`) and so stops within a line.
* **Closing the window** (`MainWindow.closeEvent`): a spontaneous close during a run asks first
  (`_confirm_close_during_run`; File → Quit asks the same through `_on_quit_requested`), then
  `request_stop(now=True)`, disconnects the worker's signals (no dialog on a hidden window) and
  waits at most `CLOSE_GRACE_MS` (3 s).  A worker still running after that is detached
  (`_worker.detach_running_worker`) and `_app_bootstrap.finish_process` ends the process with
  `os._exit` instead of destroying a running `QThread` (which aborts).  The Benchmark window
  does the same after its 2 s wait.  `StateRepository.save` writes a temporary file and swaps
  it in (`os.replace`), because the process may now end during a save.

### Keeping the computer awake

`infrastructure/power.py` implements the `SleepBlocker` interface (`interfaces.py`) for each
platform without extra packages; `hold(reason)` is a context manager that yields whether the
system agreed and never raises.

| Platform | Mechanism | Ends if the app dies |
|---|---|---|
| Windows | `PowerCreateRequest` + `PowerSetRequest(SystemRequired)` (shown by `powercfg /requests`); fallback `SetThreadExecutionState` on the same thread | yes (handle closes) |
| macOS | `caffeinate -i -w <pid>` | yes (`-w`) |
| Linux | `systemd-inhibit --what=idle:sleep … tail --pid=<pid> -f /dev/null` | yes (`tail --pid`) |
| WSL / other | `NullSleepBlocker` | n/a |

Only idle sleep of the system is requested; the screen may turn off.  On Linux the lock is
`idle:sleep` in block mode, so logind also refuses the menu's Suspend while it is held.  The
Linux command is `sh -c 'echo ready; exec tail --pid=…'`: `systemd-inhibit` starts it only after
the lock was granted, so the printed line (`ready_line`) is what makes `held` true — a refusal,
however slow, ends the helper without it.  macOS has no such signal and relies on `caffeinate`
exiting at once when it refuses.  `SleepBlocker.available` is `False` for the null blocker, so
`KeepAwakeState.available` tells "nothing was requested" (WSL) from "the system refused".  The only place it is held
is `ProcessingApplicationService.run_selected()`, around the whole `pipeline.run()` (GUI and
CLI both go through it), so it is released on errors and on Stop alike.  `Config.keep_awake`
(default on; `--allow-sleep` / `--keep-awake`; Settings → General) switches it; it is in no
cache key.  The service reports a `KeepAwakeState` (held or refused, on battery or not,
`psutil.sensors_battery()`) to the banner and to `ProgressCallbacks.on_keep_awake`, which the
main window shows under the progress bar.  Tests never block real sleep: `tests/conftest.py`
replaces `power.default_sleep_blocker` with the null blocker; a test that cares passes its own
`SleepBlocker` to the service.  Manual checks (not automatable): `powercfg /requests` on
Windows (Modern Standby may need `PowerRequestExecutionRequired` too), `pmset -g assertions`
on macOS, `systemd-inhibit --list` on Linux.

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

### Request profiles: temperatures and prompts per model

The proofreading request is not the same for every model. `RequestProfile`
(`domain/model_catalog.py`) holds what differs; the catalog carries one per model and
`request_profile(tag, provider)` resolves it — `DEFAULT_REQUEST_PROFILE` for a model it does not
know. A knob enters only with a measured reason (see `tools/model_eval/README.md`), because the
request the app sends is what an evaluation run measures.

- **One `StepProfile` per step** (① correct, ② explain, ③ unclear): `temperature`, `think`,
  `options` and, on step ① only, `prompt`.
- **Temperatures.** Step ① rewrites the text, so its value is measured: `STEP_ONE_TEMPERATURES`
  (0.05 for the models that get worse with every step up), else `STEP_ONE_DEFAULT_TEMPERATURE`
  (0.4). Steps ② and ③ and the content review answer a small object and run at the value of the
  model's card, `MAKER_TEMPERATURES`, else `DEFAULT_CORRECTION_TEMPERATURE` (0.2) or
  `DEFAULT_REVIEW_TEMPERATURE` (0.7). An online provider gets 0.2 in every step; text recognition
  is fixed at `OCR_TEMPERATURE` (0.0). Teachers set none. Read the tables only through
  `request_profile()` / `review_temperature()`.
- **Thinking.** `think=False` for every model, except a family in `THINK_LEVELS` that ignores the
  flag (`gpt-oss` is asked for `"low"`).
- **Sampling values** (`options`, names from `SAMPLING_OPTIONS`) are empty by default: the model's
  own tag (its Modelfile) decides top-k, top-p and the penalties.
- **Prompt variants.** `STEP_ONE_PROMPTS` names the step-① variant (`CORRECT_VARIANTS` in
  `domain/correction_prompts.py`) a model is sent; empty until a measurement on both test sets
  adopts one. A variant that a measurement used never changes its text — another text is another
  id — because the cache key hashes it (`prompt_identity()`).
- **One object, three readers.** The corrector, the *What is sent for this file* dialog and the
  correction cache key (`RequestProfile.fingerprint()`) read the same profile, so the dialog shows
  the request that is sent and a profile change re-runs only that model's corrections.

Every step request goes through `infrastructure/proofreading/runner.py:StepRunner`: a cut-off
answer is asked again (up to `cut_off_tries`), an answer that misses the schema gets one repair
request, and a cut-off repair answer is not retried, because the repair request is the longest of
all.

## Text recognition engines

Two engines read pages, both on a computer the user controls (`LOCAL_OCR_ENGINES`; online
services are refused by `online_ocr_reason`, because a page cannot be anonymised before it is
read):

| Id | Name | What it is |
|---|---|---|
| `ollama` (default) | Ollama (AI model) | A vision model through the Ollama server; best for handwriting |
| `ppocr` | Built-in reader (PP-OCR) | PP-OCRv6 *small* detection and recognition, run by `cv2.dnn`; no server |

The reader (`infrastructure/ppocr/`) reads a page in five steps (`engine.py:PpOcrProcessor`):

1. `image_io.read_image` — Unicode-safe load (OpenCV applies the EXIF turn).
2. `detect.detect` — the page is scaled so its long side is at most `DetectorSettings.max_side`
   (1600 px, never up, sides rounded to multiples of 32), the DB detector returns a probability
   map, and `boxes_from_map` turns it into rotated boxes: threshold `0.2`, boxes whose mean
   probability is below `0.45` or whose short side is under 3 map pixels are dropped, the rest
   grow by Paddle's *unclip* (`2 x area x 1.4 / perimeter`).  These four numbers are the ones in
   the pinned model's `inference.yml`; `tests/integration/test_ppocr_models.py` compares them
   with the file, so a new revision that changes them fails there.
3. **The boxes are lengthened** by `line_end_pad` = 0.6 x the line height (0.3 at each end).
   Paddle's boxes are tight: on a drawn A4 page at a detector size of 1600 px only 1 line of 8
   kept its full stops and last letters; with lengthened boxes 8 of 8 did, at 960, 1600 and
   2000 px alike.  `max_side` and `line_end_pad` are provisional; the evaluation tool measures
   them on real handwriting.
4. `reading_order.reading_order` — rows by centre height (a box joins the row when its centre is
   within half the median line height), each row left to right.  One column only.
5. Per box: check the stop event, `recognise.crop_line` (perspective warp to an upright strip;
   a strip 1.5 x taller than wide is turned 90 degrees), `recogniser_input` (height 48, width
   at least 320 and at most 3200, `(x/255 - 0.5)/0.5`), one forward pass, `ctc_decode`
   (classes are `["<blank>", *character_dict, " "]` = 18,710, the model's output size).  Words
   of a row are joined with a space, rows with a newline; empty lines are dropped.
   Recognition is one line at a time: batching measured slower.

**Why `cv2.dnn`, and the `OnnxNet` seam.**  OpenCV is already part of the app, so the reader
adds no package.  ONNX Runtime was measured too: the detector runs a full A4 page in 2.1 s
instead of 2.4 s, at 78 MB installed and no Intel-Mac build.  The reader depends on the
one-method `OnnxNet` protocol (`infrastructure/onnx/runner.py`) only, so a model that
`cv2.dnn` runs differently on real pages is a new class and a dependency, not a rewrite.
`CvDnnNet` loads from **bytes**, not from the path: OpenCV's path functions fail on a Windows
user folder with an umlaut (a test loads from `Jörg Müller`).  PyYAML reads the recogniser's
character list from `inference.yml` (a base dependency).

**`READER_VERSION`** (`domain/local_models.py`) is bumped whenever detection, recognition,
reading order or joining changes what the reader returns; it is part of the cache identity, so
stored pages are read again.

**Adding an engine** is in {doc}`extension_points` ("New OCR engine").  The settings file of an older
version may name `tesseract` or `paddleocr`: `config.normalize_ocr_engine` maps them to `ppocr`
(`RETIRED_OCR_ENGINES`, a notice names the old engine and where to download the reader), and an
online engine falls back to the default as before.

## Image preparation

Teachers see two choices: **Automatic (recommended)** (`preprocess_method = "auto"`, the default)
and **Original page** (`"none"`).  `infrastructure/preprocessing/` holds all of it:

- `steps.py` — `Step` (a `name` and `apply(image, ctx) -> StepResult`), `StepContext` (the
  source's DPI, whether the file carries camera tags, and a dict of measurements one step
  leaves for the later ones) and `StepPipeline`, which implements `ImagePreprocessor`: load once
  (`image_io.read_image`, which applies the EXIF rotation tag), run the steps, list the names of
  the steps that *acted*, write `<stem>_preprocessed.png`, put the DPI back.  With `save_steps`
  every acting step also leaves a JPEG snapshot.  An **empty list** is "Original page": the file
  is returned untouched — except a photo whose EXIF orientation is not 1, which is decoded
  (that applies the turn) and written as a PNG; the listed name is then `turned by tag`.
- `auto.py` — the automatic preparation and every threshold as a named constant.  `auto_steps(engine,
  store)` lists, in order: `measure` (never acts; leaves the page outline, the unevenness of the
  light and the *photo?* verdict), `page` (corners → perspective warp, only for an outline with
  four straight edges, a sheet's aspect and a contrast against its surroundings), `flatten`
  (UVDoc, when `page` did not act and the image is a photo, or the outline is curved), `turn`
  (doc_ori, confidence ≥ 0.9), `size` (long side ≤ 3508), `light` (each channel divided by the
  background: dilate, median, ¼ size), `deskew` (0.3° ≤ tilt ≤ 10°) and the output (Ollama
  gray, the built-in reader colour).  There is no denoise and no binarising.  A photo is: camera
  tags in the file, **or** a page-like region (contrast against its surroundings) with the wrong
  shape, **or** uneven light.  A scan with a framed table has no contrast between inside and
  outside the frame, so it is no page and no photo.
- `page_models.py` — `PageModels`: the two `cv2.dnn` wrappers behind the `OnnxNet` seam
  (resize and normalisation from each model's `inference.yml`).  A model step without its files
  returns `acted=False`, reason "page models not downloaded", and `PageModels.note_missing()`
  logs that once per run (info), not per page.  A model that fails at run time never fails the
  page: the step declines and the failure is logged.
- `identity.py` — `AUTO_VERSION`, `auto_output(engine)`, `available_page_steps(store)` and
  `auto_identity(engine, store)`, without importing OpenCV (the cache key needs them for every
  page).
- `recipes.py` — `steps_for(ocr, store)`: `none` → `[]`, anything else → `auto_steps(ocr.engine,
  store)`.  `stage_builder.default_build_preprocessor(tmp_dir, save_steps, ocr, store)` is
  `StepPipeline(steps_for(ocr, store or ModelStore()), …)`; the factory, `preprocess_preview`, the
  speed probe and `tools/ocr_page_tokens.py` all pass the model store through.

**The gates are conservative on purpose.**  Every step acts only where its measurement says so
and leaves the page alone otherwise, so a clean scan passes unchanged; a thing the steps get
wrong costs less than a thing they do to a page that did not need it.  The thresholds are first
values; the evaluation tool's stages tune them (the numbers change constants or steps and bump
`AUTO_VERSION`).

**Page models.**  `DOC_ORI` (PP-LCNet_x1_0_doc_ori) and `UVDOC` are `LocalModel`s in
`domain/local_models.py` (group `PAGE_MODELS`, 39 MB, Apache-2.0, PaddlePaddle), downloaded only
on request: **Download…** on Scan quality, the **Page models** box on Add-ons → Reading models
(`page_models:all`, between the reader and the Ollama pulls in `InstallRound`), or
`--download-page-models`.  Missing page models are **not** an `IssueKind`: a run never waits for
them; the CLI prints one notice line (`page_models_notice_for_run`).  `UVDoc` answers an image at
the size it was given, so the page goes to it scaled to at most `FLATTEN_MAX_SIDE` (2048).

**Identity.**  `cache_service._preprocess_fingerprint(ocr, source, store)` is the method; for
`auto` also `v<AUTO_VERSION>`, `colour` / `gray`, and `+turn` / `+flatten` for the page models
that are downloaded; then the PDF DPI and text-layer entries.  Downloading the page models, a
new `AUTO_VERSION` or another engine's output re-reads pages **once**, and that is intended.
Bump `AUTO_VERSION` whenever a step, a threshold or a page model revision changes.  The model
store reaches the key through `CacheReconciliationService(model_store=…)`,
`ApplicationCommands(model_store=…)` and `compute_ocr_config_hash(…, store)`; with `None` it is
the default folder (the tests point that at an empty temporary one).

**How to change it.**  A new step is a `Step` in `auto_steps`, with its gate and a named
constant, a `STEP_LABELS` entry in `gui/_user_text.py` if it is worth a word in the preview
caption, and an `AUTO_VERSION` bump.  It gets **no switch of its own**.  A step's `reason` text
("tilt 1.4°") goes to the debug log only.

**Tests.**  `test_preprocessing_auto.py` (drawn pictures: photo → `page` acts; framed table →
it does not; flat scan → `light` does not act; 3° corrected, 15° left; 5000 px → 3508; colour
vs gray; fake page models for turn / flatten and for missing files),
`test_preprocessing_auto_golden.py` (a digest of the automatic preparation without page models
on the sample PNG and PDF page plus three drawn pictures; a change in the pixels without an
`AUTO_VERSION` bump fails — the digests belong to the pinned OpenCV on Linux, so after an
OpenCV update look at the pictures, then regenerate from the failure messages) and
`test_page_models.py` (`needs_page_models`, the real pinned files as Bazel `http_file`s: a page
turned 90/180/270° is restored, a curved page's lines get straighter).

## Local model files

`domain/local_models.py` is the catalogue of model files the app runs itself (not through
Ollama): a `LocalModel` has a Hugging Face repo, a **pinned commit**, and per file its size and
SHA-256.  `READER_MODELS` are the reader's two sets (31,191,354 bytes, "31 MB").

`infrastructure/onnx/store.py:ModelStore` keeps them under
`<user data>/models/<repo name>/<revision>/` (`TEACHERS_TEAMMATE_MODELS_DIR` moves the root;
`conftest.py` points it at an empty temporary folder for every test).

- **The installed app carries the reader and the page models.**  `tools/build/fetch_models.py`
  fetches `READER_MODELS` and `PAGE_MODELS` into `teachers_teammate/_models/` (git-ignored) with
  the same pins and the same SHA-256 check, through `ModelStore` itself; the spec adds the folder
  and refuses to build without it.  `ModelStore(bundled_root=…)` reads the bundle first — the
  default is `sys._MEIPASS/teachers_teammate/_models`, so only a frozen build has one — and the
  download folder second, so a damaged bundled file falls back to a download instead of being
  read.  A bundled model counts as installed: nothing is downloaded and nothing is written next
  to the program files.  The smoke test's `models` check (frozen only) proves the files are
  complete, and the Add-ons rows say *included* rather than *downloaded*
  (`get_installation_status()` reports `bundled:reader:ppocr` / `bundled:page_models:all`).
- **A download happens only when the teacher asks**, and only for what the app does not carry:
  the reader's Download button, Add-ons → Reading models, the Setup Assistant's *Set up*, or
  `--download-reader`.  Never at start-up, never silently, and nothing about the user is sent
  (User-Agent `teachers-teammate/<version>`).
- Each file streams into `<name>.part`, hashing as it is written; size and SHA-256 must match
  before `os.replace` gives it its name, and a `.verified` marker is written once all files of a
  model are in.  `is_installed` checks the marker and the sizes only (no hashing), so the
  readiness line can ask every few seconds.  Cancel is checked per chunk and removes the part
  file; the streamed response is closed on every way out.
- **One download at a time per process** (`_DOWNLOAD_LOCK`): two would write the same `.part`
  file.  What is missing is worked out inside the lock, so a download that waited finds the
  files in place and fetches nothing; it can still be cancelled while it waits.
- A run reads the store its service checks (`ProcessingApplicationService(model_store=)` →
  `PipelineDependencies.model_store` / `run_ocr(model_store=)` → `StageBuilder.model_store`), so
  the readiness check and the run never look at different folders.
- `reader_refusal_for_run` (CLI) refuses only a run that has a file to read: a folder of text
  files, or of files whose text is stored, starts without the model files, as in the main window.
- Errors are sentences a teacher or an IT department can act on (`ModelDownloadError`): the
  host that could not be reached (`huggingface.co`), a damaged download, no space, a folder that
  cannot be written.
- **Tests never use the network.**  `MODULE.bazel` fetches the four pinned files as `http_file`
  repos (sha256 included) for the targets that run the real models; `conftest.reader_store()`
  links them into a store, `fake_reader_store()` makes empty files of the pinned sizes for tests
  that only ask whether the reader is "installed".
- **Pinning a new revision**: change `revision` and the per-file size/sha256 in
  `domain/local_models.py` *and* the matching `http_file` urls and hashes in `MODULE.bazel`,
  then run `//tests:test_ppocr_models` (it checks the numbers in the new `inference.yml`) and
  bump `READER_VERSION` if the output changes.  The models are Apache-2.0 (PaddlePaddle) and are
  shipped inside the bundle, so they are `bundle` rows of `third_party_licenses/inventory.csv`
  and their attribution and license text live in
  `third_party_licenses/curated/paddlepaddle-models/` — no wheel carries them.  The **page
  models** of the automatic image preparation (`PAGE_MODELS`) are pinned the
  same way; their four `http_file`s are `doc_ori_*` and `uvdoc_*`, their test helpers
  `conftest.page_models_store()` and `fake_page_models_store()`, and a new revision means a
  bump of `AUTO_VERSION` (`preprocessing/identity.py`) and a look at `test_page_models.py`.

## Storage, hashing & caching

Persistent data lives in three per-user places, resolved by `infrastructure/storage_root.py`:

| Root | Function | Holds | Linux / Windows / macOS |
|---|---|---|---|
| cache (**storage root**) | `resolve_storage_root()` | results, previews, the cache of every run; may be deleted by cleanup tools | `~/.cache/teachers_teammate` / `%LOCALAPPDATA%\teachers_teammate\Cache` / `~/Library/Caches/teachers_teammate` |
| config | `resolve_config_root()` | `ocr.toml` | `~/.config/teachers_teammate` / `%LOCALAPPDATA%\teachers_teammate` / `~/Library/Application Support/teachers_teammate` |
| data | `resolve_data_root()` | `benchmark/`, `speed/`, `work/` (next to `packages/` and `models/`, which have their own lookup) | `~/.local/share/teachers_teammate` / `%LOCALAPPDATA%\teachers_teammate` / `~/Library/Application Support/teachers_teammate` |

The `TEACHERS_TEAMMATE_TMPDIR` environment variable, when set, replaces all three with that one
folder (tests, portable use). The rule: what a teacher cannot rebuild (settings, benchmark
history, speed measurements, their own corrections) never goes into the cache.

**Moving from the cache.** Versions up to 0.5.6 kept `ocr.toml`, `benchmark/` and `speed/` in the
cache folder. `resolve_config_path(None)` and `resolve_data_folder(name)` move them to the new
place the first time they are asked for (`migrate_once`: only when the new one is missing; when
both exist the new one wins and the old one stays; a move that fails is logged and the old file
is still read). On Windows the config and data folders are the same, so the uninstaller's
settings switch removes `ocr.toml`, `benchmark\`, `speed\` and `work\` there.

```
<storage_root>/                                    # the cache
├── artifacts/
│   └── <output_dir_key>/                          # key = sha256(output_dir.resolve())[:16]
│       ├── <stem>_<key>_preprocessed.png          # prepared page 1 (the preview image)
│       ├── <stem>_<key>_p<N>.png                  # prepared pages 2..N, listed in DocumentState.page_images
│       ├── <stem>_<key>_original.<ext>            # page before preprocessing (only if steps changed it)
│       └── state/<stem>_<key>.json                # CACHE: one DocumentState per (output_dir, path)
<config_root>/ocr.toml                             # configuration (the pipeline config)
<data_root>/
├── speed/                                         # measured speeds (samples.json, measurements.json)
├── work/
│   └── <sha256>.json                              # WORK: one TeacherWork per essay, keyed by its content
└── benchmark/
    └── <document_hash>/                           # compute_file_hash(document)
        ├── document.json                          # {document_hash, display_name, last_path}
        ├── <UTC-ts>-<ocr_cfg_hash[:8]>.json       # StoredRun (self-describing)
        └── images/<UTC-ts>-<ocr_cfg_hash[:8]>.png
```

### Hashing scheme

- `compute_file_hash(path)` (`state_repository.py`) — SHA-256 of the file
  contents; the **identity of a document** regardless of its path or name.
- `compute_ocr_config_hash(config, ocr_prompt)` (`workflow/cache_service.py`) — a
  fingerprint of the OCR stage configuration (engine, model, preprocess
  method — for `auto` with its version, output and available page models —, language, prompt,
  temperature). The engine is hashed twice (the shape of the hash is kept so cached OCR results stay
  valid). For the built-in reader (`ppocr`) the model slot is `reader_fingerprint()`
  (`PP-OCRv6_small@<det rev>+<rec rev>/v<READER_VERSION>`) and the language, prompt and
  temperature slots are constants: the reader reads every language with one model, so a
  changed language or Ollama model never re-reads a page, while a new model revision or
  `READER_VERSION` does. A test pins an Ollama key, so the Ollama shape cannot drift.
  The models themselves live in the user data folder (`models/`), not in the cache. Used by the cache to detect stale stages and by the benchmark store to label
  runs.
- `compute_correction_config_hash` / `compute_eval_config_hash` deliberately leave the
  anonymizer settings out: a text proofread with names hidden is the same text, so
  switching *Remove personal information* on or off does not send it again.  What was sent
  is recorded with the result instead (`SentRecord`, see *How a stored result was sent*),
  and `privacy_fingerprint(config)` — the hash of the anonymizer settings alone, computed
  without importing spaCy — says whether a stored result was made with other settings than
  today's.  The resolved anonymizer settings (primary and secondary spaCy model plus every
  regex pattern, `tag=pattern`) are part of that fingerprint, so a changed pattern shows up
  as "other privacy settings" without a run.  `installed_models` is deliberately not part of
  it.  Removing the anonymizer part changed these two hashes once for everyone who had
  *Remove personal information* on: after that update their cached proofreading and content
  review count as stale and are made again (documents with it off are untouched).
- `compute_cache_key(value)` (`storage_root.py`) — a short (16-char) stable key
  used to map a path (output dir / source file) to an on-disk file name.
- `document_stem(path)` (`storage_root.py`) — `<stem>_<key of the resolved path>`, the name a
  document's stored files carry: its state file and its images.  Two files of one name in
  different folders are two documents and never overwrite each other's pages.

**The state schema.**  `StateRepository._SCHEMA_VERSION` is 3.  A file of another version is
read as missing, so the document is processed again; the bump to 3 also wipes the cache once,
because a review stored in the old shape would otherwise load without explanations and, its key
unchanged, never be made again.  The review's offsets are in the plain text of the stored
`raw_text` (see *Correction review*).

### The teacher's work (`domain/teacher_work.py`, `infrastructure/teacher_work_store.py`)

What the teacher changes and decides lives in a store of its own, apart from the machine results
in the cache.  The cache keeps what the models produced and is never written by an edit; the
window, every output file, *Export*, *Copy* and the next proofreading read the teacher's texts
where they exist.

**One file per essay, keyed by the file's content.**  `TeacherWorkStore` keeps
`work/<sha256>.json` in `resolve_data_folder("work")`; the key is `compute_file_hash` of the
document, the `source_hash` the cache already computes.  One essay has one work wherever its
results go and whatever it is called; a changed file is another essay.  Written atomically
(`infrastructure/atomic_file.py:write_text_atomic`, the temp-file-and-replace the state store
uses too).  **One writer**: the window, through `ApplicationCommands`; runs and the CLI only read.

**Two parts, two validity rules.**

| Part | Holds | Bound to |
|---|---|---|
| transcription | text 2, its notation, `checked` | nothing — it is what the student wrote, checked against the scan |
| corrections | text 5, `checked`, `looked_at`, `kept`, `kept_under` | one machine answer: `bound_to = proofreading_id(source identity, text 4, review)` |

`proofreading_id` hashes the answer itself, not the settings that asked for it.  A new answer —
other settings, a re-run, the same settings after the cache was emptied — has another id, and
the corrections part no longer applies: it is **ignored on read** (never shown, never in an
output) and **replaced on the next write**.  Nothing deletes it eagerly except an explicit
re-run.  The price is deliberate: after an emptied cache the corrections work cannot come back,
because the answer it was written on is gone.  The transcription part — usually the bigger
effort, and the input of the next proofreading — always does.

**Copies only where needed.**  The transcription part exists when the teacher fixed *or* checked
the transcription (*Checked* on a transcription stores a copy, so the check names the exact text
it was given).  The corrections part exists when the text differs from text 4; an edit that
returns to text 4 drops the copy but keeps the part for `checked` and `looked_at`.  An empty part
is removed, an empty work deletes its file.

**The reading is frozen while the teacher has worked on the document.**  A run does not read a
document again whose transcription the teacher fixed or checked, nor one whose corrections they
worked on (text 5, `checked` or `kept`), even when the recognition settings changed (the stored OCR
hash counts as current, `StageHashes.ocr_frozen`).  Re-reading could change the text the
proofreading was made from, and the corrections written on that answer would stop applying; for a
transcription part it would change nothing the teacher sees and cascade into a new proofreading of
the same text.  Looking at items (`looked_at`) freezes nothing.  Only *Re-run Text Recognition*
reads it again, after asking, and then drops both parts.

**The teacher's transcription needs no reading at all.**  When the cache holds no reading (it was
emptied) and the work has a transcription part, `FileProcessor._run_preprocess_and_ocr` prepares
the pages for the scan but does not read them: the teacher's text is stored as the document's
text, with its notation, as if the file carried its own text (a text file, a PDF text layer).  The
file is not skipped when text recognition is switched off either — `check_run_preflight` counts a
file with a transcription part as recognised.  The cache's text 1 is then the teacher's text;
`work_status.transcription_changed` does not say they changed it.  Until a run has rebuilt the
state, the queue and the Check tab show nothing of that document: the pages come from the cache.

**The proofreading remembers its source.**  `DocumentState.correction_source_hash` is
`text_identity` of the transcription the proofreading was made from.  A stored proofreading is
stale when that differs from the current transcription (text 2, else text 1) — whatever the
settings.  It guards against a store emptied on one side only: the teacher's transcription gone
while the cache still holds a proofreading made from it.  `""` (a state written before this
field) means "made from `raw_text`".

**Runs keep worked-on corrections by default.**  A stored proofreading that is stale only by its
settings and whose corrections part applies and is *worked on* (text 5, `checked`, or `kept`)
counts as fresh for every run, the CLI included: no run replaces it unless something explicit
invalidated it.  `looked_at` alone is not worked on — it is cheap to lose.  The window's
run-start question only offers to replace; *Keep my changes* writes `kept_under = expected
correction hash` into each part, so the same settings never ask again.

**One merge point for every reader.**  `current_texts(machine, work)` returns the texts the
document has now — transcription 2 else 1, corrected 5 (where bound) else 4 — plus
`machine_corrected` (text 4) and the `WorkStatus` a reader marks.  `ProcessingApplicationService`
merges it into every `DocumentStateView` (`load_view`, `load_views`, `load_views_reconciled`), and
the pipeline calls the same function.  Queue rows, *Export*, *Copy*, *Save*, *Hide names*, *What
is sent* and `check_document` read the view.  **Nothing outside the cache's own code reads a
`DocumentState` text** for display or output: `tests/unit/test_teacher_work_readers.py` holds the
package to that, and the GUI may not name the type at all.

**One helper for the flags.**  `cache_service.stage_hashes(state, work, expected)` is the only
place that turns a work into the three flags freshness needs: `ocr_frozen` (a transcription part
exists, or a corrections part was worked on), `expected_correction_source` (`text_identity` of the current transcription) and
`correction_kept` (the corrections part applies and was worked on).  The run
(`CacheReconciliationService.prepare`) and the queue (`load_views_reconciled`) both ask there, so
they agree on what is stale and what is kept.  `correction_kept_over_settings(hashes)` is what
the queue's *kept* note and the run-start lookup ask.

**Robust file handling.**  A work with an unknown newer `version` is ignored and never
overwritten, also by the very first write of a session: the store reads the file before it
decides (a write is refused with `WorkTooNewError`).  A file that is not a work file is moved aside
(`<name>.broken-<UTC time>`) and treated as absent; if it cannot be moved, nothing is written over
it.  A file that only cannot be opened right now (another program holds it) is neither moved nor
replaced: a read says "none" for that call, a write raises `OSError`.  Reads and writes of one store
take one lock, so a read never sees a write of the same process half done.

**A refused write is said.**  `edit_transcription`, `edit_corrected_text` and `set_checked` raise
`WorkNotSavedError(name, too_new=…)` when the work cannot be stored; the window shows
`dlg.work_not_saved` ("saved by a newer version of the app", or "could not be saved") and leaves
what was typed in the text view.  `edit_transcription` stores the work first and resets the
cache's proofreading after: a failed write changes nothing, and a failed reset leaves a
proofreading that fails the source guard anyway.  `mark_looked_at` and `keep_teacher_work` only
log: nothing the teacher did is lost.  Work of essays whose files changed or went stays (a few KB
each, `name` inside for people looking at the folder); the uninstaller's settings box removes the
folder.

**One work per content.**  Two files with the same content (a copy in another folder) are one
essay, so they share one work, though each has its own cache and its own answer.  Corrections
written on the answer of one copy do not apply to the other's, and the next write for the other
replaces them (a corrections part is bound to one answer).  The transcription part is shared as it
should be.  This is a known limit of keying by content; the alternative — one part per answer —
was left out because two copies of one essay are rare and the shared transcription is the
effort that matters.

**Who owns which text, after every operation:**

| Operation | Cache (machine) | Transcription part | Corrections part |
|---|---|---|---|
| Run: reading (no part) | text 1 new; proofreading cleared (cascade) | — | stops applying (id gone) |
| Run: reading (part exists) | not read (frozen) | kept | kept |
| Run: reading (cache emptied, transcription part exists) | pages prepared, not read; text 1 = the teacher's | kept | stops applying (id gone) |
| Run: proofreading, fresh or kept | unchanged | — | applies |
| Run: proofreading, new | text 4 + review + source hash | — | stops applying; replaced on the next write |
| Teacher: fix transcription | correction stage reset when it had one | text 2 | dropped |
| Teacher: card action / typing in *Final text* | — | — | text 5, bound |
| Teacher: *Checked* | — | checked (+ copy) **or** | checked, bound |
| Teacher: card shown | — | — | `looked_at`, bound |
| Explicit re-run proofreading | correction reset | — | dropped |
| Explicit re-read | everything reset | dropped | dropped (also when there is no transcription part) |
| Cache emptied | everything gone | kept, used as the proofreading input | kept on disk, applies to nothing |
| `work/` emptied | proofreading made from text 2 is stale by the source guard | — | — |

**Writes while a run is going.**  The run reads a work once, in `prepare`.  The window writes only
for documents the run does not hold (the lock), and a *Keep my changes* answer is written before
the run starts, so no write races a read of the same document.  A card shown while the run holds
the document records nothing (`CheckPage.mark_looked_at` is refused under the lock).

Tests: `tests/unit/test_teacher_work.py` (the model, the merge, the changes),
`tests/unit/test_teacher_work_store.py` (the store), `tests/unit/test_atomic_file.py` (the
write), `tests/unit/test_domain_policy.py` (the freshness flags),
`tests/unit/test_file_processor.py` (the pipeline on the current texts),
`tests/unit/test_service.py` (the merged views, the commands, `worked_on_documents`),
`tests/unit/test_teacher_work_readers.py` (no reader names the cache's type).

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
whichever came first fills the cache for the others.  `FileProcessor` anonymizes the extracted text once, right after recognition, and hands
that result to proofreading and to the `_anonymized.txt` output.

The caches are memory only.  What a run sent is written to disk with its result — the text
and its placeholder map, and only when names were hidden — so the window can show it later
without a spaCy pass (see *How a stored result was sent*).  `clear_anonymizer_cache()` empties
both caches (switching the privacy settings or the language invalidates the entries anyway,
because the settings are part of the key).  The GUI's `OCRWorker` receives the main window's
service, so a run fills the cache that the window's previews and exports read; a worker
created without one builds its own service and shares nothing.

### Hiding names in the Check tab (`domain/anonymization.py`)

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

`gui/check/_masking.py:Masking` holds that state for the Check tab: the mapping, whether the
stand-ins are ready, the failure, and the text they were asked for.  The page keeps the texts
*unmasked* in the `CheckDocument` it was given and asks `Masking.mask(text)` for what it shows,
so switching the setting off shows the stored text again without a reload.  The note above the
text is clickable only while the setting locks the views: a click emits
`display_anonymized_changed(False)`, the **Hide names** box's route, so the window stores the
choice and the Settings → Privacy checkbox follows.  Placeholders are marked as chips:
`gui/_formatted_text.py:chip_placeholders()` wraps them in the rendered Markdown HTML (markup is
skipped, so a stand-in that reads like a tag cannot break the document).  No tooltip ever names
the original.

The marks of the review are carried to the masked text with `Masking.carry(span, text)`, which
goes through the `OffsetMap` of `domain/anonymization.py:mask_with_offsets()` — the same map the
notes are masked with, so a name is hidden in the card too and a mark still sits on the right
words.

The page asks for the stand-ins with `Masking.needs_request(raw_text)` — once per text — and the
window answers through a background task (`_anonymize_task`), which calls
`ProcessingApplicationService.anonymize_preview()` off the GUI thread and therefore reuses the
shared cache above (a run then needs no second spaCy pass, and vice versa).  A result that
belongs to a text the page no longer shows is dropped (`set_anonymization(..., raw_text=…)`),
and a failure keeps the names hidden behind a note instead of showing them.  Changing the
anonymizer settings in the dialog calls `forget_anonymization()`.

The setting itself is a GUI preference, not part of `Config`: the checkbox under
*Settings → Privacy → Personal info: detection* and the eye button both end in
`MainWindow._apply_display_anonymized`,
which stores `privacy/display_anonymized` (`gui/_privacy_settings.py`,
`display_anonymized()` / `set_display_anonymized()`) and calls
`CheckPage.set_display_anonymized()`.  It defaults to **on** (`DISPLAY_ANONYMIZED_DEFAULT`):
only a stored answer shows the names, and without the add-on the checkbox is greyed out and the
setting takes effect once it is installed.  The checkbox is enabled only while
`ProcessingApplicationService.anonymizer_available(language)` holds — spaCy importable and the
model of the document language installed — so a missing add-on greys it out with a tooltip
pointing at Settings → Add-ons instead of failing later.  The answer is cached per document
language and refreshed on `data_flow_changed` (the language may have changed) and after the
privacy add-on is installed.

### Settings window

One modal window (`gui/settings/_window.py:SettingsWindow`) holds everything set up once;
the main window only holds what changes per batch.  The sidebar lists seven pages
(`SettingsPage`): General (`GENERAL`: `keep_awake`, `check_for_updates`), AI models, This computer (`AI_SETUP`: computer profile, local server) and Online services (`ONLINE_SERVICES`: the list of API keys), both drawn indented under AI models because they hold no choice of their own (`SUB_PAGES`), Scan quality, Privacy and Add-ons.  The main window
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
nothing.  Settings that other pages change meanwhile (computer profile, server address) are kept in
the window's working copy, so a page built later
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

**Add-ons shows one status line, not a log.**  `AddonsPage` fills the window
(`SettingsPageBase.fills_window`) instead of scrolling: its model tree takes the free height and
the action row stays at the bottom, so the install button is visible on a laptop.  Above that row
sits one status line, fed by `InstallRound`'s signals and never by the log text: the running step
with its number ("Installing spaCy (step 1 of 2)…"), a percentage for a model pull, the elapsed
time for a pip step (pip reports no progress at all while its output is a pipe), then the last
round's result, a pending restart, or what the detection found.  The log is behind "Show details",
opens by itself on a failure, and "Copy" puts it on the clipboard.  Abort appears only while it
can stop something (`InstallRound.can_abort()`): a download, not a pip install.

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

### Main window: steps, readiness line, fix targets, tools

**Steps.**  `ConfigPanel` has four steps: Documents, Language (with the Level), What should
happen, Results.  Under recognition, personal-info removal, proofreading and review sits a
`UsesLine` (`_data_flow_summary.py`) with the model and the place of the step.  The text comes
from `ProcessingApplicationService.describe_steps(config)` -- every step as if it were ticked,
so an unticked step keeps its (grey) line -- and `short_place` / `uses_line`, which share their
words with the service list on AI models (`PLACE_*`).  The model is not part of
`DataFlow`, and so not part of its fingerprint: a remembered "don't ask again" must survive a
change of model.  The data-flow box (`DataFlowSummary`) keeps only the verdict and the advice;
it shows the ticked part of the same answer (`application/data_flow.py:ticked_steps`), so the
places and regions are asked for once per refresh, not twice.
The review is a child of proofreading: `ConfigPanel.review_will_run()` (ticked **and**
proofreading ticked) is what `to_config()` and `lookup_config()` pass as `evaluation_enabled`,
so the box, the preflight, the Ollama model list and the memory check never count a review the
pipeline would drop (`StageBuilder.build_evaluation`).  The tick itself is kept and saved.

**Requirements and their fixes.**  `check_stage_requirements(config, stage)` (stages `ocr`,
`anonymizer`, `correction`, `evaluation`) returns `RequirementIssue`s that carry an `IssueKind`,
never only a sentence; `check_run_preflight(config, memory=..., recognition=..., fresh=...)` runs
`ocr` first and exposes the first blocker as `RunPreflight.blocker -> (stage, issue)`.
`recognition=False` leaves the engine check out for a run that reads no page
(`needs_text_recognition(files, recognised=...)`: only text files, or text already stored; a PDF
counts as reading, because its text layer is only known by opening it); `fresh=True` asks Ollama
for its models again instead of using the cached list ("Check again", a returning window).
The engine probes (`is_module_importable`, `reader_installed`) are shared with the connection
check, so a package or the reader's models installed from Add-ons are seen without a restart
(`IssueKind.READER_MODEL_MISSING`, fix: the **Reading models** tab of Add-ons with `reader:ppocr` ticked).  A new requirement is therefore: an
`IssueKind`, its check, and **one row in `gui/_fix_targets.py:fix_for`** -- the line for the
readiness line and the buttons (`FixAction`: a Settings page + focus + preselect, or a
`command` such as `retry` / `choose_folder`).  `DependencyGuardDialog(title, issues, actions)`
(the requirement guard when a step is ticked, and the dialog that stops Run) and the readiness
line take their buttons from that one table.  Preselect entries in Add-ons are model tags,
`provider:<id>` and `spacy:<model>`.

**Readiness line** (`gui/_readiness_line.py`).  `readiness(...)` is a pure function: the first
matching row wins (blocker, Results problem, no documents, no files, nothing selected,
"Checking…", ready).  `MainWindow._update_readiness()` is instant (it reads the last answer and
the cheap facts: queue, selection, folder) and runs on every selection change; the answer
comes from `_start_readiness_check()`, a background `check_run_preflight(..., memory=False)` in
a `LatestTask`.  It is started by every settings change (via `data_flow_changed`, debounced by
400 ms), after Settings OK / restore / a finished install or download, at start-up, when the
window is activated while something blocked, after the queue was refreshed (the files decide
whether an engine is needed -- this also covers a Results tick: "Text file, anonymized" needs
name detection) and every 10 s while Ollama is unreachable.  "Check again" and the activation
re-check pass `fresh=True`.  `ConfigPanel.lookup_config()` is recursive like `to_config()`, so
"text files would land in the documents folder" gets the same answer for the line and for Run.  The
line never disables Run: Run checks again (`_check_requirements_then`) and stays the authority.
A model download takes the line over (`show_download`) and hands it back with a new check.

**Tools menu.**  *System Monitor* is `gui/_system_monitor.py:SystemMonitorWindow`, built at
start-up and kept hidden so the chart collects every run; `SystemStatsWidget` polls only while
shown.  It is a **parentless** window of its own, like Benchmark (an owned window would stay on
top of the main window on Windows), so it can stay open and be arranged next to the main window
while a batch runs; `MainWindow.closeEvent` closes it, otherwise an open monitor would keep the
app alive.  *Benchmark…* creates `build_benchmark_window()` in-process (lazy import; the packaged
build lists `teachers_teammate.gui.benchmark` as a hidden import), as a **parentless** window
-- an owned window would stay on top of it on Windows -- and `MainWindow.closeEvent` calls its
`close()`, because only that runs the window's own `closeEvent` that stops its worker.  The
window is kept between uses: when it is shown again after a close, `reload_settings()` re-reads the
saved server and "My computer" choice and asks for the models again (closing retired a running
fetch).  A close by the user while a run is going asks first (`event.spontaneous()`); the main
window closing it as the app quits does not.

Tests: `tests/gui/test_gui_main_window.py` (menus, readiness, tools),
`tests/gui/test_gui_readiness.py` (fix table, `readiness()`, the line, the dialog),
`tests/gui/test_gui_config_panel.py` (steps, review rule, uses lines).

### The window tabs and the Check page

`MainWindow._tabs` is the window's `QTabWidget`, built by `_build_tabs()`: index 0 is
*Documents* (`_main_splitter`, the workflow panel next to the results list and the log), index 1
is *Check* (`gui/check/_page.py:CheckPage`).  `_check` is disabled while no document has a
result (`ResultsTable.checkable_source_ids()`), and the tab labels carry the state:
*Documents · 12 of 28* and *Check — Anna_Mueller.pdf*.

**One query feeds the page.**  `ProcessingApplicationService.check_document(config, source)`
(`application/check_document.py`) reads the stored state and returns a `CheckDocument`: both
texts and their plain forms, the review aligned with them, the items the list numbers, the pages
of the scan, how the results were sent.  The window asks for it once per document
(`_load_check`) and once per stored edit (`_store_edit`), and never assembles that state itself.
`CheckDocument.realigned(raw_text, corrected_text)` re-aligns after a typing pause without
touching the disk, memoised on the two plain texts and the review.  The document also carries
`sent` and `preprocess`, so the queue row's *sent* cells come from the same read; the queue
keeps no preprocessing info of its own.

**Showing a document again.**  `CheckBody.show_document` fills the scan and both texts from the
`CheckDocument`, also for the document that is already open: a run may have read it again.  Only
the store's answer to a typed transcription is passed with `typed=True`, which keeps the text
being typed and the scan as they are (refilling would move the cursor, decode every page and send
the scan to page one).  A stored edit of the final text goes through `update_document`, which
never refills a text.  The list of items is rebuilt silently: `SidePanel` tells the page about a
choice only when the teacher made it, because the page answers by selecting the words in a text
that may be being typed in.  A typed edit that is waiting for its pause is stored **before** a
stage run invalidates the cache (`_run_stage`), never after.  A zero-width edit (the student's
words put back where the model deleted them) is placed with `PlainMap.insertion()`, which does
not snap forward over a paragraph break.

**Three views, two documents.**  `gui/check/_text_views.py` holds `ReadView` (*What was read*,
editable), `FinalView` (*Final text*, editable) and `MarkedView` (*With corrections*,
read-only), in a `QStackedWidget` of `CheckBody`.  *Final text* is loaded whenever a proofreading
exists, even while *With corrections* is in front: every card action edits `FinalView`'s
`QTextDocument` and the normal commit path stores it, and *With corrections* is rebuilt from the
stored result after every commit.

**Marks are overlays, never document formatting.**  In the two editable views a mark is a
`QTextEdit.ExtraSelection` (background, underline), so `document_from_qt` stores the same
Markdown with and without marks and typing can never write a mark into a text.  Only
`MarkedView` carries formatting of its own — the student's words struck through in grey, the
AI's taken-out words in red — and nothing is ever converted back from it.  A commit that changes
nothing stores nothing (`markdown == the stored text` is checked before the write).

**Positions come from `PlainMap`, never from arithmetic on a widget's text.**
`gui/check/_plain_map.py:PlainMap` walks a `QTextDocument` the way `document_from_qt` does and
records, per text run, `(qt_start, qt_end, plain_start)`; `position(offset)`, `offset(position)`
and `cursor(document, start, end)` are bisects.  The invariant, tested on every Markdown fixture,
is `PlainMap.text == TextFormatter.plain_text(stored)`.  List markers and the ` | ` and `\n` of
tables are *virtual* text: they carry a plain range but no Qt range, so an offset inside one
snaps to the next run.  Where a document breaks the invariant the view shows its text without
marks and clicks and the card says so — a wrong mark is worse than none.

**Every action is a text edit in plain offsets.**  `domain/teacher_work.py` holds
`TextEdit(start, end, text)` and the four functions that build the edits of a card action
(`take_out`, `put_back`, `use_suggestion`, `suggestion_is_wording`); `apply_edits` is what the
tests use.  `CheckPage` computes them, `FinalView.apply_plain_edits()` maps them through
`PlainMap` and applies them last to first in one `beginEditBlock`/`endEditBlock`, so one Ctrl+Z
undoes one action and the stored text comes from the commit path of typing.  The whitespace rule
is one function (`_fit_gap`): a replacement keeps the separators around it, an insertion into a
gap gets one space on the side where the neighbour is a word, a removal takes one adjacent space
with it, and words are never joined across a block boundary.

**Ctrl+Z is the corrected text's own history.**  `CheckBody` owns two `QAction`s
(`QKeySequence.Undo` / `Redo`, `WidgetWithChildrenShortcut`) that call the *Final text*
document's `undo()` / `redo()` and store at once (`flush`), so they work from the list and the
card too, not only while typing.  A text view that can be typed in keeps the keys for itself
(Qt's shortcut override), which is what makes Ctrl+Z in *What was read* undo the transcription;
*With corrections* is read-only and lets the page's action through.  After a step the panel
chooses the item whose span holds the cursor (`PlainMap.offset`), so the teacher sees what came
back.  The actions are disabled while *Final text* is read-only (the lock, *Hide names*) or has
nothing to undo, and the history ends whenever the text is filled again — another document,
*Hide names*, a reading fix, the reload after a run — because Qt resets it on `set_text`; a
stored edit coming back (`update_document`) fills nothing and keeps it.  There is no stack of the
app's own and no approval step per correction: every correction is applied, the teacher takes out
or changes what is wrong, and *looked at* and *Checked* say how far they got.

**Where an edit goes.**  `_store_edit(kind, text)` calls `edit_transcription` or
`edit_corrected_text` on `ApplicationCommands`, which writes the teacher's work and returns the
merged view; `_sync_row(view)` fills the row from it.  The cache's texts are never written by an
edit, so the machine result stays what the models produced and the teacher's text is what every
reader sees.  The undo stack is untouched by a stored edit coming back (`update_document`), so
Ctrl+Z works across the store exactly as across the cache.

***Checked*.**  A checkable button in the Check header (`CheckPage.checked_toggled`,
`CheckPage._refresh_checked`, public as `refresh_checked()`): *Mark as checked* unchecked, *✓ Checked* with a proofreading,
*✓ Text checked* without.  It marks what the document has now — the corrections when there is a
proofreading (bound, so a new proofreading starts unchecked), else the transcription, which is
then stored as a copy.  It is its own undo (not on Ctrl+Z), an edit after it does not uncheck it,
it is disabled while the lock holds, and it is allowed with *Hide names* on because it changes no
text.  A typed edit is stored before the mark (`flush_pending_edit`), and a press that cannot be
stored shows the notice and puts the button back to what the document says.  It does not move to
the next document — ▶ does.  The queue's *Checked* column shows
*Checked* / *Text checked* / nothing, and right-click → *Select checked* selects rows with
either; "checked only" export is that selection plus *Export*, not a separate option.

**Looked at.**  `SidePanel.item_shown` fires when an item becomes the chosen one (a list click, a
text click, the first item on open); the window calls `mark_looked_at` once per new key and
updates the document's set in place, without a reload.  The keys are `edit:<id>` (also for a
correction the teacher took out: it is the same item), `unclear:<n>`, `own:<start>-<end>` (the
words' place in the student's text, so taking another change back does not renumber them) and
`minor` for the group row; the list shows ● for a looked-at item and ○ otherwise (own changes
keep ✋, taken out ↶), and the footer says "8 of 12 looked at" from `looked_at_progress`.  Own
changes do not count, the minor group counts once, and looking at an item is never undone and is
not *worked on* — a run may still replace the answer.

**Walking the corrections.**  *Next* on the card (`SidePanel.choose_next`) chooses the next row
that counts for looked-at, in list order and from the top at the end; a document opens at the
first of them.  `counts_for_looked_at`, `next_unseen` and `first_unseen`
(`application/check_items.py`) are pure and on keys, so the list's rows and `looked_at_progress`
count the same rows — a single spelling change is not folded and does not count either.  The walk
keeps no state of its own: choosing a row is what marks it looked at, so the icons and the footer
say how far it got, and a card action does not move the choice — the teacher presses *Next* when
they are done with the item.

**The questions.**  Three, all in `texts/dialogs.py`, all one dialog for the whole selection:

- *Run start* (`_confirm_teacher_work_then`, after the data-flow question and before
  `_continue_run`): the documents whose proofreading is stale with these settings and whose
  corrections were worked on, and — in the same dialog — the ones whose corrections belong to no
  stored proofreading.  *Keep my changes* (default) writes `kept_under` and runs, *Proofread
  again* invalidates their proofreading first, *Cancel* starts nothing.  Only the second list:
  *Continue* / *Cancel*.  Any answer but *Cancel* drops the corrections of the second list
  (`drop_unbound_corrections`): the teacher was told that a new proofreading will not contain
  them, and they would otherwise be asked about on every run.  The lists come from
  `worked_on_documents`, worked out in the preflight's background task because it reads the files
  (`compute_file_hash_cached`: a file is read again only when its size, time or identity changed);
  only the check that starts a run asks for it (`ask_about_work`), not the readiness line.
- *Re-run Proofreading* (`_confirm_rerun_then`): the documents of the selection whose corrections
  were worked on, before the cache is touched.
- *Re-run Text Recognition*: the documents with a transcription part or worked-on corrections,
  which the new reading replaces.

All three fail closed: if the question cannot be put together (the look at the work failed), the
window says so and re-runs nothing.  A re-run drops the work before it resets the cache; if the
work cannot be dropped, nothing is reset.

A run with changed settings keeps worked-on corrections and says so per file in its log
("→ Proofreading kept: you worked on it (made with other settings)"), in the window and in the
CLI alike; the queue shows that proofreading as done with the note *kept* and a tooltip saying
why and how to get a new one.

**The lock.**  A document the run will redo is read-only from the run's start until its own
result arrives (`MainWindow._run_pending`, `_run_current`, `_apply_lock`): between the start of
the run and its file the tab would be stale and editable, and an edit stored then races the run.
The tab keeps showing what is stored, with the banner *Waiting for this run* and then *Being
proofread — you can edit it when it is done*.  Documents of the run that are done, and documents
the run does not touch, stay editable.  `set_lock` calls `flush_pending_edit()` first, so a typed
edit is stored before the lock takes the view.

**Only the teacher or a run start switches tabs.**  `_load_check()` fills the page and never
calls `setCurrentIndex`; `_open_check()` does.  `_on_file_done()` reloads the open document and
leaves the tab alone, `_on_run_finished()` switches nothing, and `_start_worker()` goes to
*Documents* only because the run's progress lives there.  The reason: a batch of 28 essays must
not pull the teacher out of the essay they are reading when the next file finishes.  A run that redoes the open document
locks it but keeps it the open one (`_run_pending`): its result reloads it without a tab switch,
and a run that ends before reaching it (stopped, failed) reloads it in `_on_run_finished()`.

**Edits go to the open document, not to the selected row.**  `_open_source_for_edit()` reads
`_current_preview_source_id`; the selection is how rows are chosen for a run, and the two must
not drift apart.  Opening a document from the queue (`check_requested`) selects its row, so the
queue shows where the teacher is; clicking the *Check* tab does not (below).

**Clicking *Check* with nothing open** opens the current queue row when it has a result, else
the first row that has one (`_check_target()`), again without touching the selection.  A rebuilt
queue that no longer holds the open document closes *Check* and shows *Documents*
(`_sync_check_with_queue()`).

**Keys.**  Esc leaves *Check* unless the focus is in a text field, a line edit, an editable
combo box or an open popup (`_escape_is_for_the_focus()` in `gui/check/_page.py`); Ctrl+1 and
Ctrl+2 are `QShortcut`s on the window, so they work from anywhere (Ctrl+2 only while *Check* is
enabled).  The arrows in the header step through `checkable_source_ids()` and select the row
they land on.

**Copy.**  `Copy ▾` takes the text from the table row, not from the view, through
`ProcessingApplicationService.text_for_copy()` -- the same function *Export text* uses, so a
copy and an export never disagree.  `flush_pending_edit()` runs first, so a half-typed edit is
saved before it is copied.  The menu's "names hidden" entries follow
`_refresh_privacy_availability()` (`_refresh_copy_state()`), so installing the Privacy add-on or
changing the language updates the open document's menu at once.  The two *Save* entries go
through the window's export path (`_on_export_requested`), not a second one.

**Every prepared page is kept.**  `PreviewImageStore.persist_page_images()` writes page 1 as
`<stem>_preprocessed.png` (the `preview_img` the scan shows) and pages 2..N as
`<stem>_p<N>.png`, listed in `DocumentState.page_images`; the stem is `document_stem(file)`, the
name of the document's state file, so two files of one name in different folders keep their own
pages.  `prune_page_images()` removes the ones
a shorter document no longer has, and `cleanup_intermediate_images()` treats them as
intermediate.  `DocumentState.load_valid()` drops the list when a file is missing, so a deleted
cache costs a re-run, never a broken page.

**The page as it was scanned** is kept too, when the image preparation changed the page:
`persist_original_images()` writes page 1 as `<stem>_original.<ext>` (the `original_img` the
benchmark and the Word builder read) and pages 2..N as `<stem>_o<N>.<ext>`, listed in
`DocumentState.original_images` (first = `original_img`).  `prune_original_images()` matches
`<stem>_original.*` and `re.fullmatch(rf"{re.escape(stem)}_o(\d+)\..+", name)` beyond the new
count, so the files of a document called `Anna_o2` stay.  A page whose original is missing stays
prepared when *Original* is on, and a state written before this field has the one original it
had.  A PDF with a text layer stores every page for the scan as well: `PdfInputProvider` renders
the pages it skips into `InputPayload.page_images` — at `SCAN_DPI` (150), not the OCR
resolution, because nothing reads them — and `FileProcessor` hands them to
`persist_preview_artifact(pages=…)` as it hands the prepared pages.  The scan's strip
(`ScanPane` in `gui/check/_scan_view.py`) holds the *Prepared · Original* switch, offered only when a page
has an original and a step acted on it, and the caption of the steps that prepared the pages
(`prepared_caption`).  The scan opens fitted to the page; a double click toggles fit page / fit width.

**The strips above the text** (`gui/check/_run_strip.py`): `NextStepStrip` over a transcription that
has not been proofread (*Proofread*, or *Set up proofreading…* via `CheckPage.set_proofreading_ready`)
and `RunStrip` while the open document is in a run, fed by `MainWindow._refresh_check_run`.  A run
started from the Check header keeps the tab (`_run_from_check`).

Tests: `tests/gui/test_gui_check_page.py` (the page on its own),
`tests/gui/test_gui_check_body.py` (the switch, the banners, the lock, the splitter),
`tests/gui/test_gui_text_views.py` (the three views and their marks),
`tests/gui/test_gui_side_panel.py` (the list and the card), `tests/gui/test_gui_plain_map.py`
(the invariant), `tests/gui/test_gui_scan_view.py` (the pages in one scene),
`tests/gui/test_gui_main_window.py` (tabs, opening, arrows, shortcuts, copy, run start, the
lock), `tests/unit/test_preview_image_store.py` (the page files).

### Setup Assistant

`gui/setup/` is a `QWizard` that asks what only the teacher knows and then writes the settings
once.  It is opened on a first start (no settings file yet) and from **Tools → Setup Assistant…**;
`MainWindow._open_setup_assistant(first_start=...)` builds the `SetupContext` (the settings as the
panel shows them, the application service, the main window's `OllamaDownloadManager`, the system
language on a first start) and connects `SetupAssistant.committed` to `_apply_setup_result`.

**The order follows the answers** (`gui/setup/_flow.py`).  `ORDER` lists the seven pages —
Welcome, Your classes, The AI on this computer, Proofreading, Names and personal information,
Ready to set up, All set — and `next_page(page, answers)` decides which one follows, so a page
only says what it asks and never where to go.  With typed texts the reading model is not asked
for.  The Privacy page is always on the path; `SetupAnswers.privacy_case` decides its shape (online
or another PC: *Remove personal info*; this computer: only *Hide names in this app*, handed back as
`SetupPlan.display_anonymized`, never `False`).  A page that is never reached asks nothing: the
Ollama status line, the computer profile, the online service section, the model list and the setup
plan are all built the first time their page is shown.

Every page sits in a scroll area (`SetupPage._content()`), because QWizard gives all pages one
height and would squeeze a tall page's widgets; `fit_to_screen()` (`gui/_window_geometry.py`) keeps
the wizard and Settings inside the screen.  The wizard sets `ModernStyle` again on show: Windows
swaps in its Aero look (a back arrow in the title area).  A found Ollama server is a `ServerCard`
(`gui/_server_card.py`) on the Ollama page, in Settings and in the search dialog.  The proofreading
page's model list comes from `plan_with_model_choices()` with the suggestion; an empty
`SetupAnswers.local_model` means *use the recommended one*.  `SetupAnswers.extra_names_model`
(the Privacy page's extra language) becomes `anonymizer_secondary_model` and joins the downloads.

**The plan is a pure function** (`application/setup_plan.py`).  `SetupAnswers` is what the pages
collected, `SetupFacts` is what `ProcessingApplicationService.setup_facts()` measured (profile,
memory, card, whether Ollama answers, installed tags, free disk, whether removal and name
detection are on, the missing package), and `build_setup_plan(answers, current, facts, rate=,
best=, default_model=)` returns a `SetupPlan`: the Settings-window keys, the main-window keys,
one `PlannedStep` per summary row, the `PlannedDownload`s, whether the review keeps its own
model, the `DiskVerdict`, and the figures the summary line shows (`needed_gb`, `free_disk_gb`).
Two rules shape it: **a working choice is kept** (an installed model that fits is not replaced
by the recommendation, so running the assistant again changes nothing) and **nothing is turned
off** ("Remove personal info" is only ever set to `True`, and a review with its own model keeps
it).  The assistant writes only keys the Settings window or the main window already own, so
`Config`, `DEFAULTS`, the TOML keys and the CLI are untouched and there is no migration.
A reader that is already chosen (`ocr_engine = ppocr`) is kept like any working choice: its row is
*kept*, and when its model files are not downloaded the setup plan adds one `DownloadKind.READER_MODEL`
download (31 MB, the install round's *reader* phase between spaCy and the pulls).  The assistant
never proposes to switch to the reader.

**The page models** (`DOC_ORI` / `UVDOC`, 39 MB) serve the *Automatic* image preparation whatever
engine reads the page, so they are planned apart from the reader.  When pages are handwritten,
`preprocess_method` (saved value, default `auto`) is `auto` and `SetupFacts.page_models_installed`
is `False`, `build_setup_plan` adds one `DownloadKind.PAGE_MODELS` download (`PAGE_MODELS_ADDON_ID`, stage
`ocr`, `PAGE_MODELS_SIZE_GB`).  Installed, typed pages or *Original page* plan nothing, and the
assistant never changes `preprocess_method`.  The summary draws it as its own "Page models" row
after the reading row (the reading row does not list it); `_start_downloads` hands it to the
round as `page_models=True`, which runs after the reader.

**The summary's wording lives in `gui/setup/_summary.py`.**  `summary_rows(plan, place_of,
key_missing=)` turns a plan into `SummaryRow`s — the classes first, then one per step, each with
the model, the place (`short_place`, the same words as the service list) and its downloads — so
the lines can be tested without a window.  `SummaryRows` draws them with one ticked checkbox per
download and hands back the ones the teacher left ticked; those, and only those, reach the round.

**Downloads go through `InstallRound`** (`gui/_install_round.py`, taken out of Add-ons).  Set up
calls `SetupAssistant.commit(downloads)`, which stores the typed keys once
(`store_key_drafts`), emits `committed` with a `SetupResult(settings, panel)` and starts the
round with one list per kind (pip packages, spaCy models, Ollama tags).  An Ollama tag whose
`possible` is `False` — Ollama is not running — is left out, so it does not fail at once; the
main window offers it as soon as the server answers.  The Done page follows the round and the
pull manager, so every line says how far it is, and it offers the speed test on the settings the
plan leaves behind, drawn again once the queue runs empty.

**Nothing starts by itself.**  The downloads need **Set up**, a network search for a server
elsewhere starts only from the dialog's own button, **Start Ollama** is a button, and the speed
test runs only when the teacher presses **Test speed**.  The server address never changes
without the teacher choosing it.

**How the result is applied.**  `_apply_setup_result` puts the Settings-window keys into the
panel with `update_settings` and the main-window keys with `load_from_dict` — which skips the
stage guard, because the models are still downloading and a guard would untick Proofread and
open a dialog — then saves once, checks the services and refreshes the queue.  Cancel changes
nothing; on a first start it leaves today's setup in place (`_apply_first_start_defaults`: the
system language and the best models for this computer).  **Select documents…** closes the window
and asks the main window for the folder afterwards (`documents_wanted()`), because the folder
dialog must not open behind the wizard.

**Closing.**  `done()` refuses while the round is busy (`dlg.installation_running()`),
because the new package must be made importable first; otherwise it calls `shutdown()` on every
page, which stops the pending plan, the status checks and a running speed test.  Cancel puts the
typed keys back (`discard_key_drafts`) and forgets the model lists fetched with them.

The packaged build lists `teachers_teammate.gui.setup` as a hidden import, because the main
window imports the package lazily.

Tests: `tests/gui/test_gui_setup_flow.py` (the order), `tests/gui/test_gui_setup_pages.py` (the
pages, the summary rows and the done page), `tests/gui/test_gui_setup_assistant.py` (the wizard,
Set up, Cancel, closing), `tests/gui/test_gui_window_geometry.py` (a small screen),
`tests/unit/test_setup_plan.py` (the setup plan's rules); the main window's side
is tested in `tests/gui/test_gui_main_window.py`.

**The walkthrough** (`tests/gui/test_gui_setup_walkthrough.py`) is the one test that runs the whole
thing: the real `SetupAssistant` and every page, the real `ProcessingApplicationService`
(`setup_facts`, `plan_setup`, the model listing and rating), the `LatestTask` pool, `InstallRound`,
`OllamaDownloadManager`, the `ollama` client over loopback HTTP, the real `MainWindow` and
`ocr.toml` on disk.  Replaced, each at the narrowest seam: Ollama by
`tests/fakes/fake_ollama.py:FakeOllama` (one loopback port, so a server on the developer's computer
cannot answer), pip, spaCy, the reader and the page models by recording stubs on the service, the
model store by one under `tmp_path` with a `bundled_root` that does not exist, and the computer by
`hardware_profile = "16gb"`, a fixed free disk and a fixed memory.  `SetupAssistant.exec` is
replaced by a function that shows the wizard, runs the script and closes it — the same sequence
without the nested modal loop, so a failed assertion is reported instead of swallowed by it.  The
expected tags come from `best_model(...)`, never from literals, so a catalog update does not break
the test.  `tests/unit/test_fake_ollama.py` keeps the fake honest: it drives it with the real
`ollama` client, so a version bump that changes a response model fails there, with a readable
message, instead of hanging the walkthrough.  `FakeOllama` is the **only** Ollama server in the
tests: the Add-ons page tests and the discovery probe tests use it too, and what they need beyond
the walkthrough (a model's size, say) became a parameter of it.  Its answers follow Ollama's own
source — a list entry always has `modified_at`, `details.format` and `details.parent_model`, and a
failing pull streams `pulling manifest` before the error line, all in a 200 stream.  Ollama's own
repository tests the same boundary with `httptest` servers and, for integration, a real
`ollama serve` on a free port (`integration/`, `OLLAMA_TEST_EXISTING`); we have no such run, so a
server-side change is not seen until the fake is compared with its source again.

**The packaged build** proves the same on all three systems: the `setup assistant` check in
`frozen_modes.py` builds the wizard on the real service, clicks through to the summary and waits
for the setup plan, and — frozen only — asserts that the bundle's page models are seen, so no
`DownloadKind.PAGE_MODELS` download is planned.  It points the app at `http://127.0.0.1:9` and
works in a temporary folder, so it answers the same on every machine and writes nothing into the
user's folders.

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
| `never` | never — always OCR |

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

OCR engines answer in different notations: plain text (the built-in reader), Markdown (many
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
(`raw_text_format == "plain"`, e.g. the built-in reader's output, `.txt` input) or canonical Markdown
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
failed call returns its fallback untouched.

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
| Check tab | formatted rich text (`gui/check/_text_views.py`); **⋯ → Show Markdown source** shows the stored Markdown of the view in front |

**Editing.** The formatted view is editable (Ctrl+B / I / U, Ctrl+Shift+X crossed out,
Ctrl+Shift+= inserted above the line). Edits are converted back to Markdown
(`document_from_qt` → `MarkdownRenderer`) and saved like any other manual edit. Bounding
boxes of layout engines are not kept.

**Adding a notation** (hOCR, DocTags, …): implement `OcrOutputParser` in
`infrastructure/ocr_formats/`, add it to `default_parsers()` ahead of `PlainTextParser`, and
add the module to `_OCR_SRCS` in `teachers_teammate/BUILD`. Parsers must be total: an
exception falls through to the next parser, ending at plain text.

## Correction review

Proofreading answers with a *review*, not a text: the corrected text plus one explanation per
edit, and a list of unclear or ambiguous expressions.  The review is what the corrector returns,
what the cache stores and what every reader reads — the pipeline, the CLI line, both Word
layouts, the queue, the export and the evaluation tools.

**The review is edit-based** (`domain/proofreading/review.py`).  `CorrectionReview` holds
`EditNote`s and `UnclearNote`s.  An `EditNote` has an id, one or more `EditPart`s (`raw_start`,
`raw_end`, `original`, `corrected`), the source sentences it touches, a category and an
explanation; an `UnclearNote` has a span in the raw text, the expression, why it is unclear and a
recommendation.  The edits are always the diff of the student's text against the corrected text
(`word_diff`), so the model explains edits but never places them.

**The offsets are in the plain text of the stored `raw_text`** — `plain_text_of(raw_text,
raw_text_format)`, crossed-out words included, real names.  The exchange makes them in the plain
text of what it was sent, so the two producers that change that text fix the offsets before
anything stores them:

- **Pieces** (`infrastructure/proofreading/chunking.py`).  A text longer than one request is
  proofread in pieces.  Each piece's plain text is found in the whole plain text from a forward
  cursor; its review is shifted by that start, the edit ids are renumbered over the whole text
  and `EditNote.sentences` is recomputed from it (`merge_reviews`).  A piece whose plain text is
  not found, or whose shifted review does not fit, loses its review and the file gets
  `MISSING_REVIEW_WARNING`.
- **Names** (`infrastructure/workflow/file_processor.py`).  The exchange sees placeholders
  (`[PERSON_1]`), and a name is rarely as long as its placeholder, so every edit after a name
  would point at the wrong words.  `restore_with_offsets` returns the restored text and an
  `OffsetMap` (`domain/offset_map.py`); the review is remapped through it.

Both are checked with `fits(review, raw_plain)`: every part's `original` must be
`raw_plain[start:end]`.  A review that does not fit is dropped with a correction warning
(`REVIEW_UNMATCHED` for names, `MISSING_REVIEW_WARNING` for pieces) instead of being stored: a
half-right review would show AI corrections as the teacher's own.

**Align** (`domain/proofreading/review.py:align`) is how a reader shows the review.  It diffs raw
against corrected (`word_diff`, the one diff of the app) and attaches each note to the hunk it
explains, by span.  A hunk without a note is either the model's change without a reason or the
teacher's own edit; a note whose hunk is gone becomes an *orphan*.  Alignment is recomputed from
`(raw, corrected, review)` on every render and needs no extra state.  Every count shown anywhere
(`review_summary()`) is the number of listed hunks: spelling and punctuation hunks
(`AlignedChange.is_minor`) are marked in the text but left out of the lists.

**The Word file** (`infrastructure/docx_builder.py`, `infrastructure/docx_comments.py`).  Both
layouts read `align()`.

- The *table* layout lists the explanations of the changes that are in the text
  (`explained_sentences`): one block per changed sentence, quoted as it reads in the corrected
  text now, one bullet per note.  A note on an edit the teacher changed afterwards explains words
  that are no longer there and is left out (`AlignedChange.explains_text`).
- The *comments* layout writes the extracted text as plain text, one paragraph per line, and
  comments it: every hunk is highlighted on its raw span and commented with what it does to the
  words, every anchored unclear expression is commented on its stored span.  An edit of several
  hunks is explained once, in the comment of its first hunk.  There is no diff and no text search
  of its own.

**The sentence-based review is the baseline's alone.**  `tools/model_eval/one_request/
sentence_review.py` holds it: notes per sentence, `splice`, and its own `align`.  Nothing under
`teachers_teammate/` imports it (`tests/tools/test_no_sentence_review_in_the_app.py` greps the
package), and the app's one review is the edit-based one above.

**The one-request path is the evaluation baseline, not the app.**
`tools/model_eval/one_request/` holds the answer schema, the answer formats, the policy presets
and `splice`; its `edit_review.py` turns the sentence answer into the edit-based review at its
edge, so the tools read one review shape everywhere.  Nothing under `teachers_teammate/` imports
it; `tests/tools/test_session.py` keeps it that way.  A baseline row counts one
explanation per edit; the exchange rows are pinned by a test.

**The answer format is fixed.**  It is not a setting: the exchange's steps carry their
own answer shape (a plain text for ①, a schema for ② and ③), so there is nothing for a teacher to
edit.

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

`pydantic` is imported by `infrastructure/proofreading/schemas.py` (and by the tools'
`one_request/correction_schema.py`), so it is declared in `pyproject.toml` (it already arrives
with `ollama`).

`align()`, `fits()`, `merge_reviews()`, `explained_sentences()` and the offset map are pure and
table-tested in `tests/unit/test_review_v2.py`, `tests/unit/test_offset_map.py`,
`tests/unit/test_quotes.py` and `tests/unit/test_anonymization_mask.py`; the Check tab's views
and list are tested with pytest-qt in `tests/gui/test_gui_text_views.py` and
`tests/gui/test_gui_side_panel.py`, the Word layouts in `tests/unit/test_docx_builder.py` and
`tests/unit/test_docx_comments.py`.

## Proofreading exchange

The exchange is the shape of the proofreading the app runs: three requests instead of one, and
the edits are always the diff of the student's text against the corrected text, so the model
explains edits but never places them.  `ExchangeCorrector`
(`infrastructure/proofreading/corrector.py`) is the pipeline's `Corrector`: it runs exchange `T`
over a `ModelPort` and returns the edit-based review as it is, so what it makes is what the cache
stores and every reader reads.

| Module | Role |
|---|---|
| `domain/proofreading/word_diff.py` | `word_diff(raw, corrected)` → `Hunk`s. The one diff of the app: GUI preview, DOCX, `align`, the edits and the evaluation tools all read it. `difflib` is imported here and nowhere else |
| `domain/proofreading/edits.py` | hunks → `Edit`s with ids, sentence groups, step-② batches, note merging, the overlap filter, the optional-comma rule, the framing check |
| `domain/proofreading/review.py` | the stored `CorrectionReview` (`EditNote` with parts, `UnclearNote`), `align()` for display, `fits()` / `merge_reviews()` / `explained_sentences()` |
| `domain/proofreading/quotes.py` | `find_spans()` for the quotes of step ③, `map_back()` through the hunks |
| `domain/proofreading/markdown_shield.py` | `shield()` / `restore()`: strip the notation before the request, put it back after it |
| `domain/proofreading/text_loss.py` | `measure()` / `dropped_text()`: what an answer kept of the text it was sent, for the guard of step ① |
| `domain/correction_prompts.py` | the fixed English system prompts of the three steps, the parameter and notes blocks, the batch body of step ②, `PROMPT_VERSION` |
| `infrastructure/proofreading/port.py` | `ModelPort` (Bridge): `OllamaPort`, `LangChainPort` |
| `infrastructure/proofreading/runner.py` | `StepRunner`: cut-off tries, one repair, window check, timeout |
| `infrastructure/proofreading/schemas.py` | the answer schemas of steps ② and ③ with their parsers |
| `infrastructure/proofreading/exchanges/` | `Exchange` protocol, `EXCHANGES` registry, `TExchange`, `FallbackExchange` |
| `infrastructure/proofreading/chunking.py` | `ChunkingCorrector` and `proofreading_pieces()`: a text longer than one request is proofread in pieces and the results joined |

**The three steps.** ① *Correct* sends the shielded text and gets plain text back; its answer
is restored into the source notation and stored. ② *Explain* sends the sentences of a batch
before and after the correction with `⟦n⟧` in front of every edit, and gets one category and
one explanation per id back; the ids are pinned by an enum, so an answer with a wrong id is
invalid rather than silently misattached. ③ *Unclear* sends the corrected text and gets the
expressions a reader cannot tell, with a reason and a suggestion. ② and ③ depend only on ①
and never fail the file: a failed batch leaves its edits unexplained with a warning, a failed
③ leaves the unclear list empty with a warning. Only ① can fail a file, and then the source
is kept.

**A dropped text is not a correction.** A model can answer with a well-formed text that is
missing a sentence or a whole paragraph, and nothing in the answer says so. Step ① therefore
compares its answer with the text it sent (`domain/proofreading/text_loss.py`): an answer that
keeps less than `LOST_RATIO` (0.80) of the words, writes more than `ADDED_RATIO` (1.25) of them,
or deletes a run of `RUN_WORDS` (50) words without writing as many in their place is asked for
once more.  A second answer that added text fails the piece like a cut-off.  A second answer that
**left text out** is **repaired** (`text_loss.repair`): each hunk cutting `RESTORE_MIN_WORDS` (8)
words or more is written back from the student's text, and the repaired text is judged again (still
not the text: it fails as before).  The file is stored with a warning, its review is made from the
repaired text (so it fits), and each stretch is an `UnclearNote(left_out=True)`, listed in Check as
`ItemKind.LEFT_OUT`.  It is a note for the teacher: the Word files and the unclear badge skip it.
`CorrectionOutcome.left_out` makes the outcome `fell_back` (no speed sample), and the evaluation
tools count the row as lost text although the stored output is whole.  Marks are not words, and a text under `MIN_RATIO_WORDS` (10)
words is not judged by the ratio: correcting "ich bin müde" into "Ich bin müde." changes its
length by a third. The run threshold is much
higher than the one the evaluation tools use against a reference, because the app has no
reference: a text whose own correction cuts a paragraph looks exactly like a dropped one, so the
guard only fires on a cut no correction of that text would make. On the saved runs of the
official test set it fires on the four models that drop text and on no text where most models
agree.

**A rejected answer says why:** the detector's numbers (`words_in`, `words_out`, `cut_run`, `rule_*`,
`num_ctx`) go to `StepReport.counters` and the warning log; the answer itself only to the debug log,
as it is the student's text.

**Edits and ids.** `edits()` groups consecutive hunks into one edit, never across a sentence
group: a deleted token run that reappears as an inserted run in the same group is one edit
(the verb bracket), and hunks at most `GROUP_GAP_WORDS` words apart are merged. Ids run 1…k
in text order over the whole text. An edit that only inserts a comma before `und` / `oder`
between two main clauses is marked optional; the clause check is injected, so the rule is off
when no spaCy model of the text language is installed.

**Anchoring.** Step ③ quotes the corrected text. `find_spans()` looks the quote up in
plain(corrected) (whitespace-normalised, then fuzzy), `map_back()` carries the span through
the hunks to the raw text, and a quote that touches an edit is dropped and counted: the
review never shows a flag on words the correction already changed.

**Markdown shield.** `shield()` turns the parsed document into the request text — blocks as
paragraphs, no marks, no list markers, crossed-out words removed — and keeps a map from every
character back to its block and style. `restore()` diffs the request text against the answer
and rebuilds the document along the source: equal characters keep their style, a replacement
inside one styled span takes that style, a replacement across style boundaries loses it and is
counted, an insertion takes a style only when both neighbours share it, and crossed-out words
are put back exactly where they were. Block boundaries are always the source's. Plain-text
sources skip the shield.

**Corrections only.**  *Corrections only (faster)* sends step ① alone, whatever the model's
profile says: `ProofreadContext.corrections_only` makes `TExchange.proofread` return after ① with
`review=None`, and `max_text_chars` then reports ①'s limit, because a corrections-only text goes
out in fewer pieces.  The flag is `Config.correction_output == "text"`; there is no separate key
and no automatic fallback — a run that cannot answer ② or ③ keeps the corrected text and warns.

**A partial answer is not a sample.**  When a review comes back but a report of step ② or ③
failed, the answer lacks part of what was asked, so `ExchangeCorrector` sets
`CorrectionOutcome.fell_back` and the file processor drops the speed sample of that document: its
requests would be counted into the answer length of a mode that answered in full.

**The stage builder** (`infrastructure/stage_builder.py:build_corrector`) builds the corrector
from the run's settings: on Ollama an `OllamaPort` with the run's proofreading window and
`ocr_timeout`, on an online provider a `LangChainPort` over the provider's chat model (strict
schema for `openai`), always wrapped in `ChunkingCorrector` so a long text goes out in pieces.
`PipelineComponentFactory.build_corrector(port, settings, profile, cut_off_tries)` is the seam a
test replaces; the cut-off tries come from step ①'s temperature, because ① is the step whose
answer is as long as the text.  `ProofreadingSettings` carries what the teacher set (level,
explanation language, notes, corrections only); the connection's own settings (`num_ctx`,
`strict_schema`) belong to the port.

**Adding an exchange.** Implement `Exchange` (`proofread`, `max_text_chars`, `cost_model`),
register it in `EXCHANGES` under its `ExchangeKind`, and build it through `build_exchange()`
so the port, the cut-off tries and the clause check arrive the same way for every exchange.
`FallbackExchange(primary, fallback)` is the chain of responsibility an exchange uses when it
can fall back to a cheaper one. Nothing outside `infrastructure/proofreading/` sees an
exchange's own answer schema: each exchange maps its answer to `ExchangeResult`, and the GUI,
the DOCX builder and the cache read `CorrectionReview` only.

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
`TRUNCATED_MESSAGE` (*"The text was too long for the model's working memory."*). What it costs
depends on the step: a cut-off ① fails the file and the source is kept (a half-corrected text is
never stored), while a cut-off ② or ③ only costs that part of the review — the corrected text is
stored and a warning says what is missing. A provider error (`"transport"`) fails the file at
once, because ① would meet the same server.

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
- **Proofreading** is sized by the exchange, not by one request: `T.max_text_chars()` takes the
  smallest limit of the steps that run, each computed from its own request. Step ① answers with
  the text again (`answer_factor=1.0`), step ② carries the text twice — every sentence before and
  after — next to the edit list (`text_copies=2`, plus `EXPLAIN_EDITS_CHARS` for the fixed part),
  and step ③ answers short. The answer grows with the text, not with the request, so the fixed
  part of the request is taken out of the window first and only the rest is shared:
  `text_tokens = (num_ctx − overhead_tokens) / (text_copies + answer_factor)`. With corrections
  only, step ① is the only request, so its own limit is the one that counts and a text goes out
  in fewer pieces. The one-request baseline's own figure
  (`one_request/correction.py:NativeOllamaCorrector.max_text_chars`, `answer_factor=7.0`) is what
  the evaluation gate measures against; the app does not use it.
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
  as `hardware_profile` does. With the proofreading cap at the base window it does not change
  proofreading: it sizes a content review that runs on **its own** Ollama model, which can still
  get 16384 or 32768. A content review on the same model as proofreading gets 8192 as well, so in
  the default setup the setting changes nothing.
- `run_windows()` resolves both stages once per run, each from **its own** model
  (`proofreading_spec`, `evaluation_spec`), so a larger review model is not sized from the
  smaller proofreading model; `stage_builder.resolve_run_windows()` applies it to a `Config`, and
  a stage whose provider is online reports the base window (the app does not set one). When
  proofreading and content review use the same Ollama model, both get the smaller window (Q4): one reload per file between the stages would cost
  seconds on every run, and content review still gains from its own answer allowance.
- The window is sized for **one model at a time**. `run_files()` runs every stage of one file
  before the next file, so OCR and proofreading take turns and never run in parallel; a larger
  window can therefore make Ollama unload the idle OCR model and load the writing model on every
  file. That is an accepted cost, the same one `ollama_utils.py` already
  names for a small card: nothing slows down or fails, the models only swap.

The resolved window is passed down (`OllamaPort(num_ctx=…)`, `StageBuilder.resolved_windows()`)
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

`ChunkingCorrector` (`infrastructure/proofreading/chunking.py`) decorates the exchange corrector
in `stage_builder.py`; an online provider's port has no window, so `_chunked` leaves it unwrapped. On
**every call** it asks its inner corrector for `max_text_chars(language, text_format=…)` —
computed from the exchange's own prompts and the fixed part of the request, so the figure cannot
drift from what is really sent, and per call because the Markdown notation hint makes the system
prompt longer — and:

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

Both figures this section needs come from `fit` (see *Model sizes* in `tools/model_eval/README.md`), and neither has been measured
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

### Diagnosing a slow start or a window that freezes

`infrastructure/diagnostics.py`; off unless asked for. Start the app with `--diagnose` (for the
installed Windows app: a shortcut whose target ends in ` --diagnose`, or
`set TEACHERS_TEAMMATE_DIAGNOSE=1` in a terminal) and `diagnostics.log` appears in the data
folder (`resolve_data_root()`; on Windows `%LOCALAPPDATA%\teachers_teammate`). It holds

- one line per phase of the start, with the seconds since the process began (imports and the
  early splash are before the first line; `main_gui` writes the rest: the add-on folder, the
  application, the theme, the window built and shown, the first turn of the event loop), and
- a report whenever the GUI thread stays silent for more than 0.3 s: the calls it is in (module,
  line, function) and where the other threads are, repeated at 1, 3, 10 and 30 s, then a line
  when it answers again.

A stall can only be explained while it lasts, so a thread watches a heartbeat that a 50 ms
timer of the GUI thread sets (`Diagnostics.beat`). The file contains code places and times,
never a document, a file name, a setting or a value. A new phase is `diagnostics.phase("…")`;
it does nothing while the diagnostics are off. The file is cleared at the start of a session
when it is over 1 MB.

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

Optional packages that are installed at runtime — spaCy, LLM provider connectors — all land in
a **persistent user-writable directory** that is separate from the application bundle or the
development venv:

```
Windows:  %LOCALAPPDATA%\teachers_teammate\packages\
Linux:    ~/.local/share/teachers_teammate/packages/
macOS:    ~/Library/Application Support/teachers_teammate/packages/
```

The location is computed by `addon_manager.get_packages_dir()` using
`platformdirs.user_data_dir`. This directory is prepended to `sys.path` by
`inject_packages_dir()` so anything installed there is importable.

The folder is **not** versioned by Python: a package compiled for another interpreter stays where
it is after an app update.  `foreign_python_packages()` reads the Python tag in every
`.dist-info/WHEEL` at each start and treats such a folder like an outdated one — off `sys.path`,
*Update add-ons* in Settings → Add-ons.  See "The bundle's constraints and add-ons after an app
update" below.

`inject_packages_dir()` first runs `remove_retired_addons()`: the retired Paddle add-on
(`paddlepaddle`, `paddleocr`, `paddlex`, `opencv-contrib-python`, which shadowed the app's own
OpenCV) is deleted by its `RECORD` files only; numpy is left alone.  It is then called:

- At application startup (`run_gui.py`, `gui/__main__.py`, `__main__.py`), so packages
  installed during a previous session are immediately available.
- After every successful install or model download, so the newly installed package is
  usable in the same session without a restart.

The function is idempotent — safe to call multiple times.  Every entry point calls it first
(`ProcessingApplicationService.inject_addon_packages_dir()`): `run_gui.py` and `__main__.py`,
and `main_gui()`, `main_benchmark()` and `cli.main()` for a wheel install, so a spaCy model
downloaded in one session is found after a restart however the app was started.

### The bundle's constraints and add-ons after an app update (frozen builds)

The packages folder comes **first** on `sys.path`.  A numpy, pydantic or requests that an
add-on's install brought along would therefore replace the bundled one (PyInstaller may bundle
only part of a package, so putting the folder last is wrong too).  Two things prevent that:

- **Constraints.** `tools/build/addon_constraints.py` (run by `build_standalone.py`, bundled by
  the spec) writes `teachers_teammate/_build/addon_constraints.txt` — `name==version` of every
  package in the build environment except the build tools (`BUILD_ONLY`).
  `addon_manager.pip_policy_arguments()` adds `--upgrade --only-binary :all: --no-cache-dir -c <file>`
  to every pip call of the installed app: `--upgrade` because `pip --target` leaves an existing
  package folder alone; wheels only because a source build would start the GUI exe as its build
  interpreter; no cache because pip's cache is shared with other Python programs and never
  cleaned.  An add-on whose requirements conflict with the bundle fails with pip's own message.
  A language model is installed without `--upgrade` (it must leave what is there alone).  Source
  runs and wheel installs get none of it (`addon_constraints_path()` is `None`).
- **Outdated folders.** After an app update the folder may hold copies from the old bundle.
  `outdated_shared_packages(pkg_dir, constraints)` compares the folder names
  (`<name>-<version>.dist-info`) with the constraints at every start; when something differs
  `inject_packages_dir()` leaves the folder **off** `sys.path` and the app runs on its own bundle.
  `ProcessingApplicationService.addons_outdated()` tells the GUI: the requirement checks for a
  step that needs an add-on in the folder (spaCy, a LangChain package) return
  `IssueKind.ADDONS_OUTDATED` (`fix_for` → *Update add-ons…*), and Settings → Add-ons shows a
  banner with **Update add-ons**.  Nothing downloads before that click.  `update_addons()` →
  `addon_manager.update_installed_addons()` installs every add-on in the folder again with the
  constraints, removes the outdated shared copies pip did not replace (by `RECORD`), and
  injects the folder.  Language models are separate packages that share nothing with the app
  and are not touched.
- **Another Python.** The folder is not versioned by Python, so an app update that changes
  `.python-version` leaves compiled add-on packages behind that cannot be imported.
  `foreign_python_packages(pkg_dir)` reads the `Tag:` lines of every `.dist-info/WHEEL` at every
  start (no marker file to keep in step): `pyX-none` and `pyXY-none` fit when XY is at most the
  running version, `cpXY-abi3` fits when XY is at most the running version, `cpXY-cpXY…` and
  `cpXY-none` only on that exact interpreter.  Only in the installed app, and a distribution
  without a readable `WHEEL` or `RECORD` is left alone.  A folder with a foreign distribution
  counts as outdated — off `sys.path`, the same *Update add-ons* banner — and
  `update_installed_addons()` installs the add-ons one by one (a failure does not stop the
  clean-up; it is raised at the end) and drops what the reinstall left over
  (`drop_foreign_packages()`, by `RECORD`; files the fresh copy of the same package lists stay).
  spaCy language models are `py3-none-any` and stay.
- **Checks.** The smoke test (`addon constraints`, frozen only) proves the file is in the bundle
  and pins the numpy, pydantic, requests and Pillow the bundle really carries.  In CI,
  `tools/build/check_addon_resolution.py` runs the built app's pip in `--dry-run` mode once per
  add-on (Linux job), which is the only place the newest `langchain-openai` & co. are held
  against the bundled versions (they are not in the test lock on purpose).  The same check runs
  weekly on all three build runners without a built app (`--python … --constraints
  addon_constraints.txt`, `.github/workflows/addon_resolution.yml`), so a new release of an
  add-on's package shows up as a red run, not on release day.

### The add-on lock and the license list

The constraints file says what an add-on may **not** change.  What it *gets* is the lock:

- **`addon_lock.txt`.** `requirements_addons_lock.txt` is the pip-compile lock of the `llm` and
  `privacy` extras (`bazel run //:requirements_addons.update`, the same tooling as the test
  lock).  `tools/build/addon_lock.py` (run by `build_standalone.py`, bundled by the spec) turns
  it into the flat `teachers_teammate/_build/addon_lock.txt` — the lock minus the packages
  `addon_constraints.txt` already pins, because two constraints files that disagree make pip
  refuse the install.  `pip_policy_arguments()` passes it as a second `-c`, so a teacher who
  clicks *Install* gets the versions this release was tested with instead of whatever is newest
  that day.  The smoke test (`addon lock`, frozen only) proves the file is in the bundle and pins
  every package `addon_manager.addon_package_names()` lists.
- **`third_party_licenses/inventory.csv`.** One list for everything the app ships or installs —
  the bundle's packages, the add-ons', the models, the build tools.  `tools/licenses/inventory.py`
  writes it: it reads the app's own lists (`[project] dependencies`,
  `addon_manager.addon_package_names()`, the `LocalModel`s, `SPACY_MODEL_PINS`), takes every
  version from the two locks, and downloads each locked wheel for its license text.  The same run
  compiles `third_party_licenses/third_party_license.txt` and the copy the GUI shows,
  `teachers_teammate/assets/third_party_licenses.md`.  `tools/licenses/policy.toml` holds the
  denied licenses (AGPL, GPL-only, non-commercial), the display names and the accepted
  exceptions; an `OR` expression is fine as soon as one alternative is.  The Add-ons page shows a
  package's license as the tooltip of its row (`infrastructure/addon_licenses.py`, reading the
  bundled copy of the inventory).  `--compile`, `--check-env` and `--check-resolved` run offline
  and are what the tests and the builds call; only the maintainer refresh touches the network.
  `docs/deployment.md` "Third-party licenses" has the flow.

`addon_lock.txt` is written at build time and is git-ignored (`teachers_teammate/_build/`); the
inventory is committed because it is a reviewable fact, not a build artifact.

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
(spaCy, the provider connectors).  `_PipInstallThread` in `settings/_addons_page.py` also
uses it when `sys.frozen` is `True`.

In development (non-frozen) builds, both code paths fall back to the standard
`[sys.executable, "-m", "pip", "install", "--upgrade", ...]` without `--target`, so packages
land in the active venv as usual.

All pip installs run one at a time (`_PIP_LOCK` in `addon_manager.py`), so Settings → Add-ons
never writes to the packages directory twice at once.

### GPU monitoring (bundled, no package to install)

The System Monitor's GPU and VRAM gauges need no add-on.  `nvidia-ml-py` (the `pynvml` module,
60 KB, BSD) is a runtime dependency in `pyproject.toml`, so it is in the bundle; AMD needs no
package at all — `gpu_detector.read_amd_usage()` reads the `amdgpu` driver's sysfs files
(`gpu_busy_percent`, `mem_info_vram_used`, `mem_info_vram_total`).  `read_gpu_usage()` answers
`None` when neither source does, and `_stats_widget.py` then shows CPU and RAM only.

There is no start-up question and no QSettings key for it.  `nvidia-ml-py` and `pyamdgpuinfo`
are in `addon_manager._RETIRED_PACKAGES`, so a copy of them in the add-on folder is removed at
start.

GPU *detection* (`infrastructure/gpu_detector.py`) reads the Windows display-adapter registry
keys or Linux sysfs first and runs `nvidia-smi` / `rocm-smi` / `amd-smi` only to complete a name
or memory size.  `nvidia-smi` is tried on `PATH` first and then at its usual Windows locations
(a packaged app's `PATH` can lack the driver folder); if it still gives no answer, `pynvml` is
asked.  AMD integrated graphics (under 1 GB of dedicated memory) is never reported.

### spaCy library

spaCy is an optional dependency (the privacy/anonymization feature).  It is installed from
pip like any other package — via the Add-ons page's name-detection tab or, for frozen builds, via
the dedicated "Install Privacy Addon" flow in `AddonInstallerDialog`.

### spaCy language models

spaCy NER models (e.g. `en_core_web_sm`) are distributed as Python wheels on
**GitHub Releases**, not on PyPI.  A plain `pip install en_core_web_sm` fails because PyPI
does not host them.

**The version is pinned, not resolved.**  `infrastructure/spacy_models.py` holds the model of
each language and, next to it, the version that model is pinned to (`SPACY_MODEL_PINS`).
`spacy_model_url()` builds the wheel URL from that pin, so the model a teacher gets is the one
this release names — not whatever spaCy's compatibility table would pick for the spaCy that
happens to be installed.  A unit test holds each pin's major.minor to the locked spaCy, so a
spaCy bump is a model bump.

`addon_manager.download_spacy_model_subprocess()` then installs it:
`pip install --target <packages_dir> --only-binary :all: --no-cache-dir -c <constraints> <url>`
(frozen builds; `--prefer-binary <url>` from source) downloads and unpacks the wheel into the
addon packages directory.

In frozen builds pip must run inside the frozen Python interpreter, so the exe is re-invoked
with `--_spacy_download_mode <url> --target <packages_dir>`:

```python
# tools/build/run_gui.py
if "--_spacy_download_mode" in sys.argv:
    _download_url = sys.argv[idx + 1]
    # call pip._internal.cli.main(["install", "--target", ..., _download_url])
```

**Two languages have no model.**  `es_core_news_sm` is GPL-3.0 and `it_core_news_sm` is
CC BY-NC-SA (non-commercial); the license policy refuses both, so Spanish and Italian documents
are anonymized with the multilingual model like any other language without one.  The language
stays in `domain/language.py`; it has no model.

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
| pip packages (spaCy, LLM connectors, …) | pip via `--_pip_install_mode` (frozen) or `-m pip` (source) | packages_dir (frozen) / venv (source) | `inject_packages_dir()` |
| spaCy language models | pip wheel from GitHub Releases | packages_dir | `inject_packages_dir()` |
| Ollama models | Ollama HTTP API (`/api/pull`) | Ollama data dir | Ollama server running |
| Built-in reader's model files (31 MB) | HTTPS from `huggingface.co`, only on request (`ModelStore.download`) | `<user data>/models/<repo>/<revision>/` | Never at start-up |

**Focus in the Check tab** (`gui/check/_text_views.py`, `gui/check/_side_panel.py`). Choosing an
item — by clicking its words in the text or its row in the list — marks it in both. The text
uses Qt *extra selections* (`CheckTextView._apply_chosen`), not HTML, so a click neither
re-renders the page nor loses the scroll position; the list marks the row with the focus
background and opens the collapsed minor-changes row when the item is in it. A click on a mark
is found with `cursorForPosition()` → `PlainMap.offset()` → the item whose span contains it,
innermost first; tests click with `qtbot.mouseClick` on a position found that way, because
emitting a signal by hand would not show what a person sees.


### Text recognition switch (`recognition_enabled`)

`Config.recognition_enabled` (default `True`, setting key of the same name, CLI `--no-recognition`)
says whether scans are read at all.  It lives on `Config`, **not** on `OcrConfig`: `OcrConfig` says
*how* pages are read and feeds `compute_ocr_config_hash` (an explicit field list), so a flag that
decides *whether* a file is read changes no cache key.

Off, the layers behave like this:

- `PreprocessService.preprocess_input(file, allow_images=False)` raises `RecognitionSwitchedOffError`
  (a `PipelineInputError`) when the loaded input has image units -- before any page is
  preprocessed.  Text files and PDFs with a usable text layer pass.  `FileProcessor` turns it into
  `ProcessingResult(ok=False, skipped=True, message=RECOGNITION_OFF_MESSAGE)`.  A file whose OCR
  text is already cached never reaches that code and runs normally.
- `run_files()` returns `RunOutcome(succeeded, failed, skipped)`; a skipped file is neither of the
  others (exit code 0).  `FileDoneResult.skipped` -> `FileDoneEvent.skipped`; the main window opens
  no error dialog for it and `ResultsTable.add_result` shows "skipped" in grey.
- `_ollama_needed` / `configured_ollama_models` / `ocr_refusal_for_run` ignore the OCR model, the
  pipeline does not check it, and `describe_data_flow` leaves the recognition stage out (no page
  leaves anywhere).  `describe_steps` forces it back on so the line under the step can still say
  what ticking it would use.
- `check_run_preflight(config, files=..., recognised=...)` asks `plan_recognition()` (images are
  scans; PDFs are opened with `pdf_has_text_layer()`, cached by path/size/mtime/mode, so call it from
  a background thread) and returns `RunPreflight.skipped`; when no file is readable it is blocked by
  `IssueKind.NOTHING_TO_READ` (fix: **Turn on**, `COMMAND_TURN_ON_RECOGNITION`).  The main window
  feeds the result to `ConfigPanel.show_recognition_note()` and `ResultsTable.set_skipped_sources()`.
- Ticking the box goes through the requirement guard like the other steps (`check_stage_requirements(
  config, "ocr")`); the stage re-run in the preview menu overrides the box.

### Finding the Ollama server

Rules in `domain/ollama_discovery.py` (pure: `OLLAMA_HOST` parsing, `/proc/net/route` and
`resolv.conf` parsing, `classify_host`, `scan_targets`), I/O in `infrastructure/ollama_discovery.py`
(`probe_server`, `discover_this_computer`, `scan_network`) and `infrastructure/wsl.py`.  The only
entry point for the GUI is `ProcessingApplicationService.discover_ollama_servers(scope)`.

- `DiscoveryScope.THIS_COMPUTER`: the `OLLAMA_HOST` address, `127.0.0.1:11434`, and -- inside WSL --
  the Windows host (default gateway, then name servers; no `wsl.exe`, that would start a
  distribution).  Windows -> Ollama in WSL needs nothing: WSL2 forwards localhost.
- `DiscoveryScope.NETWORK`: additionally the private `/24` of each card (psutil, fallback
  `gethostbyname_ex`), 64 workers, 0.3 s per address, `GET /api/version` only, no redirects, no
  proxy, stop event.  It **never runs unasked**: only the "Search the network..." button of
  `gui/settings/_server_search_dialog.py`, after a confirmation.
- A reply counts only if it is JSON with a string `version`.
- Nothing switches the address by itself.  The dialog fills the field of the *This computer* page
  (`AiSetupPage.set_server_url`); the main window runs one silent THIS_COMPUTER search per saved
  address when Ollama is unreachable and offers the hit as "Use it" (`found_server_fix`,
  `COMMAND_USE_SERVER`).
- Privacy: `is_local_ollama_url(url, include_wsl_host=True)` (used by `application/data_flow.py` only)
  counts the Windows host of WSL as this computer.  Hardware and memory probing keep the default
  (`False`): from inside WSL those figures belong to the Linux side.
