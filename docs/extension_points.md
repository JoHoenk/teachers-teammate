# Extension points

Where to make a change and the rules that come with it, one section per task. The rules every
change keeps are in `CONTRIBUTING.md`; the explanations in {doc}`development` and
{doc}`deployment`.

## New pipeline stage

Add ABC to `interfaces.py`, implement in `infrastructure/`. What runs per file is a
`<name>_stage.py` in `infrastructure/workflow/`, which `FileProcessor.process()` calls in order;
each stage takes what it uses plus `StageShared` (reporter, stop event, stage callback, cache
service, language) and returns its result as a frozen dataclass from its module. A stage that
cannot do its work raises `StageError` with its `kind` and `stage`
(`exceptions.py`); `FileProcessor.process()` has the one `except` that turns it into a `FAILED`
file, and turns `Stopped` into a stopped result. What a stage produced with a remark is an outcome,
not an exception. The name a teacher reads for it goes into `texts/app.py:STAGE_TITLES`; the layers
pass the stage key. See {doc}`development` → "One error contract".

## New LLM provider

A new file `<name>.py` in `infrastructure/providers/` and its two default models in
`teachers_teammate/_model_defaults.py` (`config.py` reads them without importing a provider module);
`tests/unit/test_providers.py` checks both. `llm_factory.py` finds the file, and Bazel and the
bundle spec take the folder as it is. The module exposes `create(model, **kwargs) -> BaseChatModel`
and `PROVIDER = ProviderInfo(...)` (`domain/providers.py`) — the one place its facts live; the
model pickers, Add-ons, the privacy wording and the installer read them from there.

- `name`: the module name; `display_name`: what a teacher reads.
- `self_hosted=True` only for a server the user runs themselves — **anything else is shown
  (and confirmed) as an online service**.
- `package`: the pip package it needs (`AddonPackage(module, pip, version_spec)`); the Add-ons page
  and the run preflight take it from there. `package_optional=True` for one the app can talk to
  directly (Ollama). Leave it `None` for a provider that ships in the base install.
- `data_region`: `eu`, `non_eu` or `unknown` (the default). `vendor_country` is the tooltip only.
  If the region depends on settings (custom base URL), add a module function
  `data_region(**settings) -> str`; `llm_factory.get_provider_data_region()` prefers it.
- `key_url` (optional): the vendor page where a key is created; Settings links to it.
- `strict_json_schema=True` only where the review schema works in the provider's strict mode.
- Import the package only inside `create()` (`import_guard`): the Add-ons installer loads the
  module before the add-on folder is on `sys.path`.

## Anonymization (who runs spaCy)

`AnonymizationService.build_cached_anonymizer()` (`application/anonymization.py`) wraps the
per-settings `SpacyAnonymizer` in a `CachedAnonymizer`; `run_selected()` puts it into the run's
`PipelineComponentFactory`.

Never build a `SpacyAnonymizer` per file or per run: a second spaCy pass makes a run disagree
with the previews.

What is hidden is settings only: `AnonymizerConfig.patterns`, `.hide` and `.keep`. The GUI writes
a rule through `AnonymizationService.with_hiding()` — `domain/anonymization.py:config_hiding()` /
`config_keeping()` — and never decides itself what a rule looks like. All three are part of the
settings' fingerprint (`cache_keys.anonymization_fingerprint()`), so a stored result made under
other rules says so.

## Where a step runs / what leaves the computer

Rules in `domain/data_location.py` (`StageLocation`, `DataRegion`, `PrivacyLevel`, `DataFlow`),
classification in `application/data_flow.py`; GUI/CLI only render colour, and the badges, lines
and advice sentences come from `texts/privacy.py`.

## Text recognition on/off

`Config.recognition_enabled` is not part of `OcrConfig`, so no cache key changes. Off:
`RecognitionSwitchedOffError` → `ProcessingResult.skipped` → `RunSummary.skipped` →
`FileDoneEvent.skipped`. Gate anything that needs the OCR model/engine on it.
`RunChecks` (`application/run_checks.py`) works out what a run can use; it opens PDFs,
so background thread only. See {doc}`development` → "Text recognition switch".

## Ollama server: finding, installing, starting

- Finding: rules in `domain/ollama_discovery.py`, probing in `infrastructure/ollama_discovery.py`,
  WSL host in `infrastructure/wsl.py`; the GUI only calls
  `OllamaServerService.discover_ollama_servers(scope)`. See {doc}`development` →
  "Finding the Ollama server"
