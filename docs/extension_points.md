# Extension points

Where to make a change, and the rules that come with it. One section per task: read the one for
the area you are about to touch. The rules every change must keep are in `AGENTS.md` at the
repository root and are not repeated here; the longer explanations are in {doc}`development` and
{doc}`deployment`.

## New pipeline stage

Add ABC to `interfaces.py`, implement in `infrastructure/`, route through `FileProcessor`.

## New LLM provider

A new file `<name>.py` in `infrastructure/providers/`, nothing else (`llm_factory.py` discovers
it). It exposes `create(model, **kwargs) -> BaseChatModel` and a `PROVIDER_INFO`:

- `display_name`; `self_hosted: True` only for a server the user runs themselves — **anything
  else is shown (and confirmed) as an online service**.
- `data_region`: `"eu"`, `"non_eu"` or `"unknown"` (missing = unknown). `vendor_country` is the
  tooltip only. If the region depends on settings (custom base URL), add a module function
  `data_region(**settings) -> str`; `llm_factory.get_provider_data_region()` prefers it.
- `key_url` (optional): the vendor page where a key is created; Settings links to it.

## Anonymization (who runs spaCy)

`ProcessingApplicationService.build_cached_anonymizer()` wraps the per-settings `SpacyAnonymizer`
in a `CachedAnonymizer`; `run_selected()` puts it into the run's `PipelineComponentFactory`.
Never build a `SpacyAnonymizer` per file or per run: a second spaCy pass makes a run disagree
with the previews.

## Where a step runs / what leaves the computer

Rules in `domain/data_location.py` (`StageLocation`, `DataRegion`, `PrivacyLevel`, `DataFlow`),
classification in `application/data_flow.py`; GUI/CLI only render badges, colours and advice
wording.

## Text recognition on/off

`Config.recognition_enabled` is not part of `OcrConfig`, so no cache key changes. Off:
`RecognitionSwitchedOffError` → `ProcessingResult.skipped` → `RunOutcome.skipped` →
`FileDoneEvent.skipped`. Gate anything that needs the OCR model/engine on it.
`ProcessingApplicationService.plan_recognition()` works out what a run can use; it opens PDFs,
so background thread only. See {doc}`development` → "Text recognition switch"

## Ollama server: finding, installing, starting

- Finding: rules in `domain/ollama_discovery.py`, probing in `infrastructure/ollama_discovery.py`,
  WSL host in `infrastructure/wsl.py`; the GUI only calls
  `ProcessingApplicationService.discover_ollama_servers(scope)`. See {doc}`development` →
  "Finding the Ollama server"
- Found / running: `infrastructure/ollama_install.py` (program search, start, models folder, free
  space, `OLLAMA_DOWNLOAD_URL`); `IssueKind.OLLAMA_NOT_FOUND` / `OLLAMA_NOT_RUNNING` and their
  fix rows in `gui/_fix_targets.py`. `RequirementIssue.startable` is `False` on Linux (the
  tooltip names the service command). The readiness line re-checks every 10 s for all three
  Ollama kinds; `gui/_ollama_status_line.py` shows the same state on *This computer*.

## Stopping a run / its result

`domain/run_summary.py:RunSummary` is what every run returns (`status` failed > stopped > done;
`exit_code` 1 only for failed). Two events: `stop_after_file_event` ("Stop") and `stop_event`
("Stop now"); `OllamaClient(cancel_event=)` cancels the request; `RunStoppedError` is a fast exit
only, the event is the truth. Wording in `gui/_run_status.py`. See {doc}`development` →
"Stopping a run and run outcomes"

## New OCR engine

Id in `OCR_ENGINE_NAMES` (`domain/data_location.py`); a `build_<engine>_ocr` field and branch in
`PipelineComponentFactory` / `StageBuilder.build_ocr` (`infrastructure/stage_builder.py`); its
cache identity in `compute_ocr_config_hash` (`workflow/cache_service.py`); a check in
`_check_engine` / `_ocr_engine_issues`, an `IssueKind` and its row in
`gui/_fix_targets.py:fix_for`; its label in `gui/_user_text.py:OCR_ENGINE_LABELS`. The built-in
reader is `ppocr` (`infrastructure/ppocr/`), its version `READER_VERSION` in
`domain/local_models.py`. See {doc}`development` → "Text recognition engines"

## A model file the app runs itself

A `LocalModel` (repo, pinned revision, size and SHA-256 per file) in `domain/local_models.py`:
the reader's two and the two **page models** `DOC_ORI` / `UVDOC` (`PAGE_MODELS`).
`infrastructure/onnx/store.py:ModelStore` reads the bundle first and keeps downloads under the
user data folder; `CvDnnNet` runs them. Entry points that may download: Add-ons tab, the fix
button, the Setup Assistant, `--download-reader`, `--download-page-models`. Tests get the files
as Bazel `http_file`s (`MODULE.bazel`). See {doc}`development` → "Local model files"

