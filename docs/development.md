# Development

## Scope

This guide focuses on contributor workflows and codebase structure.
For runtime tuning and provider/model strategy, see the
{doc}`advanced user guide <advanced_user_guide>`.

## Prerequisites

- Python 3.12
- [Bazel](https://bazel.build/) — used as the build and test harness; all `bazel` commands below assume it is on `PATH`
- [uv](https://docs.astral.sh/uv/) (optional — speeds up venv creation)

### Set up the development environment

```bash
# Create a virtualenv and install all dependencies (including dev and test extras)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,providers]"
```

If you have `uv` installed, replace the two `pip` lines with `uv sync`.

## Development checks

### Linting

```bash
bazel test //:py_ruff
bazel test //:py_pylint
```

### Type checking

```bash
bazel test //:py_ty
```

### Formatting

```bash
# Project formatters (ruff + buildifier)
bazel run //:format.fix
bazel run //:format.check

# Direct buildifier command
bazel run //:buildifier.fix -- -r .
```

### Tests

```bash
bazel test //tests/...
```

## Documentation build

```bash
bazel build //docs:html
bazel run //docs:html.serve
```

## Dependency lock updates

Run these after adding, removing, or upgrading a dependency in `pyproject.toml` to regenerate the pinned lock files that Bazel uses:

```bash
bazel run //:requirements_dev.update
bazel run //:requirements_test.update
```


## Architecture

The codebase is split into four layers. Each layer may only depend on layers below it:

- **Presentation** (`gui/`, `cli.py`) — translates user interactions into application calls and renders results. Contains no business logic.
- **Application** (`application/service.py`, `application/commands.py`) — coordinates use cases; the only entry point for the GUI and CLI.
- **Domain** (`domain/`) — pure business concepts (stage names, freshness policy); no I/O.
- **Infrastructure** (`infrastructure/`) — concrete implementations: OCR engines, LLM providers, file I/O, DOCX builder, and the pipeline orchestration in `infrastructure/workflow/`.

```{uml} assets/component_diagram.puml
:align: center
:alt: Teacher's Teammate component architecture
:width: 100%
```

### Runtime sequence diagrams

The diagrams below show the three main runtime flows.

#### GUI initialisation

```{uml} assets/sequence_gui_init.puml
:align: center
:alt: GUI initialisation sequence
:width: 100%
```

#### Open input folder

```{uml} assets/sequence_open_folder.puml
:align: center
:alt: Open input folder sequence
:width: 100%
```

#### Run selected documents

```{uml} assets/sequence_run_documents.puml
:align: center
:alt: Run selected documents sequence
:width: 100%
```

### Benchmark app

A second GUI app, **Benchmark** (`teachers-teammate-benchmark`, entry point
`teachers_teammate.gui.benchmark:main_benchmark`), helps judge OCR quality for a
single document. It runs the document through an OCR configuration, stores the
result in an append-only history, and lets the user compare two stored runs
side-by-side (image · text A · text B · diff + similarity). It follows the same
layering:

- **Domain** `domain/benchmark.py` — pure pairwise comparison metrics (similarity
  via stdlib `difflib`, text statistics); no I/O.
- **Infrastructure** `infrastructure/benchmark/` — `BenchmarkRunStore` (the
  append-only run store, see below) and `runner.py` (an OCR-only path that reuses
  `StageBuilder`, `PreprocessService`, and `OCRStageService`).
- **Application** `application/benchmark_service.py` — `BenchmarkApplicationService`
  composes `ProcessingApplicationService` for OCR discovery queries and adds
  `run_and_store` / `list_runs` / `delete_run` / `compare`. The GUI talks only to
  this service.
- **Presentation** `gui/benchmark/` — a thin window reusing shared widgets
  (`_ocr_config_selector.OcrConfigSelector`, `_preview_panel` `ZoomableImageView`
  /`DiffWidget`, `_log_widget.LogWidget`, `_app_bootstrap.create_app`,
  `_qt_stream.QtStream`).

The OCR-only fields live in the nested `Config.ocr` value object
(`config.py:OcrConfig`), which both the pipeline and the benchmark vary.

```{uml} assets/sequence_benchmark_compare.puml
:align: center
:alt: Benchmark run and compare sequence
:width: 100%
```

## Storage, hashing & caching

All persistent data lives under a single **storage root**, resolved by
`infrastructure/storage_root.py:resolve_storage_root()` — the
`TEACHERS_TEAMMATE_TMPDIR` environment variable when set, otherwise the per-user
OS cache directory (`~/.cache/teachers_teammate` on Linux).

```
<storage_root>/
├── ocr.toml                                   # configuration (the pipeline config)
├── artifacts/
│   └── <output_dir_key>/                      # key = sha256(output_dir.resolve())[:16]
│       ├── <stem>_preprocessed.png            # preview image (one per source stem)
│       └── state/<stem>_<source_key>.json     # CACHE: one DocumentState per (output_dir, path)
└── benchmark/
    └── <document_hash>/                       # compute_file_hash(document)
        ├── document.json                      # {document_hash, display_name, last_path}
        ├── <UTC-ts>-<ocr_cfg_hash[:8]>.json   # StoredRun (self-describing)
        └── images/<UTC-ts>-<ocr_cfg_hash[:8]>.png
```

### Hashing scheme

- `compute_file_hash(path)` (`state_repository.py`) — SHA-256 of the file
  contents; the **identity of a document** regardless of its path or name.
- `compute_ocr_config_hash(config, ocr_prompt)` (`workflow/cache_service.py`) — a
  fingerprint of the OCR stage configuration (engine, provider, model, preprocess
  method, language, prompt, temperature). Used by the cache to detect stale
  stages and by the benchmark store to label runs.
- `compute_cache_key(value)` (`storage_root.py`) — a short (16-char) stable key
  used to map a path (output dir / source file) to an on-disk file name.

### Cache vs benchmark store

These two stores have deliberately opposite shapes:

| | Pipeline **cache** (`StateRepository`) | Benchmark **run store** (`BenchmarkRunStore`) |
|---|---|---|
| Purpose | memoize/resume the latest result | keep an experiment history to compare |
| Cardinality | one entry per `(output_dir, path)`, **overwritten** | **many** entries per document, **append-only** |
| Keyed by | file *path* (+ output dir) | document *content hash* (global) |
| Config awareness | stores only a *hash* to detect staleness | stores the full `OcrConfig` to label runs |
| Expiry | **none** — grows unbounded, never garbage-collected | keep-last-N cap + manual deletion |

> **Note:** the pipeline cache currently has **no automatic expiry**. Re-running
> the same file overwrites its single entry in place, so it does not grow per
> run, but nothing is ever evicted (e.g. deleting a source document leaves its
> state behind). Only intermediate images (`*_step*.jpg`, `*_page*.png`) are
> cleaned, and a preview image is removed when its state is invalidated by a
> config change.

### Benchmark run retention

Because the benchmark store is append-only, it bounds growth two ways:

- **Manual deletion** — delete a single run, or all runs for a document, from the
  GUI (`delete_run` / `delete_all_runs`).
- **Keep-last-N cap** — `BENCHMARK_KEEP_LAST_N` (default 20, in
  `infrastructure/benchmark/run_store.py`). On each save, runs for that document
  beyond the newest N are evicted (both the JSON and its preview image).

## Error handling

### Design goals

The codebase follows three principles for error handling:

#. **Typed, descriptive exceptions** — each failure mode has its own class so
   callers can catch exactly what they need without inspecting message strings.
#. **Hard failures propagate; soft failures return warnings** — unrecoverable
   problems (missing provider package, unreachable Ollama service, unreadable
   input) raise exceptions and abort the affected file.  Recoverable problems
   in optional stages (LLM correction, evaluation) are captured as warning
   strings and passed to the caller so processing can continue.
#. **Structured logging instead of bare `print`** — all diagnostic output uses
   `logging.warning` / `logging.error` so the GUI's log panel and the CLI's
   stderr stream both receive the same messages without any special-casing.

### Exception hierarchy

All custom exceptions live in `teachers_teammate/exceptions.py` and are
re-exported from the package root for convenience.

```{uml} assets/error_handling.puml
:align: center
:alt: Exception hierarchy and raising sites
:width: 100%
```

| Exception | Base | Raised by |
|---|---|---|
| `OCRError` | `Exception` | OCR processor implementations |
| `ConfigFileNotFoundError` | `FileNotFoundError` | `config.resolve_config_path` |
| `ConfigFileParseError` | `ValueError` | `config._load_config_section` |
| `StorageResolutionError` | `RuntimeError` | `storage_root.resolve_storage_root` |
| `ProviderNotAvailableError` | `RuntimeError` | provider modules, `ocr_processor`, `stage_builder` |
| `OllamaConnectionError` | `RuntimeError` | `ollama_utils.check_model` |
| `PipelineInputError` | `ValueError` | `input_provider_factory`, `preprocess_service` |

Inheriting from the appropriate built-in (`ValueError` for bad input,
`RuntimeError` for infrastructure failures, `FileNotFoundError` for missing
files) lets callers that do not import the custom class still fall through to a
sensible `except` branch.

### Soft failures and per-file warnings

The `Corrector.correct()` and `Evaluator.evaluate()` ABCs return
`tuple[str, str | None]` — the processed text plus an optional warning message.
Implementations never raise on LLM communication errors; they return
`(original_text, "Correction failed: …")` instead.  `FileProcessor` collects
all non-`None` warning strings into a `warnings: tuple[str, ...]` field on
`ProcessingResult` so the application layer can surface them to the user without
interrupting the batch run.

```python
# interfaces.py (simplified)
class Corrector(ABC):
    def correct(self, text: str, language: str) -> tuple[str, str | None]:
        """Return (corrected_text, warning_or_None)."""
        ...

class Evaluator(ABC):
    def evaluate(self, text: str, language: str) -> tuple[str, str | None]:
        """Return (evaluation_text, warning_or_None)."""
        ...
```

### Logging

Every module that emits diagnostic output creates a module-level logger:

```python
import logging
_logger = logging.getLogger(__name__)
```

The two entry points configure the root logger once:

- **CLI** (`cli.py:run_cli`): `logging.basicConfig(level=WARNING, stream=sys.stderr, …)`
- **GUI** (`gui/_main_window.py:main_gui`): same `basicConfig` call; the GUI
  worker additionally redirects `sys.stderr` to a Qt signal so `WARNING` and
  `ERROR` messages from any module reach the in-app log panel automatically.

The convention is:
- `_logger.warning(…)` — non-fatal, processing continues (e.g. DPI metadata
  could not be restored, a cleanup file could not be deleted).
- `_logger.error(…)` — file could not be processed but the batch continues.
- `print(f"ERROR: …")` — reserved for CLI user-facing status lines where the
  message is part of the normal output contract (not a log event).

### GUI error feedback

Long-running background fetches (model lists, spaCy model availability) use Qt
signals to report failures to the UI thread without blocking:

- `_ModelFetchThread` (in `_settings_dialog.py`) emits `error = Signal(str)` if
  the network call fails; the receiving slot sets a tooltip on the status label.
- `_SpacyModelFetchThread` (in `_anonymizer_config_dialog.py`) emits the same
  `error` signal; the slot makes a red status label visible and sets its text.

This prevents silent failures where the combobox simply stays empty and the user
has no indication of what went wrong.

## Downloads and installation

:::{note}
This section is only relevant if you are working on the in-app download or addon-installation feature. For day-to-day development you can skip it.
:::

This section explains how the in-app "Downloads & Packages" dialog installs packages and
models, and how those installations survive application restarts.

### Addon packages directory

Optional packages that are installed at runtime — spaCy, PaddleOCR, LLM provider connectors,
GPU monitoring libraries — all land in a **persistent user-writable directory** that is separate
from the application bundle or the development venv:

```
Windows:  %LOCALAPPDATA%\teachers_teammate\packages\
Linux:    ~/.local/share/teachers_teammate/packages/
macOS:    ~/Library/Application Support/teachers_teammate/packages/
```

The location is computed by `addon_manager.get_packages_dir()` using
`platformdirs.user_data_dir`. This directory is prepended to `sys.path` by
`inject_packages_dir()` so anything installed there is importable.

`inject_packages_dir()` is called:

- At application startup (`run_gui.py`, `gui/__main__.py`, `__main__.py`), so packages
  installed during a previous session are immediately available.
- After every successful install or model download, so the newly installed package is
  usable in the same session without a restart.

The function is idempotent — safe to call multiple times.

### pip package install (frozen builds)

The frozen Windows/macOS binary bundles pip via PyInstaller's `--collect-all pip`.  Because
`sys.executable` in a frozen build is the application `.exe`, running
`[sys.executable, "-m", "pip", ...]` re-enters the frozen entry script without a recognised
dispatch flag and launches the GUI again instead of pip.

To avoid this, every pip install in a frozen build re-invokes the exe with the custom flag
`--_pip_install_mode` followed by the normal pip arguments, plus `--target <packages_dir>`.
The entry script (`tools/build/run_gui.py`) intercepts this flag before any GUI code is loaded
and calls `pip._internal.cli.main()` directly:

```python
# tools/build/run_gui.py
if "--_pip_install_mode" in sys.argv:
    _pip_args = sys.argv[sys.argv.index("--_pip_install_mode") + 1:]
    from pip._internal.cli.main import main as _pip_main
    raise SystemExit(_pip_main(_pip_args))
```

`addon_manager.install_addon_subprocess()` uses this mechanism for the addon packages
(spaCy, PaddleOCR, GPU monitoring).  `_PipInstallThread` in `_downloads_dialog.py` also
uses it when `sys.frozen` is `True`.

In development (non-frozen) builds, both code paths fall back to the standard
`[sys.executable, "-m", "pip", "install", "--upgrade", ...]` without `--target`, so packages
land in the active venv as usual.

### spaCy library

spaCy is an optional dependency (the privacy/anonymization feature).  It is installed from
pip like any other package — via the Downloads dialog's spaCy tab or, for frozen builds, via
the dedicated "Install Privacy Addon" flow in `AddonInstallerDialog`.

### spaCy language models

spaCy NER models (e.g. `en_core_web_sm`) are distributed as Python wheels on
**GitHub Releases**, not on PyPI.  A plain `pip install en_core_web_sm` fails because PyPI
does not host them.

`addon_manager.download_spacy_model_subprocess()` handles the download in two steps:

1. **URL resolution** — `spacy.cli.download.get_compatibility()`, `get_version()`, and
   `get_model_filename()` compute the exact wheel URL for the installed spaCy version.
2. **Wheel install** — `pip install --target <packages_dir> --prefer-binary <url>` downloads
   and unpacks the wheel into the addon packages directory.

In frozen builds, URL resolution must happen inside the frozen Python interpreter
(which has spaCy bundled), so the exe is re-invoked with `--_spacy_download_mode <model>
--target <packages_dir>`:

```python
# tools/build/run_gui.py
if "--_spacy_download_mode" in sys.argv:
    _model = sys.argv[idx + 1]
    # resolve URL, then call pip._internal.cli.main(["install", "--target", ..., url])
```

After a model download, `inject_packages_dir()` ensures the model is immediately usable.
`spacy.load("en_core_web_sm")` finds the model because `packages_dir` is on `sys.path`.

### Ollama models

Ollama models are pulled via the **Ollama HTTP API**, not pip.  The Downloads dialog's
"Ollama Models" tab calls `ProcessingApplicationService.pull_ollama_model()`, which uses
the `ollama` Python client to stream a `POST /api/pull` request to the configured Ollama
server (default `http://127.0.0.1:11434`).

Ollama stores models in its own data directory (OS-managed, outside this application).
No `sys.path` manipulation is involved.  The Ollama server must be running before a pull
can start.

### Summary table

| What | How downloaded | Where stored | Required at startup? |
|---|---|---|---|
| pip packages (spaCy, PaddleOCR, …) | pip via `--_pip_install_mode` (frozen) or `-m pip` (source) | packages_dir (frozen) / venv (source) | `inject_packages_dir()` |
| spaCy language models | pip wheel from GitHub Releases | packages_dir | `inject_packages_dir()` |
| Ollama models | Ollama HTTP API (`/api/pull`) | Ollama data dir | Ollama server running |
| Tesseract | OS package manager (not in-app) | System PATH | Tesseract binary on PATH |
