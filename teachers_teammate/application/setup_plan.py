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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..config import DEFAULTS, INSTALLER_SETTING_KEYS, load_config_file
from ..domain.data_location import (
    ENGINE_PPOCR,
    PROVIDER_OLLAMA,
    RETIRED_OCR_ENGINES,
    STAGE_ANONYMIZATION,
)
from ..domain.local_models import (
    PAGE_MODELS_ADDON_ID,
    PAGE_MODELS_SIZE_GB,
    READER_MODEL_NAME,
    READER_SIZE_GB,
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
    profile_setting,
    suitability_rank,
)
from ..domain.ollama_discovery import OllamaState
from ..domain.stages import Stage
from ..settings_schema import normalize_preprocess_method
from .addons import AddonService
from .anonymization import AnonymizationService
from .correction_options import review_has_own_model
from .model_recommendations import ModelRecommendationService
from .ollama_server import OllamaServerService
from .providers import ProviderService
from .reader import ReaderService


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


class PrivacyCase(StrEnum):
    """Where the students' text goes, which decides what the Privacy page says and offers."""

    ONLINE = "online"
    """To an online service the teacher picked."""

    NETWORK_PC = "network_pc"
    """To Ollama on another PC in the network: off this computer, but not online."""

    THIS_COMPUTER = "this_computer"
    """Nowhere: everything runs on this computer."""


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
    #: The Ollama model the teacher chose for the proofreading; ``""`` follows the recommendation
    #: (and keeps a working model that is already set).  Only used for
    #: :attr:`ProofreadingPlace.LOCAL`.
    local_model: str = ""
    #: The Privacy page's second answer: show the texts in this app with placeholders for the
    #: names (display only, nothing to do with what is sent).
    hide_names: bool = False
    #: The spaCy model of an extra language to find names in, besides the document language's;
    #: ``""`` for none.  Becomes the saved extra detection model and is downloaded when missing.
    extra_names_model: str = ""

    def privacy_case(self, *, ollama_here: bool) -> PrivacyCase:
        """Return where the text goes: online, to another PC in the network, or nowhere.

        *ollama_here* is whether :attr:`ollama_url` is this computer (see
        :meth:`OllamaServerService.is_local`): only the caller can ask that.
        """
        if self.proofreading is ProofreadingPlace.ONLINE:
            return PrivacyCase.ONLINE
        if self.proofreading is ProofreadingPlace.LOCAL and not ollama_here:
            return PrivacyCase.NETWORK_PC
        return PrivacyCase.THIS_COMPUTER


@dataclass(frozen=True)
class SetupFacts:
    """What the setup plan needs to know about this computer, gathered once in the background."""

    profile: HardwareProfile
    system_memory_gb: float | None
    card_gb: float | None
    ollama_running: bool
    #: The server that runs the models is this computer (or the Windows host behind WSL).
    ollama_here: bool
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
    #: The spaCy models that are downloaded (for the extra language's model).
    installed_spacy_models: frozenset[str] = frozenset()
    #: The built-in reader's model files are downloaded.
    reader_installed: bool = False
    #: The page models (turn and flatten) of the automatic image preparation are downloaded.
    page_models_installed: bool = False


@dataclass(frozen=True)
class ModelChoice:
    """One model the proofreading page offers for the AI on this computer."""

    tag: str
    rating: ModelRating
    #: Ollama has it already, so choosing it downloads nothing.
    installed: bool
    #: Catalog download size (GB); ``None`` when the model is not catalogued.
    size_gb: float | None = None


@dataclass(frozen=True)
class LocalModelChoices:
    """The models to choose from for the proofreading, and the one the setup plan would use."""

    #: What the setup plan proposes when the teacher leaves it to the assistant.
    suggested: str
    choices: tuple[ModelChoice, ...]


#: Ratings that keep a catalog model out of the list: it cannot work on this computer.
_NOT_OFFERED = (Suitability.TOO_LARGE, Suitability.WRONG_KIND)


