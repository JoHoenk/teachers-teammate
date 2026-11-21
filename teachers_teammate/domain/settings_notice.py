"""What a settings file said that the app could not take as written.

Pure data: the loader records a notice, ``texts/`` turns it into a sentence.  A notice names
the key it is about, so a caller decides what to do with it — the GUI shows the engine
replacement, the CLI prints every one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SettingNoticeKind(StrEnum):
    """Why a value of a settings file was not used as written."""

    ENGINE_REPLACED = "engine_replaced"
    """The text-recognition engine is gone; another one is used instead."""

    VALUE_IGNORED = "value_ignored"
    """The value is not one the key accepts; the default is used instead."""

    NOTES_CUT = "notes_cut"
    """The notes for the AI were longer than a request may carry and were cut."""


@dataclass(frozen=True)
class SettingNotice:
    """One thing a settings file said that the app changed, and what it changed to."""

    kind: SettingNoticeKind
    key: str
    """The settings key the notice is about (``ocr_engine``, ``correction_notes``, …)."""
    detail: str = ""
    """The value as it was written (the engine, for a replaced engine)."""
    replacement: str = ""
    """What is used instead, when the app replaced the value (the engine)."""
    online: bool = False
    """The replaced engine was an online service, not a local engine that was removed."""
