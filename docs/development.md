# Development

## Scope

This guide covers contributor workflows and the codebase. What teachers see is in the
{doc}`user guides <using_the_app>`; how models are evaluated is in `evaluations/README.md`.

## Prerequisites

- Python 3.14
- [Bazel](https://bazel.build/) — used as the build and test harness; all `bazel` commands below assume it is on `PATH`

### Set up the development environment

```bash
# A venv with every extra, installed against the pinned constraints
python tools/build/make_venv.py .venv --for dev
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

The same script builds the bundle's venv (`--for build`) and the one the tests run in
(`--for test`), so a developer environment and CI install the same versions. Running it again
recreates the venv; it refuses a folder that is not one.

## Development checks

### Linting

```bash
bazel test //:lint          # ruff, ty, shellcheck, PSScriptAnalyzer
bazel test //:py_ruff
bazel test //:py_ty
bazel run //:format.fix     # ruff format and the lint-safe fixes
```

ruff and ty come with `aspect_rules_lint` (see `MODULE.bazel`), the same ruff the formatter
uses; a version bump is a rules_lint bump. Run them through Bazel only — a `ruff`/`ty` from a
local `.venv` can be a different version and report different results.

The linters (ruff, ty, shellcheck for the `.sh` scripts and PSScriptAnalyzer for the `.ps1`
scripts, all from `aspect_rules_lint`; `bazel test //:lint` runs them; the PowerShell one is skipped
off Linux, and its rules are in `third_party/lint/PSScriptAnalyzerSettings.psd1`) cover the whole
first-party tree: the app, the tests and every folder of `tools/` (the build, packaging and release
scripts decide what ships, so they get the same gate as the app). The libraries they visit are the
one `_LINTED` list in the root `BUILD`, one per package of Python; `tools/ci/check_lint_coverage.py`
fails the lint job when a package is missing from it, so a new folder under `tools/` needs no
second list.

Size is not a lint rule: no linter sees a class or a function that grew into several jobs.
`tests/unit/test_class_size.py` is that gate — one ratchet per number, over every layer, and a unit
over its limit is listed there at the size it has.

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
bazel run //:requirements.update
```


## Architecture

The layers, innermost first. A layer imports only from the layers above it in this list
(`tests/unit/test_layer_imports.py` enforces it):

- **Domain** (`domain/`) — pure business concepts (stage names, freshness policy); no I/O.
- **Texts** (`texts/`) — every sentence a teacher reads; Qt-free, imports only `domain` and `pkg`; infrastructure and the presentation layer import it, `application` and `domain` never do.
- **Interfaces** (`interfaces.py`) — the ABC of every pipeline stage.
- **Infrastructure** (`infrastructure/`) — concrete implementations: OCR engines, LLM providers, file I/O, DOCX builder, and the pipeline orchestration in `infrastructure/workflow/`.
- **Application** (`application/`) — the services, one per topic (`run.py` for the run, `documents.py`, `commands.py`, …); they coordinate the use cases and are the only entry point for the GUI and CLI.
- **Presentation** (`gui/`, `cli.py`) — translates user interactions into application calls and renders results. Contains no business logic, and imports `pkg`, `core`, `domain`, `application` and `texts` only.

The presentation layer never imports `infrastructure`. The modules that wire concrete classes
are the exception, and the only one: `gui/composition.py`, `gui/__main__.py`,
`gui/benchmark/composition.py`, `gui/benchmark/__main__.py`, `cli_composition.py`,
`__main__.py` and `frozen_modes.py`. `tests/unit/test_layer_imports.py` holds both rules, and a
third: a module publishes only the names it defines, neither with `from … import Name as Name`
nor as an `__all__` entry it imported from another layer (a subpackage's `__init__.py` lists the
names of its own modules, which is that package's API) — a name has one home, and a caller
imports it from there.

A service in `application/` does not reach into `infrastructure` from a method body. What it
needs of a module comes in through its constructor, typed by a `Protocol` in
`application/ports.py` (`OllamaServer`, `AddonState`, `HardwareProbe`, `NameModels`);
`application/composition.py` hands over the class of the running app, a test hands over a fake.
What still needs a lazy import — an add-on package that must not be imported at start-up, a
module that imports back — carries `# noqa: PLC0415`, and the count of those in `application/`
is the ratchet of `tests/unit/test_lazy_import_budget.py`. The modules that still import
`infrastructure` at all, and how many names each imports, are pinned the same way by
`tests/unit/test_application_infrastructure_budget.py`: a store, the pipeline or a subprocess the
service starts. A pure rule is never among them — it is imported from `domain` or the core.
`application/composition.py` is not in the table: naming the concrete classes is its job.

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

### Glossary: the code's word, the teacher's word

Both are in use, and neither renames the other: the code keeps the word the pipeline was built
with, `texts/` and the interface use the one a teacher knows.

| Code | What a teacher reads |
|---|---|
| correction, corrector, `Stage.CORRECTION` | proofreading — each change explained |
| evaluation, evaluator, `Stage.EVALUATION` | content review — remarks on the text as a whole |
| anonymization, anonymizer, `AnonymizationMap` | removing personal info: `[PERSON_1]` in place of a name |
| OCR, `Stage.OCR`, recognition | text recognition — the write-up read into text |
| review | three things: the *correction review* is the explanations of one proofreading (`domain/proofreading/review.py`), the *content review* is the evaluation, a *stored review* is what a run stored about a file, which the Check tab reads back. The module says which |
| run | one start over the selected files; the Benchmark app calls the same thing a *batch* |

### Where text lives

Every sentence a teacher reads is *defined* in `teachers_teammate/texts/` and *used* by
reference everywhere else. The package is Qt-free, so `gui/`, `cli.py` and
`infrastructure/` import it; `application/` and `domain/` do not. One module per surface:

| Module | Owns |
|---|---|
| `texts/_types.py` | The shapes: `Notice`, `Question`, `Choice`, `FilePrompt`, `Answer`. Text and nothing else. |
| `texts/dialogs.py` | Every message box, question and file picker. |
| `texts/settings.py` | Settings page titles, the `WHERE_*` "where do I change this" sentences, tab names. |
| `texts/privacy.py` | The "what is sent for this file" wording, and what each privacy level and each place means (the levels, the places, the advice, the tooltips of the data-flow summary). |
| `texts/check.py` | What the Check tab's widgets say around its text: the two editable views' tooltips, the ⋯ button, the source dialog's button, the scan switch, the strips, the side panel's tabs and its filter row. |
| `texts/cli.py` | The command line's own words: the arguments' help and what it prints while it works (the download counter, the data-flow block). |
| `texts/report.py` | What a run writes to its log: the banner, the progress lines under each file and the closing block. |
| `texts/fixes.py` | The readiness line's fixes: the line for what is missing and the label of the button that fixes it. |
| `texts/main_window.py` | The main window itself: the input and results rows, the step switches, the panel's buttons, the results table and its menu, the export entries, the System Monitor. |
| `texts/credentials.py` | The Settings list of access keys (API keys): its buttons, placeholder and the notes under the group and each key. |
| `texts/menus.py` | The main window's menu bar: the four menus, their entries and what each entry offers. |
| `texts/app.py` | The app's name, the update banner, and the Help, About and licence windows. |
| `texts/benchmark.py` | The Benchmark window: the document, the configuration, the runs list and the comparison. |
| `texts/setup.py` | Every sentence of the Setup Assistant's pages. |
| `texts/issues.py` | One sentence per unmet requirement of a step or a run, worded from a `RequirementIssue`. |
| `texts/run.py` | What a run says: the battery warning, a file it cannot use, a stage that failed, the output files it could not write, where they go. |
| `texts/servers.py` | An Ollama server: its card, the models it offers and what it answered. |
| `texts/review.py` | The review and the unclear expressions, the warnings of the proofreading and review requests, the prompt dialogs, the image-preparation caption, the settings wording that is not a page name, what is sent for a file, how a stored result was sent, the speed test, and the Check tab's panels and marks. |
| `texts/models.py` | This computer, the suitability of a model, the download warnings, the model list, the labels of the recognition and model fields and the status line under a model field. |
| `texts/addons.py` | The reading engines, the downloads of the built-in reader and the page models, and what the Add-ons page says during an install. |
| `texts/messages.py` | The English wording of every `MessageKey`: one renderer per key, installed when `texts` is imported. |

`texts/__init__.py` is the map — a docstring naming which module owns what, no logic.

Wording that is built from application data (a stored result's `SentRecord`, an install
failure, a model rating) lives here too: the function takes that data as an argument and
returns the sentence — `sent_lines(status, stage, location)`, never a lookup of the service.
So `texts` imports `domain` and `pkg`, nothing else.

Rules:

- **Dependencies point inward.** `texts` may import `domain` ids and enums; `domain` and
  `application` never import `texts`. `infrastructure` does, for the warnings and reasons it
  puts into a result and the lines it prints; every other layer returns an id and the caller asks
  `texts` for the words.
- **A failure carries a `Message`, not a sentence.** `teachers_teammate/messages.py` holds the
  `MessageKey` (the name of a sentence, never changed once released), the `Message` (a key and
  plain data: text, numbers, other messages), `message_of(exc)` and one factory per key, named
  and parameterised like the `texts` function that words it. `infrastructure` raises
  `OCRError(download_damaged(name))` and passes a caught error on as `message_of(exc)`.
  `str(message)` gives the sentence once `texts` is imported, and `"key: args"` before.
  `tests/unit/test_messages.py` holds the factories, the renderers and the golden key list
  together and fails on a caught error handed to a factory as text.
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

**The ratchet test.** `tests/unit/test_user_text_centralised.py` parses `gui/`, `cli.py`,
`application/`, `infrastructure/` and `domain/` with `ast` and fails on a string literal (or
f-string) handed to a sink that shows text. The sinks are the tables at the top of
`tools/user_text/inventory.py` (`_QUALIFIED`, `_SIMPLE`, `_MARKED`, `_KEYWORDS`,
`_FIELD_KEYWORDS`): widget calls, dialogs, the readiness line's fixes, a line sent to a log, a
CLI line, a prose keyword, a raised exception. It also fails on a module constant whose name says a
teacher reads it (`_CONSTANT_SUFFIXES`, and for a one-word value `_SHORT_SUFFIXES`; a list, tuple or
dict constant is read item by item), on a sentence handed to a function imported from `texts`
(`dlg.copy_failed("Could not copy")` — the sentence belongs in the function; a word without a space
is an id and passes), and on a template of `texts` filled at a call site with `.format`. A sentence
that is only *returned* or kept in a local variable is not a sink, so the test cannot see it:
build it in `texts/` and call it. What is not a sink is listed in the same module: a `raise` that
states an invariant of the code (any raise in `domain/`, `INVARIANT_MESSAGES`), the step reasons of
`infrastructure/preprocessing/` (debug log), the constants in `NON_TEXT_CONSTANTS`, and the
Word file's label tables. A test fails on an entry of either list whose literal is gone. The allowlist
`tools/user_text/allowlist.py` is empty: the test fails on any literal it sees at a call site, and a
second test fails on an entry whose literal is gone, so the answer is to put the sentence in
`texts/`, not to add an entry. `tools/user_text/inventory.py` scans the same sources and writes
a CSV of what it found (`bazel run //tools/user_text:inventory -- --out user_text.csv`).

**Out of scope, and must stay where it is:** the LLM prompts in
`domain/correction_prompts.py` (versioned and hashed into the protocol fingerprint and the
cache key — moving text changes cache keys), the developer log (`logging`, `logs/`), developer
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
  answers the OCR discovery queries through the services it is given and adds
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

The static defaults (`DEFAULTS["ocr_model"]` / `correction_model` / `evaluate_model` and
the per-task provider defaults in `_model_defaults.py`) all come from
`DEFAULT_OCR_MODEL` / `DEFAULT_WRITING_MODEL` in the catalog module. They are independent
of the hardware profile — the per-profile `RECOMMENDED` lists only drive the GUI
recommendations — so a default may not fit a small computer.

`structured_output=True` gives a writing model the `explanations ✓` badge; it never switches
the proofreading mode. `DEFAULT_WRITING_MODEL` and the first entry of every
`RECOMMENDED["general"]` list must carry the flag — a test enforces it. The flag is not
guessed: `tools/verify_model_catalog.py --structured-output` (gate 1) checks every `general`
model that is reachable through a local Ollama server and reports the value to enter in the
catalog by hand, like the download sizes. A mismatch against the catalog counts as a failure. A
model without the flag (`mistral:latest`) is offered and used like any other model, but has no
badge and is not recommended.

Gate 1 checks whether a model fills in a JSON answer format reliably (the process it belongs to is
in `evaluations/README.md`).  It sends the one-request review of the evaluation baseline
(`tools/evaluation/run/one_request/`): the system prompt at level B2 in the text's language, the
user message, the JSON schema as `format`, `think=False`, the model's step-① temperature and
`num_ctx` 8192.  A test pins the request to the one-request corrector's.  `--temperature` overrides
the temperature for every model named.

Six invented fixtures (`tools/evaluation/checks/gate1_fixtures.py`): plain English, German and
French, Markdown with formatting marks, one full page of dense German, and one German page that is
the longest prefix of whole sentences the budget allows.  German tokenises longest, so the two long
fixtures are the worst case.  The size is the corrector's own budget
(`structured_check.review_budget`, pinned to `NativeOllamaCorrector.max_text_chars`); a fixture
longer than its budget is reported as too long instead of being sent, and CI keeps the shipped
fixtures within it.

A run is judged in this order, the first failure wins: too long (hard), request failed (hard), cut
off (`done_reason == "length"`, hard, whether or not the answer still validates), invalid, no
sentences, unchanged, too few sentences placed, partial coverage of a long fixture, too few
explained (all soft).  Above temperature 0 every fixture runs three times (18 runs).  A model passes
with at most one soft failure; with exactly two the check runs 18 more and passes with at most four
over both rounds; any hard failure fails it in either round.  A pass also needs the explained share
over all runs to reach `CheckRule.explained_overall`.  At temperature 0 a repeat is the same answer,
so each fixture runs once and there is no second round.  The numbers are the fields of `CheckRule`
(`tools/evaluation/checks/check_rule.py`).

