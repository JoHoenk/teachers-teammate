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

### Windows test build (`build_windows.yml`)

Manual trigger with an optional version string (default `v0.0.0-test`).
Builds only the Windows NSIS installer — no lint/test gate, no release, no tag — and uploads it as a workflow-run artifact with 2-day retention. After a successful build, artifacts from earlier runs of this workflow are deleted, so only the newest installer is kept (a failed build leaves the previous one in place):

```bash
gh workflow run build_windows.yml -f version=v0.0.0-test
gh run watch
gh run download <run-id> -n teachers-teammate-windows
```

### Release (`release.yml`)

Manual trigger with a version string (e.g. `v1.2.3`).

```
lint ──┐
       ├──► create-release (draft GitHub Release)
test ──┘         │
                 ├──► build-wheel       → wheel (.whl) attached to the draft
                 └──► build-standalone  → platform installers attached to the draft
                                    │
                                    └──► publish-release → flips the draft to published
```

The release stays a draft until **publish-release** runs, so a half-uploaded release is never
visible. The upload steps pass `draft: true` too: without it the upload action publishes an
existing draft after its first upload. `create-release` sets `target_commitish` to the built
commit, because a draft's tag is only created when the release is published. A pre-release
version (one containing `-`) is published without `--latest`.

A failed run leaves its draft behind: delete it with `gh release delete <tag> --cleanup-tag`
before running the release again.

The **build-standalone** job (`build_artifacts.yml`) runs in parallel on three GitHub-hosted runners:

| Runner | Steps | Output |
|--------|-------|--------|
| `ubuntu-22.04` | pinned install → `tools/build/build_standalone.py` → smoke test → bundle checks → `tools/packaging/make_deb.sh` → container install test → `tools/packaging/make_tarball.sh` | `teachers-teammate_<ver>.deb`, `teachers-teammate-<ver>-linux-x86_64.tar.gz` |
| `windows-latest` | pinned install → `tools/build/build_standalone.py` → smoke test → NSIS (`tools/packaging/windows.nsi`) | `teachers-teammate-<ver>-w64-setup.exe` |
| `macos-latest` | pinned install → `tools/build/build_standalone.py` → smoke test → `tools/packaging/make_dmg.sh` | `teachers-teammate-<ver>-macos.dmg` |

The Linux job builds on `ubuntu-22.04` on purpose: PyInstaller bundles the libraries of the
machine it builds on, so a newer image would silently raise the minimum glibc. If GitHub drops
that image, build in an `ubuntu:22.04` container on `ubuntu-latest` rather than moving to a newer
runner.

