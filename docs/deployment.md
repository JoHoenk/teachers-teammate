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
Runs the full build matrix — lint, test, wheel, and all platform standalone builds — but **never
touches a release**: the builds run read-only and their outputs stay in the run's artifacts.
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
                              ├──► build-wheel      (read-only) → wheel artifact
                              └──► build-standalone (read-only) → installer artifacts
                                                 │
                                                 └──► publish → SHA256SUMS + attestations,
                                                               assets attached to the draft
                                                 │
                                                 └──► you: review the draft, click *Publish*
```

**validate** runs first and fails within seconds, before anything is built or a draft exists: it
checks the tag form, that the tagged commit is on `main` (so a tag on a side branch cannot
ship) and that the version is newer than the latest published release (the app's update check
reads `/releases/latest`; a pre-release must belong to a higher version). A bad tag is deleted by hand (`git push --delete origin <tag>`) and pushed again.

A tag ruleset in the repository settings allows only the maintainer to create or delete `v*` tags
and forbids moving an existing one.

Each tag has its own concurrency group (`release-<tag>`), so tags pushed together all build, side
by side — a single shared group would keep only one pending run and cancel the rest.

**publish** is the only job that writes to the release, and it only uploads files: it downloads
the four release artifacts by name (`release-wheel`, `release-linux`, `release-windows`,
`release-macos`; the `release` prefix keeps the manual *Build bundles* clean-up away from them),
requires exactly one wheel, `.deb`, tarball, installer and `.dmg` and nothing else, writes `SHA256SUMS`, attests the build provenance of each file and
attaches them to the draft with `gh release upload`. It never flips the draft to published.

The build jobs (`build_wheel.yml`, `build_artifacts.yml`) declare `permissions: contents: read`
themselves, so a caller cannot raise them, and every `actions/checkout` sets
`persist-credentials: false`. The build runs ~90 PyPI packages and PyInstaller hooks; a bad
version of any of them must not be able to replace files on a release. The assets reach the
release only through the artifact store and the `publish` job.

The release stays a **draft** until you publish it, so a half-uploaded release is never visible
and the update check (`/releases/latest`) never offers it. Open the draft from the job summary
(or `https://github.com/<owner>/teachers-teammate/releases/edit/<tag>`), check the notes and the
assets, and click *Publish* — for a final release also tick *Set as the latest release*; a `-`
tag is already marked pre-release. The workflow does not mark a release *Latest*; the job summary
tells you whether to tick the box (**not** when a higher version is already published), so an
older tag that finishes last does not take it over — the maintainer's choice decides.

A failed run leaves its draft behind: delete it with `gh release delete <tag> --cleanup-tag`
before pushing the tag again.

The **build-standalone** job (`build_artifacts.yml`) runs in parallel on three GitHub-hosted runners:

| Runner | Steps | Output |
|--------|-------|--------|
| `ubuntu-24.04` | pinned install → `tools/build/build_standalone.py` → smoke test → add-on resolution check → bundle checks → `tools/packaging/make_deb.sh` → `tools/packaging/make_tarball.sh` | `teachers-teammate_<ver>.deb`, `teachers-teammate-<ver>-linux-x86_64.tar.gz` |
| `windows-2025` | pinned install → `tools/build/build_standalone.py` → smoke test → NSIS 3.13.0 (`tools/packaging/windows.nsi`, two passes: the uninstaller, then the setup) | `teachers-teammate-<ver>-w64-setup.exe` |
| `macos-26` | pinned install → `tools/build/build_standalone.py` → smoke test → `tools/build/check_macos_bundle.py` → `tools/packaging/make_dmg.sh` | `teachers-teammate-<ver>-macos.dmg` |

The runners are pinned, not `-latest`: a moving label would change the Windows and macOS the
bundle is built and tested on without a commit saying so. NSIS comes from Chocolatey at a pinned
version for the same reason.

The Linux job builds on `ubuntu-24.04` on purpose: PyInstaller bundles the libraries of the
machine it builds on, so the glibc floor follows the build image — 2.39, which is Ubuntu 24.04+,
Debian 13+ and Mint 22+. A newer image would silently raise it, which `check_linux_bundle.py`
then catches. The next move is when 24.04 leaves standard support (2029), and it is a decision,
not a runner default. Users on Ubuntu 22.04, Mint 21 or Debian 12 stay on v0.5.x: the old app's
update check offers the new release, apt refuses it (`libc6 (>= 2.39)`) and the tarball does not
start. No code can help there — an old app cannot learn the new floor.

