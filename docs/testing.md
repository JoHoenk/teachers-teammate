# Testing Guide

How to run the tests, write them and measure coverage. Lint and format commands are in
{doc}`Development <development>`.

## Test stack

| Package | Role |
|---|---|
| `pytest` | runner |
| `pytest-mock` | `mocker` fixture |
| `pytest-cov` | coverage |
| `pytest-qt` | `qtbot` fixture for widgets, signals and threads |
| `Pillow` | synthetic test images in memory |

---

## Running the tests

Bazel brings its own toolchain and isolates every test; use it in CI and for coverage. A venv is
only for ad-hoc `pytest` runs.

```bash
bazel test //tests:unit_tests --test_output=errors       # core and unit tests
bazel test //tests:gui_tests --test_output=errors        # pytest-qt, offscreen
bazel test //tests:coverage_tests --test_output=errors   # unit + GUI + tools
bazel test //tests:test_config --test_output=errors      # one test file
pytest tests/ -m "not needs_ollama"                      # ad hoc, without Bazel
```

**Targets.** `app_test()` (`tests/defs.bzl`) makes one target per test file and tags it with its
folder; `unit_tests`, `gui_tests`, `integration_tests` and `tools_tests` are one `test_suite` per
tag. `tests/unit/test_test_targets.py` keeps targets and files in step.

**Real model files.** The built-in reader's tests (`//tests:test_ppocr_models`, the multi-page PDF
scenario) and the page models' tests (`//tests:test_page_models`) run the real, pinned files, which
Bazel fetches once (`http_file` in `MODULE.bazel`). Under plain pytest they skip. Every other test
fakes them: patch `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor`;
`fake_page_models_store()` makes empty files of the pinned sizes; `conftest.py` points the model
store at an empty temporary folder, so nothing downloads.

**Leaks between files.** Bazel runs each file in its own process, which hides a test that leaves
`sys.modules`, `sys.path` or the environment changed. A single `pytest tests` run shows such leaks.
A test that replaces modules restores them (`with patch.dict(sys.modules):`).

**Platform tests.** `platform_tests.yml` runs the tests marked `platform` (`PLATFORM_TEST_FILES` in
`tests/conftest.py`) in a plain venv on Windows and macOS, with the versions the bundle ships. They
cover paths, processes, Ollama and locale. Use `Path`, `tmp_path` and `os.sep`, never a POSIX-only
path or call. They block no merge.

### Test groups in CI

`tests.yml` runs `//tests/...` on one machine per group, each with its own disk cache. A test is in
the first group that claims it:

| Group | Tests that |
|---|---|
| `walkthrough` | carry the tag `walkthrough`: the whole app, end to end |
| `gui` | depend on `gui_layer_lib` |
| `application` | depend on `application_lib`, not on the GUI |
| `rest` | everything else |

`tools/ci/target_groups.py` makes the split with `bazel query`; `CLAIMS` names the groups and
`--matrix` feeds the workflow. Only `walkthrough` and `gui` install Qt's system library. The script
fails on an empty group.

```bash
python tools/ci/target_groups.py gui
bazel test --target_pattern_file=<(python tools/ci/target_groups.py application) --test_output=errors
bazel test //tests:walkthrough_tests --test_output=errors
```

### Coverage

```bash
bazel coverage //tests:coverage_tests \
    --instrumentation_filter="//teachers_teammate" \
    --combined_report=lcov
genhtml --ignore-errors unmapped bazel-out/_coverage/_coverage_report.dat -o coverage_html/
```

---

## Writing a test

- **Docstring:** `Given … / When … / Then …` (see the end of this page).
- **Config:** `make_config(tmp_path, **overrides)` from `tests/conftest.py` (a plain function).
  Input fixtures: `sample_png`, `sample_pdf`.
- **Patch at the import site**, e.g. `teachers_teammate.infrastructure.stage_builder.PpOcrProcessor`.
  `make_config` defaults to the built-in reader.
- **Ollama:** a test that needs a server (real client, threads, pull stream) uses
  `tests/fakes/fake_ollama.py:FakeOllama`; teach it a new switch rather than writing a second fake.
  `tests/unit/test_fake_ollama.py` pins its answers to Ollama's source. Only a test of
  `OllamaClient`'s own library handling patches `sys.modules["ollama"]`.
- **Optional packages:** under Bazel only `langchain-core` is installed. Mock provider imports with
  `patch.dict(sys.modules, {...})`; `{"langchain_anthropic": None}` simulates a missing one and
  `create()` raises `RuntimeError` ("is not installed").
- **GUI:** pytest-qt (`qtbot`).

## Test dependencies and Bazel's cache

`//teachers_teammate` has one library per layer (`pkg_lib`, `domain_lib`, `core_lib`, `texts_lib`,
`infrastructure_lib`, `application_lib`, `cli_lib`, `gui_layer_lib`), each depending on the layers
it imports. A test lists only the **highest layer it imports** (`_DOMAIN_LIB`,
`_INFRASTRUCTURE_LIB`, … in `tests/BUILD`), so it reruns only when that layer or a lower one
changes.

