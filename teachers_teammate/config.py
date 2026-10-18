"""Pipeline configuration: the :class:`Config` dataclass and TOML file helpers.

This module is the single source of truth for runtime configuration.  Both
the CLI (argument parsing → :class:`Config`) and the GUI
(:class:`~teachers_teammate.gui._config_panel.ConfigPanel` → :class:`Config`)
build a :class:`Config` and hand it to
:class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import sys
import tomllib
from typing import Any

from ._model_defaults import default_model_for_task
from .domain.data_location import LOCAL_OCR_ENGINES
from .domain.model_catalog import DEFAULT_OCR_MODEL, DEFAULT_WRITING_MODEL, PROFILE_SETTINGS
from .domain.pdf_text_layer import (
    PDF_TEXT_LAYER_MODES,
    PdfTextLayerMode,
    normalize_pdf_text_layer,
    parse_pdf_text_layer,
)
from .exceptions import ConfigFileParseError
from .interfaces import SUPPORTED_SUFFIXES as SUPPORTED_SUFFIXES  # noqa: PLC0414

DEFAULTS: dict[str, Any] = {
    # ── OCR ──────────────────────────────────────────────────────────────────
    "ocr_engine": "ollama",
    "ocr_model": DEFAULT_OCR_MODEL,
    "language": "English",
    "preprocess_method": "grayscale",
    "ollama_url": "http://127.0.0.1:11434",
    "ocr_timeout": 180,
    "ocr_temperature": 0.0,
    # ── Preprocessing (pre-steps and PDF rendering) ───────────────────────────
    "pdf_render_dpi": 300,
    # "auto" | "always" | "never" — use a PDF's embedded text instead of OCR.
    "pdf_text_layer": "auto",
    "preprocess_dewarp": True,
    "preprocess_deskew": True,
    "preprocess_border_crop": False,
    "preprocess_denoise": True,
    "preprocess_gamma": False,
    # ── Input / output ────────────────────────────────────────────────────────
    "recursive": False,
    "debug": False,
    # ── Correction ────────────────────────────────────────────────────────────
    "correction_enabled": True,
    "anonymization_enabled": False,
    "correction_provider": "ollama",
    "correction_model": DEFAULT_WRITING_MODEL,
    "correction_prompt": "",
    "correction_temperature": 0.7,
    # ── Evaluation ────────────────────────────────────────────────────────────
    "evaluation_enabled": False,
    "evaluate_provider": "ollama",
    "evaluate_model": DEFAULT_WRITING_MODEL,
    "evaluate_prompt": (
        "You are a quality evaluator for OCR correction output. "
        "Given corrected text, provide a concise quality report with: "
        "1) overall quality score from 1-10, "
        "2) key remaining issues, "
        "3) confidence level, and "
        "4) short recommendation. "
        "Keep the response compact and actionable."
    ),
    "evaluate_temperature": 0.7,
    # ── Output ────────────────────────────────────────────────────────────────
    "docx_format": "table",
    "docx_enabled": False,
    # ── Anonymizer ────────────────────────────────────────────────────────────
    "anonymizer_secondary_model": None,
    # ── Hardware recommendations ──────────────────────────────────────────────
    # "auto" | "cpu" | "8gb" | "16gb" — GUI-only; the pipeline never reads it.
    "hardware_profile": "auto",
}


def normalize_ocr_engine(value: object) -> str:
    """Return *value* if it is a local OCR engine, else the default engine.

    Settings files written by older versions may name an online engine (``langchain`` or a
    provider such as ``openai``).  Text recognition never goes online any more, so those
    fall back to the default instead of silently uploading pages.
    """
    engine = str(value).strip().lower()
    if engine in LOCAL_OCR_ENGINES:
        return engine
    print(
        f"WARNING: ocr_engine {value!r} is not available (text recognition stays on this "
        f"computer; choose one of {', '.join(LOCAL_OCR_ENGINES)}) — using "
        f"'{DEFAULTS['ocr_engine']}'.",
        file=sys.stderr,
    )
    return DEFAULTS["ocr_engine"]


def normalize_ocr_model(value: object, model: object) -> str:
    """Return *model*, or the default OCR model when *value* named an unavailable engine.

    A model name saved next to an online engine (``gpt-4o``) means nothing to the engine
    that replaces it, so it is reset together with the engine.
    """
    if str(value).strip().lower() in LOCAL_OCR_ENGINES:
        return str(model)
    return DEFAULTS["ocr_model"]


@dataclass
class OcrConfig:
    """The OCR slice of the runtime configuration.

    Holds only the fields that vary per OCR run (engine/model/
    preprocessing/temperature).  Shared fields the OCR stage *reads* but that are
    also consumed by correction/evaluation — ``language``, ``ollama_url``,
    ``ocr_timeout`` — stay on :class:`Config`.  Both the pipeline and the
    benchmark app vary this value object.
    """

    engine: str = DEFAULTS["ocr_engine"]
    model: str = DEFAULTS["ocr_model"]
    preprocess_method: str = DEFAULTS["preprocess_method"]
    temperature: float = DEFAULTS["ocr_temperature"]
    pdf_render_dpi: int = DEFAULTS["pdf_render_dpi"]
    pdf_text_layer: PdfTextLayerMode = DEFAULTS["pdf_text_layer"]
    """Whether a PDF's embedded text may replace OCR (``auto``/``always``/``never``)."""
    dewarp: bool = DEFAULTS["preprocess_dewarp"]
    deskew: bool = DEFAULTS["preprocess_deskew"]
    border_crop: bool = DEFAULTS["preprocess_border_crop"]
    denoise: bool = DEFAULTS["preprocess_denoise"]
    gamma: bool = DEFAULTS["preprocess_gamma"]

    def __post_init__(self) -> None:
        # Programmatic callers and GUI settings bypass argparse ``choices``; fold
        # case/whitespace and fall back to "auto" for anything that is not a mode.
        self.pdf_text_layer = normalize_pdf_text_layer(self.pdf_text_layer)

    @classmethod
    def from_settings(cls, values: Mapping[str, object]) -> OcrConfig:
        """Build an :class:`OcrConfig` from a flat settings dict (``ocr.toml`` key names).

        Keys that are missing fall back to :data:`DEFAULTS`.  This is the single place the
        GUI's flat settings (``ocr_engine``, ``preprocess_dewarp``, …) map onto the fields.
        """

        def get(key: str) -> Any:
            return values.get(key, DEFAULTS[key])

        return cls(
            engine=normalize_ocr_engine(get("ocr_engine")),
            model=normalize_ocr_model(get("ocr_engine"), get("ocr_model")),
            preprocess_method=str(get("preprocess_method")),
            temperature=float(get("ocr_temperature")),
            pdf_render_dpi=int(get("pdf_render_dpi")),
            pdf_text_layer=get("pdf_text_layer"),
            dewarp=bool(get("preprocess_dewarp")),
            deskew=bool(get("preprocess_deskew")),
            border_crop=bool(get("preprocess_border_crop")),
            denoise=bool(get("preprocess_denoise")),
            gamma=bool(get("preprocess_gamma")),
        )