Every job creates a clean venv and installs into it against `pyinstaller-work/constraints.txt` (see
[Standalone binary](#standalone-binary)); the Linux job also runs
`tools/build/check_pins.py --platform win_amd64` and `--platform macosx_14_0_arm64`, because pip
evaluates PyInstaller's Windows-only (`pefile`) and macOS-only (`macholib`) dependencies against the
Linux host and would never report them. Every job runs the app with `--_smoke_test smoke.json` and fails
on a non-zero exit, and uploads `smoke.json` as an artifact. The Linux job also runs
`tools/build/check_linux_bundle.py` twice: `--max-glibc 2.39` proves the bundle starts on Ubuntu
24.04, `--unused` proves it carries no library that nothing loads. The macOS job runs
`tools/build/check_macos_bundle.py` (its floor is `MACOS_MINIMUM`, 14.4): it reads the minimum macOS of every Mach-O
file in the `.app` (every architecture slice of a universal2 one) and fails when one needs a newer
macOS than 14.4. The PySide6 6.12.0 wheel is tagged `macosx_14_0`, but its Qt libraries say 14.4 and
its binding modules (`PySide6/*.abi3.so`, `libpyside6`, `shiboken6`) say 26.0, so those are exempt
(`--ignore`); no runner older than macOS 15 is available any more, so that they load on 14 stays on the VM list. After the smoke test the Linux job runs
`tools/build/check_addon_resolution.py`: the built app's own pip resolves every add-on's requirement
(`--dry-run`, nothing is installed) against the bundle's versions, so a new release of
`langchain-openai` or spaCy that needs another numpy or pydantic fails the build instead of a
teacher's Add-ons window. The same check runs weekly on all three runners
(`addon_resolution.yml`, see *Add-on resolution (weekly)* below).

Three further jobs install what the build made, one per system, so a broken package fails here
rather than on a teacher's computer. They are read-only like the build; only the Windows one
checks out, because it runs the installer test script from the repository.

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `install-linux` | `ubuntu:24.04`, `ubuntu:26.04`, `debian:13` containers | apt resolves the `.deb`'s `Depends:`, the app starts headless, the xcb platform plugin finds its libraries |
| `install-macos` | `macos-15`, `macos-26` | the dmg mounts, the app copied to `/Applications` starts |
| `install-windows` | `windows-2022`, `windows-2025` | `tools/packaging/test_windows_installer.ps1` passes: install, update, uninstall, both scopes |

None of them is a real desktop (no window, no X11/Wayland, no fonts, no Gatekeeper): that stays on
the VM list.

The **build-wheel** job stamps the version into `pyproject.toml` via `tools/release/stamp_versions.py`, then builds a pure-Python wheel with Bazel.

### Add-on resolution (weekly)

`addon_resolution.yml` runs every Monday and on dispatch, on the three build runners
(`ubuntu-24.04`, `windows-2025`, `macos-26`), natively — no `--platform`, no marker emulation, so
pip's markers see the real system. Per runner it builds the same venv as the bundle build (one
composite action, `.github/actions/build-venv`, so the two cannot drift), writes the bundle's
constraints with `tools/build/addon_constraints.py` and runs
`tools/build/check_addon_resolution.py --python … --constraints …`: every add-on's requirement is
resolved in dry-run mode against the versions the bundle ships. No PyInstaller run.

The constraints are `addon_constraints.txt` (the build environment's freeze minus the build
tools), not `pyinstaller-work/constraints.txt`: the latter is the whole test lock and pins
`langchain-core` and spaCy, so the run would never see a new release of the very packages it
exists for. A red weekly run is the signal, so release day is not the first time a new
`langchain-openai` release meets the bundle.

### Checksums and provenance

Every release carries a `SHA256SUMS` file and a build-provenance attestation per asset:

```bash
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify teachers-teammate-<version>-w64-setup.exe --repo JoHoenk/teachers-teammate
```

The attestation is signed with GitHub's OIDC token for this repository's release workflow, so it
proves which workflow and commit produced the file — not merely that the file is unchanged since
it was downloaded. It vouches for the workflow run, not for the packages that run installed.
`SHA256SUMS` covers the same files for a check without `gh`.

### Dependency updates (`dependabot.yml`)

Dependabot proposes weekly bumps for the Python pins (`/` and `/tools/build`), the Bazel modules
in `MODULE.bazel` and the actions in `.github/workflows`. Every entry carries
`cooldown: default-days: 7`: a version update is proposed only once the release is a week old.
The build installs by version — the lock's hashes are dropped by `make_constraints.py` — so a
malicious release of any of the ~90 packages the bundle carries would otherwise be merged before
anyone noticed it. Such releases are usually found and pulled within days. Security updates are
never delayed.

Two pins stay manual: the `trlc` `git_override` (a commit, not a release) and the PlantUML
artifact of the Maven install. A proposal that needs Bazel 9 is ignored: every rule has to
support Bazel 8 first, so moving to 9 is a decision, not a bump.

---

## Compatibility

What each system needs, what sets that floor, and what keeps it honest. The user-facing version
of this table is in `docs/index.md` → "System requirements".

| | Windows | macOS | Linux |
|---|---|---|---|
| **What sets the floor** | Qt 6.12 (its supported platforms name Windows 10 1809 as the oldest target) and Python 3.14 | the PySide6 wheel (tagged `macosx_14_0`, its Qt libraries ask for 14.4) | the build image's glibc |
| **Built on** | `windows-2025` | `macos-26` | `ubuntu-24.04` (glibc 2.39) |
| **Tested on** | the installer test and the app start on Server 2022 and Server 2025 | the dmg installed and the app started on macOS 15 and 26 (not Gatekeeper) | the package installed and the app started headless in Ubuntu 24.04, 26.04 and Debian 13 |
| **What guards it** | the installer's own check (`${AtLeastBuild} 17763`, exit code 15) | `tools/build/check_macos_bundle.py` (`MACOS_MINIMUM`) | `tools/build/check_linux_bundle.py --max-glibc 2.39` |

A real desktop on each system — a window, X11/Wayland, fonts, Gatekeeper, a Windows 10 1809
machine — is the VM list, not CI.

**When the floor moves.** A PySide6 bump that raises its wheel's macOS tag fails
`check_macos_bundle.py` first, and a newer Linux build image fails `check_linux_bundle.py` first.
Windows has no such check: **Qt 6.12 is the last Qt to support Windows 10**, so the PySide6 bump
after it moves the Windows floor to 11 and nothing in CI will say so. Read the Qt release notes at
every PySide6 major or minor bump.

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

### Third-party licenses

One file lists every third-party part the app ships or installs:
`third_party_licenses/inventory.csv`. Nothing else lists licenses — not the add-on
asset, not a summary, not a manual-review list. `tools/licenses/inventory.py` owns
it: it reads the app's own lists (`[project] dependencies`, the add-ons'
`addon_package_names()`, the `LocalModel`s, `SPACY_MODEL_PINS`), takes every
version from the two locks, and writes the rows, the texts and the compiled
outputs.

