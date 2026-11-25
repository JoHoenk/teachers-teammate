# Deployment

How Teacher's Teammate is built and released, in CI and on a developer machine.

---

## Releasing

A release is a tag: the tag is the version, and the commit it points at is what is built.

```bash
git tag v1.2.3            # or v1.2.3-rc.1 for a pre-release
git push origin v1.2.3
```

Accepted tags are `vX.Y.Z` and `vX.Y.Z-(alpha|beta|rc|pre)[.]N`. Check a tag before you push it
(`release.yml`'s `validate` job applies the same rules; they live in `tools/release/release_tag.py`):

```bash
python tools/release/release_tag.py check v1.2.3 --repo JoHoenk/teachers-teammate --skip-main
```

`release.yml` then runs:

```
validate ──► lint + test ──► create-release (draft)
                               ├──► build-wheel
                               └──► build-standalone (Linux, Windows, macOS) + install tests
                                          └──► publish: SHA256SUMS, attestations, assets → draft
```

- **validate** fails within seconds when the tag has the wrong form, the commit is not on `main`,
  the version is not newer than the latest published release, or the release that holds the
  third-party sources of the shipped Qt, OpenCV and lxml is missing or incomplete (publish it first,
  see *Third-party licenses* below).
- Only the newest version ships, so the 0.5.x line gets no further releases: a tag older than the
  latest release is refused, and so is one that is not on `main`.
- The release stays a **draft**. Open it from the job summary, check the notes and the assets, and
  click *Publish*; for a final release also tick *Set as the latest release* (the job summary says
  whether to). The app's update check only sees published releases.
- A failed run leaves its draft behind. Use *Re-run failed jobs* (the draft is reused), or delete it
  before pushing the tag again:

  ```bash
  gh release delete v1.2.3 --cleanup-tag
  ```

Anyone with write access can push, move or delete a `v*` tag, so keep write access to the
maintainer. A tag that was pushed by mistake is deleted with `git push --delete origin <tag>`
before its release exists.

### CI workflows

| Workflow | Runs on | What it does |
|---|---|---|
| `pull_request_checks.yml` | pushes to `main`, pull requests | lint (Bazel format check, ruff, ty; shellcheck and PSScriptAnalyzer for the scripts), the three lock files against `pyproject.toml` and each other, the Bazel tests, the docs build (`build_docs.yml`) and CodeQL; the `check` job is the required status |
| `deploy_docs.yml` | pushes to `main`, manual | builds the Sphinx site and deploys it to GitHub Pages |
| `release.yml` | a `v*` tag | the release above |
| `release_dry_run.yml` | manual, with a version | the whole release build without touching a release |
| `build_bundles.yml` | manual; PRs touching the app (`teachers_teammate/`), `tests/e2e/`, `tools/packaging/`, `tools/build/`, the requirement, `pyproject.toml` and Bazel module files, the build-venv action or `build_*.yml` | the three bundles as workflow artifacts (2 days) |
| `platform_tests.yml` | pull requests | the tests marked `platform` in a plain venv on Windows and macOS, without Bazel; a trial, not part of the merge gate |
| `addon_resolution.yml` | weekly | resolves every add-on's requirement against the bundle's versions, so a new `langchain-*` or spaCy release that breaks an add-on shows up before release day |
| `dependabot.yml` | weekly | dependency bumps, each held back 7 days after its release (security updates are not) |

Test the bundles of the current branch without a release:

```bash
gh workflow run build_bundles.yml -f version=v0.0.0-rc.1
gh run watch
gh run download <run-id> -n teachers-teammate-linux   # or -windows, -macos
```

What CI checks on a build:

- **Wheel:** installed into a clean venv; every console script must start.
- **Bundle:** the smoke test (`--_smoke_test`), every add-on installed and used
  (`--_addon_smoke_test`, needs the network), the update banner against the release feed
  (`tests/e2e/update_banner.py`, needs the network), and the walkthrough ([testing.md](testing.md)
  → "The app walkthrough" and "The update banner").
- **Install:** on fresh Ubuntu 24.04/26.04 and Debian 13 containers, macOS 15/26 and Windows Server
  2022/2025, including an update from the newest published release, after which the new copy walks
  through the data that release left behind.

**By hand on a VM before a release:** the installer's interactive pages, the UAC prompt, a real
desktop (fonts, icons, window manager), Gatekeeper, Windows 10 1809 and macOS 15.0.

### Checksums and provenance

Every release carries a `SHA256SUMS` file and a build-provenance attestation per asset, which
proves which workflow and commit produced the file:

```bash
sha256sum -c SHA256SUMS --ignore-missing            # Linux, next to the download
shasum -a 256 -c SHA256SUMS --ignore-missing        # macOS
gh attestation verify teachers-teammate-<version>-w64-setup.exe --repo JoHoenk/teachers-teammate
```

---

## Compatibility

| | Windows | macOS | Linux |
|---|---|---|---|
| **Oldest supported** | Windows 10 1809 | macOS 15.0 | glibc 2.39 (Ubuntu 24.04, Debian 13, Mint 22) |
| **Set by** | Qt 6.12 | the OpenCV wheel | the build image (`ubuntu-24.04`) |
| **Guarded by** | the installer (exit code 15) | `tools/build/check_macos_bundle.py` | `tools/build/check_linux_bundle.py --max-glibc 2.39` |

A PySide6 bump that raises the macOS floor, or a newer Linux build image, fails the checks above.
Windows has no such check: Qt 6.12 is the last Qt for Windows 10, so read the Qt release notes at
every PySide6 minor bump. Ubuntu 22.04, Mint 21 and Debian 12 are served by v0.5.x.

---

## Building locally

All commands run from the repository root. You need Python 3.14 (`.python-version`) and Bazel.

### Standalone app

`tools/build/build.sh` (Linux, macOS) and `tools/build/build.bat` (Windows) create a fresh venv,
install the pinned versions and run the PyInstaller build, like CI:

```bash
bash tools/build/build.sh
```

Output: `dist/teachers-teammate/` (macOS: `dist/teachers-teammate.app/`). Versions come from
`requirements_lock.txt` and `tools/build/requirements_platform.txt`, so one commit always gives the
same bundle. Check that it starts:

```bash
QT_QPA_PLATFORM=offscreen ./dist/teachers-teammate/teachers-teammate --_smoke_test smoke.json
QT_QPA_PLATFORM=offscreen ./dist/teachers-teammate/teachers-teammate --_addon_smoke_test addon_smoke.json
```

### Python wheel

```bash
bazel build //teachers_teammate:teachers_teammate_wheel   # the development version
```

A release build stamps the version into the metadata and file name; the file lands in the `.dist`
folder:

```bash
bazel build --stamp --embed_label=1.2.3 //teachers_teammate:teachers_teammate_release_wheel.dist
```

### Installers

Run these after the standalone build. Each takes the version as its argument.

```bash
bash tools/packaging/make_deb.sh 1.2.3       # dist/teachers-teammate_1.2.3_amd64.deb
bash tools/packaging/make_dmg.sh 1.2.3       # dist/teachers-teammate-1.2.3-macos.dmg
```

The Windows installer needs [NSIS](https://nsis.sourceforge.io/). The script runs two `makensis`
passes: the uninstaller first, so it can be signed before the setup packs it. `-WinVersion` is the
version without a pre-release suffix:

```powershell
pwsh tools/packaging/make_windows_installer.ps1 -Version 1.2.3 -WinVersion 1.2.3   # dist/teachers-teammate-1.2.3-w64-setup.exe
```

```powershell
# Install, update and uninstall it, and check what is left behind (elevated shell)
pwsh -File tools/packaging/test_windows_installer.ps1 -Setup dist\teachers-teammate-1.2.3-w64-setup.exe -Version 1.2.3
```

### The Python version

`.python-version` holds the full version (`3.14.10`); `MODULE.bazel` names it once more
(`python.toolchain`). Change both together, then relock:

```bash
bazel run //:requirements.update && bazel run //:requirements_dev.update
```

---

## Third-party licenses

`third_party_licenses/inventory.csv` lists every third-party part the app ships or installs. It
is written by `tools/licenses/inventory.py` from the app's lists and the locks, never by hand. When
the inventory test is red (a new or bumped dependency, add-on, model or spaCy language), run
`python tools/licenses/inventory.py` and commit the result; the diff shows any license change. The
tool fails on a license the allow list in `tools/licenses/policy.toml` does not hold, on a copyleft
family in a wheel's texts that `[vendored]` does not account for, and on a Windows or macOS wheel
whose license files differ from the Linux one without a `[variants]` entry.

| Path | Written by | Content |
|---|---|---|
| `third_party_licenses/inventory.csv` | tool | one row per component: version, scope, license |
| `third_party_licenses/texts/` | tool | the license texts from the locked wheels |
| `third_party_licenses/curated/` | hand | texts no wheel carries (Qt, CPython, the PaddleOCR models); a Qt or Python minor bump leaves a test red until it is refreshed |
| `teachers_teammate/assets/LICENSE.txt` | tool | a copy of `LICENSE`: what the first tab of **Help → Licenses** shows, and every download carries as `LICENSE.txt` |
| `teachers_teammate/assets/third_party_licenses.txt` | tool | the license list: what the third-party tab of **Help → Licenses** shows, and what every download carries as `third_party_license.txt` |
| `NOTICE` | hand | attributions and source pointers; a test checks its versions |

Our own source needs no mirror: each release page carries the tag's "Source code" archive, which
is the Corresponding Source of that release (`NOTICE` → "Written offer").

The bundle carries Qt under the LGPL v3 as separate, replaceable files
(`_internal/PySide6/Qt/lib`). The LGPL requires passing on the Qt, PySide6, FFmpeg and libiconv
sources, so every shipped combination of Qt and of the packages that carry the rest (OpenCV with
its FFmpeg, lxml with its libiconv) gets its own release,
`third-party-sources-qt<qt>-<package>-<version>...`, built and published by
`tools/release/mirror_sources.py` as an ordinary, public release of this repository (with a tag of
the same name; it is not a `v*` tag, so no release runs). Versions come from the inventory and
`NOTICE`; file names from the authors' directory listings, so a renamed archive fails here, not on
a download page. The set carries `SHA256SUMS` and a `README.txt`:

```bash
python tools/release/mirror_sources.py                                # fetch and check, discard
python tools/release/mirror_sources.py --keep dist/sources            # keep the set
python tools/release/mirror_sources.py --bundle dist/teachers-teammate  # hold: Qt modules
python tools/release/mirror_sources.py --upload --repo <owner>/<name>  # publish it
```

Which files are held to a published digest, and which are not:

| File | Held to |
|---|---|
| the Qt submodule archives | `md5sums.txt` next to the tarballs; a folder that publishes none fails the run |
| the PySide6 source | its folder's digest list; a folder that publishes none stops the run |
| the `opencv-python-headless` source | the sha256 PyPI publishes |
| the `lxml` source | the sha256 PyPI publishes |
| the FFmpeg tarball | nothing: FFmpeg publishes a signature and no digest. Its `.asc` travels in the set, unverified |
| the libiconv tarball | nothing: GNU publishes a signature and no digest. Its `.sig` travels in the set, unverified |

- `--keep` needs an empty folder (without it, a temporary one), so an upload never publishes an
  earlier run's files.
- `--bundle` names a **Linux** folder build: every `libQt6*.so*` in it needs its submodule archive;
  a folder without any is refused. With `--no-fetch` it checks that and stops, without downloading
  anything; `build_package.yml` runs it on every Linux build, so a Qt module that PySide6 gains
  turns the build red.
- `--upload` needs `gh` and `GH_TOKEN` (or a `gh auth login`), never marks the release *Latest* (the
  update check must not see it), and refills a release of the same name (a failed run's) with this
  set's assets, title and notes. Another version of Qt, OpenCV or lxml is another release; the older
  one stays untouched.
- **Publish the sources before the tag.** `release.yml`'s `validate` job fails when the sources
  release of the current versions is missing or does not hold the whole set. Only
  `release_dry_run.yml` passes `--skip-sources`.
- Never delete a sources release while a download of its versions exists.

**Bumping `pyside6-essentials`, `shiboken6`, `opencv-python-headless` or `lxml`:** re-run the
mirror, name the new versions and the new release name in the Qt/PySide6, FFmpeg and libiconv
entries of `NOTICE`, then run `python tools/licenses/inventory.py --compile`. Dependabot groups
them. `certifi` needs none of
this: every certifi release on PyPI carries its own source.

---

## Windows installer reference

For IT departments installing silently.

| Scope | Folder | Needs |
|---|---|---|
| Only for me (default) | `%LOCALAPPDATA%\Programs\TeachersTeammate` | nothing |
| All users | `%ProgramFiles%\TeachersTeammate` | administrator rights |

Settings, models and add-ons live per account in `%LOCALAPPDATA%\teachers_teammate`, in either
scope, and survive an update.

| Argument | Meaning |
|---|---|
| `/S` | silent |
| `/SCOPE=user`, `/SCOPE=all` | the scope |
| `/NODESKTOP` | no desktop shortcut |
| `/NOUPDATECHECK` | the app does not look for a newer version at start (fresh install); the same choice is a check box on the second page, and a fresh install writes the box either way |
| `/SETTINGSDIR="<folder>"` | the settings folder that choice is written to; default `%LOCALAPPDATA%\teachers_teammate` of the account that runs the installer, so a silent all-users install run by an administrator sets it for that administrator only. The installer passes it on to its elevated copy, which Windows may run as another account |
| `/ALLOWDOWNGRADE` | allow an older version (silent) |
| `/D=<folder>` | install folder of a fresh install (last argument, not quoted) |

A silent uninstall keeps all user data unless `/DELDATA` (add-ons and models) or `/DELSETTINGS`
(settings, saved keys, the teacher's corrections, benchmark history, results cache) is given.

| Exit code | Meaning |
|---|---|
| 0 | done |
| 1 / 2 | cancelled / aborted |
| 10 | Teacher's Teammate is still open |
| 11 | an older version was refused |
| 12 | the folder holds other files |
| 13 | a copy exists in the other scope (pass `/SCOPE=`) |
| 14 | needs administrator rights |
| 15 | Windows 10 (version 1809) or newer is required |
