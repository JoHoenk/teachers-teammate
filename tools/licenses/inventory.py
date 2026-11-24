#!/usr/bin/env python3
"""One inventory of every third-party part the app ships or installs.

``third_party_licenses/inventory.csv`` is the single list: one row per component, with the
version it is pinned to, the license it states, and whether it is inside our download
(``bundle``), installed on the teacher's click (``addon``) or only needed by the build
(``build``).  Nothing else in the repository lists licenses.

Versions come from the locks, never from a hand edit: a row changes only when this tool runs
after a lock changed.  The tool reads the app's own lists — ``[project] dependencies``,
``addon_manager.addon_package_names()``, the ``LocalModel`` sets — instead of keeping a copy of
them.

How a part reaches the teacher decides what we owe, not what kind of part it is.  Inside our
download: the full texts, in every download and in the in-app dialog.  Downloaded from its
author on the teacher's click: a known license that passes the deny rules, shown as a tooltip.
Same list, same rules.

Generated and hand-written files are kept apart: ``texts/`` and the compiled outputs belong to
this tool, ``curated/`` belongs to a person and is never touched, so no run is destructive.

Usage (from the repo root):

    python tools/licenses/inventory.py            # refresh the inventory and the texts
    python tools/licenses/inventory.py --compile  # rebuild the outputs from the committed store

The first form needs the network (PyPI); ``--compile`` and the checks in
``tests/unit/test_license_inventory.py`` do not.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
import csv
from dataclasses import dataclass, replace
import hashlib
from importlib import metadata
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import tomllib
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "third_party_licenses"
INVENTORY = STORE / "inventory.csv"
TEXTS = STORE / "texts"
CURATED = STORE / "curated"
COMPILED = STORE / "third_party_license.txt"
NOTICE = ROOT / "NOTICE"
LICENSE = ROOT / "LICENSE"
ASSET_MD = ROOT / "teachers_teammate" / "assets" / "third_party_licenses.md"
ASSET_CSV = ROOT / "teachers_teammate" / "assets" / "third_party_inventory.csv"
ASSET_LICENSE = ROOT / "teachers_teammate" / "assets" / "LICENSE.txt"
POLICY = Path(__file__).resolve().parent / "policy.toml"
TEST_LOCK = ROOT / "requirements_test_lock.txt"
ADDON_LOCK = ROOT / "requirements_addons_lock.txt"
PLATFORM_PINS = ROOT / "tools" / "build" / "requirements_platform.txt"
PYPROJECT = ROOT / "pyproject.toml"
PYTHON_VERSION = ROOT / ".python-version"

#: Where spaCy publishes the metadata of each model release: one JSON per model and version.
SPACY_META_URL = "https://raw.githubusercontent.com/explosion/spacy-models/master/meta"

# The build environment installs the project, but a developer running this from a plain checkout
# has not: the repo root makes ``teachers_teammate`` importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from teachers_teammate.domain.local_models import (  # noqa: E402
    PAGE_MODELS,
    READER_MODELS,
)
from teachers_teammate.infrastructure.addon_manager import addon_package_names  # noqa: E402
from teachers_teammate.infrastructure.spacy_models import SPACY_MODEL_PINS  # noqa: E402

#: The three ways a part reaches a teacher, and what each one owes.
SCOPES = ("bundle", "addon", "build")

#: What a row describes: a pip package, the interpreter, or a model file set.
KINDS = ("package", "interpreter", "model")

#: What :func:`license_of` returns when PyPI says nothing about a package's license.
UNKNOWN = "unknown"

#: Distributions in the build environment that are not third-party parts: the project itself.
_EXEMPT = frozenset({"teachers-teammate"})

#: Longest a license string may be before it is cut: PyPI's ``license`` field sometimes holds a
#: whole license text, and the first line is the name.
_MAX_LICENSE = 60

#: The platform whose wheel the texts are read from unless the policy names another one.
DEFAULT_PLATFORM = "manylinux"

#: What a wheel file name must contain to be the one for a platform, and how the compiled text
#: names it.
_PLATFORM_MARKERS: dict[str, tuple[str, ...]] = {
    "manylinux": ("manylinux", "x86_64"),
    "win_amd64": ("win_amd64",),
    "macosx_arm64": ("macosx", "arm64"),
}
_PLATFORM_LABELS: dict[str, str] = {"win_amd64": "Windows", "macosx_arm64": "macOS"}

#: The file names a wheel's license and notice files have (``find_license_files``'s rule).
_LICENSE_NAMES = frozenset(
    name.upper()
    for name in (
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.md",
        "LICENSE.rst",
        "COPYING",
        "COPYING.txt",
        "NOTICE",
        "NOTICE.txt",
        "LICENSE-3RD-PARTY.txt",
        "LICENSE-3RD-PARTY",
    )
)

#: How many path parts below ``<name>.dist-info/`` a text may sit: ``licenses/`` plus two folders
#: is the PEP 639 layout.  pypdfium2 groups its vendored attributions one folder deeper, in
#: ``licenses/BUILD_LICENSES/<group>/``, and its own rule is what picks the groups to keep.
_LICENSE_DEPTH = 3
_LICENSE_DEPTHS: dict[str, int] = {"pypdfium2": 4}

#: ``(name, version) -> the package's PyPI JSON ``info`` block``.  Replaced in tests.
FetchInfo = Callable[[str, str], dict]

#: ``(name, version) -> PyPI's ``urls`` list``.  Replaced in tests.
FetchFiles = Callable[[str, str], list[dict]]


@dataclass(frozen=True)
class Row:
    """One component of the inventory."""

    component: str
    version: str
    license: str
    scope: str
    kind: str = "package"


@dataclass(frozen=True)
class Policy:
    """What ``policy.toml`` says: the deny rules and the facts a person has to state."""

    deny: tuple[tuple[re.Pattern[str], str], ...]
    names: dict[str, str]
    licenses: dict[str, str]
    accepted: dict[str, str]
    texts: dict[str, str]
    variants: dict[str, tuple[str, ...]]
    build_only: tuple[str, ...]
    host_deny: tuple[str, ...]
    host_rows: dict[str, str]


@dataclass(frozen=True)
class Stamp:
    """A hand-owned folder of texts, and the rows and version it was written for."""

    folder: Path
    rows: tuple[str, ...]
    valid_for: str


def load_policy(path: Path = POLICY) -> Policy:
    """Return the policy written in *path*."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return Policy(
        deny=tuple(
            (re.compile(entry["pattern"], re.IGNORECASE), entry["reason"])
            for entry in data.get("deny", [])
        ),
        names=dict(data.get("names", {})),
        licenses=dict(data.get("license", {})),
        accepted=dict(data.get("accepted", {})),
        texts=dict(data.get("text", {})),
        variants={name: tuple(tags) for name, tags in data.get("variants", {}).items()},
        build_only=tuple(data.get("build_only", {}).get("packages", [])),
        host_deny=tuple(data.get("host_deny", {}).get("patterns", [])),
        host_rows=dict(data.get("host_rows", {})),
    )


