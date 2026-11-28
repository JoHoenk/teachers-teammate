# Development

## Scope

How the code is built and why. Setup, everyday commands and the rules of a change are in
[CONTRIBUTING.md](https://github.com/JoHoenk/teachers-teammate/blob/main/CONTRIBUTING.md); the
windows in {doc}`gui`; model evaluation in `evaluations/README.md` and `evaluations/TOOLS.md`.

## Development checks

`bazel test //:lint` runs ruff, ty, shellcheck for the `.sh` scripts and PSScriptAnalyzer for the
`.ps1` scripts (skipped off Linux; its rules are in `third_party/lint/PSScriptAnalyzerSettings.psd1`),
all from `aspect_rules_lint`; a version bump is a rules_lint bump. Run them through Bazel only: a
`ruff` or `ty` from a local `.venv` can be another version. They cover the whole first-party tree,
the build, packaging and release scripts of `tools/` included, because those decide what ships. The
libraries they visit are the one `_LINTED` list in the root `BUILD`;
`tools/ci/check_lint_coverage.py` fails the lint job when a package is missing from it.

Size is not a lint rule: `tests/unit/test_class_size.py` is the gate for a class or function that
grew into several jobs, one ratchet per number.

The workflow files have their own linters (`lint-ci` in `.github/workflows/format_lint.yml`):

```bash
python -m venv /tmp/ci-lint && /tmp/ci-lint/bin/pip install -r tools/ci/requirements.txt
/tmp/ci-lint/bin/actionlint                    # syntax, expressions and the shell of every run: block
/tmp/ci-lint/bin/zizmor --offline .            # security; exceptions and reasons: .github/zizmor.yml
```

A workflow holds no logic. What a `run:` block would decide (a tag's form, the release files, an
install test) is a script in `tools/` with a unit test, so it runs and fails the same way on a laptop.

`bazel run //:format.fix` formats (ruff, buildifier) and applies the lint-safe fixes;
`//:format.check` only checks. How tests are written and run is in {doc}`testing`.

## Dependency locks

After a change in `pyproject.toml`, regenerate the pinned locks and check them:

```bash
bazel run //:requirements_dev.update
bazel run //:requirements.update
bazel run //:requirements_addons.update   # the lock of the add-on extras
bazel test //:lock_tests --test_output=errors
```

## Documentation build

```bash
bazel build //docs:html
bazel run //docs:html.serve
```

PlantUML draws the diagrams; its jar is the one Maven artifact (`MODULE.bazel`), pinned in
`maven_install.json`. Another PlantUML version needs `bazel run @blueprint_maven_dependencies//:pin`
afterwards. A diagram names modules and classes, never private methods, so it stays true when the
code behind them moves; `tests/tools/test_diagrams.py` holds the exception diagram to
`exceptions.py` and every diagram to a page that shows it.

## Architecture

The parts, innermost first. A part imports only from the parts in its row
(`tests/unit/test_layer_imports.py` enforces it):

| Part | Holds | May import |
|---|---|---|
| `pkg` | `exceptions.py`, `messages.py` | nothing |
| `domain/` | pure rules and concepts (stage names, freshness, model catalog, review, privacy levels); no I/O | `pkg` |
| core | `config.py`, `interfaces.py` (the ABC of every stage), `cache_keys.py`, `run_rules.py`, `settings_schema.py`, `atomic_file.py` | `pkg`, `domain` |
| `texts/` | every sentence a teacher reads; Qt-free | `pkg`, `domain` |
| `infrastructure/` | OCR engines, LLM providers, stores, the DOCX builder, the pipeline in `workflow/` | `pkg`, `domain`, core, `texts` |
| `application/` | the services, one per topic; the only entry for the adapters | `pkg`, `domain`, core, `infrastructure` |
| `gui/`, `cli.py` | adapters: translate input into service calls and render the answers | `pkg`, `domain`, core, `application`, `texts` |

The presentation layer never imports `infrastructure`. The modules that wire concrete classes
are the exception, and the only one: `gui/composition.py`, `gui/__main__.py`,
`cli_composition.py`, `__main__.py` and `frozen_modes.py`. `tests/unit/test_layer_imports.py`
holds both rules, and a third: a module publishes only the names it defines, neither with
`from … import Name as Name` nor as an `__all__` entry it imported from another layer (a
subpackage's `__init__.py` lists the names of its own modules, which is that package's API) — a
name has one home, and a caller imports it from there.

A service in `application/` does not reach into `infrastructure` from a method body. What it
needs of a module comes in through its constructor, typed by a `Protocol` in
`application/ports.py` (`OllamaServer`, `AddonState`, `HardwareProbe`, `NameModels`,
`ReleaseFeed`);
`application/composition.py` hands over the class of the running app, a test hands over a fake.
What still needs a lazy import — an add-on package that must not be imported at start-up, a
module that imports back — carries `# noqa: PLC0415`, and the count of those in `application/`
is the ratchet of `tests/unit/test_lazy_import_budget.py`. The modules that still import
`infrastructure` at all, and how many names each imports, are pinned the same way by
`tests/unit/test_application_infrastructure_budget.py`: a store, the pipeline or a subprocess the
service starts. A pure rule is never among them — it is imported from `domain` or the core.
`application/composition.py` is not in the table: naming the concrete classes is its job.

```{uml} assets/layers.puml
:align: center
:alt: The layers and what each may import
:width: 100%
```

The same test holds two more rules:

- Only the composition modules named in the diagram import `infrastructure`; every other adapter
  module does not.
- A module publishes only the names it defines, never `from … import Name as Name` and no
  imported entry in `__all__` (a subpackage's `__init__.py` lists the names of its own modules).
  A name has one home, and a caller imports it from there.

**Services and ports.** A service in `application/` does not reach into `infrastructure` from a
method body. What it needs comes in through its constructor, typed by a `Protocol` in
`application/ports.py` (`OllamaServer`, `AddonState`, `HardwareProbe`, `NameModels`);
`application/composition.py` hands over the running app's class, a test hands over a fake. A lazy
import (an add-on package that must not load at start-up, a module that imports back) carries
`# noqa: PLC0415`. Their count in `application/` and the modules that still import
`infrastructure`, with how many names each takes, are ratchets
(`tests/unit/test_lazy_import_budget.py`, `tests/unit/test_application_infrastructure_budget.py`):
the numbers only fall. A pure rule is never among them; it belongs in `domain` or core.

### What a run is made of

```{uml} assets/runtime_components.puml
:align: center
:alt: The parts of a run and the stores
:width: 100%
```

`application/composition.py` builds `AppServices` once per entry point; every other class is
given the service it uses. `OCRPipeline` is the composition root of one run and holds no stage
logic: `run_files()` iterates over the files and `FileProcessor` runs all stages for one file.
Two stores keep two kinds of fact apart: the cache (`StateRepository`) holds what the models
produced and no edit writes to it; `TeacherWorkStore` holds what the teacher changed and decided,
and `DocumentCommands` is its one writer (see *Storage, hashing & caching*).

### One run, one file

```{uml} assets/sequence_run_documents.puml
:align: center
:alt: Run selected documents
:width: 100%
```

```{uml} assets/activity_file_processor.puml
:align: center
:alt: The stages of one file and how it ends
:width: 70%
```

### Stages and where they run

```{uml} assets/pipeline_stages.puml
:align: center
:alt: The pipeline stages and the choices at each
:width: 100%
```

```{uml} assets/data_flow.puml
:align: center
:alt: What runs where and what leaves the computer
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
| run | a batch — one start over the selected files |

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

- **A failure goes through `gui/_errors.py`**, the only module that shows one:
  `show_failure(parent, notice, error)` writes the error down and shows the notice, and
  `show_unexpected(parent, error)` is a bug's generic notice with "Open Log Folder". A failed file
  gets no dialog at all: the row says why, and the end-of-run line counts it.

**The ratchet test.** `tests/unit/test_user_text_centralised.py` parses `gui/`, `cli.py`,
`application/`, `infrastructure/` and `domain/` with `ast` and fails on:

- a string literal or f-string handed to a sink that shows text: widget calls, dialogs, the
  readiness line's fixes, a log line, a CLI line, a prose keyword, a raised exception (the tables
  `_QUALIFIED`, `_SIMPLE`, `_MARKED`, `_KEYWORDS`, `_FIELD_KEYWORDS` in
  `tools/user_text/inventory.py`);
- a module constant whose name says a teacher reads it (`_CONSTANT_SUFFIXES`, `_SHORT_SUFFIXES`
  for one-word values; list, tuple and dict constants item by item);
- a sentence handed to a `texts` function (`dlg.copy_failed("Could not copy")`; a word without a
  space is an id and passes);
- a `texts` template filled with `.format` at a call site.

A sentence only *returned* or kept in a local variable is not a sink and goes unseen: build it in
`texts/`. Not sinks, listed in the same module: invariant raises (any in `domain/`,
`INVARIANT_MESSAGES`), the step reasons of `infrastructure/preprocessing/` (debug log),
`NON_TEXT_CONSTANTS`, and the Word file's label tables. A test fails on a listed entry whose literal
is gone. The allowlist `tools/user_text/allowlist.py` is empty and stays empty: move the sentence to
`texts/` instead. `bazel run //tools/user_text:inventory -- --out user_text.csv` writes a CSV of
what the scan finds.

**Out of scope, and must stay where it is:** the LLM prompts in
`domain/correction_prompts.py` (versioned and hashed into the protocol fingerprint and the
cache key — moving text changes cache keys), the developer log (`logging`, `logs/`), developer
exceptions that never reach a teacher, `tools/`, tests and docs. The Word output's en/de
label tables follow the *document* language, not the UI language, and stay in
`docx_builder.py` / `docx_comments.py`.

## Ollama model catalog

`domain/model_catalog.py` is the single source of truth for which Ollama models the app
knows; what it recommends and how it rates a model for a computer type (hardware profile) is in
`domain/model_fit.py`. Around them: `domain/model_tags.py` (tag spelling and lookups),
`domain/request_profile.py` (what is sent to a model), `domain/hardware_profile.py` (the profiles
and their detection) and `domain/system_facts.py` (what a server reports).

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
recommended) its tag to the matching `RECOMMENDED` entries.  A reading model the catalog does not
know yet is a candidate in `tools/ocr_eval/candidates.py` — tag, licence, where it came from —
and enters the catalog only after it has been read against the page set (see *Comparing reading
models* under *Text recognition engines*). Consistency tests in
`tests/unit/test_model_catalog.py` and `tests/unit/test_model_fit.py` check that every recommended
tag exists with the right category and that every best pick runs at full speed on its profile — new
entries must keep them green.

The static defaults (`DEFAULTS["ocr_model"]` / `correction_model` / `evaluate_model` and
the per-task provider defaults in `_model_defaults.py`) all come from
`DEFAULT_OCR_MODEL` / `DEFAULT_WRITING_MODEL` in the catalog module. They are independent
of the hardware profile — the per-profile `RECOMMENDED` lists only drive the GUI
recommendations — so a default may not fit a small computer.

`structured_output=True` gives a writing model the `explanations ✓` badge; it never switches
the proofreading mode. `DEFAULT_WRITING_MODEL` and the first entry of every
`RECOMMENDED["general"]` list must carry the flag — a test enforces it. The flag is not
guessed: `tools/verify_model_catalog.py --structured-output` (gate 1, see `evaluations/TOOLS.md`) checks every `general`
model that is reachable through a local Ollama server and reports the value to enter in the
catalog by hand, like the download sizes. A mismatch against the catalog counts as a failure. A
model without the flag (`mistral:latest`) is offered and used like any other model, but has no
badge and is not recommended.

A writing model that can also read scanned pages lists `also_uses=frozenset({"ocr"})`;
`catalog_uses()`, `suitability()` and `models_for()` then treat it as a reading model as
well (Settings → Add-ons still lists it once, under its primary category). Entries
whose tag starts with `hf.co/` are pulled from Hugging Face, so
`tools/verify_model_catalog.py` checks them against the Hugging Face file list instead
of the Ollama registry.

Every entry also names the terms its weights come under (`OllamaModelSpec.licence`, a
`ModelLicence` with the name, a link and — only when the licence restricts *use* — one sentence).
The app never ships a model, so a permissive licence (MIT, Apache-2.0) adds nothing a teacher has
to know; a restrictive one does, and only the default reading model has one.  `licence_note(tag)`
returns the sentence; the Add-ons model tree shows it in its *License* column and the Setup Assistant in the
tooltip of the download box.  Check a model's licence again when it is added or a tag moves to a
new version.

### Model sizes: what a model needs on the card

`OllamaModelSpec.memory_gb` and `context_gb_per_8k` say how much a model needs at 8,192 tokens and
how much it grows per further 8,192. `model-fit/sizes.jsonl` in the private evaluation repository
holds the measurements they come from (`evaluations/TOOLS.md`, *Model sizes and the memory agent*).

**How the catalog's figures come from it.** `memory_gb` is the need at 8,192 tokens and
`context_gb_per_8k` the growth, both from `fit --report --catalog`, entered by hand in one commit
that names the store lines' dates. Until a model has a measured growth it stays at 8192. The
figures are measured on one backend and cross-checked on another; where the two disagree, the
larger one is entered, because the model has to fit the machine that needs more.

### Rating a model against the computer's memory

`suitability()` / `rate_model()` rate a catalogued model for a task on a computer, from what
the app detected there (graphics memory, total main memory) or, when the profile was chosen by
hand, from the size the profile stands for.  `memory_gb` of a catalog entry is the total memory
the model needs at the proofreading window of 8192 tokens (card and main memory together),
measured where possible (*Model sizes*, above).

| Quantity | Rule (constants in `domain/hardware_profile.py`, tiers in `domain/model_fit.py`) |
| --- | --- |
| Card budget | `CARD_USABLE_SHARE` of the card's memory; a share of the unified memory on Apple Silicon (`HardwareDetection.card_gb`); none without a card |
| Reserve | the larger of `SYSTEM_RESERVE_MIN_GB` and `SYSTEM_RESERVE_SHARE` of the total main memory; swapping to disk is never counted as memory |
| Works (`WORKS`) | at least `FULL_SPEED_SHARE` of the model fits the card; without a card, up to `CPU_COMFORT_GB` (a page in under five minutes on an ordinary processor, measured with `fit --cpu-only`) |
| Slow (`SLOW`) | the part that does not fit (the *spill*) fits in main memory beyond the reserve; `ModelRating.very_slow` when under `SLOWER_SHARE` of the model fits the card, or there is no card |
| Does not fit (`TOO_LARGE`) | the spill does not fit; also whenever the main memory is unknown (a remote server) and the model is not within the card |
| Recommended (`BEST` / `GOOD`) | the tier's list, but never for a model that does not fit the computer's known memory |

The two share thresholds come from speeds measured on one 8 GB card and one processor; they are
ratios, so they scale with the computer.

`RunChecks._memory_shortfall()` is the run-time counterpart, used by
`check_run_preflight()` (both adapters) before a run: for an Ollama on this computer,
the part of every catalogued model that does not fit the card (all of it on Apple Silicon) has to
fit the main memory that is free *right now*, minus `RUN_MEMORY_MARGIN_GB`.  It reads
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

## Speed estimates: the test, the real runs and the time left

Three questions, three answers, one owner each:

| Question | Answered by | Where |
| --- | --- | --- |
| How fast is this computer? | the speed test, when the teacher asks for it | `infrastructure/performance_probe.py` |
| How long will a run take? | the estimate from the store | `application/speed_estimates.py` |
| How long is left? | the pace of the run so far | `gui/_run_status.py` |

No widget computes a duration: `texts/review.py` formats the figures the service returns, and
`gui/_run_status.py` turns them into the note on the progress bar.

### Where the teacher sees it

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

An answer is measured as a part per text plus a part per word, not as a rate per 100 words: an
answer restates its finding and its notes whatever the text's length, so a rate per 100 words
is too much for a short text and too little for a long one.  The estimate is therefore about a
text of a known length, and passes it in: the compare window sends the opened document's own
words, the settings panel and the time left of a run keep `TYPICAL_PAGE_WORDS`, because no text
is known before a file is read.  A file's pages are proofread as one text, so only its first page
pays the part per text: `StepEstimate.further_page` is what each further page adds, and the class
set and the time left (once the page counts are known) charge it for every page after the first.

### What is measured, and with which request

The test sends the app's own requests, so a figure means what a run will cost:

| Step | Request | Answer length |
| --- | --- | --- |
| Reading | the OCR prompt with a drawn A4 page | tokens per page |
| Proofreading | step ① of the exchange on a test text | tokens for that text |
| Review | the content-review request on the same text | tokens for that text |

The proofreading step measures step ① because it is the request every run sends, and the test
measures rates, not answer lengths: a plain rewrite of the paragraph, at that step's temperature
and thinking, with no answer schema.

`MEASURE_ANSWER_TOKENS` caps the two writing steps: enough for a steady rate, short enough
that the whole test stays under a minute and a half on a card.  `MEASURE_TIMEOUT_S` is the
limit per request; a step that hits it is `TOO_SLOW`, and its figure is a lower bound at best.  A
warm-up request (`WARMUP_PROMPT`, one token) loads the model first, so the load time is reported
separately instead of being counted as speed.

The test page is drawn (`draw_test_page`, `TEST_PAGE_WORDS`) and then preprocessed exactly as
a run preprocesses a scan, because the image the model sees decides the image token count.

### The maths (`domain/performance_estimate.py`, pure)

| Piece | Meaning |
| --- | --- |
| `ModelSpeed(read_tps, write_tps, image_tokens)` | one model on one machine: request tokens per second, answer tokens per second, tokens of one page image |
| `TimeRange(low_s, high_s, typical_s)` | a duration as a range; `scaled` and `__add__` keep the three figures together |
| `AnswerLength(low, typical, high)` | an answer length as three figures: one request's answer, or all the steps' answers for one text |
| `AnswerCurve(fixed, per_word, low, high)` | how long one step's answer is: a part per text and a part per word, with what the runs answered against it as the range |
| `ExchangeCost(requests_per_page, answers, text_copies)` | what an exchange costs per step; `DEFAULT_EXCHANGE_COST` holds the text-first exchange's figures |
| `answer_tokens(cost, steps, words)` | the steps' answers for a text of *words* words, as a range |
| `fit_answer_curve(points, *, narrow)` | the curve through the runs' own `(words, answer tokens)` pairs; `None` without two text lengths to tell the fixed part from the per-word one |
| `proofreading_time(speed, words, cost, steps, *, answer=None)` | a proofreading run: the text times the copies of the steps that run, plus their answers; *answer* is the tokens a run measured for that text |
| `band(seconds_per_page)` | `SpeedBand`: `FAST` up to `FAST_LIMIT_S`, `OK` up to `OK_LIMIT_S`, `SLOW` up to `SLOW_LIMIT_S`, else `VERY_SLOW` |
| `machine_factor(measured, reference)` | how much slower this computer is than the reference machine; `None` when either figure is missing, so "unknown" is never read as "the same" |
| `median_factor`, `percentile` | the middle of several models' factors; the 10th and 90th percentile of a set of figures |
| `MachineFactors(writing, reading)` | the two factors, with `for_category` picking one: a card can be better at reading a page than at writing an answer |
| `expected_speed`, `expected_band` | a model's reference speed carried to this computer by the factor, and the band of the middle of the resulting range |
| `SpeedSource` | `MEASURED` (this model, this computer) or `EXPECTED` (from the reference machine) |

The constants that stand in for a measurement, and what their values rest on:

| Constant | Rests on |
| --- | --- |
| `TYPICAL_PAGE_WORDS` | the median of the pages measured, used only where no text is known yet; a run works in pages before a file is read |
| `CHARS_PER_WORD` | German prose is the longest of the three languages (about 6.2), English the shortest (about 5.5) |
| `DEFAULT_CHARS_PER_TOKEN` | between the window budget's cautious 3 and the 4.3 to 5 measured on German prose at page length |
| `DEFAULT_IMAGE_TOKENS` | a full A4 page at 300 DPI; how many tokens a vision model makes of it depends on its own encoder |
| `CONTENT_REVIEW_ANSWER` | a review answers with a short assessment; deliberately wide |
| `DEFAULT_READING_ANSWER` | a page of about 250 words plus the markup a reading model adds |
| `DEFAULT_EXCHANGE_COST` | the exchange's per-step answers fitted over the runs of the reference machine: a part per text, a part per word, and the 10th to 90th percentile of what the models answered against that line |

A model without an `answer_length` gets the median over the catalogued models of its kind, not a
constant (`default_answer_length`).

### The store (`infrastructure/speed_store.py`)

Two files, both versioned by `SCHEMA`, both keyed by what makes a figure go out of date:

| Store | Key | Holds |
| --- | --- | --- |
| `SpeedStore` | `SpeedKey`: server, tag, digest, Ollama version, graphics card | the test's result per step, with its outcome and message |
| `SampleStore` | `SampleKey`: server, tag, step, mode | the last `KEEP_SAMPLES` samples of real runs |

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
page or one document into one `RunSample`, and counts a load only from `LOAD_THRESHOLD_S` up,
so a model that was in memory does not look like one that had to be loaded.  Collecting statistics
never fails a document: a sink that cannot write (full or read-only disk) is logged and skipped.
A step that failed, or whose answer came back without a review (`CorrectionOutcome.fell_back`: a
report of step ② or ③ failed, so the answer lacks part of what was asked), is discarded: the
requests of the failed attempt would be counted into the answer length of the mode that finally
answered.

### Real runs win over the test

`speed_summary` prefers the samples to the stored test, per step and per model:

- from `MIN_ANSWER_SAMPLES` samples the run's own answer length replaces the exchange's figures:
  they are this class's error level with this model and these instructions, which no test can
  know.  A writing model's answers are fitted as a curve, so the samples have to cover two text
  lengths; one length says nothing about where the line crosses zero.
- from `PERCENTILE_SAMPLES` the range is the 10th to the 90th percentile, so one runaway
  answer no longer widens it.
- a step whose samples are missing falls back to the test, and a step with neither keeps its row
  in `SpeedSummary.unmeasured`, with the band the catalog expects here.  `class_set` is `None`
  while any step is unmeasured: a sum that left one out would promise a time that is too short.

`SpeedSummary.sample_count` and `sample_pages` say which of the two a row rests on, and the panel
says it in words ("Based on your last 12 proofread documents").

### The time left

`RunPace` (`gui/_run_status.py`) starts from the store's seconds per page.  After the first file
the run's own pace takes over, blended with the estimate for the first `BLEND_FILES` files: one
fast file is not a pace.  `pages_left` counts the pages of the files that are not done yet, from
the run's page counts when they are known and from the average of the files done so far otherwise.
Nothing known at all gives no figure, which is better than a guess the teacher would plan around.

### Which models have reference figures

Only the catalog entries with a `reference_speed` get an expected band, and no entry has a
`read_tps`: a reading model gets no expected band, and `MachineFactors.reading` stays `None` until
reading models are measured on the reference machine.  `tools/dev/ocr_page_tokens.py` measures the
answer tokens a reading model needs, `tools/ocr_eval/cli screen` its seconds a page, and
`report --performance --catalog-lines` prints the `answer_length` lines for the writing models.

## Who owns what (hardware detection and recommendations)

| Piece | Where | Rule |
|---|---|---|
| Decisions (best model, suitability, `rate_model`) | `domain/model_fit.py` | Pure functions, no I/O — table-test them. |
| Decisions (the hardware profile and its detection) | `domain/hardware_profile.py` | Same, and the thresholds every one of them reads. |
| The local hardware probe | `application/model_recommendations.py` | One owner: it runs once per process and describes this computer, not the server. The memory and GPU readers and the context choice live here too (`available_memory_gb`, `read_gpu_usage`, `read_system_usage`, `context_choice`). |
| What a server answered (version, installed models with digests and sizes, what a model can do) | `application/ollama_server.py` | One owner. `snapshot(url)` asks and holds the answer, `cached_snapshot(url)` never asks, `forget(url)` drops it — a pull does by itself. No other service keeps an Ollama cache; what is in memory (`/api/ps`) is never held. |
| Texts a teacher reads | `teachers_teammate/texts/` | Qt-free; widgets never build these sentences themselves. See "Where text lives" above. |
| Widgets | `gui/_hardware_profile_selector.py`, `gui/_model_guidance.py`, `gui/settings/_addons_page.py` | Presentation only. They rate models through `rate_for(..., cached_only=True)` — never contacting Ollama on the GUI thread. |

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

## AI services: providers, model lists and install hints

Proofreading and content review share one widget, `AiServiceSection`
(`gui/settings/_ai_service_section.py`); an `AiServiceSpec` only names the settings keys of
the step.  What it relies on:

- **Each provider module declares its package** in `PROVIDER.package`
  (`AddonPackage(module="langchain_mistralai", pip="langchain-mistralai")`); Ollama's connector
  is `package_optional` because the app talks to Ollama directly.  The Add-ons tables in
  `addon_list.py` are built from these declarations.  `ProviderService`
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

## Finding the Ollama server

Rules in `domain/ollama_discovery.py` (pure: `OLLAMA_HOST` parsing, `/proc/net/route` and
`resolv.conf` parsing, `classify_host`, `scan_targets`), I/O in `infrastructure/ollama_discovery.py`
(`probe_server`, `discover_this_computer`, `scan_network`) and `infrastructure/wsl.py`.  The only
entry point for the GUI is `OllamaServerService.discover_ollama_servers(scope)`.

What an address *is* — its normal form, its host, whether it points at this computer, whether a
model name is installed — is `domain/ollama_address.py`, pure and reachable without a lazy import.
It takes this computer's names as lookups, so the cheap loopback check answers the default address
without a socket or DNS call.  Both lookups are required arguments; `OllamaServerService.is_local`
and `ollama_host.ollama_server` pass `own_host_names` and `windows_host_addresses`.

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

## Stopping a run and run outcomes

A run has two stop events, both owned by `OCRWorker` and set through `request_stop(now=...)`:
`stop_after_file_event` ("Stop": the file being processed finishes completely, no new file
starts) and `stop_event` ("Stop now": also cancels the Ollama request being made and stops
the file at its next step).  The Stop button is two-step (`StopStage` in `gui/_run_session.py`).

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
  The stages check the event between steps (`StageShared.stopping()`) and **before** a slow
  anonymization pass, and each raises `Stopped` (`stopped_result(stage, ...)`) itself.
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
  (`MainWindow` asks first; File → Quit asks the same), then
  `request_stop(now=True)`, disconnects the worker's signals (no dialog on a hidden window) and
  waits at most `CLOSE_GRACE_MS`.  A worker still running after that is detached
  (`_worker.detach_running_worker`) and `_app_bootstrap.finish_process` ends the process with
  `os._exit` instead of destroying a running `QThread` (which aborts).
  `StateRepository.save` writes a temporary file and swaps it in (`os.replace`), because the
  process may now end during a save.

## Keeping the computer awake

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

## Request profiles: temperatures and prompts per model

What differs per model in the request the app sends is one `RequestProfile`
(`domain/request_profile.py`); `request_profile(tag, provider)` looks the model up in the catalog
(`domain/model_catalog.py`) and fills it in, and the evaluation tools
send the same request (`evaluations/TOOLS.md`). The corrector, the *What is sent for this file*
dialog and the correction cache key (`RequestProfile.fingerprint()`) read the one object, so the
dialog shows the request that is sent and a profile change re-runs only that model's corrections.

- **One `StepProfile` per step** (① correct, ② explain, ③ unclear): `temperature`, `think`,
  `options` and, on step ① only, `prompt`.
- **Temperatures.** Step ① rewrites the text, so its value is measured: `STEP_ONE_TEMPERATURES`,
  else `STEP_ONE_DEFAULT_TEMPERATURE`. Steps ② and ③ run at the value of the model's card
  (`MAKER_TEMPERATURES`), else `DEFAULT_CORRECTION_TEMPERATURE`. The content review resolves its own
  (`review_temperature`, default `DEFAULT_REVIEW_TEMPERATURE`). An online provider gets 0.2 in
  every step; text recognition is fixed at `OCR_TEMPERATURE`. Teachers set none. Read the tables
  only through `request_profile()` / `review_temperature()`.
- **Thinking.** `think=False` for every model, except a family in `THINK_LEVELS` that ignores the
  flag.
- **Sampling values** (`options`, names from `SAMPLING_OPTIONS`) are empty by default: the model's
  own tag (its Modelfile) decides top-k, top-p and the penalties.
- **Prompt variants.** `STEP_ONE_PROMPTS` names the step-① variant (`CORRECT_VARIANTS` in
  `domain/correction_prompts.py`) a model is sent; empty until a measurement on both test sets
  adopts one. A variant a measurement used never changes its text — another text is another id —
  because the cache key hashes it (`prompt_identity()`). Another quantisation of a listed model is
  sent the same variant.
- **`compact_json`** (default off) adds `COMPACT_ANSWER_RULE` to a one-request review. The exchange
  does not send that request; the evaluation tools measure it per model.
- **A cut-off answer is asked once more** (`cut_off_tries`: not for an online service and not at
  temperature 0, where it would be the same answer); a cut-off *repair* answer is not retried
  (`infrastructure/proofreading/runner.py`).

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

### The lines of a page

`PpOcrProcessor.read_page` returns a `PageReading` (`domain/page_layout.py`): the text
`process_image` gives, plus a `ReadLine` per non-empty row — its text, the lowest `ctc_decode`
confidence of its boxes, and its boxes' corners as fractions of the page's width and height, so
a mark drawn over a stored image of another size still sits on the line.  `process_image`
returns `read_page(...).text`.  An engine without a layout of its own — an Ollama reader, a
PDF's text layer — takes the interface's default and returns the text alone.

**Do the rows line up with the written lines?**  `tools/dev/page_lines.py` measures it:

    bazel run //tools/dev:page_lines -- --pages ~/pages

A folder under `--pages` is a group (`handwritten`, `photographed`, `printed` …); beside every
page image sits its transcription, one reference line per written line (`s01.png` and
`s01.gt.txt`).  A PDF is read page by page, each page rendered as a run renders it: a one-page
PDF takes `s01.gt.txt`, and the pages of a longer one are counted (`scan.pdf` page 2 →
`scan_p2.gt.txt`).  Each page is read on the automatic preparation (the preset `R`) and the reader's
words are aligned with the transcription's (`word_diff`).  A row is *lined up* when at least
90 % of its aligned words come from one reference line and that line sends at least 90 % of its
own aligned words back to the row; otherwise it is *merged* (one row, two written lines) or
*split* (one line over two rows), and a row none of whose words matched is *unaligned*.  A row
*has an error* when any of its words is not read exactly.  The report gives those counts per
group and, for confidence thresholds 0.50 … 0.95, the share of rows a *Check first* mark would
show at that threshold, the share of those that hold an error, and the share of error rows it
would catch.  Numbers only: no page's text is printed.

**Where the references come from.**  They are typed from the page; a Chandra OCR run
(`datalab-to/chandra`) of the same pages can stand in for the typing:

    bazel run //tools/dev:chandra_gt -- --chandra ~/chandra-out --pages ~/pages

It reads the layout blocks Chandra saved (`<stem>/<stem>_chunks.json`: per page its blocks, their
labels and their HTML), takes their text in reading order and breaks it where a block breaks a
line — `<br>`, a paragraph, a list item, a table row — leaving pictures out; a Markdown output is
the fallback, and only one written with page separators.  It writes the transcription where the
measure looks for it and keeps one that is already there unless `--overwrite` is given.  Chandra
joins written lines into paragraphs wherever it can, so a page it read as paragraphs gives a
reference of paragraphs: that page's rows stay *split* instead of *lined up* and it has to be
typed by hand.  Such a reference is no truth, only what one other model read.

### Comparing reading models, and where the defaults come from

`tools/ocr_eval/` reads one page set with the app's own preparation, the app's own reader
(`PpOcrProcessor(store, settings=…)`), the app's own Ollama request (`ocr_options`,
`encode_image`) and the app's own OCR answer handling (`clean_ocr_text`, `OcrOutputPostprocessor`,
`plain_text_of`), and keeps the numbers in the store:

