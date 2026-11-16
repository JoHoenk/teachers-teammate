#!/usr/bin/env python3
"""Write the license list of the pip add-ons, and refuse a license we must not ship.

The add-ons (spaCy, the LangChain provider packages) are installed from PyPI at runtime, so the
teacher downloads them from their authors — we are not the distributor.  But the app loads that
code into its own process and picks the versions, so the licenses still have to be known: a
GPL-only or non-commercial package in that list is a decision, not an accident.

The list is read from ``requirements_addons_lock.txt`` (the pip-compile lock of the ``llm`` and
``privacy`` extras) and each package's license from PyPI's JSON API — no install, no wheel
download.  Only what an add-on install can bring is listed: the lock is one resolution of the
whole project, so it also holds the bundle's own packages, and those are covered by ``NOTICE``.
The ``# via`` comments give the graph; the list is what the add-ons' packages reach.

The result is committed at ``teachers_teammate/assets/addon_licenses.csv``: it is bundled (the
Add-ons page shows a package's license as a tooltip) and it is reviewable in a pull request,
where a changed license shows up as a diff.

Usage (from the repo root):

    python tools/build/addon_licenses.py            # write the CSV, fail on a denied license
    python tools/build/addon_licenses.py --check    # fail when the committed CSV is stale

CI runs ``--check`` weekly (``.github/workflows/addon_resolution.yml``), so a new release of an
add-on's package that changes its license is a red run, not a surprise on release day.
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Callable, Iterable
import io
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent.parent
LOCK = ROOT / "requirements_addons_lock.txt"
OUTPUT = ROOT / "teachers_teammate" / "assets" / "addon_licenses.csv"

# The build environment installs the project, but a developer running this from a plain checkout
# has not: the repo root makes ``teachers_teammate`` importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teachers_teammate.infrastructure.addon_manager import addon_package_names  # noqa: E402

#: Longest a license string may be before it is cut: PyPI's ``license`` field sometimes holds a
#: whole license text, and the first line is the name.
_MAX_LICENSE = 60

#: Licenses the add-ons must not have.  Each is a pattern and the reason it is refused, so a red
#: run says what to do about it.  LGPL is allowed: it is not GPL-only, and nothing here is
#: statically linked into the app.
_DENIED: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bAGPL", re.IGNORECASE),
        "AGPL: the app would have to offer its own source to anyone using it over a network",
    ),
    (
        re.compile(r"\bGPL-?[23]\b|\bGPLv[23]\b", re.IGNORECASE),
        "GPL-only: loading it into the app's process is the argument we avoid",
    ),
    (
        re.compile(r"non[- ]commercial|CC[- ]BY[- ]NC", re.IGNORECASE),
        "non-commercial: a teacher may be paid for tutoring",
    ),
)

#: What :func:`license_of` returns when PyPI says nothing about a package's license.
UNKNOWN = "unknown"

#: Packages whose license PyPI does not state but a person has read and accepted (name → why).
#: Empty on purpose: an ``unknown`` license is refused until it is listed here with a reason, so
#: the gate is closed exactly where it has no information.
UNKNOWN_ALLOWED: dict[str, str] = {}

#: ``(name, version) -> the package's PyPI JSON ``info`` block``.  Replaced in tests.
FetchInfo = Callable[[str, str], dict]


def _pypi_info(name: str, version: str) -> dict:
    """Return PyPI's ``info`` block for one release of *name*."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 — a fixed https host
        return json.load(response)["info"]


def license_of(info: dict) -> str:
    """Return the license of a package as one short line.

    PEP 639's ``license_expression`` wins, then the ``License ::`` classifier (the field PyPI
    shows), then the free-text ``license`` field's first line.
    """
    expression = str(info.get("license_expression") or "").strip()
    if expression:
        return expression
    classifiers = [
        str(entry) for entry in info.get("classifiers") or [] if str(entry).startswith("License ::")
    ]
    if classifiers:
        return classifiers[0].split("::")[-1].strip()
    text = str(info.get("license") or "").strip()
    if text:
        return text.splitlines()[0].strip()[:_MAX_LICENSE]
    return UNKNOWN


