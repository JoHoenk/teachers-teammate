"""Where each step of a run takes place — answers "does anything leave my computer?".

Combines the pure rules in :mod:`teachers_teammate.domain.data_location` with the facts
only the outside world can answer (is this Ollama URL this computer? is a provider
hosted by a vendor, and where does that vendor process the data?).  The GUI and CLI
render the result; neither decides anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from ..config import DEFAULTS, Config
from ..domain.data_location import (
    OCR_ENGINE_NAMES,
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    DataFlow,
    DataFlowStage,
    DataLocation,
    DataRegion,
    StageLocation,
    anonymization_badge,
    classify_ollama,
    llm_stage_location,
    online_ocr_reason,
)
from ..infrastructure.ollama_utils import is_local_ollama_url, ollama_host

ProviderInfo = Callable[[str], Mapping[str, Any]]
#: Answers where a provider processes the data: ``"eu"``, ``"non_eu"`` or ``"unknown"``.
ProviderRegion = Callable[[str], str]


def _ollama_location(model: str, ollama_url: str) -> StageLocation:
    return classify_ollama(
        model,
        url_is_local=is_local_ollama_url(ollama_url, include_wsl_host=True),
        host=ollama_host(ollama_url),
    )


def ocr_refusal(engine: str, model: str, ollama_url: str) -> str:
    """Return why text recognition with these settings is refused, or ``""`` when allowed."""
    return online_ocr_reason(engine, model, ollama_host(ollama_url))


def ocr_location(engine: str, model: str, ollama_url: str) -> StageLocation:
    """Classify text recognition with *engine*; only engines the user controls are supported."""
    if engine == "ollama":
        return _ollama_location(model, ollama_url)
    if engine in OCR_ENGINE_NAMES:
        return StageLocation(DataLocation.THIS_COMPUTER, OCR_ENGINE_NAMES[engine])
    # Not a supported engine: a run is refused, so assume the worst for display.
    return StageLocation(DataLocation.ONLINE, engine)


def data_region_of(
    provider: str,
    info: Mapping[str, Any],
    provider_region: ProviderRegion | None = None,
) -> DataRegion:
    """Return where *provider* processes the data, defaulting to "not declared".

    *provider_region* answers providers whose region depends on their settings (a custom
    base URL, an EU endpoint); without it the provider's ``PROVIDER_INFO`` is used.  A
    value that is not one of the known regions counts as :attr:`DataRegion.UNKNOWN`: a
    school can only rely on what a vendor declares.
    """
    value = provider_region(provider) if provider_region is not None else info.get("data_region")
    try:
        return DataRegion(str(value or ""))
    except ValueError:
        return DataRegion.UNKNOWN


def llm_location(
    provider: str,
    model: str,
    ollama_url: str,
    provider_info: ProviderInfo,
    provider_region: ProviderRegion | None = None,
) -> StageLocation:
    """Classify a LangChain provider: self-hosted ones follow the server, others are online.

    "Self-hosted" is judged by the Ollama server settings (*ollama_url*, the model's cloud
    tag): the only self-hosted provider today is Ollama.  A second one would need its own
    server setting passed in here.

    A provider that does not declare ``"self_hosted": True`` in its ``PROVIDER_INFO`` is
    treated as a vendor-hosted online service — the safe default for a new provider.
    """
    info = provider_info(provider)
    name = str(info.get("display_name") or provider.capitalize())
    region = data_region_of(provider, info, provider_region)
    country = str(info.get("vendor_country") or "")
    self_hosted = bool(info.get("self_hosted"))
    where = llm_stage_location(
        self_hosted=self_hosted,
        model=model,
        url_is_local=is_local_ollama_url(ollama_url, include_wsl_host=True),
        host=ollama_host(ollama_url),
    )
    if not self_hosted:
        # The Ollama server settings say nothing about a vendor's own service.
        return StageLocation(DataLocation.ONLINE, name, region=region, vendor_country=country)
    location = _ollama_location(model, ollama_url)
    if where is DataLocation.ONLINE:
        # An Ollama cloud model keeps its own, more telling name.
        return replace(location, region=region, vendor_country=country)
    return replace(location, service=name)


def llm_steps(correction_enabled: bool, evaluation_enabled: bool) -> tuple[str, ...]:
    """Return the text steps a run takes, in order (``STAGE_CORRECTION``, ``STAGE_EVALUATION``).

    The content review reads the proofread text, so it only runs after proofreading.  The one
    place that says which steps exist: the data-flow box and the Settings window both ask it.
    """
    if not correction_enabled:
        return ()
    return (STAGE_CORRECTION, STAGE_EVALUATION) if evaluation_enabled else (STAGE_CORRECTION,)


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
            if (stage.stage != STAGE_OCR or recognition_enabled)
            and (
                stage.stage not in (STAGE_CORRECTION, STAGE_EVALUATION) or stage.stage in text_steps
            )
        )
    )


#: Settings-key prefix and default-model task of each text step.
_LLM_STEP_SETTINGS = {
    STAGE_CORRECTION: ("correction", "correction"),
    STAGE_EVALUATION: ("evaluate", "evaluation"),
}


def text_goes_online(
    settings: Mapping[str, object],
    describe: Callable[[str, str, str], StageLocation],
    effective_model: Callable[[str, str, str], str],
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
    ollama_url = str(settings.get("ollama_url") or DEFAULTS["ollama_url"])
    for step in steps:
        prefix, task = _LLM_STEP_SETTINGS[step]
        provider = str(settings.get(f"{prefix}_provider") or DEFAULTS[f"{prefix}_provider"])
        model = effective_model(provider, str(settings.get(f"{prefix}_model") or ""), task)
        if describe(provider, model, ollama_url).location is DataLocation.ONLINE:
            return True
    return False


def describe_data_flow(
    config: Config,
    provider_info: ProviderInfo,
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
    stages = [DataFlowStage(STAGE_OCR, ocr, "page images")] if config.recognition_enabled else []
    stages += [
        DataFlowStage(
            STAGE_ANONYMIZATION,
            StageLocation(DataLocation.THIS_COMPUTER, "spaCy"),
            "text",
            personal_info_hidden=hidden,
            detail=anonymization_badge(anonymizer_models) if hidden else "Off",
        ),
    ]
    for step in llm_steps(config.correction_enabled, config.evaluation_enabled):
        if step == STAGE_CORRECTION:
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
