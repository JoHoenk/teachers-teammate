# AGENTS.md

Guidance for AI coding agents (Claude Code, Copilot, Codex, …) working in this repository.
People: see [CONTRIBUTING.md](CONTRIBUTING.md). Claude Code reads this file through `CLAUDE.md`.

**Teacher's Teammate** is a batch OCR and proofreading pipeline (CLI + PySide6 GUI) for
handwritten documents.

## Commands

```bash
bazel test //tests:unit_tests --test_output=errors   # fast, no external services
bazel test //tests/... --test_output=errors           # full suite (skips Ollama tests if absent)
bazel test //tests:test_config --test_output=errors   # one test file
bazel test //:lint --test_output=errors               # ruff + pylint + ty
bazel run //:format.fix                               # ruff format + lint-safe fixes
bazel build //docs:html                               # Sphinx docs (serve: bazel run //docs:html.serve)
```

Coverage: see `docs/testing.md`. A venv (`source .venv/bin/activate`) is for running the app
and the editor, not for Bazel.

## Architecture

Layers, innermost first; a layer imports only from those above it in this list
(`tests/unit/test_layer_imports.py` guards it):

```
domain/          — pure business concepts (freshness policy, stage names); no I/O
interfaces.py    — ABCs for every pipeline stage; SUPPORTED_SUFFIXES lives here
infrastructure/  — concrete implementations (OCR engines, LLM providers, DOCX builder, etc.)
infrastructure/workflow/ — FileProcessor (per-file stage orchestrator), run_files() coordinator
application/     — ProcessingApplicationService (queries) + ApplicationCommands (writes);
                   the only entry point for GUI and CLI
gui/ / cli.py    — adapters; no discovery or availability logic
                   (gui/settings/ = the Settings window, one page per topic)
texts/           — every sentence a teacher reads; Qt-free, imports only domain
```

Pipeline stages, each an ABC in `interfaces.py`: InputProvider → ImagePreprocessor →
OCRProcessor (TXT files skip it) → OcrOutputPostprocessor → Anonymizer (optional) →
Corrector (`proofreading/`, optional) → Evaluator (optional) → DocumentCreator. The stage
list and its diagrams are in `docs/development.md` → "Architecture".

`OCRPipeline` (`infrastructure/pipeline.py`) is the composition root: it wires services and
holds no stage logic. `FileProcessor` runs all stages for one file; `run_files()` (a plain
function in `coordinator.py`) iterates over the file list.

Two GUI apps share these layers: the main window (`teachers-teammate-gui`; the CLI
`teachers-teammate` runs the same pipeline) and **Benchmark** (`gui/benchmark/`).

## Hard rules

What the code cannot tell you. Each is explained in [docs/extension_points.md](docs/extension_points.md)
or `docs/development.md`.

**Privacy and what runs where**
- Text recognition never uses an online engine (`LOCAL_OCR_ENGINES`). An LLM provider is shown
  and confirmed as an online service unless `PROVIDER_INFO["self_hosted"]` is `True`.
- The privacy settings are not part of the proofreading and review fingerprints; what a run
  sent is stored with its result (`SentRecord`).

**Nothing starts by itself**
- Model and add-on downloads only on request (a button, the Setup Assistant, a CLI flag),
  never at start-up and never silently.
- The Ollama network search only from an explicit user action; `ollama_url` never changes
  without the teacher choosing it. The Setup Assistant writes only keys that Settings or the
  main window already own and never turns anything off.

**Where code goes**
- GUI and CLI contain no discovery or availability logic: implement it in
  `ProcessingApplicationService`, call it from the GUI.
- A sentence a teacher reads is defined in `teachers_teammate/texts/` and used by reference,
  never a literal at the call site. Message boxes and file pickers go through
  `gui/_dialogs.py`. `tests/unit/test_user_text_centralised.py` enforces it.
- A new setting goes on exactly one page of `gui/settings/` (or in `ConfigPanel` if it is a
  per-batch choice), never two.
- Anything a teacher cannot rebuild lives in the config or data root, never the cache.

**Results and caches**
- `raw_text` / `correction_text` are plain or Markdown (`raw_text_format`): use
  `TextFormatter.plain_text` / `.document`, and `PlainMap` for positions in views, never
  string arithmetic on a widget's text.