Each run prints placed / explained / orphans / covered / prompt and answer tokens / margin /
`done_reason` / seconds. The **margin** is what the window had left after the request and the
answer; it is not a verdict, but the summary names the smallest one on the budget fixture and
warns below a tenth of the window, because such a model passes with little room to spare.

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
profile (compact JSON on or off), temperature, window, exchange and steps. A full gate-1 run is the
expensive way to find out whether a setup works at all. `verify_model_catalog smoke` sends gate 1's
own requests, cheap to expensive, one run each, and stops at the first fixture that fails twice: at
most ten requests, a few seconds to a few minutes. It is gate 1's first stage, not a verdict — a
pass means "no sign of a systematic fault", never "passes gate 1".

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
Ollama's llama.cpp runner shifts its context and keeps writing, and the app sends no
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
`evaluate run` smokes each setup once before its first session.  The screen is the one the setup
sends: `one-request-review` gets gate 1's own check, `text-first` step ①'s request on the three
plain fixtures (a comparison sends plain text only, so text mode's Markdown fixture and prompt
would skip a model for a fault the run never meets), and `one-request-text` text mode's request on
its three short fixtures.

The window is `--num-ctx`, 8192 by default: the window the app uses for proofreading, always. A
model the automatic rule would lift above the checked window on a tier's card is noted in the
header; while proofreading is capped at the base window that note does not fire, and it is the
reminder to check the larger window when the cap is raised.

A verdict belongs to the **exact tag** it was measured on. The app sends only the temperature and
`num_ctx`; `top_k`, `top_p`, penalties and the chat template come from how the tag was packaged
(the official `qwen3.5:9b` ships `top_k 20`, `top_p 0.95`, `presence_penalty 1.5`; the same model
imported from a `hf.co/unsloth/…` GGUF ships none of them). The check prints the settings a tag
brings along, so a result can be traced. Check a Hugging Face build as its own tag; do not
transfer a verdict between packagings.

The check talks to `$OLLAMA_HOST` (else `127.0.0.1`); `--ollama-url` overrides it. (The app's own *Settings → This computer → Find…* finds the Windows host for you, see "Finding the Ollama server".) From WSL in
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

A writing model that can also read scanned pages lists `also_uses=frozenset({"ocr"})`;
`catalog_uses()`, `suitability()` and `models_for()` then treat it as a reading model as
well (Settings → Add-ons still lists it once, under its primary category). Entries
whose tag starts with `hf.co/` are pulled from Hugging Face, so
`tools/verify_model_catalog.py` checks them against the Hugging Face file list instead
of the Ollama registry.

Every entry also names the terms its weights come under (`OllamaModelSpec.licence`, a
`ModelLicence` with the name, a link and — only when the licence restricts *use* — one sentence).
The app never ships a model, so a permissive licence (MIT, Apache-2.0) adds nothing a teacher has
to know; a restrictive one does.  That is the default reading model only: Chandra OCR 2's
weights are under a modified OpenRAIL-M licence: free for personal use and research, and for
other use only below $2M in revenue or funding; schools are not named, so the note asks a school to
check the licence.  `licence_note(tag)` returns the
sentence; the Add-ons model tree shows it in its *License* column and the Setup Assistant in the
tooltip of the download box.  The facts were read from each model's licence on 2026-11-18 — check
them again when a model is added or a tag moves to a new version.

### Model sizes: what a model needs on the card

`OllamaModelSpec.memory_gb` and `context_gb_per_8k` say how much a model needs at 8,192 tokens and
how much it grows per further 8,192. `model-fit/sizes.jsonl` in the private evaluation repository
holds the measurements they come from: one JSON object per line, append-only, numbers only. The fields are documented on `FitRecord` in `tools/evaluation/checks/model_fit.py`;
machines are in `tools/evaluation/core/machines.py`.

**One record per model, machine, device and window.** It carries what Ollama reports from `/api/ps`
(`size`, `size_vram`, the window it really loaded) and what the card really held, from the memory
agent's window around the load (`idle`, `peak`, `end`, the card's total). Every figure is a raw
byte count; the reports divide by 10⁹, the unit of the catalog's sizes. Nothing derived is stored:
the card's increase, the need, the growth and the tier windows are computed when the file is read,
so a change of the rule costs no new measurement. The record is `format: 3`; a line of another
version is refused, not guessed at.

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
bazel run //tools:verify_model_catalog -- fit --store ~/git/teachers-teammate-evals \
    --machine rx9070xt-server \
    --ollama-url http://gpu-server.example:11434 --gpu-agent http://gpu-server.example:11436 \
    --gpu-token-file ~/.config/gpu_agent.token --tag gemma4:e4b --tag qwen3:4b
```

The request is a copy of a short German text (about 170 tokens), long enough to read the model's
speed from: every record stores the answer tokens and the generation time (`tokens_per_second`), and
the report shows the speed next to Ollama's own split between card and processor
(`ollama_card_share`, the share the app's full-speed rule is about), so the rating's share
thresholds can be checked against measured speeds.

`--cpu-only` sends `num_gpu` 0 and measures at 8,192 tokens only: the speed of the processor, for
the tier that has no card. The record's `device` is then `cpu`, and the size lookups (`latest`,
`size_for_decision`, `catalog_lines`) skip such records, because the card's share is missing from
their need.

`--machine` is an id of the store's `machines/`, and it is required when measuring: what the id
stands for is written once in that file, because Ollama's API cannot report it. An id the registry
does not hold is refused, naming the known ones. A step already stored for the same build and
version is skipped (`--again` measures it again). The ladder stops for a tag when Ollama loaded a
smaller window than asked for (the model's own maximum) or when the request fails; a failing tag
does not stop the next one. Exit codes: 0 every step measured or already stored, 1 a failure, 2 the
server or the agent cannot be reached.

**Reading it back, without a server.** `fit --report` prints, per model, machine and device, the
need at each window, Ollama's figure next to the card's increase, the growth per 8k (short and long
span, flagged when not linear), the difference to the catalog's `memory_gb` (flagged above
`MEMORY_TOLERANCE`, 10 %), and the largest window that still fits a card of a given size
(`--fit-limit`, repeatable; default `8gb` and `16gb`). A record whose `ollama_version` differs from
the one the machine runs now is marked as another backend, not hidden. `--compare A B` puts two
machines side by side and marks each figure that disagrees (need within 10 %, growth within 20 % or
0.1 GB). `--catalog` prints the two values to enter by hand, taking the larger of two machines that
disagree.

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
(`tools/evaluation/run/gpu_agent.py`) reports what the card really holds, sampled from the driver, so a
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
python3 tools/evaluation/run/gpu_agent.py --token-file ~/.config/gpu_agent.token --port 11436
```

On Windows it runs in the Windows session, not in WSL: WSL's `nvidia-smi` sees the card but reports
0 MiB used. The token file holds one secret; the agent refuses every request without it
(`X-Token`), and the tools read the same file (`--gpu-token-file`). The port has to be reachable
from the machine that runs the tools, so a firewall rule for it is part of the setup. The agent
never writes anything, never loads or unloads a model, and never touches the server: it only reads
the driver and the main memory.

`AgentClient` (`tools/evaluation/run/agent_client.py`) is the other side: `info()`, `open_window()`,
`close_window(window)` and `settle()`, which waits until the card's use has stayed within a
tolerance for several readings in a row (and at least a short while) before a measurement
starts; the driver can give memory back a second after the server reports a model unloaded. `fit` and the smoke check's size limit are its two users.

### Rating a model against the computer's memory

`suitability()` / `rate_model()` rate a catalogued model for a task on a computer, from what
the app detected there (graphics memory, total main memory) or, when the profile was chosen by
hand, from the size the profile stands for.  `memory_gb` of a catalog entry is the total memory
the model needs at the proofreading window of 8192 tokens (card and main memory together),
measured where possible (*Model sizes*, above).

| Quantity | Rule (constants in `domain/model_catalog.py`) |
| --- | --- |
| Card budget | `CARD_USABLE_SHARE` (85 %) of the card's memory; Apple Silicon 66 % of the unified memory (`HardwareDetection.card_gb`); none without a card |
| Reserve | the larger of `SYSTEM_RESERVE_MIN_GB` (8 GB) and `SYSTEM_RESERVE_SHARE` (20 %) of the total main memory; swapping to disk is never counted as memory |
| Works (`WORKS`) | at least `FULL_SPEED_SHARE` (0.9) of the model fits the card; without a card, up to `CPU_COMFORT_GB` (5 GB: a page in under five minutes on an ordinary processor, measured with `fit --cpu-only`) |
| Slow (`SLOW`) | the part that does not fit (the *spill*) fits in main memory beyond the reserve; `ModelRating.very_slow` when under `SLOWER_SHARE` (0.7) of the model fits the card, or there is no card |
| Does not fit (`TOO_LARGE`) | the spill does not fit; also whenever the main memory is unknown (a remote server) and the model is not within the card |
| Recommended (`BEST` / `GOOD`) | the tier's list, but never for a model that does not fit the computer's known memory |

The two share thresholds come from speeds measured on one 8 GB card and one processor; they are
ratios, so they scale with the computer.

