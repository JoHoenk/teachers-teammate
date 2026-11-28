"""The app's own ways of reading a page: the built-in reader, or a model through Ollama.

Both ask the page the way a run asks it.  The reader is ``PpOcrProcessor(store, settings=...)``,
read line by line; a model is the app's OCR request — the app's prompt in the page's language, the
app's options (:func:`ocr_options`), its image encoding and, for a CPU-only run, ``num_gpu 0`` —
sent through ``OllamaClient.chat_with_stats`` rather than ``OllamaOCRProcessor``, because a
comparison needs the answer's tokens and the reason it stopped.  A cut-off answer is kept, as the
app keeps it.

What this module adds is only what a run does not measure: the seconds a page took, the tokens of
the answer, and the memory the model took.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path
import resource
import sys
import time
from typing import Protocol

from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.data_location import ENGINE_OLLAMA, ENGINE_PPOCR
from teachers_teammate.domain.local_models import reader_fingerprint
from teachers_teammate.domain.model_tags import normalize_tag
from teachers_teammate.domain.ocr_prompt import get_ocr_prompt
from teachers_teammate.domain.request_profile import OCR_TEMPERATURE
from teachers_teammate.infrastructure.ocr_processor import encode_image, ocr_options
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.performance_probe import (
    WARMUP_ANSWER_TOKENS,
    WARMUP_PROMPT,
)
from teachers_teammate.infrastructure.ppocr.detect import DetectorSettings
from teachers_teammate.infrastructure.ppocr.engine import PpOcrProcessor
from tools.evaluation.core.constants import CPU_ONLY_OPTIONS

from . import candidates
from .records import APP, CARD

READER = "reader"
"""The name a run writes for the built-in reader, where an Ollama run writes a model tag."""

LIMITS = tuple(sorted((name, float(value)) for name, value in ocr_options(OCR_TEMPERATURE).items()))
"""The app's OCR options — token limit and temperature — as a reading's key holds them."""

DETECTOR = tuple(field.name for field in fields(DetectorSettings))
"""The detector's numbers, by the name a ``--detector`` override uses."""

DEADLINE = "deadline"
"""The ``done_reason`` of an answer the run's deadline ended: the tool's own, Ollama has none."""

_BYTES_PER_MIB = 1024 * 1024
"""Ollama counts the memory of a model in bytes; a record keeps whole MiB."""

_CUT_OFF = frozenset({"length", DEADLINE})
"""The reasons an answer stopped before the end of the page: the token limit, or the deadline."""


@dataclass(frozen=True)
class Ask:
    """What one engine is asked of one page: everything its answer depends on."""

    engine: str
    """The app's engine id, ``ollama`` or ``ppocr``."""
    model: str
    """The tag a record names this engine by, or ``reader``."""
    digest: str
    """What makes this engine's answers their own: revisions, or a tag's digest."""
    prompt: str
    """The text a run sends with the page; the reader is asked for no text."""
    limits: tuple[tuple[str, float], ...] = LIMITS
    """The token limit and the temperature the request carries."""
    detector: tuple[tuple[str, float], ...] = ()
    """The reader's detector numbers; a model's request has none."""
    cpu_only: bool = False
    """Whether the model is kept off the graphics card, which makes the seconds other seconds."""


@dataclass(frozen=True)
class Answer:
    """One page as one engine read it, and what it cost; the cache keeps it as it is."""

    text: str
    seconds: float = 0.0
    answer_tokens: int = 0
    done_reason: str = ""
    """Ollama's reason for stopping, or :data:`DEADLINE`; ``""`` for the reader, which has none."""
    memory_mb: int = 0

    @property
    def cut_off(self) -> bool:
        """Whether the token limit or the deadline, not the end of the page, ended the answer."""
        return self.done_reason in _CUT_OFF


class Engine(Protocol):
    """What a run reads a page with: the reader or a model, asked the app's way."""

    @property
    def model(self) -> str:
        """The tag a record names this engine by."""

    def ask(self, language: str) -> Ask:
        """Return what this engine is asked of a page in *language*: all its answer depends on."""

    def warm_up(self) -> None:
        """Load what has to be loaded, so no page's time carries the load."""

    def read(self, image: Path, language: str) -> Answer:
        """Read one page in *language*."""


def detector_settings(overrides: tuple[tuple[str, float], ...] = ()) -> DetectorSettings:
    """Return the detector's numbers: the app's own, with *overrides* set.

    Raises:
        ValueError: naming a setting the detector does not have.
    """
    unknown = [key for key, _value in overrides if key not in DETECTOR]
    if unknown:
        raise ValueError(
            f"{', '.join(unknown)}: not a detector setting; name one of {', '.join(DETECTOR)}"
        )
    settings = DetectorSettings()
    for name, value in overrides:
        settings = replace(settings, **{name: type(getattr(settings, name))(value)})
    return settings


def numbers_of(settings: DetectorSettings) -> tuple[tuple[str, float], ...]:
    """Return a detector's numbers as a record keeps them: sorted, and only the numbers."""
    return tuple((name, float(getattr(settings, name))) for name in sorted(DETECTOR))


