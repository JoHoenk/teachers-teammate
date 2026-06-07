# Teacher's Teammate — Copilot Instructions

**Teacher's Teammate** is a batch OCR pipeline (CLI + PySide6 GUI) for handwritten documents.
Full documentation: [docs/development.md](../docs/development.md) · [docs/testing.md](../docs/testing.md) · [docs/advanced_user_guide.md](../docs/advanced_user_guide.md)

---

## Quick-start commands

```bash
# Activate venv
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
genhtml bazel-out/_coverage/_coverage_report.dat -o coverage_html/

# Lint + type check (all in one)
bazel test //:py_ruff //:py_pylint //:py_ty --test_output=errors

# Auto-fix ruff violations
ruff check teachers_teammate/ --fix

# Format
bazel run //:format.fix

# Build & serve Sphinx docs
bazel build //docs:html
bazel run //docs:html.serve
```

---

## Architecture

The pipeline is defined in `teachers_teammate/infrastructure/pipeline.py` (`OCRPipeline`).
Key interfaces live in `teachers_teammate/interfaces.py` (ABCs for every stage).
See the architecture diagrams in [docs/development.md](../docs/development.md).

```
Config (config.py)
  → InputProvider (input_providers.py)          PDF/image/TXT → InputPayload of InputUnits
  → ImagePreprocessor (image_preprocessor.py)   adaptive_threshold / CLAHE / grayscale / none
  → OCRProcessor (ocr_processor.py)             Ollama | Tesseract | Paddle | LangChain
     (TXT files bypass OCR — content flows directly to the next stage)
  → Anonymizer (anonymizer.py)                  spaCy NER + regex, optional
  → Corrector (correction.py)                   LangChain-based, optional
  → Evaluator (evaluation.py)                   LangChain-based, optional
  → DocumentCreator (docx_builder.py)           table or comments DOCX format
```

`OCRPipeline` (`infrastructure/pipeline.py`) is the composition root — wires services together but contains no stage logic. Stage logic lives in `infrastructure/workflow/`: `FileProcessor` runs all stages for one file, `run_files()` (plain function in `coordinator.py`) iterates over the file list.

Application layer: `application/service.py` (`ProcessingApplicationService`) is the single entry point for GUI and CLI. `application/commands.py` (`ApplicationCommands`) handles all state-mutating write operations.

LLM provider selection lives in `infrastructure/llm_factory.py`.
All provider modules are in `teachers_teammate/infrastructure/providers/`.

---

## Code conventions

- **Line length**: 100. Double quotes. Config in `pyproject.toml` under `[tool.ruff]`.
- **Lazy imports** (imports inside functions): add `# noqa: PLC0415`.
- **Type checker**: `ty` (Astral, Rust-based). Configure under `[tool.ty]` in `pyproject.toml`. `unresolved-import` is suppressed in the Bazel sandbox — only internal type errors are caught there.
- **GUI code** (`teachers_teammate/gui/`): use pytest-qt for widget/thread behavior where practical.

---

## Testing conventions

See [docs/testing.md](../docs/testing.md) for the full guide.

Key points:
- **Every test** must have a `"""Given … / When … / Then …"""` docstring.
- Use `make_config(tmp_path, **overrides)` (plain function in `tests/conftest.py`) — not a fixture.
- Fixtures: `sample_png`, `sample_pdf` in `tests/conftest.py`.
- **Patch at the import site**, not at the origin:
  - `teachers_teammate.infrastructure.pipeline.TesseractOCRProcessor`
  - For Ollama: `patch.dict(sys.modules, {"ollama": mock_ollama})` where `mock_ollama` exposes a mock `Client` (the `ollama` library is lazy-imported inside `OllamaClient`)
  - For lazy provider imports: `langchain_openai.ChatOpenAI`
- Simulate missing optional packages: `patch.dict(sys.modules, {"langchain_anthropic": None})` → expect `SystemExit(1)`.
- PaddleOCR (optional): `patch.dict(sys.modules, {"paddleocr": mock_module})`.
- **Coverage target**: keep meaningful tests and avoid coverage padding.

---

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