```bash
bazel run //tools/ocr_eval:cli -- screen --url http://127.0.0.1:11434
bazel run //tools/ocr_eval:cli -- run --store $EVALS --machine ID --pages ~/pages \
    --model qwen3-vl:8b --preset R
bazel run //tools/ocr_eval:cli -- report --store $EVALS \
    --baseline qwen3-vl_8b--R--app--gpu--rx9070xt-server--1a2b3c
```

`screen` asks what a candidate is before any page of the set is spent on it — the tag pulls, the
server lists `vision`, the answer on a drawn page is not empty — and times the drawn pages of
`screen.LINES` for every model, with the app's prompt and with the one its card names, and for the
built-in reader. One table, machine named; a tag the server does not hold is a row of that table
and a line on stderr, and the rest is timed anyway. The reader's row is the number a `cpu`
recommendation needs. The licence a row shows is the catalog's or the survey's claim: the developer
reads the model's card, and a wrong claim is corrected in `candidates.py`, not in the table.

`run` reads the set: `--model` a tag or `reader`, `--preset` `raw`, `R` (the automatic
preparation), `R-<step>` with one automatic step left out, or `R-colour`; `--prompt app|card`,
`--detector key=value`, `--cpu-only`. The run's name says what it is — model, preset, prompt,
device, machine and a hash of the rest — so the same ask twice is refused; `--replace` writes over
the old file, and the store is a git repository. `report` takes `--runs` and `--baseline` as that
name, with or without its campaign folder in front. Every reading is cached in the tool's own
folder in the user's cache directory, keyed by what decides it (`readings.key_of`), so a rerun of
the same ask reads nothing again. A reading is a page's text: it stays on the machine and never
enters the store, which takes one numbers-only record per run (`ocr/runs/`, checked by
`validate`).

