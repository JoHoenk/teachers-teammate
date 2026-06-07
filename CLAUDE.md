# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick-start commands

```bash
# Activate venv (not required for bazel)
source .venv/bin/activate

# Run tests
bazel test //tests:unit_tests --test_output=errors   # fast, no external services
bazel test //tests/... --test_output=errors           # full suite (skips Tesseract/Ollama if absent)

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
```

### Pipeline flow

```
Config
  → InputProvider        (input_providers.py)      PDF/image/TXT → InputPayload of InputUnits
  → ImagePreprocessor    (image_preprocessor.py)   adaptive_threshold / CLAHE / grayscale / none
  → OCRProcessor         (ocr_processor.py)        Ollama | Tesseract | Paddle | LangChain
     (TXT files bypass OCR — content flows directly to the next stage)
  → Anonymizer           (anonymizer.py)           spaCy NER + regex, optional
  → Corrector            (correction.py)           LangChain-based, optional
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
| New LLM provider | Add `<name>.py` to `infrastructure/providers/` with a `create()` function |
| New input file type | Add suffix to `SUPPORTED_SUFFIXES` in `interfaces.py` **and** a builder to `_SUFFIX_TO_BUILDER` in `infrastructure/input_provider_factory.py` |
| New GUI capability/check | Implement in `ProcessingApplicationService`, call it from the GUI — never duplicate discovery logic in widgets |
| Benchmark app changes | Domain metrics in `domain/benchmark.py`; run persistence in `infrastructure/benchmark/run_store.py`; OCR-only run in `infrastructure/benchmark/runner.py`; orchestration in `application/benchmark_service.py`; GUI in `gui/benchmark/` (reuse `gui/_ocr_config_selector.py`). The OCR config slice is `Config.ocr` (`config.py:OcrConfig`). |

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
- **Type checker**: `ty` (Astral, Rust-based). Configure under `[tool.ty]` in `pyproject.toml`. Runtime packages (PySide6, numpy, spaCy, …) are resolved under Bazel via the lint-only `//teachers_teammate:gui_lib_typed` / `ocr_lib_typed` targets (which carry the pip deps; the wheel libs stay clean). Optional extras (LLM providers, PaddleOCR, GPU libs) are intentionally not wired, so `unresolved-import` is kept ignored for their lazy imports.
- **Suppress ty with `# ty: ignore[<rule>]`, NOT `# type: ignore[...]`.** ty uses its own directive and rule names (`assignment`→`invalid-assignment`, `arg-type`→`invalid-argument-type`, `override`→`invalid-method-override`, `union-attr`→`unresolved-attribute`, …); mypy-style `# type: ignore` is silently inert. Prefer fixing or a precise `# ty: ignore[<rule>]` with a reason.
- **Run linters through Bazel only.** ruff/ty versions are pinned in `third_party/lint/tools.bzl` (wired from `MODULE.bazel`); a `ruff`/`ty` installed into a local `.venv` can be a different version and give different results. Use `bazel test //:lint` and `bazel run //:format.fix` — do not run venv `ruff`/`ty` directly.
- **GUI code** (`teachers_teammate/gui/`): use pytest-qt for widget/thread behavior.

## Testing conventions

- **Every test** must have a `"""Given … / When … / Then …"""` docstring.
- Use `make_config(tmp_path, **overrides)` (plain function in `tests/conftest.py`) — not a fixture.
- Fixtures: `sample_png`, `sample_pdf` in `tests/conftest.py`.
- **Patch at the import site**, not at the origin:
  - `teachers_teammate.infrastructure.pipeline.TesseractOCRProcessor`
  - For Ollama: `patch.dict(sys.modules, {"ollama": mock_ollama})` where `mock_ollama` exposes a mock `Client` (the `ollama` library is lazy-imported inside `OllamaClient`)
  - For lazy provider imports: `langchain_openai.ChatOpenAI`
- Simulate missing optional packages: `patch.dict(sys.modules, {"langchain_anthropic": None})` → expect `RuntimeError` (message contains "is not installed"). Provider `create()` raises `RuntimeError`, and `build_llm()` converts any `SystemExit` from a provider into `RuntimeError` so it never propagates to callers.
- PaddleOCR (optional): `patch.dict(sys.modules, {"paddleocr": mock_module})`.

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