@dataclass
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
    correction_prompt: str = DEFAULTS["correction_prompt"]
    anonymization_enabled: bool = DEFAULTS["anonymization_enabled"]

    # ── Evaluation ────────────────────────────────────────────────────────────
    evaluation_enabled: bool = DEFAULTS["evaluation_enabled"]
    evaluate_provider: str = DEFAULTS["evaluate_provider"]
    evaluate_model: str = DEFAULTS["evaluate_model"]
    evaluate_prompt: str = DEFAULTS["evaluate_prompt"]

    # ── Output ───────────────────────────────────────────────────────────────
    docx_format: str = DEFAULTS["docx_format"]
    docx_enabled: bool = DEFAULTS["docx_enabled"]

    # ── Anonymizer ───────────────────────────────────────────────────────────
    anonymizer_secondary_model: str | None = None
    # None = use DEFAULT_PATTERNS from anonymizer module; [] = no regex patterns
    anonymizer_patterns: list[tuple[str, str]] | None = None

    # ── Temperature ──────────────────────────────────────────────────────────
    correction_temperature: float = DEFAULTS["correction_temperature"]
    evaluate_temperature: float = DEFAULTS["evaluate_temperature"]

    # ── Internal ─────────────────────────────────────────────────────────────
    ocr_timeout: int = DEFAULTS["ocr_timeout"]

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


