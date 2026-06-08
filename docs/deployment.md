# Deployment

This page covers how Teacher's Teammate is built and released — both via CI and locally on a developer machine.

---

## CI pipeline

### Pull request checks (`pull_request_checks.yml`)

Runs on every push to `main`, on all pull requests targeting `main`, and on merge-queue checks.

| Job | What it does |
|-----|-------------|
| **lint** | ruff + pylint + ty type check |
| **test** | Full Bazel test suite |
| **check** | Merge gate — fails unless every job above succeeded. Mark this as the required status check in branch protection. |

### Documentation (`deploy_docs.yml`)

Builds the Sphinx site (`bazel build //docs:html`) on every push to `main` and on PRs touching `docs/`. Pushes to `main` deploy the result to GitHub Pages; PRs only build it as a check.

### Release dry run (`release_dry_run.yml`)

Manual trigger (`workflow_dispatch`) with a version string (e.g. `v1.0.0-rc1`).
Runs the full build matrix — lint, test, wheel, and all platform standalone builds — but **does not upload artifacts or publish a release**.
Use this to validate the build before cutting a real release.

### Release (`release.yml`)

Manual trigger with a version string (e.g. `v1.2.3`).

```
lint ──┐
       ├──► create-release (empty GitHub Release shell)
test ──┘         │
                 ├──► build-wheel       → wheel (.whl) attached to release
                 └──► build-standalone  → platform installers attached to release
```

The **build-standalone** job (`build_artifacts.yml`) runs in parallel on three GitHub-hosted runners:

| Runner | Steps | Output |
|--------|-------|--------|
| `ubuntu-latest` | `tools/build/build_standalone.py` → `tools/packaging/make_deb.sh` | `teachers-teammate-<ver>-linux`, `teachers-teammate_<ver>.deb` |
| `windows-latest` | `tools/build/build_standalone.py` → NSIS (`tools/packaging/windows.nsi`) | `teachers-teammate-<ver>-w64-setup.exe` |
| `macos-latest` | `tools/build/build_standalone.py` → `tools/packaging/make_dmg.sh` | `teachers-teammate-<ver>-macos.dmg` |

The **build-wheel** job stamps the version into `pyproject.toml` via `tools/release/stamp_versions.py`, then builds a pure-Python wheel with Bazel.

---

## Manual build (local)

All commands are run from the **repository root**.

### Prerequisites

- Python 3.12
- [Bazel](https://bazel.build/) (for wheel builds and tests)
- [PyInstaller](https://pyinstaller.org/) ≥ 6.0 (`pip install "pyinstaller>=6.0"`)

```bash
# Activate the project virtualenv
source .venv/bin/activate

# Install the package with provider extras
pip install -e ".[providers]"
pip install "pyinstaller>=6.0"
```

### Python wheel

```bash
# Stamp the version first (optional for local builds)
python tools/release/stamp_versions.py --version 1.2.3

# Build
bazel build //teachers_teammate:teachers_teammate_wheel

# Output
ls bazel-bin/teachers_teammate/*.whl
```

### Third-party license report

Before cutting a release, regenerate the license documentation and commit both
the per-package store and the GUI asset:

```bash
bash tools/release/update_licenses.sh
git add third_party_licenses/ teachers_teammate/assets/third_party_licenses.md
```

The script creates an isolated venv (`.venv-licenses/`) containing only the base
runtime dependencies (`pip install -e .`) plus `pip-licenses`, so dev/test
packages are never included. The enumerated set (base + transitive) matches
exactly what PyInstaller bundles into the standalone binary.

Outputs:

| Path | Purpose |
|------|---------|
| `third_party_licenses/packages.md` | Table of all runtime packages and their license types |
| `third_party_licenses/summary.md` | Aggregated count per license type |
| `third_party_licenses/licenses/<pkg>/` | Copied LICENSE / NOTICE files per package |
| `third_party_licenses/manual_review.md` | Packages where no license file was found in the wheel |
| `teachers_teammate/assets/third_party_licenses.md` | Single bundled file shown in **Help → Third-Party Licenses** |

The `third_party_licenses/` directory is not committed (listed in `.gitignore`).
Only `teachers_teammate/assets/third_party_licenses.md` should be committed.

### Standalone binary

`tools/build/build_standalone.py` is the primary build script.
It produces a single self-contained executable (Linux), a `.app` bundle (macOS), or an `onedir` folder (Windows).

```bash
python tools/build/build_standalone.py
```

Outputs:

| Platform | Path |
|----------|------|
| Linux | `dist/teachers-teammate` |
| Windows | `dist/teachers-teammate/teachers-teammate.exe` |
| macOS | `dist/teachers-teammate.app/` |

Convenience wrappers: `tools/build/build.sh` (Linux/macOS) and `tools/build/build.bat` (Windows).

### Platform installers

Run these **after** `build_standalone.py`.

**Linux — `.deb` package**

```bash
bash tools/packaging/make_deb.sh [version]
# e.g.  bash tools/packaging/make_deb.sh 1.2.3
# Output: dist/teachers-teammate_1.2.3_amd64.deb
```

**macOS — `.dmg` installer**

```bash
# Build the .app bundle (.icns is generated internally by build_standalone.py)
python tools/build/build_standalone.py

# Wrap into a .dmg
bash tools/packaging/make_dmg.sh [version]
# e.g.  bash tools/packaging/make_dmg.sh 1.2.3
# Output: dist/teachers-teammate-1.2.3-macos.dmg
```

**Windows — NSIS installer**

Requires [NSIS](https://nsis.sourceforge.io/) on `PATH`.
Run `tools/packaging/make_ico.py` first (or let `build_standalone.py` do it automatically on Windows).

```powershell
makensis /DAPP_VERSION="1.2.3" /DAPP_VERSION_WIN="1.2.3.0" tools/packaging/windows.nsi
# Output: dist/teachers-teammate-1.2.3-w64-setup.exe
```

---

## Tools overview

| File | Purpose |
|------|---------|
| `tools/build/build_standalone.py` | Primary cross-platform PyInstaller build (used by CI) |
| `tools/build/build.sh` / `tools/build/build.bat` | Thin wrappers around `build_standalone.py` |
| `tools/build/run_gui.py` | PyInstaller GUI entry point (pip-dispatch + addon injection) |
| `tools/build/run_cli.py` | PyInstaller CLI entry point |
| `tools/packaging/make_ico.py` | Generates `.ico` and welcome BMP for Windows |
| `tools/packaging/make_icns.sh` | Generates `.icns` for macOS |
| `tools/packaging/make_deb.sh` | Creates `.deb` package for Debian/Ubuntu |
| `tools/packaging/make_dmg.sh` | Creates `.dmg` installer for macOS |
| `tools/packaging/windows.nsi` | NSIS script for the Windows installer |
| `tools/release/stamp_versions.py` | Stamps version into `pyproject.toml` and Bazel `BUILD` |
| `tools/release/update_licenses.py` | Generates third-party license report and GUI asset |
| `tools/release/update_licenses.sh` | Creates isolated venv and runs `update_licenses.py` |