Every job installs against `pyinstaller-work/constraints.txt` (see
[Standalone binary](#standalone-binary)); the Linux job also runs
`tools/build/check_pins.py --platform win_amd64` and `--platform macosx_14_0_arm64`, because pip
evaluates PyInstaller's Windows-only (`pefile`) and macOS-only (`macholib`) dependencies against the
Linux host and would never report them. Every job runs the app with `--_smoke_test smoke.json` and fails
on a non-zero exit, and uploads `smoke.json` as an artifact. `tools/release/check_inventory.py
--env` runs on all three, so the platform-only packages are covered too. The Linux job also runs
`tools/build/check_linux_bundle.py` twice: `--max-glibc 2.35` proves the bundle starts on Ubuntu
22.04, `--unused` proves it carries no library that nothing loads.

The **build-wheel** job stamps the version into `pyproject.toml` via `tools/release/stamp_versions.py`, then builds a pure-Python wheel with Bazel.

---

## Manual build (local)

All commands are run from the **repository root**.

### Prerequisites

- Python 3.12.16 (`.python-version`; the `python` row of the license list names the same patch version)
- [Bazel](https://bazel.build/) (for wheel builds and tests)

The standalone build installs [PyInstaller](https://pyinstaller.org/) itself, at the version
pinned in `tools/build/requirements_platform.txt`; nothing has to be installed by hand.

```bash
# Activate the project virtualenv
source .venv/bin/activate

# Install the package with provider extras
pip install -e ".[llm]"
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

The store `third_party_licenses/` is mostly **hand-made**: the full collector run cannot
reproduce it (see below). Releases therefore only compile it:

```bash
python tools/release/update_licenses.py --compile-only
git add third_party_licenses/ teachers_teammate/assets/third_party_licenses.md
```

`--compile-only` reads `packages.csv` and `licenses/`, applies the license names of
`tools/release/license_overrides.toml`, rewrites `packages.csv`, `packages.md` and `summary.md`
from them and writes the two compiled files (`third_party_license.txt` and the in-app
`third_party_licenses.md`, which starts with `NOTICE`). It needs no virtual environment.

**When a dependency changes** (a lock bump, a new or removed package), edit the store by hand,
then compile:

1. Put the version in `packages.csv` (the same as `requirements_test_lock.txt` or
   `tools/build/requirements_platform.txt`; `python` is the patch version of `.python-version`).
2. Copy the license files of the new version from the wheel into `licenses/<package>/`. Compare the
   old and new texts; bundled-library notices change between versions (pypdfium2, numpy, pillow,
   lxml, opencv).
3. For a Qt/PySide6 bump, regenerate `licenses/PySide6_Essentials/qt_third_party/` from the
   `qt_attribution.json` files of the new Qt version (see `pyside6_qt_third_party.md`), and change
   the version and links in `NOTICE`.
4. Run `--compile-only`.

`check_inventory.py --env` runs on every OS build and fails when the build installs a distribution
that has no row in `packages.csv` or a different version.

What the hand-made parts are (the same list is in the docstring of `update_licenses.py`):
the `python` and `pyinstaller` rows and texts, the platform-only packages (`pywin32-ctypes` on
Windows; `SecretStorage`, `jeepney`, `cryptography`, `cffi`, `pycparser` on Linux),
`pypdfium2/deps/`, the vendored trees of `numpy` and `pip`, the Qt attributions, and
`cryptography/OPENSSL.txt`.

`bash tools/release/update_licenses.sh --destructive` still exists as the full collector run. It
installs the newest packages instead of the locked ones, sees one platform only and wipes the
store, so **do not use it to prepare a release**. Without `--destructive` it does nothing.

Outputs:

| Path | Purpose |
|------|---------|
| `third_party_licenses/packages.md` | Table of all runtime packages and their license types |
| `third_party_licenses/summary.md` | Aggregated count per license type |
| `third_party_licenses/licenses/<pkg>/` | The LICENSE / NOTICE files per package |
| `third_party_licenses/third_party_license.txt` | All of it in one file; shipped as `third_party_license.txt` |
| `teachers_teammate/assets/third_party_licenses.md` | Single bundled file shown in **Help → Third-Party Licenses**; starts with `NOTICE` |

`NOTICE` and `third_party_license.txt` travel with every download: in the `.deb` and the tarball
under the documentation folder, in the Windows install folder, and in the macOS `.app`
(`Contents/Resources`) and next to it in the `.dmg`. Qt is used under the LGPL-3.0, so the notice
and the source links must reach the user with each copy.

Both `third_party_licenses/` and `teachers_teammate/assets/third_party_licenses.md` are
committed; only the throwaway `.venv-licenses/` is ignored.

### Standalone binary

`tools/build/build_standalone.py` is the primary build script. It generates the platform icons
and runs the committed spec `tools/build/teachers-teammate.spec`, which lists what the bundle
contains. It produces a folder (Linux and Windows) or a `.app` bundle (macOS).

Every version it installs comes from `requirements_test_lock.txt` plus
`tools/build/requirements_platform.txt`: `tools/build/make_constraints.py` writes
`pyinstaller-work/constraints.txt`, pip installs against it, and `tools/build/check_pins.py`
fails the build when anything in the environment is not pinned. Two builds of the same commit
therefore produce the same bundle and the same license list.

```bash
python tools/build/make_constraints.py
pip install -c pyinstaller-work/constraints.txt -e . pyinstaller pyinstaller-hooks-contrib
python tools/build/check_pins.py
python tools/build/build_standalone.py
```

Outputs:

| Platform | Path |
|----------|------|
| Linux | `dist/teachers-teammate/teachers-teammate` |
| Windows | `dist/teachers-teammate/teachers-teammate.exe` |
| macOS | `dist/teachers-teammate.app/` |

Every platform gets a folder, never a single file: the `.deb` installs it under `/opt`, the
portable tarball unpacks it, and the Qt libraries stay separate files that can be replaced.

`tools/build/build.sh` (Linux/macOS) and `tools/build/build.bat` (Windows) do all of that in a
fresh venv, so a local build matches CI.

Then check what was built:

```bash
# The app starts and its imports resolve; writes a JSON report.
QT_QPA_PLATFORM=offscreen ./dist/teachers-teammate/teachers-teammate --_smoke_test smoke.json

# Linux only: the bundle runs on Ubuntu 22.04 (glibc 2.35) and carries no dead libraries.
python tools/build/check_linux_bundle.py --max-glibc 2.35
python tools/build/check_linux_bundle.py --unused
```

### Platform installers

Run these **after** `build_standalone.py`.

**Linux — `.deb` package**

```bash
bash tools/packaging/make_deb.sh [version]
# e.g.  bash tools/packaging/make_deb.sh 1.2.3
# Output: dist/teachers-teammate_1.2.3_amd64.deb
```

The package installs the folder into `/opt/teachers-teammate`, symlinks
`/usr/bin/teachers-teammate` to it, and puts the desktop entry, the icon and the licenses under
`/usr/share`. Its `Depends:` line is measured from the build every time (`check_linux_bundle.py --depends`):
every `DT_NEEDED` entry of every bundled ELF file that the bundle does not itself carry, mapped
to Debian packages with `ldconfig` and `dpkg-query`, plus `libc6` at the glibc floor the bundle
needs. The build machine must have those libraries installed; the CI job does. CI installs the `.deb` in `ubuntu:22.04`, `ubuntu:24.04` and
`debian:12` containers, runs the smoke test there, and checks that the xcb platform plugin has
no missing libraries.

**Linux — portable tarball**

```bash
bash tools/packaging/make_tarball.sh [version]
# Output: dist/teachers-teammate-1.2.3-linux-x86_64.tar.gz
```

The tarball holds the same folder under `teachers-teammate-1.2.3/`, plus a `README.txt` that says
to unpack it and run `./teachers-teammate`.

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

`tools/packaging/test_windows_installer.ps1` installs, updates and uninstalls that file and
checks what is left behind; CI runs it on every Windows build. It needs an elevated shell
(GitHub's `windows-latest` is one), which is why its per-user runs pass `/SCOPE=user`.

```powershell
pwsh -File tools/packaging/test_windows_installer.ps1 `
    -Setup dist\teachers-teammate-1.2.3-w64-setup.exe -Version 1.2.3
```

#### Windows: scopes, command line and exit codes

The installer starts **unelevated** and asks for administrator rights only when the chosen
scope needs them, so a teacher without an administrator account can still install.

| Scope | Folder | Registry | Needs |
|-------|--------|----------|-------|
| Only for me (default when not elevated) | `%LOCALAPPDATA%\Programs\TeachersTeammate` | `HKCU` | nothing |
| All users (default when elevated) | `%ProgramFiles%\TeachersTeammate` | `HKLM`, 32-bit view | administrator rights |

Settings, downloaded models and add-ons are kept per Windows account, in
`%LOCALAPPDATA%\teachers_teammate`, whichever scope the program itself was installed in.

| Argument | Meaning |
|----------|---------|
| `/S` | silent (NSIS) |
| `/SCOPE=user` / `/SCOPE=all` | choose the scope; also passed to the elevated copy |
| `/NODESKTOP` | no desktop shortcut (fresh install) |
| `/ALLOWDOWNGRADE` | silent: allow installing an older version |
| `/D=<folder>` | install folder of a fresh install (NSIS, must be the last argument, not quoted); an update always stays in the folder of the copy it updates. The relaunch for administrator rights passes it on |

An update never runs the old uninstaller: it removes the old program files itself and leaves
every registry value and every piece of user data alone. A folder that already holds other
files is refused rather than emptied. When a computer has a copy in both scopes (possible only
from before the installer looked in both places), the interactive setup names both and asks
for one to be removed in Windows Settings; a silent run needs `/SCOPE=` to pick the copy
(exit code 13 without it).

| Exit code | Meaning |
|-----------|---------|
| 0 | done |
| 1 / 2 | cancelled / aborted (NSIS defaults) |
| 10 | Teacher's Teammate is still open |
| 11 | an older version was refused |
| 12 | the folder holds other files (silent runs; the folder page says so otherwise) |
| 13 | would create a second copy in the other scope, or two copies exist and `/SCOPE=` is missing |
| 14 | needs administrator rights |

A silent uninstall keeps all user data unless `/DELDATA` (add-ons and models) or
`/DELSETTINGS` (settings, saved API keys, results cache) is given.

---

## Tools overview

| File | Purpose |
|------|---------|
| `tools/build/build_standalone.py` | Primary cross-platform PyInstaller build (used by CI) |
| `tools/build/build.sh` / `tools/build/build.bat` | Fresh venv, pinned install, then `build_standalone.py` |
| `tools/build/make_constraints.py` | Writes `pyinstaller-work/constraints.txt` from the lock and the platform pins |
| `tools/build/check_pins.py` | Fails the build when an installed package is not pinned |
| `tools/build/check_linux_bundle.py` | Checks the Linux bundle's glibc floor and lists libraries nothing loads |
| `tools/build/teachers-teammate.spec` | The committed PyInstaller spec: what the bundle contains |
| `tools/build/run_gui.py` | PyInstaller entry point (pip dispatch, smoke test, addon injection) |
| `tools/packaging/make_ico.py` | Generates `.ico` and welcome BMP for Windows |
| `tools/packaging/make_icns.sh` | Generates `.icns` for macOS |
| `tools/packaging/make_deb.sh` | Creates `.deb` package for Debian/Ubuntu |
| `tools/packaging/make_tarball.sh` | Creates the portable Linux `.tar.gz` |
| `tools/packaging/make_dmg.sh` | Creates `.dmg` installer for macOS |
| `tools/packaging/windows.nsi` | NSIS script for the Windows installer |
| `tools/release/stamp_versions.py` | Stamps version into `pyproject.toml` and Bazel `BUILD` |
| `tools/release/update_licenses.py` | Compiles the third-party license report and GUI asset (`--compile-only`) |
| `tools/release/update_licenses.sh` | The destructive full run (`--destructive`), not for releases |
