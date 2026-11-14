"""Where each pipeline step runs and what leaves the user's computer.

Pure rules, no I/O: the application layer resolves the facts that need the outside
world (is this Ollama URL this computer? what does a provider declare?) and passes
them in.  The GUI and CLI only render the result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

#: OCR engines that read pages on a computer the user controls, with the name shown for
#: each.  Reading a page needs the whole page (names, grades, handwriting), so personal
#: information cannot be hidden first: online services are deliberately not offered here.
ENGINE_OLLAMA = "ollama"
ENGINE_PPOCR = "ppocr"
OCR_ENGINE_NAMES: dict[str, str] = {
    ENGINE_OLLAMA: "Ollama",
    ENGINE_PPOCR: "Built-in reader",
}
LOCAL_OCR_ENGINES: tuple[str, ...] = tuple(OCR_ENGINE_NAMES)

STAGE_OCR = "ocr"
STAGE_CORRECTION = "correction"
STAGE_EVALUATION = "evaluation"
#: Not a pipeline stage: the box lists personal-info removal to show whether it is on.
STAGE_ANONYMIZATION = "anonymization"


_STAGE_TITLES = {
    STAGE_OCR: "Text recognition",
    STAGE_ANONYMIZATION: "Personal info removal",
    STAGE_CORRECTION: "Proofreading",
    STAGE_EVALUATION: "Content review",
}


class DataLocation(StrEnum):
    """Where a step's data is processed, from most to least private."""

    THIS_COMPUTER = "this_computer"
    OTHER_COMPUTER = "other_computer"
    ONLINE = "online"


class DataRegion(StrEnum):
    """Where an online service processes the data, as far as it can be told.

    "EU" means a member state of the European Union.  The United Kingdom, Switzerland,
    Norway and the other EEA countries count as outside it, and so does a provider that
    does not say where it processes requests: a school can only rely on what is declared.
    """

    EU = "eu"
    NON_EU = "non_eu"
    UNKNOWN = "unknown"

    @property
    def label(self) -> str:
        """How the region is written next to a service: ``EU`` or ``outside the EU``."""
        return "EU" if self is DataRegion.EU else "outside the EU"


class PrivacyLevel(StrEnum):
    """The worst thing that happens to the documents, from most to least private.

    Ordered: a run is as private as its least private step, so the levels are compared by
    :attr:`severity` and the highest one wins.
    """

    LOCAL = "local"
    OWN_SERVER = "own_server"
    ANON_EU = "anon_eu"
    ANON_NON_EU = "anon_non_eu"
    NAMES_EU = "names_eu"
    NAMES_NON_EU = "names_non_eu"

    @property
    def severity(self) -> int:
        """Position on the private-to-exposed scale; the highest one of a run decides."""
        return _PRIVACY_ORDER.index(self)

    @property
    def leaves_computer(self) -> bool:
        """Whether data leaves this computer at this level."""
        return self is not PrivacyLevel.LOCAL

    @property
    def sends_names(self) -> bool:
        """Whether names are sent as written at this level."""
        return self in (PrivacyLevel.NAMES_EU, PrivacyLevel.NAMES_NON_EU)


_PRIVACY_ORDER: tuple[PrivacyLevel, ...] = (
    PrivacyLevel.LOCAL,
    PrivacyLevel.OWN_SERVER,
    PrivacyLevel.ANON_EU,
    PrivacyLevel.ANON_NON_EU,
    PrivacyLevel.NAMES_EU,
    PrivacyLevel.NAMES_NON_EU,
)


def is_ollama_cloud_model(model: str) -> bool:
    """Return whether *model* is an Ollama *cloud* model (runs on ollama.com).

    These are served through the local Ollama but processed on Ollama's servers; their
    tag is ``cloud`` or ends in ``-cloud`` (``gpt-oss:120b-cloud``, ``glm-4.6:cloud``).

    Detection is by name only: the ``ollama`` client library does not expose where a model
    runs, so a cloud model published under a different naming scheme would pass as local.
    """
    _name, sep, tag = model.strip().lower().rpartition(":")
    return bool(sep) and (tag == "cloud" or tag.endswith("-cloud"))


