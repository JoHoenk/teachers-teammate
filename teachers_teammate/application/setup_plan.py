"""The Setup Assistant's plan: which models to use and what to download.

The assistant asks what only the teacher knows (language, handwritten or typed pages, where the
proofreading runs) and this module turns the answers plus the facts about the computer into one
plan: the settings to write, the rows the summary page shows and the downloads the install round
runs.  It is a pure function, so the rules can be tested without a computer, a server or a GUI.

Two rules shape it:

- **A working choice is kept.**  A model that is installed and fits is not replaced by the
  recommendation, so running the assistant again changes nothing.
- **Nothing is turned off.**  "Remove personal info" is only ever turned on, and a content review
  that has its own model keeps it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..config import DEFAULTS
from ..domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
)
from ..domain.model_catalog import (
    OLLAMA_CATALOG,
    DiskVerdict,
    HardwareProfile,
    ModelCategory,
    ModelRating,
    Suitability,
    catalog_uses,
    disk_verdict,
    find_spec,
    normalize_tag,
)
from .correction_options import review_has_own_model


class HandIn(StrEnum):
    """What the teacher hands in."""

    HANDWRITTEN = "handwritten"
    """Pages are read by a model: text recognition is on."""

    TYPED = "typed"
    """The text already exists: text recognition is off."""


class ProofreadingPlace(StrEnum):
    """Where the proofreading runs."""

    LOCAL = "local"
    """Ollama at the address of the Local AI page."""

    ONLINE = "online"
    """A service the teacher picks, with its key."""

    OFF = "off"
    """Not now — only offered for handwritten pages."""


@dataclass(frozen=True)
class SetupAnswers:
    """What the teacher answered in the assistant."""

    language: str
    hand_in: HandIn
    proofreading: ProofreadingPlace
    ollama_url: str
    #: The setting: ``"auto"``, ``"cpu"``, ``"8gb"`` or ``"16gb"``.
    hardware_profile: str
    #: Whether the Local AI page was part of the path (it is skipped for typed pages).
    local_ai_shown: bool
    #: ``(provider, model)`` for :attr:`ProofreadingPlace.ONLINE`; an empty model means the
    #: provider's default.
    online_choice: tuple[str, str] = ("", "")
    #: The Privacy page's answer; only used for :attr:`ProofreadingPlace.ONLINE`.
    remove_names: bool = False


@dataclass(frozen=True)
class SetupFacts:
    """What the setup plan needs to know about this computer, gathered once in the background."""

    profile: HardwareProfile
    system_memory_gb: float | None
    card_gb: float | None
    ollama_running: bool
    installed_tags: frozenset[str]
    free_disk_gb: float | None
    #: "Remove personal info" is ticked in the main window right now.
    removal_on: bool
    #: Name detection can run for the document language right now.
    name_detection: bool
    spacy_installed: bool
    #: The spaCy model the document language needs.
    spacy_model: str
    #: pip name of the online provider's package, or ``""`` when nothing is missing.
    missing_package: str


class DownloadKind(StrEnum):
    """What a planned download is."""

    PACKAGE = "package"
    SPACY_MODEL = "spacy_model"
    OLLAMA_MODEL = "ollama_model"


@dataclass(frozen=True)
class PlannedDownload:
    """One download the install round runs."""

    kind: DownloadKind
    #: pip package, spaCy model or Ollama tag.
    name: str
    #: The stage that needs it: ``"ocr"``, ``"correction"`` or ``"anonymization"``.
    stage: str
    #: Catalog download size (GB); ``None`` for a small or unknown download.
    size_gb: float | None = None
    #: ``False`` when Ollama is not running, so its models cannot be pulled now.
    possible: bool = True


@dataclass(frozen=True)
class PlannedStep:
    """One row of the summary page: a step of a run and the model it will use."""

    stage: str
    provider: str
    model: str
    #: How the model fits this computer; ``None`` for a step that runs no model of ours.
    rating: ModelRating | None = None
    #: The model is already there, so nothing is downloaded for this row.
    installed: bool = False
    #: The step keeps what it had: another reading method, or a review with its own model.
    kept: bool = False


@dataclass(frozen=True)
class SetupPlan:
    """What the assistant writes and downloads when the teacher presses Set up."""

    #: Settings-window keys (``ollama_url``, ``hardware_profile``, ``ocr_model``,
    #: ``correction_*``, ``evaluate_*``).
    settings: dict[str, Any]
    #: Main-window keys (``language``, ``recognition_enabled``, ``correction_enabled``,
    #: ``anonymization_enabled`` — only ever set to ``True``).
    panel: dict[str, Any]
    #: One per summary row.
    steps: tuple[PlannedStep, ...]
    downloads: tuple[PlannedDownload, ...]
    #: The content review keeps its own service and model.
    review_kept: bool
    disk: DiskVerdict
    #: What the downloads that can run now add up to, in GB.
    needed_gb: float = 0.0
    #: Free space where Ollama keeps its models, or ``None`` when it could not be read.
    free_disk_gb: float | None = None


#: Ratings that make an installed model a working choice for its step.
_USABLE = (Suitability.BEST, Suitability.GOOD, Suitability.WORKS)
#: Ratings that make an installed model worth preferring over the recommendation.
_FITS = (Suitability.BEST, Suitability.GOOD)


def _installed(tag: str, facts: SetupFacts) -> bool:
    """Return whether *tag* is one of the models Ollama has here."""
    return normalize_tag(tag) in facts.installed_tags


def _propose(
    current: str,
    category: ModelCategory,
    facts: SetupFacts,
    rate: Callable[[str, ModelCategory], ModelRating],
    best: Callable[[ModelCategory], str],
) -> tuple[str, ModelRating, bool]:
    """Return the model to use for *category* as ``(tag, rating, installed)``.

    The order is: the current model when it is installed and works; otherwise the first
    installed catalog model of that kind that fits well; otherwise the recommendation.
    """
    if current and _installed(current, facts):
        rating = rate(current, category)
        if rating.suitability in _USABLE:
            return current, rating, True
    for spec in OLLAMA_CATALOG:
        if category not in catalog_uses(spec) or not _installed(spec.tag, facts):
            continue
        rating = rate(spec.tag, category)
        if rating.suitability in _FITS:
            return spec.tag, rating, True
    tag = best(category)
    return tag, rate(tag, category), _installed(tag, facts)


def _current(values: Mapping[str, Any], key: str) -> str:
    """Return the saved value of *key*, or its default."""
    return str(values.get(key) or DEFAULTS[key])


def _reading_plan(
    answers: SetupAnswers,
    current: Mapping[str, Any],
    facts: SetupFacts,
    rate: Callable[[str, ModelCategory], ModelRating],
    best: Callable[[ModelCategory], str],
) -> tuple[dict[str, Any], list[PlannedStep], str]:
    """Return the reading settings, its summary row and the model it proposes."""
    if answers.hand_in is not HandIn.HANDWRITTEN:
        return {}, [], ""
    engine = _current(current, "ocr_engine")
    if engine != "ollama":
        row = PlannedStep(
            stage=STAGE_OCR,
            provider=engine,
            model=_current(current, "ocr_model"),
            installed=True,
            kept=True,
        )
        return {}, [row], ""
    tag, rating, installed = _propose(_current(current, "ocr_model"), "ocr", facts, rate, best)
    row = PlannedStep(
        stage=STAGE_OCR, provider="ollama", model=tag, rating=rating, installed=installed
    )
    return {"ocr_model": tag}, [row], tag


def _proofreading_plan(
    answers: SetupAnswers,
    current: Mapping[str, Any],
    facts: SetupFacts,
    rate: Callable[[str, ModelCategory], ModelRating],
    best: Callable[[ModelCategory], str],
) -> tuple[dict[str, Any], list[PlannedStep], str, str]:
    """Return the proofreading settings, its row and the ``(provider, model)`` pair."""
    place = answers.proofreading
    if place is ProofreadingPlace.LOCAL:
        candidate = (
            _current(current, "correction_model")
            if _current(current, "correction_provider") == "ollama"
            else ""
        )
        tag, rating, installed = _propose(candidate, "general", facts, rate, best)
        row = PlannedStep(
            stage=STAGE_CORRECTION,
            provider="ollama",
            model=tag,
            rating=rating,
            installed=installed,
        )
        return {"correction_provider": "ollama", "correction_model": tag}, [row], "ollama", tag
    if place is ProofreadingPlace.ONLINE:
        provider, model = answers.online_choice
        row = PlannedStep(stage=STAGE_CORRECTION, provider=provider, model=model)
        return {"correction_provider": provider, "correction_model": model}, [row], provider, model
    return {}, [PlannedStep(stage=STAGE_CORRECTION, provider="", model="")], "", ""


def _review_plan(
    place: ProofreadingPlace,
    pair: tuple[str, str],
    current: Mapping[str, Any],
    default_model: Callable[[str, str], str],
) -> tuple[dict[str, Any], list[PlannedStep], bool]:
    """Return the review settings, its row and whether the review keeps its own model."""
    kept = review_has_own_model(
        current, lambda provider, model, task: model.strip() or default_model(provider, task)
    )
    if kept or place is ProofreadingPlace.OFF:
        row = PlannedStep(
            stage=STAGE_EVALUATION,
            provider=_current(current, "evaluate_provider"),
            model=_current(current, "evaluate_model"),
            kept=kept,
        )
        return {}, [row], kept
    provider, model = pair
    review_model = model or default_model(provider, "correction")
    row = PlannedStep(stage=STAGE_EVALUATION, provider=provider, model=review_model)
    return {"evaluate_provider": provider, "evaluate_model": review_model}, [row], kept


def _names_plan(
    answers: SetupAnswers, facts: SetupFacts
) -> tuple[dict[str, Any], list[PlannedStep], bool]:
    """Return the panel keys, the names row and whether name detection is wanted."""
    if answers.proofreading is not ProofreadingPlace.ONLINE or not answers.remove_names:
        return {}, [], False
    panel = {} if facts.removal_on else {"anonymization_enabled": True}
    row = PlannedStep(
        stage=STAGE_ANONYMIZATION,
        provider="",
        model=facts.spacy_model,
        installed=facts.name_detection,
    )
    return panel, [row], True


def _downloads_plan(
    facts: SetupFacts,
    *,
    names_wanted: bool,
    reading_tag: str,
    correction_model: str,
    place: ProofreadingPlace,
) -> list[PlannedDownload]:
    """Return the downloads in the order the install round runs them."""
    local_proofreading = place is ProofreadingPlace.LOCAL
    downloads: list[PlannedDownload] = []
    # The online service's package is only needed when proofreading goes there.
    if facts.missing_package and place is ProofreadingPlace.ONLINE:
        downloads.append(
            PlannedDownload(
                kind=DownloadKind.PACKAGE, name=facts.missing_package, stage=STAGE_CORRECTION
            )
        )
    if names_wanted and not facts.name_detection:
        if not facts.spacy_installed:
            downloads.append(
                PlannedDownload(kind=DownloadKind.PACKAGE, name="spacy", stage=STAGE_ANONYMIZATION)
            )
        downloads.append(
            PlannedDownload(
                kind=DownloadKind.SPACY_MODEL,
                name=facts.spacy_model,
                stage=STAGE_ANONYMIZATION,
            )
        )
    proposed = [(reading_tag, STAGE_OCR)]
    if local_proofreading:
        proposed.append((correction_model, STAGE_CORRECTION))
    for tag, stage in proposed:
        if not tag or _installed(tag, facts):
            continue
        if any(download.name == tag for download in downloads):
            continue
        spec = find_spec(tag)
        downloads.append(
            PlannedDownload(
                kind=DownloadKind.OLLAMA_MODEL,
                name=tag,
                stage=stage,
                size_gb=spec.download_gb if spec is not None else None,
                possible=facts.ollama_running,
            )
        )
    return downloads


def build_setup_plan(
    answers: SetupAnswers,
    current: Mapping[str, Any],
    facts: SetupFacts,
    *,
    rate: Callable[[str, ModelCategory], ModelRating],
    best: Callable[[ModelCategory], str],
    default_model: Callable[[str, str], str],
) -> SetupPlan:
    """Return the setup plan for *answers* on this computer.

    *current* is the settings as they are now (the Settings-window keys plus the main window's
    ``anonymization_enabled``).  *rate* rates one Ollama tag for a task, *best* names the
    recommendation for a task, and *default_model* resolves a provider's default model for a
    task.
    """
    settings: dict[str, Any] = {}
    panel: dict[str, Any] = {"language": answers.language}
    steps: list[PlannedStep] = []

    if answers.local_ai_shown:
        settings["ollama_url"] = answers.ollama_url
        settings["hardware_profile"] = answers.hardware_profile

    panel["recognition_enabled"] = answers.hand_in is HandIn.HANDWRITTEN
    reading_settings, reading_rows, reading_tag = _reading_plan(answers, current, facts, rate, best)
    settings.update(reading_settings)
    steps += reading_rows

    place = answers.proofreading
    panel["correction_enabled"] = place is not ProofreadingPlace.OFF
    correction_settings, correction_rows, provider, model = _proofreading_plan(
        answers, current, facts, rate, best
    )
    settings.update(correction_settings)
    steps += correction_rows

    review_settings, review_rows, review_kept = _review_plan(
        place, (provider, model), current, default_model
    )
    settings.update(review_settings)
    steps += review_rows

    names_panel, names_rows, names_wanted = _names_plan(answers, facts)
    panel.update(names_panel)
    steps += names_rows

    downloads = _downloads_plan(
        facts,
        names_wanted=names_wanted,
        reading_tag=reading_tag,
        correction_model=model,
        place=place,
    )
    needed_gb = sum(
        download.size_gb or 0.0
        for download in downloads
        if download.kind is DownloadKind.OLLAMA_MODEL and download.possible
    )
    return SetupPlan(
        settings=settings,
        panel=panel,
        steps=tuple(steps),
        downloads=tuple(downloads),
        review_kept=review_kept,
        disk=disk_verdict(needed_gb, facts.free_disk_gb),
        needed_gb=needed_gb,
        free_disk_gb=facts.free_disk_gb,
    )
