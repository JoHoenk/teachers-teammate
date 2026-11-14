"""Unit tests for where each step runs: domain rules and the application classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.application.data_flow import (
    describe_data_flow,
    llm_location,
    llm_steps,
    ocr_location,
    text_goes_online,
)
from teachers_teammate.domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    Advice,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    PrivacyLevel,
    StageLocation,
    anonymization_badge,
    classify_ollama,
    describe_fingerprint,
    is_ollama_cloud_host,
    is_ollama_cloud_model,
    online_ocr_reason,
)
from tests.conftest import make_config

_PROVIDERS = {
    "ollama": {"display_name": "Ollama", "self_hosted": True},
    "openai": {"display_name": "OpenAI (ChatGPT)", "data_region": "non_eu"},
    "mistral_eu": {"display_name": "Mistral (EU)", "data_region": "eu", "vendor_country": "France"},
    "novendor": {"display_name": "Novendor"},
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
    assert loc.label == "Online service (Ollama cloud) · outside the EU"


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
        ("ppocr", "", "localhost"),
        ("ollama", "deepseek-ocr:latest", "localhost"),
        ("ollama", "deepseek-ocr:latest", "school-server"),
        ("ppocr", "x-cloud:cloud", "localhost"),  # model is irrelevant for the reader
    ],
)
def test_local_ocr_is_allowed(engine: str, model: str, host: str) -> None:
    """
    Given  a local engine (Ollama with a downloaded model, the built-in reader)
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
_CLOUD = StageLocation(DataLocation.ONLINE, "OpenAI", region=DataRegion.EU)
_OUTSIDE = StageLocation(DataLocation.ONLINE, "OpenAI", region=DataRegion.NON_EU)


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
    assert flow.fingerprint == "v2|correction:online:eu:OpenAI::1"


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
        "Content review: Online service (OpenAI) · EU — receives text, names sent as written"
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
    Then   the built-in reader is on this computer and the unsupported one is online
    """
    reader = ocr_location("ppocr", "", "http://127.0.0.1:11434")
    assert reader.location is DataLocation.THIS_COMPUTER
    assert reader.service == "Built-in reader"
    assert ocr_location("langchain", "", "http://127.0.0.1:11434").location is DataLocation.ONLINE


def test_llm_location_unknown_provider_is_online() -> None:
    """
    Given  a provider that does not declare self_hosted in its PROVIDER_INFO
    When   llm_location() classifies it
    Then   it is an online service named after the provider (the safe default)
    """
    loc = llm_location("newvendor", "m", "http://127.0.0.1:11434", lambda _p: {})
    assert loc == StageLocation(DataLocation.ONLINE, "Newvendor", region=DataRegion.UNKNOWN)


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


def test_llm_location_online_provider_keeps_its_own_name_whatever_the_ollama_settings_say() -> None:
    """
    Given  an online provider, while the Ollama settings point at ollama.com or at a cloud tag
    When   llm_location() classifies it
    Then   it is named after the provider, not after "Ollama cloud", with the provider's region
    """

    def info(_provider: str) -> dict[str, str]:
        return {"display_name": "OpenAI", "data_region": "non_eu", "vendor_country": "USA"}

    by_host = llm_location("openai", "gpt-5", "https://ollama.com", info)
    by_tag = llm_location("openai", "gpt-oss:120b-cloud", "http://127.0.0.1:11434", info)

    for loc in (by_host, by_tag):
        assert loc.location is DataLocation.ONLINE
        assert loc.service == "OpenAI"
        assert loc.data_region is DataRegion.NON_EU
        assert loc.vendor_country == "USA"


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
    assert [s.stage for s in flow.stages] == [
        STAGE_OCR,
        STAGE_ANONYMIZATION,
        STAGE_CORRECTION,
    ]
    assert flow.leaves_computer is False


def test_describe_data_flow_online_proofreading_and_review(tmp_path: Path) -> None:
    """
    Given  local text recognition, online proofreading and online content review
    When   describe_data_flow() runs with and without name hiding
    Then   both text steps are outbound, and name hiding is reported for both
    """
    base = {
        "ocr_engine": "ppocr",
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


# ── Data region ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("region", "label"),
    [
        (DataRegion.EU, "EU"),
        (DataRegion.NON_EU, "outside the EU"),
        (DataRegion.UNKNOWN, "outside the EU"),
    ],
)
def test_data_region_label(region: DataRegion, label: str) -> None:
    """
    Given  a data region
    When   its label is read
    Then   only the European Union reads "EU"; everything else, including an undeclared
           region, reads "outside the EU"
    """
    assert region.label == label


def test_stage_location_defaults_to_an_undeclared_region() -> None:
    """
    Given  an online step whose provider declares no region
    When   the region and label are read
    Then   it counts as unknown and is shown like a service outside the EU
    """
    location = StageLocation(DataLocation.ONLINE, "Mistral")
    assert location.data_region is DataRegion.UNKNOWN
    assert location.label == "Online service (Mistral) · outside the EU"


def test_stage_location_details_name_the_vendor_country() -> None:
    """
    Given  an online step of a vendor based in France
    When   the tooltip details are built
    Then   they say where the data is processed and where the vendor is at home
    """
    location = StageLocation(
        DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU, vendor_country="France"
    )
    assert location.details == "Online service (Mistral (EU)) · EU\nVendor based in France"


def test_llm_location_reads_the_region_from_provider_info() -> None:
    """
    Given  a provider that declares its data region and vendor country
    When   llm_location() classifies it
    Then   the region and the vendor country end up in the location
    """
    european = llm_location("mistral_eu", "m", "", _info)
    assert european.region is DataRegion.EU
    assert european.data_region is DataRegion.EU
    assert european.vendor_country == "France"

    outside = llm_location("openai", "m", "", _info)
    assert outside.region is DataRegion.NON_EU


def test_llm_location_prefers_a_region_that_depends_on_settings() -> None:
    """
    Given  a provider whose region depends on its settings and a provider_region callable
    When   llm_location() classifies it
    Then   the callable's answer wins over PROVIDER_INFO
    """
    location = llm_location(
        "openai", "m", "", _info, provider_region=lambda _p: DataRegion.EU.value
    )
    assert location.region is DataRegion.EU


def test_llm_location_treats_an_unparsable_region_as_unknown() -> None:
    """
    Given  a provider_region callable that returns something unexpected
    When   llm_location() classifies it
    Then   the region is unknown rather than a crash or a claim of privacy
    """
    location = llm_location("openai", "m", "", _info, provider_region=lambda _p: "mars")
    assert location.region is DataRegion.UNKNOWN


def test_ollama_cloud_keeps_its_name_and_gets_the_declared_region() -> None:
    """
    Given  an Ollama cloud model via a local server, with a vendor region declared
    When   llm_location() classifies it
    Then   the label keeps saying "Ollama cloud" and the region is filled in
    """
    providers = {
        "ollama": {
            "display_name": "Ollama",
            "self_hosted": True,
            "data_region": "non_eu",
            "vendor_country": "USA",
        }
    }
    location = llm_location(
        "ollama", "gpt-oss:120b-cloud", "http://localhost:11434", lambda p: providers.get(p, {})
    )
    assert location.location is DataLocation.ONLINE
    assert location.service == "Ollama cloud"
    assert location.region is DataRegion.NON_EU
    assert location.vendor_country == "USA"


# ── PrivacyLevel ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("location", "hidden", "level"),
    [
        (StageLocation(DataLocation.THIS_COMPUTER, "Ollama"), False, PrivacyLevel.LOCAL),
        (StageLocation(DataLocation.THIS_COMPUTER, "Ollama"), True, PrivacyLevel.LOCAL),
        (
            StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", "srv"),
            False,
            PrivacyLevel.OWN_SERVER,
        ),
        (
            StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", "srv"),
            True,
            PrivacyLevel.OWN_SERVER,
        ),
        (_CLOUD, True, PrivacyLevel.ANON_EU),
        (_CLOUD, False, PrivacyLevel.NAMES_EU),
        (_OUTSIDE, True, PrivacyLevel.ANON_NON_EU),
        (_OUTSIDE, False, PrivacyLevel.NAMES_NON_EU),
        (
            StageLocation(DataLocation.ONLINE, "Mistral"),
            True,
            PrivacyLevel.ANON_NON_EU,
        ),
        (
            StageLocation(DataLocation.ONLINE, "Mistral"),
            False,
            PrivacyLevel.NAMES_NON_EU,
        ),
    ],
)
def test_privacy_level_of_one_step(
    location: StageLocation, hidden: bool, level: PrivacyLevel
) -> None:
    """
    Given  a step's place and whether its names are hidden
    When   its privacy level is read
    Then   the level matches the place, and an undeclared region counts as outside the EU
    """
    assert DataFlowStage(STAGE_CORRECTION, location, "text", hidden).privacy_level is level


def test_privacy_level_severity_orders_the_levels() -> None:
    """
    Given  the privacy levels
    When   they are sorted by severity
    Then   nothing leaving the computer is mildest and names leaving it is worst
    """
    assert sorted(PrivacyLevel, key=lambda level: level.severity) == [
        PrivacyLevel.LOCAL,
        PrivacyLevel.OWN_SERVER,
        PrivacyLevel.ANON_EU,
        PrivacyLevel.ANON_NON_EU,
        PrivacyLevel.NAMES_EU,
        PrivacyLevel.NAMES_NON_EU,
    ]


@pytest.mark.parametrize(
    ("level", "leaves", "names"),
    [
        (PrivacyLevel.LOCAL, False, False),
        (PrivacyLevel.OWN_SERVER, True, False),
        (PrivacyLevel.ANON_EU, True, False),
        (PrivacyLevel.ANON_NON_EU, True, False),
        (PrivacyLevel.NAMES_EU, True, True),
        (PrivacyLevel.NAMES_NON_EU, True, True),
    ],
)
def test_privacy_level_helpers(level: PrivacyLevel, leaves: bool, names: bool) -> None:
    """
    Given  a privacy level
    When   its helpers are read
    Then   only LOCAL stays on this computer and only the NAMES levels send names
    """
    assert level.leaves_computer is leaves
    assert level.sends_names is names


def test_data_flow_level_is_the_worst_step() -> None:
    """
    Given  local text recognition, anonymized proofreading into the EU and content review
           outside the EU
    When   the overall level is read
    Then   the least private step decides
    """
    flow = DataFlow(
        (
            DataFlowStage(STAGE_OCR, _HERE, "page images"),
            DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", True),
            DataFlowStage(STAGE_EVALUATION, _OUTSIDE, "text", True),
        )
    )
    assert flow.level is PrivacyLevel.ANON_NON_EU


def test_data_flow_level_is_local_without_steps() -> None:
    """
    Given  a data flow with no steps at all
    When   the overall level is read
    Then   it is LOCAL rather than an error
    """
    assert DataFlow(()).level is PrivacyLevel.LOCAL


# ── Advice about the region ────────────────────────────────────────────────


def test_data_flow_advises_an_eu_service_outside_the_eu() -> None:
    """
    Given  an online step outside the European Union
    When   the advice is read
    Then   it suggests a service in the EU instead
    """
    flow = DataFlow((DataFlowStage(STAGE_CORRECTION, _OUTSIDE, "text", True),))
    assert flow.advice == (Advice.NAMES_MAY_BE_MISSED, Advice.SERVICE_OUTSIDE_EU)


def test_data_flow_gives_no_region_advice_inside_the_eu() -> None:
    """
    Given  an online step in the European Union
    When   the advice is read
    Then   no service-outside-the-EU advice is given
    """
    flow = DataFlow((DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", True),))
    assert Advice.SERVICE_OUTSIDE_EU not in flow.advice


# ── Anonymization row and badge ────────────────────────────────────────────


def test_anonymization_badge_names_the_models() -> None:
    """
    Given  the spaCy models a run loads
    When   the personal-info-removal badge is built
    Then   each model is shortened to its language code, and no model reads "On"
    """
    assert anonymization_badge(["en_core_web_sm", "de_core_news_sm"]) == "On (spaCy en + de)"
    assert anonymization_badge(["en_core_web_sm"]) == "On (spaCy en)"
    assert anonymization_badge([]) == "On"


def test_describe_data_flow_lists_anonymization_as_a_local_row(tmp_path: Path) -> None:
    """
    Given  text recognition only, with and without personal-info removal
    When   describe_data_flow() runs
    Then   the row is always listed, stays on this computer, and says whether it is on
    """
    off = describe_data_flow(make_config(tmp_path), _info)
    row = off.stage(STAGE_ANONYMIZATION)
    assert row is not None
    assert row.location.location is DataLocation.THIS_COMPUTER
    assert row.badge == "Off"
    assert row.privacy_level is PrivacyLevel.LOCAL

    on = describe_data_flow(
        make_config(tmp_path, anonymization_enabled=True),
        _info,
        anonymizer_models=["en_core_web_sm", "de_core_news_sm"],
    )
    assert on.stage(STAGE_ANONYMIZATION).badge == "On (spaCy en + de)"


def test_describe_data_flow_hides_names_without_proofreading(tmp_path: Path) -> None:
    """
    Given  personal-info removal ticked and proofreading off
    When   describe_data_flow() runs
    Then   names count as hidden (removal no longer depends on proofreading)
    """
    cfg = make_config(tmp_path, anonymization_enabled=True, correction_enabled=False)
    flow = describe_data_flow(cfg, _info)
    assert flow.stage(STAGE_ANONYMIZATION).personal_info_hidden is True
    assert flow.leaves_computer is False


def test_describe_data_flow_uses_the_region_callback(tmp_path: Path) -> None:
    """
    Given  a provider_region callable that reports an EU region
    When   describe_data_flow() classifies online proofreading
    Then   the region comes from the callable, not from PROVIDER_INFO
    """
    cfg = make_config(
        tmp_path, correction_enabled=True, correction_provider="openai", correction_model="m"
    )
    flow = describe_data_flow(cfg, _info, provider_region=lambda _p: DataRegion.EU.value)
    assert flow.stage(STAGE_CORRECTION).location.region is DataRegion.EU


# ── Fingerprint formats ────────────────────────────────────────────────────


def test_fingerprint_includes_the_region() -> None:
    """
    Given  the same online step in and outside the EU
    When   the fingerprints are compared
    Then   they differ, so an answer for an EU service does not carry over
    """
    european = DataFlow((DataFlowStage(STAGE_CORRECTION, _CLOUD, "text", True),))
    outside = DataFlow((DataFlowStage(STAGE_CORRECTION, _OUTSIDE, "text", True),))
    assert european.fingerprint != outside.fingerprint


def test_describe_fingerprint_reads_a_v2_fingerprint() -> None:
    """
    Given  a stored v2 fingerprint with a region
    When   it is turned into a readable line
    Then   the line names the place, including the region
    """
    line = describe_fingerprint("v2|correction:online:eu:Mistral (EU)::1")
    assert line == (
        "Proofreading: Online service (Mistral (EU)) · EU — names replaced where detected"
    )


def test_describe_fingerprint_still_reads_a_v1_fingerprint() -> None:
    """
    Given  an answer stored before the region existed
    When   it is turned into a readable line
    Then   it is understood, without a region in the line
    """
    line = describe_fingerprint("correction:online:OpenAI::0")
    assert line == "Proofreading: Online service (OpenAI) · outside the EU — names sent as written"


def test_describe_fingerprint_keeps_a_part_it_cannot_parse() -> None:
    """
    Given  a stored part that does not parse (unknown region or place)
    When   it is turned into a readable line
    Then   the part is shown as it is instead of being dropped
    """
    assert describe_fingerprint("v2|correction:online:mars:OpenAI::1") == (
        "correction:online:mars:OpenAI::1"
    )
    assert describe_fingerprint("garbage") == "garbage"


def test_data_flow_gives_no_region_advice_for_an_own_server() -> None:
    """
    Given  a text step that runs on another computer the user controls
    When   the advice is read
    Then   it does not tell the user to leave for an EU service: only a vendor has a region
    """
    own_server = StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", host="192.168.1.9")
    flow = DataFlow((DataFlowStage(STAGE_CORRECTION, own_server, "text", False),))

    assert Advice.SERVICE_OUTSIDE_EU not in flow.advice


def test_llm_steps_lists_the_review_only_after_proofreading() -> None:
    """
    Given  every combination of proofreading and content review being on
    When   llm_steps() is asked
    Then   the review is listed only together with proofreading
    """
    assert llm_steps(False, False) == ()
    assert llm_steps(False, True) == ()
    assert llm_steps(True, False) == (STAGE_CORRECTION,)
    assert llm_steps(True, True) == (STAGE_CORRECTION, STAGE_EVALUATION)


def _locate(provider: str, model: str, url: str) -> StageLocation:
    return llm_location(provider, model, url, _info)


def _default_model(provider: str, model: str, task: str) -> str:
    return model.strip() or f"{provider}-{task}"


@pytest.mark.parametrize(
    ("settings", "online"),
    [
        ({"correction_provider": "ollama"}, False),
        ({"correction_provider": "openai"}, True),
        ({"correction_provider": "openai", "correction_enabled": False}, False),
        ({"correction_provider": "ollama", "evaluate_provider": "openai"}, False),
        (
            {
                "correction_provider": "ollama",
                "evaluate_provider": "openai",
                "evaluation_enabled": True,
            },
            True,
        ),
        (
            {
                "correction_provider": "openai",
                "evaluate_provider": "ollama",
                "correction_enabled": False,
                "evaluation_enabled": True,
            },
            False,
        ),
        ({"correction_provider": "ollama", "correction_model": "gpt-oss:120b-cloud"}, True),
    ],
    ids=[
        "local",
        "online proofreading",
        "online proofreading switched off",
        "online review switched off",
        "online review",
        "review without proofreading",
        "cloud model on the local server",
    ],
)
def test_text_goes_online_follows_the_steps_that_run(settings: dict, online: bool) -> None:
    """
    Given  settings with local or online services and the steps switched on or off
    When   text_goes_online() is asked
    Then   only a step that runs, with the model it uses, can send text online
    """
    assert text_goes_online(settings, _locate, _default_model) is online


def test_text_goes_online_uses_the_same_steps_as_the_data_flow(tmp_path: Path) -> None:
    """
    Given  a configuration with online review and proofreading switched off
    When   the data-flow box and text_goes_online() are asked about it
    Then   they agree
    """
    values = {
        "correction_enabled": False,
        "evaluation_enabled": True,
        "evaluate_provider": "openai",
    }
    flow = describe_data_flow(make_config(tmp_path, **values), _info)

    assert not flow.leaves_computer
    assert not text_goes_online(values, _locate, _default_model)
