"""Where each pipeline step runs and what leaves the user's computer.

Pure rules, no I/O: the application layer resolves the facts that need the outside
world (is this Ollama URL this computer? what does a provider declare?) and passes
them in.  The GUI and CLI only render the result.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum

from .stages import Stage


class OcrEngine(StrEnum):
    """The engines that read pages on a computer the user controls.

    Reading a page needs the whole page (names, grades, handwriting), so personal information
    cannot be hidden first: online services are deliberately not offered here.
    """

    OLLAMA = "ollama"
    PPOCR = "ppocr"


ENGINE_OLLAMA = OcrEngine.OLLAMA
ENGINE_PPOCR = OcrEngine.PPOCR

#: The provider id of the server an engine and a proofreading step can share: `providers/ollama.py`.
#: Code that asks the same question about the provider ("runs on a server the teacher names")
#: reads ``ProviderInfo.self_hosted`` instead.
PROVIDER_OLLAMA = "ollama"


def is_ollama(provider: object) -> bool:
    """Return whether *provider* is the Ollama server the teacher names, not a vendor's service."""
    return provider == PROVIDER_OLLAMA


#: The name shown for each engine.
OCR_ENGINE_NAMES: dict[str, str] = {
    ENGINE_OLLAMA: "Ollama",
    ENGINE_PPOCR: "Built-in reader",
}
LOCAL_OCR_ENGINES: tuple[str, ...] = tuple(OCR_ENGINE_NAMES)

#: Engines of older versions that were removed, and what replaces them.  Both were local, so the
#: replacement is local too: the built-in reader also reads pages on this computer.
RETIRED_OCR_ENGINES: dict[str, str] = {"tesseract": ENGINE_PPOCR, "paddleocr": ENGINE_PPOCR}

#: How a teacher knew each retired engine (for the notice that it was replaced).
RETIRED_ENGINE_NAMES: dict[str, str] = {"tesseract": "Tesseract", "paddleocr": "PaddleOCR"}

#: Not a pipeline stage: the box lists personal-info removal to show whether it is on.
STAGE_ANONYMIZATION = "anonymization"


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
    #: Host name of the Ollama server (another computer, or one on the internet); empty for a
    #: vendor, whose address is not the teacher's to choose.
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
        if self.location is DataLocation.ONLINE and self.host:
            parts.append(f"Server: {self.host}")
        if self.location is DataLocation.ONLINE and self.vendor_country:
            parts.append(f"Vendor based in {self.vendor_country}")
        return "\n".join(parts)


def anonymization_languages(models: Iterable[str]) -> tuple[str, ...]:
    """Return the language codes of the spaCy models that will be loaded.

    ``["de_core_news_sm", "en_core_web_sm"]`` → ``("de", "en")``.  A sentence that names the
    languages turns them into names with
    :func:`~teachers_teammate.domain.language.language_name`.
    """
    return tuple(model.split("_")[0] for model in models if model)


def anonymization_badge(models: Iterable[str]) -> str:
    """Return what the personal-info-removal row says, e.g. ``On (spaCy en + de)`` or ``Off``.

    *models* are the spaCy models that will actually be loaded; each is shortened to its
    language code so the row stays short (``de_core_news_sm`` → ``de``).
    """
    codes = anonymization_languages(models)
    return f"On (spaCy {' + '.join(codes)})" if codes else "On"


def classify_ollama(
    model: str, *, url_is_local: bool, host: str, host_is_private: bool, service: str = "Ollama"
) -> StageLocation:
    """Classify a step served by an Ollama server at *host* running *model*.

    A server that is neither this computer nor on a local network is an online service: the
    data crosses the internet to reach it, so it is shown and confirmed like a vendor.  A
    private address (``192.168.1.5``, ``nas.local``) stays another computer the teacher
    controls.
    """
    if is_ollama_cloud_model(model) or is_ollama_cloud_host(host):
        return StageLocation(DataLocation.ONLINE, "Ollama cloud")
    if url_is_local:
        return StageLocation(DataLocation.THIS_COMPUTER, service)
    if not host_is_private:
        return StageLocation(DataLocation.ONLINE, service, host)
    return StageLocation(DataLocation.OTHER_COMPUTER, service, host)


def llm_stage_location(
    *, self_hosted: bool, model: str, url_is_local: bool, host: str, host_is_private: bool
) -> DataLocation:
    """Return where a step that runs on an LLM provider processes its data.

    The one rule for "this step runs online", so the badge a teacher sees and the number of
    tries a request gets cannot disagree: a self-hosted provider follows its server
    (:func:`classify_ollama`), anything else is online.  A provider that does not declare
    ``"self_hosted": True`` is treated as a vendor-hosted online service — the safe default for
    a new provider.
    """
    if self_hosted:
        return classify_ollama(
            model, url_is_local=url_is_local, host=host, host_is_private=host_is_private
        ).location
    return DataLocation.ONLINE


