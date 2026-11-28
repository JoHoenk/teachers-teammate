"""One run: the pages of a set, one engine, and the numbers that come out of them.

A run prepares every page the way the app prepares it under the chosen preset, reads it with the
chosen engine in the page's language, scores the answer against the transcription beside the page,
and writes one record of numbers.  Nothing of a page's text leaves the machine: the reading goes to
the local cache, the record holds the figures.

Every page is prepared even when its reading is cached — that is what the app does, and the
prepared image is part of the key.  A cached reading keeps the seconds it was read in, so a report's
time per page does not fall to nothing when the pages are read again.  Only the reading is timed;
preparing a page is not.

A page the run's deadline ended is an empty answer that was cut off, timed and cached like any
other: a model that loops has read nothing there, and a rerun does not wait for it again — unless
the rerun allows longer.  A page the engine failed otherwise is an empty answer too, timed, said on
stderr and read again next time.  A run that reads every page empty refuses to write a record.

A lost server or a model the server does not hold is not a page's failure: it ends the run, and no
record is written, because the pages read so far measure a model that was never asked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.data_location import ENGINE_OLLAMA
from teachers_teammate.domain.local_models import auto_identity
from teachers_teammate.exceptions import (
    ModelMissingError,
    OCRDeadlineError,
    OCRError,
    OllamaUnreachableError,
)
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing.identity import available_page_steps
from teachers_teammate.messages import message_of
from tools.evaluation.core.hashing import file_sha256
from tools.evaluation.core.revision import tool_revision

from . import candidates, metrics, pages, runs
from .engine import DEADLINE, READER, Answer, Ask, Engine, Model, Reader, detector_settings
from .prepare import Preparation, Prepared
from .presets import Preset
from .readings import ReadingCache, key_of
from .records import ALL, APP, Header, PageNumbers, RunRecord

LANGUAGE = "German"
"""The language a page is asked in when ``pages.jsonl`` names none for it."""

TIMEOUT = 900.0
"""How long one page's whole answer may take by default, in seconds."""


@dataclass(frozen=True)
class Plan:
    """What a run asks for, as the command line says it."""

    model: str
    """An Ollama tag, or ``reader`` for the built-in reader."""
    preset: Preset
    pages: Path
    """The page set's root: a folder per group, ``pages.jsonl`` beside them."""
    store: Path
    """The store the record goes to."""
    machine: str
    """The machine id the store's registry knows."""
    cache: Path
    prompt: str = APP
    """``app`` for the app's own OCR prompt, ``card`` for the one the model's card names."""
    language: str = LANGUAGE
    cpu_only: bool = False
    url: str = ""
    timeout: float | None = TIMEOUT
    """How long the whole answer may take, so a model that loops cannot hold a run."""
    detector: tuple[tuple[str, float], ...] = ()
    split: str = ALL
    folder: str = ""


def engine_of(
    plan: Plan, *, store: ModelStore | None = None, client: OllamaClient | None = None
) -> Engine:
    """Return the engine *plan* names: the reader, or a model through *client*.

    Raises:
        ValueError: for a card prompt no card of the model names.
    """
    if plan.model == READER:
        return Reader(store or ModelStore(), settings=detector_settings(plan.detector))
    return Model(
        client or OllamaClient(_url(plan)),
        plan.model,
        prompt=plan.prompt,
        cpu_only=plan.cpu_only,
        total_timeout=plan.timeout,
    )


def execute(
    plan: Plan, *, engine: Engine | None = None, store: ModelStore | None = None
) -> RunRecord:
    """Read every page of *plan*'s set and return the record, without writing it.

    Raises:
        ValueError: if the set cannot be scored (see :func:`pages.load`), the side of the set
            asked for holds no page, the preset leaves out a step the app does not have, the
            server does not hold the model, or not one page could be read.
    """
    page_set = pages.load(plan.pages)
    wanted = page_set.side(plan.split)
    reader_store = store or ModelStore()
    session = _Session(plan, engine or engine_of(plan, store=reader_store), reader_store)
    numbers = session.read(page_set, wanted)
    if session.empty == len(wanted):
        raise ValueError(
            f"{len(wanted)} page(s) read, every one of them empty: check the tag and its card"
        )
    return RunRecord(
        header=Header(
            model=session.first.model,
            engine=session.first.engine,
            prompt=plan.prompt,
            preset=plan.preset.name,
            split=plan.split,
            digest=session.first.digest,
            auto_identity=session.identity,
            detector=session.first.detector,
            set_version=page_set.version,
            pages=len(numbers),
            language=",".join(sorted(session.languages)),
            machine=plan.machine,
            cpu_only=session.first.cpu_only,
            revision=tool_revision(),
            licence="" if plan.model == READER else candidates.licence(plan.model),
            memory_mb=session.memory_mb,
        ),
        pages=numbers,
    )


def run(
    plan: Plan,
    *,
    engine: Engine | None = None,
    store: ModelStore | None = None,
    replace: bool = False,
) -> tuple[Path, RunRecord]:
    """Read the set, write the run's record to the store and return the file and the record.

    Raises:
        FileExistsError: when a run of this name is there and *replace* is not asked for.
    """
    record = execute(plan, engine=engine, store=store)
    path = runs.run_path(plan.store, runs.run_name(record.header), folder=plan.folder)
    return runs.write(path, record, replace=replace), record