def is_ollama_cloud_host(host: str) -> bool:
    """Return whether *host* is Ollama's hosted API rather than a server the user runs."""
    host = host.strip().lower()
    return host == "ollama.com" or host.endswith(".ollama.com")


class Advice(StrEnum):
    """What the user can still do to keep more on this computer.

    Codes, not sentences: the wording (and the names of the controls it points to) belongs
    to whichever GUI or CLI shows it.
    """

    PAGES_TO_OTHER_COMPUTER = "pages_to_other_computer"
    NAMES_SENT_AS_WRITTEN = "names_sent_as_written"
    NAMES_MAY_BE_MISSED = "names_may_be_missed"
    SERVICE_OUTSIDE_EU = "service_outside_eu"


@dataclass(frozen=True)
class StageLocation:
    """Where one step runs and which service does the work."""

    location: DataLocation
    #: Display name of the engine or provider ("Ollama", "Built-in reader", "OpenAI").
    service: str
    #: Host name of the other computer (OTHER_COMPUTER only).
    host: str = ""
    #: Where the data is processed (ONLINE only).  ``None`` means "not declared", which is
    #: shown like :attr:`DataRegion.UNKNOWN`.
    region: DataRegion | None = None
    #: Home country of the vendor, for the tooltip only (e.g. ``"France"``, ``"USA"``).
    vendor_country: str = ""

    @property
    def data_region(self) -> DataRegion:
        """The region to show, treating "not declared" as :attr:`DataRegion.UNKNOWN`."""
        return self.region if self.region is not None else DataRegion.UNKNOWN

    @property
    def label(self) -> str:
        """Plain-language place, e.g. ``On this computer`` or ``Online service (OpenAI) · EU``."""
        if self.location is DataLocation.THIS_COMPUTER:
            return "On this computer"
        if self.location is DataLocation.OTHER_COMPUTER:
            return f"On another computer ({self.host})" if self.host else "On another computer"
        return f"Online service ({self.service}) · {self.data_region.label}"

    @property
    def details(self) -> str:
        """The tooltip: where the data is processed, and where the vendor is at home."""
        parts = [self.label]
        if self.location is DataLocation.ONLINE and self.vendor_country:
            parts.append(f"Vendor based in {self.vendor_country}")
        return "\n".join(parts)


def anonymization_badge(models: Iterable[str]) -> str:
    """Return what the personal-info-removal row says, e.g. ``On (spaCy en + de)`` or ``Off``.

    *models* are the spaCy models that will actually be loaded; each is shortened to its
    language code so the row stays short (``de_core_news_sm`` → ``de``).
    """
    codes = [model.split("_")[0] for model in models if model]
    return f"On (spaCy {' + '.join(codes)})" if codes else "On"


def classify_ollama(
    model: str, *, url_is_local: bool, host: str, service: str = "Ollama"
) -> StageLocation:
    """Classify a step served by an Ollama server at *host* running *model*."""
    if is_ollama_cloud_model(model) or is_ollama_cloud_host(host):
        return StageLocation(DataLocation.ONLINE, "Ollama cloud")
    if url_is_local:
        return StageLocation(DataLocation.THIS_COMPUTER, service)
    return StageLocation(DataLocation.OTHER_COMPUTER, service, host)


def llm_stage_location(
    *, self_hosted: bool, model: str, url_is_local: bool, host: str
) -> DataLocation:
    """Return where a step that runs on an LLM provider processes its data.

    The one rule for "this step runs online", so the badge a teacher sees and the number of
    tries a request gets cannot disagree: a self-hosted provider follows its server
    (:func:`classify_ollama`), anything else is online.  A provider that does not declare
    ``"self_hosted": True`` is treated as a vendor-hosted online service — the safe default for
    a new provider.
    """
    if self_hosted:
        return classify_ollama(model, url_is_local=url_is_local, host=host).location
    return DataLocation.ONLINE


