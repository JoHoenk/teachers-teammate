# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick-start commands

```bash
# Activate venv (not required for bazel)
source .venv/bin/activate

# Run tests
bazel test //tests:unit_tests --test_output=errors   # fast, no external services
bazel test //tests/... --test_output=errors           # full suite (skips Ollama tests if absent)

# Run a single test file
bazel test //tests:test_config --test_output=errors

# Coverage (unit + GUI; requires lcov for HTML)
bazel coverage //tests:coverage_tests \
  --instrumentation_filter="//teachers_teammate" \
  --combined_report=lcov
genhtml --ignore-errors unmapped bazel-out/_coverage/_coverage_report.dat -o coverage_html/

# Lint + type check (all in one)
bazel test //:lint --test_output=errors          # == //:py_ruff + //:py_pylint + //:py_ty

# Format (ruff format) + apply lint-safe fixes
bazel run //:format.fix

# Build & serve Sphinx docs
bazel build //docs:html
bazel run //docs:html.serve
```

## Architecture

**Teacher's Teammate** is a batch OCR pipeline (CLI + PySide6 GUI) for handwritten documents.

### Layers (innermost → outermost)

```
domain/          — pure business concepts (freshness policy, stage names); no I/O
interfaces.py    — ABCs for every pipeline stage; SUPPORTED_SUFFIXES lives here
infrastructure/  — concrete implementations (OCR engines, LLM providers, DOCX builder, etc.)
infrastructure/workflow/ — FileProcessor (per-file stage orchestrator), run_files() coordinator
application/     — ProcessingApplicationService (queries) + ApplicationCommands (writes); the only entry point for GUI and CLI
gui/ / cli.py    — adapters; must not contain discovery or availability logic
                   (gui/settings/ = the Settings window, one page per topic)
```

### Pipeline flow

```
Config
  → InputProvider        (input_providers.py)      PDF/image/TXT → InputPayload of InputUnits
  → ImagePreprocessor    (preprocessing/)          automatic preparation (auto) or the original page (none)
  → OCRProcessor         (ocr_processor.py)        Ollama | built-in reader (PP-OCR)
     (TXT files bypass OCR — content flows directly to the next stage)
  → OcrOutputPostprocessor (ocr_formats/)          plain | Markdown | Chandra HTML → stored
                                                   as plain or canonical Markdown (raw_text_format)
  → Anonymizer           (anonymizer.py)           spaCy NER + regex, optional
  → Corrector            (correction.py)           LangChain-based, optional; the exchange
                                                   (infrastructure/proofreading/) is its
                                                   replacement and is not wired yet
  → Evaluator            (evaluation.py)           LangChain-based, optional
  → DocumentCreator      (docx_builder.py)         table or comments DOCX format
```

`OCRPipeline` (`infrastructure/pipeline.py`) is the composition root — it wires services together but contains no stage logic. Stage logic lives in `infrastructure/workflow/`: `FileProcessor` runs all stages for one file, `run_files()` (a plain function in `coordinator.py`) iterates over the file list.

### LLM providers

`infrastructure/llm_factory.py` discovers provider modules at runtime from `infrastructure/providers/`. Each module exposes a single `create(model, **kwargs) -> BaseChatModel`. **Adding a new provider = dropping a new file in that directory; no other changes needed.**

### Key extension points