- Found / running: `infrastructure/ollama_install.py` (program search, start, models folder, free
  space, `OLLAMA_DOWNLOAD_URL`); `IssueKind.OLLAMA_NOT_FOUND` / `OLLAMA_NOT_RUNNING` and their
  fix rows in `gui/_fix_targets.py`. `RequirementIssue.startable` is `False` on Linux (the
  tooltip names the service command). The readiness line re-checks every 10 s for all three
  Ollama kinds; `gui/_ollama_status_line.py` shows the same state on *This computer*.
- What the server answered — its version, the installed models with their digests and sizes, and
  what a model can do — is held by `OllamaServerService` alone: `snapshot(url)` asks when the held
  answer is older than `SNAPSHOT_TTL_S` (`domain/ollama_snapshot.py`) or lists no model,
  `cached_snapshot(url)` never asks, `forget(url)` drops it and a pull forgets by itself. Nothing
  else caches an answer of the server.

## Stopping a run / its result

`domain/run_summary.py:RunSummary` is what every run returns (`status` failed > stopped > done;
`exit_code` 1 only for failed). Two events: `stop_after_file_event` ("Stop") and `stop_event`
("Stop now"); `OllamaClient(cancel_event=)` cancels the request; `RunStoppedError` is a fast exit
only, the event is the truth. Wording in `gui/_run_status.py`. See {doc}`development` →
"Stopping a run and run outcomes"

## New OCR engine

Id in `OCR_ENGINE_NAMES` (`domain/data_location.py`); a `build_<engine>_ocr` field and branch in
`PipelineComponentFactory` / `StageBuilder.build_ocr` (`infrastructure/stage_builder.py`); its
cache identity in `ocr_key` (`cache_keys.py`); a check in
`_ocr_engine_issues`, an `IssueKind` and its row in
`gui/_fix_targets.py:fix_for`; its label in `texts/addons.py:OCR_ENGINE_LABELS`. The built-in
reader is `ppocr` (`infrastructure/ppocr/`), its version `READER_VERSION` in
`domain/local_models.py`. An engine that knows where its words sit on the page overrides
`read_page` and returns `PageReading(text, lines)` (`domain/page_layout.py`); one that does not
takes the interface's default, and the built-in reader reads its pages alongside for the Check
tab's marks (`StageBuilder.build_scan_places`). See {doc}`development`
→ "Text recognition engines"

## A model file the app runs itself

A `LocalModel` (repo, pinned revision, size and SHA-256 per file) in `domain/local_models.py`:
the reader's two and the two **page models** `DOC_ORI` / `UVDOC` (`PAGE_MODELS`).
`infrastructure/onnx/store.py:ModelStore` reads the bundle first and keeps downloads under the
user data folder; `CvDnnNet` runs them. Entry points that may download: Add-ons tab, the fix
button, the Setup Assistant, `--download-reader`, `--download-page-models`. Tests get the files
as Bazel `http_file`s (`MODULE.bazel`). See {doc}`development` → "Local model files"

## New image preparation step

Add a `Step` (`name`, `apply(image, ctx) -> StepResult`) to `auto_steps` in
`infrastructure/preprocessing/auto.py` (bump `AUTO_VERSION` in `domain/local_models.py`). The
settings get **no switch of its own** (teachers see "Automatic" or "Original page"). Thresholds
are named constants in `auto.py`. Regenerate the digests of
`tests/unit/test_preprocessing_auto_golden.py` only when the pixel change is on purpose. See
{doc}`development` → "Image preparation"

## New input file type

Add a line to `INPUT_KINDS` in `interfaces.py` (the suffix and what reads it) **and**, for a
new kind, a builder to `_BUILDERS` in `infrastructure/input_provider_factory.py`.
`tests/unit/test_input_provider_factory.py` builds a provider for every offered suffix.

PDF embedded text: thresholds/modes in `domain/pdf_text_layer.py`; measurement in
`PdfInputProvider` (`infrastructure/input_providers.py`); the setting is
`OcrConfig.pdf_text_layer` (default `auto`), passed via `InputOptions`.

## New OCR output notation (HTML/Markdown/…)

Add an `OcrOutputParser` to `infrastructure/ocr_formats/` and list it in `default_parsers()`;
model in `domain/formatted_text.py`, adapters use `application/formatting.py:TextFormatter`. See
{doc}`development` → "OCR output formats"

## New run requirement

Add an `IssueKind` and its check to `check_stage_requirements` / `check_run_preflight`
(`application/run_checks.py`), and a row in `gui/_fix_targets.py:fix_for`: the readiness line, the
requirement guard and the "run can't start" dialog all take their line and buttons from it.
Preselect entries for Add-ons are `InstallItem`s (`domain/install_item.py`): a kind and a
name, never a string key.