def _normalise(name: str) -> str:
    """Return the PEP 503 form of a distribution name (``PySide6_Essentials`` → ``pyside6-essentials``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: str) -> str:
    """Return the normalised name of a PEP 508 requirement string."""
    return _normalise(re.split(r"[<>=!~\[;,\s]", requirement, maxsplit=1)[0])


def _pypi_info(name: str, version: str) -> dict:
    """Return PyPI's ``info`` block for one release of *name*."""
    return _pypi_json(name, version)["info"]


def _pypi_files(name: str, version: str) -> list[dict]:
    """Return PyPI's ``urls`` list for one release of *name*."""
    return _pypi_json(name, version)["urls"]


def _pypi_json(name: str, version: str) -> dict:
    """Return PyPI's JSON document for one release of *name*."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _spacy_meta(name: str, version: str) -> dict:
    """Return the meta JSON of one spaCy model release."""
    url = f"{SPACY_META_URL}/{name}-{version}.json"
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


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


def closure(text: str, roots: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``(name, version)`` of what *roots* reach through a lock's ``# via`` graph.

    A lock resolves the whole project, so it also holds packages the roots do not reach.  Start
    at the roots and follow the graph down: that is the set a teacher gets, and the set whose
    licenses this tool has to know.
    """
    versions, parents = _graph(text)
    children: dict[str, list[str]] = {}
    for child, needs in parents.items():
        for parent in needs:
            children.setdefault(parent, []).append(child)

    reached: set[str] = set()
    queue = [root for root in (_normalise(root) for root in roots) if root in versions]
    while queue:
        name = queue.pop()
        if name in reached:
            continue
        reached.add(name)
        queue.extend(children.get(name, []))
    return [(name, version) for name, version in versions.items() if name in reached]