def _normalise(name: str) -> str:
    """Return the PEP 503 form of a distribution name (``PySide6_Essentials`` → ``pyside6-essentials``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _graph(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return the pins of a pip-compile file and, per package, what pulled it in.

    The ``# via`` comments hold the graph in two shapes: ``# via typer`` on one line, and a
    ``# via`` line followed by one indented ``#   name`` line per parent.  A parent may carry a
    note (``teachers-teammate (pyproject.toml)``), so only its first word is the name.
    """
    versions: dict[str, str] = {}
    parents: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if current is None:
                continue  # the file's header, before the first pin
            entry = stripped.lstrip("#").strip()
            if entry.startswith("via"):
                entry = entry[len("via") :].strip()
            if entry:
                parents[current].append(_normalise(entry.split()[0]))
            continue
        if stripped.startswith("--"):
            continue
        name, separator, version = stripped.rstrip("\\").strip().partition("==")
        if separator and version:
            current = _normalise(name.partition("[")[0].strip())
            versions[current] = version.strip()
            parents.setdefault(current, [])
    return versions, parents


def addon_closure(text: str) -> list[tuple[str, str]]:
    """Return ``(name, version)`` of what an add-on install can bring, in file order.

    The lock resolves the whole project, so it also holds the packages the bundle ships.  Start
    at the add-ons' own packages and follow the ``# via`` graph down: that is the set a teacher
    downloads, and the set whose licenses this script has to know.
    """
    versions, parents = _graph(text)
    children: dict[str, list[str]] = {}
    for child, needs in parents.items():
        for parent in needs:
            children.setdefault(parent, []).append(child)

    roots = addon_package_names()
    reached: set[str] = set()
    queue = [root for root in roots if root in versions]
    while queue:
        name = queue.pop()
        if name in reached:
            continue
        reached.add(name)
        queue.extend(children.get(name, []))
    return [(name, version) for name, version in versions.items() if name in reached]


def collect(locked: Iterable[tuple[str, str]], fetch: FetchInfo | None = None) -> list[tuple]:
    """Return ``(name, version, license)`` for every locked package, sorted by name."""
    fetch = fetch or _pypi_info
    rows: list[tuple[str, str, str]] = []
    for name, version in locked:
        try:
            info = fetch(name, version)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            raise SystemExit(f"Could not read the license of {name} {version}: {exc}") from exc
        rows.append((name, version, license_of(info)))
    return sorted(rows)


def denied_reason(license_text: str) -> str | None:
    """Return why *license_text* is refused, or ``None`` when it is acceptable.

    An ``OR`` expression is acceptable as soon as one alternative is: the package can be used
    under that one (``LGPL-3.0-only OR GPL-3.0-only`` is fine, ``GPL-3.0-only OR AGPL-3.0`` is
    not).  ``AND`` is not split — every part then applies.
    """
    alternatives = [
        part.strip() for part in re.split(r"\s+OR\s+", license_text, flags=re.IGNORECASE)
    ]
    reasons: list[str] = []
    for alternative in alternatives:
        matched = [reason for pattern, reason in _DENIED if pattern.search(alternative)]
        if not matched:
            return None
        reasons.extend(matched)
    return reasons[0] if reasons else None


def denied(rows: Iterable[tuple[str, str, str]]) -> list[str]:
    """Return one sentence per package whose license the add-ons must not have."""
    problems: list[str] = []
    for name, version, license_text in rows:
        reason = denied_reason(license_text)
        if reason is None and license_text == UNKNOWN and name not in UNKNOWN_ALLOWED:
            reason = (
                "PyPI states no license: read it, then list the package in UNKNOWN_ALLOWED "
                "with the reason it is acceptable"
            )
        if reason is not None:
            problems.append(f"{name} {version} is {license_text} — {reason}")
    return problems


def render(rows: Iterable[tuple[str, str, str]]) -> str:
    """Return the CSV text of *rows*."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Package", "Version", "License"])
    writer.writerows(rows)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    """Write the license list (or check it); return 1 when a license is denied or the list is stale."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write: fail when the committed list differs from what PyPI says",
    )
    arguments = parser.parse_args(argv)

    rows = collect(addon_closure(LOCK.read_text(encoding="utf-8")))
    problems = denied(rows)
    if problems:
        print("Denied licenses among the add-ons:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    text = render(rows)
    if arguments.check:
        committed = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if committed != text:
            print(
                f"{OUTPUT.relative_to(ROOT)} is out of date: run tools/build/addon_licenses.py",
                file=sys.stderr,
            )
            return 1
        print(f"{len(rows)} add-on licenses are up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"Wrote {len(rows)} add-on licenses to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
