"""The reading models a run may name before the catalog knows them.

A candidate is a tag, the prompt its card names, its licence and where it came from.  Nothing
here has been measured: a candidate enters ``OLLAMA_CATALOG`` only after a run, through the usual
``tools/verify_model_catalog.py`` jobs (``sizes``, ``capabilities``, ``fit``).

A tag on ``hf.co/…`` is a tag like any other — Ollama pulls it — so a candidate the survey named
that way is listed.  A candidate whose tag the survey left open is not listed: an empty place
here is a gap in the survey, not a model somebody chose, and guessing a tag would measure a
stranger.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG, OllamaModelSpec

SURVEY = "a web survey, unverified"
"""Where every candidate here comes from: none of them is measured."""


@dataclass(frozen=True)
class Candidate:
    """A reading model the tool knows before the catalog does."""

    tag: str
    kind: str
    """``ocr``: a model that only reads.  ``general``: a vision model that also writes."""
    licence: str
    """The licence the survey claims; the developer reads the card, and no run reads the claim."""
    source: str
    card_prompt: str = ""
    """The prompt the model's card names, or ``""`` when it names none (or it is not typed in)."""


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        tag="maternion/LightOnOCR-3",
        kind="ocr",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="richardyoung/lightonocr-3-1b",
        kind="ocr",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="richardyoung/olmocr2",
        kind="ocr",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="hf.co/richardyoung/olmOCR-2-7B-1025-GGUF:Q8_0",
        kind="ocr",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="gemma4:e4b",
        kind="general",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="gemma4:12b",
        kind="general",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="qwen3-vl:27b",
        kind="general",
        licence="Apache-2.0",
        source=SURVEY,
    ),
    Candidate(
        tag="qwen3-vl:30b-a3b",
        kind="general",
        licence="Apache-2.0",
        source=SURVEY,
    ),
)

CARD_PROMPTS: Mapping[str, str] = {
    "glm-ocr:q8_0": "Text Recognition:",
}
"""The prompts a catalogued model's card names, by tag.

The app sends one prompt to every model; a run that reads a model with its card's prompt instead
measures whether the app should learn a prompt per model.  A prompt is typed in from the card and
never guessed: a card whose wording is not transcribed here reads with the app's prompt.
"""


def catalogued() -> tuple[OllamaModelSpec, ...]:
    """Return every catalogued reading model, in the catalog's order."""
    return tuple(spec for spec in OLLAMA_CATALOG if spec.category == "ocr")


def screen() -> tuple[str, ...]:
    """Return what a model screen runs: every catalogued reading model, then the candidates."""
    return tuple(spec.tag for spec in catalogued()) + tuple(
        candidate.tag for candidate in CANDIDATES
    )


def find(tag: str) -> Candidate | None:
    """Return the candidate named *tag*, or ``None`` when the catalog or nobody knows it."""
    return next((candidate for candidate in CANDIDATES if candidate.tag == tag), None)


def card_prompt(tag: str) -> str:
    """Return the prompt *tag*'s card names, or ``""`` when no card's prompt is transcribed."""
    return CARD_PROMPTS.get(tag, "")


def licence(tag: str) -> str:
    """Return what the catalog says the weights of *tag* come under, or the survey's claim.

    ``""`` when neither knows: a licence nobody read is not a licence.
    """
    spec = next((spec for spec in OLLAMA_CATALOG if spec.tag == tag), None)
    if spec is not None and spec.licence is not None:
        return spec.licence.name
    candidate = find(tag)
    return candidate.licence if candidate is not None else ""