## Setup Assistant

Rules in `application/setup_plan.py` (`build_setup_plan`, a pure function of `SetupAnswers` +
`SetupFacts`), page order in `gui/setup/_flow.py:next_page`, wording in `gui/setup/_summary.py`.
No new `Config` key, no migration. Downloads need *Set up*; the network search, *Start Ollama*
and *Test speed* each need their own click. **Start with the walkthrough**
`tests/gui/test_gui_setup_walkthrough.py` (real wizard, service, threads and `MainWindow`
against a fake Ollama). See {doc}`gui` → "Setup Assistant"

## A service needs something from infrastructure

1. Add the method to a `Protocol` in `application/ports.py` — narrow: only what the caller asks
   (`OllamaServer` carries a server's answers, `HardwareProbe` this computer's cards and memory,
   `AddonState` the packages folder, `NameModels` the spaCy models).
2. Give the service the protocol as a required constructor argument and call it; the method body
   imports nothing.
3. Build the implementation in `application/composition.py`. An adapter class whose methods call
   the module's functions by name keeps those functions patchable in a test, and no second
   construction is needed (see `SystemHardwareProbe`, `AddonPackages`, `SpacyModels`).
4. Lower `BUDGET` in `tests/unit/test_lazy_import_budget.py` if the change removed a
   `# noqa: PLC0415`, and the count of that module in `PINNED` of
   `tests/unit/test_application_infrastructure_budget.py` if the module stops importing
   `infrastructure`; both tests fail on a mismatch.

What stays a lazy import: installs with progress and their own errors, the facts of the process
(`inject_packages_dir()`, `can_install_addons()`), and pure helpers on strings and names.

## Add-on packages (installed app)