def lock_hashes(text: str) -> dict[str, set[str]]:
    """Return the ``sha256:`` digests a pip-compile lock states per package."""
    hashes: dict[str, set[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip().rstrip("\\").strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("--hash="):
            if current is not None:
                hashes[current].add(stripped[len("--hash=") :].strip())
            continue
        name, separator, _version = stripped.partition("==")
        if separator:
            current = _normalise(name.partition("[")[0].strip())
            hashes.setdefault(current, set())
    return hashes


def bundle_roots(root: Path = ROOT) -> list[str]:
    """Return the normalised names of the app's own dependencies."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [_requirement_name(entry) for entry in data["project"]["dependencies"]]


def bundle_rows(root: Path = ROOT) -> list[Row]:
    """Return the bundle's package rows: the closure of the app's own dependencies."""
    locked = closure(
        (root / "requirements_test_lock.txt").read_text(encoding="utf-8"), bundle_roots(root)
    )
    return [Row(name, version, "", "bundle") for name, version in locked]


def platform_pins(root: Path = ROOT) -> dict[str, str]:
    """Return the pins of ``requirements_platform.txt``."""
    return _pins(root / "tools" / "build" / "requirements_platform.txt")


def addon_platform_pins(root: Path = ROOT) -> dict[str, str]:
    """Return the pins of ``requirements_addons_platform.txt``.

    The add-on lock is resolved on Linux, so a package only Windows or macOS needs (``click``
    brings ``colorama`` there) is not in it.  This file is where such a package is listed, and
    the weekly add-on resolution check is what proves it is still the version pip resolves.
    """
    return _pins(root / "tools" / "build" / "requirements_addons_platform.txt")


def _pins(path: Path) -> dict[str, str]:
    """Return the ``name → version`` pins of a requirements file of plain pins."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, separator, version = stripped.partition("==")
        if separator:
            pins[_normalise(name)] = version.strip()
    return pins


def platform_rows(policy: Policy, root: Path = ROOT) -> list[Row]:
    """Return the platform pins: a bundle row each, except the build-only ones.

    A build-only package that is no platform pin but is locked (``setuptools``, which
    PyInstaller needs and the spec leaves out of the bundle) gets its build row at the
    lock's version, so the build environment check finds it.
    """
    pins = platform_pins(root)
    rows = [
        Row(name, version, "", "build" if name in policy.build_only else "bundle")
        for name, version in pins.items()
    ]
    versions, _parents = _graph((root / "requirements_test_lock.txt").read_text(encoding="utf-8"))
    rows += [
        Row(name, versions[name], "", "build")
        for name in policy.build_only
        if name not in pins and name in versions
    ]
    return rows


def addon_rows(lock: Path = ADDON_LOCK, shipped: Iterable[str] = ()) -> list[Row]:
    """Return the add-on rows: what an add-on install can bring, minus the bundle's own packages.

    A package the bundle carries is a bundle row at the bundle's version: the add-on install is
    pinned to that version, so the add-on lock's one never arrives.
    """
    carried = set(shipped)
    locked = closure(lock.read_text(encoding="utf-8"), addon_package_names())
    return [Row(name, version, "", "addon") for name, version in locked if name not in carried]


def interpreter_row(root: Path = ROOT) -> Row:
    """Return the one row for the interpreter the bundle carries."""
    version = (root / ".python-version").read_text(encoding="utf-8").strip()
    return Row("python", version, "PSF-2.0", "bundle", "interpreter")


def model_rows() -> list[Row]:
    """Return one row per model file set the app runs itself."""
    return [
        Row(model.key, model.short_revision, model.licence, "bundle", "model")
        for model in (*READER_MODELS, *PAGE_MODELS)
    ]


def spacy_rows() -> list[Row]:
    """Return one add-on row per spaCy model the app offers, at its pinned version."""
    return [
        Row(model, version, "", "addon", "model") for model, version in SPACY_MODEL_PINS.items()
    ]


def expected(policy: Policy, root: Path = ROOT) -> list[Row]:
    """Return every row the locks and the app's own lists say, without the licenses.

    *root* is the checkout the locks are read from; the tests point it at Bazel's runfiles.
    """
    bundle = bundle_rows(root)
    shipped = {row.component for row in bundle} | set(platform_pins(root))
    addons = addon_rows(root / "requirements_addons_lock.txt", shipped)
    locked = {row.component for row in addons}
    addons += [
        Row(name, version, "", "addon")
        for name, version in addon_platform_pins(root).items()
        if name not in shipped and name not in locked
    ]
    return _sorted(
        [
            *bundle,
            *platform_rows(policy, root),
            *addons,
            interpreter_row(root),
            *model_rows(),
            *spacy_rows(),
        ]
    )


def _sorted(rows: Iterable[Row]) -> list[Row]:
    """Return *rows* in the order the inventory is written in."""
    return sorted(rows, key=lambda row: (row.component, row.scope, row.kind))


def collect(
    rows: Iterable[Row],
    fetch: FetchInfo | None = None,
    fetch_model: FetchInfo | None = None,
) -> list[Row]:
    """Return *rows* with the license of every package and spaCy model read from its publisher.

    A package's license comes from PyPI, a spaCy model's from the meta JSON of its release.  A
    model row that already states a license (the app's own model files) is left alone.
    """
    fetch = fetch or _pypi_info
    fetch_model = fetch_model or _spacy_meta
    filled: list[Row] = []
    for row in rows:
        if row.kind == "package":
            source = fetch
        elif row.kind == "model" and not row.license:
            source = fetch_model
        else:
            filled.append(row)
            continue
        try:
            info = source(row.component, row.version)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            raise SystemExit(
                f"Could not read the license of {row.component} {row.version}: {exc}"
            ) from exc
        filled.append(replace(row, license=license_of(info)))
    return filled


def apply_policy(rows: Iterable[Row], policy: Policy) -> list[Row]:
    """Return *rows* with the policy's spellings and component licenses applied."""
    applied: list[Row] = []
    for row in rows:
        license_text = policy.licenses.get(row.component, row.license)
        applied.append(replace(row, license=policy.names.get(license_text, license_text)))
    return _sorted(applied)


def denied_reason(license_text: str, deny: Iterable[tuple[re.Pattern[str], str]]) -> str | None:
    """Return why *license_text* is refused, or ``None`` when it is acceptable.

    An ``OR`` expression is acceptable as soon as one alternative is: the package can be used
    under that one (``LGPL-3.0-only OR GPL-3.0-only`` is fine, ``GPL-3.0-only OR AGPL-3.0`` is
    not).  ``AND`` is not split — every part then applies.  Only the upper-case SPDX operator
    splits: a free-text "or later" is part of one license's name.
    """
    deny = tuple(deny)
    alternatives = [part.strip() for part in re.split(r"\s+OR\s+", license_text)]
    reasons: list[str] = []
    for alternative in alternatives:
        matched = [reason for pattern, reason in deny if pattern.search(alternative)]
        if not matched:
            return None
        reasons.extend(matched)
    return reasons[0] if reasons else None


def problems(rows: Iterable[Row], policy: Policy) -> list[str]:
    """Return one sentence per row whose license we must not ship or install."""
    found: list[str] = []
    for row in rows:
        if row.component in policy.accepted:
            continue
        reason = denied_reason(row.license, policy.deny)
        if reason is None and row.license == UNKNOWN:
            reason = (
                "PyPI states no license: read it, then list the component in [accepted] "
                "with the reason it is acceptable"
            )
        if reason is not None:
            found.append(f"{row.component} {row.version} is {row.license} — {reason}")
    return found


def installed_distributions() -> list[tuple[str, str]]:
    """Return ``(normalised name, version)`` of every distribution in this environment."""
    return [
        (_normalise(distribution.metadata["Name"] or ""), distribution.version)
        for distribution in metadata.distributions()
    ]


def environment_problems(
    rows: Iterable[Row], distributions: Iterable[tuple[str, str]] | None = None
) -> list[str]:
    """Return one sentence per installed distribution the inventory does not list.

    The build environment is what PyInstaller freezes, so a distribution in it that is not a
    ``bundle`` or ``build`` row at the same version would reach the download unlisted — and a
    row at another version would make the license list describe something else.
    """
    listed = {row.component: row.version for row in rows if row.scope in ("bundle", "build")}
    found: list[str] = []
    for name, version in installed_distributions() if distributions is None else distributions:
        if name in _EXEMPT:
            continue
        wanted = listed.get(name)
        if wanted is None:
            found.append(f"{name}=={version} is installed but is no bundle or build row")
        elif wanted != version:
            found.append(f"{name}=={version} is installed, but the inventory lists {wanted}")
    return sorted(found)


def resolved_distributions(report: Path) -> list[tuple[str, str]]:
    """Return ``(normalised name, version)`` of what pip's ``--dry-run --report`` would install.

    *report* is one report file, or a folder of them (``check_addon_resolution.py --report``
    writes one per add-on and mode).
    """
    paths = sorted(report.glob("*.json")) if report.is_dir() else [report]
    found: list[tuple[str, str]] = []
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        for entry in document.get("install", []):
            info = entry.get("metadata", {})
            found.append((_normalise(info.get("name", "")), info.get("version", "")))
    return found


def resolved_problems(rows: Iterable[Row], distributions: Iterable[tuple[str, str]]) -> list[str]:
    """Return one sentence per resolved distribution the inventory does not list.

    The add-on lock is resolved on Linux, so a dependency that only exists on Windows or macOS
    never appears in it.  This is the check that finds one: what pip would install on this OS
    must be a ``bundle`` or ``addon`` row at that version.
    """
    listed = {row.component: row.version for row in rows if row.scope in ("bundle", "addon")}
    found: list[str] = []
    for name, version in distributions:
        if name in _EXEMPT:
            continue
        wanted = listed.get(name)
        if wanted is None:
            found.append(f"{name}=={version} would be installed but is no bundle or add-on row")
        elif wanted != version:
            found.append(f"{name}=={version} would be installed, but the inventory lists {wanted}")
    return sorted(set(found))


def render(rows: Iterable[Row]) -> str:
    """Return the CSV text of *rows*."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Component", "Version", "License", "Scope", "Kind"])
    for row in rows:
        writer.writerow([row.component, row.version, row.license, row.scope, row.kind])
    return buffer.getvalue()


def read_inventory(path: Path = INVENTORY) -> list[Row]:
    """Return the rows of the committed inventory."""
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            Row(
                row["Component"].strip(),
                row["Version"].strip(),
                row["License"].strip(),
                row["Scope"].strip(),
                row["Kind"].strip(),
            )
            for row in csv.DictReader(handle)
        ]


def write_inventory(rows: Iterable[Row], path: Path = INVENTORY) -> None:
    """Write *rows* to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rows), encoding="utf-8")