def online_ocr_reason(
    engine: str, model: str, host: str, *, url_is_local: bool, host_is_private: bool
) -> str:
    """Return why OCR with these settings is not allowed, or ``""`` when it is.

    OCR must stay on a computer the user controls: a page cannot be anonymised before
    it is read.  That rules out every engine outside :data:`LOCAL_OCR_ENGINES`, Ollama cloud
    models / Ollama's hosted API, and a server that is neither this computer nor on a local
    network — page images would cross the internet.
    """
    if engine not in LOCAL_OCR_ENGINES:
        return (
            f"Text recognition with '{engine}' is not available: online services would "
            "receive the whole page, including names and grades. Use "
            f"{' or '.join(OCR_ENGINE_NAMES.values())} instead."
        )
    if engine != ENGINE_OLLAMA:
        return ""
    if is_ollama_cloud_model(model) or is_ollama_cloud_host(host):
        return (
            f"The Ollama model '{model}' runs on Ollama's servers, not on your computer. "
            "Choose a model that is downloaded to your own Ollama."
        )
    if not url_is_local and not host_is_private:
        return (
            f"The Ollama server at '{host}' is not on your computer or your local network, "
            "so the page images would be sent over the internet. Choose a server on this "
            "computer or on your own network."
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
    #: The languages whose names are hidden, for personal-info removal only (``("de", "en")``).
    languages: tuple[str, ...] = ()

    @property
    def leaves_computer(self) -> bool:
        """Whether the data leaves the user's own computer for this step."""
        return self.location.location is not DataLocation.THIS_COMPUTER

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
    def advice(self) -> tuple[Advice, ...]:
        """What the user can still do to keep more on this computer."""
        tips: list[Advice] = []
        ocr = self.stage(Stage.OCR)
        if ocr is not None and ocr.leaves_computer:
            tips.append(Advice.PAGES_TO_OTHER_COMPUTER)
        text_steps = [s for s in self.outbound if s.stage != Stage.OCR]
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


# ── How a stored result was sent ──────────────────────────────────────────────


@dataclass(frozen=True)
class SentRecord:
    """How one stored result was sent: the inputs of its classification, not the verdict.

    Stored with the result it belongs to and never overwritten while that result exists, so
    the window can always say what really went out — even after the privacy settings changed.
    The inputs are kept rather than the classification, so a changed rule changes the wording
    and not the stored data.
    """

    #: Provider id the step ran on (``"ollama"``, ``"mistral"``, …).
    provider: str = ""
    #: Model tag the step ran with.
    model: str = ""
    #: What decides the location besides provider and model: the Ollama host (this computer or
    #: another computer; a cloud model is in the model tag), or the base URL / regional
    #: endpoint a provider's ``data_region(**settings)`` reads.  Empty where neither applies.
    endpoint: str = ""
    #: Number of placeholders the text was sent with; 0 when names went as written.
    names_hidden: int = 0
    #: Whether hiding names was switched on, so ``names_hidden == 0`` can be told apart:
    #: nothing found to hide (on) versus names sent as written (off).
    hiding_on: bool = False
    #: Hash of the anonymizer settings the text was made with (on/off, resolved models,
    #: patterns), so "made with other privacy settings" needs no run to answer.
    privacy: str = ""

    def to_dict(self) -> dict:
        """Return the record as the JSON-ready dict the state stores."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> SentRecord | None:
        """Return the record stored in *data*, or ``None`` when it is not one of ours.

        A record without a provider says nothing about where its result was made, so it is
        treated as no record at all rather than shown as an unnamed online service.
        """
        if not isinstance(data, dict):
            return None
        texts = [data.get(key, "") for key in ("provider", "model", "endpoint", "privacy")]
        if not all(isinstance(value, str) for value in texts):
            return None
        names_hidden = data.get("names_hidden", 0)
        if not isinstance(names_hidden, int) or isinstance(names_hidden, bool):
            return None
        hiding_on = data.get("hiding_on", False)
        if not isinstance(hiding_on, bool):
            return None
        provider, model, endpoint, privacy = texts
        if not provider:
            return None
        # A record from before ``hiding_on`` existed, with names hidden, was made with it on.
        return cls(provider, model, endpoint, names_hidden, hiding_on or names_hidden > 0, privacy)


@dataclass(frozen=True)
class SentStatus:
    """How a stored result was sent, and whether today's settings would send it differently."""

    record: SentRecord
    #: The record's privacy fingerprint differs from the current settings', so the result was
    #: made with other privacy settings.  A fact the window shows; sending again is the
    #: teacher's choice, because what was already sent cannot be unsent.
    other_privacy_settings: bool = False


@dataclass(frozen=True)
class FingerprintPart:
    """One step of a stored :attr:`DataFlow.fingerprint`, taken apart again."""

    stage: str
    location: StageLocation
    #: ``True`` when the text was sent with names replaced where they were detected.
    names_hidden: bool


def fingerprint_parts(fingerprint: str) -> tuple[FingerprintPart | str, ...]:
    """Return one entry per step of a stored :attr:`DataFlow.fingerprint`.

    A part that does not parse comes back as the string it is, so a line that describes the
    fingerprint can show it instead of hiding it.

    Both formats are understood: version 1 (``stage:where:service:host:hidden``) and
    version 2, which starts with ``v2|`` and adds the data region.
    """
    parts = fingerprint.split("|")
    with_region = bool(parts) and parts[0] == _FINGERPRINT_V2_PREFIX
    return tuple(
        _fingerprint_part(part, with_region=with_region)
        for part in (parts[1:] if with_region else parts)
    )


def _fingerprint_part(part: str, *, with_region: bool) -> FingerprintPart | str:
    """Return one step of a stored fingerprint, or *part* when it does not parse."""
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
        location = StageLocation(DataLocation(where), service, ":".join(host), region)
    except ValueError:
        return part
    return FingerprintPart(stage, location, hidden == "1")