A page scores CER, WER and bag-of-words WER (reading order ignored), the share of the student's
misspelt words a model read exactly as written (*kept mistakes*: a model that silently corrects
hides what proofreading is there to find), failure flags, seconds and the answer's tokens. A
transcription wraps a misspelt word as the student wrote it (`{Fehler}`) and writes `[?]` where
nobody can read one. `report` gives one row per run and group, each with a paired bootstrap
interval against a baseline run over the same page ids, weighted like the row's own rates: a
difference whose interval holds zero is none.

The set is the developer's own pages, outside the repository, with a version (the hash of its
images and transcriptions); runs of two versions are not compared. Its `pages.jsonl` names every
page by a neutral id, with its writer and its language, which the page's prompt names. It is split
once, whole writers held out (`pages.TUNE_SHARE` to choose on, the rest to confirm a choice), which
only catches a large reversal.

A measured result changes the app in its own commit: a threshold in `auto.py` and an
`AUTO_VERSION` bump, a `DetectorSettings` number and a `READER_VERSION` bump, `auto_output`, the
catalog's notes and `RECOMMENDED` order, or a per-model OCR prompt. A candidate tag enters
`OLLAMA_CATALOG` only through `tools/verify_model_catalog.py`.

**Adding an engine** is in {doc}`extension_points` ("New OCR engine").  A settings file that names a
retired engine (`RETIRED_OCR_ENGINES`: `tesseract`, `paddleocr`) is read as `ppocr` by
`settings_schema.normalize_ocr_engine`, with a notice that names the engine; an online engine falls
back to the default.