- A stored review is never half right: if `fits(review, raw_plain)` fails, drop it with a
  warning. All readers go through `align()`.
- A stopped file is `ProcessingResult.stopped`, never a failure, never stored half done.
  Recognition switched off makes a file *skipped*, never an error.
- Output of the reader changes → bump `READER_VERSION`; automatic image preparation changes →
  bump `AUTO_VERSION` and keep `tests/unit/test_preprocessing_auto_golden.py` green.
- Model temperatures only through `request_profile()` / `review_temperature()`.

**Prompts**
- The system prompts in `domain/correction_prompts.py` are fixed: never editable, never with
  output-format rules. A step ① variant's text never changes once a measurement used it
  (the cache key hashes it); another text is another id.

**Generated and ignored files**
- Never hand-edit `third_party_licenses/` (except `curated/`); run `tools/licenses/inventory.py`.
- The git-ignored working-notes folder is not part of the repository. No source, test or
  document may point into it or cite its ids
  (`tests/tools/test_no_plan_references.py`).

## Extension points

[docs/extension_points.md](docs/extension_points.md) has one section per task (new pipeline
stage, LLM provider, OCR engine, local model file, input file type, run requirement, GUI
setting, proofreading exchange, evaluation corpus, …). Read the section before you touch
that area. A new LLM provider is a new file in `infrastructure/providers/` and nothing else.

## Commits and pull requests

- Branch off `main`, named `joho_<topic>`. Commit subjects are `[area, area] Sentence`
  (`[app, gui]`, `[tests]`, `[docs]`, `[bazel]`, `[packaging]`, `[tool]`), and the merge adds
  `(#NN)`. Look at `git log` before inventing an area.
- Before a PR: `bazel test //:lint` and `bazel test //tests/...` pass.

## Code conventions

- Line length 100, double quotes (`[tool.ruff]` in `pyproject.toml`).
- Lazy imports (inside functions): add `# noqa: PLC0415`.
- Type checker is `ty`. Suppress with `# ty: ignore[<rule>]`, **not** `# type: ignore[...]`
  (ty ignores it silently). ty uses its own rule names (`invalid-assignment`,
  `invalid-argument-type`, `invalid-method-override`, `unresolved-attribute`, …). Prefer
  fixing the cause. `unresolved-import` is ignored on purpose for optional extras.
- **Run linters through Bazel only** (`bazel test //:lint`, `bazel run //:format.fix`). A
  `ruff` or `ty` in a local `.venv` can be a different version and give different results.
- GUI code: test widgets and threads with pytest-qt.

## Testing conventions

- Every test has a `"""Given … / When … / Then …"""` docstring.
- A test's Bazel `deps` list the highest layer it imports (`_DOMAIN_LIB`,
  `_INFRASTRUCTURE_LIB`, `_APPLICATION_LIB`, `gui_layer_lib`, … in `tests/BUILD`), never
  `//teachers_teammate:ocr_lib` / `:gui_lib` (every test would rerun on every edit). A new
  module goes into its layer's `_*_SRCS` list in `teachers_teammate/BUILD`.
- Configs: `make_config(tmp_path, **overrides)` from `tests/conftest.py` (a plain function,
  not a fixture).
- Patch at the import site, not at the origin:
  - `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor` (`make_config` defaults to
    the built-in reader `ppocr`; `conftest.py` points the model store at an empty temporary
    folder, so nothing downloads).
  - A test that needs an Ollama *server* (real client, threads, pull stream) uses
    `tests/fakes/fake_ollama.py:FakeOllama`, the one stand-in. Need another answer? Teach
    it a switch; do not write a second server or monkeypatch `OllamaClient`. Only a test of
    `OllamaClient`'s own handling of the library patches
    `patch.dict(sys.modules, {"ollama": mock_ollama})`.
  - Lazy provider imports: `langchain_openai.ChatOpenAI`.
- Under Bazel only `langchain-core` is installed. A test never relies on a provider package
  being present: `patch.dict(sys.modules, {"langchain_anthropic": None})` simulates a missing
  one and expects `RuntimeError` ("is not installed"). `build_llm()` converts a provider's
  `SystemExit` into `RuntimeError`.
- The built-in reader's real models run only in tests marked `needs_reader_models`
  (`reader_store(tmp_path)`, Bazel `http_file`s); everything else uses `fake_reader_store` or
  patches the processor. No test touches the network.
