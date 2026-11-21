"""Pipeline configuration: the :class:`Config` dataclass and TOML file helpers.

This module is the single place a :class:`Config` is assembled.  Both the CLI (argument
parsing → :meth:`Config.from_settings`) and the GUI
(:class:`~teachers_teammate.gui._config_panel.ConfigPanel` → :meth:`Config.from_settings`)
build one and hand it to :class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline`.
Which keys exist, what they default to and what they accept is
:mod:`teachers_teammate.settings_schema`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from ._model_defaults import default_model_for_task
from .domain.correction_policy import CorrectionOutput
from .domain.model_catalog import (
    OCR_TEMPERATURE,
    HardwareProfileSetting,
    RequestProfile,
    request_profile,
    review_temperature,
)
from .domain.output_files import DocxFormat
from .domain.pdf_text_layer import PdfTextLayerMode, normalize_pdf_text_layer
from .domain.setting_values import ContextSetting, PreprocessMethod
from .exceptions import ConfigFileParseError
from .interfaces import SUPPORTED_SUFFIXES as SUPPORTED_SUFFIXES  # noqa: PLC0414

# ``DEFAULTS`` is read from here by most of the app; every other name of the schema is imported
# from ``settings_schema`` itself.
from .settings_schema import (
    DEFAULTS as DEFAULTS,  # noqa: PLC0414
    SECONDARY_MODEL_AUTO,
    LoadedSettings,
    normalize_ocr_engine,
    normalize_ocr_model,
    normalize_preprocess_method,
    normalize_settings,
)


@dataclass(frozen=True)
class OcrConfig:
    """The OCR slice of the runtime configuration.

    Holds only the fields that vary per OCR run (engine/model/
    preprocessing/temperature).  Shared fields the OCR stage *reads* but that are
    also consumed by correction/evaluation — ``language``, ``ollama_url``,
    ``ocr_timeout`` — stay on :class:`Config`.  Both the pipeline and the
    benchmark app vary this value object.
    """

    engine: str = DEFAULTS["ocr_engine"]
    """An :class:`~teachers_teammate.domain.data_location.OcrEngine` for a run; a ``str`` because a
    stored Benchmark run keeps the engine it was read with, an old online one included."""
    model: str = DEFAULTS["ocr_model"]
    preprocess_method: PreprocessMethod = DEFAULTS["preprocess_method"]
    temperature: float = OCR_TEMPERATURE
    """Fixed for the app; only the Benchmark varies it to compare readings."""
    pdf_render_dpi: int = DEFAULTS["pdf_render_dpi"]
    pdf_text_layer: PdfTextLayerMode = DEFAULTS["pdf_text_layer"]
    """Whether a PDF's embedded text may replace OCR (``auto``/``always``/``never``)."""

    def __post_init__(self) -> None:
        # Programmatic callers and GUI settings bypass argparse ``choices``; fold
        # case/whitespace and fall back to "auto" for anything that is not a mode.
        object.__setattr__(
            self, "preprocess_method", normalize_preprocess_method(self.preprocess_method)
        )
        object.__setattr__(self, "pdf_render_dpi", int(self.pdf_render_dpi))
        object.__setattr__(self, "pdf_text_layer", normalize_pdf_text_layer(self.pdf_text_layer))

    @classmethod
    def from_settings(cls, values: Mapping[str, object]) -> OcrConfig:
        """Build an :class:`OcrConfig` from a flat settings dict (``ocr.toml`` key names).

        Keys that are missing fall back to :data:`DEFAULTS`.  This is the single place the
        GUI's flat settings (``ocr_engine``, ``preprocess_method``, …) map onto the fields.

        The engine and its model are normalised here and not in :meth:`__post_init__`: an
        :class:`OcrConfig` is also the record of a Benchmark run, and a stored run must keep
        the engine it was read with (an old online one included) so its history still reads
        right.
        """

        def get(key: str) -> Any:
            return values.get(key, DEFAULTS[key])

        engine = get("ocr_engine")
        return cls(
            engine=normalize_ocr_engine(engine),
            model=normalize_ocr_model(engine, get("ocr_model")),
            preprocess_method=get("preprocess_method"),
            pdf_render_dpi=get("pdf_render_dpi"),
            pdf_text_layer=get("pdf_text_layer"),
        )