`RunService._memory_shortfall()` is the run-time counterpart, used by
`check_run_preflight()` (both adapters) before a run: for an Ollama on this computer,
the part of every catalogued model that does not fit the card (all of it on Apple Silicon) has to
fit the main memory that is free *right now*, minus `RUN_MEMORY_MARGIN_GB` (2 GB).  It reads
`ModelRecommendationService.available_memory_gb()` (`psutil`'s `available`, never swap).  It is a
**warning**, not a refusal: free memory changes from run to run, so the GUI offers "Run anyway" and
the command line needs `--ignore-memory-check`.  `check_run_preflight()` therefore runs the memory
check last, after the stage requirements (reading, anonymizing, correcting, evaluating), and reports
a memory warning and a blocking issue separately.  A model that is not catalogued, a remote server
and an unreadable memory give `None`.  It rates the models through
`resolve_hardware_profile()`, so a hand-picked profile counts as it will at run time, and at the
window the run really gives the model (`memory_at_context_gb`).  The wording of all this is in
`texts/models.py`.

The check behaves as it would after Ollama unloaded every model, which is the case it has to warn
about anyway, since Ollama unloads idle models to make room.  On Apple Silicon the whole model
counts against main memory; "Run anyway" covers a warning that turns out too cautious.

### Speed estimates: the test, the real runs and the time left

Three questions, three answers, one owner each:

| Question | Answered by | Where |
| --- | --- | --- |
| How fast is this computer? | the speed test, when the teacher asks for it | `infrastructure/performance_probe.py` |
| How long will a run take? | the estimate from the store | `application/speed_estimates.py` |
| How long is left? | the pace of the run so far | `gui/_run_status.py` |

No widget computes a duration: `texts/review.py` formats the figures the service returns, and
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
`from_config` (the time left of a run, `SpeedEstimateService.choices_for_config`) leaves
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
| `ExchangeCost(requests_per_page, answer_tokens_per_100_words, text_copies)` | what an exchange costs per step; `DEFAULT_EXCHANGE_COST` holds the text-first exchange's provisional figures (① 1 copy, ② 2, ③ 1) |
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
the two would make the answer length meaningless.  Samples under any other mode are ignored.

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

#### Which models have reference figures

Only the catalog entries with a `reference_speed` get an expected band, and no entry has a
`read_tps`: a reading model gets no expected band, and `MachineFactors.reading` stays `None` until
reading models are measured on the reference machine.  `tools/dev/ocr_page_tokens.py` measures them,
and `report --performance --catalog-lines` prints the `answer_length` lines for the writing models.

### Who owns what (hardware detection and recommendations)

| Piece | Where | Rule |
|---|---|---|
| Decisions (best model, suitability, hardware profile, `rate_model`) | `domain/model_catalog.py` | Pure functions, no I/O — table-test them. |
| Caches (local hardware probe, model classification) | `application/model_recommendations.py` | One owner. Call `on_ollama_models_changed(url)` after every pull; it evicts the model list, the classifications and the server's speed facts. Only definite classifications are cached. The memory and GPU readers and the context choice live here too (`available_memory_gb`, `read_gpu_usage`, `context_choice`). |
| Texts a teacher reads | `teachers_teammate/texts/` | Qt-free; widgets never build these sentences themselves. See "Where text lives" above. |
| Widgets | `gui/_hardware_profile_selector.py`, `gui/_model_guidance.py`, `gui/settings/_addons_page.py` | Presentation only. They rate models through `rate_ollama_models(..., cached_only=True)` — never contacting Ollama on the GUI thread. |

Details that are easy to get wrong:

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

- **Each provider module declares its package** in `PROVIDER.package`
  (`AddonPackage(module="langchain_mistralai", pip="langchain-mistralai")`); Ollama's connector
  is `package_optional` because the app talks to Ollama directly.  The Add-ons tables in
  `addon_manager.py` are built from these declarations.  `ProviderService`
  (`application/providers.py`) answers `missing_provider_package()` and
  `list_provider_packages()` (providers sharing a package appear once), so the pages,
  Add-ons and the run preflight need no list of their own.
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
- **Every provider declares where it processes data** in `PROVIDER.data_region`:
  `eu`, `non_eu` or `unknown`.  An undeclared region counts as `unknown`, which is shown
  like "outside the EU" — a school can only rely on what a vendor actually declares, so
  "not declared" must never read as "EU".  `PROVIDER.vendor_country` is optional
  and only fills the tooltip.  A provider whose region depends on the settings (a custom
  base URL, a regional endpoint such as `api.eu.mistral.ai`) adds a module function
  `data_region(**settings) -> str`; `llm_factory.get_provider_data_region()` prefers it
  over the key and returns `"unknown"` for anything missing, unrecognised or raising.
  `application/data_flow.py` turns the answer into `StageLocation.region`
  (`domain/data_location.py:DataRegion`), which feeds `PrivacyLevel` and the
  "don't ask again" fingerprint.

### Finding the Ollama server

Rules in `domain/ollama_discovery.py` (pure: `OLLAMA_HOST` parsing, `/proc/net/route` and
`resolv.conf` parsing, `classify_host`, `scan_targets`), I/O in `infrastructure/ollama_discovery.py`
(`probe_server`, `discover_this_computer`, `scan_network`) and `infrastructure/wsl.py`.  The only
entry point for the GUI is `OllamaServerService.discover_ollama_servers(scope)`.

What an address *is* — its normal form, its host, whether it points at this computer, whether a
model name is installed — is `domain/ollama_address.py`, pure and reachable without a lazy import.
It takes this computer's names as lookups, so the cheap loopback check answers the default address
without a socket or DNS call.  Both lookups are required arguments; `OllamaServerService.is_local`
and `ollama_utils.ollama_server` pass `own_host_names` and `windows_host_addresses`.

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
- Privacy: `OllamaServerService.is_local(url, include_wsl_host=True)` counts the Windows host of
  WSL as this computer; the rule itself is `domain/ollama_address.py:is_local_ollama_url`.
  Hardware and memory probing keep the default (`False`): from inside WSL those figures belong
  to the Linux side.

### Remembered "don't ask again" answers

`gui/_privacy_settings.py:FlowAcknowledgements` is the only code that reads or writes them
(QSettings key `privacy/acknowledged_data_flows`, one line per answer: its fingerprint).  The
question before a run, the list in Settings → Privacy and "Ask again" all use it.  The line the
list shows is put into words when it is read, by `texts/privacy.py:describe_fingerprint()`, so
no wording sits in the settings file.

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
  correction, evaluation_prompt)` walks `DataFlow.outbound` (the steps that leave the
  computer) and returns a frozen `SentText` per step: proofreading gets the extracted text,
  content review the proofread text, each anonymized first when the step says names are
  hidden.  A step whose names are hidden needs `anonymize`; without it the text is reported
  as written, because hiding it is impossible.
- The function keeps an internal `hidden()` memo, so every distinct text is anonymized at
  most once.  The proofreading section, the content-review section and the comparison view
  therefore share one spaCy pass, and the shared cache behind `anonymize`
  (`application/anonymization.py`, see *Shared anonymization cache*) answers the second call.
- `preview_extracted` asks for the anonymized extracted text as well, for the dialog's
  original-vs-anonymized tab; the service passes `config.anonymization_enabled`, so the tab
  appears exactly when names are actually being hidden.
- `DocumentQueries.describe_sent_texts()` supplies `anonymize` (the caching
  preview anonymizer, `None` when removal is off) and fills `correction` and
  `evaluation_prompt` with the same code the stages use (`_sent_correction` /
  `_sent_evaluation_prompt`), so the dialog cannot drift from what a run sends.  A `SentText`
  carries the requests as data — the `(step, system prompt)` pairs and the teacher's notes —
  and the dialog words them through `texts/review.py:prompts_block()`.  `SentTexts` carries the
  sections, `flow.level`, the raw text and the anonymized raw text.  Its `stored` argument is the file's `DocumentStateView.sent`
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
stage line, how the stored result was sent (`sent_lines()` of the row's `SentStatus`, left out
while the run is redoing that stage) and the cell's note with blank lines, so none replaces
another.  `set_stage_tooltips()` redraws every row, so the cells follow a settings change.

### How a stored result was sent

The tooltip and the dialog answer two different questions: *where would the text go now*
(`DataFlow`, from the current settings) and *where did the stored result really go*
(`SentRecord`, from the run that made it).  The second one has to survive a settings change,
so it is stored with the result:

- `domain/data_location.py:SentRecord` holds the **inputs** of the classification, not the
  verdict: `provider`, `model`, `endpoint` (the Ollama host; empty for a provider, none of
  which has a base-URL setting), `names_hidden` (the number of placeholders), `hiding_on` (whether hiding was switched on,
  which tells "nothing found to hide" from "names as written") and `privacy` (the fingerprint
  of the anonymizer settings).  A changed rule therefore changes
  the wording, not the stored data.  `from_dict()` returns `None` for anything that is not
  one of ours — including a record without a provider, which would otherwise read as an
  unnamed online service.
- `SentStatus` pairs the record with `other_privacy_settings`, the comparison with the current
  `privacy_key(config)` of `cache_keys.py`.  The comparison is by settings, not by text: working
  out the text would need a spaCy pass per document.
- `cache_keys.py:sent_record(config, stage, *, names_hidden, facts)` builds it (it holds the
  `Config`); `application/data_flow.py:sent_location(record)` classifies it through the same
  `llm_location()` the box uses, so the two cannot disagree.
- `DocumentState` keeps `correction_sent_text`, `correction_sent_map`, `correction_sent` and
  `evaluation_sent`; `DocumentStateView.sent` exposes them keyed by stage name.
  `DocumentQueries._sent_verdict()` (`application/documents.py`) adds the comparison in
  `_documents()` (behind `queue_rows()`) and `load_view()` — the state repository does not know the
  settings.  The current fingerprint is computed lazily, only for a view that has a record.
  `sent_location()` writes a bare IPv6 host (`::1`) in brackets before it asks `llm_location()`.
- The record belongs to its result: every path that clears a result clears its record too
  (`record_ocr_result`, `invalidate_from_stage`, `record_correction_result` for the review).
  The teacher's edits never touch it: they go to `TeacherWorkStore`.  `load_valid()` drops sent fields
  whose result is not stored (`_without_orphan_sent`) and keeps the recognised text, so a
  stray record never costs a new reading; `DocumentStateView.sent` only lists stages that are
  done, so a stage `reconciled_view()` marks stale shows no line.
- The wording lives in `texts/review.py` (`_how_sent` builds the phrase once; `sent_lines`
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
(`workflow/coordinator.py`), `OCRPipeline.run()` and `RunService.run_selected()`
return it; `OCRWorker.run_finished` carries it to the GUI, and `gui/_run_status.py:describe_run`
holds all the wording (progress text, tray message).  A stopped file is neither a success nor
a failure, like a skipped one: `ProcessingResult.stopped` -> `FileOutcome(status=STOPPED)` ->
`FileDoneEvent`, shown as a grey row with "stopped" in the cut-short step.

Rules that keep a stop honest:

* **A finished step is never thrown away.**  Each step stores its result as soon as it has
  it, so a stopped file keeps it and the next run reuses it.  A request cut off by the stop is
  never stored as a result: `RunStoppedError` leaves the step before anything is recorded.
* **The event is the truth, `RunStoppedError` is the fast exit.**  It is not an `OCRError`
  on purpose.  The Ollama wrappers in `_llm_invoke` re-raise it instead of degrading it to a
  warning (any other error still becomes one, and is a failure even when Stop now was pressed
  at the same moment); a new wrapper that catches every exception must do the same.
  `FileProcessor` checks the event between steps (`_stopping()`) and **before** a slow
  anonymization pass, and the steps raise `_stopped_result(message, stage, ...)` themselves.
  The stop carries the stage (`ProcessingResult.stopped_stage`, one of `ocr`, `correction`,
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
is `RunService.run_selected()`, around the whole `pipeline.run()` (GUI and
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

There are two kinds of background work, one module each.

**A query or a fetch** — no window waits for it, and only the newest answer counts — uses
`gui/_background.py`:

```python
self._task = LatestTask(owner=self)            # "latest wins" per widget
self._task.submit(lambda: service.slow_call(), self._on_done)   # on_done(result, error)
run_streaming(fetch, on_item, on_done, owner=self)              # items as they arrive
```

Tasks run on a shared `QThreadPool`; results arrive on the GUI thread through a queued
signal, results for a destroyed `owner` are dropped, and superseded results of a
`LatestTask` are ignored. There is no thread object to destroy, so closing a dialog while
a task runs cannot abort the process. The main window waits a bounded moment for running
tasks when it closes (`wait_for_background_tasks`). A task that waits on a remote server (model
lists, the update check, a server search) passes `network=True` and runs on a second pool: it
cannot be interrupted, so a stale one keeps its thread until it ends, and it must not hold up the
local tasks meanwhile. In GUI tests, `neutralize_gui_threads` keeps a smoke test offline: it
patches `LatestTask.submit` and `submit_streaming` and the `run_in_background` of the modules that
call it directly.

**A long job** — a pip install, a model download, an Ollama pull: it reports while it runs, its
thread must not be destroyed before it ended even if the window that asked is closed, and it can
be stopped only between two chunks — subclasses `LongJobThread` (`gui/_long_job.py`) and
implements `work()` alone:

```python
class _PipInstallThread(LongJobThread):
    def work(self) -> None:                     # the result travels with finished_ok
        self._svc.addons.install_packages(self._packages, self.log_line.emit)
```

The base carries `log_line`, `progress`, `finished_ok`, `finished_err`, a stop event that
`work()` watches where it waits, and `stop()`. A job that ended unfinished because it was stopped
raises `ModelDownloadCancelledError` from `work()` and reports `ABORTED`; one that finished anyway
reports its result. It holds every started job until it has ended, so a window may let go of
one that is still running — destroying a running `QThread` aborts the process — and the
application waits a bounded moment for them when it closes (`wait_for_long_jobs`).

A `QThread` subclass of its own is refused by `tests/unit/test_thread_kinds.py`. The two that
stay are `OCRWorker` and `BenchmarkWorker`: a run of their own, with a worker thread that has
to be stopped and detached as a whole.

### The evaluation tools

Three command lines, one job each.  They parse arguments and print; the work is in
`tools/evaluation/` (see *Where the code lives*).

| Tool | Commands | Reads | Writes |
| --- | --- | --- | --- |
| `tools/evaluate.py` | `run` | the test set in the store, a running Ollama server | answers, appended to `runs/` below `--store` |
| `tools/report.py` | `table`, `compare`, `gate`, `passes`, `sheet`, `page` | the store | the report folder (`page`); the rest prints |
| `tools/store.py` | `convert`, `validate`, `marks`, `accept-marks`, `manifest` | the store, the corpora, a filled-in sheet | the marks files, the accepted alternatives |

`prepare_gec_testset.py` builds a test set into the store and is not one of the three: it reads the
corpora and writes `testsets/<id>/`.

**The store is a repository of its own.**  Every evaluation record — the runs, the test sets with
their texts, the marks, the machines, the model sizes, the gate records — lives in the private
repository `teachers-teammate-evals`, cloned to `~/git/teachers-teammate-evals`, which is what
`--store` names (every command takes it; none guesses it).  It holds the corpus text, so it may
never be published; this repository holds the code and the formats (`evaluations/README.md`, `evaluations/FORMAT.md`) and no record.
`core/paths.py:store_path()` refuses a store inside this repository's working tree, and
`tests/tools/test_no_eval_records.py` fails when a run file, a manifest, a size record or a marks
file appears here.  That repository is a Bazel module that depends on this one (a sibling clone,
`local_path_override`), so the tools also run from there: `bazel run //:report -- table`,
`//:store`, `//:evaluate`, `//:prepare_gec_testset`, `//:verify_model_catalog`.  This is why the
`ocr_test_dependencies` pip hub is not a dev dependency here.

**A run is named by its axes.**  `core/axis_table.py` declares every attribute a run can vary in
once, and `core/axes.py` derives the four keys from it: the **compare key** (what two runs must
share to be pooled — the task, the test set and every axis away from its default), the **series
key** (the compare key plus the machine and isolation: one gate group), the **run key** (the series
key plus the repeat: one file) and the **environment key** (the compare key plus the machine, the
server version and isolation: the speed table).  The file name is a label derived from the axes and
never read back.

**Adding an axis.**  One `Axis(...)` line in `AXES` and one field on `Protocol`; the record codec,
the file name's tags, the labels and `compare --along` all iterate `AXES`, and a test asserts the
two agree.  An axis with a default moves no key, no sample, no cache and no file name, because a
value at its default is left out of the compare key.  `compare_role` says how a comparison treats
it: `MODEL` (two values are two models to compare), `FIXED` (it may not vary inside one comparison)
or `DERIVED` (another axis determines it).  An axis a newer writer added and this reader does not
know is kept under `unknown.<name>`, so two runs that differ in it never pool by accident.

### Request profiles: temperatures and prompts per model

A writing model must pass two gates before it is recommended (`evaluations/README.md`).  **Gate 1**
is the structured-output check above (`verify_model_catalog.py`); its first stage is the smoke
check: a setup that fails it is not sent the eighteen runs, and one that passes it is continued
from its own answers.  **Gate 2** (`evaluate.py` runs it, `report.py` reads it) asks how good its
corrections are.  It sends what the app sends, at the temperatures the app sends that model (it
builds the app's own corrector — the proofreading exchange inside the app's `ChunkingCorrector`, with the
app's own tokeniser and word diff — and the one-request corrector for the two one-request
exchanges), so a result describes the request a teacher's run sends, including the app's long-text
path: a text longer than the window's budget is proofread in pieces, one request each, and the
answer line records how many (`pieces`).

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

```bash
# 1. Build the test set from the public corpora (kept outside the repository)
bazel run //tools:prepare_gec_testset -- --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json --store ~/git/teachers-teammate-evals
# 2. Send every text to the models (answers are appended; an interrupted run resumes)
bazel run //tools:evaluate -- run --store ~/git/teachers-teammate-evals --machine rx9070xt-server \
    --testset gec-2026-12 --model gemma4:e4b
#    ... and ask for the corrected text only (no JSON, no explanations) on the same texts
bazel run //tools:evaluate -- run --store ~/git/teachers-teammate-evals --machine rx9070xt-server \
    --testset gec-2026-12 --model gemma4:e4b --exchange one-request-text
# 3. Print the table / which kinds of error were found / the explanations a teacher marks
bazel run //tools:report -- table --store ~/git/teachers-teammate-evals
bazel run //tools:report -- table --store ~/git/teachers-teammate-evals --by-error category
bazel run //tools:report -- sheet --run RUN_NAME --store ~/git/teachers-teammate-evals > spotcheck.md
```

