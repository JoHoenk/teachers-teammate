"""The unit that proofreads one text: what a run per file, a comparison per model builds.

Building contacts no server, so what a caller can check here is what the unit is built with: the
client it will ask, and the event that abandons a request that is waiting.
"""

from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

from teachers_teammate.infrastructure.speed_store import SampleStore
from teachers_teammate.infrastructure.stage_builder import PipelineComponentFactory
from teachers_teammate.infrastructure.workflow.proofread_unit import proofread_unit_for
from tests.conftest import make_config
from tests.fakes.fake_client_factory import RecordingClientFactory


def _config(tmp_path: Path, **overrides: object):
    """Return a comparison's settings: one model, on Ollama, without name removal."""
    values: dict[str, object] = {
        "correction_enabled": True,
        "correction_provider": "ollama",
        "correction_model": "qwen3:8b",
        "anonymization_enabled": False,
    }
    values.update(overrides)
    return make_config(tmp_path, **values)


@pytest.mark.unit
def test_the_units_client_comes_from_the_factory_with_its_stop_event(tmp_path: Path) -> None:
    """
    Given  a comparison's settings, the app's client factory and a stop event
    When   the unit that proofreads one text is built
    Then   its client came from that factory with the event and with the sink of the answer's
           sample, as a run's does, so "Stop now" abandons its request too
    """
    stop = threading.Event()
    factory = RecordingClientFactory()
    config = _config(tmp_path)

    proofread_unit_for(
        config,
        factory=PipelineComponentFactory(),
        model_store=MagicMock(),
        reporter=MagicMock(),
        samples=SampleStore(tmp_path / "samples.json"),
        client_factory=factory,
        stop_event=stop,
    )

    build = factory.only_build()
    assert build.url == config.ollama_url
    assert build.cancel_event is stop
    assert build.stats_sink is not None