def build_model_choices(
    facts: SetupFacts,
    *,
    suggested: str,
    rate: Callable[[str, ModelCategory], ModelRating],
) -> LocalModelChoices:
    """Return the models to offer for the proofreading: installed ones and rated catalog ones.

    A model that is installed is always offered (the teacher may know why it is there); a
    catalog model only when it can work on this computer.  The best come first.
    """
    tags: dict[str, bool] = {}  # normalised tag -> whether Ollama has it
    for tag in sorted(facts.installed_tags):
        tags[normalize_tag(tag)] = True
    for spec in OLLAMA_CATALOG:
        if "general" in catalog_uses(spec):
            tags.setdefault(normalize_tag(spec.tag), False)
    if suggested:
        tags.setdefault(normalize_tag(suggested), _installed(suggested, facts))
    choices: list[ModelChoice] = []
    for tag, installed in tags.items():
        rating = rate(tag, "general")
        if rating.suitability in _NOT_OFFERED and not installed:
            continue
        spec = find_spec(tag)
        choices.append(
            ModelChoice(
                tag=tag,
                rating=rating,
                installed=installed,
                size_gb=spec.download_gb if spec is not None else None,
            )
        )
    choices.sort(key=lambda choice: (suitability_rank(choice.rating.suitability), choice.tag))
    return LocalModelChoices(suggested=suggested, choices=tuple(choices))


class DownloadKind(StrEnum):
    """What a planned download is."""

    PACKAGE = "package"
    SPACY_MODEL = "spacy_model"
    READER_MODEL = "reader_model"
    PAGE_MODELS = "page_models"
    OLLAMA_MODEL = "ollama_model"


@dataclass(frozen=True)
class PlannedDownload:
    """One download the install round runs."""

    kind: DownloadKind
    #: pip package, spaCy model, the reader's id (``ppocr``), ``page_models`` or Ollama tag.
    name: str
    #: The stage that needs it: ``"ocr"``, ``"correction"`` or ``"anonymization"``.
    stage: str
    #: Catalog download size (GB); ``None`` for a small or unknown download.
    size_gb: float | None = None
    #: ``False`` when Ollama is not running, so its models cannot be pulled now.
    possible: bool = True
    #: A name-detection model of an extra language, not the one of the document language.
    extra: bool = False


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
    #: The name-detection row serves only the view (names hidden in this app), not what is sent.
    view_only: bool = False


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
    #: Show the names hidden in this app's texts: ``True`` when the teacher asked for it, and
    #: ``None`` when the assistant leaves the setting alone (it never turns it off).
    display_anonymized: bool | None = None
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
    installed = [
        spec.tag
        for spec in OLLAMA_CATALOG
        if category in catalog_uses(spec) and _installed(spec.tag, facts)
    ]
    rated = [(tag, rate(tag, category)) for tag in installed]
    for suitability in _FITS:
        for tag, rating in rated:
            if rating.suitability is suitability:
                return tag, rating, True
    tag = best(category)
    return tag, rate(tag, category), _installed(tag, facts)


def _current(values: Mapping[str, Any], key: str) -> str:
    """Return the saved value of *key*, or its default."""
    return str(values.get(key) or DEFAULTS[key])


