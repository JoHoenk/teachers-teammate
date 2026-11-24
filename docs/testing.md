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

The suites take their tests from a tag rather than a list: `unit_tests`/`gui_tests`/
`integration_tests`/`tools_tests` are one `test_suite` per tag, and `app_test()` (`tests/defs.bzl`)
gives every test the tag of the folder its file lives in. A target is one call that names the test
file; the macro derives the rest (name, tag, entry point). `tests/unit/test_test_targets.py` holds
the targets to the test files.

`platform_tests.yml` runs the tests marked `platform` in a plain venv on Windows and macOS: the
build venv's own recipe with the `[test]` extra, so the versions are the ones the bundle ships
there. They cover the code that differs per system (paths, processes, Ollama, locale); the list
is `PLATFORM_TEST_FILES` in `tests/conftest.py`. A test in it uses `Path`, `tmp_path` and
`os.sep`, never a literal POSIX path or a POSIX-only call. It blocks no merge.

Bazel gives every test file its own process, which hides a test that changes global state (`sys.modules`, `sys.path`, environment) and leaves it changed. A single `pytest tests` run puts all files in one process and is how such a leak between files shows up. A test that evicts or replaces modules must restore them (`with patch.dict(sys.modules):`).

### Test groups in CI

`tests.yml` runs `//tests/...` on one machine per group, each with its own disk cache
(`tests-<group>`). A test is in the first group that claims it:

| Group | Tests that |
|---|---|
| `walkthrough` | carry the tag `walkthrough` (`//tests:walkthrough_tests`): the whole app, end to end |
| `gui` | depend on `gui_layer_lib` |
| `application` | depend on `application_lib` and not on the GUI |
| `rest` | are not claimed above |

`tools/ci/target_groups.py` makes the split with `bazel query`; `CLAIMS` in it is the one place
that names the groups, and the workflow takes its matrix from `--matrix`. Only `walkthrough` and
`gui` import Qt, so only their machines install its system library (`libegl1`). `rest` is the
complement of the others, so a new test cannot be left out. The script stops if a group is
empty (a lost tag, a layer library no test depends on). A test that plays the whole app gets
`tags = ["walkthrough"]` in `tests/BUILD`. List or run one group:

```bash
python tools/ci/target_groups.py gui
bazel test --target_pattern_file=<(python tools/ci/target_groups.py application) --test_output=errors
bazel test //tests:walkthrough_tests --test_output=errors
```

### Coverage report