## New image preparation step

Add a `Step` (`name`, `apply(image, ctx) -> StepResult`) to `auto_steps` in
`infrastructure/preprocessing/auto.py` (bump `AUTO_VERSION` in `preprocessing/identity.py`). The
settings get **no switch of its own** (teachers see "Automatic" or "Original page"). Thresholds
are named constants in `auto.py`. Regenerate the digests of
`tests/unit/test_preprocessing_auto_golden.py` only when the pixel change is on purpose. See
{doc}`development` → "Image preparation"

## New input file type

Add the suffix to `SUPPORTED_SUFFIXES` in `interfaces.py` **and** a builder to
`_SUFFIX_TO_BUILDER` in `infrastructure/input_provider_factory.py`.

PDF embedded text: thresholds/modes in `domain/pdf_text_layer.py`; measurement in
`PdfInputProvider` (`infrastructure/input_providers.py`); the setting is
`OcrConfig.pdf_text_layer` (default `auto`), passed via `InputOptions`; the benchmark always OCRs.

## New OCR output notation (HTML/Markdown/…)

Add an `OcrOutputParser` to `infrastructure/ocr_formats/` and list it in `default_parsers()`;
model in `domain/formatted_text.py`, adapters use `application/formatting.py:TextFormatter`. See
{doc}`development` → "OCR output formats"

## New run requirement

Add an `IssueKind` and its check to `check_stage_requirements` / `check_run_preflight`
(`application/service.py`), and a row in `gui/_fix_targets.py:fix_for`: the readiness line, the
requirement guard and the "run can't start" dialog all take their line and buttons from it.
Preselect entries for Add-ons are model tags, `provider:<id>` and `spacy:<model>`.

## Setup Assistant

Rules in `application/setup_plan.py` (`build_setup_plan`, a pure function of `SetupAnswers` +
`SetupFacts`), page order in `gui/setup/_flow.py:next_page`, wording in `gui/setup/_summary.py`.
No new `Config` key, no migration. Downloads need *Set up*; the network search, *Start Ollama*
and *Test speed* each need their own click. **Start with the walkthrough**
`tests/gui/test_gui_setup_walkthrough.py` (real wizard, service, threads and `MainWindow`
against a fake Ollama). See {doc}`development` → "Setup Assistant"

## Add-on packages (installed app)

