"""Which add-ons exist, and what the bundle pins for them."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from pathlib import Path
import re
import sys
from types import MappingProxyType

from ..domain.local_models import PRIVACY_ADDON_ID
from ..domain.providers import AddonPackage
from .llm_factory import get_provider_info, list_providers

_logger = logging.getLogger(__name__)


#: The add-on every LangChain provider builds on, which no provider module declares.
ADDON_LLM_CORE = "llm_core"


#: An add-on of a provider package is ``llm_<provider>``, the provider that first needs it.
_LLM_ADDON_PREFIX = "llm_"


#: Where the build puts the constraints file, relative to the folder the bundle's files sit in.
ADDON_CONSTRAINTS_RELATIVE = Path("teachers_teammate") / "_build" / "addon_constraints.txt"


#: Where the build puts the add-on lock, next to the constraints file.
ADDON_LOCK_RELATIVE = Path("teachers_teammate") / "_build" / "addon_lock.txt"


def _llm_addons() -> dict[str, AddonPackage]:
    """Return ``{add-on id: package}`` for every provider package, one entry per package.

    Read from the providers themselves: a new provider module brings its add-on with it, and
    the ones that share a package (Mistral and Mistral (EU)) are installed once, under the
    first of them.
    """
    addons: dict[str, AddonPackage] = {}
    seen: set[str] = set()
    for provider in list_providers():
        info = get_provider_info(provider)
        if info is None or info.package is None or info.package.pip in seen:
            continue
        seen.add(info.package.pip)
        addons[f"{_LLM_ADDON_PREFIX}{info.name}"] = info.package
    return addons


# Runs at import, before the add-on folder is on ``sys.path``: a provider module must not
# import its package at the top level, which is found only after that folder is injected.
_LLM_ADDONS: dict[str, AddonPackage] = _llm_addons()


ADDON_PACKAGES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        PRIVACY_ADDON_ID: ("spacy>=3.7",),
        # LangChain packages — not bundled by PyInstaller; installed at runtime via Add-ons.
        ADDON_LLM_CORE: ("langchain-core>=1.4.7",),
        **{addon: (package.requirement,) for addon, package in _LLM_ADDONS.items()},
    }
)


# Top-level module name used to check importability for each addon — and the prefix of the
# sys.modules entries evicted after an install, so that find_spec() probes the folder again.
ADDON_CHECK_MODULE: Mapping[str, str] = MappingProxyType(
    {
        PRIVACY_ADDON_ID: "spacy",
        ADDON_LLM_CORE: "langchain_core",
        **{addon: package.module for addon, package in _LLM_ADDONS.items()},
    }
)


def addon_requirements() -> dict[str, list[str]]:
    """Return the pip requirements of every add-on, by add-on name (a copy)."""
    return {addon: list(packages) for addon, packages in ADDON_PACKAGES.items()}


def requirement_name(requirement: str) -> str:
    """Return the normalised package name of a pip *requirement* (``spacy>=3.7`` → ``spacy``)."""
    return normalise(re.split(r"[<>=!~\[;,\s]", requirement, maxsplit=1)[0])


def addon_package_names() -> list[str]:
    """Return the normalised name of every package an add-on installs, without its version."""
    return [
        requirement_name(requirement)
        for packages in ADDON_PACKAGES.values()
        for requirement in packages
    ]


def normalise(name: str) -> str:
    """Return *name* in PEP 503 form (``Nvidia_ML.py`` → ``nvidia-ml-py``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


# ── The constraints of the installed app ────────────────────────────────────────────────────


def _bundled_build_file(relative: Path) -> Path | None:
    """Return a file the build put into the bundle, or ``None`` outside a frozen build."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or meipass is None:
        return None
    path = Path(meipass) / relative
    if path.is_file():
        return path
    _logger.warning("The bundled file %s is missing", path)
    return None


def addon_constraints_path() -> Path | None:
    """Return the constraints file the installed app ships, or ``None``.

    ``None`` outside a frozen build: a source checkout or a wheel install has no bundle whose
    versions an add-on could clash with.  A frozen build without the file (the smoke test
    reports that) is logged and treated like a source run.
    """
    return _bundled_build_file(ADDON_CONSTRAINTS_RELATIVE)


def addon_lock_path() -> Path | None:
    """Return the add-on lock the installed app ships, or ``None``.

    The lock holds the versions of the add-ons' packages and their dependencies that this
    release was tested with (``tools/build/addon_lock.py``).  Without it pip would install
    whatever is newest on the day a teacher clicks *Install*, which is a version nobody ran.
    """
    return _bundled_build_file(ADDON_LOCK_RELATIVE)


def read_constraints(path: Path) -> dict[str, str]:
    """Return ``{normalised name: version}`` from a constraints file of ``name==version`` lines."""
    pins: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return pins
    for line in text.splitlines():
        name, separator, version = line.strip().partition("==")
        if separator and name and version and not name.startswith("#"):
            pins[normalise(name)] = version.strip()
    return pins


def addon_module(addon: str) -> str:
    """Return the top-level module *addon* provides (``privacy`` → ``spacy``)."""
    return ADDON_CHECK_MODULE[addon]