def _reading_engine(current: Mapping[str, Any]) -> str:
    """Return the saved reading engine; a removed one counts as the built-in reader.

    The settings window turns a removed engine into the reader when it loads, but this has to
    answer the same before it has: only ``ollama`` and ``ppocr`` are known from here on.
    """
    engine = _current(current, "ocr_engine").strip().lower()
    return RETIRED_OCR_ENGINES.get(engine, engine)


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
    if _reading_engine(current) == ENGINE_PPOCR:
        # A reader that is chosen is kept like any working choice (its files are downloaded
        # when they are missing); the assistant never proposes to switch to the reader.
        row = PlannedStep(
            stage=Stage.OCR,
            provider=ENGINE_PPOCR,
            model=READER_MODEL_NAME,
            installed=facts.reader_installed,
            kept=True,
        )
        return {}, [row], ""
    tag, rating, installed = _propose(_current(current, "ocr_model"), "ocr", facts, rate, best)
    row = PlannedStep(
        stage=Stage.OCR, provider=PROVIDER_OLLAMA, model=tag, rating=rating, installed=installed
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
    if place is ProofreadingPlace.LOCAL and answers.local_model:
        # The teacher named the model: it is used as chosen, and pulled when it is missing.
        tag = answers.local_model
        row = PlannedStep(
            stage=Stage.CORRECTION,
            provider=PROVIDER_OLLAMA,
            model=tag,
            rating=rate(tag, "general"),
            installed=_installed(tag, facts),
        )
        return (
            {"correction_provider": PROVIDER_OLLAMA, "correction_model": tag},
            [row],
            PROVIDER_OLLAMA,
            tag,
        )
    if place is ProofreadingPlace.LOCAL:
        candidate = (
            _current(current, "correction_model")
            if _current(current, "correction_provider") == PROVIDER_OLLAMA
            else ""
        )
        tag, rating, installed = _propose(candidate, "general", facts, rate, best)
        row = PlannedStep(
            stage=Stage.CORRECTION,
            provider=PROVIDER_OLLAMA,
            model=tag,
            rating=rating,
            installed=installed,
        )
        return (
            {"correction_provider": PROVIDER_OLLAMA, "correction_model": tag},
            [row],
            PROVIDER_OLLAMA,
            tag,
        )
    if place is ProofreadingPlace.ONLINE:
        provider, model = answers.online_choice
        row = PlannedStep(stage=Stage.CORRECTION, provider=provider, model=model)
        return {"correction_provider": provider, "correction_model": model}, [row], provider, model
    return {}, [PlannedStep(stage=Stage.CORRECTION, provider="", model="")], "", ""


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
            stage=Stage.EVALUATION,
            provider=_current(current, "evaluate_provider"),
            model=_current(current, "evaluate_model"),
            kept=kept,
        )
        return {}, [row], kept
    provider, model = pair
    review_model = model or default_model(provider, Stage.CORRECTION)
    row = PlannedStep(stage=Stage.EVALUATION, provider=provider, model=review_model)
    return {"evaluate_provider": provider, "evaluate_model": review_model}, [row], kept


def _names_plan(
    answers: SetupAnswers, facts: SetupFacts
) -> tuple[dict[str, Any], dict[str, Any], list[PlannedStep], bool]:
    """Return the settings, the panel keys, the names rows and whether name detection is wanted.

    Name detection is wanted for the removal before text is sent online, and for hiding the
    names in this app's texts; only the removal changes what a run sends.  An extra language
    becomes the extra detection model (its download is listed with the others); without one the
    saved choice is left alone.
    """
    case = answers.privacy_case(ollama_here=facts.ollama_here)
    removal = case is not PrivacyCase.THIS_COMPUTER and answers.remove_names
    if not (removal or answers.hide_names):
        return {}, {}, [], False
    panel = {"anonymization_enabled": True} if removal and not facts.removal_on else {}
    row = PlannedStep(
        stage=STAGE_ANONYMIZATION,
        provider="",
        model=facts.spacy_model,
        installed=facts.name_detection,
        view_only=not removal,
    )
    extra = extra_names_model(answers, facts)
    settings = {"anonymizer_secondary_model": extra} if extra else {}
    return settings, panel, [row], True


def extra_names_model(answers: SetupAnswers, facts: SetupFacts) -> str:
    """Return the extra language's model, or ``""`` when none was chosen or it is the main one."""
    extra = answers.extra_names_model.strip()
    return "" if extra == facts.spacy_model else extra


