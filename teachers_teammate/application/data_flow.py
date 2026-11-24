"""Where each step of a run takes place — answers "does anything leave my computer?".

Combines the pure rules in :mod:`teachers_teammate.domain.data_location` with the facts
only the outside world can answer (is this Ollama URL this computer? is a provider
hosted by a vendor, and where does that vendor process the data?).  The GUI and CLI
render the result; neither decides anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from ..config import Config
from ..domain.data_location import (
    ENGINE_OLLAMA,
    OCR_ENGINE_NAMES,
    STAGE_ANONYMIZATION,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    SentRecord,
    StageLocation,
    anonymization_badge,
    anonymization_languages,
    classify_ollama,
    llm_stage_location,
    online_ocr_reason,
)
from ..domain.ollama_address import normalize_ollama_url
from ..domain.providers import ProviderInfo, display_name_of
from ..domain.stages import Stage
from ..infrastructure.llm_factory import get_provider_info
from ..infrastructure.ollama_utils import ollama_server
from ..settings_schema import DEFAULTS

#: Answers what a provider is and needs (``None`` for a name that is no provider).
ProviderLookup = Callable[[str], ProviderInfo | None]
#: Answers where a provider processes the data: ``"eu"``, ``"non_eu"`` or ``"unknown"``.
ProviderRegion = Callable[[str], str]


def _ollama_location(model: str, ollama_url: str) -> StageLocation:
    server = ollama_server(ollama_url)
    return classify_ollama(
        model,
        url_is_local=server.is_this_computer,
        host=server.host,
        host_is_private=server.is_private,
    )


def ocr_refusal(engine: str, model: str, ollama_url: str) -> str:
    """Return why text recognition with these settings is refused, or ``""`` when allowed."""
    server = ollama_server(ollama_url)
    return online_ocr_reason(
        engine,
        model,
        server.host,
        url_is_local=server.is_this_computer,
        host_is_private=server.is_private,
    )


def ocr_location(engine: str, model: str, ollama_url: str) -> StageLocation:
    """Classify text recognition with *engine*; only engines the user controls are supported."""
    if engine == ENGINE_OLLAMA:
        return _ollama_location(model, ollama_url)
    if engine in OCR_ENGINE_NAMES:
        return StageLocation(DataLocation.THIS_COMPUTER, OCR_ENGINE_NAMES[engine])
    # Not a supported engine: a run is refused, so assume the worst for display.
    return StageLocation(DataLocation.ONLINE, engine)


def data_region_of(
    provider: str,
    info: ProviderInfo | None,
    provider_region: ProviderRegion | None = None,
) -> DataRegion:
    """Return where *provider* processes the data, defaulting to "not declared".

    *provider_region* answers providers whose region depends on their settings (a custom
    base URL, an EU endpoint); without it the provider's ``PROVIDER.data_region`` is used.
    A value that is not one of the known regions counts as :attr:`DataRegion.UNKNOWN`: a
    school can only rely on what a vendor declares.
    """
    if provider_region is not None:
        value: object = provider_region(provider)
    else:
        value = info.data_region if info is not None else DataRegion.UNKNOWN
    try:
        return DataRegion(str(value or ""))
    except ValueError:
        return DataRegion.UNKNOWN


def llm_location(
    provider: str,
    model: str,
    ollama_url: str,
    provider_info: ProviderLookup,
    provider_region: ProviderRegion | None = None,
) -> StageLocation:
    """Classify a LangChain provider: self-hosted ones follow the server, others are online.

    "Self-hosted" is judged by the Ollama server settings (*ollama_url*, the model's cloud
    tag): the only self-hosted provider today is Ollama.  A second one would need its own
    server setting passed in here.

    A provider that does not declare ``self_hosted`` in its ``PROVIDER`` is treated as a
    vendor-hosted online service — the safe default for a new provider.
    """
    info = provider_info(provider)
    name = display_name_of(provider, info)
    region = data_region_of(provider, info, provider_region)
    country = info.vendor_country if info is not None else ""
    self_hosted = info is not None and info.self_hosted
    server = ollama_server(ollama_url)
    where = llm_stage_location(
        self_hosted=self_hosted,
        model=model,
        url_is_local=server.is_this_computer,
        host=server.host,
        host_is_private=server.is_private,
    )
    if not self_hosted:
        # The Ollama server settings say nothing about a vendor's own service.
        return StageLocation(DataLocation.ONLINE, name, region=region, vendor_country=country)
    location = _ollama_location(model, ollama_url)
    if where is DataLocation.ONLINE:
        # An Ollama cloud model keeps its own, more telling name.
        return replace(location, region=region, vendor_country=country)
    return replace(location, service=name)


def sent_location(record: SentRecord) -> StageLocation:
    """Return where a stored result was made, from the inputs its record kept.

    The record holds the inputs of the classification, not the classification, so a changed
    rule changes the wording and not the stored data.  Its ``endpoint`` is the Ollama host,
    which is all :func:`llm_location` needs to tell this computer from another one.
    """
    return llm_location(record.provider, record.model, _endpoint_url(record), get_provider_info)


def _endpoint_url(record: SentRecord) -> str:
    """Return the record's endpoint as a URL: a bare IPv6 host (``::1``) gets its brackets.

    Without them the host cannot be read back out of the URL, and a run on this computer
    would be shown as one on another computer.
    """
    endpoint = record.endpoint
    if ":" in endpoint and "://" not in endpoint and not endpoint.startswith("["):
        return f"[{endpoint}]"
    return endpoint


def llm_steps(correction_enabled: bool, evaluation_enabled: bool) -> tuple[Stage, ...]:
    """Return the text steps a run takes, in order (``Stage.CORRECTION``, ``Stage.EVALUATION``).

    The content review reads the proofread text, so it only runs after proofreading.  The one
    place that says which steps exist: the data-flow box and the Settings window both ask it.
    """
    if not correction_enabled:
        return ()
    return (Stage.CORRECTION, Stage.EVALUATION) if evaluation_enabled else (Stage.CORRECTION,)


def ticked_steps(
    steps: DataFlow,
    *,
    correction_enabled: bool,
    evaluation_enabled: bool,
    recognition_enabled: bool = True,
) -> DataFlow:
    """Return the part of *steps* (every step as if it were ticked) that a run takes.

    The same stages, in the same order, that :func:`describe_data_flow` lists for a config with
    these ticks — so a caller that already has the all-steps flow does not ask for the places
    and regions a second time.
    """
    text_steps = set(llm_steps(correction_enabled, evaluation_enabled))
    return DataFlow(
        tuple(
            stage
            for stage in steps.stages
            if (stage.stage != Stage.OCR or recognition_enabled)
            and (
                stage.stage not in (Stage.CORRECTION, Stage.EVALUATION) or stage.stage in text_steps
            )
        )
    )


#: Settings-key prefix and default-model task of each text step.
_LLM_STEP_SETTINGS: dict[Stage, tuple[str, Stage]] = {
    Stage.CORRECTION: ("correction", Stage.CORRECTION),
    Stage.EVALUATION: ("evaluate", Stage.EVALUATION),
}


def text_goes_online(
    settings: Mapping[str, object],
    describe: Callable[[str, str, str], StageLocation],
    effective_model: Callable[[str, str, Stage], str],
) -> bool:
    """Return whether a run with *settings* sends text to an online service.

    Only the steps that run count (:func:`llm_steps`), each with the model it really uses
    (*effective_model*: provider, model, task), classified by *describe* (provider, model,
    Ollama URL) -- the classification the data-flow box uses, but from settings, so a page can
    ask before anything is saved.
    """
    steps = llm_steps(
        bool(settings.get("correction_enabled", DEFAULTS["correction_enabled"])),
        bool(settings.get("evaluation_enabled", DEFAULTS["evaluation_enabled"])),
    )
    ollama_url = normalize_ollama_url(str(settings.get("ollama_url", "")))
    for step in steps:
        prefix, task = _LLM_STEP_SETTINGS[step]
        provider = str(settings.get(f"{prefix}_provider") or DEFAULTS[f"{prefix}_provider"])
        model = effective_model(provider, str(settings.get(f"{prefix}_model") or ""), task)
        if describe(provider, model, ollama_url).location is DataLocation.ONLINE:
            return True
    return False


class DataFlowService:
    """Where each step of a run takes place, for the settings this process was built with.

    The rules are the functions of this module; this holds the facts only the process can
    answer (the providers' metadata and the spaCy models name removal would load), so the GUI
    and the CLI ask one object and decide nothing themselves.
    """

    def __init__(
        self,
        *,
        provider_info: ProviderLookup,
        provider_region: ProviderRegion | None = None,
        anonymizer_models: Callable[[Config], Sequence[str]] = lambda _config: (),
    ) -> None:
        self._provider_info = provider_info
        self._provider_region = provider_region
        self._anonymizer_models = anonymizer_models

    def describe_data_flow(self, config: Config) -> DataFlow:
        """Return where each enabled step of *config* runs (this computer, another, online)."""
        return describe_data_flow(
            config,
            self._provider_info,
            self._provider_region,
            self._anonymizer_models(config),
        )

    def describe_steps(self, config: Config) -> DataFlow:
        """Return where every step would run if it were ticked, whatever *config* ticks.

        The main window shows the model and place of each step even while it is unticked, so
        the teacher sees what ticking it would use.  Asking here keeps the GUI from deciding
        a place itself.
        """
        return self.describe_data_flow(
            replace(
                config,
                recognition_enabled=True,
                correction_enabled=True,
                evaluation_enabled=True,
            )
        )


def describe_data_flow(
    config: Config,
    provider_info: ProviderLookup,
    provider_region: ProviderRegion | None = None,
    anonymizer_models: Sequence[str] = (),
) -> DataFlow:
    """Return where every enabled step of *config* runs.

    Text recognition is listed even for text-only inputs: the answer is for the settings,
    not for the files that happen to be selected.  Only a switched-off recognition
    (``recognition_enabled``) leaves it out: no page is read, so no page image goes anywhere.
    Personal-info removal is listed too —
    although it is not a pipeline step, it is the one place where names are taken out, so
    the box has to say whether it is on.  *anonymizer_models* are the spaCy models it will
    load, shown next to it.
    """
    hidden = config.anonymization_enabled
    ocr = ocr_location(config.ocr.engine, config.ocr.model, config.ollama_url)
    stages = [DataFlowStage(Stage.OCR, ocr, "page images")] if config.recognition_enabled else []
    stages += [
        DataFlowStage(
            STAGE_ANONYMIZATION,
            StageLocation(DataLocation.THIS_COMPUTER, "spaCy"),
            "text",
            personal_info_hidden=hidden,
            detail=anonymization_badge(anonymizer_models) if hidden else "Off",
            languages=anonymization_languages(anonymizer_models) if hidden else (),
        ),
    ]
    for step in llm_steps(config.correction_enabled, config.evaluation_enabled):
        if step == Stage.CORRECTION:
            provider, model = config.correction_provider, config.effective_correction_model
        else:
            provider, model = config.evaluate_provider, config.effective_evaluate_model
        stages.append(
            DataFlowStage(
                step,
                llm_location(provider, model, config.ollama_url, provider_info, provider_region),
                "text",
                personal_info_hidden=hidden,
            )
        )
    return DataFlow(tuple(stages))
