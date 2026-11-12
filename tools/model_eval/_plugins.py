"""Finding the modules of a plugin package: one adapter per corpus, one recipe per test set.

The corpora and the recipes are found the same way (as the app finds its LLM providers): every
module whose name does not start with ``_`` is imported once, sorted by name, and must expose one
object under a fixed attribute.  A broken module fails loudly, on the first command and in the
tests.  What differs between the two packages — the attribute, what counts as a valid object and
what names it — is passed in; the rules that do not differ live here once.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import importlib
import pkgutil
from typing import Any


def discover(
    package: str,
    path: Iterable[str],
    *,
    attribute: str,
    kind: str,
    name_of: Callable[[Any], str],
    check: Callable[[str, Any], None],
) -> dict[str, Any]:
    """Import every plugin module of *package* and return ``{name: object}``.

    Args:
        package: The dotted name of the package, e.g. ``tools.model_eval.corpora``.
        path: The package's ``__path__``.
        attribute: The module attribute that holds the plugin object (``ADAPTER``, ``RECIPE``).
        kind: What a plugin is called in messages (``corpus``, ``test set``).
        name_of: Returns the name a plugin object goes by.
        check: Raises :exc:`RuntimeError` (with the module's name first) for an object that is
            not a valid plugin; called with the module name and the object.

    Raises:
        RuntimeError: for a module without *attribute*, an invalid object, a module name that is
            not the plugin name with ``-`` replaced by ``_``, or a name used twice.
    """
    found: dict[str, Any] = {}
    for info in sorted(pkgutil.iter_modules(path), key=lambda info: info.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        plugin = getattr(module, attribute, None)
        if plugin is None:
            raise RuntimeError(f"{module.__name__}: no {attribute}, so it is not a {kind}")
        check(module.__name__, plugin)
        name = name_of(plugin)
        expected = name.replace("-", "_")
        if info.name != expected:
            raise RuntimeError(
                f"{module.__name__}: the module is {info.name!r}, its {kind} {name!r} "
                f"needs {expected!r}"
            )
        if name in found:
            raise RuntimeError(f"{module.__name__}: the {kind} {name!r} is used twice")
        found[name] = plugin
    return found