def write_asset(rows: Iterable[Row], path: Path = ASSET_CSV) -> None:
    """Write the copy of the inventory the app reads (the Add-ons page's tooltips)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rows), encoding="utf-8")


def _read_text(path: Path) -> str:
    """Return the text of a license file, falling back to cp1252 for the ones that are not UTF-8."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


#: The macOS the bundle is built on: pip picks the newest wheel tagged for this or an older one.
MACOS_TARGET = (15, 0)

_WHEEL_TAGS = re.compile(r"-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$")


def _python_tag(root: Path = ROOT) -> str:
    """Return the CPython tag of the interpreter the bundle carries (``cp314``)."""
    major, minor = (root / ".python-version").read_text(encoding="utf-8").strip().split(".")[:2]
    return f"cp{major}{minor}"


def _fits_python(filename: str, python: str) -> bool:
    """Return whether a wheel runs on the bundle's (GIL) CPython."""
    match = _WHEEL_TAGS.search(filename)
    if match is None:
        return False
    abi = match["abi"]
    if abi in ("none", "abi3"):
        return match["py"].startswith(("py", "cp"))
    return abi == python and match["py"] == python


def _macos_version(filename: str) -> tuple[int, int]:
    """Return the macOS version in a wheel's platform tag, ``(0, 0)`` when it names none."""
    found = re.search(r"macosx_(\d+)_(\d+)", filename)
    return (int(found[1]), int(found[2])) if found else (0, 0)


