"""The shapes a teacher-facing sentence comes in.

A :class:`Notice` is something the app tells a teacher, a :class:`Question` is something it
asks, and a :class:`FilePrompt` is a picker's caption.  They carry text and nothing else:
the Qt adapter (``gui/_dialogs.py``) turns them into widgets, so this module stays
importable without PySide6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NoticeKind(StrEnum):
    """How a notice is shown: the icon of the box, and whether it draws attention."""

    INFORMATION = "information"
    WARNING = "warning"


class QuestionKind(StrEnum):
    """How a question is shown: a plain question, or one that warns before it asks."""

    QUESTION = "question"
    WARNING = "warning"


class StandardButton(StrEnum):
    """A platform button whose text Qt translates, so a plain yes/no keeps the desktop's words."""

    YES = "yes"
    NO = "no"
    CANCEL = "cancel"
    OK = "ok"


class ChoiceRole(StrEnum):
    """What a button does, so the platform puts it where the desktop expects it."""

    ACCEPT = "accept"
    REJECT = "reject"
    DESTRUCTIVE = "destructive"


_DECLINING_BUTTONS = frozenset({StandardButton.NO, StandardButton.CANCEL})


@dataclass(frozen=True)
class Choice:
    """One button of a :class:`Question`.

    ``label`` is the button's own text.  ``standard`` names a platform button instead, whose
    text Qt translates — a yes/no question keeps the wording the rest of the desktop uses.
    ``default`` marks the button Enter presses; exactly one choice should carry it.  ``id``
    names the button for a question with more than two answers, so the caller reads which one
    was clicked without comparing labels.
    """

    label: str = ""
    standard: StandardButton | None = None
    role: ChoiceRole = ChoiceRole.ACCEPT
    default: bool = False
    enabled: bool = True
    id: str = ""

    @property
    def declines(self) -> bool:
        """Return whether clicking this button calls the action off.

        A button declines by its role, and also when it is the platform's No or Cancel: a
        hand-built ``Choice(standard=StandardButton.NO)`` must not read as "go ahead" because
        its role was left at the default.
        """
        return self.role is ChoiceRole.REJECT or self.standard in _DECLINING_BUTTONS


@dataclass(frozen=True)
class Notice:
    """One thing the app tells a teacher: a title, a body and how it is shown."""

    title: str
    body: str
    kind: NoticeKind = NoticeKind.INFORMATION


@dataclass(frozen=True)
class Question:
    """One thing the app asks a teacher, with the buttons that answer it.

    ``informative`` is a second, smaller paragraph under the body; ``checkbox`` is the label
    of an optional tick box, whose state the answer reports.
    """

    title: str
    body: str
    choices: tuple[Choice, ...]
    kind: QuestionKind = QuestionKind.QUESTION
    informative: str = ""
    checkbox: str = ""


@dataclass(frozen=True)
class FilePrompt:
    """A file or folder picker: the caption, the name filter and where it starts."""

    title: str
    name_filter: str = ""
    start: str = ""


@dataclass(frozen=True)
class Answer:
    """What a teacher did with a :class:`Question`: the button clicked, and the tick box."""

    choice: Choice | None = None
    checked: bool = False

    @property
    def accepted(self) -> bool:
        """Return whether the teacher went ahead: any button but Cancel or No.

        A destructive button ("Stop and close", "Reset everything") accepts the action as
        much as a plain one does; only a rejecting button calls it off.
        """
        return self.choice is not None and not self.choice.declines


def yes_no(*, default_yes: bool = False) -> tuple[Choice, Choice]:
    """Return the platform's Yes and No buttons, with *default_yes* saying which Enter presses."""
    return (
        Choice(standard=StandardButton.YES, default=default_yes),
        Choice(standard=StandardButton.NO, role=ChoiceRole.REJECT, default=not default_yes),
    )


def cancel(*, default: bool = True) -> Choice:
    """Return the platform's Cancel button, by default the one Enter presses."""
    return Choice(standard=StandardButton.CANCEL, role=ChoiceRole.REJECT, default=default)