def online_ocr_reason(engine: str, model: str, host: str) -> str:
    """Return why OCR with these settings is not allowed, or ``""`` when it is.

    OCR must stay on a computer the user controls: a page cannot be anonymised before
    it is read.  That rules out every engine outside :data:`LOCAL_OCR_ENGINES` and
    Ollama cloud models / Ollama's hosted API.
    """
    if engine not in LOCAL_OCR_ENGINES:
        return (
            f"Text recognition with '{engine}' is not available: online services would "
            "receive the whole page, including names and grades. Use "
            f"{' or '.join(OCR_ENGINE_NAMES.values())} instead."
        )
    if engine == ENGINE_OLLAMA and (is_ollama_cloud_model(model) or is_ollama_cloud_host(host)):
        return (
            f"The Ollama model '{model}' runs on Ollama's servers, not on your computer. "
            "Choose a model that is downloaded to your own Ollama."
        )
    return ""


@dataclass(frozen=True)
class DataFlowStage:
    """One pipeline step with the place it runs and what it receives."""

    stage: str
    location: StageLocation
    #: What the step is given: ``"page images"`` or ``"text"``.
    receives: str
    #: Whether names and other personal data are replaced before the step sees them.  The
    #: replacement is automatic detection, so it can miss a name (see :attr:`DataFlow.advice`).
    personal_info_hidden: bool = False
    #: What the row says instead of a place, for display-only rows (personal-info removal:
    #: ``"On (spaCy en + de)"`` or ``"Off"``).
    detail: str = ""

    @property
    def title(self) -> str:
        """The step's name as the user knows it."""
        return _STAGE_TITLES.get(self.stage, self.stage)

    @property
    def leaves_computer(self) -> bool:
        """Whether the data leaves the user's own computer for this step."""
        return self.location.location is not DataLocation.THIS_COMPUTER

    @property
    def badge(self) -> str:
        """Short line for the status box: where it runs and how names are handled."""
        if self.detail:
            return self.detail
        parts = [self.location.label]
        if self.leaves_computer:
            parts.append("names hidden" if self.personal_info_hidden else "names as written")
        return " · ".join(parts)

    @property
    def privacy_level(self) -> PrivacyLevel:
        """What this step alone means for privacy; :attr:`DataFlow.level` takes the worst."""
        if self.location.location is DataLocation.THIS_COMPUTER:
            return PrivacyLevel.LOCAL
        if self.location.location is DataLocation.OTHER_COMPUTER:
            return PrivacyLevel.OWN_SERVER
        european = self.location.data_region is DataRegion.EU
        if self.personal_info_hidden:
            return PrivacyLevel.ANON_EU if european else PrivacyLevel.ANON_NON_EU
        return PrivacyLevel.NAMES_EU if european else PrivacyLevel.NAMES_NON_EU

    @property
    def summary(self) -> str:
        """One line: the step, where it runs, and (when it leaves) what it receives."""
        if self.detail:
            return f"{self.title}: {self.detail}"
        line = f"{self.title}: {self.location.label}"
        if not self.leaves_computer:
            return line
        names = (
            "names replaced where detected"
            if self.personal_info_hidden
            else "names sent as written"
        )
        return f"{line} — receives {self.receives}, {names}"