@dataclass(frozen=True)
class Config:
    """All runtime configuration, derived from CLI arguments or the GUI."""

    # ── Input ────────────────────────────────────────────────────────────────
    input_dir: Path
    output_dir: Path
    recursive: bool
    debug: bool

    # ── OCR Parameters ────────────────────────────────────────────────────────
    ocr: OcrConfig
    language: str
    ollama_url: str

    # ── Correction ────────────────────────────────────────────────────────────
    correction_enabled: bool
    correction_provider: str
    correction_model: str
    correction_notes: str = DEFAULTS["correction_notes"]
    correction_level: str = DEFAULTS["correction_level"]
    explanation_language: str = DEFAULTS["explanation_language"]
    correction_output: CorrectionOutput = DEFAULTS["correction_output"]
    anonymization_enabled: bool = DEFAULTS["anonymization_enabled"]

    # ── Evaluation ────────────────────────────────────────────────────────────
    evaluation_enabled: bool = DEFAULTS["evaluation_enabled"]
    evaluate_provider: str = DEFAULTS["evaluate_provider"]
    evaluate_model: str = DEFAULTS["evaluate_model"]
    evaluate_prompt: str = DEFAULTS["evaluate_prompt"]

    # ── Output ───────────────────────────────────────────────────────────────
    docx_format: DocxFormat = DEFAULTS["docx_format"]
    docx_enabled: bool = DEFAULTS["docx_enabled"]
    text_enabled: bool = DEFAULTS["text_enabled"]
    text_anonymized_enabled: bool = DEFAULTS["text_anonymized_enabled"]

    # ── Hardware ─────────────────────────────────────────────────────────────
    # "auto" (detect) or a profile chosen by hand ("cpu" / "8gb" / "16gb").  Only the
    # recommendations and the run-start memory warning read it.
    hardware_profile: HardwareProfileSetting = DEFAULTS["hardware_profile"]

    # ── Context window ───────────────────────────────────────────────────────
    # "auto" (as large as the graphics card holds at full speed) or a fixed window in tokens.
    # An unknown value falls back to "auto" with a warning, as hardware_profile does.
    proofreading_context: ContextSetting = DEFAULTS["proofreading_context"]

    # ── Text recognition ─────────────────────────────────────────────────────
    # Deliberately not part of OcrConfig: that slice says *how* pages are read (and
    # fingerprints the cached text); this says *whether* scans are read at all.  Off, a scan
    # is skipped; text files and PDFs with a text layer are used as they are.
    recognition_enabled: bool = DEFAULTS["recognition_enabled"]

    # ── Run ──────────────────────────────────────────────────────────────────
    # Keep the computer awake while a run is going.  Not in any fingerprint: it changes how the
    # run is carried out, not what it produces.
    keep_awake: bool = DEFAULTS["keep_awake"]

    # ── Start ────────────────────────────────────────────────────────────────
    # Whether the app asks GitHub for a newer version when it starts.  The GUI reads it; no
    # pipeline step does, and it is in no fingerprint.
    check_for_updates: bool = DEFAULTS["check_for_updates"]

    # ── Anonymizer ───────────────────────────────────────────────────────────
    # None = derive the spaCy model from the document language ("Auto")
    anonymizer_primary_model: str | None = None
    # SECONDARY_MODEL_AUTO (the default), SECONDARY_MODEL_NONE / None, or a model name
    anonymizer_secondary_model: str | None = SECONDARY_MODEL_AUTO
    # None (or the earlier built-in list) = current DEFAULT_PATTERNS from the anonymizer module;
    # [] = no regex patterns
    anonymizer_patterns: list[tuple[str, str]] | None = None

    # ── Internal ─────────────────────────────────────────────────────────────
    ocr_timeout: int = DEFAULTS["ocr_timeout"]

    @classmethod
    def from_settings(
        cls,
        values: Mapping[str, object],
        *,
        input_dir: Path,
        output_dir: Path,
        recursive: bool,
        ocr: OcrConfig | None = None,
    ) -> Config:
        """Build a :class:`Config` from a flat settings dict (``ocr.toml`` key names).

        The one place a :class:`Config` is assembled: the GUI, the CLI and the Benchmark all
        come through here, so a key means the same in all three.  Keys *values* does not hold
        fall back to :data:`DEFAULTS`; the folders and ``recursive`` are the caller's, because
        they are what a batch is about rather than a setting of the app.
        """
        settings = normalize_settings(values).values

        def get(key: str) -> Any:
            return settings.get(key, DEFAULTS[key])

        return cls(
            input_dir=input_dir,
            output_dir=output_dir,
            recursive=recursive,
            ocr=ocr if ocr is not None else OcrConfig.from_settings(settings),
            debug=get("debug"),
            language=get("language"),
            ollama_url=get("ollama_url"),
            ocr_timeout=get("ocr_timeout"),
            recognition_enabled=get("recognition_enabled"),
            keep_awake=get("keep_awake"),
            check_for_updates=get("check_for_updates"),
            correction_enabled=get("correction_enabled"),
            anonymization_enabled=get("anonymization_enabled"),
            correction_provider=get("correction_provider"),
            correction_model=get("correction_model"),
            correction_notes=get("correction_notes"),
            correction_level=get("correction_level"),
            explanation_language=get("explanation_language"),
            correction_output=get("correction_output"),
            evaluation_enabled=get("evaluation_enabled"),
            evaluate_provider=get("evaluate_provider"),
            evaluate_model=get("evaluate_model"),
            evaluate_prompt=get("evaluate_prompt"),
            docx_format=get("docx_format"),
            docx_enabled=get("docx_enabled"),
            text_enabled=get("text_enabled"),
            text_anonymized_enabled=get("text_anonymized_enabled"),
            anonymizer_primary_model=get("anonymizer_primary_model"),
            anonymizer_secondary_model=get("anonymizer_secondary_model"),
            anonymizer_patterns=get("anonymizer_patterns"),
            hardware_profile=get("hardware_profile"),
            proofreading_context=get("proofreading_context"),
        )

    @property
    def writes_text_files(self) -> bool:
        """Whether a run saves text files (which the next run must not read as inputs)."""
        return self.text_enabled or self.text_anonymized_enabled

    @property
    def writes_output_files(self) -> bool:
        """Whether a run saves any file into ``output_dir`` (not just the cache)."""
        return self.docx_enabled or self.writes_text_files

    @property
    def effective_correction_model(self) -> str:
        if self.correction_model:
            return self.correction_model
        return default_model_for_task(self.correction_provider, "correction")

    @property
    def effective_evaluate_model(self) -> str:
        if self.evaluate_model:
            return self.evaluate_model
        return default_model_for_task(self.evaluate_provider, "evaluation")

    @property
    def correction_profile(self) -> RequestProfile:
        """Return how the proofreading requests are built: the compact rule and the temperatures.

        The model's own profile, resolved for the provider.  Teachers set no temperature; step ①
        runs at the value measured for it and steps ② and ③ at the one the model's maker
        recommends (see :data:`~.domain.model_catalog.STEP_ONE_TEMPERATURES` and
        :data:`~.domain.model_catalog.MAKER_TEMPERATURES`).
        """
        return request_profile(self.effective_correction_model, self.correction_provider)

    @property
    def correction_temperature(self) -> float:
        """Return the temperature the online model itself is built with.

        Every step of an online profile is sent at this value
        (:data:`~.domain.model_catalog.DEFAULT_CORRECTION_TEMPERATURE`), so it is the model's own
        temperature and not a request's.  A local model is asked per step instead
        (``correction_profile``), because Ollama takes the temperature with each request.
        """
        return self.correction_profile.review_temperature

    @property
    def evaluate_think(self) -> bool | str:
        """Return how the content review asks a reasoning model to think (see ``RequestProfile``)."""
        return request_profile(self.effective_evaluate_model, self.evaluate_provider).think

    @property
    def evaluate_temperature(self) -> float:
        """Return the temperature the content review is sent at."""
        return review_temperature(self.effective_evaluate_model, self.evaluate_provider)


