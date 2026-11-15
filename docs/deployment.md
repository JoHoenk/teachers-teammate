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
Use this to validate the build before cutting a real release. It starts with the same `validate`
job as the release, so a version that would fail there fails here first, before anything is built.

### Bundle test build (`build_bundles.yml`)

Manual trigger with a version string (default `v0.0.0-test`).
Builds all three platform bundles — no lint/test gate, no release, no tag — and uploads them as
workflow-run artifacts with 2-day retention. After a successful build, artifacts from earlier manual
runs of this workflow are deleted, so only the newest bundles are kept (a failed build leaves the
previous ones in place):

```bash
gh workflow run build_bundles.yml -f version=v0.0.0-test
gh run watch
gh run download <run-id> -n teachers-teammate-linux   # or -windows, -macos
```

A pull request that touches `tools/packaging/**`, `tools/build/**` or
`.github/workflows/build_*.yml` runs it too, with read-only access (so a fork's pull request works).
Its artifacts are named `teachers-teammate-pr-*` and are never pruned by a manual run. A new push to
the same pull request cancels the run it supersedes.

### Release (`release.yml`)

A release is a tag: the tag is the version and the commit it points at is what is built.

```bash
git tag v1.2.3            # or v1.2.3-rc1 for a pre-release
git push origin v1.2.3
```

Accepted are `vX.Y.Z` and `vX.Y.Z-(alpha|beta|rc|pre)[.]N`, the number required — `v0.5.7-rc1`
and `v0.5.7-rc.1` both, `v0.5.7-rc.` and `v0.5.7-pre` not. A PEP 440 form without the dash (`v0.5.7rc1`) is rejected: GitHub would publish it as
*Latest*, and `VIProductVersion` would break `makensis`.

```
validate ──► lint ──┐
             test ──┴──► create-release (draft GitHub Release)
                              │
                              ├──► build-wheel      → wheel (.whl) attached to the draft
                              └──► build-standalone → platform installers attached to the draft
                                                 │
                                                 └──► publish-release → flips the draft to published
```

**validate** runs first and fails within seconds, before anything is built or a draft exists: it
checks the tag form, that the tagged commit is on `main` (so a tag on a side branch cannot
ship) and that the version is newer than the latest published release (the app's update check
reads `/releases/latest`; a pre-release must belong to a higher version). A bad tag is deleted by hand (`git push --delete origin <tag>`) and pushed again.

A tag ruleset in the repository settings allows only the maintainer to create or delete `v*` tags
and forbids moving an existing one.

Each tag has its own concurrency group (`release-<tag>`), so tags pushed together all build, side
by side — a single shared group would keep only one pending run and cancel the rest.
**publish-release** marks a final release *Latest* only if no higher version is already published,
so an older tag that finishes last cannot take it over.

The release stays a draft until **publish-release** runs, so a half-uploaded release is never
visible. The upload steps pass `draft: true` too: without it the upload action publishes an
existing draft after its first upload. A pre-release version (one containing `-`) is published
without `--latest`.

A failed run leaves its draft behind: delete it with `gh release delete <tag> --cleanup-tag`
before pushing the tag again.

The **build-standalone** job (`build_artifacts.yml`) runs in parallel on three GitHub-hosted runners:

| Runner | Steps | Output |
|--------|-------|--------|
| `ubuntu-22.04` | pinned install → `tools/build/build_standalone.py` → smoke test → add-on resolution check → bundle checks → `tools/packaging/make_deb.sh` → container install test → `tools/packaging/make_tarball.sh` | `teachers-teammate_<ver>.deb`, `teachers-teammate-<ver>-linux-x86_64.tar.gz` |
| `windows-latest` | pinned install → `tools/build/build_standalone.py` → smoke test → NSIS (`tools/packaging/windows.nsi`) | `teachers-teammate-<ver>-w64-setup.exe` |
| `macos-latest` | pinned install → `tools/build/build_standalone.py` → smoke test → `tools/packaging/make_dmg.sh` | `teachers-teammate-<ver>-macos.dmg` |

The Linux job builds on `ubuntu-22.04` on purpose: PyInstaller bundles the libraries of the
machine it builds on, so a newer image would silently raise the minimum glibc. If GitHub drops
that image, build in an `ubuntu:22.04` container on `ubuntu-latest` rather than moving to a newer
runner.

Every job creates a clean venv and installs into it against `pyinstaller-work/constraints.txt` (see
[Standalone binary](#standalone-binary)); the Linux job also runs
`tools/build/check_pins.py --platform win_amd64` and `--platform macosx_14_0_arm64`, because pip
evaluates PyInstaller's Windows-only (`pefile`) and macOS-only (`macholib`) dependencies against the
Linux host and would never report them. Every job runs the app with `--_smoke_test smoke.json` and fails
on a non-zero exit, and uploads `smoke.json` as an artifact. The Linux job also runs
`tools/build/check_linux_bundle.py` twice: `--max-glibc 2.35` proves the bundle starts on Ubuntu
22.04, `--unused` proves it carries no library that nothing loads. After the smoke test it runs
`tools/build/check_addon_resolution.py`: the built app's own pip resolves every add-on's requirement
(`--dry-run`, nothing is installed) against the bundle's versions, so a new release of
`langchain-openai` or spaCy that needs another numpy or pydantic fails the build instead of a
teacher's Add-ons window.

The **build-wheel** job stamps the version into `pyproject.toml` via `tools/release/stamp_versions.py`, then builds a pure-Python wheel with Bazel.

---

## Manual build (local)

All commands are run from the **repository root**.

### Prerequisites

- Python 3.14 (`.python-version`)
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

Before it runs PyInstaller, `build_standalone.py` writes `teachers_teammate/_build/addon_constraints.txt`
(`tools/build/addon_constraints.py`, git-ignored): the version of every package in the build
environment except the build tools. The spec bundles it. The installed app gives it to pip as `-c`
for every add-on install, and compares the add-on folder against it after an app update — see
`docs/development.md` → "The bundle's constraints and add-ons after an app update". The folder is
`packages/` next to the other per-user data. Add-ons built for another Python are noticed at
start: the app reads the Python tag in every `.dist-info/WHEEL`, keeps such a folder off
`sys.path` and asks for *Update add-ons*, which reinstalls the add-ons for the running Python and
drops what is left over. Nothing is deleted before that click.

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

### The Python version

`.python-version` holds the full patch (`3.14.10`). `MODULE.bazel` names it once more
(`python.toolchain`, which gives the Bazel toolchain its coverage tool): change both together.
rules_python reads the file for the toolchain, `setup-python` reads it in CI, and
`tools/build/build.sh` / `build.bat` name the same minor. `//tests:test_python_version` fails when
the interpreter the tests run on differs from the file, and the bundle workflow fails when a
`smoke.json` reports another version.

To bump the patch, change `.python-version` and `MODULE.bazel` and make sure the rules_python
version in `MODULE.bazel` knows it (`python/versions.bzl`, `MINOR_MAPPING`); then relock with
`bazel run //:requirements_test.update` and `//:requirements_dev.update`. Nothing reports a newer
patch yet: the weekly add-on job that would (`addon_resolution.yml`) is not written, so check
`actions/python-versions` by hand before a release.

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
`/DELSETTINGS` (settings file `ocr.toml`, benchmark history, speed data, Qt settings, saved API
keys, results cache) is given. Settings, benchmark history and speed data live in
`%LOCALAPPDATA%\teachers_teammate` beside `packages\` and `models\`, not in the cache folder;
the installer test checks that an update and a plain uninstall keep them and `/DELSETTINGS`
removes them.

---

## Tools overview

| File | Purpose |
|------|---------|
| `tools/build/build_standalone.py` | Primary cross-platform PyInstaller build (used by CI) |
| `tools/build/build.sh` / `tools/build/build.bat` | Fresh venv, pinned install, then `build_standalone.py` |
| `tools/build/make_constraints.py` | Writes `pyinstaller-work/constraints.txt` from the lock and the platform pins |
| `tools/build/check_pins.py` | Fails the build when an installed package is not pinned |
| `tools/build/addon_constraints.py` | Writes the bundle's package versions (`teachers_teammate/_build/addon_constraints.txt`) that the installed app gives to pip |
| `tools/build/check_addon_resolution.py` | Dry-runs every add-on's requirement against the built app's constraints |
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
| `tools/release/update_licenses.py` | Generates third-party license report and GUI asset |
| `tools/release/update_licenses.sh` | Creates isolated venv and runs `update_licenses.py` |
