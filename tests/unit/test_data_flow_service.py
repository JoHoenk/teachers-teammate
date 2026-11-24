"""Unit tests for where each step of a run takes place."""

from __future__ import annotations

from pathlib import Path
import pytest
from teachers_teammate.domain.stages import Stage
from tests.conftest import make_config
from ._service_helpers import _make_svc, _ollama_config, reader_models_in_place


@pytest.fixture(autouse=True)
def _reader_models_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every service of this file a store that holds the built-in reader's models."""
    reader_models_in_place(tmp_path, monkeypatch)


@pytest.mark.unit
def test_describe_data_flow_uses_provider_metadata(tmp_path: Path) -> None:
    """
    Given  local text recognition and OpenAI proofreading
    When   the service describes the data flow
    Then   proofreading is an online service named by its provider's display name
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=True,
        correction_provider="openai",
    )

    flow = _make_svc().data_flow.describe_data_flow(cfg)

    assert [s.stage for s in flow.outbound] == ["correction"]
    assert flow.outbound[0].location.service == "OpenAI (ChatGPT)"


@pytest.mark.unit
def test_describe_data_flow_reports_the_provider_region(tmp_path: Path) -> None:
    """
    Given  proofreading at an EU provider and content review at a provider outside the EU
    When   the service describes the data flow
    Then   each step carries the region the provider declares, and the worst step decides
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=True,
        correction_provider="mistral_eu",
        evaluation_enabled=True,
        evaluate_provider="openai",
    )

    flow = _make_svc().data_flow.describe_data_flow(cfg)

    assert flow.stage("correction").location.data_region.value == "eu"
    assert flow.stage("evaluation").location.data_region.value == "non_eu"
    assert flow.level.value == "names_non_eu"


@pytest.mark.unit
def test_describe_steps_lists_every_step_although_none_is_ticked(tmp_path: Path) -> None:
    """
    Given  a config with proofreading and review both off
    When   the service describes the steps
    Then   proofreading and the review are listed with the place they would run at
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=False,
        evaluation_enabled=False,
        correction_provider="mistral_eu",
        evaluate_provider="openai",
    )
    svc = _make_svc()

    assert svc.data_flow.describe_data_flow(cfg).stage("correction") is None
    steps = svc.data_flow.describe_steps(cfg)

    assert steps.stage("correction").location.data_region.value == "eu"
    assert steps.stage("evaluation").location.data_region.value == "non_eu"
    assert steps.stage("ocr") is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("correction", "evaluation"), [(False, False), (False, True), (True, False), (True, True)]
)
def test_ticked_steps_of_all_steps_equal_the_flow_of_the_ticked_config(
    tmp_path: Path, correction: bool, evaluation: bool
) -> None:
    """
    Given  a config with proofreading and review ticked or not (a review needs proofreading)
    When   the all-steps flow is narrowed to the ticked steps
    Then   it equals the flow the service describes for that very config
    """
    from teachers_teammate.application.data_flow import ticked_steps  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        anonymization_enabled=True,
        correction_enabled=correction,
        evaluation_enabled=evaluation,
        correction_provider="mistral_eu",
        evaluate_provider="openai",
    )
    svc = _make_svc()

    narrowed = ticked_steps(
        svc.data_flow.describe_steps(cfg),
        correction_enabled=correction,
        evaluation_enabled=evaluation,
    )

    assert narrowed == svc.data_flow.describe_data_flow(cfg)


@pytest.mark.unit
def test_the_data_flow_has_no_recognition_step_while_it_is_off_but_the_steps_view_has(
    tmp_path: Path,
) -> None:
    """
    Given  a config with text recognition switched off
    When   the data flow and the all-steps view are described
    Then   the flow omits text recognition (no page leaves anywhere), the all-steps view
           still lists it so the line under the step can say what ticking it would use
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, recognition_enabled=False, correction_enabled=False)

    assert svc.data_flow.describe_data_flow(cfg).stage(Stage.OCR) is None
    assert svc.data_flow.describe_steps(cfg).stage(Stage.OCR) is not None