def _read_toml(path: Path) -> dict[str, Any]:
    """Return the parsed TOML of *path*, raising :exc:`ConfigFileParseError` on failure."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except OSError as exc:
        raise ConfigFileParseError(f"Cannot read config file '{path}': {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileParseError(f"Could not parse config file {path}: {exc}") from exc


#: The settings the installer may write before the app has ever started.  A settings file that
#: holds nothing else is not a teacher's saved setup: the first start (the Setup Assistant) still
#: runs, and carries these values over.
INSTALLER_SETTING_KEYS: frozenset[str] = frozenset({"check_for_updates"})


def load_config_file(path: Path) -> LoadedSettings:
    """Parse *path* as TOML and return the settings it holds, and what was changed about them.

    Keys the schema does not know are dropped; a value a key does not accept is left out (the
    default applies) and reported in ``notices``, so a caller can say so instead of changing a
    teacher's setting silently.

    Args:
        path: Path to a TOML file.

    Returns:
        The recognised values, suitable for :meth:`Config.from_settings` and for
        :meth:`argparse.ArgumentParser.set_defaults`.
    """
    return normalize_settings(_read_toml(path))


def _toml_string(val: str) -> str:
    """Return *val* as a properly-escaped TOML value string.

    Multi-line values use triple-quoted basic strings; single-line values use
    regular quoted basic strings.  All required escape sequences are applied so
    that ``tomllib.loads()`` round-trips the value correctly.
    """
    if "\n" in val or "\r" in val:
        # Escape backslashes first, then protect any embedded triple-quote.
        escaped = val.replace("\\", "\\\\").replace('"""', '""\\"')
        return f'"""\n{escaped}\n"""'
    escaped = (
        val.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\x08", "\\b")
        .replace("\x0c", "\\f")
    )
    return f'"{escaped}"'


def _toml_list(val: list) -> str:
    """Serialize a Python list as a TOML inline array."""
    items: list[str] = []
    for item in val:
        if isinstance(item, (list, tuple)):
            sub = ", ".join(_toml_string(x) if isinstance(x, str) else str(x) for x in item)
            items.append(f"[{sub}]")
        elif isinstance(item, str):
            items.append(_toml_string(item))
        else:
            items.append(str(item))
    return "[" + ", ".join(items) + "]"


def dump_config_file(values: dict) -> str:
    """Serialize a settings *values* dict to ``ocr.toml`` text.

    Inverse of :func:`load_config_file`: the output round-trips back through it.
    ``None`` values are skipped.  Keys are written in iteration order.
    """
    lines = ["# ocr.toml — Teacher's Teammate configuration\n"]
    for key, val in values.items():
        if val is None:
            continue
        if isinstance(val, bool):
            lines.append(f"{key} = {'true' if val else 'false'}\n")
        elif isinstance(val, str):
            lines.append(f"{key} = {_toml_string(val)}\n")
        elif isinstance(val, list):
            lines.append(f"{key} = {_toml_list(val)}\n")
        else:
            lines.append(f"{key} = {val}\n")
    return "".join(lines)