**A run that is checked before it starts, and read afterwards.**  `run --dry-run` makes the same
plan the run makes and prints it: one line per session (model, temperature, mode, repeat, how many
texts it would send), the results files that already hold every text, the refusals, and the number
of requests.  It sends nothing but the server probe, which reads the version and the model's
details, so a night's work can be checked in a second; a refusal exits 2, as the run would.
`run --log FILE` appends one JSON line per event — `complete`, `session`, `smoke`, `row` (with the
row's seconds and whether it lost text) — and closes with `finished`, `refused` or `interrupted`.
Ctrl-C is how a night is paused, so a paused run still says where it stopped.  At the end a run
that lost a setup says so: `N of M planned sessions did not run:` and one line per session with the
reason the smoke check gave.  The exit code stays 0 — a script that chains runs decides for itself
what a skipped setup means, and a run that wrote most of its rows is not a failure.

**Evaluation report.** `bazel run //tools:report -- page --store ~/git/teachers-teammate-evals
--report-out ~/gec-testset/reports/NAME [--zip] [--single-file]
[--model TAG] [--languages de,en]` writes one static page (opens from `file://`, no server) with the
scorecard, a heat map, the sentences of every variant next to the reference, and the error types.
Python computes every number with the report's own functions; the page only displays. A run it
cannot use (no header, another test set, sessions that disagree, an older header) is listed in the
footer with the reason. The output must lie outside a git work tree (a report holds corpus text)
and be a new or earlier-report folder. Reading the store scores every answer again, which takes
minutes on long texts; the tool prints each file as it goes. Code: `tools/evaluation/report/viewer/`.

**The test sets, metrics, floors and the comparison** are described in `evaluations/README.md`; their
numbers are in `tools/evaluation/report/gates.py`, `stats.py` and `metrics/`. The held-out set is built like the first:
`prepare_gec_testset --testset gec-2026-12-heldout --corpora ~/gec-testset/corpora.json --store
~/git/teachers-teammate-evals`.

**Gate 2 and the comparison on the command line.** The floors are `report/gates.py`
(`GateTwoRule.measure(group)` returns one check per floor and language, `rate(measurement, marks)`
decides which enter the outcome, `judge` is both), the comparison `report/compare.py` and
`report/stats.py`.

```bash
# the floors and the explanations of one model
bazel run //tools:report -- gate --store ~/git/teachers-teammate-evals \
    --model ministral-3:14b [--exchange text-first] [--steps correct,explain,unclear] \
    [--temperature X] [--machine rx9070xt-server] [--testset gec-2026-12]
# one model against another
bazel run //tools:report -- compare --store ~/git/teachers-teammate-evals \
    --testset gec-2026-12 --along model --baseline gemma4:e4b
# one model over its temperatures, step ① alone, with the best row and its ties
bazel run //tools:report -- compare --store ~/git/teachers-teammate-evals \
    --testset gec-2026-12 --machine rx9070xt-server --model qwen3.5:4b \
    --exchange text-first --steps correct --along temperature --baseline 0.05 \
    --where step_one_prompt=standard --where sampling.presence_penalty=
# what a second and a third pass of each configuration would add, and what they would cost
bazel run //tools:report -- passes --store ~/git/teachers-teammate-evals \
    --testset gec-2026-12
```

A command names a model, not a file: the runs are read from `--store`, and `--model`,
`--temperature`, `--exchange`, `--steps`, `--digest`, `--compact-json`, `--machine` and `--testset`
narrow them.  The machine and the test set default to those of the newest run, and the command
prints what it chose and which other series the store holds.  `gate` reads the model's step-①
temperature unless `--temperature` names one; `compare` reads every temperature it did not name.  A
comparison takes the test set and the machine from the runs it reads, never from the axis it
varies.  What is left ambiguous is refused rather than guessed: every axis in which the runs differ
has to be named in `--along` or fixed with `--where` (`--where sampling.presence_penalty=` keeps the
runs without that option; `machine` and `isolated` name where a run ran).  `gate` needs `--store`, because its spot-check floor reads the marks
files of the set.  One line per check (`✓`, `✗`, `·` for a diagnostic), then the outcome.  Exit
codes: `gate` 0 for `PASS` / `PASS_PENDING`, 1 for `FAIL` or `INCOMPLETE`; `compare` 0 when it
printed its tables; both 2 when the selection is refused.  The record of a gate is
`gates/gate2-<date>.md` in the private evaluation repository: a verdict, the runs it rests on and
the command, no figure.

**`compare`** scores every answer again and forms the groups from the headers, so a run that lost a
repeat says so (`skipped <file>: <reason>`).  `--along` names the axes that vary, comma-separated
(`model`, `temperature`, `exchange`, `steps`, `num_ctx`, `compact_json`, `sampling.<option>` and the
rest of `core/axis_table.py`), `--where` fixes others, and `--baseline` names the value of the first
axis the differences are measured against — the first row when it is not given.  `--corrections`
compares the corrections only: `text-first` and `text-first/correct` count as one measurement,
because step ① writes the corrections of both.  Along `temperature` the table shows, per
temperature, the deciding mean F0.5 with German and English beside it, recall, precision, left
alone, coverage, false alarms per text, the spread of the repeats, lost or added texts, seconds per
100 words, answer tokens per text, the fixes, wrong changes and misses per 300-word page, and the
difference against the baseline with its 95 % interval.  Below it the best row is named with every
row that is not worse beyond the noise: a tie goes to the cheaper configuration, which is a choice,
not a number.

**Passes.**  `passes --store DIR --testset NAME` asks what a second and a third pass of one
configuration would buy if the passes were merged by a vote.  An edit's *votes* are how many of the
repeats made it.  The table shows the single pass (the mean of the repeats), the edits at least two
of three made, and the edits every repeat made; then what share of a single pass's edits not every
repeat made, and how many of its false alarms and of its real fixes that share holds; then the
precision of an edit made by one, two and three passes.  The last column is the time against one
pass, `1 + (k - 1) × step ①'s share`, because a further pass repeats only step ①; a run without step
tags shows a dash.  A group of one repeat is left out; a repeat that lacks a text another has is
refused.

**Marking explanations and accepting alternatives.**  `report -- sheet --run RUN_NAME --store DIR`
prints the sheet of a run's explanation sample with the source sentences; `store -- marks SHEET.md
--marker NAME` writes `marks/<marker>__<question_sha256>.jsonl` (`--marker` is required, because
the name goes into the repository).  The file is merged, not replaced, and the explanation's hash is
part of a mark's identity, so a verdict on one model's explanation is not credited to another's.
`store -- validate` checks the file against its set; `gate` reads a marks file only when that check
passes.  The sample's seed is the run key, not the file name, so renaming a run file draws no other
sample.  `store -- accept-marks --testset NAME --marker NAME --store DIR` turns one marker's `valid`
edit marks into the set's accepted alternatives; the file is merged, a disagreement between markers
is listed, never resolved, and a mark whose edit no stored answer makes is listed.
`report -- table --no-accepted` scores without them.

**Exchange and steps.**  `run --exchange` says how the text travels and `run --steps` which steps of
it run.  `text-first` is the app's proofreading exchange (① the corrected text, ② the explanations,
③ the unclear check) and the default; `one-request-review` asks for corrections with explanations in
one JSON answer; `one-request-text` for the corrected text only.  `--steps correct` sends step ①
alone, so `text-first --steps correct` scores the exchange's corrections for a fraction of its
time (`--performance` prints step ①'s share).  It is the exchange's own code: the port refuses the
requests of steps ② and ③ (`OnlyStepsPort`) and the exchange treats that as "no explanations, no
unclear list", so the prompt, the cut-off handling and the text budget (hence the pieces of a long
text) are the exchange's, and a test pins that its request hashes like the exchange's first one.
Its rows have no review, no warning and no explanations, like `one-request-text`'s.  Use it for
every question about the corrections (temperature, sampling values, prompts, passes, thinking,
budgets); use `text-first` for the explanations, the unclear flags and the time of a whole page.
The exchange and the steps are tags of the run file's name
(`2026-11-16_rx9070xt-server_gemma4-12b_6114515d__steps-correct__temperature-0.4__repeat-3__<key8>
.run.jsonl`; a run that ran no step of the exchange reads `steps-none`), and the table keeps them
apart.  A run
without `explain` has no explanations, so "Explained", orphans and "Same category" stay empty for
it.

`run --option NAME=VALUE` (repeatable) sends sampling values with step ①, besides the temperature:
`top_k`, `top_p`, `min_p`, `presence_penalty`, `frequency_penalty`, `repeat_penalty`
(`SAMPLING_OPTIONS`).  Without it the request carries the temperature and the window only and the
model's own tag decides the rest, as in the app.  A run with options writes a file of its own — each
sampling option is a tag of the name
(`…__sampling.presence_penalty-0__temperature-0.2__repeat-1__<key8>.run.jsonl`) — and the options are
part of the compare key, so a report keeps runs asked with different values apart.  Every request of
the exchange is tagged with its step in the performance record (`step`, derived from its system
prompt), and `--performance` prints step ①'s share of the time.

`run --prompt NAME` asks step ① with a named variant of its system prompt (`keep`, `find` or
`shot`, `CORRECT_VARIANTS` in `domain/correction_prompts.py`) instead of the model's own;
`--prompt standard` sends the standard `CORRECT_SYSTEM` to a model that has an adopted variant
(to measure the two against each other).  Only `text-first` sends it, so another exchange refuses.
The variants are written from what the evaluation showed and never from a test text: `keep` for
the models that over-edit (the false alarms of those models are mostly extra edits inside
sentences that have a mistake, so a sentence with a mistake changes only the words of
the mistake; a short list says what is no mistake and is not to be added either, an optional comma
included), `find` for the models that leave mistakes alone (a checklist of what to look at in every
sentence, its comma rule per language), `shot` with two invented worked examples (labelled so that
no label can start an answer; the exchange refuses an answer that starts with a `Word:` label the
text does not start with: `framing_problem`).  A variant's rules and examples come from German and
English, the languages that were measured, and say so: a model's variant is sent for every text
language.  A variant is part of the request profile (`StepProfile.prompt`,
step ① only; `STEP_ONE_PROMPTS` in `model_catalog.py` is the table of the models that are sent one,
empty until a measurement on both test sets adopts a variant; another quantisation of a listed
model is sent the same one).  A run with a variant writes a file of its own
(`…__step_one_prompt-keep__temperature-0.2__repeat-1__<key8>.run.jsonl`; the name follows the prompt
the requests carry, so a model with an adopted variant writes that file without a flag and
`--prompt standard` writes the plain one), and the prompt is part of the compare key, so a report
keeps runs of different prompts apart.  The fingerprint hashes the text of every variant, so a changed
variant needs a new protocol version once a version has been merged or measured; a variant that a measurement used is never edited, another text is another id.
The cache key of the correction stage carries the variant (id and hash of its text,
`prompt_identity()`) only for a model that has one.

**The request per model, and a second try.**  The request the app sends is not the same for every
model.  `RequestProfile` (`domain/model_catalog.py`) holds what differs, the catalog carries one per
model, and `request_profile(tag)` resolves it — the default for a model the catalog does not know
(see *Request profiles* above):

* `temperature` differs by step.  Step ① runs at `STEP_ONE_TEMPERATURES` (0.05 for `qwen3.5:4b` /
  `:9b`, the Ministral models and `mistral-nemo:12b`) or 0.4: over 0.05, 0.4, 0.7 and 1.0, no model
  was better above 0.4, and those models get worse with every step up (`compare --along
  temperature` on the step-① runs of `gec-2026-12`).  Steps ② and ③ run at `MAKER_TEMPERATURES`,
  else `DEFAULT_CORRECTION_TEMPERATURE` (0.2).  An online provider gets 0.2 in every step
  (`Config.correction_temperature`, the chat model's temperature).  The correction cache key holds
  the temperature of all three steps.  The content review resolves its own the same way
  (`review_temperature`, default 0.7); text recognition is fixed at 0.0.
* `compact_json` (default **off**) adds `COMPACT_ANSWER_RULE` to a one-request review.  The
  exchange does not send that request; the evaluation tools measure it as a per-model decision.

Sampling is *not* in the profile by default: top-k, top-p and the penalties come from the tag, which
is where the vendors put their recommendations.  The three readers of the fixed part — the
corrector, the dialog that shows "what is sent for this file", and the cache key — resolve the same
object, so the dialog shows the request that is sent and a profile change invalidates only that
model's cached corrections.

A step answer the window cut off is asked **once more** (`cut_off_tries`: not for an online service
and not at temperature 0, where it would be the same answer), and a cut-off *repair* answer is not
retried (`infrastructure/proofreading/runner.py`).  The evaluation mirrors it: `RecordingClient`
sends `seed + k` for the *k*-th identical request, so a repeat samples afresh, and gate 1 sends the
second try and stores both attempts (`attempt` 1 and 2).  Its verdicts: cut off twice is
`TRUNCATED` (hard), cut off once and then answered is `CUT_OFF_ONCE` (soft), and a second answer
that fails another test gets that test's verdict.

**Protocol version.**  What a run measured and how — the prompts, the answer format, the window, the
level and the long-text budget — is versioned in `tools/evaluation/core/protocol.json`, not by commit.
`run` computes the fingerprint of that request side before the first request and stops with exit
code 2 when the current version does not accept it, so an uncommitted prompt edit is caught like a
committed one.  The fingerprint is captured from the correctors a run builds, for every setup,
language and profile variant: a hash of every request the probe text produces (system and user
message, answer schema, options), the text budget the app splits a long text by, the repair message
and the answer format version.  A version that only *adds* something (a setup, a profile variant)
leaves every earlier request hash as it was, and its entry says so.  Raising the version means adding an entry with the new fingerprint, the date and a
line saying why, and setting `current` to it; do it when the check refuses.  A change of the
scoring, the caps or the edit extraction does not move the fingerprint: it raises `scorer_version`
(`report/score.py`), and the golden numbers of the tests fail until it does.  Old entries are never
edited or removed.  A run resumed after a tool,
Ollama or model change is refused as well, before the first request of any combination: the
sessions of one run have to agree on the protocol, the test set (by hash), the machine, the model
digest and the request settings including the timeout, and a results file without a header is
refused.  The fingerprint is compared by its version, not by its value: two fingerprints of one
version are one protocol, so an additive change does not block a long run in progress, while a
changed `instructions` hash per language still refuses.
`tool_revision` stays in the header as a pointer for a human reader; nothing checks it.

**What Ollama reports about memory.**  `memory_gb` in the catalog is the total a model needs at the
8192-token window, on the card and in main memory together.  Ollama's own size report (`/api/ps`)
can be far off for some models, so the fit check reads the card through the memory agent, and the
graphics-memory column of a report is the figure to trust, not *On the card*.

**Speed, memory and repeatable runs.**  Every answer line of a `run` records what its text cost
(`Answer.requests`, `tools/evaluation/core/performance.py`): each request with Ollama's token
counts and durations, the models the server had loaded, the graphics-memory peak, the seed.  The
report shows it with `report --performance`: seconds per 100 words of student text (what a teacher
waits), answer tokens per second (how fast the machine runs the model), answer tokens per 100
words, requests per text (a repair or fallback adds one), graphics memory added over the idle
baseline.  Options of `run`:

| Option | Effect |
| --- | --- |
| `--seed N` (default 20261109) | The app sends no seed; the evaluation sends one so a run can be repeated on the same machine and software. |
| `--repeat N` | Run every text N times; repeat k uses seed + k - 1 and a file tagged `repeat-k` |
| `--isolate` | Unload every model first, refuse a server that will not become idle, unload the model afterwards.  Only on a server you own |
| `--gpu-sampler amd\|nvidia`, `--gpu-ssh "ssh.exe user@host"`, `--gpu-agent URL`, `--gpu-token-file PATH` | Sample the graphics memory in use once a second: on this machine, through SSH (from WSL the Windows client `ssh.exe` holds the key), or through the memory agent on the machine with the card |
| `--machine ID` | The machine the server runs on, an id of the store's `machines/`; required, and written into the header |

Each run file starts with its header — the model's digest, quantisation, parameter size and family,
the window, the seed, the machine and the Ollama version — and appends one session line per
invocation with the tool revision, the idle graphics memory and what the server had loaded.  The
protocol (`tools/evaluation/run/measure.py`): unload and check,
start the sampler, one unscored warm-up request that loads the model, then per text note what is
loaded before and after.  A text is **shared** when another model was loaded at either point; shared
texts are left out of every speed and memory figure.  Samples are stamped when they arrive here,
because the server's clock can be seconds off, and a one-second sampling interval is the limit of
the memory peak.  The speed runs sample with the local process, through SSH or through the memory
agent (see *Memory agent*), which is what the fit check reads.

**Committing a run as evidence.**  A run is committed in the store itself, which is the private
repository: the file the tools write is the file the tools read, nothing is converted, cut or
encoded on the way in.  `store -- validate` checks the store's files without the corpora, and
`store -- manifest` checks that a test set is still the one the corpora give — every corpus file and
every item:

```bash
bazel run //tools:store -- validate --store ~/git/teachers-teammate-evals
bazel run //tools:store -- manifest --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json
```

The method is in `evaluations/README.md`, the store's layout and rules in
`evaluations/FORMAT.md`, the fields in the records of `tools/evaluation/core/`; the test-set manifest and the machine files are written once and then
pinned.

Things to know when reading the figures:

* **Compare speed only between runs on the same machine.**  Backends, memory and drivers differ
  between the machines of `machines/`.
* **Tokens per second is not waiting time.**  Models write different amounts for the same text, and
  one model writes several times more in `one-request-review` than in `one-request-text`; seconds
  per 100 words is what a teacher waits.
* **The samplers read `amd-smi` and `nvidia-smi`.**  Other cards and versions may print other
  columns.
* A sampler started over SSH is killed by process id on the remote machine; stopping the local
  `ssh.exe` alone leaves `amd-smi` running there.

**Where the code lives.**  `tools/evaluation/` is one package per layer, and the command lines
(`evaluate.py`, `report.py`, `store.py`, `prepare_gec_testset.py`) only parse arguments and print.
A layer imports only the layers below it:

```
report -> store -> input -> core, metrics
run     -> store, input, core, metrics
checks  -> run, core, metrics
viewer  -> report, run, store, input, core, metrics
convert -> store, input, core, metrics
```

| Module | Job |
| --- | --- |
| `core/records` | The typed records of every stored format: test set, run file, marks. |
| `core/formats` | The format version of every file, JSONL with sorted keys, and the fields a reader keeps. |
| `core/axis_table` | Every axis of a run, declared once: its field, its codec, its label, its tag and its compare role. |
| `core/axes` | The four keys (compare, series, run, environment) and the file name derived from the axes. |
| `core/machines` | The machine registry: the ids a run and a size measurement name, read from the store's `machines/`. |
| `core/modes` | How a model is asked: the exchange and its steps. |
| `core/labels` | The corpus labels of the reference edits and the category agreement. |
| `core/languages` | The language codes of the test set and the names the corrector is asked in. |
| `core/performance` | What one text cost: requests, tokens, seconds, memory. |
| `core/protocol_table` | The version file of the request side, and which version a fingerprint belongs to. |
| `metrics/item_scores` | Every number of one text, from counts. |
| `metrics/gleu` | The GLEU counts of the model and of the source. |
| `metrics/text_loss` | How much of a text an answer keeps, and how much it adds. |
| `metrics/quality` | The word diff, the edit matching and the sentence rules the numbers rest on. |
| `store/layout` | Where the parts of the store live, and what a run file is called. |
| `store/reader`, `store/writer` | Reading and writing the store's files as typed records. |
| `store/validate` | What a test set, a run file and a marks file must satisfy. |
| `store/marks`, `store/accept` | What a mark means, and the accepted alternatives it becomes. |
| `convert/v1` | The one-off converter: a run of format 1 read and written as format 2. |
| `input/testset` | The test set: its manifest, its items and the way from a pointer to a text. |
| `input/corpora/` | One module per corpus: what it may be used for, which files it is, how a pointer becomes a text. |
| `input/recipes/` | One module per test-set id: the selection rule, its seed and the corpora it needs. |
| `run/runner` | The run loop: plan, resume, warm-up, header, sampler. |
| `run/measure` | The requests, the server and the graphics memory of a measured run. |
| `run/session` | The sessions of one run and whether it may be resumed. |
| `run/protocol` | The fingerprint of the request side, captured from the corrector. |
| `run/app_corrector` | The one-request corrector, as the tools build it — the baseline the exchange is measured against. |
| `run/one_request/` | The one-request path itself: `correction.py` (the correctors), `correction_schema.py` (the answer model), `answer_format.py`, `presets.py` (the per-language policy presets), `sentence_review.py`. Nothing under `teachers_teammate/` imports it. |
| `run/comparison` | Runs a test set through that corrector and turns the answers into rows. |
| `run/gpu_agent`, `run/agent_client` | The memory agent on the machine with the card, and the client that reads it. |
| `report/score` | The one scoring path, and `SCORER_VERSION`. |
| `report/load` | The store as a report reads it. |
| `report/summary` | The numbers a report is made of: one summary per model, temperature, language and band. |
| `report/groups` | Which runs may be compared: the group key, the runs of one key, and the spread of a number over the repeats. |
| `report/pooled` | The counts of one language, added up over the repeats. |
| `report/stats` | The paired bootstrap, the interval, the minimum effect and `verdict_of`. |
| `report/compare` | One comparison along the axes that vary: the rows, the baseline and the verdicts. |
| `report/gates` | Gate 2: the floors of one model, the comparison of two, the paired bootstrap. |
| `report/spot_check` | The explanation check: the sample of a run, the sheet, the marks. |
| `report/select` | Which committed runs a gate-2 command reads: the newest series of a model, and what it refuses as ambiguous. |
| `report/tables/` | The tables: `models`, `by_error`, `performance`, `sweep`, `passes`, `false_alarms`. |
| `report/viewer/` | The static report page. |
| `checks/` | The checks that run a model rather than read a run: `structured_check`, `model_fit`, `smoke`, `gate_one`, `check_rule`, `download_sizes`. |

`report` imports nothing above it: the command lines and `viewer` read it, and the numbers it prints
come from `summary`, which `groups` and `gates` read instead.  A test in
`test_evaluation_session.py` reads the imports of every module of the run layer and refuses one that
reaches above it.

### Adding a corpus or a test set

A corpus is one module in `tools/evaluation/input/corpora/`, named after the corpus with `-` replaced by
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
several corpora. It lives in `tools/evaluation/input/recipes/`, one module per test-set id, found the same
way, setting `RECIPE` to a `Recipe(testset, seed, corpora, select)`. `prepare_gec_testset --testset ID`
picks the recipe, and `ID` is required: with two recipes a default would build the wrong set sooner
or later. A recipe never changes once its manifest is committed — another selection is another
test-set id.

Each needs its tests: `tests/tools/test_evaluation_input.py` covers the declarations, the refusals,
the splits, the pointers, the manifest and the rebuild of every item.

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
   Paddle's boxes are tight and cut off full stops and last letters at the line ends.
4. `reading_order.reading_order` — rows by centre height (a box joins the row when its centre is
   within half the median line height), each row left to right.  One column only.
5. Per box: check the stop event, `recognise.crop_line` (perspective warp to an upright strip;
   a strip 1.5 x taller than wide is turned 90 degrees), `recogniser_input` (height 48, width
   at least 320 and at most 3200, `(x/255 - 0.5)/0.5`), one forward pass, `ctc_decode`
   (classes are `["<blank>", *character_dict, " "]` = 18,710, the model's output size).  Words
   of a row are joined with a space, rows with a newline; empty lines are dropped.
   Recognition is one line at a time, which is faster than batching.

**Why `cv2.dnn`, and the `OnnxNet` seam.**  OpenCV is already part of the app, so the reader
adds no package; ONNX Runtime is barely faster, adds 78 MB and has no Intel-Mac build.  The reader depends on the
one-method `OnnxNet` protocol (`infrastructure/onnx/runner.py`) only, so a model that
`cv2.dnn` runs differently on real pages is a new class and a dependency, not a rewrite.
`CvDnnNet` loads from **bytes**, not from the path: OpenCV's path functions fail on a Windows
user folder with an umlaut (a test loads from `Jörg Müller`).  PyYAML reads the recogniser's
character list from `inference.yml` (a base dependency).

**`READER_VERSION`** (`domain/local_models.py`) is bumped whenever detection, recognition,
reading order or joining changes what the reader returns; it is part of the cache identity, so
stored pages are read again.

**Adding an engine** is in {doc}`extension_points` ("New OCR engine").  A settings file that names a
retired engine (`RETIRED_OCR_ENGINES`: `tesseract`, `paddleocr`) is read as `ppocr` by
`settings_schema.normalize_ocr_engine`, with a notice that names the engine; an online engine falls
back to the default.

### Text recognition switch (`recognition_enabled`)

`Config.recognition_enabled` (default `True`, setting key of the same name, CLI `--no-recognition`)
says whether scans are read at all.  It lives on `Config`, **not** on `OcrConfig`: `OcrConfig` says
*how* pages are read and feeds `cache_keys.ocr_key` (an explicit field list), so a flag that
decides *whether* a file is read changes no cache key.

Off, the layers behave like this:

- `PreprocessService.preprocess_input(file, allow_images=False)` raises `RecognitionSwitchedOffError`
  (a `PipelineInputError`) when the loaded input has image units -- before any page is
  preprocessed.  Text files and PDFs with a usable text layer pass.  `FileProcessor` turns it into
  `ProcessingResult(ok=False, skipped=True, message=RECOGNITION_OFF_MESSAGE)`.  A file whose OCR
  text is already cached never reaches that code and runs normally.
- `run_files()` returns a `RunSummary`; a skipped file is neither a success nor a failure
  (exit code 0).  `ProcessingResult.skipped` -> `FileOutcome(status=SKIPPED)`; a file that is
  gone since it was queued is skipped the same way.  The main window opens
  no error dialog for it and `ResultsTable.add_result` shows "skipped" in grey.
- The refusal of settings that would read pages online, `run_rules.uses_ollama` and
  `run_rules.ollama_models` ignore the OCR model, the pipeline does not check it, and
  `describe_data_flow` leaves the recognition stage out (no page leaves anywhere).
  `describe_steps` forces it back on so the line under the step can still say what ticking it
  would use.
- `check_run_preflight(config, files=..., recognised=...)` asks `_plan_recognition()` (images are
  scans; PDFs are opened with `pdf_has_text_layer()`, cached by path/size/mtime/mode, so call it from
  a background thread) and returns `RunPreflight.skipped`; when no file is readable it is blocked by
  `IssueKind.NOTHING_TO_READ` (fix: **Turn on**, `COMMAND_TURN_ON_RECOGNITION`).  The main window
  feeds the result to `ConfigPanel.show_recognition_note()` and `ResultsTable.set_skipped_sources()`.
- Ticking the box goes through the requirement guard like the other steps (`check_stage_requirements(
  config, "ocr")`); the stage re-run in the preview menu overrides the box.

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
- `identity.py` — `available_page_steps(store)`: which page models a store can run, without
  importing OpenCV (the cache key needs it for every page).  The identity itself is
  `domain/local_models.py` (`AUTO_VERSION`, `auto_output`, `auto_identity`).
- `recipes.py` — `steps_for(ocr, store)`: `none` → `[]`, anything else → `auto_steps(ocr.engine,
  store)`.  `stage_builder.default_build_preprocessor(tmp_dir, save_steps, ocr, store)` is
  `StepPipeline(steps_for(ocr, store or ModelStore()), …)`; the factory, `preprocess_preview`, the
  speed probe and `tools/dev/ocr_page_tokens.py` all pass the model store through.

**The gates are conservative on purpose.**  Every step acts only where its measurement says so
and leaves the page alone otherwise, so a clean scan passes unchanged; a thing the steps get
wrong costs less than a thing they do to a page that did not need it.  A changed threshold is a
changed constant and an `AUTO_VERSION` bump.

**Page models.**  `DOC_ORI` (PP-LCNet_x1_0_doc_ori) and `UVDOC` are `LocalModel`s in
`domain/local_models.py` (group `PAGE_MODELS`, 39 MB, Apache-2.0, PaddlePaddle).  The installed app
carries them (see *Local model files*); otherwise they are downloaded only on request: **Download…** on Scan quality, the **Page models** box on Add-ons → Reading models
(`PAGE_MODELS_ITEM` of `domain/install_item.py`, between the reader and the Ollama pulls in
`InstallRound`), or
`--download-page-models`.  Missing page models are **not** an `IssueKind`: a run never waits for
them; the CLI prints one notice line (`texts/addons.py:PAGE_MODELS_SKIPPED_NOTICE`).  `UVDoc`
answers an image at
the size it was given, so the page goes to it scaled to at most `FLATTEN_MAX_SIDE` (2048).

**Identity.**  `domain/local_models.auto_identity(engine, page_steps)` is the identity; for
`auto` it adds `v<AUTO_VERSION>`, `colour` / `gray`, and `+turn` / `+flatten` for the page models
that are downloaded, and `cache_keys.preprocess_fingerprint(ocr, source, facts)` puts the
method in front of it, then the PDF DPI and text-layer entries.  Downloading the page models, a
new `AUTO_VERSION` or another engine's output re-reads pages **once**, and that is intended.
Bump `AUTO_VERSION` (`domain/local_models.py`) whenever a step, a threshold or a page model
revision changes.  The model
store reaches the key through the `KeyFacts` port
(`domain/key_facts.py`), which `infrastructure/key_facts.py:MachineKeyFacts` answers with the
`ModelStore` it was given; with `None` that is the default folder (the tests point that at an
empty temporary one).

**How to change it.**  A new step is a `Step` in `auto_steps`, with its gate and a named
constant, a `STEP_LABELS` entry in `texts/review.py` if it is worth a word in the preview
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
  (`AddonService.get_installation_status()` reports `READER_ITEM.bundled` /
  `PAGE_MODELS_ITEM.bundled`).
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
- A run reads the store its service checks (`RunService(model_store=)` →
  `PipelineDependencies.model_store` / `run_ocr(model_store=)` → `StageBuilder.model_store`), so
  the readiness check and the run never look at different folders.
- `check_run_preflight()` reports `IssueKind.READER_MODEL_MISSING` (the built-in reader's model
  files are not downloaded) only for a run that has a file to read: a folder of text files, or of
  files whose text is stored, starts without the model files, as in the main window.
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
  bump of `AUTO_VERSION` (`domain/local_models.py`) and a look at `test_page_models.py`.

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

**Moving from the cache.** `resolve_config_path(None)` and `resolve_data_folder(name)` move an
`ocr.toml`, `benchmark/` or `speed/` found in the cache folder to its own place the first time they
are asked for (`migrate_once`: only when the new one is missing; when
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

Every stage key and the keys of the stored results are computed by `cache_keys.py`, a module of the
core: pure functions of the settings, the windows a run resolved and a `KeyFacts` port
(`domain/key_facts.py`) that `infrastructure/key_facts.py:MachineKeyFacts` answers for the computer
the app runs on.  So a key can be computed and tested without a file system, a server or a model
store, and `tests/unit/test_cache_keys_golden.py` holds the digests that must not move.

- `compute_file_hash(path)` (`state_repository.py`) — SHA-256 of the file
  contents; the **identity of a document** regardless of its path or name.
- `ocr_key(config, source, facts)` (`cache_keys.py`) — a
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
- `correction_key` / `review_key` deliberately leave the
  anonymizer settings out: a text proofread with names hidden is the same text, so
  switching *Remove personal information* on or off does not send it again.  What was sent
  is recorded with the result instead (`SentRecord`, see *How a stored result was sent*),
  and `privacy_key(config)` — the hash of the anonymizer settings alone, computed
  without importing spaCy — says whether a stored result was made with other settings than
  the current ones.  The resolved anonymizer settings (primary and secondary spaCy model plus every
  regex pattern, `tag=pattern`) are part of that fingerprint, so a changed pattern shows up
  as "other privacy settings" without a run.  `installed_models` is deliberately not part of
  it.
- `compute_cache_key(value)` (`storage_root.py`) — a short (16-char) stable key
  used to map a path (output dir / source file) to an on-disk file name.
- `document_stem(path)` (`storage_root.py`) — `<stem>_<key of the resolved path>`, the name a
  document's stored files carry: its state file and its images.  Two files of one name in
  different folders are two documents and never overwrite each other's pages.

**The state schema.**  `StateRepository._SCHEMA_VERSION` is 3.  A file of another version is
read as missing, so the document is processed again: a review stored in another shape would
otherwise load without explanations and, its key unchanged, never be made again.  The review's offsets are in the plain text of the stored
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
(`atomic_file.py:write_text_atomic`, the temp-file-and-replace the state store
uses too).  **One writer**: the window, through `DocumentCommands`; runs and the CLI only read.

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
`machine_corrected` (text 4) and the `WorkStatus` a reader marks.  `DocumentQueries`
merges it into every `DocumentStateView` (`load_view`, `queue_rows`), and
the pipeline calls the same function.  Queue rows, *Export*, *Copy*, *Save*, *Hide names*, *What
is sent* and `check_document` read the view.  **Nothing outside the cache's own code reads a
`DocumentState` text** for display or output: `tests/unit/test_teacher_work_readers.py` holds the
package to that, and the GUI may not name the type at all.

**One helper for the flags.**  `domain/freshness.py:stage_hashes(state, work, expected)` is the only
place that turns a work into the three flags freshness needs: `ocr_frozen` (a transcription part
exists, or a corrections part was worked on), `expected_correction_source` (`text_identity` of the current transcription) and
`correction_kept` (the corrections part applies and was worked on).  The run
(`CacheReconciliationService.prepare`) and the queue (`reconciled_view`) both ask there, so
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
`tests/unit/test_documents_service.py` (the merged views, `worked_on_documents`) and
`tests/unit/test_commands_service.py` (the writes),
`tests/unit/test_teacher_work_readers.py` (no reader names the cache's type).

### Shared anonymization cache

`AnonymizationService` (`application/anonymization.py`) keeps two caches under one `RLock`:

- `_anonymizer_cache` — one `SpacyAnonymizer` per `(language, resolved settings)`;
  building one loads the spaCy models.
- `_anonymization_cache` — an `AnonymizationResultCache` (LRU, a few hundred texts,
  memory only) holding the anonymized text and its placeholder map, keyed by language
  plus resolved settings plus the SHA-256 of the text.

A pipeline run gets its anonymizer from the service: `run_selected()` hands the
`PipelineDependencies` a `PipelineComponentFactory` whose `build_anonymizer` is
`AnonymizationService.build_cached_anonymizer`.  That returns a
`CachedAnonymizer` over the same two caches, so a run, `anonymize_text()` and
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
so switching the setting off shows the stored text again without a reload.  Whether the names
are hidden is the 👁 toggle's business and nothing else: no strip above the text says it, because
a strip that came and went with the toggle moved the texts under the cursor.  Until the
placeholders have arrived (`Masking.working`) the toggle reads *👁…* with the tooltip *Hiding
personal information…*, because the names still stand in the text.  Why the texts cannot be
typed into is the answer to a keystroke in them (`CheckTextView.keyPressEvent`, a tooltip at the
caret) and the tooltip of the card's buttons (`txt.SHOW_NAMES_TO_CHANGE`); a tooltip over the
whole text would pop up wherever the pointer rests.  Only a hide that *failed* gets a strip — the names then stand in
the text while the toggle says they are hidden — and its button emits
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
`AnonymizationService.anonymize_text()` off the GUI thread and therefore reuses the
shared cache above (a run then needs no second spaCy pass, and vice versa).  A result that
belongs to a text the page no longer shows is dropped (`set_anonymization(..., raw_text=…)`),
and a failure keeps the names hidden behind the strip that says so.  Changing the
anonymizer settings in the dialog calls `forget_anonymization()`.

The setting itself is a GUI preference, not part of `Config`: the checkbox under
*Settings → Privacy → Personal info: detection* and the eye button both end in
`MainWindow._apply_display_anonymized`,
which stores `privacy/display_anonymized` (`gui/_privacy_settings.py`,
`display_anonymized()` / `set_display_anonymized()`) and calls
`CheckPage.set_display_anonymized()`.  It defaults to **on** (`DISPLAY_ANONYMIZED_DEFAULT`):
only a stored answer shows the names, and without the add-on the checkbox is greyed out and the
setting takes effect once it is installed.  The checkbox is enabled only while
`AnonymizationService.anonymizer_available(language)` holds — spaCy importable and the
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

**Shared state is wired by the window**, not by the pages: the editable profile on *This computer* feeds the read-only profile lines of AI models and Add-ons; its server address
(announced on `editingFinished`, never while typing) feeds the model lists; a finished
install on Add-ons calls `on_packages_changed()` on every built page and is re-emitted as
`SettingsWindow.packages_changed`; choosing a recognition method applies its default image
preparation on Scan quality.  **Deep links** between pages (a package hint's "Install…", "Download
a model…", "change…" next to the profile) are `navigate_requested` signals that the window turns
into `show_page(page, focus, preselect)`; `OpenDownloads` callbacks therefore return at once.

**API keys** are one `ApiKeyDrafts` (`gui/settings/_credentials.py`) owned by the window and passed
to pages as `SettingsContext.key_drafts`; the key row under the AI service and the list on *Online
services* are both views of it (`ApiKeyRow`).  A key is held by the app as soon as it is
committed — on `editingFinished` and 800 ms after the last keystroke — because providers ask
`ApiKeys` for it when they list their models, and that list is the check that the key works.
It goes into the keychain/settings file only on OK (`apply()`, which also reports keys the
keychain refused); every other way out of `done()` (Cancel, Esc, close, Restore › All settings)
calls `discard()`, which puts back what the app held, after the pages' `shutdown()` stopped any
commit timer.  `ApiKeys` (`application/api_keys.py`) is the only place a key is kept: the
environment is only read, so a key the teacher exported before the app started counts and the app
writes none.

**Where a key lives, and who reads it.**  `infrastructure/credential_store.py` is the store: the
system's secret store (Keychain, Credential Manager, Secret Service) when there is one, else the
plain-text `credentials/` group of the app's settings file.  Both it and `gui/_qsettings.py` take
the file's location from `config.gui_store_location()`, so the two cannot drift apart.  The store
is built once, in `application/composition.py`, and handed to `ApiKeys` there; a key is asked for
on first use and the answer is remembered, so the window loads every key at start
(`ApiKeys.load_stored`, migration included) and a run that asks nothing online never touches the
keychain.  A key found in the settings file moves into the secret store at the next start
(`CredentialStore.migrate`), and the uninstaller deletes them all (`ApiKeys.forget_all`).

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

**Closing.**  `done()` calls `shutdown()` on every built page (a running model fetch is
cancelled; a long job it started is held by `LongJobThread` until it ends, never destroyed
while running).  A pip install or
language-model download on Add-ons blocks closing (`close_blocker()`), because the new package
must be made importable first; an Ollama pull belongs to the main window's
`PullManager` and keeps going.  Dialogs opened from inside the window (the
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
from `DataFlowService.describe_steps(config)` (`application/data_flow.py`) -- every step as if it
were ticked, so an unticked step keeps its (grey) line -- and `short_place` / `uses_line`, which
share their words with the service list on AI models (`PLACE_*`).  The model is not part of
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
never only a sentence; `check_run_preflight(config, for_run=..., recognition=..., fresh=...)` runs
`ocr` first and exposes the first blocker as `RunPreflight.blocker -> (stage, issue)`.
`recognition=False` leaves the engine check out for a run that reads no page
(`needs_text_recognition(files, recognised=...)`: only text files, or text already stored; a PDF
counts as reading, because its text layer is only known by opening it).  Given `files`, the
preflight adds the documents whose recognised text an earlier run stored to `recognised`
(`DocumentQueries.recognised_files()`: a reading made with other settings is not among them), so
the command line, which passes none, answers like the window.  `for_run=False` is the bare
readiness question: it leaves out the memory, the wait for Ollama and the output folder (the
line shows that problem on its own, and with no input folder chosen yet both folders are the
current one).  `fresh=True` asks Ollama
for its models again instead of using the cached list ("Check again", a returning window).
The engine probes (`AddonService.is_module_importable`, `ReaderService.reader_installed`) are
shared with the connection check, so a package or the reader's models installed from Add-ons are
seen without a restart (`IssueKind.READER_MODEL_MISSING`, fix: the **Reading models** tab of
Add-ons with `READER_ITEM` ticked).  A new requirement is therefore: an
`IssueKind`, its check, and **one row in `gui/_fix_targets.py:fix_for`** -- the line for the
readiness line and the buttons (`FixAction`: a Settings page + focus + preselect, or a
`command` such as `retry` / `choose_folder`).  `DependencyGuardDialog(title, issues, actions)`
(the requirement guard when a step is ticked, and the dialog that stops Run) and the readiness
line take their buttons from that one table.  A `FixAction`'s preselect entries are
`InstallItem`s (`domain/install_item.py`): one kind plus one name, never a string key.

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

**One query feeds the page.**  `DocumentQueries.check_document(config, source)`
(`application/check_document.py`) reads the stored state and returns a `CheckDocument`: both
texts and their plain forms, the review aligned with them, the items the list numbers, the pages
of the scan, how the results were sent.  The window asks for it once per document
(`_load_check`) and once per stored edit (`_store_edit`), and never assembles that state itself.
A typed text is stored and read back through the same query; the alignment is memoised on the
two plain texts and the review (`_align_cached`).  The document also carries `sent` and
`preprocess`, so the queue row's *sent* cells come from the same read; the queue keeps no
preprocessing info of its own.

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
`edit_corrected_text` on `DocumentCommands`, which writes the teacher's work and returns the
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

**The panel: two tabs, a filter row, a card.**  `gui/check/_side_panel.py` holds all three.
`QTabWidget` with `setTabBarAutoHide(True)` puts the content review beside the list
(*Corrections · Content*, `show_corrections()` / `show_content()`, chosen by `CheckPage.set_view`
from the view kind); the tab bar hides itself while there is no review, so a document without one
looks as it did.  Above the list a `FlowLayout` (`gui/check/_flow.py`, wraps and skips hidden
widgets) holds one `QCheckBox` per category, plus *Unclear* and *Left out*, each with its count;
a box whose count is 0 is hidden.  Nothing ticked shows every row, ticked boxes combine, and
*Not looked at* is a box of its own that narrows whatever else is ticked.  `set_filter()` only
re-applies row visibility (`_apply_row_visibility`), which also keeps the chosen row when it
survives and otherwise chooses the first visible one, so a filter never leaves the card pointing
at a hidden row.  The card is a fixed-height `QScrollArea`
around the text plus a fixed-height button row: a long explanation scrolls inside the card and the
buttons stay where the pointer is.  Its text is HTML built by `_body_html()` — the bold
`before → after` line, the sentence around the change with the student's words highlighted
(`_context_html()`, masked like the title), the category as a coloured chip and *optional* /
*AI not sure* as grey tags (`_tags_of()`), then the explanation as normal text.  The button row
holds *Take out* / *Put back*, *Use suggestion*, the field that replaces the words and *OK*; a
spelling or punctuation change of the model also carries *Take out all N*, which puts the
student's words back for every such change still standing (`minor_corrections()`); a correction
the teacher took out and a change of their own are not in it.  The list's
`_ItemDelegate` draws the icon, two aligned word columns (`_column_rects()`) and a badge, and
greys a looked-at row instead of hiding it.  Every row has a badge: a model change shows its
category (*other* when it has none, and the *Other* box counts it), every other item its kind
(*unclear*, *left out*, *your change*, *taken out*).
The colours are one table, `gui/check/_style.py`: `MARK_COLOURS` for the marks in the text,
`CATEGORY_COLOURS` for the badge and the chip, `KIND_BADGE_COLOURS` for the other rows, in the
hue of their mark but weaker, so no kind's badge looks like a category's.  The app always runs on the dark theme, so every colour is a dark tint with light text
on it and the chosen item is the one bright background.

**Looked at.**  `SidePanel.item_shown` fires when an item becomes the chosen one (a list click, a
text click, the first item on open); the window calls `mark_looked_at` once per new key and
updates the document's set in place, without a reload.  The keys are `edit:<id>` (also for a
correction the teacher took out: it is the same item), `unclear:<n>` and `own:<start>-<end>` (the
words' place in the student's text, so taking another change back does not renumber them); the
list shows ● for a looked-at item and ○ otherwise (own changes keep ✋, taken out ↶), and the
footer says "8 of 12 looked at" from `looked_at_progress`.  Own changes do not count, every other
item counts on its own — a spelling change is a row like any other — and looking at an item is
never undone and is not *worked on* — a run may still replace the answer.

**Walking the corrections.**  *Next* on the card (`SidePanel.choose_next`) chooses the next row
that counts for looked-at, in list order and from the top at the end; a document opens at the
first of them.  With a filter on it stays inside the filter — the next not-looked-at row the
filter shows — while the counts above the list keep covering the whole document.
`counts_for_looked_at`, `next_unseen` and `first_unseen`
(`application/check_items.py`) are pure and on keys, so the list's rows and `looked_at_progress`
count the same rows.  The walk
keeps no state of its own: choosing a row is what marks it looked at, so the icons and the footer
say how far it got, and a card action does not move the choice — the teacher presses *Next* when
they are done with the item.

**The strip above the texts.**  One `_Banner` (`_refresh_banner`), and it shows the one reason
that matters most: the lock, a hide that failed, the reading fix, then `doc.warning` (the AI
warning).  Nothing that comes and goes with the 👁 toggle is in it, because a strip that appeared
and disappeared moved the texts under the cursor: the privacy state is the toggle's own, the
reason the texts cannot be typed into is the answer to a keystroke, the 👁 toggle says while the
names are still being hidden, and the AI warning is visible while the names are hidden.  The
reading fix's *Edit anyway* is greyed out, with the reason, while the names are hidden: the texts
would stay locked.  A failed hide is the exception — the names then stand in the text while the toggle
says they are hidden — so the strip says so and its button shows them again.

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
`ExportService.text_for_copy()` -- the same function *Export text* uses, so a
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
the pages it skips into `InputPayload.page_images` — at `pdf_render_dpi`, the same resolution
as the OCR page, because the scan is read by zooming into it — and `FileProcessor` hands them to
`persist_preview_artifact(pages=…)` as it hands the prepared pages.  The scan's strip
(`ScanPane` in `gui/check/_scan_view.py`) holds the *Prepared · Original* switch, offered only when a page
has an original and a step acted on it, and the caption of the steps that prepared the pages
(`prepared_caption`).  The scan opens fitted to the page; a double click toggles fit page / fit width.

`ScanView` draws each page from its own image and scales the item down to the pane
(`QGraphicsPixmapItem.setScale`, smooth), so zooming in shows the scanned pixels; the zoom stops at
twice the page's own pixels (`_ZOOM_NATIVE`), or at `_ZOOM_MAX` where the page is small enough that
four times the fitted size is already more.  Memory is bounded by `_full`: only the pages in view
and their neighbours (`_pages_in_view`) are held at their own size, the others hold a copy at
`_THUMBNAIL_WIDTH`, swapped in by `_refresh_full_pages` when the teacher scrolls or zooms.  A
text-layer PDF's pages are rendered at `pdf_render_dpi` for the same reason.

**The strips above the text** (`gui/check/_run_strip.py`): `NextStepStrip` over a transcription that
has not been proofread (*Proofread*, or *Set up proofreading…* via `CheckPage.set_proofreading_ready`)
and `RunStrip` while the open document is in a run, fed by `MainWindow._refresh_check_run`.  A run
started from the Check header keeps the tab (`_run_from_check`).

**Focus in the Check tab** (`gui/check/_text_views.py`, `gui/check/_side_panel.py`). Choosing an
item — by clicking its words in the text or its row in the list — marks it in both. The text
uses Qt *extra selections* (`CheckTextView._apply_marks`), not HTML, so a click neither
re-renders the page nor loses the scroll position; the chosen item is the one bright background
among the dark tints, and the list marks its row with the focus background. `reveal()` scrolls
to the item and puts the caret on its first word without selecting anything: a selection would
draw the theme's blue over the mark. A click on a mark
is found with `cursorForPosition()` → `PlainMap.offset()` → the item whose span contains it,
innermost first; tests click with `qtbot.mouseClick` on a position found that way, because
emitting a signal by hand would not show what a person sees.

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
panel shows them, the application service, the main window's `PullManager`, the system
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
collected, `SetupFacts` is what `SetupService.setup_facts()` measured (profile,
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
nothing; on a first start it leaves the current setup in place (`_apply_first_start_defaults`: the
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
thing: the real `SetupAssistant` and every page, the real `SetupService`
(`setup_facts`, `plan_setup`, the model listing and rating), the `LatestTask` pool, `InstallRound`,
`PullManager`, the `ollama` client over loopback HTTP, the real `MainWindow` and
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
| Expiry | `results_keep_days` at start (default: keep everything) + manual deletion | keep-last-N cap + manual deletion |

> **Note:** the pipeline cache is bounded on **Settings → Privacy**. The retention
> `results_keep_days` (default `0`, keep everything) drops the state of documents untouched for
> that many days at the next start, with their previews; the CLI never expires anything. *Delete
> stored results…* empties the cache by hand and can take the teacher's work with it. Re-running
> the same file overwrites its single entry in place, so it does not grow per run. Intermediate
> images (`*_step*.jpg`, `*_page*.png`) are cleaned, and a preview image is removed when its state
> is invalidated by a config change.

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
unit — the same path TXT files take, so `FileProcessor` skips OCR — and renders every
page as an image for the scan view (`InputPayload.page_images`).

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
  builders pick what they need.  `domain/pdf_text_layer.input_fingerprint(suffix, mode)` answers
  which of them affects a suffix, so the OCR config hash only contains the mode for PDF
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
Ctrl+Shift+= inserted above the line). The keys and the context menu's formatting actions are
offered only for a Markdown text: a plain text is stored through the plain renderer, which drops
every character style. Edits are converted back to Markdown (`document_from_qt` →
`MarkdownRenderer`) and saved like any other manual edit. Bounding boxes of layout engines are
not kept.

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
half-right review would show AI corrections as the teacher's own.  A stored review is checked
again when it is read (`application/merged_view.py`): one that no longer fits the stored
transcription — a restored backup, a text the teacher rewrote while the review stayed — is dropped
with a logged warning, and the changes are shown without explanations.

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

**The sentence-based review is the baseline's alone.**  `tools/evaluation/run/one_request/
sentence_review.py` holds it: notes per sentence, `splice`, and its own `align`.  Nothing under
`teachers_teammate/` imports it (`tests/tools/test_no_sentence_review_in_the_app.py` greps the
package), and the app's one review is the edit-based one above.

**The one-request path is the evaluation baseline, not the app.**
`tools/evaluation/run/one_request/` holds the answer schema, the answer formats, the policy presets
and `splice`; its `edit_review.py` turns the sentence answer into the edit-based review at its
edge, so the tools read one review shape everywhere.  Nothing under `teachers_teammate/` imports
it; `tests/tools/test_evaluation_session.py` keeps it that way.  A baseline row counts one
explanation per edit; the exchange rows are pinned by a test.

**The answer format is fixed.**  It is not a setting: the exchange's steps carry their
own answer shape (a plain text for ①, a schema for ② and ③), so there is nothing for a teacher to
edit.

**Failures** (`infrastructure/_llm_invoke.py:StructuredResult`). A schema-bound request
ends as success or as one of three failures. `transport`: the provider call raised; the
message is the complete "Correction failed (…)." sentence, and it becomes the `StageError` that
ends the file.
`unsupported`: the model or provider cannot bind a schema at all; no repair is attempted.
`invalid`: it answered, but not in the schema. An invalid answer with some text gets one
repair request that quotes the validation error. Unsupported, invalid-and-empty and
still-invalid answers all end in the corrections-only fallback for that file, with the
fixed fallback warning; the raw validation error is only ever sent back to the model.
A `transport` failure raises a `StageError` for that file; a step ① answer that cannot be used
is a `failed` outcome, so the unchanged source is never stored as a finished correction either;
a corrections-only fallback is an outcome and is stored. An
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
(`infrastructure/proofreading/corrector.py`) is the pipeline's `Corrector`: it runs the text-first exchange
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
| `infrastructure/proofreading/exchanges/` | `Exchange` protocol, `EXCHANGES` registry, `TextFirstExchange` |
| `infrastructure/proofreading/chunking.py` | `ChunkingCorrector`: a text longer than one request is proofread in pieces and the results joined; `Corrector.pieces()` counts them |

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
profile says: `ProofreadContext.corrections_only` makes `TextFirstExchange.proofread` return after ① with
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
Nothing outside `infrastructure/proofreading/` sees an exchange's own answer schema: each
exchange maps its answer to `ExchangeResult`, and the GUI, the DOCX builder and the cache read
`CorrectionReview` only.

## Error handling

### Design goals

The codebase follows three principles for error handling:

1. **Typed, descriptive exceptions** — each failure mode has its own class so
   callers can catch exactly what they need without inspecting message strings.
2. **Hard failures propagate; soft failures return warnings** — unrecoverable
   problems (missing provider package, unreachable Ollama service, unreadable
   input) raise exceptions and abort the affected file.  Recoverable problems
   in optional stages (LLM correction, evaluation) are captured as warning
   strings and passed to the caller so processing can continue.
3. **Structured logging instead of bare `print`** — all diagnostic output uses
   `logging.warning` / `logging.error` so the GUI's log panel and the CLI's
   stderr stream both receive the same messages without any special-casing.

### Exception hierarchy

All custom exceptions live in `teachers_teammate/exceptions.py`. A stage error is raised with a
text or with a `Message` (see "Where text lives"); `str(exc)` gives the sentence either way.

```{uml} assets/error_handling.puml
:align: center
:alt: Exception hierarchy and raising sites
:width: 100%
```

| Exception | Base | Raised by |
|---|---|---|
| `PipelineError` | `Exception` | base of every failure that ends one file |
| `StageError` | `PipelineError` | any stage that could not do its work (also `kind` + `stage`) |
| `FileSkipped` | `PipelineError` | a file left out on purpose (recognition off, file gone) |
| `OCRError` | `StageError` | OCR processor implementations |
| `ConfigFileNotFoundError` | `FileNotFoundError` | `storage_root.resolve_config_path` |
| `ConfigFileParseError` | `ValueError` | `config._read_toml` |
| `StorageResolutionError` | `RuntimeError` | `storage_root.resolve_storage_root` |
| `ProviderNotAvailableError` | `RuntimeError` | provider modules, `ocr_processor`, `stage_builder` |
| `OllamaUnreachableError` | `OCRError` | `ollama_utils`: server unreachable, HTTP error, unknown model |
| `ResponseTruncatedError` | `OCRError` | `ollama_utils.OllamaClient.chat` |
| `AnonymizerError` | `StageError` | `file_processor._hide_personal_info`, `._restore_pii` |
| `PipelineInputError` | `ValueError` | `input_provider_factory`, `preprocess_service` |

Inheriting from the appropriate built-in (`ValueError` for bad input,
`RuntimeError` for infrastructure failures, `FileNotFoundError` for missing
files) lets callers that do not import the custom class still fall through to a
sensible `except` branch.

### One error contract: no answer is an exception, a remark is an outcome

A stage that could not do its work raises a `StageError`; one that did work returns it, with a
remark when the result is not what it could be.  Nothing returns `(value, error)`.  The exception
carries the sentence a teacher reads in `str(exc)`, what failed in `kind` (`StageErrorKind`:
`engine`, `provider`, `anonymizer`, `preprocessing`, `storage`, `output`; it goes to the log) and
where in `stage` (`"ocr"`, `"correction"`, `"evaluation"`, or empty outside those three).
`FileProcessor.process()` handles it in one `except`, so a failure always ends the same way: the
file is `FAILED`, carries that sentence and no texts, what the stages before it stored stays
stored, and the next file runs.

What a stage *did* produce is an outcome:

- `Corrector.correct()` returns a `CorrectionOutcome` (`domain/proofreading/result.py`): the
  text, the review (`None` in corrections-only mode), an optional warning, and the flags
  `failed`, `fell_back` and `left_out`.  A corrections-only fallback or a partial review is an
  outcome with a warning.  An answer that cannot be used (the model added or dropped text, or
  answered with a remark around it) is an outcome with `failed`: the unchanged source and the
  reason; the file is saved with that warning and the source is never stored as a finished
  proofreading.  Only a request that never reached the model raises `StageError`, because there
  was no answer to judge and a retry would meet the same server.
- `Evaluator.evaluate()` returns an `EvaluationOutcome` (`domain/evaluation.py`): the report and
  an optional warning.  The review is optional, so a request that did not answer is a warning
  with no text, and the file is saved with its proofreading.

`FileProcessor` collects the warnings into `ProcessingResult.warnings` so the application layer
can surface them without interrupting the batch run.  A stop is neither: `RunStoppedError` and
`_Stopped` leave the file `stopped`, and `FileSkipped` leaves it *skipped*.

```python
# interfaces.py (simplified)
class Corrector(ABC):
    def correct(self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
                ) -> CorrectionOutcome: ...   # raises StageError: no request got through

class Evaluator(ABC):
    def evaluate(self, corrected_text: str, language: str, *, text_format: TextFormat = "plain"
                 ) -> EvaluationOutcome: ...
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

An online provider says the same thing in the response metadata, under one of three names
(`finish_reason == "length"` at OpenAI and Mistral, `stop_reason == "max_tokens"` at Anthropic,
`finish_reason == "MAX_TOKENS"` at Gemini).  `answer_was_cut_off()` in `_llm_invoke.py` is the one
place that reads it, and it counts metadata that says nothing as a whole answer, so a provider the
app cannot read is never reported as cut off.  `LangChainPort.ask_text()` maps it to the same
`"truncated"` failure as the server does, so a cut-off online answer is reported on both paths and
never stored as complete.

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
  `answer_tokens=EVALUATION_ANSWER_TOKENS` (1536, an estimate). That removes
  the artificial half — a review can use about (8192 − 1536) × 3 ≈ 19 900 characters minus its
  prompt.

`ensure_prompt_fits()` delegates here, and `OllamaClient.chat()` takes the allowance from the
caller (`answer_tokens`, `None` means "as large as the request"). Over the limit a proofreading
request warns `TRUNCATED_MESSAGE`; a content review warns its own sentence,
`TOO_LONG_FOR_REVIEW_WARNING` (*"The text is too long for a content review with a local model."*).
When both stages use the same model they get the same `num_ctx` (see below), so no reload
happens between them.

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
  smaller proofreading model; `run_rules.resolve_run_windows()` applies it to a `Config`, and
  a stage whose provider is online reports the base window (the app does not set one). When
  proofreading and content review use the same Ollama model, both get the smaller window: one reload per file between the stages would cost
  seconds on every run, and content review still gains from its own answer allowance.
- The window is sized for **one model at a time**. `run_files()` runs every stage of one file
  before the next file, so OCR and proofreading take turns and never run in parallel; a larger
  window can therefore make Ollama unload the idle OCR model and load the writing model on every
  file. That is an accepted cost, the same one `ollama_utils.py` already
  names for a small card: nothing slows down or fails, the models only swap.

The resolved window is passed down (`OllamaPort(num_ctx=…)`, `StageBuilder.resolved_windows()`)
and used by `proofreading_options()`, `ContextBudget`, the chunk budget below, the correction
cache fingerprint and the memory rating and run-start warning (`memory_at_context_gb`, per
model via `run_rules.window_of_model()`). The
fingerprint part `num-ctx-{…}` exists only for an Ollama corrector and holds the window the run
really uses (`RunWindows.proofreading`, resolved once and passed to the cache service), so results
made at 8192 and at 16384 cannot share a cache entry; the evaluation hash carries
`num-ctx-{windows.evaluation}` for the same reason — a review that failed with "too long" can
succeed at a larger window. Without resolved windows the key falls back to
`window_from_setting()`, the pinned window up to the proofreading cap.

The GUI asks `application/run.py:windows_for()` for the keys it
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

- a text within that budget goes to the inner corrector unchanged, in one request;
- a longer text is split by `domain/text_chunks.py` and each piece corrected on its own. The
  pieces are joined with their original separators and the reviews merged (`merge_reviews`:
  offsets shifted to the whole text, edit ids renumbered; see *Correction review*).

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

Failure is all-or-nothing: **any piece failed → the whole text has no proofreading**, because a half-corrected
file must never be cached: a `failed` outcome keeps the original text (`CHUNK_FAILURE` when the
piece gave no reason), and the `StageError` of a piece goes up unchanged. The pieces after the
failed one are not sent (their answers would be discarded, and each would wait out the timeout on an
unreachable server). A piece that fell back to
text mode does not fail the file; it costs the explanations of that piece and the merged result
says `MISSING_REVIEW_WARNING` (*"Explanations are missing for part of the text."*). Chunking is
deterministic for a text and a window, so the cache fingerprint carries the window and a later
change of it proofreads the file again.

Known costs: there is no context across a cut (a change that spans a boundary cannot be made,
which cutting at sentence ends keeps rare), and one request per piece is slower in total than one
big request would have been — if one could fit.

#### Measuring the context growth

`context_gb_per_8k` comes from `fit` (see *Model sizes*, above), which loads a model at `num_ctx`
8192, 16384 and 32768 and reads `ollama ps` and the card:

```bash
bazel run //tools:verify_model_catalog -- fit --store ~/git/teachers-teammate-evals --machine ID \
    --ollama-url http://HOST:11434 --gpu-agent http://HOST:11436 --tag TAG
```

It appends to `model-fit/sizes.jsonl` in the private evaluation repository, and `fit --report`
prints the figures without a server. A model without a figure stays at 8192. How fast a context
cache grows depends on the attention design (sliding-window or linear-attention layers grow much
more slowly), so it cannot be derived from the model size. The report puts the need at 8192 next
to the catalog's `memory_gb`, gives the growth per 8192 tokens from two spans (they must agree, or
the growth is not linear) and flags a step that ran partly on the processor. The figures go into
the catalog by hand, from `fit --catalog`.

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