### Where a sentence stands on the page

The Check tab marks the sentence under the cursor on the scan.  The fuzzy work happens once per
reading, between two readings of the same page; everything after it is between clean texts:

    boxes ──①── reading (R) ──②── transcription (T) ──③── corrected text (C)

① is `infrastructure/text_alignment.py`: RapidFuzz's character opcodes of the joined pages against
the plain text of the stored reading, then `domain/page_layout.py:place`, which keeps a line only
where its stretch of R is at least `PLACE_MIN` similar to what the reader read.  It runs only while
an engine reads the pages (`infrastructure/workflow/scan_places.py`, built by
`StageBuilder.build_scan_places`), and the places are stored beside the reading
(`DocumentState.raw_layout`, `LAYOUT_VERSION`, the identity of R).  They are never made afterwards:
a reading without them, or with places that are stale or of an older version, shows no mark until
it is read again.  A PDF's text layer and the teacher's own transcription get none.  An engine
with no layout of its own (an Ollama reader) is joined by the built-in reader, which reads each
page on a thread of its own while the engine's requests run; without its models there is no
layout, no mark and no message.  `Config.scan_marks` switches all of it off, and
`DocumentService.check_document` then serves no places.

② carries the places into the teacher's transcription (`TextFormatter.plain_text` of
`DocumentStateView.machine_raw_text` against `raw_text`) and ③ is `mark_for_cursor`
(`application/check_document.py`): the sentence is the corrected text's where there is one,
carried into the transcription, and the transcription's otherwise.  A sentence is marked when
`COVER_MIN` of its characters sit on placed lines; a passage no sentence mark closes falls back to
the line under the cursor, and everything else shows the page instead.  `page_layout.sentences`
joins the spans `sentence_spans` splits at a dotted letter, an abbreviation or a day (`15.`), and
at a year or `usw.` only before a lower-case word — the splitter a stored review numbers its edits
by does not change.

