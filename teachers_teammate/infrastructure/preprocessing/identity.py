"""Which page models a store can run, without importing OpenCV.

The cache key of a stored page (``domain.local_models.auto_identity``) needs this; the steps
themselves are in :mod:`.auto`.  Kept apart so that computing a key never loads OpenCV.
"""

from __future__ import annotations

from ...domain.local_models import DOC_ORI, UVDOC
from ..onnx.store import ModelStore


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
