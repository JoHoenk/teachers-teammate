"""Where each step of a run takes place — answers "does anything leave my computer?".

Combines the pure rules in :mod:`teachers_teammate.domain.data_location` with the facts
only the outside world can answer (is this Ollama URL this computer? is a provider
hosted by a vendor?).  The GUI and CLI render the result; neither decides anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from ..config import Config
from ..domain.data_location import (
    OCR_ENGINE_NAMES,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    DataFlow,
    DataFlowStage,
    DataLocation,
    StageLocation,
    classify_ollama,
    online_ocr_reason,
)
from ..infrastructure.ollama_utils import is_local_ollama_url, ollama_host

ProviderInfo = Callable[[str], Mapping[str, Any]]


def _ollama_location(model: str, ollama_url: str) -> StageLocation:
    return classify_ollama(
        model, url_is_local=is_local_ollama_url(ollama_url), host=ollama_host(ollama_url)
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


def llm_location(
    provider: str, model: str, ollama_url: str, provider_info: ProviderInfo
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
    if info.get("self_hosted"):
        location = _ollama_location(model, ollama_url)
        # An online result (an Ollama cloud model) keeps its own, more telling name.
        if location.location is DataLocation.ONLINE:
            return location
        return replace(location, service=name)
    return StageLocation(DataLocation.ONLINE, name)


def describe_data_flow(config: Config, provider_info: ProviderInfo) -> DataFlow:
    """Return where every enabled step of *config* runs.

    Text recognition is listed even for text-only inputs: the answer is for the settings,
    not for the files that happen to be selected.
    """
    hidden = config.anonymization_enabled and config.correction_enabled
    ocr = ocr_location(config.ocr.engine, config.ocr.model, config.ollama_url)
    stages = [DataFlowStage(STAGE_OCR, ocr, "page images")]
    if config.correction_enabled:
        stages.append(
            DataFlowStage(
                STAGE_CORRECTION,
                llm_location(
                    config.correction_provider,
                    config.effective_correction_model,
                    config.ollama_url,
                    provider_info,
                ),
                "text",
                personal_info_hidden=hidden,
            )
        )
        if config.evaluation_enabled:
            stages.append(
                DataFlowStage(
                    STAGE_EVALUATION,
                    llm_location(
                        config.evaluate_provider,
                        config.effective_evaluate_model,
                        config.ollama_url,
                        provider_info,
                    ),
                    "text",
                    personal_info_hidden=hidden,
                )
            )
    return DataFlow(tuple(stages))
