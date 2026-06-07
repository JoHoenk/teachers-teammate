"""Domain stage metadata and helpers for pipeline stage ordering."""

from __future__ import annotations

from typing import Literal, cast

StageName = Literal["ocr", "correction", "evaluation"]

STAGE_ORDER: tuple[StageName, ...] = ("ocr", "correction", "evaluation")


def normalize_stage(stage: str) -> StageName:
    """Normalize and validate a stage label used for invalidation flows."""
    value = stage.strip().lower()
    if value not in STAGE_ORDER:
        msg = f"Invalid stage '{stage}'. Expected one of: {', '.join(STAGE_ORDER)}"
        raise ValueError(msg)
    return cast(StageName, value)
