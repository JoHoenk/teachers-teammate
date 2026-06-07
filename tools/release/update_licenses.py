#!/usr/bin/env python3
"""
Generate third-party license documentation.

Requirements:
    pip install pip-licenses

Two-step process:
  Step 1 — Collect all packages (direct + transitive) into the committed store:
      third_party_licenses/
      ├── packages.csv
      ├── packages.md
      ├── summary.md
      ├── manual_review.md
      └── licenses/
          ├── requests/
          │   └── LICENSE
          └── ...

  Step 2 — Compile the final distribution artifact:
      third_party_licenses/third_party_license.txt     ← one file, all packages
      teachers_teammate/assets/third_party_licenses.md  ← bundled into GUI

Transitive dependencies are included automatically because the script queries
all packages installed in the current environment (the dedicated .venv-licenses/
created by update_licenses.sh) rather than only the direct requirements.

For packages that don't bundle a license file in their wheel, add an entry to
tools/release/license_overrides.toml.

╔══════════════════════════════════════════════════════════════════════════╗
║  RELEASE PATH = --compile-only.  A FULL run (Step 1) is DESTRUCTIVE.      ║
╚══════════════════════════════════════════════════════════════════════════╝

The committed third_party_licenses/licenses/ store is partly HAND-CURATED. The
automatic collector only captures each package's *own* shallow license files; it
does NOT descend into the deep per-platform/vendored license trees where some
packages keep the attributions for the native libraries they statically link or
the Python deps they vendor. Those were added by hand and the renderer now
recurses into subfolders to surface them. Hand-curated packages include:

    pypdfium2  → licenses/pypdfium2/deps/        (PDFium's freetype/libjpeg/zlib/…)
    numpy      → licenses/numpy/numpy/…          (pocketfft, pcg64, SVML, highway, …)
    pip        → licenses/pip/src/pip/_vendor/…  (urllib3, requests, rich, certifi, …)
    opencv-python → licenses/opencv-python/LICENSE-3RD-PARTY.txt  (FFmpeg/libpng/…)
    PySide6    → SPDX texts + pyside6_qt_third_party.md  (Qt bundled components)

A FULL run calls prepare_output_dir() which `rmtree`s third_party_licenses/ and
re-collects from wheels — WIPING all of the above. So:

    * To REGENERATE the shipped artifacts (the normal case, e.g. before a
      release), use --compile-only. It reads the committed store and needs no
      pip env, so the hand-curated trees are preserved.
    * Only do a FULL run when dependencies actually change, and afterwards
      RE-APPLY the hand-curated trees listed above before committing.

Usage:
    python tools/release/update_licenses.py --compile-only  # release path (safe)
    python tools/release/update_licenses.py                 # full run (DESTRUCTIVE)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

TextSource = Callable[[str], list[tuple[str, str]]]


# Committed, authoritative third-party license store. A full run rewrites this
# from the packages installed in .venv-licenses (base deps + transitive = exactly
# what PyInstaller bundles), so stale entries are pruned automatically.
OUTPUT_DIR = Path("third_party_licenses")
LICENSE_DIR = OUTPUT_DIR / "licenses"

# Both LICENSE and NOTICE files are matched.
# NOTICE files record attributions required by licences like Apache-2.0.
LICENSE_AND_NOTICE_PATTERNS = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "LICENSE.rst",
    "COPYING",
    "COPYING.txt",
    "NOTICE",
    "NOTICE.txt",
    # Some wheels (e.g. opencv-python) ship the attributions for their bundled
    # native libraries (FFmpeg, libpng, …) in a separate third-party file.
    "LICENSE-3RD-PARTY.txt",
    "LICENSE-3RD-PARTY",
)

# Single-file Markdown bundled into the GUI assets so the About dialog can
# display it without any external files at runtime.
BUNDLED_MD_OUTPUT = Path("teachers_teammate/assets/third_party_licenses.md")

# Human-curated overrides: license name and/or license text for packages that
# don't bundle their license in the wheel, or where the auto-selected license
# should be replaced.  See the file for the TOML schema.
OVERRIDES_FILE = Path("tools/release/license_overrides.toml")

# Packages that are build/dev tools or the application itself — excluded from
# the license report even when present in the active environment.
# NOTE: `pip` is intentionally NOT excluded — the standalone build bundles it
# via `--collect-all pip` (in-app addon installer), so its license must ship.
_EXCLUDE: frozenset[str] = frozenset(
    {
        "setuptools",
        "wheel",
        "piplicenses",
        "pip-licenses",
        "pip_licenses",
        "teachers-teammate",
        "teachers_teammate",
    }
)

# Keyword-to-rank mapping for license permissiveness (lower = more permissive).
# Keys are checked as substrings of the lowercased license string.
# Order here is insertion order — longer/more-specific keys must come before
# shorter ones so "bsd 2" is matched before the bare "bsd" catch-all.
_LICENSE_RANK: list[tuple[str, int]] = [
    ("public domain", 0),
    ("unlicense", 0),
    ("cc0", 0),
    ("mit", 1),
    ("isc", 1),
    ("expat", 1),
    ("bsd 2", 2),
    ("bsd-2", 2),
    ("bsd 3", 3),
    ("bsd-3", 3),
    ("bsd", 3),  # bare "BSD License" → treat as BSD-3 tier
    ("apache", 4),
    ("mpl", 5),
    ("mozilla", 5),
    ("lgpl", 6),
    ("gpl", 7),
    ("agpl", 8),
]
_DEFAULT_RANK = 99


def _license_rank(s: str) -> int:
    sl = s.lower()
    for keyword, rank in _LICENSE_RANK:
        if keyword in sl:
            return rank
    return _DEFAULT_RANK


def select_most_permissive(raw: str) -> str:
    """Return the most permissive license from a semicolon-joined list.

    If *raw* contains only one entry it is returned unchanged (after stripping).
    """
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    if len(parts) <= 1:
        return raw.strip()
    return min(parts, key=_license_rank)


def load_overrides(path: Path) -> dict[str, dict]:
    """Load license_overrides.toml, returning a dict keyed by lowercase package name."""
    if not path.exists():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return {k.lower(): v for k, v in data.get("packages", {}).items()}


def get_all_installed_packages() -> list[dict]:
    """Return all packages installed in the current env, excluding build tools.

    Calling pip-licenses without --packages returns every installed distribution,
    which includes both direct requirements *and* all transitive dependencies that
    pip pulled in when the project (`pip install -e .`) was installed.
    """
    result = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json"],
        capture_output=True,
        text=True,
        check=True,
    )
    packages = json.loads(result.stdout)
    return [
        p
        for p in packages
        if p["Name"].lower() not in _EXCLUDE and p["Name"].lower().replace("-", "_") not in _EXCLUDE
    ]


def prepare_output_dir() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    LICENSE_DIR.mkdir(parents=True)


def sanitize_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)


def read_packages_from_csv(source_dir: Path = OUTPUT_DIR) -> list[dict]:
    """Read package metadata from packages.csv in *source_dir*.

    Used by --compile-only so no live pip environment is required.
    """
    csv_path = source_dir / "packages.csv"
    if not csv_path.exists():
        print(f"Error: {csv_path} not found.", file=sys.stderr)
        sys.exit(1)
    packages = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            packages.append(
                {"Name": row["Package"], "Version": row["Version"], "License": row["License"]}
            )
    return packages


def make_folder_text_source(licenses_dir: Path) -> TextSource:
    """Return a TextSource that reads from *licenses_dir*/<pkg>/ subdirectories.

    Used by --compile-only so no live pip environment is required.
    """

    def _get(pkg_name: str) -> list[tuple[str, str]]:
        pkg_dir = licenses_dir / sanitize_filename(pkg_name)
        if not pkg_dir.exists():
            return []
        result = []
        # Recurse: packages that bundle native libraries (pypdfium2, numpy) or
        # vendor Python deps (pip) keep those licenses in subfolders. The
        # relative path is used as the display name so same-named files in
        # different subdirs (e.g. several LICENSE.md) don't collide.
        for f in sorted(pkg_dir.rglob("*")):
            if f.is_file():
                try:
                    name = f.relative_to(pkg_dir).as_posix()
                    result.append((name, f.read_text(encoding="utf-8", errors="replace").rstrip()))
                except OSError:
                    pass
        return result

    return _get


def write_csv(packages: list[dict]) -> None:
    output = OUTPUT_DIR / "packages.csv"
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Package", "Version", "License"])
        for pkg in sorted(packages, key=lambda p: p["Name"].lower()):
            writer.writerow([pkg["Name"], pkg["Version"], pkg["License"]])


def write_markdown(packages: list[dict]) -> None:
    output = OUTPUT_DIR / "packages.md"
    lines = [
        "# Third-Party Packages",
        "",
        "| Package | Version | License |",
        "|---------|---------|---------|",
    ]
    for pkg in sorted(packages, key=lambda p: p["Name"].lower()):
        name = pkg["Name"].replace("|", "\\|")
        version = pkg["Version"].replace("|", "\\|")
        license_name = pkg["License"].replace("|", "\\|")
        lines.append(f"| {name} | {version} | {license_name} |")
    output.write_text("\n".join(lines), encoding="utf-8")


def write_summary(packages: list[dict]) -> None:
    output = OUTPUT_DIR / "summary.md"
    counter: Counter = Counter()
    for pkg in packages:
        counter[pkg["License"]] += 1
    lines = [
        "# License Summary",
        "",
        "| License | Count |",
        "|---------|-------|",
    ]
    for license_name, count in sorted(counter.items(), key=lambda x: (-x[1], x[0].lower())):
        lines.append(f"| {license_name} | {count} |")
    output.write_text("\n".join(lines), encoding="utf-8")


def find_license_files(dist: metadata.Distribution) -> list[Path]:
    """Try to find license and notice files inside a distribution.

    Two search strategies are combined:

    1. Legacy pattern match — any file whose bare name exactly matches one of
       ``LICENSE_AND_NOTICE_PATTERNS`` regardless of directory depth.

    2. PEP 639 layout — files under ``<name>.dist-info/licenses/`` up to two
       directory levels deep.  This covers SPDX-named files such as
       ``Apache-2.0.txt`` or ``licenses/LICENSES/BSD-3-Clause.txt``.
       Deeper subtrees (e.g. ``data/*/BUILD_LICENSES/``) are excluded because
       they contain transitive C-library attributions, not the Python package
       license itself.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    files = dist.files
    if not files:
        return found

    def _add(located: object) -> None:
        p = Path(located)  # type: ignore[arg-type]
        if p.exists() and p not in seen:
            found.append(p)
            seen.add(p)

    # Pass 1: legacy exact-filename patterns
    for file in files:
        if any(file.name.upper() == p.upper() for p in LICENSE_AND_NOTICE_PATTERNS):
            _add(dist.locate_file(file))

    # Pass 2: PEP 639 dist-info/licenses/ tree (max 2 levels inside licenses/)
    for file in files:
        parts = Path(str(file)).parts
        dist_info_idx = next(
            (i for i, p in enumerate(parts) if p.endswith(".dist-info")),
            None,
        )
        if dist_info_idx is None:
            continue
        remainder = parts[dist_info_idx + 1 :]
        if not remainder or remainder[0] != "licenses":
            continue
        if len(remainder) > 3:
            continue
        _add(dist.locate_file(file))

    return found


def copy_license_files(
    packages: list[dict],
    overrides: dict[str, dict],
) -> tuple[int, int]:
    """Copy discovered license files into LICENSE_DIR/<package>/.

    For packages covered by an override that supplies 'text', write that text
    as LICENSE.txt instead.

    Returns:
        (copied_count, missing_count)
    """
    copied = 0
    missing = 0

    for pkg in packages:
        package_name = pkg["Name"]
        override = overrides.get(package_name.lower(), {})

        try:
            dist = metadata.distribution(package_name)
        except metadata.PackageNotFoundError:
            dist = None

        license_files = find_license_files(dist) if dist else []

        if not license_files:
            override_text = override.get("text", "")
            if override_text:
                package_dir = LICENSE_DIR / sanitize_filename(package_name)
                package_dir.mkdir(exist_ok=True)
                (package_dir / "LICENSE.txt").write_text(override_text.strip(), encoding="utf-8")
                copied += 1
            else:
                missing += 1
            continue

        package_dir = LICENSE_DIR / sanitize_filename(package_name)
        package_dir.mkdir(exist_ok=True)

        for src in license_files:
            dst = package_dir / src.name
            try:
                shutil.copy2(src, dst)
                copied += 1
            except OSError:
                pass

    return copied, missing


def write_missing_report(packages: list[dict], overrides: dict[str, dict]) -> None:
    output = OUTPUT_DIR / "manual_review.md"
    lines = [
        "# Manual Review",
        "",
        "The following packages have no license or notice file bundled inside",
        "their wheel (i.e. nothing matching LICENSE*, COPYING*, or NOTICE* was",
        "found in the installed distribution). This typically means the package",
        "ships its license text only in the source distribution (sdist) or via",
        "an external repository. Please verify manually and add the relevant",
        "license text to `tools/release/license_overrides.toml`.",
        "",
        "| Package | Version | License |",
        "|---------|---------|---------|",
    ]

    for pkg in sorted(packages, key=lambda p: p["Name"].lower()):
        override = overrides.get(pkg["Name"].lower(), {})
        if override.get("text", ""):
            continue  # covered by manual override
        try:
            dist = metadata.distribution(pkg["Name"])
            if find_license_files(dist):
                continue
        except metadata.PackageNotFoundError:
            pass
        lines.append(f"| {pkg['Name']} | {pkg['Version']} | {pkg['License']} |")

    output.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Step 2 outputs
# ---------------------------------------------------------------------------

_SEPARATOR = "=" * 45


def _get_license_texts(
    pkg_name: str,
    overrides: dict[str, dict],
) -> list[tuple[str, str]]:
    """Return a list of (filename, text) pairs for the given package."""
    override = overrides.get(pkg_name.lower(), {})
    try:
        dist = metadata.distribution(pkg_name)
        license_files = find_license_files(dist)
    except metadata.PackageNotFoundError:
        license_files = []

    if license_files:
        result = []
        for file_path in sorted(license_files, key=lambda p: p.name.upper()):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace").rstrip()
                result.append((file_path.name, text))
            except OSError:
                pass
        return result

    if override.get("text", ""):
        return [("LICENSE.txt", override["text"].strip())]

    return []


def write_license_txt(
    packages: list[dict],
    overrides: dict[str, dict],
    text_source: TextSource | None = None,
    out_dir: Path = OUTPUT_DIR,
) -> None:
    """Write third_party_license.txt — one file with every package and its license text.

    Format per package:
        =============================================
        <Name>
        Version <version>
        License: <spdx>
        =============================================

        --- <filename> ---
        <full license text>

    *text_source*, if given, is called as ``text_source(pkg_name)`` and must
    return ``[(filename, text), …]``.  Defaults to querying the live pip env.
    """
    output = out_dir / "third_party_license.txt"
    _texts = text_source or (lambda n: _get_license_texts(n, overrides))
    lines: list[str] = []

    for pkg in sorted(packages, key=lambda p: p["Name"].lower()):
        name = pkg["Name"]
        version = pkg["Version"]
        license_name = pkg["License"]

        lines += [
            _SEPARATOR,
            name,
            f"Version {version}",
            f"License: {license_name}",
            _SEPARATOR,
            "",
        ]

        texts = _texts(name)
        if texts:
            for filename, text in texts:
                lines += [f"--- {filename} ---", "", text, ""]
        else:
            lines += ["[License text not available — see manual_review.md]", ""]

        lines.append("")

    output.write_text("\n".join(lines), encoding="utf-8")


def write_bundled_gui_markdown(
    packages: list[dict],
    overrides: dict[str, dict],
    text_source: TextSource | None = None,
) -> None:
    """Write a single Markdown file that embeds every license and notice text.

    The file is placed in teachers_teammate/assets/ so it can be bundled
    into the GUI executable and displayed in the Third-Party Licenses dialog.

    *text_source* has the same semantics as in :func:`write_license_txt`.
    """
    _texts = text_source or (lambda n: _get_license_texts(n, overrides))
    lines: list[str] = [
        "# Third-Party Licenses",
        "",
        "This application bundles third-party software. "
        "The full license and notice texts are reproduced below.",
        "",
    ]

    for pkg in sorted(packages, key=lambda p: p["Name"].lower()):
        name = pkg["Name"]
        version = pkg["Version"]
        license_name = pkg["License"]

        lines += [
            f"## {name} {version}",
            "",
            f"**License type:** {license_name}",
            "",
        ]

        texts = _texts(name)
        if texts:
            for filename, text in texts:
                lines += [
                    f"### {filename}",
                    "",
                    "```",
                    text,
                    "```",
                    "",
                ]
        else:
            lines += ["*License text not available — see manual_review.md.*", ""]

        lines.append("---")
        lines.append("")

    BUNDLED_MD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    BUNDLED_MD_OUTPUT.write_text("\n".join(lines), encoding="utf-8")


def apply_license_choices(
    packages: list[dict],
    overrides: dict[str, dict],
) -> list[tuple[str, str, str]]:
    """Resolve each package's License field and return a log of automatic selections.

    Mutates *packages* in place.  Returns a list of
    ``(package_name, original_license, selected_license)`` tuples for every
    package where the raw license string was a multi-value list and the script
    automatically chose one entry.  Manual overrides are applied silently.
    """
    selections: list[tuple[str, str, str]] = []

    for pkg in packages:
        name = pkg["Name"]
        raw = pkg["License"]
        key = name.lower()

        if key in overrides and "license" in overrides[key]:
            pkg["License"] = overrides[key]["license"]
        else:
            chosen = select_most_permissive(raw)
            pkg["License"] = chosen
            if chosen != raw:
                selections.append((name, raw, chosen))

    return selections


def _run_step2(
    packages: list[dict],
    overrides: dict[str, dict],
    text_source: TextSource | None,
    out_dir: Path = OUTPUT_DIR,
) -> None:
    write_license_txt(packages, overrides, text_source, out_dir)
    write_bundled_gui_markdown(packages, overrides, text_source)
    print(f"    third_party_license.txt:     {(out_dir / 'third_party_license.txt').resolve()}")
    print(f"    Bundled GUI file:            {BUNDLED_MD_OUTPUT.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate third-party license documentation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help=(
            f"Skip collection (Step 1). Read {OUTPUT_DIR}/packages.csv and "
            f"{LICENSE_DIR}/ from a previous run and regenerate the output "
            "files. No pip environment required."
        ),
    )
    args = parser.parse_args()

    overrides = load_overrides(OVERRIDES_FILE)

    if args.compile_only:
        # -----------------------------------------------------------------------
        # Step 2 only — read back the committed third_party_licenses/ store.
        # -----------------------------------------------------------------------
        source_dir = OUTPUT_DIR
        print(f"==> Compile-only: reading from {source_dir}/…")
        packages = read_packages_from_csv(source_dir)
        text_source = make_folder_text_source(source_dir / "licenses")
        print(f"    Packages read from CSV:      {len(packages)}")
        print()
        print("==> Step 2: Compiling third_party_license.txt and bundled GUI markdown…")
        _run_step2(packages, overrides, text_source, source_dir)

    else:
        # -----------------------------------------------------------------------
        # Step 1: collect
        # -----------------------------------------------------------------------
        print("==> Step 1: Collecting packages (direct + transitive)…")
        print(
            "    WARNING: a full run wipes third_party_licenses/ and re-collects\n"
            "    from wheels. Hand-curated native/vendored license trees (pypdfium2,\n"
            "    numpy, pip, opencv-python, PySide6) are NOT reproduced and must be\n"
            "    re-applied afterwards. For a normal release regeneration use\n"
            "    --compile-only instead. See this file's module docstring.",
            file=sys.stderr,
        )

        prepare_output_dir()

        packages = get_all_installed_packages()
        selections = apply_license_choices(packages, overrides)

        write_csv(packages)
        write_markdown(packages)
        write_summary(packages)

        copied, missing = copy_license_files(packages, overrides)
        write_missing_report(packages, overrides)

        print(f"    Packages found:              {len(packages)}")
        print(f"    License files copied:        {copied}")
        print(f"    Packages requiring review:   {missing}")
        print(f"    Output directory:            {OUTPUT_DIR.resolve()}")

        if selections:
            print()
            print("    Automatic license selections (multi-value → most permissive):")
            for pkg_name, original, chosen in sorted(selections):
                print(f"      {pkg_name}: {original!r} → {chosen!r}")
            print()
            print(
                f"    Review the selections above. If a different choice is needed, add an\n"
                f"    entry to {OVERRIDES_FILE} and re-run."
            )

        # -----------------------------------------------------------------------
        # Step 2: compile
        # -----------------------------------------------------------------------
        print()
        print("==> Step 2: Compiling third_party_license.txt and bundled GUI markdown…")
        _run_step2(packages, overrides, None)

    print()
    print("Done. Commit teachers_teammate/assets/third_party_licenses.md before releasing.")


if __name__ == "__main__":
    main()
