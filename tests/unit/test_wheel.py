"""The Python wheel is the whole package and says what pyproject.toml says.

Until this test existed the wheel held only the non-GUI half of the package, so
``teachers-teammate-gui`` and ``teachers-teammate-benchmark`` crashed after a ``pip install``, the
help pages and the icon were missing, and ``[llm]`` and ``[privacy]`` installed nothing.  The
wheel is built by Bazel (``//teachers_teammate:teachers_teammate_wheel``) from the targets in
``teachers_teammate/BUILD``, and this test holds it to the source tree and to pyproject.toml, so
the three cannot drift apart.
"""

from __future__ import annotations

from email.parser import Parser
import os
from pathlib import Path
import re
import tomllib
import zipfile

from packaging.requirements import Requirement
import pytest

#: Everything besides Python files that a wheel install needs (see ``wheel_data`` in
#: ``teachers_teammate/BUILD``).
_DATA_FILES = (
    "teachers_teammate/assets/teachers_teammate.png",
    "teachers_teammate/assets/teachers_teammate_icon.png",
    "teachers_teammate/assets/third_party_inventory.csv",
    "teachers_teammate/assets/third_party_licenses.md",
    "teachers_teammate/_docs/index.md",
    "teachers_teammate/_docs/using_the_app.md",
    "teachers_teammate/_docs/advanced_user_guide.md",
)

#: The extras a teacher installs. ``test`` and ``dev`` are tooling and stay out of the wheel.
_USER_EXTRAS = {"llm", "privacy"}


def _runfile(variable: str) -> Path:
    """Return the Bazel runfile named by the environment variable *variable*."""
    return Path(os.environ["TEST_SRCDIR"]) / os.environ[variable]


@pytest.fixture(name="wheel", scope="module")
def _wheel() -> zipfile.ZipFile:
    """The wheel Bazel built."""
    return zipfile.ZipFile(_runfile("WHEEL"))


@pytest.fixture(name="metadata", scope="module")
def _metadata(wheel: zipfile.ZipFile):
    """The wheel's METADATA, parsed."""
    name = next(item for item in wheel.namelist() if item.endswith(".dist-info/METADATA"))
    return Parser().parsestr(wheel.read(name).decode("utf-8"))


@pytest.fixture(name="pyproject", scope="module")
def _pyproject() -> dict:
    """pyproject.toml, parsed."""
    return tomllib.loads(_runfile("PYPROJECT").read_text(encoding="utf-8"))


@pytest.fixture(name="package_dir", scope="module")
def _package_dir() -> Path:
    """The source tree's ``teachers_teammate`` folder."""
    return _runfile("PACKAGE_INIT").parent


def _without_extra(requirement: Requirement) -> Requirement:
    """Return *requirement* without the ``extra == '...'`` marker the metadata adds."""
    plain = Requirement(str(requirement))
    plain.marker = None
    return plain


def test_every_source_module_is_in_the_wheel(wheel: zipfile.ZipFile, package_dir: Path) -> None:
    """
    Given  the Python files of teachers_teammate in the source tree
    When   they are looked up in the wheel
    Then   every one is there, GUI and Benchmark included (a missing module is a crash after
           `pip install`, not a missing feature)
    """
    in_wheel = set(wheel.namelist())
    expected = {
        f"teachers_teammate/{path.relative_to(package_dir).as_posix()}"
        for path in package_dir.rglob("*.py")
        if "__pycache__" not in path.parts
    }

    assert expected, "the test found no sources: is the runfile path right?"
    assert sorted(expected - in_wheel) == []


@pytest.mark.parametrize("name", _DATA_FILES)
def test_the_wheel_carries_the_assets_and_the_help_pages(wheel: zipfile.ZipFile, name: str) -> None:
    """
    Given  a file the GUI reads at run time (an icon, the license page, a help page)
    When   it is looked up in the wheel
    Then   it is there and not empty
    """
    assert name in wheel.namelist()
    assert wheel.getinfo(name).file_size > 0


def test_the_help_pages_are_the_ones_in_docs(wheel: zipfile.ZipFile) -> None:
    """
    Given  the three help pages in docs/
    When   the copies in the wheel are compared with them
    Then   they are identical, so a wheel install shows the same guide as the bundle
    """
    docs = _runfile("DOCS_INDEX").parent
    for page in ("index.md", "using_the_app.md", "advanced_user_guide.md"):
        copy = wheel.read(f"teachers_teammate/_docs/{page}")
        assert copy == (docs / page).read_bytes(), page


def test_the_console_scripts_are_the_ones_in_pyproject(
    wheel: zipfile.ZipFile, pyproject: dict
) -> None:
    """
    Given  [project.scripts] in pyproject.toml
    When   the wheel's entry_points.txt is read
    Then   it holds exactly those console scripts
    """
    name = next(item for item in wheel.namelist() if item.endswith("entry_points.txt"))
    text = wheel.read(name).decode("utf-8")
    pairs = (line.partition("=") for line in text.split("[console_scripts]")[1].splitlines())
    scripts = {key.strip(): value.strip() for key, _, value in pairs if key.strip()}

    assert scripts == pyproject["project"]["scripts"]


def test_requires_dist_is_the_dependency_list_of_pyproject(metadata, pyproject: dict) -> None:
    """
    Given  [project.dependencies] in pyproject.toml
    When   the wheel's Requires-Dist lines without an extra are read
    Then   they are exactly those requirements
    """
    wheel_requirements = [
        Requirement(line)
        for line in metadata.get_all("Requires-Dist", [])
        if "extra ==" not in line
    ]
    expected = [Requirement(line) for line in pyproject["project"]["dependencies"]]

    assert sorted(map(str, wheel_requirements)) == sorted(map(str, expected))


def test_the_extras_are_llm_and_privacy_with_their_pyproject_requirements(
    metadata, pyproject: dict
) -> None:
    """
    Given  [project.optional-dependencies] in pyproject.toml
    When   the wheel's Provides-Extra and the requirements behind each extra are read
    Then   the extras are llm and privacy, each with exactly the requirements pyproject lists
           (so `pip install "teachers-teammate[llm]"` installs the providers)
    """
    assert set(metadata.get_all("Provides-Extra", [])) == _USER_EXTRAS

    for extra in _USER_EXTRAS:
        marked = [
            _without_extra(Requirement(line))
            for line in metadata.get_all("Requires-Dist", [])
            if f"extra == '{extra}'" in line or f'extra == "{extra}"' in line
        ]
        expected = [
            Requirement(line) for line in pyproject["project"]["optional-dependencies"][extra]
        ]
        assert sorted(map(str, marked)) == sorted(map(str, expected)), extra


def test_requires_python_is_the_one_in_pyproject(metadata, pyproject: dict) -> None:
    """
    Given  requires-python in pyproject.toml
    When   the wheel's Requires-Python is read
    Then   they are the same
    """
    assert metadata["Requires-Python"] == pyproject["project"]["requires-python"]


def test_the_wheel_version_is_the_package_version(metadata, package_dir: Path) -> None:
    """
    Given  `__version__` in teachers_teammate/__init__.py
    When   the wheel's Version is compared with it
    Then   they are the same (stamp_versions.py writes both for a release)
    """
    init = (package_dir / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', init, flags=re.MULTILINE)

    assert match is not None
    assert metadata["Version"] == match.group(1)