**Measured on 8 handwritten pages** (4 ink photos, 4 pencil scans, 178 written lines): the
built-in reader's rows line up with 78 % of the written lines (43/43 printed); how far a row's
confidence predicts its misread words is the comment on `CONFIDENCE_BANDS`, and which alignment
holds on misread text, at what cost, the docstring of `infrastructure/text_alignment.py`.

**Not measured:** those figures come from one typed reference beside the pages, not from an Ollama
reading of a teacher's own scans, and the gate behind the decision — at most 5 % of the shown marks
wrong while at least 70 % of the sentences are marked — has not been run: no local vision model and
no corpus were at hand.  The thresholds are the code's (`PLACE_MIN`, `COVER_MIN`,
`CONFIDENCE_BANDS`).

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
changed constant and an `AUTO_VERSION` bump.  What a step is worth is measured, not argued:
`bazel run //tools/ocr_eval:cli -- run … --preset R-<step>` reads the same pages with that step
left out, and `report` compares the two runs page for page (see *Comparing reading models* above).

**Page models.**  `DOC_ORI` (PP-LCNet_x1_0_doc_ori) and `UVDOC` are `LocalModel`s in
`domain/local_models.py` (group `PAGE_MODELS`, Apache-2.0, PaddlePaddle).  The installed app
carries them (see *Local model files*); otherwise they are downloaded only on request: **Download…** on Scan quality, the **Page models** box on Add-ons → Reading models
(`PAGE_MODELS_ITEM` of `domain/install_item.py`, between the reader and the Ollama pulls in
`InstallRound`), or
`--download-page-models`.  Missing page models are **not** an `IssueKind`: a run never waits for
them; the CLI prints one notice line (`texts/addons.py:PAGE_MODELS_SKIPPED_NOTICE`).  `UVDoc`
answers an image at
the size it was given, so the page goes to it scaled to at most `FLATTEN_MAX_SIDE`.

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
SHA-256.  `READER_MODELS` are the reader's two sets.

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
- A run reads the store its service checks (`RunParts(model_store=)` →
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
| data | `resolve_data_root()` | `speed/`, `work/`, `compare_marks/` (next to `packages/` and `models/`, which have their own lookup) | `~/.local/share/teachers_teammate` / `%LOCALAPPDATA%\teachers_teammate` / `~/Library/Application Support/teachers_teammate` |

The `TEACHERS_TEAMMATE_TMPDIR` environment variable, when set, replaces all three with that one
folder (tests, portable use). The rule: what a teacher cannot rebuild (settings, speed
measurements, their own corrections, their marks on a comparison) never goes into the cache.

**Moving from the cache.** `resolve_config_path(None)` and `resolve_data_folder(name)` move an
`ocr.toml`, `speed/` or `work/` found in the cache folder to its own place the first time they
are asked for (`migrate_once`: only when the new one is missing; when
both exist the new one wins and the old one stays; a move that fails is logged and the old file
is still read). On Windows the config and data folders are the same, so the uninstaller's
settings switch removes `ocr.toml`, `speed\` and `work\` there.

```
<storage_root>/                                    # the cache
├── artifacts/
│   └── <output_dir_key>/                          # key = sha256(output_dir.resolve())[:16]
│       ├── <stem>_<key>_preprocessed.png          # prepared page 1 (the preview image)
│       ├── <stem>_<key>_p<N>.png                  # prepared pages 2..N, listed in DocumentState.page_images
│       ├── <stem>_<key>_original.<ext>            # page before preprocessing (only if steps changed it)
│       └── state/<stem>_<key>.json                # CACHE: one DocumentState per (output_dir, path)
├── compare/
│   └── <text sha>/<key>.json                      # CACHE: one model's answer in a comparison
<config_root>/ocr.toml                             # configuration (the pipeline config)
<data_root>/
├── compare_marks/
│   └── <text sha>.json                          # WORK: the teacher's verdicts on one text's comparison
├── speed/                                         # measured speeds (samples.json, measurements.json)
└── work/
    └── <sha256>.json                              # WORK: one TeacherWork per essay, keyed by its content
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
  temperature). The engine is hashed twice, a shape the golden digests pin. For the built-in reader (`ppocr`) the model slot is `reader_fingerprint()`
  (`PP-OCRv6_small@<det rev>+<rec rev>/v<READER_VERSION>`) and the language, prompt and
  temperature slots are constants: the reader reads every language with one model, so a
  changed language or Ollama model never re-reads a page, while a new model revision or
  `READER_VERSION` does. A test pins an Ollama key, so the Ollama shape cannot drift.
  The models themselves live in the user data folder (`models/`), not in the cache. Used by the
  cache to detect stale stages.