```bash
# Maintainer refresh: needs the network, downloads the wheels, rewrites the rows
python tools/licenses/inventory.py
git add third_party_licenses/ teachers_teammate/assets/third_party_licenses.md

# Offline, what the tests and the builds run
python tools/licenses/inventory.py --compile          # rebuild the outputs from the store
python tools/licenses/inventory.py --check-env        # this environment vs the inventory
python tools/licenses/inventory.py --check-resolved R # a pip --dry-run report vs the inventory
```

| Path | Owner | Purpose |
|------|-------|---------|
| `third_party_licenses/inventory.csv` | tool | Every bundle, add-on, build and model row: component, version, scope, license, text |
| `third_party_licenses/texts/<component>/` | tool | The license texts, taken from the locked wheel |
| `third_party_licenses/curated/<component>/` | by hand | Texts no wheel carries (Qt, CPython, the PaddleOCR models), each with a `STAMP.toml` |
| `third_party_licenses/third_party_license.txt` | tool | The compiled text every download carries |
| `teachers_teammate/assets/third_party_licenses.md` | tool | The same, shown in **Help → Third-Party Licenses** |
| `NOTICE` | by hand | The attributions and the source pointers; a test checks its versions |

The tool never writes into `curated/`. A stamp records the versions it was
refreshed for, so a Qt or Python minor bump leaves the stamp test red until
someone refreshes that folder by hand.

The checks run offline in `bazel test //tests:unit_tests`; only the maintainer
refresh touches the network. `--check-env` runs in each OS build and fails on an
installed distribution the inventory does not list; `--check-resolved` runs in the
weekly add-on job and does the same for what an add-on's requirement resolves to.

#### What a bump does