def _read_toml(path: Path) -> dict[str, Any]:
    """Return the parsed TOML of *path*, raising :exc:`ConfigFileParseError` on failure."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except OSError as exc:
        raise ConfigFileParseError(f"Cannot read config file '{path}': {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileParseError(f"Could not parse config file {path}: {exc}") from exc


def unavailable_ocr_engine(path: Path) -> str:
    """Return the OCR engine *path* names if it is no longer available, else ``""``.

    :func:`load_config_file` already swaps such an engine for the default; this tells a
    GUI what was swapped so it can say so instead of changing the setting silently.
    """
    try:
        engine = _read_toml(path).get("ocr_engine")
    except ConfigFileParseError:
        return ""
    if engine is None or str(engine).strip().lower() in LOCAL_OCR_ENGINES:
        return ""
    return str(engine)


def load_config_file(path: Path) -> dict:
    """Parse *path* as TOML and return a dict of recognised pipeline keys.

    Unknown provider values are passed through unchanged; callers are
    responsible for validating against the runtime provider list.

    Args:
        path: Path to a TOML file.

    Returns:
        Dict suitable for passing to :meth:`argparse.ArgumentParser.set_defaults`.
    """
    known_keys = {
        "input",
        "output",
        "recursive",
        "debug",
        "ocr_engine",
        "ocr_model",
        "language",
        "preprocess_method",
        "ollama_url",
        "ocr_timeout",
        "correction_enabled",
        "anonymization_enabled",
        "correction_provider",
        "correction_model",
        "correction_prompt",
        "correction_temperature",
        "evaluation_enabled",
        "evaluate_provider",
        "evaluate_model",
        "evaluate_prompt",
        "evaluate_temperature",
        "docx_format",
        "docx_enabled",
        "anonymizer_secondary_model",
        "anonymizer_patterns",
        "ocr_temperature",
        "pdf_render_dpi",
        "pdf_text_layer",
        "preprocess_dewarp",
        "preprocess_deskew",
        "preprocess_border_crop",
        "preprocess_denoise",
        "preprocess_gamma",
        "hardware_profile",
    }
    raw = _read_toml(path)
    result = {k: v for k, v in raw.items() if k in known_keys}

    if "ocr_engine" in result:
        result["ocr_model"] = normalize_ocr_model(
            result["ocr_engine"], result.get("ocr_model", DEFAULTS["ocr_model"])
        )
        result["ocr_engine"] = normalize_ocr_engine(result["ocr_engine"])

    if "pdf_text_layer" in result:
        raw_mode = result["pdf_text_layer"]
        mode = parse_pdf_text_layer(raw_mode)
        if mode is not None:
            result["pdf_text_layer"] = mode
        else:
            print(
                f"WARNING: pdf_text_layer must be one of {', '.join(PDF_TEXT_LAYER_MODES)}"
                f" (got {raw_mode!r}) — ignored.",
                file=sys.stderr,
            )
            del result["pdf_text_layer"]

    if "anonymizer_patterns" in result:
        raw_pats = result["anonymizer_patterns"]
        if not isinstance(raw_pats, list) or not all(
            isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(x, str) for x in p)
            for p in raw_pats
        ):
            print(
                "WARNING: anonymizer_patterns must be a list of [tag, pattern] string pairs — ignored.",
                file=sys.stderr,
            )
            del result["anonymizer_patterns"]
        else:
            result["anonymizer_patterns"] = [tuple(p) for p in raw_pats]

    if "hardware_profile" in result and result["hardware_profile"] not in PROFILE_SETTINGS:
        print(
            f"WARNING: hardware_profile '{result['hardware_profile']}' is not one of "
            f"{', '.join(PROFILE_SETTINGS)} — falling back to 'auto'.",
            file=sys.stderr,
        )
        del result["hardware_profile"]

    return result


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