class Reader:
    """The built-in reader, built the way a run builds it; it reads every language alike."""

    def __init__(self, store: ModelStore, *, settings: DetectorSettings | None = None) -> None:
        self._store = store
        self._settings = settings or DetectorSettings()
        self._reader: PpOcrProcessor | None = None

    @property
    def model(self) -> str:
        """The name a record gives the reader: ``reader``, never a tag."""
        return READER

    def ask(self, language: str) -> Ask:  # noqa: ARG002  # one model reads every language
        """Return what the reader is asked: its models' revisions and detector numbers, no prompt."""
        return Ask(
            engine=ENGINE_PPOCR,
            model=READER,
            digest=reader_fingerprint(),
            prompt="",
            detector=numbers_of(self._settings),
        )

    def warm_up(self) -> None:
        """Load the models, so the first page's time is the page's and not the load's."""
        self._processor()

    def read(self, image: Path, language: str) -> Answer:
        """Read one page and return its text, the seconds it took and the peak memory."""
        started = time.perf_counter()
        reading = self._processor().read_page(image, language)
        seconds = time.perf_counter() - started
        return Answer(text=reading.text, seconds=seconds, memory_mb=_peak_memory_mb())

    def _processor(self) -> PpOcrProcessor:
        """Return the reader, building it once."""
        if self._reader is None:
            self._reader = PpOcrProcessor(self._store, settings=self._settings)
        return self._reader


class Model:
    """A reading model through the Ollama server, asked exactly as the app asks it."""

    def __init__(
        self,
        client: OllamaClient,
        tag: str,
        *,
        prompt: str = APP,
        cpu_only: bool = False,
        total_timeout: float | None = None,
    ) -> None:
        """Build the model *tag*, asked with the app's prompt or with its card's.

        Raises:
            ValueError: for ``card`` when no card prompt is transcribed for *tag*.
        """
        self._client = client
        self.tag = tag
        self.cpu_only = cpu_only
        self._card = candidates.card_prompt(tag) if prompt == CARD else ""
        if prompt == CARD and not self._card:
            raise ValueError(f"{tag}: no card prompt is transcribed for it; ask it with the app's")
        self._total_timeout = total_timeout
        self._timeout = float(DEFAULTS["ocr_timeout"])
        self._digest: str | None = None

    @property
    def model(self) -> str:
        """The tag a record names this model by."""
        return self.tag

    def prompt(self, language: str) -> str:
        """Return the text a page in *language* is sent with: the card's, or the app's."""
        return self._card or get_ocr_prompt(language)

    def ask(self, language: str) -> Ask:
        """Return what the model is asked: the prompt, the tag's digest, the device it runs on.

        The digest is asked of the server once; ``""`` when the server did not say.
        """
        if self._digest is None:
            self._digest = self._client.server_facts().digest_of(self.tag)
        return Ask(
            engine=ENGINE_OLLAMA,
            model=self.tag,
            digest=self._digest,
            prompt=self.prompt(language),
            cpu_only=self.cpu_only,
        )

    def warm_up(self) -> None:
        """Load the model with a one-token request, so no page's time carries the load.

        A CPU-only run warms up on the processor too: warming up on the card would load the
        weights where the pages are then not read.
        """
        self._client.chat_with_stats(
            self.tag,
            WARMUP_PROMPT,
            options=self._on_device({"num_predict": WARMUP_ANSWER_TOKENS}),
            timeout=self._timeout,
        )

    def read(self, image: Path, language: str) -> Answer:
        """Read one page and return its text, the seconds it took and the answer's tokens."""
        encoded = encode_image(image)
        started = time.perf_counter()
        text, stats = self._client.chat_with_stats(
            self.tag,
            self.prompt(language),
            images=[encoded],
            options=self._on_device(ocr_options(OCR_TEMPERATURE)),
            timeout=self._timeout,
            total_timeout=self._total_timeout,
        )
        seconds = time.perf_counter() - started
        return Answer(
            text=text,
            seconds=seconds,
            answer_tokens=stats.eval_count,
            done_reason=stats.done_reason,
            memory_mb=self.memory_mb(),
        )

    def memory_mb(self) -> int:
        """Return the memory the server holds for this model, in whole MiB."""
        wanted = normalize_tag(self.tag)
        for loaded in self._client.loaded_models():
            if normalize_tag(loaded.name) == wanted:
                return round(loaded.size_bytes / _BYTES_PER_MIB)
        return 0

    def _on_device(self, options: dict[str, float | int]) -> dict[str, float | int]:
        """Return *options* with the device the model is kept on."""
        return {**options, **CPU_ONLY_OPTIONS} if self.cpu_only else options


def _peak_memory_mb() -> int:
    """Return this process's peak memory in MiB: what the reader's models take.

    ``ru_maxrss`` is in KiB on Linux and in bytes on macOS, so the unit is what tells them apart.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (_BYTES_PER_MIB if sys.platform == "darwin" else 1024))