def wheel_url(files: list[dict], platform: str, python: str | None = None) -> dict:
    """Return PyPI's entry for the wheel the bundle of *platform* ships.

    The wheel must fit the bundle's CPython (``cp314``, not the free-threaded ``cp314t``) and the
    platform; among macOS wheels the newest one a ``macosx_15_0`` build accepts wins, as with pip.
    Without a platform wheel the universal one is used.
    """
    python = python or _python_tag()
    markers = _PLATFORM_MARKERS[platform]
    wheels = [
        entry
        for entry in files
        if entry["filename"].endswith(".whl") and _fits_python(entry["filename"], python)
    ]
    matching = [e for e in wheels if all(marker in e["filename"] for marker in markers)]
    if platform.startswith("macosx"):
        matching = [e for e in matching if _macos_version(e["filename"]) <= MACOS_TARGET]
        matching.sort(key=lambda e: _macos_version(e["filename"]), reverse=True)
    if matching:
        return matching[0]
    for entry in wheels:
        if entry["filename"].endswith("-none-any.whl"):
            return entry
    raise SystemExit(
        f"no {platform} wheel for {python} and no universal wheel among "
        f"{[e['filename'] for e in files]}"
    )


def wheel_license_files(wheel: Path, depth: int = _LICENSE_DEPTH) -> dict[str, bytes]:
    """Return ``{relative name: content}`` of the license files a wheel carries.

    Two rules: a file whose bare name is one
    of :data:`_LICENSE_NAMES` at any depth (kept with its path in the wheel), and the PEP 639 ``<name>.dist-info/licenses/`` tree
    up to *depth* path parts.  Deeper subtrees are a package's own vendored attributions, which
    the package's license file names rather than reproduces.
    """
    found: dict[str, bytes] = {}
    with zipfile.ZipFile(wheel) as archive:
        for entry in archive.namelist():
            if entry.endswith("/"):
                continue
            parts = Path(entry).parts
            name = _license_relative_name(parts, depth)
            if name is not None:
                found[name] = archive.read(entry)
    return found


