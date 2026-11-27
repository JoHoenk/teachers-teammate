# AGENTS.md

Rules for coding agents. **Teacher's Teammate** is a batch OCR and proofreading pipeline (CLI +
PySide6 GUI) for handwritten documents.

## Commands

```bash
bazel test //tests:unit_tests --test_output=errors   # fast
bazel test //tests/... --test_output=errors           # full suite
bazel test //tests:test_config --test_output=errors   # one test file
bazel test //:lock_tests --test_output=errors         # after a dependency change
bazel test //:lint --test_output=errors               # ruff + ty; never a local ruff/ty
bazel run //:format.fix
bazel build //docs:html
```

## Architecture

Layers, innermost first; each imports only inner ones (`tests/unit/test_layer_imports.py`):
`pkg` (`exceptions.py`, `messages.py`) → `domain/` (pure, no I/O) → `texts/` (every sentence a
teacher reads) and core (`config.py`, `interfaces.py`, `cache_keys.py`, `run_rules.py`,
`settings_schema.py`), which do not import each other → `infrastructure/` → `application/` (one
service per topic; the only entry for GUI and CLI; does not import `texts/`) → `gui/`, `cli.py`.

- `gui/` and `cli.py` never import `infrastructure`. Only the composition modules wire concrete
  classes: `gui/composition.py`, `gui/__main__.py`, `cli_composition.py`, `__main__.py`,
  `frozen_entry.py`, `frozen_modes.py`.
- A name has one home; no module re-exports a name it does not define.
- Stages are ABCs in `interfaces.py`. `OCRPipeline` wires them, `FileProcessor` runs one file,
  `run_files()` the list.
- Before touching an area, read its section in `docs/extension_points.md`.

## Hard rules

**Privacy**
- Text recognition never uses an online engine (`LOCAL_OCR_ENGINES`). An LLM provider counts as
  online unless `PROVIDER.self_hosted`.
- Privacy settings are not part of the proofreading and review fingerprints; what a run sent is
  stored with its result (`SentRecord`).
- A log line never holds a document's text, a prompt or a key.

**Nothing starts by itself**
- Downloads only on request (button, Setup Assistant, CLI flag), never at start-up.
- The Ollama network search only on a user action; `ollama_url` changes only by the teacher's
  choice. The Setup Assistant writes only keys Settings or the main window own and never turns
  anything off.

**Where code goes**
- No discovery or availability logic in GUI or CLI: it goes in the owning service.
- Teacher-facing sentences live in `texts/`, never as literals; dialogs and file pickers go through
  `gui/_dialogs.py`.
- An `infrastructure` failure carries a `Message` (key + data), worded in `texts/messages.py`. Pass a
  caught error as `message_of(exc)`, never `str(exc)`. A new key: enum value, factory, renderer,
  golden line in `tests/unit/test_messages.py`; a released key's value never changes.
- A service is built once per entry point (`application/composition.py`) and passed in, never built
  or looked up elsewhere.
- A collaborator a service branches on is a `Protocol` in `application/ports.py`, passed through the
  constructor. The lazy-import and infrastructure-import budgets of `application/` only shrink.
- A setting lives on exactly one page of `gui/settings/` (or in `ConfigPanel` if per batch).
- What a teacher cannot rebuild lives in the config or data root, never the cache.
- Diagnostics go to `logging`; nothing prints. An `except` that only passes, continues or returns a
  constant says why in a comment.
- Log once, where the error is handled; never log and re-raise. A catch-all and a failure shown to
  the teacher log through `messages.log_failure`. `docs/error_handling.md` has the rest: expected
  errors against bugs, and the process's nets.
- The GUI shows a failure only through `gui/_errors.py`; a failed file gets its row and the
  end-of-run line, never a dialog. A success line goes to the status bar: the window keeps no log
  pane.

**Results and caches**
- `raw_text` / `correction_text` are plain or Markdown: use `TextFormatter.plain_text` / `.document`
  and `PlainMap` for positions, never string arithmetic on a widget's text.
- A stored review that fails `fits(review, raw_plain)` is dropped with a warning; readers go through
  `align()`.
- A stopped file is `ProcessingResult.stopped`, never a failure or half stored. Recognition off makes
  a file *skipped*, not an error.
- Reader output changes → bump `READER_VERSION`; automatic image preparation changes → bump
  `AUTO_VERSION`.
- Temperatures only through `request_profile()` / `review_temperature()`.
- The system prompts in `domain/correction_prompts.py` are fixed: not editable, no output-format
  rules. A measured step ① variant's text never changes; another text is another id.

**Repository**
- Evaluation records live only in the private `teachers-teammate-evals` (`--store`). A gate record
  holds no figures.
- Never hand-edit `third_party_licenses/` (except `curated/`); run `tools/licenses/inventory.py`.
- Nothing points into the git-ignored working-notes folder or cites its ids.

## Conventions

- `ty`: suppress with `# ty: ignore[<rule>]` (ty's own rule names), never `# type: ignore`. An
  unresolvable optional import goes in `allowed-unresolved-imports` in `pyproject.toml`.
- Lazy import inside a function: `# noqa: PLC0415`.
- Commits: branch `joho_<topic>` off `main`; subject `[area, area] Sentence` (areas from `git log`).
  Before a PR, lint and the full suite pass.

## Tests

- Every test has a `"""Given … / When … / Then …"""` docstring.
- Bazel `deps` name the highest layer the test imports (`_DOMAIN_LIB`, `_APPLICATION_LIB`, … in
  `tests/BUILD`), never `:ocr_lib` / `:gui_lib`. A new module goes into its layer's `_*_SRCS` in
  `teachers_teammate/BUILD`.
- Configs: `make_config(tmp_path, **overrides)` (plain function). Patch at the import site, e.g.
  `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor`, `langchain_openai.ChatOpenAI`.
- An Ollama server in a test is `tests/fakes/fake_ollama.py:FakeOllama`; extend it, never write a
  second one or monkeypatch `OllamaClient`.
- Only `langchain-core` is installed under Bazel: simulate a missing provider with
  `patch.dict(sys.modules, {"langchain_anthropic": None})` and expect `RuntimeError`.
- Real reader models only in tests marked `needs_reader_models`; others use `fake_reader_store`. No
  test touches the network. GUI: pytest-qt.

## Writing

- Short and plain, current state only: no history, upgrade notes, stories or dated figures.
- A rule or number lives in one place, preferably the code; docs point to it.
- `README.md`, `docs/index.md`, `using_the_app.md`, `advanced_user_guide.md` are for teachers, in
  their words; everything else for developers.
- Comments only where the code is not obvious, one or two lines, never history.