```bash
# Bazel coverage across the unit, GUI and tools suites (preferred)
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
| `platform` | Covers code that differs per operating system; runs on Linux, Windows and macOS |
| `needs_bazel` | Reads Bazel's runfiles (the built wheel, the model files); skipped unless `TEST_SRCDIR` is set |
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
and warnings and Qt's own messages are collected. A wait that runs out names what the guards
caught, so a broken bundle says why it broke, and a watchdog ends a hung walk with a result
instead of running into the CI job's limit: the result then carries every thread's stack and what
the app was running (`hang`).
Every guard lives in the script, so a bundle run reports the same way a test run does.

A run is over only when the summary has arrived and the window has taken it in (Run enabled, no
worker running); a stop is clicked at a fixed point of the worker, not on a timer. The words
the reader made of the drawn pages are compared with a slack of a character in eight, because the
reader's runtime picks its kernels by CPU; what the walk types and the text file hold are compared
exactly, and `readings` in the result shows what the reader made of the pages.

The walk writes one JSON file (steps, dialogs, modals, exceptions, warnings, Qt messages, readings,
screenshots, platform and versions) and exits 0 only when every step passed. It writes that file
on failure too, because a windowed build has no console.

### From source

```bash
bazel test //tests:test_gui_main_walkthrough --test_output=errors
```

The test links the real reader and page models into one root via `models_store(tmp_path)` and
runs `walkthrough.main`, the same entry point the bundle runner calls, on the three documents.
Every walk is a process of its own (`tests/e2e/walk_checked.py`): a walk leaves windows and
queued signals behind, and a hung walk ends only its own process. One thing is broken per test and
the report is checked: a reader that raises, an unexpected dialog, an unexpected window, a raising
slot, a reader that never returns (the watchdog) and a saved config; the plain walk also checks
that no thread is left running. The target is `large` (about a minute).

One walk is the app with proofreading on (`walkthrough.main(..., proofreading=...)`): the settings
file turns it on and names a model on `127.0.0.1`, and `FakeOllama` answers the exchange's three
steps (`ProofreadingAnswers`, which replaces the last word of every text). The walk then reads the items
the model left in the Check tab, ticks them as checked, finds the model's word in the Word file a
run writes, and spoils a stored review so that the app has to drop it. That last step is what
`application/merged_view.py` does for any stored document: a review that no longer quotes the
words it points at is dropped with a logged warning, and the walk requires that warning and the
changes without explanations.

The bundle walk never has proofreading on: the stand-in is test code, and the import list below
keeps it out of the script.

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

The walk makes its own temporary folder and removes it at the end. `--root <dir>` runs it in a
folder of the caller's instead, and `--keep` leaves that folder as the walk left it — the settings
file, the documents, the stored results, the add-on folder and the log, which is what a failed
walk or an upgrade is looked into with. Without `--keep` the walk removes only a folder it made
itself; one that exists already, such as the data root an earlier version left, is never removed.

On a Wayland desktop the window opens on the `wayland` platform, even under `xvfb-run`:
`WAYLAND_DISPLAY` is what Qt follows. `QT_QPA_PLATFORM=xcb` puts it on the display `DISPLAY`
names instead; the Qt xcb plugin needs `libxcb-cursor0` and the other `libxcb-*` packages that
`.github/workflows/build_package.yml` installs; the `.deb` depends on them and a bare checkout
does not have them.

### On each system

Three steps are about one system's behaviour: `PLATFORM_STEPS` names the systems each one runs on
and the step it follows, and `walk_steps(proofreading=…, platform=…)` inserts them.

- `export-held-open` (Windows, after `export`): a Word file is held open with no sharing at all,
  which is stricter than the read sharing Word allows, while the queue is exported over it — one
  notice naming the source, the other two Word files written, the held file untouched.
- `long-output-path` (Windows and Linux, after `run`): a results folder whose path is over 260
  characters, and the run's Word file still lands in it. Linux has no such limit, so the folder is
  only long there: the same work, on the walk the Linux tests run. On Windows the step records
  `LongPathsEnabled` under `readings` and fails when that switch is off, because the app adds no
  long-path prefix of its own and the machine would be what makes the run work.
- `case-spelling` (macOS and Windows, after `restart`): the same three documents reached through
  the folder's name in capitals — one cache entry per document (`state_path_for_input`), the texts
  the store already holds, and a second run that recognizes nothing. `document_stem` keys a
  document by the spelling the volume stores, which is what `Path.resolve` gives on Windows and
  what `F_GETPATH` gives on macOS.

Only a file system that ignores case makes the last one worth walking. The Windows and macOS steps
run on a bundle of that system, in the builds CI makes.

### In CI

Every bundle build runs the walkthrough on the build machine — on `xvfb-run` on Linux, in the
app's own window on Windows and macOS — and every install job runs it on the installed copy:
the `.deb` in the ubuntu:24.04, ubuntu:26.04 and debian:13 containers, the `.app` on macOS 15 and
26, the setup on Windows Server 2022 and 2025. This is the only place a platform's own plugin,
fonts and window server meet the app.

Each container walks twice when the bundle carries the Wayland platform plugin: once on
`xvfb`'s X server and once under `weston --backend=headless`, and the second result must say
`platform: wayland`, because Qt falls back to another plugin without a word. A bundle without
that plugin logs that the Wayland walk is skipped.

Each job uploads `e2e.json` and the screenshots even when the step failed
(`<prefix>-<platform>-e2e` for a build machine, `<prefix>-<platform>-installed-e2e-<image or os>`
for an install, where a container uploads its Wayland walk as `e2e-wayland.json` beside the xcb
one and the compositor's `weston.log` beside both): the file says which step broke and what the
guards caught. Every one of those steps has a time limit, and the script's own watchdog — armed
before the app services are built — ends a hung app with a result before that limit is reached.

The build job walks after it has uploaded the packages: a failed walk never costs a platform its
installers, and nothing the app writes while it runs can land in one of them.

### Adding a step

One method on `Walk`, one row in `STEPS` (`tests/e2e/walkthrough.py`) — or in `PROOFREADING_STEPS`
with the step it follows, for one that needs a model behind it: those run only when the walk is
given a `ProofreadingSetup`; or in `PLATFORM_STEPS` with the step it follows and the systems it
belongs to. The step clicks what a teacher clicks through `_handles`, and declares the dialogs it
expects with `self._answers.expect` before it triggers them — any other dialog fails the step. A
menu or a window the step opens itself goes through `_in_modal`. Waits are conditions
with a deadline (`_settle`), never sleeps; a run is waited for with `_wait_run`. New settings go
into `write_settings`; new input files into `write_inputs`.

### What the script may import

`tests/unit/test_walkthrough_imports.py` holds the list, for this script and for the update
banner's: the standard library, Qt, Pillow, python-docx and `teachers_teammate`, and nothing from
`tests`. The bundle carries no test framework, so a test-only helper in a script breaks the bundle
run; teach the script instead.

---

## The update banner

`tests/e2e/update_banner.py` is the one check of the update check that is not a unit test: the
bundle's own `urllib` against the real feed, and the window that follows it. The unit tests cover
each piece on its own (`ReleaseService`, the banner's text, the answer on the GUI thread), and a
break between them looks exactly like "no update", so nothing a teacher reports would show it.

It claims to be version `0.0.1` first, which composition reads when the services are built, so a
published release is newer than this build. Then it writes an `ocr.toml` with the check on, builds
the window the app builds, waits up to 20 s for `UpdateBanner`, and reads what it says: the newest
tag at its page under the project's release prefix. It clicks the banner's close button, and a
second window built with the check off must send no request — the feed is counted for that.

Every bundle build runs it once per system, on `xvfb-run` on Linux, with the newest tag from
`tools/packaging/latest_release.py tag` as `--tag`, so the answer the app reads is held against
GitHub's own idea of the newest release. Run it by hand against a built bundle:

```bash
xvfb-run -a ./dist/teachers-teammate/teachers-teammate \
  --_run_script tests/e2e/update_banner.py banner.json \
  --tag "$(python tools/packaging/latest_release.py tag)"
```

GitHub allows 60 unauthenticated API requests per hour per address, and a hosted runner shares
its address, so the limit is used up there at times. A `403` or `429` with
`x-ratelimit-remaining: 0` is therefore not a failed check: it proves TLS and the request work,
which is what the check is for, so the script passes and writes "rate limited: TLS and the request
work, banner not checked" into its result and the job summary. To tell the two cases apart it asks
the feed directly before it builds a window — two requests per system per build. Every other
failure fails, and so does a `200` that shows no banner.

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
