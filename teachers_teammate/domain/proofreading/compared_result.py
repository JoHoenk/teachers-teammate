"""One writing model's answer to one text, as the comparison keeps it between runs.

A compared result is not the teacher's work: it holds student text and can be made again by
asking the same model the same question.  So it lives in the cache, it is removed by *Delete
stored results* and the retention expires it — and it carries what it was made with, so the
window can say whether it still fits the text it belongs to.

The record is JSON: a ``format`` field names its shape, so a file written by a newer version of
the app is not read as this one.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..data_location import SentRecord
from .model_comparison import Version
from .review import CorrectionReview

FORMAT = "compare-result/1"
"""The shape of a stored compared result; a newer app writes another one."""


def stem_of(key: str, repeat: int) -> str:
    """Return the name an answer of the settings *key* and its run *repeat* is filed under."""
    return key if repeat == 0 else f"{key}~{repeat + 1}"


def key_of_stem(stem: str) -> str:
    """Return the settings key of the answer filed under *stem* (the inverse of :func:`stem_of`)."""
    return stem.split("~", 1)[0]


@dataclass(frozen=True)
class ComparedResult:
    """What one model answered about one text, with what it was asked and sent with."""

    text_sha: str
    """Hash of the plain transcription this answer belongs to."""

    key: str
    """The proofreading cache key of the settings the answer was made with."""

    provider: str
    """The provider the answer came from (``"ollama"``, ``"mistral"``, …)."""

    model: str
    """The model tag the answer came from."""

    language: str
    """The language of the document it was proofread in."""

    text: str
    """The answer, plain, with the real names back."""

    review: CorrectionReview | None
    """The model's explanations with the real names back, or ``None``."""

    seconds: float
    """How long the answer took, loading the model included."""

    fell_back: bool
    """Part of what was asked is missing from the answer."""

    left_out: int
    """Stretches of the text the model left out and the app put back."""

    warning: str
    """What the teacher should know about the answer; ``""`` for a clean one."""

    sent: SentRecord | None
    """How the text was sent, or ``None`` when the answer needed no request."""

    digest: str
    """The model's digest on its server, so a replaced model tag is a different answer."""

    exchange: str
    """The proofreading exchange and request profile the answer was made with."""

    app_version: str
    """The app version that wrote the answer, for a result that cannot be read later."""

    created: float
    """When the answer was stored (seconds since the epoch), for the retention."""

    repeat: int = 0
    """Which of this model's answers to the text this is: ``0`` for the first, then one more."""

    @property
    def stem(self) -> str:
        """Return the name this answer's file carries: its key, and the run when it is not the first."""
        return stem_of(self.key, self.repeat)

    def version(self) -> Version:
        """Return the answer as one version of a comparison."""
        return Version(
            key=self.key,
            text=self.text,
            review=self.review,
            seconds=self.seconds,
            fell_back=self.fell_back,
            left_out=self.left_out,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the result as the JSON-ready dict the store writes."""
        return {
            "format": FORMAT,
            "text_sha": self.text_sha,
            "key": self.key,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "text": self.text,
            "review": self.review.to_dict() if self.review is not None else None,
            "seconds": self.seconds,
            "fell_back": self.fell_back,
            "left_out": self.left_out,
            "warning": self.warning,
            "sent": self.sent.to_dict() if self.sent is not None else None,
            "digest": self.digest,
            "exchange": self.exchange,
            "app_version": self.app_version,
            "created": self.created,
            "repeat": self.repeat,
        }

    @classmethod
    def from_dict(cls, data: object) -> ComparedResult | None:
        """Return the result stored in *data*, or ``None`` when it is not one of ours.

        A record of another shape, of another app version's format, or without the text it
        belongs to says nothing a comparison could use, so it is treated as no result at all.
        """
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            return None
        texts = [data.get(name, "") for name in ("text_sha", "key", "provider", "model")]
        texts += [data.get(name, "") for name in ("language", "text", "warning", "digest")]
        if not all(isinstance(value, str) for value in texts):
            return None
        text_sha, key, provider, model, language, text, warning, digest = texts
        if not (text_sha and key and text):
            return None
        review = data.get("review")
        left_out = data.get("left_out", 0)
        seconds = data.get("seconds", 0.0)
        created = data.get("created", 0.0)
        repeat = data.get("repeat", 0)
        if not isinstance(left_out, int) or isinstance(left_out, bool):
            return None
        if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 0:
            return None
        if not isinstance(seconds, (int, float)) or not isinstance(created, (int, float)):
            return None
        return cls(
            text_sha=text_sha,
            key=key,
            provider=provider,
            model=model,
            language=language,
            text=text,
            review=CorrectionReview.from_dict(review) if review is not None else None,
            seconds=float(seconds),
            fell_back=bool(data.get("fell_back", False)),
            left_out=left_out,
            warning=warning,
            sent=SentRecord.from_dict(data.get("sent")),
            digest=digest,
            exchange=str(data.get("exchange", "")),
            app_version=str(data.get("app_version", "")),
            created=float(created),
            repeat=repeat,
        )
