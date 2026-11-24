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
bazel test //:lock_tests --test_output=errors         # the three locks after a dependency change
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
texts/           — every sentence a teacher reads; Qt-free, imports `domain` and `pkg`;
                   infrastructure and the adapters use it, `application` does not
interfaces.py    — ABCs for every pipeline stage; SUPPORTED_SUFFIXES lives here
infrastructure/  — concrete implementations (OCR engines, LLM providers, DOCX builder, etc.)
infrastructure/workflow/ — FileProcessor (per-file stage orchestrator), run_files() coordinator
application/     — the services, one per topic: RunService (the run) in `run.py`, plus
                   providers, reader, recommendations, speeds, anonymization, addons, data_flow,
                   setup, export, stored results (what earlier runs kept), DocumentQueries
                   (reads) and DocumentCommands (writes), reached as `.run` / `.stored` /
                   `.documents` / `.commands`; the only entry for GUI and CLI
gui/ / cli.py    — adapters; no discovery or availability logic
                   (gui/settings/ = the Settings window, one page per topic)
```

`gui/` and `cli.py` import `pkg`, `core`, `domain`, `application` and `texts` only — never
`infrastructure`. The modules that wire concrete classes are the exception and the only
exception: `gui/composition.py`, `gui/__main__.py`, `gui/benchmark/composition.py`,
`gui/benchmark/__main__.py`, `cli_composition.py`, `__main__.py` and `frozen_modes.py`.
A module does not re-export a name it does not define either: a name has one home, and a
caller imports it from there.

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
  and confirmed as an online service unless `PROVIDER.self_hosted` is `True`.
- The privacy settings are not part of the proofreading and review fingerprints; what a run
  sent is stored with its result (`SentRecord`).

**Nothing starts by itself**
- Model and add-on downloads only on request (a button, the Setup Assistant, a CLI flag),
  never at start-up and never silently.
- The Ollama network search only from an explicit user action; `ollama_url` never changes
  without the teacher choosing it. The Setup Assistant writes only keys that Settings or the
  main window already own and never turns anything off.

**Where code goes**
- GUI and CLI contain no discovery or availability logic: implement it in the service that
  owns the topic (`application/run.py`, `providers.py`, …), call it from the GUI.
- A sentence a teacher reads is defined in `teachers_teammate/texts/` and used by reference,
  never a literal at the call site. Message boxes and file pickers go through
  `gui/_dialogs.py`. `tests/unit/test_user_text_centralised.py` enforces it.
- A failure raised in `infrastructure` carries a `Message` (`teachers_teammate/messages.py`): a
  key and its data, never a sentence. `texts/messages.py` words it. A caught error goes in as
  `message_of(exc)`, never `str(exc)`; a new key is an enum value, a factory, a renderer and a line
  in the golden list of `tests/unit/test_messages.py`, and a released key's value never changes.
- A service is built once per entry point (`application/composition.py`, called from the
  composition modules); every other class is given the one it uses, never builds or looks one up
  (`tests/unit/test_service_construction.py`).
- A new setting goes on exactly one page of `gui/settings/` (or in `ConfigPanel` if it is a
  per-batch choice), never two.
- Anything a teacher cannot rebuild lives in the config or data root, never the cache.
- A diagnostic goes to `logging` (it lands in `<data root>/logs/teachers-teammate.log`, Help →
  Open Log Folder); a sentence a teacher reads goes to the Reporter or to `texts/`. Nothing
  prints. A log line never holds a document's text, a prompt or a key
  (`tests/unit/test_log_lines_carry_no_text.py`).
- An `except` that only passes, continues or returns a constant says in a comment why nothing
  happens, or writes a line (`tests/unit/test_silent_handlers.py`).

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

**Evaluation records**
- Every record — runs, test sets with their texts, marks, machines, model sizes, gate records — lives
  in the private repository `teachers-teammate-evals`, which every tool takes as `--store`. Never in
  this one: `store_path()` refuses a store inside this tree and `tests/tools/test_no_eval_records.py`
  fails on a run file, a manifest, a size record or a marks file here. The texts come from corpora
  that may not be passed to a third party, so that repository stays private.
- A gate record is a verdict, the runs it rests on and the command that reproduces the figures — no
  figure. A number copied into a record goes stale the first time a scorer changes.

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
that area. A new LLM provider is a new file in `infrastructure/providers/` plus its two
default models in `_model_defaults.py`.

## Commits and pull requests

- Branch off `main`, named `joho_<topic>`. Commit subjects are `[area, area] Sentence`
  (`[app, gui]`, `[tests]`, `[docs]`, `[bazel]`, `[packaging]`, `[tool]`), and the merge adds
  `(#NN)`. Look at `git log` before inventing an area.
- Before a PR: `bazel test //:lint` and `bazel test //tests/...` pass.

## Code conventions

- Line length 100, double quotes (`[tool.ruff]` in `pyproject.toml`).
- Lazy imports (inside functions): add `# noqa: PLC0415`. The count of them in `application/` is
  pinned by `tests/unit/test_lazy_import_budget.py`; a collaborator a service branches on belongs
  in `application/ports.py` as a `Protocol` and comes in through the constructor. Which
  `application/` modules import `infrastructure` at all, and how many names each imports, is
  pinned by `tests/unit/test_application_infrastructure_budget.py`; both tables only shrink.
- Type checker is `ty`. Suppress with `# ty: ignore[<rule>]`, **not** `# type: ignore[...]`
  (ty ignores it silently). ty uses its own rule names (`invalid-assignment`,
  `invalid-argument-type`, `invalid-method-override`, `unresolved-attribute`, …). Prefer
  fixing the cause. `unresolved-import` is ignored on purpose for optional extras.
- **Run linters through Bazel only** (`bazel test //:lint`, `bazel run //:format.fix`). A
  `ruff` or `ty` in a local `.venv` can be a different version and give different results.
- GUI code: test widgets and threads with pytest-qt.

## Writing: docs and comments

- Say it once, short and plain. Describe the current state only: no history ("was", "now",
  "previously", "no longer", "since"), no upgrade notes, no stories, no dated measurements or
  status. History is in git; plans and open items go in the git-ignored working-notes folder.
- A rule or a number lives in one place, preferably the code. A doc points to it and does not copy
  it; a copied figure goes stale.
- Each document keeps to its reader: `README.md` and `docs/index.md`, `using_the_app.md`,
  `advanced_user_guide.md` are for teachers (what they see, in their words); everything else is for
  developers.
- Code comments and docstrings only where the code is not obvious, one or two lines, and never
  history. A field's docstring says what it means, not how it came about.

## Testing conventions

- Every test has a `"""Given … / When … / Then …"""` docstring.
- A test's Bazel `deps` list the highest layer it imports (`_DOMAIN_LIB`,
  `_INFRASTRUCTURE_LIB`, `_APPLICATION_LIB`, `gui_layer_lib`, … in `tests/BUILD`), never
  `//teachers_teammate:ocr_lib` / `:gui_lib` (every test would rerun on every edit). A new
  module goes into its layer's `_*_SRCS` list in `teachers_teammate/BUILD`. See
  `docs/testing.md` → "Test dependencies and Bazel's cache".
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