def _license_relative_name(parts: tuple[str, ...], depth: int) -> str | None:
    """Return where a wheel entry belongs in ``texts/<component>/``, or ``None`` when it is no text.

    A file inside ``<name>.dist-info/`` is named by what follows the folder.  Any other file keeps
    its whole path inside the wheel: a package's vendored libraries each carry a ``LICENSE``, and
    a bare name would let the last one overwrite the others.
    """
    index = next((i for i, part in enumerate(parts) if part.endswith(".dist-info")), None)
    if index is None:
        return "/".join(parts) if parts[-1].upper() in _LICENSE_NAMES else None
    remainder = parts[index + 1 :]
    if not remainder:
        return None
    if remainder[0] == "licenses":
        return "/".join(remainder[1:]) if 1 < len(remainder) <= depth else None
    return remainder[0] if len(remainder) == 1 and remainder[0].upper() in _LICENSE_NAMES else None


def wheel_texts(component: str, wheel: Path) -> dict[str, bytes]:
    """Return the texts a wheel carries, in the layout ``texts/<component>/`` uses."""
    contents = wheel_license_files(wheel, _LICENSE_DEPTHS.get(component, _LICENSE_DEPTH))
    return pypdfium2_texts(contents) if component == "pypdfium2" else contents


def pypdfium2_texts(contents: dict[str, bytes]) -> dict[str, bytes]:
    """Return pypdfium2's texts in the layout its wheel's ``BUILD_LICENSES/`` needs.

    The one code rule of the store: ``BUILD_LICENSES/`` goes flat into ``deps/``, keeping the
    top-level files and the two folders that apply to the shipped build, and every
    ``#include ./SHARED/Apache-2.0.txt`` line is replaced by that text — the wheel's own
    ``LICENSES/`` files stay where they are.
    """
    shared = contents.get("BUILD_LICENSES/SHARED/Apache-2.0.txt", b"")
    kept = ("all_except_musl/", "pbin_and_sbuild/")
    result: dict[str, bytes] = {}
    for name, data in contents.items():
        if not name.startswith("BUILD_LICENSES/"):
            result[name] = data
            continue
        inner = name[len("BUILD_LICENSES/") :]
        if inner.startswith("SHARED/"):
            continue
        if "/" in inner and not inner.startswith(kept):
            continue
        text = data.decode("utf-8", errors="replace")
        text = re.sub(
            r"^#include\s+\.\.?/SHARED/Apache-2\.0\.txt\s*$",
            lambda _match: shared.decode("utf-8", errors="replace"),
            text,
            flags=re.MULTILINE,
        )
        result[f"deps/{inner}"] = text.encode("utf-8")
    return result