`infrastructure/addon_folder.py`. `get_packages_dir()` comes **first** on `sys.path`, so every
pip call goes through `pip_policy_arguments()` (`--only-binary :all:`, `-c addon_constraints.txt`
= the bundle's versions, written by `tools/build/addon_constraints.py`). After an app update
`outdated_shared_packages()` / `foreign_python_packages()` keep stale folders **off**
`sys.path`; `IssueKind.ADDONS_OUTDATED` and the Add-ons banner offer **Update add-ons**
(`AddonService.update_addons()`). Every entry point calls
`application/composition.inject_addon_packages_dir()` first. A new add-on = its requirement
in `ADDON_PACKAGES`; `tools/build/check_addon_resolution.py` (CI) proves it fits the bundle.
See {doc}`development` → "The bundle's constraints and add-ons after an app update"

## New GUI setting

Per-batch choices (documents, language, which steps run, Results) belong in `ConfigPanel`;
anything set once belongs on a page of the Settings window (`gui/settings/`). Add the widget to
the owning page and its key to that page's `load` / `values` / `owned_keys`. Say where to change
it with the `WHERE_*` constants in `texts/settings.py`, not a hard-coded menu path. See
{doc}`gui` → "Settings window"

## A failure, and what the log says about it

Failures are logged where they are handled, once; a catch-all logs through `messages.log_failure`:
an error that carries a `Message` is expected (WARNING, the message), everything else is a bug
(ERROR, its type and frames, never its message). The hooks `start_logging()` installs catch what reaches no
handler at all; a Qt slot, a `QThread.run` and a plain thread all end there. The GUI shows a failure
only through `gui/_errors.py`: one notice about the action that failed, a row for a file that did
not work. See {doc}`error_handling`

## New user-facing text

Dynamic parts are functions (`dlg.no_files_found(folder)`), one module per surface in `texts/`.
`tests/unit/test_user_text_centralised.py` fails on a literal handed to a sink that shows text, on
a module constant whose name says a teacher reads it and on a template of `texts` filled with
`.format` at a call site; the sinks and the name suffixes are in `tools/user_text/inventory.py`. The
allowlist in `tools/user_text/allowlist.py` is empty and stays empty. A constant whose name says
text but whose value no teacher reads is listed in `inventory.NON_TEXT_CONSTANTS`. See
{doc}`development` → "Where text lives"

A failure raised in `infrastructure` is a `Message`, not a `texts` call: add the `MessageKey`, its
factory in `messages.py` (the name and parameters of the `texts` function), the line in
`_RENDERERS` of `texts/messages.py` and the key to the golden list in
`tests/unit/test_messages.py`. A released key's value never changes. A test of the raise site
checks `raised.value.args[0].key`, not the wording.

## The window's tabs

Tab 0 *Documents*, tab 1 *Check* (`gui/check/_page.py:CheckPage`, driven by `CheckController`;
views in `_text_views.py`, list and card in `_side_panel.py`, scan in `_scan_view.py`). Only the
teacher or a run start switches tabs; a finished file reloads the open document
and never changes the tab. Edits go to the open document, never to
the selected row, and land in `DocumentCommands` through `CheckController`. Marks are overlays (`ExtraSelection`), never
document formatting. A document the run will redo is read-only from the run's start
(`CheckPage.set_lock()`). The scan's mark of the sentence under the cursor comes from the places
stored with the reading (`domain/page_layout.py`, `check_document.py:mark_for_cursor`); the window
only carries a view's offset over (`gui/check/_scan_link.py`). See {doc}`gui` → "The window tabs
and the Check page"

## Model temperatures

Teachers set none. `domain/request_profile.py` holds `STEP_ONE_TEMPERATURES` (measured per model,
else `STEP_ONE_DEFAULT_TEMPERATURE`) for step ①, and `MAKER_TEMPERATURES` (the model
card) for steps ② and ③ and the content review. A `RequestProfile` carries one `StepProfile`
(temperature, `think`, `options`) per step; `options` are sampling values besides the
temperature (`SAMPLING_OPTIONS`), empty = the model's own tag decides. The cache key and the
evaluation tools use the same `request_profile(tag, provider)` / `review_temperature()`. See
{doc}`development` → "Request profiles: temperatures and prompts per model"

## New proofreading exchange

Implement `Exchange` (`proofread`, `max_text_chars`, `cost_model`) in a module of
`infrastructure/proofreading/exchanges/`, register it in `EXCHANGES` under its `ExchangeKind`
(`domain/proofreading/result.py`), and build it through `build_exchange()` so port, cut-off tries
and clause check arrive the same way. Map the exchange's own answer schema to `ExchangeResult`:
nothing outside `infrastructure/proofreading/` may see it. See {doc}`development` →
"Proofreading exchange"

## Proofreading steps / prompts

The three system prompts (`CORRECT_SYSTEM`, `EXPLAIN_SYSTEM`, `UNCLEAR_SYSTEM`,
`PROMPT_VERSION`) are in `domain/correction_prompts.py`; languages, level and the teacher's notes
travel in the user message. Step ① variants: `CORRECT_VARIANTS`, adopted per model in
`STEP_ONE_PROMPTS`, hashed into the cache key by `prompt_identity()`. A step's key is what
travels; the heading a teacher reads over its prompt is `texts/review.py:PROMPT_STEP_TITLES`.
Answer schemas of steps ② and ③: `infrastructure/proofreading/schemas.py`; retry and repair rules:
`runner.py:StepRunner`. See {doc}`development` → "Request profiles: temperatures and prompts per
model"

## Stored review / Word file explanations

`domain/proofreading/review.py` is the one review: `EditNote`s with `EditPart`s, and
`UnclearNote`s. Its offsets are in the **plain text of the stored `raw_text`**
(`plain_text_of(raw_text, raw_text_format)`), so a producer that changes that text fixes them
first: `ChunkingCorrector` merges the pieces (`merge_reviews`), `FileProcessor` restores the
names (`restore_with_offsets`); both check `fits()`. A teacher's fix of the reading goes through
`domain/proofreading/carry.py`, which carries the review and the corrected text over the fix or
gives `None` (`DocumentCommands.edit_transcription` then resets the stage). It moves offsets with
`OffsetMap` (`word_diff.py`), which counts layout whitespace, and checks that the carried text
differs from the new reading by the model's own changes, each where the review says. Every reader goes
through `align()` (CLI, queue, export, both Word layouts, evaluation tools). The sentence-based
review lives only in `tools/evaluation/run/one_request/sentence_review.py`; no module of
`teachers_teammate/` may import it (`tests/tools/test_no_sentence_review_in_the_app.py`). See
{doc}`development` → "Correction review"

## Comparing models

One text, several writing models, side by side. Not a stage and not a run: it asks several
correctors about the same transcription, one after another.

- `domain/proofreading/model_comparison.py:compare(source_plain, versions)` is pure: the edits of
  every version (`ordered_edits`) grouped where their word spans touch (`spans_touch`) into a
  `ChangeSite` with one `Option` per replacement, the `Row`s through `sentence_spans`, and a
  `VersionFigures` per version (changes, shared, alone, explained of needed, orphans, unclear,
  added marks, text loss, failed, fell back, seconds, and what the teacher's verdicts come to). A
  new figure is a `VersionFigures` field plus a line in `gui/compare/_rows.py:_CELLS`.
- A version is one **model**, not one answer: `Version.repeats` holds the answers of a second run,
  so a change the model made twice is *in both runs* and one it made in one answer only is *once*
  (`VersionFigures.in_every_run` / `.once`, `Option.once`). The counts stay those of the first
  answer; the sites are built from every run.
- `application/model_comparison.py:ModelComparisonService` is the entry the GUI has for the text,
  the answers and the comparison. It imports nothing from `infrastructure`: `ComparisonParts`
  brings the store (`CompareResults`), the unit builder (`ProofreadUnitBuilder`), the documents,
  the key of a config, the key facts, the run session and the Ollama server. `run()` asks a list
  of models inside one `running()`, skips a model that answered already, builds each model's
  `Config` variant, unloads what it loaded and stores the answer.
- `application/compare_candidates.py:ComparisonCandidates` lists the models to tick: the installed
  ones rated through `ModelRecommendationService.rate_for` (the rating the AI models page shows)
  and the online ones whose key and package are there. `candidates()` reads only the lists the
  app holds; `refresh()` asks for expired ones again and runs off the GUI thread.
- `domain/proofreading/option_marks.py` says what a teacher called right or wrong: a mark is on
  the option (the wording at one place), keyed by the text's hash, the character span and the hash
  of the replacement, so it holds no text; `MarkCount` holds right, wrong, unmarked and missed per
  version (counted in `model_comparison.py`) and gives F0.5 once every change of that version is
  marked.
  `infrastructure/option_mark_store.py:OptionMarkStore` keeps `compare_marks/<text sha>.json` in
  the data root, where the retention does not reach; only *Delete stored results* with the
  teacher's work deletes it.
- `infrastructure/compare_store.py:CompareStore` keeps one JSON file per answer under
  `<cache>/compare/<text sha>/<key>.json`, beside `<key>~2.json` when the model ran twice, writes
  atomically and counts into the retention and
  *Delete stored results*. The retention expires one model's answers together. It is the only place that writes `ComparedResult`
  (`domain/proofreading/compared_result.py`); the answer holds the text and is never logged.
- `infrastructure/workflow/proofread_unit.py:proofread_unit_for` is the one unit that hides names,
  proofreads and restores them; a comparison and `FileProcessor` share it, so a compared answer
  goes the way a run's text goes (`SentRecord` included).
- `gui/compare/` is the window: `_window.py:CompareWindow` and `CompareHooks`, `_job.py` the models
  in a `LongJobThread`, `_models.py` the ticks and row states, `_changes.py` / `_side_by_side.py` /
  `_overview.py` the three tabs, `_rows.py` the `Comparison` turned into rows. Words are
  `texts/compare.py`. See {doc}`gui` → "The compare window"

A new model appears by itself: the list of models is the installed Ollama models rated for writing
plus the online providers with a key, so a new provider only has to be in `PROVIDERS`.

## New evaluation corpus

Add `<name>.py` with an `ADAPTER` to `tools/evaluation/input/corpora/`; a new test set is a module
in `tools/evaluation/input/recipes/` that sets `RECIPE`. Both packages find their modules. Recipes
of whole texts in bands share `input/recipes/_bands.py` (`select_texts`); each recipe holds its own
`Rules` and never imports another's numbers. Details: `evaluations/TOOLS.md` → "Extending".

## Third-party licenses

`third_party_licenses/inventory.csv` is written by `tools/licenses/inventory.py`. The tool reads
the app's own lists (`[project] dependencies`, `addon_list.addon_package_names()`, the
`LocalModel`s, `SPACY_MODEL_PINS`) and the two locks, so a new dependency, add-on, model or spaCy
language is a red test until the tool runs. `tools/licenses/policy.toml` holds the allow list —
the ids and `WITH` exceptions a component may state — the deny rules that explain a refusal, and
the components a person accepted. A component that states none of them goes in `[accepted]`; a
copyleft part inside a wheel that its own license does not name goes in `[vendored]`; a package
whose Windows or macOS wheel carries other texts than the Linux one goes in `[variants]`.
`NOTICE` is hand-written; a test keeps its versions on the locked ones. See
{doc}`deployment` → "Third-party licenses"

## Also in `development.md`

No rules beyond the code, only explanation: slow start or a frozen window
(`infrastructure/diagnostics.py`, "Diagnosing a slow start or a window that freezes") and
keeping the computer awake (`infrastructure/power.py`, "Keeping the computer awake").