- `review_key` hashes the review's instructions and the wording the app adds around them
  (`review_wording()`: the message and the Markdown hint), so changed wording sends a review again.
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

**The state schema.**  A file of another `StateRepository._SCHEMA_VERSION` is read as missing, so the document is processed again: a review stored in another shape would
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
emptied) and the work has a transcription part, `ReadingStage._run_preprocess_and_ocr` prepares
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
while the cache still holds a proofreading made from it.  An empty value means "made from `raw_text`".

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
should be.  This is the limit of keying by content: one part per answer would cost more than it
saves, because two copies of one essay are rare and the shared transcription is the effort that
matters.

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
  `GUI` writes the cells' lines from `view.sent` after every load, including the one after a file is done; the dialog shows one line per stored result in a grey block at the
  top of the *What is sent* tab (`stored_lines()`, `objectName="stored_results"`).

### The cache's shape

The pipeline **cache** (`StateRepository`) memoizes the latest result: one entry per
`(output_dir, path)`, keyed by the file *path*. It stores only a *hash* of the settings, enough
to detect a stale stage.

> **Note:** the pipeline cache is bounded on **Settings → Privacy**. The retention
> `results_keep_days` (default `0`, keep everything) drops the state of documents untouched for
> that many days at the next start, with their previews; the CLI never expires anything. *Delete
> stored results…* empties the cache by hand and can take the teacher's work with it. Re-running
> the same file overwrites its single entry in place, so it does not grow per run. Intermediate
> images (`*_step*.jpg`, `*_page*.png`) are cleaned, and a preview image is removed when its state
> is invalidated by a config change.