The evaluation tools (`tools/verify_model_catalog.py`, `tools/evaluate.py`) pin
`PROOFREADING_NUM_CTX` (8192) on purpose: their verdicts are compared across models and across
months, and a window that followed the card of the machine would make two runs incomparable.

### Logging

Every module that writes a diagnostic creates a module-level logger
(`_logger = logging.getLogger(__name__)`).

`infrastructure/logging_setup.py:start_logging(data_root)` is the one place a line goes,
installed once per entry point through `application/composition.py:start_logging()`: the root
logger at warning, a rotating file `<data root>/logs/teachers-teammate.log` (1 MiB × 3), and the
same line on stderr in a process that has a console. A data folder that cannot be written leaves
stderr only; the app starts all the same. Help → Open Log Folder opens that folder.

A line may hold a file name, a stage name and the message of a failure the code expects — never
a document's text, a prompt, a model's answer or an access key. An unexpected error logs its kind
and its frames, not its message, which can quote the text it choked on
(`tests/unit/test_log_lines_carry_no_text.py` fails on a forbidden name in a line and on
`.exception`). A
sentence a teacher reads is not a log line: that is the `Reporter`'s, from `texts/`.

`_logger.warning(…)` is non-fatal and the work goes on; `_logger.debug(…)` is a detail nobody
acts on. The GUI worker redirects `sys.stderr` to a Qt signal, so a line reaches the in-app log
panel as well.