def _text_folder(component: str, platform: str) -> str:
    """Return the folder name of one component's texts on one platform."""
    return component if platform == DEFAULT_PLATFORM else f"{component}@{platform}"


def collect_texts(
    rows: Iterable[Row],
    policy: Policy,
    store: Path = STORE,
    fetch_files: FetchFiles | None = None,
    hashes: dict[str, set[str]] | None = None,
) -> None:
    """Rewrite ``texts/`` from the locked wheels, and drop the folders of rows that are gone.

    Every wheel is read before anything on disk changes, so a network error leaves the old
    store as it was.
    """
    fetch_files = fetch_files or _pypi_files
    hashes = hashes or {}
    texts = store / "texts"
    collected: dict[str, dict[str, bytes]] = {}
    for row in rows:
        if row.scope != "bundle" or row.kind != "package":
            continue
        files = fetch_files(row.component, row.version)
        for platform in (DEFAULT_PLATFORM, *policy.variants.get(row.component, ())):
            entry = wheel_url(files, platform)
            collected[_text_folder(row.component, platform)] = _wheel_texts(row, entry, hashes)

    if texts.is_dir():
        for folder in texts.iterdir():
            if folder.is_dir() and folder.name not in collected:
                shutil.rmtree(folder)
    for name, contents in collected.items():
        folder = texts / name
        if folder.is_dir():
            shutil.rmtree(folder)
        # A wheel without a license file: the row's text comes from `[text]` or from a folder
        # under `curated/`, and the store test is what proves it has one.
        for relative, data in contents.items():
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def _wheel_texts(row: Row, entry: dict, hashes: dict[str, set[str]]) -> dict[str, bytes]:
    """Download one wheel, check its digest, and return the license files it carries."""
    with tempfile.TemporaryDirectory(prefix="license-wheel-") as temporary:
        wheel = Path(temporary) / entry["filename"]
        with urllib.request.urlopen(entry["url"], timeout=300) as response:
            wheel.write_bytes(response.read())
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest != entry["digests"]["sha256"]:
            raise SystemExit(
                f"{entry['filename']} does not match the digest PyPI states for it "
                f"({digest} != {entry['digests']['sha256']})"
            )
        locked = hashes.get(row.component)
        if locked and f"sha256:{digest}" not in locked:
            raise SystemExit(
                f"{entry['filename']} is not one of the files the lock pins for "
                f"{row.component} {row.version}"
            )
        return wheel_texts(row.component, wheel)