def _downloads_plan(
    facts: SetupFacts,
    *,
    names_wanted: bool,
    extra_models: Sequence[str] = (),
    reading_tag: str,
    reader_needed: bool,
    page_models_needed: bool,
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
                kind=DownloadKind.PACKAGE, name=facts.missing_package, stage=Stage.CORRECTION
            )
        )
    wanted_models = [facts.spacy_model, *extra_models] if names_wanted else []
    missing_models = [
        model
        for model in wanted_models
        if (
            not facts.name_detection
            if model == facts.spacy_model
            else model not in facts.installed_spacy_models
        )
    ]
    if missing_models and not facts.spacy_installed:
        downloads.append(
            PlannedDownload(kind=DownloadKind.PACKAGE, name="spacy", stage=STAGE_ANONYMIZATION)
        )
    for model in missing_models:
        downloads.append(
            PlannedDownload(
                kind=DownloadKind.SPACY_MODEL,
                name=model,
                stage=STAGE_ANONYMIZATION,
                extra=model != facts.spacy_model,
            )
        )
    if reader_needed and not facts.reader_installed:
        downloads.append(
            PlannedDownload(
                kind=DownloadKind.READER_MODEL,
                name=ENGINE_PPOCR,
                stage=Stage.OCR,
                size_gb=READER_SIZE_GB,
            )
        )
    if page_models_needed and not facts.page_models_installed:
        downloads.append(
            PlannedDownload(
                kind=DownloadKind.PAGE_MODELS,
                name=PAGE_MODELS_ADDON_ID,
                stage=Stage.OCR,
                size_gb=PAGE_MODELS_SIZE_GB,
            )
        )
    proposed = [(reading_tag, Stage.OCR)]
    if local_proofreading:
        proposed.append((correction_model, Stage.CORRECTION))
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

    names_settings, names_panel, names_rows, names_wanted = _names_plan(answers, facts)
    settings.update(names_settings)
    panel.update(names_panel)
    steps += names_rows

    downloads = _downloads_plan(
        facts,
        names_wanted=names_wanted,
        extra_models=[names_settings["anonymizer_secondary_model"]] if names_settings else [],
        reading_tag=reading_tag,
        reader_needed=(
            answers.hand_in is HandIn.HANDWRITTEN and _reading_engine(current) == ENGINE_PPOCR
        ),
        page_models_needed=(
            answers.hand_in is HandIn.HANDWRITTEN
            and normalize_preprocess_method(_current(current, "preprocess_method")) == "auto"
        ),
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
        display_anonymized=True if answers.hide_names else None,
        needed_gb=needed_gb,
        free_disk_gb=facts.free_disk_gb,
    )