- Never use `//teachers_teammate:ocr_lib` or `:gui_lib` in a test: they hold the whole package and
  are for the wheel, the docs and lint.
- Lazy imports and `patch("teachers_teammate.…")` strings count as imports.
- `infrastructure_lib` depends on `texts_lib` (it words its failures), so a `texts/` change reruns
  the infrastructure, application, GUI and CLI tests.
- `tests/unit/test_layer_imports.py` fails on an import up a layer. A new module goes into its
  layer's `_*_SRCS` list in `teachers_teammate/BUILD`; a new layer needs a library there and a row
  in that test.

---

## Test markers

| Marker | Meaning |
|---|---|
| `needs_reader_models` | runs the built-in reader's real model files (skipped without them) |
| `needs_page_models` | runs the page models' real files (skipped without them) |
| `needs_ollama` | needs a running Ollama |
| `gui` | uses PySide6 widgets and the event loop |
| `platform` | code that differs per operating system; runs on Linux, Windows and macOS |
| `needs_bazel` | reads Bazel's runfiles; skipped unless `TEST_SRCDIR` is set |
| `use_case` | links the test to a use case in `use_cases.trlc` |

Two tests read the private evaluation repository and skip unless it is named:

```bash
bazel test //tests:test_evaluation_machines //tests:test_evaluation_convert_v1 --test_output=errors \
  --test_env=GEC_EVALS=$HOME/git/teachers-teammate-evals --test_env=GEC_RESULTS=$HOME/gec-testset
```

---

## The app walkthrough

`tests/e2e/walkthrough.py` plays a teacher in the real main window. It writes an `ocr.toml` and
three input files (PNG, scanned PDF, TXT), builds the window the app builds, and clicks through:
choosing the folder, a run, the Check tab, a correction, a DOCX export, a cached second run, both
Stop clicks, closing and reopening, every settings window, and shutdown. Each step checks what a
teacher would see (queue rows, stored texts, Word files, run summaries); the first failure ends the
walk, naming the step and what it waited for.

**Guards.** An unexpected dialog is declined and recorded; an unexpected window is closed and
recorded with its title and text; an exception in a slot fails its step; warnings and Qt messages
are collected. A watchdog ends a hung walk with a result holding every thread's stack (`hang`)
before the CI job's limit. All guards live in the script, so a bundle reports like a test run.

**Waiting.** A run is over when the summary has arrived and the window has taken it in; Stop is
clicked at a fixed point of the worker, not on a timer. Text read from the drawn pages is compared
with a slack of one character in eight (the reader picks CPU kernels per machine); typed text and
the text file are compared exactly.

**Result.** One JSON file (steps, dialogs, exceptions, warnings, Qt messages, readings, screenshots,
platform, versions), written on failure too. Exit 0 only when every step passed.

### From source

```bash
bazel test //tests:test_gui_main_walkthrough --test_output=errors
```

Links the real models via `models_store(tmp_path)` and calls `walkthrough.main`, the bundle
runner's entry point. Each walk runs in its own process (`tests/e2e/walk_checked.py`). Further
tests break one thing each and check the report: a raising reader, an unexpected dialog or window,
a raising slot, a reader that never returns, a saved config; the plain walk also checks that no
thread is left running. The target is `large`.

One walk runs with proofreading on: the settings name a model on `127.0.0.1` and `FakeOllama`
answers the exchange (`ProofreadingAnswers`). The walk reads and ticks the items in the Check tab,
finds the model's word in the Word file, and spoils a stored review, which the app must drop with a
logged warning (`application/merged_view.py`). The bundle walk never has proofreading on.

### Against a bundle

`--_run_script` runs the script inside the app, so a bundle runs the script from its own commit:

```bash
gh run download <run-id> -n teachers-teammate-linux    # then unpack
./teachers-teammate --_run_script tests/e2e/walkthrough.py e2e.json --screenshots shots
```

A bundle works only if the walk exits 0; a missing Qt platform plugin aborts before the script
starts, leaving no `e2e.json`. On a Wayland desktop Qt follows `WAYLAND_DISPLAY`, even under
`xvfb-run`; `QT_QPA_PLATFORM=xcb` uses `DISPLAY` instead and needs the `libxcb-*` packages that
`build_package.yml` installs.

The walk makes its own temporary folder and removes it at the end. `--root <dir>` runs it in a
folder of yours instead, and `--keep` leaves that folder as the walk left it (settings file,
documents, stored results, add-on folder, log), which is what a failed walk or an upgrade is looked
into with. Without `--keep` the walk removes only a folder it made itself; one that exists already,
such as the data root an earlier version left, is never removed.

### Over the data an earlier version left