**Comparisons use the same cache.** A model's answer in a comparison is
`<storage root>/compare/<text sha>/<key>.json` (`infrastructure/compare_store.py`), keyed by the
text and by the proofreading key of that model's settings, so a model the teacher compared twice
costs one request. *Run again* keeps the second answer beside the first (`<key>~2.json`), so the
window can say which of the model's changes came in both runs. It holds the student's text and can be made again, so it is a cache file: the
retention expires it and *Delete stored results…* removes it.

**The marks on a comparison go the other way.** What a teacher calls right or wrong is their own
work and holds no text: `compare_marks/<text sha>.json` in `resolve_data_folder("compare_marks")`,
one file per text, each mark the character span of the student's words plus the hash of the
replacement (`domain/proofreading/option_marks.py`,
`infrastructure/option_mark_store.py:OptionMarkStore`). Neither the retention nor *Delete stored
results…* touches it: a mark survives a re-run of a model and a lost answer, and a text that
changes is another text with no marks.

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
- The preprocessing preview always renders an image.

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
notation of the raw text, so one `raw_text_format` field covers both. The field defaults to plain; an entry
whose text is raw model markup but says plain is dropped by `StateRepository.load` (the document is
processed again) and upgraded to Markdown in memory by `StoredRun.from_json_dict`, so markup is
never shown or compared as literal text. A model answer wrapped in one code fence (```` ```markdown ````)
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
| Corrector | the stored text with its marks stripped and crossed-out words held back, restored afterwards (`domain/proofreading/markdown_shield.py`) |
| Evaluator | the stored text; for Markdown the system prompt gains a notation hint (`domain/review_prompts.py`): crossed-out words are not evaluated; inserted words belong to the sentence |
| Diff tab | `PlainTextRenderer` output — formatting marks never count as differences, crossed-out words are kept |
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
  `EXPLANATIONS_MISSING`.
- **Names** (`infrastructure/workflow/hidden_names.py`).  The exchange sees placeholders
  (`[PERSON_1]`), and a name is rarely as long as its placeholder, so every edit after a name
  would point at the wrong words.  `restore_with_offsets` returns the restored text and an
  `OffsetMap` (`domain/offset_map.py`); the review is remapped through it.

Both are checked with `fits(review, raw_plain)`: every part's `original` must be
`raw_plain[start:end]`.  A review that does not fit is dropped with a correction warning
(`REVIEW_UNMATCHED` for names, `EXPLANATIONS_MISSING` for pieces) instead of being stored: a
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
repair request that quotes the validation error, not the rejected answer. Unsupported, invalid-and-empty and
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
| `domain/proofreading/paragraph_gaps.py` | `collapse()` / `restore_gaps()`: narrow a long run of blank lines before the request, put the student's gaps back after it |
| `domain/proofreading/text_loss.py` | `measure()` / `dropped_text()`: what an answer kept of the text it was sent, for the guard of step ① |
| `domain/correction_prompts.py` | the fixed English system prompts of the three steps, the parameter and notes blocks, the batch body of step ②, the repair message, `PROMPT_VERSION` |
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
keeps less than `LOST_RATIO` of the words, writes more than `ADDED_RATIO` of them,
or deletes a run of `RUN_WORDS` words without writing as many in their place is asked for
once more.  A second answer that added text fails the piece like a cut-off.  A second answer that
**left text out** is **repaired** (`text_loss.repair`): each hunk cutting `RESTORE_MIN_WORDS`
words or more is written back from the student's text, and the repaired text is judged again (still
not the text: it fails as before).  The file is stored with a warning, its review is made from the
repaired text (so it fits), and each stretch is an `UnclearNote(left_out=True)`, listed in Check as
*not proofread* (`ItemKind.LEFT_OUT`).  It is a note for the teacher: the Word files and the unclear
badge skip it, so its `why` is stored empty.
`CorrectionOutcome.left_out` makes the outcome `fell_back` (no speed sample), and the evaluation
tools count the row as lost text although the stored output is whole.  Marks are not words, and a text under `MIN_RATIO_WORDS`
words is not judged by the ratio: correcting "ich bin müde" into "Ich bin müde." changes its
length by a third. The run threshold is much
higher than the one the evaluation tools use against a reference, because the app has no
reference: a text whose own correction cuts a paragraph looks exactly like a dropped one, so the
guard only fires on a cut no correction of that text would make.

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

**Blank-line gaps.** A run of blank lines is the student's layout, and a model reads a wide one
as a break between two texts: a held-out text of two essays four blank lines apart came back as
the second essay alone, while the same text with one blank line was answered whole. Step ①
therefore carries a plain text with every run of three or more line breaks narrowed to one blank
line (a Markdown request already has one blank line between its blocks), and the answer gets the
student's own gaps back afterwards (`domain/proofreading/paragraph_gaps.py`). The gaps are
matched by counting the paragraph breaks of the answer, never by looking for words: a break the
model kept stands where the student's gap stood, and an answer that merged or split a paragraph
keeps the breaks it wrote. So the stored corrected text keeps the student's own line breaks
wherever the model left the paragraph structure alone.

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

## Context window and long texts

### The context-budget rule

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

- `chars_per_token` is a cautious estimate (clean German prose has more, misspelt words fewer),
  so the check refuses a little early rather than too late.
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
  `answer_tokens=EVALUATION_ANSWER_TOKENS` (an estimate), which removes the artificial half: a
  review can use the window minus that allowance and its prompt.

`ensure_prompt_fits()` delegates here, and `OllamaClient.chat()` takes the allowance from the
caller (`answer_tokens`, `None` means "as large as the request"). Over the limit a proofreading
request warns `TRUNCATED_MESSAGE`; a content review warns its own sentence,
`TOO_LONG_FOR_REVIEW_WARNING`.
When both stages use the same model they get the same `num_ctx` (see below), so no reload
happens between them.

### How large the window is

`domain/context_budget.py` also decides the window, from a fixed ladder
`CONTEXT_STEPS = (8192, 16384, 32768)` — a ladder rather than an arbitrary size, because a
window that changes between files makes Ollama reload the model for every file.

- `automatic_num_ctx(spec, card_gb, cap=…)` returns the largest step at which
  `memory_at_context_gb(spec, step)` still reaches `FULL_SPEED_SHARE` of the graphics card, so a
  larger window can never turn "Works" into "Slower". Caps: `PROOFREADING_NUM_CTX_CAP` (8192, the
  base window) for proofreading, `EVALUATION_NUM_CTX_CAP` for content review.
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
  file. That is an accepted cost: nothing slows down or fails, the models only swap.

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

### Long texts are proofread in pieces

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
says `EXPLANATIONS_MISSING`. Chunking is
deterministic for a text and a window, so the cache fingerprint carries the window and a later
change of it proofreads the file again.

Known costs: there is no context across a cut (a change that spans a boundary cannot be made,
which cutting at sentence ends keeps rare), and one request per piece is slower in total than one
big request would have been — if one could fit.

### What the window does not apply to

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
`BASE_NUM_CTX` on purpose: their verdicts are compared across models and across
months, and a window that followed the card of the machine would make two runs incomparable.


## Error handling

### Design goals

- **One class per failure mode**, so a caller catches exactly what it needs and never reads a
  message string. Each inherits from the matching built-in (`ValueError` for bad input,
  `RuntimeError` for infrastructure, `FileNotFoundError` for a missing file).
- **A hard failure raises and ends the file; a soft one is a warning.** A missing provider package,
  an unreachable Ollama or an unreadable input ends the file. A problem in an optional stage
  (proofreading, content review) is an outcome with a warning, and the run goes on.
- **A diagnostic is a log line**, never a `print` (see *Logging*).

### Exception hierarchy

Every custom exception is in `teachers_teammate/exceptions.py`. A stage error carries a text or a
`Message` (see *Where text lives*); `str(exc)` gives the sentence either way.

```{uml} assets/error_handling.puml
:align: center
:alt: Exception hierarchy
:width: 100%
```

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
`Stopped` (`workflow/processing_result.py`) leave the file `stopped`, and `FileSkipped` leaves it
*skipped*.

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
  on.
- *The prompt is too long.* Ollama silently drops part of the prompt and still reports
  `done_reason == "stop"`, with a `prompt_eval_count` below the real prompt size. Nothing in
  the answer shows it, and the `ollama` client library cannot send `truncate=false`, which would
  make the server refuse instead. So `chat()` estimates the request size before
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
`TRUNCATED_MESSAGE`. What it costs
depends on the step: a cut-off ① fails the file and the source is kept (a half-corrected text is
never stored), while a cut-off ② or ③ only costs that part of the review — the corrected text is
stored and a warning says what is missing. A provider error (`"transport"`) fails the file at
once, because ① would meet the same server.

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
acts on. The GUI worker writes the run's own lines through `LoggingReporter`, into this file; the
window shows the last of a run's successes in its status bar and keeps no pane of lines.

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

## Downloads and installation

How Settings → Add-ons installs packages and models, and how they survive a restart.

### Addon packages directory

Optional packages that are installed at runtime — spaCy, LLM provider connectors — all land in
a **persistent user-writable directory** that is separate from the application bundle or the
development venv:

```
Windows:  %LOCALAPPDATA%\teachers_teammate\packages\
Linux:    ~/.local/share/teachers_teammate/packages/
macOS:    ~/Library/Application Support/teachers_teammate/packages/
```

The location is computed by `addon_folder.get_packages_dir()` using
`platformdirs.user_data_dir`. This directory is prepended to `sys.path` by
`inject_packages_dir()` so anything installed there is importable.

The folder is not versioned by Python; see *Another Python* below.

`inject_packages_dir()` first runs `remove_retired_addons()`: the retired Paddle add-on
(`paddlepaddle`, `paddleocr`, `paddlex`, `opencv-contrib-python`, which shadowed the app's own
OpenCV) is deleted by its `RECORD` files only; numpy is left alone.  It is then called:

- At application startup (`frozen_entry.py`, `gui/__main__.py`, `__main__.py`), so packages
  installed during a previous session are immediately available.
- After every successful install or model download, so the newly installed package is
  usable in the same session without a restart.

It is idempotent.  Every entry point calls it first
(`application/composition.inject_addon_packages_dir()`): `frozen_entry.py` and `__main__.py`,
and `main_gui()` and `cli.main()` for a wheel install, so a spaCy model
downloaded in one session is found after a restart however the app was started.

### The bundle's constraints and add-ons after an app update (frozen builds)

The packages folder comes **first** on `sys.path`.  A numpy, pydantic or requests that an
add-on's install brought along would therefore replace the bundled one (PyInstaller may bundle
only part of a package, so putting the folder last is wrong too).  Two things prevent that:

- **Constraints.** `tools/build/addon_constraints.py` (run by `build_standalone.py`, bundled by
  the spec) writes `teachers_teammate/_build/addon_constraints.txt` — `name==version` of every
  package in the build environment except the build tools (`BUILD_ONLY`).
  `addon_pip.pip_policy_arguments()` adds `--upgrade --only-binary :all: --no-cache-dir -c <file>`
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
  `addon_pip.update_installed_addons()` installs every add-on in the folder again with the
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
  `addon_list.addon_package_names()` lists.
- **`third_party_licenses/inventory.csv`.** One list for everything the app ships or installs —
  the bundle's packages, the add-ons', the models, the build tools.  `tools/licenses/inventory.py`
  writes it: it reads the app's own lists (`[project] dependencies`,
  `addon_list.addon_package_names()`, the `LocalModel`s, `SPACY_MODEL_PINS`), takes every
  version from the two locks, and downloads each locked wheel for its license text.  The same run
  compiles the one license list, `teachers_teammate/assets/third_party_licenses.txt`, which the
  GUI shows and every download carries as `third_party_license.txt`.  `tools/licenses/policy.toml`
  holds the allow list — the SPDX ids and `WITH` exceptions a component may state — the deny rules
  that explain a refusal, the spellings `[names]` folds, and the components a person accepted.
  An expression is read as SPDX writes one: `OR` passes when one alternative does, `AND` when every
  part does, `WITH` when license and exception are both listed, and anything else is refused until
  someone adds it.  A copyleft family in a wheel's texts that neither the row's License column nor
  `[vendored]` accounts for turns the refresh red, and so does a Windows or macOS wheel whose
  license files differ from the Linux one without a `[variants]` entry.  The Add-ons page shows a
  package's license as the tooltip of its row
  (`infrastructure/addon_licenses.py`, reading the bundled copy of the inventory).  `--compile`, `--check-env` and `--check-resolved` run offline
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

`addon_pip.install_addon_subprocess()` uses this mechanism for the addon packages
(spaCy, the provider connectors).  `_PipInstallThread` in `settings/_addons_page.py` also
uses it when `sys.frozen` is `True`.

In development (non-frozen) builds, both code paths fall back to the standard
`[sys.executable, "-m", "pip", "install", "--upgrade", ...]` without `--target`, so packages
land in the active venv as usual.

All pip installs run one at a time (`_PIP_LOCK` in `addon_pip.py`), so Settings → Add-ons
never writes to the packages directory twice at once.

### GPU monitoring (bundled, no package to install)

The System Monitor's GPU and VRAM gauges need no add-on.  `nvidia-ml-py` (the `pynvml` module,
BSD) is a runtime dependency in `pyproject.toml`, so it is in the bundle; AMD needs no
package at all — `gpu_detector.read_amd_usage()` reads the `amdgpu` driver's sysfs files
(`gpu_busy_percent`, `mem_info_vram_used`, `mem_info_vram_total`).  `read_gpu_usage()` answers
`None` when neither source does, and `_stats_widget.py` then shows CPU and RAM only.  CPU and RAM
come from `read_system_usage()` (psutil); without a first reading the widget shows a note instead.

`nvidia-ml-py` and `pyamdgpuinfo` are in `addon_folder._RETIRED_PACKAGES`, so a copy of them
in the add-on folder is removed at start.

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

`addon_pip.download_spacy_model_subprocess()` then installs it:
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
| Built-in reader and page models | in the installed app; from source, HTTPS from `huggingface.co`, only on request (`ModelStore.download`) | the bundle / `<user data>/models/<repo>/<revision>/` | Never at start-up |