@dataclass(frozen=True)
class DataFlow:
    """Where every enabled step of a run takes place."""

    stages: tuple[DataFlowStage, ...]

    @property
    def outbound(self) -> tuple[DataFlowStage, ...]:
        """Steps whose data leaves this computer."""
        return tuple(s for s in self.stages if s.leaves_computer)

    @property
    def leaves_computer(self) -> bool:
        return bool(self.outbound)

    @property
    def level(self) -> PrivacyLevel:
        """The overall privacy level: the worst step of the run decides."""
        return max(
            (s.privacy_level for s in self.stages),
            key=lambda level: level.severity,
            default=PrivacyLevel.LOCAL,
        )

    @property
    def fingerprint(self) -> str:
        """Stable key of everything that leaves this computer, for "don't ask again".

        Versioned: ``v2`` adds the data region, so an answer given before the region was
        shown no longer matches and the user is asked once more.  Parts are separated by
        ``"|"`` and their fields by ``":"``; the host may itself contain a colon and is
        always the field before the last one.
        """
        parts = [
            ":".join(
                (
                    s.stage,
                    s.location.location.value,
                    s.location.data_region.value,
                    s.location.service,
                    s.location.host,
                    str(int(s.personal_info_hidden)),
                )
            )
            for s in self.outbound
        ]
        return f"{_FINGERPRINT_V2_PREFIX}|" + "|".join(parts) if parts else ""

    @property
    def outbound_summary(self) -> str:
        """The steps that leave this computer as one line, for a list of remembered answers."""
        return "; ".join(stage.summary for stage in self.outbound)

    @property
    def advice(self) -> tuple[Advice, ...]:
        """What the user can still do to keep more on this computer."""
        tips: list[Advice] = []
        ocr = self.stage(STAGE_OCR)
        if ocr is not None and ocr.leaves_computer:
            tips.append(Advice.PAGES_TO_OTHER_COMPUTER)
        text_steps = [s for s in self.outbound if s.stage != STAGE_OCR]
        if any(not s.personal_info_hidden for s in text_steps):
            tips.append(Advice.NAMES_SENT_AS_WRITTEN)
        elif text_steps:
            # Every online text step has its names replaced — but detection is not perfect.
            tips.append(Advice.NAMES_MAY_BE_MISSED)
        # Only a vendor has a region; a computer the user controls is not "outside the EU".
        online_steps = [s for s in text_steps if s.location.location is DataLocation.ONLINE]
        if any(s.location.data_region is not DataRegion.EU for s in online_steps):
            tips.append(Advice.SERVICE_OUTSIDE_EU)
        return tuple(tips)

    def stage(self, name: str) -> DataFlowStage | None:
        """Return the step called *name*, or ``None`` when it is not part of the run."""
        return next((s for s in self.stages if s.stage == name), None)


_FINGERPRINT_V2_PREFIX = "v2"


def describe_fingerprint(fingerprint: str) -> str:
    """Return a readable line for a stored :attr:`DataFlow.fingerprint`.

    Answers remembered by an earlier version kept only the fingerprint, which no person can
    read (``correction:online:Mistral::0``).  The line says the same as
    :attr:`DataFlowStage.summary` for each step, minus what the fingerprint does not hold; a
    part that does not parse is kept as it is, so nothing is ever hidden from the list.

    Both formats are understood: version 1 (``stage:where:service:host:hidden``) and
    version 2, which starts with ``v2|`` and adds the data region.
    """
    parts = fingerprint.split("|")
    if parts and parts[0] == _FINGERPRINT_V2_PREFIX:
        parts = parts[1:]
        lines = [_describe_part(part, with_region=True) for part in parts]
    else:
        lines = [_describe_part(part, with_region=False) for part in parts]
    return "; ".join(line for line in lines if line)


def _describe_part(part: str, *, with_region: bool) -> str:
    """Return one readable step of a stored fingerprint, or *part* when it does not parse."""
    fields = part.split(":")
    # stage, where, [region], service, host…, hidden: the host is everything before "hidden".
    minimum = 6 if with_region else 5
    if len(fields) < minimum:
        return part
    stage, where = fields[0], fields[1]
    rest = fields[2:]
    region: DataRegion | None = None
    if with_region:
        try:
            region = DataRegion(rest[0])
        except ValueError:
            return part
        rest = rest[1:]
    service, host, hidden = rest[0], rest[1:-1], rest[-1]
    try:
        place = StageLocation(DataLocation(where), service, ":".join(host), region).label
    except ValueError:
        return part
    names = "names replaced where detected" if hidden == "1" else "names sent as written"
    return f"{_STAGE_TITLES.get(stage, stage)}: {place} — {names}"