A handler that swallows a failure — its body only `pass`, `continue` or a constant `return` —
writes a line or carries a comment saying why nothing happens
(`tests/unit/test_silent_handlers.py`).

### Diagnosing a slow start or a window that freezes

`infrastructure/diagnostics.py`; off unless asked for. Start the app with `--diagnose` (for the
installed Windows app: a shortcut whose target ends in ` --diagnose`, or
`set TEACHERS_TEAMMATE_DIAGNOSE=1` in a terminal) and `logs/diagnostics.log` appears in the data
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

- At application startup (`frozen_entry.py`, `gui/__main__.py`, `__main__.py`), so packages
  installed during a previous session are immediately available.
- After every successful install or model download, so the newly installed package is
  usable in the same session without a restart.

The function is idempotent — safe to call multiple times.  Every entry point calls it first
(`application/composition.inject_addon_packages_dir()`): `frozen_entry.py` and `__main__.py`,
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
  runs and wheel installs get none of it (`addon_constraints_path()` is `None`).  pip checks TLS
  and the pins, not hashes: `--require-hashes` needs a hashed set per platform, and the lock is
  compiled on Linux only.
- **Outdated folders.** After an app update the folder may hold copies from the old bundle.
  `outdated_shared_packages(pkg_dir, constraints)` compares the folder names
  (`<name>-<version>.dist-info`) with the constraints at every start; when something differs
  `inject_packages_dir()` leaves the folder **off** `sys.path` and the app runs on its own bundle.
  `AddonService.addons_outdated()` tells the GUI: the requirement checks for a
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
  add-on's package shows up as a red run, not on release day.  The locked run also fails when
  its report names a package neither file pins.

