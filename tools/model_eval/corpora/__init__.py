"""The corpora a test set can draw on: one adapter module per corpus, found at run time.

Adding a corpus is one module here plus its tests: the module sets ``ADAPTER`` to a
:class:`~._base.CorpusAdapter` subclass, and nothing else in the tool names the corpus.  The
selection of texts is *not* here — that is a recipe (:mod:`tools.model_eval.recipes`), because one
corpus serves several test sets and one set draws on several corpora.

The modules are found like the app's providers (``infrastructure/providers/``): every module whose
name does not start with ``_``, imported once, sorted by name.  A broken adapter fails loudly, on
the first command and in the tests (:func:`~tools.model_eval._plugins.discover`).
"""

from __future__ import annotations

from .._plugins import discover
from ._base import CorpusAdapter, ResolvedItem, pointers

_ADAPTERS: dict[str, type[CorpusAdapter]] | None = None


def adapters() -> dict[str, type[CorpusAdapter]]:
    """Return ``{name: adapter class}`` of every corpus, found once and then kept.

    The dict is the same object on every call, so a test can add a corpus to it with
    ``monkeypatch.setitem``.
    """
    global _ADAPTERS  # noqa: PLW0603  # pylint: disable=global-statement  # found once, so a test can add a corpus to the dict
    if _ADAPTERS is None:
        _ADAPTERS = _discover()
    return _ADAPTERS


def names() -> list[str]:
    """Return the names of the corpora, sorted."""
    return sorted(adapters())


def adapter_class(name: str) -> type[CorpusAdapter]:
    """Return the adapter class of corpus *name*.

    Raises:
        ValueError: if no adapter has that name; the message lists the known ones.
    """
    found = adapters().get(name)
    if found is None:
        raise ValueError(f"no corpus {name!r}; known: {', '.join(names())}")
    return found


def _check(module: str, adapter: object) -> None:
    """Raise :exc:`RuntimeError` unless *adapter* is a complete :class:`~._base.CorpusAdapter`."""
    if not (isinstance(adapter, type) and issubclass(adapter, CorpusAdapter)):
        raise RuntimeError(f"{module}: ADAPTER is not a CorpusAdapter")
    for attribute in ("NAME", "LICENCE", "RESTRICTED", "LANGUAGES", "HINT"):
        if not hasattr(adapter, attribute):
            raise RuntimeError(f"{module}: the adapter has no {attribute}")


def _discover() -> dict[str, type[CorpusAdapter]]:
    """Import every adapter module and check that it is one (see :func:`.._plugins.discover`)."""
    return discover(
        __name__,
        __path__,
        attribute="ADAPTER",
        kind="corpus",
        name_of=lambda adapter: adapter.NAME,
        check=_check,
    )


__all__ = ["CorpusAdapter", "ResolvedItem", "adapter_class", "adapters", "names", "pointers"]
