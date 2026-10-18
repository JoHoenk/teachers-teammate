"""Unit tests for where each step runs: domain rules and the application classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.application.data_flow import (
    describe_data_flow,
    llm_location,
    ocr_location,
)
from teachers_teammate.domain.data_location import (
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    Advice,
    DataFlow,
    DataFlowStage,
    DataLocation,
    StageLocation,
    classify_ollama,
    is_ollama_cloud_host,
    is_ollama_cloud_model,
    online_ocr_reason,
)
from tests.conftest import make_config

_PROVIDERS = {
    "ollama": {"display_name": "Ollama", "self_hosted": True},
    "openai": {"display_name": "OpenAI (ChatGPT)"},
}


def _info(provider: str) -> dict:
    return _PROVIDERS.get(provider, {})


# ── Ollama cloud detection ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model",
    ["gpt-oss:120b-cloud", "glm-4.6:cloud", "Qwen3-VL:235B-Cloud", " kimi-k2:1t-cloud "],
)
def test_ollama_cloud_models_are_recognised(model: str) -> None:
    """
    Given  a model tag that is served by Ollama's servers
    When   is_ollama_cloud_model() is asked
    Then   it is True
    """
    assert is_ollama_cloud_model(model) is True


@pytest.mark.parametrize(
    "model",
    ["qwen3-vl:8b", "deepseek-ocr:latest", "cloud:latest", "mycloud", "", "hf.co/x/y:UD-Q4_K_XL"],
)
def test_downloaded_models_are_not_cloud_models(model: str) -> None:
    """
    Given  a model that is downloaded to the user's own Ollama
    When   is_ollama_cloud_model() is asked
    Then   it is False (a name merely containing "cloud" does not count)
    """
    assert is_ollama_cloud_model(model) is False


@pytest.mark.parametrize(
    ("host", "expected"),
    [("ollama.com", True), ("api.OLLAMA.com", True), ("localhost", False), ("myollama.com", False)],
)
def test_ollama_cloud_host(host: str, expected: bool) -> None:
    """
    Given  a host name
    When   is_ollama_cloud_host() is asked
    Then   only Ollama's own domain counts as the hosted API
    """
    assert is_ollama_cloud_host(host) is expected


# ── classify_ollama ────────────────────────────────────────────────────────


def test_classify_ollama_on_this_computer() -> None:
    """
    Given  a downloaded model on a server at this computer
    When   classify_ollama() runs
    Then   the step is on this computer
    """
    loc = classify_ollama("qwen3.5:9b", url_is_local=True, host="localhost")
    assert loc.location is DataLocation.THIS_COMPUTER
    assert loc.label == "On this computer"


def test_classify_ollama_on_another_computer_names_the_host() -> None:
    """
    Given  a server that is not this computer
    When   classify_ollama() runs
    Then   the step is on another computer and the label names it
    """
    loc = classify_ollama("qwen3.5:9b", url_is_local=False, host="school-server")
    assert loc.location is DataLocation.OTHER_COMPUTER
    assert loc.label == "On another computer (school-server)"


@pytest.mark.parametrize(
    ("model", "url_is_local", "host"),
    [("gpt-oss:120b-cloud", True, "localhost"), ("qwen3.5:9b", False, "ollama.com")],
)
def test_classify_ollama_cloud_is_online_even_via_local_server(
    model: str, url_is_local: bool, host: str
) -> None:
    """
    Given  an Ollama cloud model (through the local server) or Ollama's hosted API
    When   classify_ollama() runs
    Then   the step is an online service
    """
    loc = classify_ollama(model, url_is_local=url_is_local, host=host)
    assert loc.location is DataLocation.ONLINE
    assert loc.label == "Online service (Ollama cloud)"


# ── online_ocr_reason ──────────────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["langchain", "openai", "anthropic", ""])
def test_online_ocr_engines_are_refused(engine: str) -> None:
    """
    Given  an OCR engine that is not one of the local engines
    When   online_ocr_reason() is asked
    Then   a reason mentioning the whole page is returned
    """
    assert "whole page" in online_ocr_reason(engine, "gpt-4o", "localhost")


@pytest.mark.parametrize(
    ("engine", "model", "host"),
    [
        ("tesseract", "", "localhost"),
        ("paddleocr", "", "localhost"),
        ("ollama", "deepseek-ocr:latest", "localhost"),
        ("ollama", "deepseek-ocr:latest", "school-server"),
        ("tesseract", "x-cloud:cloud", "localhost"),  # model is irrelevant for tesseract
    ],
)
def test_local_ocr_is_allowed(engine: str, model: str, host: str) -> None:
    """
    Given  a local engine (Ollama with a downloaded model, Tesseract, PaddleOCR)
    When   online_ocr_reason() is asked
    Then   there is no reason to refuse
    """
    assert online_ocr_reason(engine, model, host) == ""


@pytest.mark.parametrize(
    ("model", "host"), [("qwen3-vl:235b-cloud", "localhost"), ("deepseek-ocr:latest", "ollama.com")]
)
def test_ollama_cloud_ocr_is_refused(model: str, host: str) -> None:
    """
    Given  Ollama OCR with a cloud model or Ollama's hosted API
    When   online_ocr_reason() is asked
    Then   the reason says it runs on Ollama's servers
    """
    assert "Ollama's servers" in online_ocr_reason("ollama", model, host)


# ── DataFlow ───────────────────────────────────────────────────────────────

_HERE = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
_CLOUD = StageLocation(DataLocation.ONLINE, "OpenAI")


def test_data_flow_reports_only_steps_that_leave() -> None:
    """
    Given  local text recognition and online proofreading with names hidden
    When   the flow is inspected
    Then   only proofreading is outbound and the fingerprint names exactly that
    """
    flow = DataFlow(
        (
            DataFlowStage(STAGE_OCR, _HERE, "page images"),
            DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", personal_info_hidden=True),
        )
    )
    assert flow.leaves_computer is True
    assert [s.stage for s in flow.outbound] == [STAGE_CORRECTION]
    assert flow.fingerprint == "correction:online:OpenAI::1"


def test_data_flow_fingerprint_changes_when_hiding_names_changes() -> None:
    """
    Given  the same online step with and without hidden names
    When   the fingerprints are compared
    Then   they differ, so "don't ask again" never carries over to weaker privacy
    """
    hidden = DataFlow((DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", True),))
    exposed = DataFlow((DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", False),))
    assert hidden.fingerprint != exposed.fingerprint


def test_data_flow_local_only_has_no_outbound_and_no_advice() -> None:
    """
    Given  every step on this computer
    When   the flow is inspected
    Then   nothing leaves, the fingerprint is empty and there is no advice
    """
    flow = DataFlow((DataFlowStage(STAGE_OCR, _HERE, "page images"),))
    assert flow.leaves_computer is False
    assert flow.fingerprint == ""
    assert flow.advice == ()


def test_data_flow_advises_hiding_names_for_exposed_online_text() -> None:
    """
    Given  online proofreading without hidden names
    When   the advice is read
    Then   it tells the teacher the names are sent as written
    """
    flow = DataFlow((DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", False),))
    assert flow.advice == (Advice.NAMES_SENT_AS_WRITTEN,)


def test_data_flow_warns_that_name_replacement_can_miss_a_name() -> None:
    """
    Given  online proofreading with names replaced automatically
    When   the advice is read
    Then   it says detection is not perfect instead of promising the names are hidden
    """
    flow = DataFlow((DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", True),))
    assert flow.advice == (Advice.NAMES_MAY_BE_MISSED,)


def test_data_flow_advice_is_empty_when_everything_stays_local() -> None:
    """
    Given  every step on this computer
    When   the advice is read
    Then   there is none, however names are handled
    """
    flow = DataFlow((DataFlowStage(STAGE_CORRECTION, _HERE, "text", True),))
    assert flow.advice == ()


def test_data_flow_advice_is_wording_free() -> None:
    """
    Given  advice codes from the domain
    When   they are compared with the GUI's names for controls
    Then   the domain carries no GUI wording ("Step 3", "Remove personal info")
    """
    for advice in Advice:
        assert "Step" not in advice.value
        assert "Remove" not in advice.value


def test_stage_summary_for_leaving_step_says_what_is_received() -> None:
    """
    Given  an online step that receives text with names visible
    When   its summary is built
    Then   the line says where it runs and that names are sent as written
    """
    stage = DataFlowStage(STAGE_EVALUATION, _CLOUD, "text", False)
    assert stage.summary == (
        "Content review: Online service (OpenAI) — receives text, names sent as written"
    )


def test_stage_summary_does_not_promise_names_are_hidden() -> None:
    """
    Given  an online step whose names are replaced automatically
    When   its summary is built
    Then   it says "where detected": detection can miss a name
    """
    stage = DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", True)
    assert stage.summary.endswith("names replaced where detected")


# ── application classification ─────────────────────────────────────────────


def test_ocr_location_for_each_engine() -> None:
    """
    Given  the supported engines and an unsupported one
    When   ocr_location() classifies them
    Then   Tesseract and PaddleOCR are on this computer and the unsupported one is online
    """
    assert ocr_location("tesseract", "", "http://127.0.0.1:11434").location is (
        DataLocation.THIS_COMPUTER
    )
    assert ocr_location("paddleocr", "", "http://127.0.0.1:11434").service == "PaddleOCR"
    assert ocr_location("langchain", "", "http://127.0.0.1:11434").location is DataLocation.ONLINE


def test_llm_location_unknown_provider_is_online() -> None:
    """
    Given  a provider that does not declare self_hosted in its PROVIDER_INFO
    When   llm_location() classifies it
    Then   it is an online service named after the provider (the safe default)
    """
    loc = llm_location("newvendor", "m", "http://127.0.0.1:11434", lambda _p: {})
    assert loc == StageLocation(DataLocation.ONLINE, "Newvendor")


def test_llm_location_self_hosted_provider_follows_the_server() -> None:
    """
    Given  a self-hosted provider with a local and a remote server URL
    When   llm_location() classifies both
    Then   the local one is on this computer and the remote one names its host
    """
    here = llm_location("ollama", "qwen3.5:9b", "http://127.0.0.1:11434", _info)
    there = llm_location("ollama", "qwen3.5:9b", "http://school-server:11434", _info)
    assert here.location is DataLocation.THIS_COMPUTER
    assert there.location is DataLocation.OTHER_COMPUTER
    assert there.host == "school-server"


def test_describe_data_flow_default_settings_stay_local(tmp_path: Path) -> None:
    """
    Given  Ollama for text recognition and proofreading on this computer
    When   describe_data_flow() runs
    Then   nothing leaves the computer
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="deepseek-ocr:latest",
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3.5:9b",
    )
    flow = describe_data_flow(cfg, _info)
    assert [s.stage for s in flow.stages] == [STAGE_OCR, STAGE_CORRECTION]
    assert flow.leaves_computer is False