| Event | What happens |
|---|---|
| Dependabot or a lock bump (test lock or add-on lock) | inventory test red → `python tools/licenses/inventory.py` → commit; the diff shows any license change |
| New dependency, add-on, model, spaCy language | same red test (row missing) |
| Platform-only dependency appears | `--check-env` red in that OS build (or `--check-resolved` in the weekly job) → pin it in `requirements_platform.txt` or accept it → run the tool |
| Qt or Python minor bump | rows update by themselves; the stamp test stays red until the curated folder is refreshed by hand |
| Qt patch or OpenCV bump | the NOTICE source test is red until its entry and the sources release name the new version (see below) |
| A license turns denied | the tool fails; drop the part, or `[accepted]` with a reason |

#### The sources release

LGPL-3 and LGPL-2.1 oblige us to pass on the source of what we ship. The Qt and
FFmpeg sources live in a separate GitHub release, `third-party-sources-qt<version>`,
holding qtbase, qtsvg, qtimageformats, qtwayland and `pyside-setup` of the shipped
Qt version plus the FFmpeg tarball of the version inside the shipped OpenCV (about
90 MB). A maintainer creates it by hand once per Qt or OpenCV bump:

```bash
gh release create third-party-sources-qt<version> --latest=false \
  --notes "<what it holds, for which app versions>"
gh release upload third-party-sources-qt<version> <archives>
```

`--latest=false` is not optional: the app's update check and `release.yml` read
`/releases/latest`, so a sources release marked latest would be offered to teachers
as an update. Never delete a sources release while an app release that ships that
version is still up. `NOTICE` names the release next to the upstream link, and a
test keeps that tag on the locked Qt version.

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

#### Version information in the files

`tools/build/make_version_resource.py` writes `teachers_teammate/_build/version_info.txt`
(git-ignored) before PyInstaller runs, and the spec hands it to `EXE(version=…)` on Windows. The
version comes from `$VERSION` (what CI passes) and otherwise from
`teachers_teammate.__version__`:

```bash
python tools/build/make_version_resource.py --version 1.2.3 --output teachers_teammate/_build/version_info.txt
```

The strings are the ones the installer and the uninstaller carry, so a signing service that
compares the three files finds one product name and one product version:

| Key | Value |
|-----|-------|
| `ProductName` | `Teacher's Teammate` (the `APP_NAME` of `windows.nsi`) |
| `CompanyName` | `Teacher's Teammate Contributors` (`APP_PUBLISHER`) |
| `ProductVersion`, `FileVersion` | the release string exactly as the tag reads it (`0.5.7`, `0.5.7-rc.1`) |
| `FileDescription` | `Teacher's Teammate` |
| `OriginalFilename` | `teachers-teammate.exe` |
| `LegalCopyright` | `© 2026 Teacher's Teammate contributors · Apache-2.0` |

Only the numeric fixed fields of the resource are derived: the pre-release suffix is stripped, so
`0.5.7-rc.1` becomes `0.5.7.0` there. `//tests:test_make_version_resource` checks the fields for
three versions and fails when the product name in the generator and in `windows.nsi` differ; it
reads the signing service's artifact configuration (`tools/packaging/signpath/*.xml`) too, as
soon as that file exists.

On macOS the spec's `BUNDLE` sets the same version in the `.app`'s `Info.plist`:
`CFBundleShortVersionString` and `CFBundleVersion` from the stamped version,
`NSHighResolutionCapable`, and `LSMinimumSystemVersion` from `MACOS_MINIMUM` in
`tools/build/check_macos_bundle.py` — one number for the bundle and the check, so they cannot
disagree.

Then check what was built:

```bash
# The app starts and its imports resolve; writes a JSON report.
QT_QPA_PLATFORM=offscreen ./dist/teachers-teammate/teachers-teammate --_smoke_test smoke.json

# Linux only: the bundle runs on Ubuntu 24.04 (glibc 2.39) and carries no dead libraries.
python tools/build/check_linux_bundle.py --max-glibc 2.39
python tools/build/check_linux_bundle.py --unused

# macOS only: the .app asks for no newer macOS than MACOS_MINIMUM (14.4); the PySide6
# bindings are exempt, see above.
python tools/build/check_macos_bundle.py --ignore '*/PySide6/*.abi3.so'

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
patch: the weekly add-on job does not look for one, so check `actions/python-versions` by hand
before a release and bump `.python-version` when you decide to.

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
needs. The build machine must have those libraries installed; the CI job does. The
`install-linux` job of `build_artifacts.yml` installs the `.deb` in `ubuntu:24.04`,
`ubuntu:26.04` and `debian:13` containers, runs the smoke test there, and checks that the
xcb platform plugin has no missing libraries.

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

The installer is built in **two passes**, because `Uninstall.exe` is a file of its own: NSIS
cannot sign inside `makensis` when the signer is a remote service, so the uninstaller is written
first, signed, and then packed into the setup. The first pass compiles a helper that only writes
the uninstaller and quits; the second pass packs that file instead of writing it. Both passes get
the same version, because the uninstaller carries the setup's version resource.

```powershell
# Pass 1: the helper, then run it to write Uninstall.exe
makensis /DBUILD_UNINSTALLER /DAPP_VERSION="1.2.3" /DAPP_VERSION_WIN="1.2.3.0" `
    "/DUNINSTALLER_HELPER=$PWD\pyinstaller-work\uninstaller-helper.exe" `
    "/DUNINSTALLER_TARGET=$PWD\pyinstaller-work\Uninstall.exe" `
    "/DUNINSTALLER_OUT=$PWD\pyinstaller-work\Uninstall.exe" tools/packaging/windows.nsi
Start-Process .\pyinstaller-work\uninstaller-helper.exe -Wait   # a GUI program: `&` would not wait

# Pass 2: the setup, which packs pyinstaller-work\Uninstall.exe
makensis /DAPP_VERSION="1.2.3" /DAPP_VERSION_WIN="1.2.3.0" tools/packaging/windows.nsi
# Output: dist/teachers-teammate-1.2.3-w64-setup.exe
```

The three defines default to `..\..\pyinstaller-work\…` relative to `tools/packaging/`, so a
build from the repository root needs none of them. `UNINSTALLER_TARGET` is where the helper
writes at run time (`$EXEDIR\Uninstall.exe` by default), `UNINSTALLER_OUT` is the file the setup
packs at compile time.

`tools/packaging/test_windows_installer.ps1` installs, updates and uninstalls that file and
checks what is left behind; the `install-windows` job of `build_artifacts.yml` runs it on
`windows-2022` and `windows-2025` for every build. It needs an elevated shell (both runners
are), which is why its per-user runs pass `/SCOPE=user`. Its first step also reads the version
resource of the setup, of `teachers-teammate.exe` and of the installed `Uninstall.exe`: one
product name, one product version, one publisher in all three.

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

Updating an all-users copy from an installer that is not elevated shows the **Welcome page first**:
it names the installed version, whether the copy is for all users or for the current user, its
folder, and the version it will be replaced with. Administrator rights are asked for when the teacher
clicks **Next** (`WelcomePageLeave` starts the elevated copy with `/SCOPE=all /ELEVATED`); that copy
skips the pages, because they were shown, and installs at once. A silent run still exits with
code 14 instead. `tools/packaging/windows.nsi` can only be compiled and run on Windows, where the
`install-windows` job tests the silent paths; the Welcome page text is checked by hand.

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
| 10 | Teacher's Teammate is still open (a silent run waits up to 10 s for the file first) |
| 11 | an older version was refused |
| 12 | the folder holds other files (silent runs; the folder page says so otherwise) |
| 13 | would create a second copy in the other scope, or two copies exist and `/SCOPE=` is missing |
| 14 | needs administrator rights |
| 15 | Windows 10 (version 1809) or newer is required |

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
| `tools/build/check_macos_bundle.py` | Checks the minimum macOS of every Mach-O file in the `.app`; `MACOS_MINIMUM` is the one floor |
| `tools/build/make_version_resource.py` | Writes the Windows version resource the spec hands to `EXE(version=…)` |
| `tools/build/teachers-teammate.spec` | The committed PyInstaller spec: what the bundle contains |
| `tools/build/run_gui.py` | PyInstaller entry point (pip dispatch, smoke test, addon injection) |
| `tools/packaging/make_ico.py` | Generates `.ico` and welcome BMP for Windows |
| `tools/packaging/make_icns.sh` | Generates `.icns` for macOS |
| `tools/packaging/make_deb.sh` | Creates `.deb` package for Debian/Ubuntu |
| `tools/packaging/make_tarball.sh` | Creates the portable Linux `.tar.gz` |
| `tools/packaging/make_dmg.sh` | Creates `.dmg` installer for macOS |
| `tools/packaging/windows.nsi` | NSIS script for the Windows installer and, in its first pass, the uninstaller |
| `tools/release/stamp_versions.py` | Stamps version into `pyproject.toml` and Bazel `BUILD` |
| `tools/licenses/inventory.py` | Writes the license inventory, its texts and the compiled outputs; `--compile`, `--check-env`, `--check-resolved` |
| `tools/licenses/policy.toml` | The denied licenses, the display names and the accepted exceptions |
