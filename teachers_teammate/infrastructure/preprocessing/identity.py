"""What decides the pixels of an automatically prepared page, without importing OpenCV.

The cache key of a stored page (``workflow/cache_service.py``) needs this; the steps themselves
are in :mod:`.auto`.  Kept apart so that computing a key never loads OpenCV.
"""

from __future__ import annotations

from ...domain.data_location import ENGINE_PPOCR
from ...domain.local_models import DOC_ORI, UVDOC
from ..onnx.store import ModelStore

#: Bump whenever a step, a threshold or a page model revision of the automatic preparation
#: changes (``auto.py``): it is part of the cache identity of a prepared page, so stored pages
#: are then read again.
AUTO_VERSION = 1


def auto_output(engine: str) -> str:
    """Return what the automatic preparation hands to *engine*: ``"colour"`` or ``"gray"``.

    The built-in reader wants colour; Ollama gets a gray page.
    """
    return "colour" if engine == ENGINE_PPOCR else "gray"


def available_page_steps(store: ModelStore) -> tuple[str, ...]:
    """Return which model steps *store* can run: ``"turn"`` and/or ``"flatten"``.

    The names are part of the cache identity of a prepared page: downloading the page models
    changes what the preparation does, so pages are read again once.
    """
    steps = []
    if store.is_installed(DOC_ORI):
        steps.append("turn")
    if store.is_installed(UVDOC):
        steps.append("flatten")
    return tuple(steps)


def auto_identity(engine: str, store: ModelStore) -> tuple[str, ...]:
    """Return what decides the pixels of a prepared page: version, output, available model steps.

    Changes when the steps change (:data:`AUTO_VERSION`), when the engine wants another colour,
    and when the page models are downloaded or removed.
    """
    return (
        f"v{AUTO_VERSION}",
        auto_output(engine),
        *(f"+{name}" for name in available_page_steps(store)),
    )