def test_describe_data_flow_online_proofreading_and_review(tmp_path: Path) -> None:
    """
    Given  local text recognition, online proofreading and online content review
    When   describe_data_flow() runs with and without name hiding
    Then   both text steps are outbound, and name hiding is reported for both
    """
    base = {
        "ocr_engine": "tesseract",
        "correction_enabled": True,
        "correction_provider": "openai",
        "evaluation_enabled": True,
        "evaluate_provider": "openai",
    }
    exposed = describe_data_flow(make_config(tmp_path, **base), _info)
    hidden = describe_data_flow(make_config(tmp_path, anonymization_enabled=True, **base), _info)

    assert [s.stage for s in exposed.outbound] == [STAGE_CORRECTION, STAGE_EVALUATION]
    assert not any(s.personal_info_hidden for s in exposed.outbound)
    assert all(s.personal_info_hidden for s in hidden.outbound)
    assert exposed.stage(STAGE_CORRECTION).location.service == "OpenAI (ChatGPT)"


def test_describe_data_flow_review_needs_proofreading(tmp_path: Path) -> None:
    """
    Given  content review enabled while proofreading is off
    When   describe_data_flow() runs
    Then   the review is not listed (the pipeline skips it too)
    """
    cfg = make_config(
        tmp_path, correction_enabled=False, evaluation_enabled=True, evaluate_provider="openai"
    )
    assert describe_data_flow(cfg, _info).stage(STAGE_EVALUATION) is None


def test_describe_data_flow_remote_ollama_for_ocr(tmp_path: Path) -> None:
    """
    Given  Ollama text recognition on another computer
    When   describe_data_flow() runs
    Then   page images are reported as leaving, with the host named and the advice shown
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="deepseek-ocr:latest",
        ollama_url="http://school-server:11434",
    )
    flow = describe_data_flow(cfg, _info)
    ocr = flow.stage(STAGE_OCR)
    assert ocr.location.location is DataLocation.OTHER_COMPUTER
    assert ocr.receives == "page images"
    assert Advice.PAGES_TO_OTHER_COMPUTER in flow.advice
