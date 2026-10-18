"""Where each pipeline step runs and what leaves the user's computer.

Pure rules, no I/O: the application layer resolves the facts that need the outside
world (is this Ollama URL this computer? what does a provider declare?) and passes
them in.  The GUI and CLI only render the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: OCR engines that read pages on a computer the user controls, with the name shown for
#: each.  Reading a page needs the whole page (names, grades, handwriting), so personal
#: information cannot be hidden first: online services are deliberately not offered here.
OCR_ENGINE_NAMES: dict[str, str] = {
    "ollama": "Ollama",
    "tesseract": "Tesseract",
    "paddleocr": "PaddleOCR",
}
LOCAL_OCR_ENGINES: tuple[str, ...] = tuple(OCR_ENGINE_NAMES)

STAGE_OCR = "ocr"
STAGE_CORRECTION = "correction"
STAGE_EVALUATION = "evaluation"


_STAGE_TITLES = {
    STAGE_OCR: "Text recognition",
    STAGE_CORRECTION: "Proofreading",
    STAGE_EVALUATION: "Content review",
}


class DataLocation(StrEnum):
    """Where a step's data is processed, from most to least private."""

    THIS_COMPUTER = "this_computer"
    OTHER_COMPUTER = "other_computer"
    ONLINE = "online"


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


@dataclass(frozen=True)
class StageLocation:
    """Where one step runs and which service does the work."""

    location: DataLocation
    #: Display name of the engine or provider ("Ollama", "Tesseract", "OpenAI").
    service: str
    #: Host name of the other computer (OTHER_COMPUTER only).
    host: str = ""

    @property
    def label(self) -> str:
        """Plain-language place, e.g. ``On this computer`` or ``Online service (OpenAI)``."""
        if self.location is DataLocation.THIS_COMPUTER:
            return "On this computer"
        if self.location is DataLocation.OTHER_COMPUTER:
            return f"On another computer ({self.host})" if self.host else "On another computer"
        return f"Online service ({self.service})"


def classify_ollama(
    model: str, *, url_is_local: bool, host: str, service: str = "Ollama"
) -> StageLocation:
    """Classify a step served by an Ollama server at *host* running *model*."""
    if is_ollama_cloud_model(model) or is_ollama_cloud_host(host):
        return StageLocation(DataLocation.ONLINE, "Ollama cloud")
    if url_is_local:
        return StageLocation(DataLocation.THIS_COMPUTER, service)
    return StageLocation(DataLocation.OTHER_COMPUTER, service, host)


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
            f"{', '.join(LOCAL_OCR_ENGINES)} instead."
        )
    if engine == "ollama" and (is_ollama_cloud_model(model) or is_ollama_cloud_host(host)):
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

    @property
    def title(self) -> str:
        """The step's name as the user knows it."""
        return _STAGE_TITLES.get(self.stage, self.stage)

    @property
    def leaves_computer(self) -> bool:
        """Whether the data leaves the user's own computer for this step."""
        return self.location.location is not DataLocation.THIS_COMPUTER

    @property
    def summary(self) -> str:
        """One line: the step, where it runs, and (when it leaves) what it receives."""
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
    def fingerprint(self) -> str:
        """Stable key of everything that leaves this computer, for "don't ask again"."""
        return "|".join(
            f"{s.stage}:{s.location.location.value}:{s.location.service}:{s.location.host}"
            f":{int(s.personal_info_hidden)}"
            for s in self.outbound
        )

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
        return tuple(tips)

    def stage(self, name: str) -> DataFlowStage | None:
        """Return the step called *name*, or ``None`` when it is not part of the run."""
        return next((s for s in self.stages if s.stage == name), None)