### The add-on lock and the license list

The constraints file says what an add-on may **not** change.  What it *gets* is the lock:

- **`addon_lock.txt`.** `requirements_addons_lock.txt` is the pip-compile lock of the `llm` and
  `privacy` extras (`bazel run //:requirements_addons.update`, the same tooling as the test
  lock).  `tools/build/addon_lock.py` turns it into the flat `teachers_teammate/_build/addon_lock.txt` — the lock minus the packages
  `addon_constraints.txt` already pins, because two constraints files that disagree make pip
  refuse the install.  `pip_policy_arguments()` passes it as a second `-c`, so a teacher who
  clicks *Install* gets the versions this release was tested with instead of whatever is newest
  that day.  `tools/build/platform_lock.py` (run by `build_standalone.py`, bundled by the spec,
  and by the weekly resolution check) adds the pins that each add-on's own dry run on this
  platform needs and the lock lacks — a dependency only Windows or macOS needs
  (`tools/build/addon_pins.py`) — so nothing installs unpinned there either; a dry run that
  fails fails the build.  The smoke test
  (`addon lock`, frozen only) proves the file is in the bundle and pins every package
  `addon_manager.addon_package_names()` lists.
- **`third_party_licenses/inventory.csv`.** One list for everything the app ships or installs —
  the bundle's packages, the add-ons', the models, the build tools.  `tools/licenses/inventory.py`
  writes it: it reads the app's own lists (`[project] dependencies`,
  `addon_manager.addon_package_names()`, the `LocalModel`s, `SPACY_MODEL_PINS`), takes every
  version from the two locks, and downloads each locked wheel for its license text.  The same run
  compiles `third_party_licenses/third_party_license.txt` and the copy the GUI shows,
  `teachers_teammate/assets/third_party_licenses.md`.  `tools/licenses/policy.toml` holds the
  denied licenses (what cannot join a GPL-3.0 program, AGPL, non-commercial), the display names and the accepted
  exceptions; an `OR` expression is fine as soon as one alternative is.  The Add-ons page shows a
  package's license as the tooltip of its row (`infrastructure/addon_licenses.py`, reading the
  bundled copy of the inventory).  `--compile`, `--check-env` and `--check-resolved` run offline
  and are what the tests and the builds call; only the maintainer refresh touches the network.
  `docs/deployment.md` "Third-party licenses" has the flow.