`infrastructure/addon_manager.py`. `get_packages_dir()` comes **first** on `sys.path`, so every
pip call goes through `pip_policy_arguments()` (`--only-binary :all:`, `-c addon_constraints.txt`
= the bundle's versions, written by `tools/build/addon_constraints.py`). After an app update
`outdated_shared_packages()` / `foreign_python_packages()` keep stale folders **off**
`sys.path`; `IssueKind.ADDONS_OUTDATED` and the Add-ons banner offer **Update add-ons**
(`ProcessingApplicationService.update_addons()`). Every entry point calls
`ProcessingApplicationService.inject_addon_packages_dir()` first. A new add-on = its requirement
in `_ADDON_PACKAGES`; `tools/build/check_addon_resolution.py` (CI) proves it fits the bundle.
See {doc}`development` → "The bundle's constraints and add-ons after an app update"

## New GUI setting

Per-batch choices (documents, language, which steps run, Results) belong in `ConfigPanel`;
anything set once belongs on a page of the Settings window (`gui/settings/`). Add the widget to
the owning page and its key to that page's `load` / `values` / `owned_keys`. Say where to change
it with the `WHERE_*` constants in `texts/settings.py`, not a hard-coded menu path. See
{doc}`development` → "Settings window"

## New user-facing text

Dynamic parts are functions (`dlg.no_files_found(folder)`), one module per surface in `texts/`.
`tests/unit/test_user_text_centralised.py` fails on a new literal and on a stale allowlist entry
in `tools/user_text/allowlist.py`: delete the entry when you move the text. See
{doc}`development` → "Where text lives"

## The window's tabs

`MainWindow._tabs`: 0 *Documents* (`_main_splitter`), 1 *Check* (`gui/check/_page.py:CheckPage`;
views in `_text_views.py`, list and card in `_side_panel.py`, scan in `_scan_view.py`). Only the
teacher or a run start switches tabs (`_open_check()`); a finished file reloads the open document
and never changes the tab. Edits go to the open document (`_current_preview_source_id`), never to
the selected row, and land in `_store_edit()`. Marks are overlays (`ExtraSelection`), never
document formatting. A document the run will redo is read-only from the run's start
(`CheckPage.set_lock()`). See {doc}`development` → "The window tabs and the Check page"

## Benchmark app changes

Domain metrics in `domain/benchmark.py`; run persistence in
`infrastructure/benchmark/run_store.py`; OCR-only run in `infrastructure/benchmark/runner.py`;
orchestration in `application/benchmark_service.py`; GUI in `gui/benchmark/` (reuse
`gui/_ocr_config_selector.py`). The OCR config slice is `Config.ocr` (`config.py:OcrConfig`).

## Model temperatures

Teachers set none. `domain/model_catalog.py` holds `STEP_ONE_TEMPERATURES` (measured per model;
`STEP_ONE_DEFAULT_TEMPERATURE` 0.4 otherwise) for step ①, and `MAKER_TEMPERATURES` (the model
card) for steps ② and ③ and the content review. A `RequestProfile` carries one `StepProfile`
(temperature, `think`, `options`) per step; `options` are sampling values besides the
temperature (`SAMPLING_OPTIONS`), empty = the model's own tag decides. The cache key and the
evaluation tools use the same `request_profile(tag, provider)` / `review_temperature()`. See
{doc}`development` → "Choosing writing models: the quality comparison"

## New proofreading exchange

Implement `Exchange` (`proofread`, `max_text_chars`, `cost_model`) in a module of
`infrastructure/proofreading/exchanges/`, register it in `EXCHANGES` under its `ExchangeKind`
(`domain/proofreading/result.py`), and build it through `build_exchange()` so port, cut-off tries
and clause check arrive the same way. Map the exchange's own answer schema to `ExchangeResult`:
nothing outside `infrastructure/proofreading/` may see it. `FallbackExchange(primary, fallback)`
is the chain of responsibility. See {doc}`development` → "Proofreading exchange"

## Proofreading steps / prompts

The three system prompts (`CORRECT_SYSTEM`, `EXPLAIN_SYSTEM`, `UNCLEAR_SYSTEM`,
`PROMPT_VERSION`) are in `domain/correction_prompts.py`; languages, level and the teacher's notes
travel in the user message. Step ① variants: `CORRECT_VARIANTS`, adopted per model in
`STEP_ONE_PROMPTS`, hashed into the cache key by `prompt_identity()`. Answer schemas of steps ②
and ③: `infrastructure/proofreading/schemas.py`; retry and repair rules:
`runner.py:StepRunner`. See {doc}`development` → "Choosing writing models: the quality
comparison"

## Stored review / Word file explanations

`domain/proofreading/review.py` is the one review: `EditNote`s with `EditPart`s, and
`UnclearNote`s. Its offsets are in the **plain text of the stored `raw_text`**
(`plain_text_of(raw_text, raw_text_format)`), so a producer that changes that text fixes them
first: `ChunkingCorrector` merges the pieces (`merge_reviews`), `FileProcessor` restores the
names (`restore_with_offsets`); both check `fits()`. Every reader goes through `align()` (CLI,
queue, export, both Word layouts, evaluation tools). The sentence-based review lives only in
`tools/model_eval/one_request/sentence_review.py`; no module of `teachers_teammate/` may import
it (`tests/tools/test_no_sentence_review_in_the_app.py`). See {doc}`development` → "Correction
review"

## New evaluation corpus

Add `<name>.py` to `tools/model_eval/corpora/` (an `ADAPTER` class); a new test set is a module
in `tools/model_eval/recipes/` that sets `RECIPE`. Nothing lists either: both packages find their
modules. An adapter reads only the files it pins. Recipes of whole texts in bands share
`recipes/_bands.py` (`select_texts`: slots, seed, splits); `gec-2026-12-heldout` is the set for
confirming a tuning choice on splits the first never reads; each recipe holds its own `Rules`
and never imports another recipe's numbers.

## Third-party licenses

`third_party_licenses/inventory.csv` is written by `tools/licenses/inventory.py`. The tool reads
the app's own lists (`[project] dependencies`, `addon_manager.addon_package_names()`, the
`LocalModel`s, `SPACY_MODEL_PINS`) and the two locks, so a new dependency, add-on, model or spaCy
language is a red test until the tool runs. `tools/licenses/policy.toml` holds the denied
licenses, display names and accepted exceptions. `NOTICE` is hand-written; a test keeps its
versions on the locked ones. See {doc}`deployment` → "Third-party licenses"

## Also in `development.md`

No rules beyond the code, only explanation: slow start or a frozen window
(`infrastructure/diagnostics.py`, "Diagnosing a slow start or a window that freezes") and
keeping the computer awake (`infrastructure/power.py`, "Keeping the computer awake").