`--after-upgrade <root>` checks the folder an earlier version left instead of playing teacher: it
reads the settings file that version wrote and fails on a notice this version gives about it,
builds the real window on the folder and fails if the app copies the file aside as broken, queues
the documents the old run listed, and runs them. It fails when a stored text or a teacher's
correction is gone, when the log holds an `ERROR` line or a traceback, or when the settings file
lost a value it held. A stored result comes back unless `READER_VERSION` or `AUTO_VERSION` asks for
a new reading; a review that no longer fits is dropped with a warning, as everywhere else.

The add-on folder is the one the old version filled: a folder the update made outdated shows
`ADDONS_OUTDATED` in readiness, and the walk clicks *Update add-ons* on the Settings window's
Add-ons page and requires the banner to go. That click installs from the network, so this walk
needs one. `--root` is refused beside `--after-upgrade`: the folder is the old one's, and the walk
never removes one that was there before.

### Platform steps

`PLATFORM_STEPS` adds steps for one system's behaviour, each after a given step:

- `export-held-open` (Windows): a Word file held open without sharing while the queue is exported
  over it — one notice, the other files written, the held file untouched.
- `long-output-path` (Windows, Linux): a results folder path over 260 characters. On Windows the
  step records `LongPathsEnabled` and fails when it is off, since the app adds no long-path prefix.
- `case-spelling` (macOS, Windows): the documents reached through the folder name in capitals —
  one cache entry per document and a second run that recognizes nothing (`document_stem` keys by
  the spelling the volume stores).

### In CI

Every bundle build runs the walk on the build machine (`xvfb-run` on Linux), and every install job
on the installed copy: the `.deb` in ubuntu:24.04, ubuntu:26.04 and debian:13, the `.app` on macOS
15 and 26, the setup on Windows Server 2022 and 2025. Containers walk a second time under
`weston --backend=headless` and require `platform: wayland` in the result.

The three install jobs walk once more, over the data the release they updated from left: that
version's settings file (no `check_for_updates` among its keys) and an add-on folder its own pip
filled, so the update finds it outdated. The install tests write both before they install the new
package and walk after the update, through the installed copy and before the uninstaller removes
the data.

Each job uploads `e2e.json` and screenshots even on failure (`<prefix>-<platform>-e2e`, or
`…-installed-e2e-<image or os>`; plus `e2e-wayland.json` and `weston.log` in containers, and
`e2e-upgrade.json` with `e2e-upgrade-shots/` for the walk over the old data). The build job walks
after uploading the packages, so a failed walk never costs a platform its installers.

### Adding a step

One method on `Walk` and one row in `STEPS` (`tests/e2e/walkthrough.py`), or in `PROOFREADING_STEPS`
(needs a model behind it) or `PLATFORM_STEPS`, each with the step it follows; the steps of the walk
over an earlier version's data are `UPGRADE_STEPS`.

- Click through `_handles`; declare expected dialogs with `self._answers.expect` before triggering
  them — any other dialog fails the step.
- Open menus and windows through `_in_modal`.
- Wait with `_settle` (a condition with a deadline) or `_wait_run`, never sleep.
- New settings go into `write_settings`, new input files into `write_inputs`.

**Imports.** The bundle has no test framework: the walkthrough and the update banner's script may
import only the standard library, Qt, Pillow, python-docx and `teachers_teammate`
(`tests/unit/test_walkthrough_imports.py`).

---

## The update banner

`tests/e2e/update_banner.py` is the one check of the update check that is not a unit test: the
bundle's own `urllib` against the real feed, and the window that follows it. The unit tests cover
each piece on its own, and a break between them looks exactly like "no update".

It claims to be version `0.0.1`, so a published release is newer, writes an `ocr.toml` with the
check on, builds the window, waits up to 20 s for `UpdateBanner` and reads what it says: the newest
tag under the project's release prefix. It clicks the close button, and a second window built with
the check off must send no request.

Every bundle build runs it once per system (`xvfb-run` on Linux) with the newest tag from
`tools/packaging/latest_release.py tag` as `--tag`. By hand, against a built bundle:

```bash
xvfb-run -a ./dist/teachers-teammate/teachers-teammate \
  --_run_script tests/e2e/update_banner.py banner.json \
  --tag "$(python tools/packaging/latest_release.py tag)"
```

GitHub allows 60 unauthenticated API requests per hour per address, and a hosted runner shares its
address. A `403` or `429` with `x-ratelimit-remaining: 0` is therefore not a failed check: TLS and
the request work, so the script passes and writes "rate limited: TLS and the request work, banner
not checked" into its result and the job summary. To tell the cases apart it asks the feed directly
before it builds a window (two requests per system per build). Every other failure fails, and so
does a `200` that shows no banner.

---

## Given-When-Then

Every test documents its preconditions, action and expected outcome
([Gherkin convention](https://en.wikipedia.org/wiki/Given-When-Then)):

```
Given  <the preconditions and starting state>
When   <the action or call under test>
Then   <the expected outcome>
```

A test that covers a formal use case is tagged `@pytest.mark.use_case("UC_NAME")`; the catalogue
is {doc}`Test Specifications by Use Case <test_specs>`.