`addon_lock.txt` is written at build time and is git-ignored (`teachers_teammate/_build/`); the
inventory is committed because it is a reviewable fact, not a build artifact.

### pip package install (frozen builds)

The frozen build bundles pip via PyInstaller's `--collect-all pip`.  Because
`sys.executable` in a frozen build is the application `.exe`, running
`[sys.executable, "-m", "pip", ...]` re-enters the frozen entry script without a recognised
dispatch flag and launches the GUI again instead of pip.

To avoid this, every pip install in a frozen build re-invokes the exe with the custom flag
`--_pip_install_mode` followed by the normal pip arguments, plus `--target <packages_dir>`.
The entry module (`teachers_teammate/frozen_entry.py`) intercepts this flag before any GUI code
is loaded
and calls `pip._internal.cli.main()` directly:

```python
# teachers_teammate/frozen_entry.py
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
`AddonInstallerDialog`.

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
# teachers_teammate/frozen_entry.py
if "--_spacy_download_mode" in sys.argv:
    _download_url = sys.argv[idx + 1]
    # call pip._internal.cli.main(["install", "--target", ..., _download_url])
```

**Two languages have no model.**  Spanish has none listed (`es_core_news_sm` is GPL-3.0, which
the license policy accepts); `it_core_news_sm` is CC BY-NC-SA (non-commercial), which it refuses.
Spanish and Italian documents are anonymized with the multilingual model like any other language
without one.  The languages stay in `domain/language.py`.

After a model download, `inject_packages_dir()` ensures the model is immediately usable.
`spacy.load("en_core_web_sm")` finds the model because `packages_dir` is on `sys.path`.

### Ollama models

Ollama models are pulled via the **Ollama HTTP API**, not pip.  The Add-ons page's
"AI models for this computer" tab calls `OllamaServerService.pull_ollama_model()`, which uses
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
| Built-in reader and page models (31 MB, 39 MB) | in the installed app; from source, HTTPS from `huggingface.co`, only on request (`ModelStore.download`) | the bundle / `<user data>/models/<repo>/<revision>/` | Never at start-up |
