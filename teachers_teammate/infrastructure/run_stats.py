"""Turning the requests of a real run into samples of how fast this machine is.

The speed test measures a model on a test page; a real run measures it on the teacher's own
documents, which is the evidence that matters.  The Ollama client reports what the server said
about every request (:attr:`~.ollama_utils.OllamaClient.stats_sink`), and this collector groups
those reports by the step that sent them: one sample per page for text recognition, one per
document for proofreading and for the content review.

A step that sent no request - a result taken from the cache - gives no sample.  That is the
point: a cached page says nothing about how fast the machine is today.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading

from ..domain.performance_estimate import SpeedStep
from .ollama_utils import ChatStats, normalize_ollama_url
from .speed_store import RunSample, SampleKey, now_stamp

_logger = logging.getLogger(__name__)

#: A request that spent at least this long loading the model did not find it in memory.  Below
#: it the figure is noise: Ollama reports a few milliseconds even for a model it just used.
LOAD_THRESHOLD_S = 1.0

_NANOS_PER_S = 1_000_000_000


class RunStatsCollector:
    """Collects the statistics of one step's requests and turns them into one sample.

    The pipeline marks which step is running (:meth:`begin`) and, when the step ends, asks for
    the sample (:meth:`finish`).  The Ollama client hands every request's statistics to
    :meth:`record`; a request that arrives while no step is marked - the model check, the speed
    test - is dropped, because it belongs to no page and no document.

    One collector serves the whole run, and a run processes one file at a time, so the buffer
    holds the requests of exactly one step.  The lock is there because the client may report
    from the thread that reads the answer, not from the one that started the step.
    """

    def __init__(
        self,
        server: str,
        sink: Callable[[SampleKey, RunSample], None] | None = None,
    ) -> None:
        """Create the collector.

        Args:
            server: The Ollama URL the run talks to; normalised here, because every lookup of
                the samples asks for the normalised one.
            sink: Called with the key and the sample of every finished step; the speed store's
                ``add`` in a run, a list in a test.
        """
        self._server = normalize_ollama_url(server)
        self._sink = sink
        self._lock = threading.Lock()
        self._step: SpeedStep | None = None
        self._tag = ""
        self._stats: list[ChatStats] = []

    def begin(self, step: SpeedStep) -> None:
        """Mark *step* as running, forgetting whatever an earlier step left behind."""
        with self._lock:
            self._step = step
            self._tag = ""
            self._stats = []

    def discard(self) -> None:
        """Forget the step that is running: its requests say nothing about the machine.

        For a step that failed, or that answered in another mode than the one it is filed
        under (a review that fell back to corrections only): the requests of the failed attempt
        would be counted into the answer length of the mode that finally answered.
        """
        with self._lock:
            self._step, self._tag, self._stats = None, "", []

    def record(self, model: str, stats: ChatStats) -> None:
        """Take the statistics of one request (the Ollama client's sink)."""
        with self._lock:
            if self._step is None:
                return
            if not self._tag:
                self._tag = model
            self._stats.append(stats)

    def finish(self, *, words: int = 0, mode: str = "") -> RunSample | None:
        """Return the sample of the step that just ended, and hand it to the sink.

        Args:
            words: Words of the text that went through: the page's text for text recognition,
                the text that was proofread or reviewed for the other steps.
            mode: ``"review"`` or ``"text"`` for proofreading, ``""`` for the other steps.

        Returns:
            The sample, or ``None`` when the step sent no request (a result from the cache) or
            when the server reported no time for it: a sample without a rate would only dilute
            the median.
        """
        with self._lock:
            step, tag, stats = self._step, self._tag, self._stats
            self._step, self._tag, self._stats = None, "", []
        if step is None or not tag or not stats:
            return None
        sample = _sample_of(step, stats, words=words)
        if sample is None:
            return None
        if self._sink is not None:
            try:
                self._sink(SampleKey(self._server, tag, step, mode), sample)
            except OSError as exc:
                # Collecting statistics must never fail a document: a full or read-only disk
                # only costs the sample.
                _logger.warning("Could not save a speed sample: %s", exc)
        return sample


def _sample_of(step: SpeedStep, stats: list[ChatStats], *, words: int) -> RunSample | None:
    """Return the sample the requests *stats* of *step* add up to, or ``None`` without a rate."""
    read_tokens = sum(item.prompt_eval_count for item in stats)
    read_ns = sum(item.prompt_eval_duration for item in stats)
    answer_tokens = sum(item.eval_count for item in stats)
    write_ns = sum(item.eval_duration for item in stats)
    if read_tokens <= 0 or read_ns <= 0 or answer_tokens <= 0 or write_ns <= 0:
        return None
    load_ns = max(item.load_duration for item in stats)
    return RunSample(
        read_tps=read_tokens / (read_ns / _NANOS_PER_S),
        write_tps=answer_tokens / (write_ns / _NANOS_PER_S),
        load_s=load_ns / _NANOS_PER_S if load_ns >= LOAD_THRESHOLD_S * _NANOS_PER_S else 0.0,
        # A page request is the image plus a short instruction, so what the server read is
        # mostly the image: the closest figure for its size that a run can report.
        image_tokens=read_tokens if step is SpeedStep.READING else 0,
        answer_tokens=answer_tokens,
        words=words,
        pieces=len(stats),
        at=now_stamp(),
    )
