# Testing Guide

## Audience and scope

This guide covers how to run the test suite, understand test conventions, and generate coverage reports.
For linting, type checking, and formatting commands, see {doc}`Development <development>`.
End-user runtime usage is documented in {doc}`overview <index>` and {doc}`advanced_user_guide <advanced_user_guide>`.

## Test stack

| Package | Role |
|---|---|
| `pytest` | Test runner |
| `pytest-mock` | `mocker` fixture — convenient wrapper around `unittest.mock` |
| `pytest-cov` | Coverage reporting |
| `pytest-qt` | `qtbot` fixture for widgets, signals and threads |
| `Pillow` | Generate synthetic test images in-memory (no numpy required) |

---

## Running the tests

Bazel is the standard test runner. All test commands below assume the virtualenv is active (`source .venv/bin/activate`).

```bash
# Core/unit-oriented suite
bazel test //tests:unit_tests --test_output=errors

# GUI-oriented suite (pytest-qt in headless/offscreen mode under Bazel)
bazel test //tests:gui_tests --test_output=errors

# Combined suite used for broad regression checks
bazel test //tests:coverage_tests --test_output=errors

# Single test file
bazel test //tests:test_config --test_output=errors
```

The built-in reader's model tests (`//tests:test_ppocr_models`, the multi-page PDF scenario) run the real, pinned model files. Bazel fetches them once (31 MB, `http_file` in `MODULE.bazel`) and caches them; under plain pytest, where the files are not provided, those tests skip. Every other test fakes the reader (patch `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor`) and points the model store at an empty temporary folder, so no test downloads anything. The page models of the automatic image preparation work the same way (`//tests:test_page_models`, marker `needs_page_models`, 39 MB; `conftest.page_models_store()` links the fetched files, `fake_page_models_store()` makes empty files of the pinned sizes).

You can also invoke pytest directly (without Bazel) for quick ad-hoc runs, but Bazel is preferred in CI and for coverage because it applies the correct sandbox and dependency isolation.

Bazel gives every test file its own process, which hides a test that changes global state (`sys.modules`, `sys.path`, environment) and leaves it changed. A single `pytest tests` run puts all files in one process and is how such a leak between files shows up. A test that evicts or replaces modules must restore them (`with patch.dict(sys.modules):`).

### Coverage report

```bash
# Bazel coverage across unit + GUI suites (preferred)
bazel coverage //tests:coverage_tests \
    --instrumentation_filter="//teachers_teammate" \
    --combined_report=lcov

# Optional HTML report (requires lcov/genhtml)
genhtml --ignore-errors unmapped bazel-out/_coverage/_coverage_report.dat -o coverage_html/
```

---

## Writing a test

- **Docstring:** every test has a `Given … / When … / Then …` docstring (see below).
- **Config:** build one with `make_config(tmp_path, **overrides)` from `tests/conftest.py` (a plain
  function, not a fixture). Fixtures for input files: `sample_png`, `sample_pdf`.
- **Patch at the import site**, not at the origin — for example
  `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor`, which the stage builder
  constructs. `make_config` defaults to the built-in reader, and `conftest.py` points the model store
  at an empty temporary folder, so nothing downloads.
- **Ollama:** a test of `OllamaClient`'s own handling of the library patches it with
  `patch.dict(sys.modules, {"ollama": mock_ollama})`. A test that needs a *server* (real client,
  threads, pull stream) uses `tests/fakes/fake_ollama.py:FakeOllama`, the one Ollama stand-in; teach
  it a new switch rather than writing a second fake. `tests/unit/test_fake_ollama.py` pins its
  answers to Ollama's own source.
- **Optional packages:** under Bazel only `langchain-core` is installed. A test never relies on a
  provider package: mock the import (`patch.dict(sys.modules, {...})`), and simulate a missing one
  with `{"langchain_anthropic": None}` — `create()` then raises `RuntimeError` ("is not installed").
- **GUI:** use pytest-qt (`qtbot`) for widgets, signals and threads.

## Test dependencies and Bazel's cache

`//teachers_teammate` is one Bazel library per layer (`pkg_lib`, `domain_lib`, `core_lib`,
`texts_lib`, `infrastructure_lib`, `application_lib`, `cli_lib`, `gui_layer_lib`), each with the
layers it imports as `deps`. A test lists the **highest layer it imports** and nothing more
(`_DOMAIN_LIB`, `_INFRASTRUCTURE_LIB`, … in `tests/BUILD`; the lower layers come with it), so
Bazel reruns it only when that layer or one below it changes. Editing a `gui/` file reruns the
GUI tests; editing `domain/` reruns nearly everything, which is correct.

- Never give a test `//teachers_teammate:ocr_lib` or `:gui_lib`: they hold the whole package and
  every test would rerun on every edit. They are for the wheel, the docs and the lint targets.
- Count lazy imports and `patch("teachers_teammate.…")` strings too: the test imports that module.
- `tests/unit/test_layer_imports.py` fails on an import that points up a layer (domain →
  infrastructure, application → gui, …). A new module goes into the matching `_*_SRCS` list of
  `teachers_teammate/BUILD`; a new layer needs a library there and a row in that test.

---

## Test markers

| Marker | Meaning |
|---|---|
| `needs_reader_models` | Runs the built-in reader's real model files (Bazel fetches them; skipped without) |
| `needs_page_models` | Runs the page models' real model files (Bazel fetches them; skipped without) |
| `needs_ollama` | Requires a running Ollama instance |
| `gui` | Exercises PySide6 widgets and the event loop |
| `use_case` | Links the test to a use case in `use_cases.trlc` (see below) |

Run only tests that do not require Ollama (direct pytest, ad-hoc use only):

```bash
pytest tests/ -m "not needs_ollama" -v
```

---


## Test case specification — Given-When-Then

Every test function documents its preconditions, action, and expected outcome as a
**Given / When / Then** docstring following the
[Gherkin convention](https://en.wikipedia.org/wiki/Given-When-Then):

```
Given  <the preconditions and starting state>
When   <the action or call under test>
Then   <the expected outcome>
```

This keeps test intent readable as plain prose and makes each test self-describing
without requiring knowledge of the implementation.

Tests that cover a formal use case are tagged with `@pytest.mark.use_case("UC_NAME")`.
A generated catalogue linking each use case to its tagged test scenarios is available in
{doc}`Test Specifications by Use Case <test_specs>`.
