"""What the ``proofreading_context`` setting changes on this computer, and what it gives.

The Settings page has to say two things about the context choice: whether it changes anything
at all, and what ``"auto"`` resolves to right now.  Both are pure decisions over the settings and
the graphics card, so they live here rather than in the widget.

With proofreading capped at the base window (``spec-cw-followup.md`` CW7) the setting only sizes
the content review, and only when that runs on its own Ollama model.  ``applies`` is therefore
*computed* from the windows rather than written out by hand: it stays right when the cap is
raised again or the ``same_model`` rule changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.context_budget import BASE_NUM_CTX, CONTEXT_STEPS, RunWindows, stage_windows


@dataclass(frozen=True)
class ContextChoice:
    """What the context setting changes on this computer, and the windows ``"auto"`` gives."""

    applies: bool
    """Whether any value of the setting changes a window."""

    auto_review: int
    """The window the content review gets with ``"auto"``."""

    auto_proofreading: int
    """The window proofreading gets with ``"auto"``."""


def context_choice(
    *,
    proofreading_provider: str,
    proofreading_model: str,
    review_provider: str,
    review_model: str,
    card_gb: float | None,
) -> ContextChoice:
    """Return what the context setting changes, and the windows ``"auto"`` gives.

    *card_gb* is the graphics memory of a detection the caller already has; ``None`` means there
    is no card to grow into (a computer without one, a remote server, a hand-picked profile).
    Nothing here does I/O.

    The content review is assumed to run: whether it does is a checkbox in the main window, not
    a setting, so the page cannot know.  Where it does not run, the setting changes nothing and
    the tooltip says so.
    """

    def windows(setting: str) -> RunWindows:
        return stage_windows(
            setting,
            proofreading_model=proofreading_model,
            evaluation_model=review_model,
            proofreading_local=proofreading_provider == "ollama",
            evaluation_local=review_provider == "ollama",
            evaluation_runs=True,
            card_gb=card_gb,
        )

    auto = windows("auto")
    largest = windows(str(CONTEXT_STEPS[-1]))
    smallest = windows(str(BASE_NUM_CTX))
    return ContextChoice(
        applies=(largest.proofreading, largest.evaluation)
        != (smallest.proofreading, smallest.evaluation),
        auto_review=auto.evaluation,
        auto_proofreading=auto.proofreading,
    )


__all__ = ["ContextChoice", "context_choice"]
