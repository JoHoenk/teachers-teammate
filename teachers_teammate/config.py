"""Pipeline configuration: the :class:`Config` dataclass and TOML file helpers.

This module is the single source of truth for runtime configuration.  Both
the CLI (argument parsing → :class:`Config`) and the GUI
(:class:`~teachers_teammate.gui._config_panel.ConfigPanel` → :class:`Config`)
build a :class:`Config` and hand it to
:class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
import tomllib
from typing import Any

from ._model_defaults import default_model_for_task
from .exceptions import ConfigFileParseError
from .interfaces import SUPPORTED_SUFFIXES as SUPPORTED_SUFFIXES  # noqa: PLC0414

DEFAULTS: dict[str, Any] = {
    # ── OCR ──────────────────────────────────────────────────────────────────
    "ocr_engine": "ollama",
    "ocr_model": "deepseek-ocr:latest",
    "ocr_provider": "",
    "language": "English",
    "preprocess_method": "grayscale",
    "ollama_url": "http://127.0.0.1:11434",
    "ocr_timeout": 180,
    "ocr_temperature": 0.0,
    # ── Preprocessing (pre-steps and PDF rendering) ───────────────────────────
    "pdf_render_dpi": 300,
    "preprocess_dewarp": False,
    "preprocess_deskew": False,
    "preprocess_border_crop": False,
    "preprocess_denoise": False,
    "preprocess_gamma": False,
    # ── Input / output ────────────────────────────────────────────────────────
    "recursive": False,
    "debug": False,
    # ── Correction ────────────────────────────────────────────────────────────
    "correction_enabled": True,
    "anonymization_enabled": False,
    "correction_provider": "ollama",
    "correction_model": "gpt-oss:20b",
    "correction_prompt": "",
    "correction_temperature": 0.7,
    # ── Evaluation ────────────────────────────────────────────────────────────
    "evaluation_enabled": False,
    "evaluate_provider": "ollama",
    "evaluate_model": "gpt-oss:20b",
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
}


@dataclass
class OcrConfig:
    """The OCR slice of the runtime configuration.

    Holds only the fields that vary per OCR run (engine/model/provider/
    preprocessing/temperature).  Shared fields the OCR stage *reads* but that are
    also consumed by correction/evaluation — ``language``, ``ollama_url``,
    ``ocr_timeout`` — stay on :class:`Config`.  Both the pipeline and the
    benchmark app vary this value object.
    """

    engine: str = DEFAULTS["ocr_engine"]
    model: str = DEFAULTS["ocr_model"]
    # Used only when ``engine == "langchain"``.
    provider: str = DEFAULTS["ocr_provider"]
    preprocess_method: str = DEFAULTS["preprocess_method"]
    temperature: float = DEFAULTS["ocr_temperature"]
    pdf_render_dpi: int = DEFAULTS["pdf_render_dpi"]
    dewarp: bool = DEFAULTS["preprocess_dewarp"]
    deskew: bool = DEFAULTS["preprocess_deskew"]
    border_crop: bool = DEFAULTS["preprocess_border_crop"]
    denoise: bool = DEFAULTS["preprocess_denoise"]
    gamma: bool = DEFAULTS["preprocess_gamma"]

    @property
    def effective_model(self) -> str:
        """Return the OCR model, falling back to provider defaults for the langchain engine."""
        if self.model:
            return self.model
        return default_model_for_task(self.provider, "ocr")


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
        "ocr_provider",
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
        "preprocess_dewarp",
        "preprocess_deskew",
        "preprocess_border_crop",
        "preprocess_denoise",
        "preprocess_gamma",
    }
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except OSError as exc:
        raise ConfigFileParseError(f"Cannot read config file '{path}': {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileParseError(f"Could not parse config file {path}: {exc}") from exc
    result = {k: v for k, v in raw.items() if k in known_keys}

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