def _url(plan: Plan) -> str:
    """Return the Ollama server's address as *plan* names it, or the app's default."""
    return plan.url or str(DEFAULTS["ollama_url"])


class _Session:
    """One run's reading of its pages: the engine, the preparation, the cache, what it saw.

    Built once per run: the steps of the preparation and the engine's digest are asked once.
    """

    def __init__(self, plan: Plan, engine: Engine, store: ModelStore) -> None:
        """Ask the engine what it is and build the preparation, before any page is read.

        Raises:
            ValueError: for a model the server does not hold, or a preset the app cannot build.
        """
        self._plan = plan
        self._engine = engine
        self._cache = ReadingCache(plan.cache)
        self.first = engine.ask(plan.language)
        """What the engine is asked; only the prompt changes with a page's language."""
        if self.first.engine == ENGINE_OLLAMA and not self.first.digest:
            raise ValueError(
                f"{self.first.model}: the server at {_url(plan)} does not hold it, or did not answer"
            )
        self.identity = auto_identity(self.first.engine, available_page_steps(store))
        self._preparation = Preparation.of(plan.preset, engine=self.first.engine, store=store)
        self._asks: dict[str, Ask] = {}
        self._loaded = False
        self.memory_mb = 0
        self.empty = 0
        self.languages: set[str] = set()

    def read(
        self, page_set: pages.PageSet, wanted: Sequence[pages.Case]
    ) -> tuple[PageNumbers, ...]:
        """Prepare, read and score every page of *wanted*, in order."""
        numbers: list[PageNumbers] = []
        renders: dict[Path, tuple[Path, ...]] = {}
        with TemporaryDirectory(prefix="ocr-eval-") as temporary:
            folder = Path(temporary)
            for index, case in enumerate(wanted):
                meta = page_set.meta[case.page_id]
                language = meta.language or self._plan.language
                self.languages.add(language)
                out = folder / f"page{index}"
                out.mkdir()
                prepared = self._preparation.prepare(_page_image(case, folder, renders), out)
                answer = self._answer(case, prepared, language)
                self.empty += 0 if answer.text.strip() else 1
                self.memory_mb = max(self.memory_mb, answer.memory_mb)
                numbers.append(
                    _numbers(case, prepared, answer, page_set.sides[case.page_id], meta.writer)
                )
        return tuple(numbers)

    def _ask(self, language: str) -> Ask:
        """Return what the engine is asked of a page in *language*, asking once per language."""
        if language not in self._asks:
            self._asks[language] = self._engine.ask(language)
        return self._asks[language]

    def _answer(self, case: pages.Case, prepared: Prepared, language: str) -> Answer:
        """Return *prepared* as the engine read it: from the cache, or read now and cached."""
        key = key_of(
            self._ask(language),
            image_sha256=file_sha256(prepared.image),
            auto_identity=self.identity,
            machine=self._plan.machine,
        )
        cached = self._cache.get(key)
        if cached is not None and not self._allows_longer(cached):
            return cached
        started: float | None = None
        try:
            if not self._loaded:
                self._engine.warm_up()
                self._loaded = True
            started = time.perf_counter()
            answer = self._engine.read(prepared.image, language)
        except OllamaUnreachableError, ModelMissingError:
            raise
        except OCRDeadlineError:
            given = max(_since(started), self._plan.timeout or 0.0)
            answer = Answer(text="", seconds=given, done_reason=DEADLINE)
        except OCRError as exc:
            print(f"{case.page_id}: {message_of(exc).key}, read as empty", file=sys.stderr)
            return Answer(text="", seconds=_since(started))
        self._cache.put(key, answer)
        return answer

    def _allows_longer(self, cached: Answer) -> bool:
        """Whether *cached* ran into a deadline this run would have waited longer for."""
        if cached.done_reason != DEADLINE:
            return False
        return self._plan.timeout is None or self._plan.timeout > cached.seconds + 1.0


def _since(started: float | None) -> float:
    """Return the seconds since *started*, or 0 when the reading never began."""
    return 0.0 if started is None else time.perf_counter() - started


def _page_image(case: pages.Case, folder: Path, renders: dict[Path, tuple[Path, ...]]) -> Path:
    """Return the file *case* reads: its image, or the page of its PDF rendered the app's way."""
    if case.page is None:
        return case.image
    if case.image not in renders:
        out = folder / f"render_{len(renders)}"
        out.mkdir()
        renders[case.image] = pages.rendered(case.image, out)
    found = renders[case.image]
    if case.page >= len(found):
        raise ValueError(f"{case.image.name}: page {case.page + 1} could not be rendered")
    return found[case.page]


def _numbers(
    case: pages.Case, prepared: Prepared, answer: Answer, side: str, writer: str
) -> PageNumbers:
    """Score one page and return the numbers a record keeps of it."""
    score = metrics.score(answer.text, case.reference, cut_off=answer.cut_off)
    return PageNumbers(
        page=case.page_id,
        group=case.group,
        split=side,
        cer=score.cer,
        wer=score.wer,
        bag_wer=score.bag_wer,
        kept=score.kept,
        words=score.words,
        characters=score.characters,
        writer=writer,
        flags=score.flags.names(),
        seconds=round(answer.seconds, 3),
        answer_tokens=answer.answer_tokens,
        steps=prepared.steps,
    )
