"""The list of steps the settings ask for.

Two methods, nothing else:

* ``auto`` (the default) — the automatic preparation (:mod:`.auto`): the steps look at each
  page and act only where it needs them.
* ``none`` — "Original page": no step.  The file goes to the reader as it is, except that a
  photo's rotation tag is applied (see :class:`~.steps.StepPipeline`).
"""

from __future__ import annotations

from ...config import OcrConfig
from ..onnx.store import ModelStore
from .auto import auto_steps
from .steps import Step


def steps_for(ocr: OcrConfig, store: ModelStore) -> list[Step]:
    """Return the steps for *ocr*'s settings, in the order they run.

    *store* is where the page models are; the model steps of ``auto`` leave a page alone when
    their files are not there.
    """
    if ocr.preprocess_method == "none":
        return []
    return auto_steps(ocr.engine, store)
