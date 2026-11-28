"""One proofreading unit for one config: what a run does per file, a comparison per model.

A run builds the corrector, the anonymizer and the statistics of its batch once and proofreads
every file with them.  A comparison builds the same three for each model it tries, one after
another, so that what it measures is one model on one server.

Building contacts no server: the model is asked when the unit is called.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...domain.stages import Stage
from ...exceptions import StageError, StageErrorKind
from ...messages import proofreading_not_compared
from ..ollama_client import OllamaClient
from ..run_stats import RunStatsCollector, StepSampler
from ..stage_builder import PipelineComponentFactory, StageBuilder
from .proofread_text import ProofreadText

if TYPE_CHECKING:
    import threading

    from ...config import Config
    from ...domain.hardware_profile import HardwareDetection
    from ...infrastructure.onnx.store import ModelStore
    from ...infrastructure.speed_store import SampleStore
    from ...interfaces import Reporter

__all__ = ["proofread_unit_for"]


def proofread_unit_for(
    config: Config,
    *,
    factory: PipelineComponentFactory,
    model_store: ModelStore,
    reporter: Reporter,
    samples: SampleStore,
    hardware: HardwareDetection | None = None,
    ollama_client: OllamaClient | None = None,
    stop_event: threading.Event | None = None,
) -> ProofreadText:
    """Return the unit that proofreads one text with *config*.

    Args:
        config: The settings of this proofreading; its provider and model are the ones asked.
        factory: Builds the corrector and the anonymizer (with the caches of the app).
        model_store: Where the reader's model files are, which the builder needs to exist.
        reporter: The sink of the unit's status lines and warnings.
        samples: Where the sample of the answer's speed goes, as a run's does.
        hardware: The computer that runs the models, for the context window; ``None`` keeps the
            base window.
        ollama_client: The client a caller already has (a run shares one); ``None`` builds one
            that reports its statistics to *samples*.
        stop_event: Set to abandon the request that is waiting, as a run's *Stop now* does.
    """
    builder = StageBuilder(
        config=config,
        factory=factory,
        model_store=model_store,
        reporter=reporter,
        hardware=hardware,
    )
    collector = RunStatsCollector(config.ollama_url, samples.add)
    # What the server reports about a request is turned into a sample of this machine's speed —
    # the same way a run does it, so the AI models page learns every model the teacher tried.
    client = ollama_client or OllamaClient(
        config.ollama_url, cancel_event=stop_event, stats_sink=collector.record
    )
    corrector = builder.build_corrector(client)
    if corrector is None:
        # Proofreading is switched off, so there is no unit to build and nothing to compare.
        raise StageError(
            proofreading_not_compared(), kind=StageErrorKind.PROVIDER, stage=Stage.CORRECTION
        )
    anonymizer = builder.build_anonymizer() if config.anonymization_enabled else None
    return ProofreadText(
        corrector,
        anonymizer,
        config.language,
        reporter=reporter,
        sampler=StepSampler(collector),
    )