class SetupService:
    """What the Setup Assistant asks about this computer, and the setup plan it makes from it.

    The rules are the functions of this module; this gathers the facts only the running app can
    answer (the Ollama server, the hardware, the installed packages) and hands them to
    :func:`build_setup_plan`.  It also says which settings file a first start reads.
    """

    def __init__(
        self,
        *,
        ollama: OllamaServerService,
        providers: ProviderService,
        recommendations: ModelRecommendationService,
        anonymization: AnonymizationService,
        reader: ReaderService,
        addons: AddonService,
    ) -> None:
        self._ollama = ollama
        self._providers = providers
        self._recommendations = recommendations
        self._anonymization = anonymization
        self._reader = reader
        self._addons = addons

    # ── What the setup plan needs to know ─────────────────────────────────

    def setup_facts(self, answers: SetupAnswers, current: Mapping[str, Any]) -> SetupFacts:
        """Gather what the setup plan needs to know about this computer.

        Blocking: it probes Ollama, reads the disk and asks spaCy.  Run it on a background
        thread.  *current* is the settings as they are now, including the main window's
        ``anonymization_enabled``.
        """
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            free_disk_gb,
            ollama_models_dir,
        )

        url = answers.ollama_url
        running = self._ollama.ollama_status(url).state is OllamaState.RUNNING
        hardware = self._recommendations.resolve_hardware_profile(
            profile_setting(answers.hardware_profile), url
        )
        local = self._ollama.is_local(url, include_wsl_host=True)
        return SetupFacts(
            profile=hardware.profile,
            system_memory_gb=hardware.system_memory_gb,
            card_gb=hardware.card_gb,
            ollama_running=running,
            ollama_here=local,
            installed_tags=(
                frozenset(self._providers.list_provider_models(PROVIDER_OLLAMA, base_url=url))
                if running
                else frozenset()
            ),
            free_disk_gb=free_disk_gb(ollama_models_dir()) if local else None,
            removal_on=bool(current.get("anonymization_enabled")),
            name_detection=self._anonymization.anonymizer_available(answers.language),
            spacy_installed=self._addons.is_module_importable("spacy"),
            spacy_model=self._anonymization.spacy_model_for_language(answers.language),
            installed_spacy_models=frozenset(self._anonymization.list_installed_spacy_models()),
            reader_installed=self._reader.reader_installed(),
            page_models_installed=self._reader.page_models_installed(),
            missing_package=(
                self._providers.missing_provider_package(answers.online_choice[0]) or ""
                if answers.proofreading is ProofreadingPlace.ONLINE
                else ""
            ),
        )

    def system_language(self) -> str | None:
        """Return the language of this computer's system, or ``None`` when it is not supported."""
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        return system_language()

    def privacy_case(self, answers: SetupAnswers) -> PrivacyCase:
        """Return where the text goes for *answers*: the rule the setup plan follows as well.

        Looks at the Ollama address only when the proofreading is local, so no other answer waits
        for a lookup of this computer's names.
        """
        here = answers.proofreading is not ProofreadingPlace.LOCAL or self._ollama.is_local(
            answers.ollama_url, include_wsl_host=True
        )
        return answers.privacy_case(ollama_here=here)

    def plan_setup(self, answers: SetupAnswers, current: Mapping[str, Any]) -> SetupPlan:
        """Return the setup plan for *answers*: the settings to write and the downloads to run.

        Blocking, like :meth:`setup_facts`: run it on a background thread.
        """
        facts = self.setup_facts(answers, current)
        return build_setup_plan(answers, current, facts, **self._plan_tools(answers, facts))

    def plan_with_model_choices(
        self, answers: SetupAnswers, current: Mapping[str, Any]
    ) -> tuple[SetupPlan, LocalModelChoices]:
        """Return the setup plan and the models the assistant offers for the proofreading.

        The models are the installed ones and the catalog ones that can work on the computer
        Ollama runs on, best first, with the one the setup plan uses when the teacher leaves the
        choice to the assistant.  One call measures the facts once for both.  Blocking, like
        :meth:`plan_setup`: run it on a background thread.
        """
        facts = self.setup_facts(answers, current)
        tools = self._plan_tools(answers, facts)
        plan = build_setup_plan(answers, current, facts, **tools)
        step = next((row for row in plan.steps if row.stage == Stage.CORRECTION), None)
        choices = build_model_choices(
            facts, suggested=step.model if step is not None else "", rate=tools["rate"]
        )
        return plan, choices

    def _plan_tools(self, answers: SetupAnswers, facts: SetupFacts) -> dict[str, Any]:
        """Return the rating, recommendation and default-model functions the setup plan is built with."""

        def rate(tag: str, task: ModelCategory) -> ModelRating:
            return self._recommendations.rate_ollama_models(
                [tag],
                task,
                facts.profile,
                answers.ollama_url,
                # Blocking is fine here (a background thread): an installed model outside the
                # catalog is classified by asking the server, and one that stays unrated would
                # be replaced although it works.
                cached_only=False,
                system_memory_gb=facts.system_memory_gb,
                card_gb=facts.card_gb,
            )[0]

        def best(task: ModelCategory) -> str:
            return self._recommendations.best_ollama_model(
                task,
                facts.profile,
                system_memory_gb=facts.system_memory_gb,
                card_gb=facts.card_gb,
            )

        return {
            "rate": rate,
            "best": best,
            "default_model": self._providers.default_model_for_task,
        }

    # ── The settings file of this start ───────────────────────────────────

    def default_config_path(self) -> Path:
        """Return the default ``ocr.toml`` path inside the config root."""
        from ..infrastructure.storage_root import (  # noqa: PLC0415
            default_config_path,
        )

        return default_config_path()

    def resolve_config_path(self, explicit: str | None) -> Path | None:
        """Return the config file path to load, or ``None`` when none applies."""
        from ..infrastructure.storage_root import (  # noqa: PLC0415
            resolve_config_path,
        )

        return resolve_config_path(explicit)

    def first_start_settings(self, path: Path | None) -> dict | None:
        """Return what the installer chose when *path* is no saved setup yet, else ``None``.

        A first start is a missing settings file, or one that holds only the keys the installer
        writes (``INSTALLER_SETTING_KEYS``): the teacher has not set the app up then, and the
        Setup Assistant must still run.  The dict carries those keys, so the first save does not
        undo them.  The keys are the ones the file holds, also those the schema drops: a file
        with a retired key was saved by an app that was set up.  A file that cannot be read is a
        saved setup, so the caller reports it.
        """
        if path is None or not path.is_file():
            return {}
        try:
            loaded = load_config_file(path)
        except Exception:  # noqa: BLE001  # a broken file is the caller's warning, not a first start
            return None
        if loaded.keys <= INSTALLER_SETTING_KEYS:
            return loaded.values
        return None