| Task | What to change |
|---|---|
| New pipeline stage | Add ABC to `interfaces.py`, implement in `infrastructure/`, route through `FileProcessor` |
| New LLM provider | Add `<name>.py` to `infrastructure/providers/` with a `create()` function. Set `PROVIDER_INFO["display_name"]`; a provider that runs on the user's own server rather than at a vendor sets `"self_hosted": True` — **anything else is shown (and confirmed) as an online service**. A provider with a key may set `PROVIDER_INFO["key_url"]` (the vendor's page where a key is created); the Settings window links to it under the key field. |
| Where a provider processes data | `PROVIDER_INFO["data_region"]`: `"eu"`, `"non_eu"` or `"unknown"` (missing = unknown, shown like "outside the EU"). `PROVIDER_INFO["vendor_country"]` is the tooltip only. When the region depends on the settings (custom base URL, regional endpoint), add a module function `data_region(**settings) -> str`; `llm_factory.get_provider_data_region()` prefers it over the key. |
| Anonymization (who runs spaCy) | `ProcessingApplicationService.build_cached_anonymizer()` wraps the per-settings `SpacyAnonymizer` in a `CachedAnonymizer` over the service's two caches, and `run_selected()` puts it into the run's `PipelineComponentFactory`. Never build a `SpacyAnonymizer` per file or per run — an extra one means a second spaCy pass and a run that disagrees with the previews. |
| Where a step runs / what leaves the computer | Rules in `domain/data_location.py` (`StageLocation`, `DataRegion`, `PrivacyLevel`, `DataFlow`), classification in `application/data_flow.py`; GUI/CLI only render badges, colours and advice wording. Text recognition never uses online engines (`LOCAL_OCR_ENGINES`, `online_ocr_reason`) |
| Text recognition on/off | `Config.recognition_enabled` (not part of `OcrConfig`, so no cache key changes). Off: images / PDFs without text layer become *skipped* files (`RecognitionSwitchedOffError` → `ProcessingResult.skipped` → `RunOutcome.skipped` → `FileDoneEvent.skipped`), never errors. Gate anything that needs the OCR model/engine on it; what a run can use is worked out by `ProcessingApplicationService.plan_recognition()` (opens PDFs: background thread only). See `docs/development.md` → "Text recognition switch" |
| Finding the Ollama server | Rules in `domain/ollama_discovery.py`, probing in `infrastructure/ollama_discovery.py`, WSL host in `infrastructure/wsl.py`; GUI only calls `ProcessingApplicationService.discover_ollama_servers(scope)`. The network scope asks many computers: start it only from an explicit user action, and never switch `ollama_url` without the user choosing. See `docs/development.md` → "Finding the Ollama server" |
| Stopping a run / its result | `domain/run_summary.py:RunSummary` is what every run returns (`status` failed > stopped > done; `exit_code` 1 only for failed). Two events: `stop_after_file_event` ("Stop") and `stop_event` ("Stop now"); `OllamaClient(cancel_event=)` cancels the request; `RunStoppedError` is a fast exit only, the event is the truth. A stopped file is `ProcessingResult.stopped`, never a failure and never stored half done. Wording in `gui/_run_status.py`. See `docs/development.md` → "Stopping a run and run outcomes" |
| Keep the computer awake | `infrastructure/power.py` (`SleepBlocker` per platform); held only in `ProcessingApplicationService.run_selected()`; `Config.keep_awake` (Settings → General, `--allow-sleep`). Tests get the null blocker from `conftest.py`. See `docs/development.md` → "Keeping the computer awake" |
| New OCR engine | Id in `OCR_ENGINE_NAMES` (`domain/data_location.py`, the name teachers see), a `build_<engine>_ocr` field and branch in `PipelineComponentFactory` / `StageBuilder.build_ocr` (`infrastructure/stage_builder.py`), its cache identity in `compute_ocr_config_hash` (`workflow/cache_service.py`: what makes it read a page differently), a check in `_check_engine` / `_ocr_engine_issues` plus an `IssueKind` and its row in `gui/_fix_targets.py:fix_for`, and its label in `gui/_user_text.py:OCR_ENGINE_LABELS`. Engines are local only (`LOCAL_OCR_ENGINES`). The built-in reader is `ppocr` (`infrastructure/ppocr/`: `detect.py` → `reading_order.py` → `recognise.py`, joined in `engine.py`); `READER_VERSION` in `domain/local_models.py` is bumped when its output changes. See `docs/development.md` → "Text recognition engines" |
| A model file the app runs itself | A `LocalModel` (repo, pinned revision, size and SHA-256 per file) in `domain/local_models.py` (the reader's two, and the two **page models** `DOC_ORI` / `UVDOC` of the automatic image preparation: turn and flatten, `PAGE_MODELS`; `--download-page-models`); `infrastructure/onnx/store.py:ModelStore` keeps it under the user data folder, `CvDnnNet` runs it. **Downloads only on request** (Add-ons tab, the fix button, the Setup Assistant, `--download-reader`) — never at start-up, never silently. Tests get the files as Bazel `http_file`s (`MODULE.bazel`), never from the network. See `docs/development.md` → "Local model files" |
| New image preparation step | Add a `Step` (`name`, `apply(image, ctx) -> StepResult`) to `auto_steps` in `infrastructure/preprocessing/auto.py` and bump `AUTO_VERSION` (`preprocessing/identity.py`); the settings get **no switch of its own** (teachers see "Automatic" or "Original page"). Keep `tests/unit/test_preprocessing_auto_golden.py` green: it pins the pixels of the automatic preparation (regenerate its digests when the change is on purpose). Thresholds are named constants in `auto.py`. See `docs/development.md` → "Image preparation" |
| New input file type | Add suffix to `SUPPORTED_SUFFIXES` in `interfaces.py` **and** a builder to `_SUFFIX_TO_BUILDER` in `infrastructure/input_provider_factory.py` |
| PDF embedded-text rules | Thresholds/modes in `domain/pdf_text_layer.py`; measurement in `PdfInputProvider` (`infrastructure/input_providers.py`); setting is `OcrConfig.pdf_text_layer` (default `auto`), passed to providers via `InputOptions` in `input_provider_factory.py`; benchmark always OCRs |
| New OCR output notation (HTML/Markdown/…) | Add an `OcrOutputParser` to `infrastructure/ocr_formats/` and list it in `default_parsers()`; model in `domain/formatted_text.py`, adapters use `application/formatting.py:TextFormatter` (see `docs/development.md` → "OCR output formats") |
| New run requirement | Add an `IssueKind` and its check to `check_stage_requirements` / `check_run_preflight` (`application/service.py`), and a row in `gui/_fix_targets.py:fix_for` — the readiness line, the requirement guard and the "run can't start" dialog all take their line and buttons from it. Preselect entries for Add-ons are model tags, `provider:<id>` and `spacy:<model>` |
| Setup Assistant | Rules in `application/setup_plan.py` (`build_setup_plan` is a pure function of `SetupAnswers` + `SetupFacts`), page order in `gui/setup/_flow.py:next_page`, summary wording in `gui/setup/_summary.py`. It writes **only** keys the Settings window or the main window already own — no new `Config` key, no migration. Two rules: a working choice is kept (an installed model that fits is not replaced) and nothing is ever turned off ("Remove personal info" is only set to `True`; a review with its own model keeps it). **Nothing starts by itself**: downloads need Set up, a network search its own button, Ollama **Start Ollama**, the speed test **Test speed**; `ollama_url` never changes without the teacher choosing it. See `docs/development.md` → "Setup Assistant" |
| Ollama found / running | `infrastructure/ollama_install.py` (program search, start, models folder, free space, `OLLAMA_DOWNLOAD_URL`); the two `IssueKind`s `OLLAMA_NOT_FOUND` / `OLLAMA_NOT_RUNNING` and their fix rows in `gui/_fix_targets.py` (`COMMAND_GET_OLLAMA`, `COMMAND_START_OLLAMA`; `RequirementIssue.startable` is `False` on Linux, where the tooltip names the service command instead). The readiness line re-checks every 10 s for all three Ollama kinds, so it turns green by itself. `gui/_ollama_status_line.py` is the same state on *This computer* |
| New GUI capability/check | Implement in `ProcessingApplicationService`, call it from the GUI — never duplicate discovery logic in widgets |
| New GUI setting | Per-batch choices (documents, language, which steps run, Results) belong in `ConfigPanel`; anything set once belongs on a page of the Settings window (`gui/settings/`). Add the widget to the owning page and its key to that page's `load` / `values` / `owned_keys` — never to two pages. Say where to change it with the `WHERE_*` constants in `gui/_user_text.py`, not a hard-coded menu path. See `docs/development.md` → "Settings window" |
| Benchmark app changes | Domain metrics in `domain/benchmark.py`; run persistence in `infrastructure/benchmark/run_store.py`; OCR-only run in `infrastructure/benchmark/runner.py`; orchestration in `application/benchmark_service.py`; GUI in `gui/benchmark/` (reuse `gui/_ocr_config_selector.py`). The OCR config slice is `Config.ocr` (`config.py:OcrConfig`). |
| Model temperatures | Teachers set none. `RECOMMENDED_TEMPERATURES` in `domain/model_catalog.py` holds what each model's maker recommends; `request_profile(tag, provider)` and `review_temperature()` read it (0.2 and 0.7 where a maker states none, and for every online provider), `Config.correction_temperature` / `evaluate_temperature` expose it, and the cache key and the evaluation tools use the same functions. A `RequestProfile` carries one `StepProfile` (temperature, `think`) per step of the exchange; `profile.temperature` / `profile.think` are step ①'s. |
| New proofreading exchange | Implement `Exchange` (`proofread`, `max_text_chars`, `cost_model`) in a module of `infrastructure/proofreading/exchanges/`, register it in `EXCHANGES` under its `ExchangeKind` (`domain/proofreading/result.py`), and build it through `build_exchange()` so port, cut-off tries and clause check arrive the same way. Map the exchange's own answer schema to `ExchangeResult` — nothing outside `infrastructure/proofreading/` may see it. `FallbackExchange(primary, fallback)` is the chain of responsibility. See `docs/development.md` → "Proofreading exchange" |
| Proofreading steps / prompts | The three system prompts are fixed and versioned in `domain/correction_prompts.py` (`CORRECT_SYSTEM`, `EXPLAIN_SYSTEM`, `UNCLEAR_SYSTEM`, `PROMPT_VERSION`); languages, level and the teacher's notes travel in the user message (`parameters()`, `notes_block()`). Never make a prompt editable and never put output-format rules into it. The answer schemas of steps ② and ③ are in `infrastructure/proofreading/schemas.py`, the retry and repair rules in `runner.py:StepRunner` |
| New evaluation corpus | Add `<name>.py` to `tools/model_eval/corpora/` (an `ADAPTER` class); a new test set is a module in `tools/model_eval/recipes/` that sets `RECIPE`. Nothing lists either: both packages find their modules. An adapter reads only the files it pins. |

### Apps & storage

Two GUI apps share the layers above: the main pipeline (`teachers-teammate` /
`teachers-teammate-gui`) and **Benchmark** (`teachers-teammate-benchmark`,
`gui/benchmark:main_benchmark`) — runs a document through OCR configs, keeps an
append-only history, and compares two runs side-by-side.

All persistent data lives under `resolve_storage_root()`
(`$TEACHERS_TEAMMATE_TMPDIR` or the OS cache dir). Identity/fingerprint helpers:
`compute_file_hash` (document content), `compute_ocr_config_hash` (OCR stage
fingerprint), `compute_cache_key` (path → short key). Two distinct stores: the
pipeline **cache** (`StateRepository`, one overwritten `DocumentState` per
`(output_dir, path)`, **no expiry**) and the benchmark **run store**
(`BenchmarkRunStore`, append-only per document-hash, keep-last-N + manual
delete). See `docs/development.md` → "Storage, hashing & caching".

## Code conventions

- **Line length**: 100. Double quotes. Config under `[tool.ruff]` in `pyproject.toml`.
- **Lazy imports** (imports inside functions): add `# noqa: PLC0415`.
- **Text notation**: `raw_text` / `correction_text` are plain *or* Markdown (see `raw_text_format`). Never feed them to diffs/metrics directly — use `TextFormatter.plain_text`; never parse them from a widget — use `TextFormatter.document`.
- **Type checker**: `ty` (Astral, Rust-based). Configure under `[tool.ty]` in `pyproject.toml`. Runtime packages (PySide6, numpy, spaCy, …) are resolved under Bazel via the lint-only `//teachers_teammate:gui_lib_typed` / `ocr_lib_typed` targets (which carry the pip deps; the wheel libs stay clean). Optional extras (LLM providers, GPU libs) are intentionally not wired, so `unresolved-import` is kept ignored for their lazy imports.
- **Suppress ty with `# ty: ignore[<rule>]`, NOT `# type: ignore[...]`.** ty uses its own directive and rule names (`assignment`→`invalid-assignment`, `arg-type`→`invalid-argument-type`, `override`→`invalid-method-override`, `union-attr`→`unresolved-attribute`, …); mypy-style `# type: ignore` is silently inert. Prefer fixing or a precise `# ty: ignore[<rule>]` with a reason.
- **Run linters through Bazel only.** ruff/ty versions are pinned in `third_party/lint/tools.bzl` (wired from `MODULE.bazel`); a `ruff`/`ty` installed into a local `.venv` can be a different version and give different results. Use `bazel test //:lint` and `bazel run //:format.fix` — do not run venv `ruff`/`ty` directly.
- **GUI code** (`teachers_teammate/gui/`): use pytest-qt for widget/thread behavior.

## Testing conventions

- **Every test** must have a `"""Given … / When … / Then …"""` docstring.
- Use `make_config(tmp_path, **overrides)` (plain function in `tests/conftest.py`) — not a fixture.
- Fixtures: `sample_png`, `sample_pdf` in `tests/conftest.py`.
- **Patch at the import site**, not at the origin:
  - `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor` (the builder constructs it there; `make_config` defaults to the built-in reader, `engine="ppocr"`, and `conftest.py` points the model store at an empty temporary folder, so nothing downloads)
  - For Ollama: `patch.dict(sys.modules, {"ollama": mock_ollama})` where `mock_ollama` exposes a mock `Client` (the `ollama` library is lazy-imported inside `OllamaClient`)
  - For lazy provider imports: `langchain_openai.ChatOpenAI`
- Simulate missing optional packages: `patch.dict(sys.modules, {"langchain_anthropic": None})` → expect `RuntimeError` (message contains "is not installed"). Provider `create()` raises `RuntimeError`, and `build_llm()` converts any `SystemExit` from a provider into `RuntimeError` so it never propagates to callers.
- The built-in reader's real models (`reader_store(tmp_path)` in `tests/conftest.py`, Bazel-fetched `http_file`s) run only in tests marked `needs_reader_models`; everything else uses `fake_reader_store` or patches the processor.

## Installed LangChain packages (dev env)

| Package | Installed |
|---|---|
| `langchain_openai` | ✓ |
| `langchain_ollama` | ✓ |
| `langchain_anthropic` | ✗ |
| `langchain_google_genai` | ✗ |
| `langchain_mistralai` | ✗ |
| `langchain_cohere` | ✗ |

Tests for uninstalled providers must mock the import.