def stamps(store: Path = STORE) -> list[Stamp]:
    """Return the stamps of the hand-owned folders under ``curated/``."""
    found: list[Stamp] = []
    for path in sorted((store / "curated").glob("*/STAMP.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        found.append(Stamp(path.parent, tuple(data["rows"]), str(data.get("valid_for", ""))))
    return found


def texts_of(component: str, policy: Policy, store: Path = STORE) -> list[tuple[str, str]]:
    """Return ``(label, text)`` of every text file a bundle row owes."""
    entries: list[tuple[str, str]] = []
    for platform in (DEFAULT_PLATFORM, *policy.variants.get(component, ())):
        folder = store / "texts" / _text_folder(component, platform)
        if not folder.is_dir():
            continue
        label = _PLATFORM_LABELS.get(platform, "")
        prefix = f"{label} only: " if label else ""
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                entries.append((prefix + str(path.relative_to(folder)), _read_text(path)))
    for stamp in stamps(store):
        if component not in stamp.rows:
            continue
        for path in sorted(stamp.folder.rglob("*")):
            if path.is_file() and path.name != "STAMP.toml":
                entries.append(
                    (f"{stamp.folder.name}/{path.relative_to(stamp.folder)}", _read_text(path))
                )
    if component in policy.texts:
        entries.append(("LICENSE.txt", policy.texts[component].strip() + "\n"))
    return entries


def _notice() -> str:
    """Return the NOTICE text every compiled output opens with."""
    return NOTICE.read_text(encoding="utf-8").rstrip() + "\n"


def compile_text(rows: Iterable[Row], policy: Policy, store: Path = STORE) -> str:
    """Return the compiled ``third_party_license.txt``: NOTICE, then every bundle row's texts."""
    parts = [_notice()]
    for row in rows:
        if row.scope != "bundle":
            continue
        rule = "=" * 45
        parts.append(
            f"\n{rule}\n{row.component}\nVersion {row.version}\nLicense: {row.license}\n{rule}\n"
        )
        for label, text in texts_of(row.component, policy, store):
            parts.append(f"\n--- {label} ---\n\n{text.rstrip()}\n")
    return "".join(parts)


def compile_markdown(rows: Iterable[Row], policy: Policy, store: Path = STORE) -> str:
    """Return the in-app license page: NOTICE, then every bundle row with its texts."""
    parts = ["# Third-Party Licenses\n\n", _notice(), "\n---\n"]
    for row in rows:
        if row.scope != "bundle":
            continue
        parts.append(f"\n## {row.component} {row.version}\n\n**License type:** {row.license}\n")
        for label, text in texts_of(row.component, policy, store):
            parts.append(f"\n### {label}\n\n```\n{text.rstrip()}\n```\n")
    return "".join(parts)


def write_compiled(
    rows: Iterable[Row],
    policy: Policy,
    store: Path = STORE,
    compiled: Path = COMPILED,
    asset_md: Path = ASSET_MD,
    asset_license: Path = ASSET_LICENSE,
    license_file: Path = LICENSE,
) -> None:
    """Write the compiled outputs, and the copy of our own license the app shows."""
    rows = list(rows)
    compiled.parent.mkdir(parents=True, exist_ok=True)
    compiled.write_text(compile_text(rows, policy, store), encoding="utf-8")
    asset_md.parent.mkdir(parents=True, exist_ok=True)
    asset_md.write_text(compile_markdown(rows, policy, store), encoding="utf-8")
    asset_license.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(license_file, asset_license)


def main(argv: list[str] | None = None) -> int:
    """Refresh the inventory and the texts, or run one of the offline checks.

    Returns 1 when a license is denied, when the build environment holds a distribution the
    inventory does not list, or when a resolved install is outside it.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--compile",
        action="store_true",
        help="do not touch the network: rebuild the outputs from the committed store",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="do not touch the network: fail when this environment holds an unlisted distribution",
    )
    parser.add_argument(
        "--check-resolved",
        type=Path,
        default=None,
        metavar="REPORT",
        help="do not touch the network: fail when a pip --dry-run --report file (or a folder of "
        "them) holds a distribution that is no bundle or add-on row",
    )
    arguments = parser.parse_args(argv)

    policy = load_policy(POLICY)
    if arguments.compile:
        rows = read_inventory(INVENTORY)
        write_compiled(rows, policy, STORE, COMPILED, ASSET_MD, ASSET_LICENSE, LICENSE)
        write_asset(rows, ASSET_CSV)
        print(f"Compiled {len(rows)} rows")
        return 0

    if arguments.check_env:
        rows = read_inventory(INVENTORY)
        found = environment_problems(rows)
        if found:
            print("The build environment holds what the inventory does not list:", file=sys.stderr)
            for problem in found:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print("Every installed distribution is a bundle or build row at its version")
        return 0

    if arguments.check_resolved is not None:
        rows = read_inventory(INVENTORY)
        found = resolved_problems(rows, resolved_distributions(arguments.check_resolved))
        if found:
            print("A resolved install holds what the inventory does not list:", file=sys.stderr)
            for problem in found:
                print(f"  {problem}", file=sys.stderr)
            return 1
        print("Every resolved distribution is a bundle or add-on row at its version")
        return 0

    rows = apply_policy(collect(expected(policy)), policy)
    found = problems(rows, policy)
    if found:
        print("Denied licenses:", file=sys.stderr)
        for problem in found:
            print(f"  {problem}", file=sys.stderr)
        return 1

    collect_texts(rows, policy, STORE, hashes=lock_hashes(TEST_LOCK.read_text(encoding="utf-8")))
    write_inventory(rows, INVENTORY)
    write_compiled(rows, policy, STORE, COMPILED, ASSET_MD, ASSET_LICENSE, LICENSE)
    write_asset(rows, ASSET_CSV)
    print(f"Wrote {len(rows)} rows to {INVENTORY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
