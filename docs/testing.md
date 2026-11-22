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

Bazel is the standard test runner and brings its own toolchain; it needs no virtualenv. A venv (`source .venv/bin/activate`) is only for direct pytest runs.

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

`infrastructure_lib` depends on `texts_lib`, because infrastructure words the failures and
warnings it reports. So a change to any `texts/` module reruns the infrastructure, application and
GUI tests as well as the CLI's; wording changes rarely enough that one `texts_lib` stays simpler
than a second library for the modules infrastructure imports.

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

Two test files also read the private evaluation repository (the machine registry and the converter's
acceptance tests). They are skipped unless the repository is named, so by default nothing runs them:

```bash
bazel test //tests:test_evaluation_machines //tests:test_evaluation_convert_v1 --test_output=errors \
  --test_env=GEC_EVALS=$HOME/git/teachers-teammate-evals --test_env=GEC_RESULTS=$HOME/gec-testset
```

Run only tests that do not require Ollama (direct pytest, ad-hoc use only):

```bash
pytest tests/ -m "not needs_ollama" -v
```

---

## The app walkthrough

`tests/e2e/walkthrough.py` plays teacher in the real main window: it writes an `ocr.toml` and
three input files into one temporary folder, builds the window the app builds, and then clicks,
types and answers exactly what a teacher does. It covers the settings file, choosing the input
folder, a run over a PNG, a scanned PDF and a TXT file, opening a document in the Check tab,
correcting it, exporting one DOCX, a second run that hits the cache, both clicks of the Stop
button, closing and reopening the window, every other window behind the settings, and closing
down. Each step checks what a teacher would see — the rows of the queue, the stored texts, the
Word files on disk, the summary of every run — and the first failure ends the walk with a message
that names the step and what it waited for.

Guards replace the teacher's eyes: an unexpected dialog is declined and recorded, a window a
step did not open is closed and recorded with its title and its own sentences (a bundle that
lost a model file says so in the result), an exception in a slot is recorded and fails its step,
and warnings are collected. A wait that runs out names what the guards caught, so a broken
bundle says why it broke, and a watchdog ends a hung walk with a result instead of running into
the CI job's limit.
Every guard lives in the script, so a bundle run reports the same way a test run does.

The walk writes one JSON file (steps, dialogs, modals, exceptions, warnings, screenshots,
platform and versions) and exits 0 only when every step passed. It writes that file on failure
too, because a windowed build has no console.

### From source

```bash
bazel test //tests:test_gui_main_walkthrough --test_output=errors
```

The test links the real reader and page models into one root via `models_store(tmp_path)` and
runs `walkthrough.main`, the same entry point the bundle runner calls, on the three documents.
It also replaces one thing per test and checks the report: a reader that raises, an unexpected
dialog, an unexpected window, a raising slot, a saved config, and that no thread is left running.
The target is `large` (about three and a half minutes on a fast machine).

### Against a bundle

The app runs the script itself: `--_run_script` takes the file, so a downloaded bundle runs the
script from the same commit as the bundle.

```bash
gh run download <run-id> -n teachers-teammate-linux    # then unpack
./teachers-teammate --_run_script tests/e2e/walkthrough.py e2e.json --screenshots shots
```

An older bundle that lacks a handle a newer script uses fails the first step, naming the handle.
A bundle is a working one only if the walk exits 0: a missing Qt platform plugin aborts the
process before the script's first line, so there is no `e2e.json` to read.

On a Wayland desktop the window opens on the `wayland` platform, even under `xvfb-run`:
`WAYLAND_DISPLAY` is what Qt follows. `QT_QPA_PLATFORM=xcb` puts it on the display `DISPLAY`
names instead; the Qt xcb plugin needs `libxcb-cursor0` and the other `libxcb-*` packages that
`.github/workflows/build_artifacts.yml` installs; the `.deb` depends on them and a bare checkout
does not have them.

### Adding a step

One method on `Walk`, one row in `STEPS` (`tests/e2e/walkthrough.py`). The step clicks what a
teacher clicks through `_handles`, and declares the dialogs it expects with `self._answers.expect`
before it triggers them — any other dialog fails the step. Waits are conditions with a deadline
(`_settle`), never sleeps. New settings go into `write_settings`; new input files into
`write_inputs`.

### What the script may import

`tests/unit/test_walkthrough_imports.py` holds the list: the standard library, Qt, Pillow,
python-docx and `teachers_teammate`, and nothing from `tests`. The bundle carries no test
framework, so a test-only helper in the script breaks the bundle run; teach the script instead.

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
