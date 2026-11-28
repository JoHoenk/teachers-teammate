"""Rules a whole run follows, read off the settings and the computer.

The context windows a run gives the models it talks to, and which of them live on an Ollama
server.  A rule here takes the ``Config`` and returns a value: no I/O, no sentence, so every
caller — the run, the cache keys, the banner — reads the same answer.
"""

from __future__ import annotations

from .config import Config
from .domain.context_budget import BASE_NUM_CTX, RunWindows, stage_windows
from .domain.data_location import ENGINE_OLLAMA, PROVIDER_OLLAMA
from .domain.hardware_profile import HardwareDetection


def resolve_run_windows(config: Config, hardware: HardwareDetection | None) -> RunWindows:
    """Return the context windows of a run, resolved once from the settings and the computer.

    The window is a property of the computer and the models, not of the file, so it is
    resolved once and passed down: a window that changed per file would make Ollama reload the
    model for every file.  Each stage is sized from its own provider and model; an online
    provider brings its own window, which the app does not set.  *hardware* is ``None`` when
    the computer is not known, which keeps the base window.  The rules live in
    :func:`~teachers_teammate.domain.context_budget.stage_windows`.
    """
    return stage_windows(
        config.proofreading_context,
        proofreading_model=config.effective_correction_model,
        evaluation_model=config.effective_evaluate_model,
        proofreading_local=config.correction_provider == PROVIDER_OLLAMA,
        evaluation_local=config.evaluate_provider == PROVIDER_OLLAMA,
        evaluation_runs=config.evaluation_enabled,
        card_gb=hardware.card_gb if hardware is not None else None,
    )


def window_of_model(config: Config, windows: RunWindows, tag: str) -> int:
    """Return the largest window a run gives the Ollama model *tag*.

    A model that only reads scans keeps the base window: OCR requests do not set one.
    """
    sizes = [BASE_NUM_CTX]
    if (
        config.correction_enabled
        and config.correction_provider == PROVIDER_OLLAMA
        and config.effective_correction_model == tag
    ):
        sizes.append(windows.proofreading)
    if (
        config.evaluation_enabled
        and config.evaluate_provider == PROVIDER_OLLAMA
        and config.effective_evaluate_model == tag
    ):
        sizes.append(windows.evaluation)
    return max(sizes)


def uses_ollama(config: Config) -> bool:
    """Return whether any enabled stage of *config* talks to an Ollama server."""
    return (
        (config.recognition_enabled and config.ocr.engine == ENGINE_OLLAMA)
        or (config.correction_enabled and config.correction_provider == PROVIDER_OLLAMA)
        or (config.evaluation_enabled and config.evaluate_provider == PROVIDER_OLLAMA)
    )


def ollama_models(config: Config) -> tuple[str, ...]:
    """Return the Ollama models the enabled stages of *config* read and write with."""
    models: list[str] = []
    if config.recognition_enabled and config.ocr.engine == ENGINE_OLLAMA and config.ocr.model:
        models.append(config.ocr.model)
    if config.correction_enabled and config.correction_provider == PROVIDER_OLLAMA:
        model = config.effective_correction_model or ""
        if model and model not in models:
            models.append(model)
    if config.evaluation_enabled and config.evaluate_provider == PROVIDER_OLLAMA:
        model = config.effective_evaluate_model or ""
        if model and model not in models:
            models.append(model)
    return tuple(models)
