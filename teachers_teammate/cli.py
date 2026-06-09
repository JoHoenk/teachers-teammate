"""Teacher's Teammate — command-line entry point.

Parses arguments, resolves configuration, and delegates to
:class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline`.

Usage::

    python -m teachers_teammate -i <input_folder> -o <output_folder> [options]

Supported input formats: PDF, JPG, JPEG, PNG, TXT
Output per file: <stem>.docx  (in output folder)
                 OCR/correction/evaluation stage text is cached in JSON state (in temp cache dir)
Preprocessing images are cleaned up after each run (kept with --debug).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import logging
from pathlib import Path
import sys
from typing import Protocol

from .application.service import ProcessingApplicationService
from .config import (
    DEFAULTS,
    Config,
    OcrConfig,
    load_config_file,
)
from .exceptions import ConfigFileNotFoundError, ConfigFileParseError


class _CliAppService(Protocol):
    def list_providers(self) -> list[str]: ...

    def resolve_config_path(self, explicit: str | None) -> Path | None: ...

    def run_selected(self, config: Config, /, *, config_file: Path | None = None) -> int: ...

    def run_preview_only(self, config: Config, /) -> int: ...


_PDF_DPI_MIN = 72
_PDF_DPI_MAX = 600


def _pdf_dpi_type(value: str) -> int:
    """Parse and range-check ``--pdf-render-dpi`` (mirrors the GUI's 72-600 spin box)."""
    try:
        dpi = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not an integer") from exc
    if not _PDF_DPI_MIN <= dpi <= _PDF_DPI_MAX:
        raise argparse.ArgumentTypeError(
            f"DPI must be between {_PDF_DPI_MIN} and {_PDF_DPI_MAX}, got {dpi}"
        )
    return dpi


def _parse_args(
    providers: list[str] | None = None,
    extra_defaults: dict | None = None,
    argv: Sequence[str] | None = None,
) -> argparse.Namespace:
    """Parse CLI arguments.

    *providers* is optional: when supplied it restricts the ``choices=`` on
    provider arguments for immediate validation; when ``None``, any string is
    accepted (useful in tests that do not care about provider names).
    """
    parser = argparse.ArgumentParser(
        prog="teachers_teammate",
        description=(
            "Teacher's Teammate — batch OCR pipeline that extracts text from PDFs/images "
            "and builds DOCX documents with grammar/spelling correction proposals."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Config file (top-level)
    parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help=(
            "Path to a TOML config file. Defaults to ocr.toml in the current directory "
            "when that file exists.  CLI arguments override file values."
        ),
    )

    # ── Input / Output ─────────────────────────────────────────────────────
    io_grp = parser.add_argument_group("input / output")
    io_grp.add_argument(
        "-i",
        "--input",
        default=None,
        metavar="FOLDER",
        help="Input folder containing PDF, JPG, JPEG, PNG, or TXT files.",
    )
    io_grp.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FOLDER",
        help="Output folder for generated .docx files (cache state is stored separately).",
    )
    io_grp.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Scan subfolders recursively.",
    )
    io_grp.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Do not scan subfolders recursively.",
    )
    io_grp.add_argument(
        "--debug",
        action="store_true",
        help="Keep preprocessed temp files after the run (useful for inspecting OCR inputs).",
    )
    io_grp.add_argument(
        "--no-debug",
        dest="debug",
        action="store_false",
        help="Do not keep preprocessing temp files after the run.",
    )

    # ── OCR Parameters ─────────────────────────────────────────────────────
    ocr_grp = parser.add_argument_group("OCR parameters")
    ocr_grp.add_argument(
        "-m",
        "--ocr-model",
        default=DEFAULTS["ocr_model"],
        metavar="MODEL",
        help="Ollama OCR vision model.",
    )
    ocr_grp.add_argument(
        "--ocr-engine",
        default=DEFAULTS["ocr_engine"],
        choices=["ollama", "tesseract", "paddleocr", "langchain"],
        metavar="ENGINE",
        help=(
            "OCR engine: ollama (vision model via Ollama), tesseract (local install), "
            "paddleocr (local PaddleOCR), or langchain (any vision-capable LangChain provider)."
        ),
    )

    ocr_provider_choices: list[str] | None = ["", *(providers)] if providers is not None else None
    ocr_grp.add_argument(
        "--ocr-provider",
        default="",
        choices=ocr_provider_choices,
        metavar="PROVIDER",
        dest="ocr_provider",
        help=(
            "LangChain provider for OCR (only used with --ocr-engine langchain): "
            f"{', '.join(providers or [])}."
        ),
    )
    ocr_grp.add_argument(
        "-l",
        "--language",
        default=DEFAULTS["language"],
        metavar="LANGUAGE",
        help="Language passed to the OCR vision model.",
    )
    ocr_grp.add_argument(
        "--preprocess-method",
        default=DEFAULTS["preprocess_method"],
        choices=["adaptive_threshold", "clahe", "grayscale", "none"],
        metavar="METHOD",
        help=(
            "Image preprocessing method: "
            "adaptive_threshold (grayscale → binary, robust to lighting variation), "
            "clahe (grayscale → CLAHE contrast enhancement, recommended with Tesseract), "
            "grayscale (grayscale only, no binarisation), or "
            "none (pass the original image directly to the OCR engine)."
        ),
    )
    ocr_grp.add_argument(
        "--pdf-render-dpi",
        type=_pdf_dpi_type,
        default=DEFAULTS["pdf_render_dpi"],
        metavar="DPI",
        dest="pdf_render_dpi",
        help=(
            "Resolution for rendering PDF pages to images before OCR. "
            "72 = screen quality, 150 = draft OCR, 300 = recommended (default), "
            "600 = high quality. Has no effect on image inputs (JPG, PNG)."
        ),
    )
    ocr_grp.add_argument(
        "--preprocess-dewarp",
        action="store_true",
        default=DEFAULTS["preprocess_dewarp"],
        dest="preprocess_dewarp",
        help="Correct perspective distortion before OCR (photographed pages, book scans).",
    )
    ocr_grp.add_argument(
        "--preprocess-deskew",
        action="store_true",
        default=DEFAULTS["preprocess_deskew"],
        dest="preprocess_deskew",
        help="Correct page tilt up to ±45° before OCR (minimum-area bounding-rectangle detection).",
    )
    ocr_grp.add_argument(
        "--preprocess-border-crop",
        action="store_true",
        default=DEFAULTS["preprocess_border_crop"],
        dest="preprocess_border_crop",
        help="Crop dark scanner borders before OCR to reduce image size and processing time.",
    )
    ocr_grp.add_argument(
        "--preprocess-denoise",
        action="store_true",
        default=DEFAULTS["preprocess_denoise"],
        dest="preprocess_denoise",
        help="Non-local means noise removal before OCR (scanner grain, pencil artifacts).",
    )
    ocr_grp.add_argument(
        "--preprocess-gamma",
        action="store_true",
        default=DEFAULTS["preprocess_gamma"],
        dest="preprocess_gamma",
        help="Gamma brightening (gamma=0.5) for dark or underexposed scans.",
    )
    ocr_grp.add_argument(
        "--ollama-url",
        default=DEFAULTS["ollama_url"],
        metavar="URL",
        help="Ollama base URL.",
    )
    ocr_grp.add_argument(
        "--ocr-timeout",
        type=int,
        default=DEFAULTS["ocr_timeout"],
        metavar="SECONDS",
        dest="ocr_timeout",
        help="Per-image OCR timeout in seconds (Ollama only).",
    )

    # ── Correction ─────────────────────────────────────────────────────────
    corr_grp = parser.add_argument_group("correction")
    corr_grp.add_argument(
        "--correction",
        dest="correction_enabled",
        action="store_true",
        default=DEFAULTS["correction_enabled"],
        help="Enable the LLM correction step (default).",
    )
    corr_grp.add_argument(
        "--no-correction",
        dest="correction_enabled",
        action="store_false",
        help="Skip the LLM correction step (DOCX is still created without column 2).",
    )
    corr_grp.add_argument(
        "--anonymization",
        dest="anonymization_enabled",
        action="store_true",
        default=DEFAULTS["anonymization_enabled"],
        help=(
            "Anonymize PII (person names, emails, phone numbers) in OCR text before "
            "sending to the correction LLM. Requires spacy and a language model: "
            "pip install 'teachers_teammate[privacy]' && python -m spacy download en_core_web_sm"
        ),
    )
    corr_grp.add_argument(
        "--no-anonymization",
        dest="anonymization_enabled",
        action="store_false",
        help="Disable PII anonymization before correction (default).",
    )
    corr_grp.add_argument(
        "--correction-provider",
        default=DEFAULTS["correction_provider"],
        choices=providers,
        metavar="PROVIDER",
        help=(
            f"LLM provider for correction: {', '.join(providers or [])}."
            if providers
            else "LLM provider for correction."
        ),
    )
    corr_grp.add_argument(
        "--correction-model",
        default=DEFAULTS["correction_model"],
        metavar="MODEL",
        help=(
            "Correction LLM model name. "
            "Auto-selected per provider when omitted: "
            "hf.co/unsloth/gpt-oss-20b-GGUF:UD-Q4_K_XL (ollama), gpt-4o-mini (openai), "
            "claude-3-haiku-20240307 (anthropic)."
        ),
    )
    corr_grp.add_argument(
        "--correction-prompt",
        default=DEFAULTS["correction_prompt"],
        metavar="PROMPT",
        help=(
            "System prompt for the correction LLM. "
            "Built-in presets: 'english' (default) or 'german'. "
            "Or supply a custom system prompt string."
        ),
    )

    # ── Evaluation ───────────────────────────────────────────────────────
    eval_grp = parser.add_argument_group("evaluation")
    eval_grp.add_argument(
        "--evaluation",
        dest="evaluation_enabled",
        action="store_true",
        default=DEFAULTS["evaluation_enabled"],
        help="Enable the evaluation step.",
    )
    eval_grp.add_argument(
        "--no-evaluation",
        dest="evaluation_enabled",
        action="store_false",
        help="Skip the evaluation step (default).",
    )
    eval_grp.add_argument(
        "--evaluate-provider",
        default=DEFAULTS["evaluate_provider"],
        choices=providers,
        metavar="PROVIDER",
        help=(
            f"LLM provider for evaluation: {', '.join(providers or [])}."
            if providers
            else "LLM provider for evaluation."
        ),
    )
    eval_grp.add_argument(
        "--evaluate-model",
        default=DEFAULTS["evaluate_model"],
        metavar="MODEL",
        help="Evaluation model name; provider default is used when omitted.",
    )
    eval_grp.add_argument(
        "--evaluate-prompt",
        default=DEFAULTS["evaluate_prompt"],
        metavar="PROMPT",
        help="System prompt used for evaluating corrected text.",
    )

    # ── Output ─────────────────────────────────────────────────────────────
    out_grp = parser.add_argument_group("output")
    out_grp.add_argument(
        "--preview-only",
        action="store_true",
        default=False,
        dest="preview_only",
        help=(
            "Preprocess all input files and save the resulting images to the "
            "temp cache folder, then exit. No OCR, correction, or DOCX generation "
            "is performed."
        ),
    )
    out_grp.add_argument(
        "--docx",
        dest="docx_enabled",
        action="store_true",
        default=DEFAULTS["docx_enabled"],
        help="Enable DOCX generation (disabled by default; only cache state is written otherwise).",
    )
    out_grp.add_argument(
        "--no-docx",
        dest="docx_enabled",
        action="store_false",
        help="Skip DOCX generation; only cache state is written.",
    )
    out_grp.add_argument(
        "--docx-format",
        default=DEFAULTS["docx_format"],
        choices=["table", "comments"],
        dest="docx_format",
        help=(
            "DOCX output layout: 'table' = three columns (image | OCR | correction); "
            "'comments' = two columns (image | OCR) with diff corrections as Word comments."
        ),
    )
    if extra_defaults:
        parser.set_defaults(**extra_defaults)
    return parser.parse_args(list(argv) if argv is not None else None)


def _validate_providers(args: argparse.Namespace, valid: frozenset[str]) -> list[str]:
    """Warn about unknown provider values in *args* and return list of warning messages."""
    warnings: list[str] = []
    for attr, label in (
        ("correction_provider", "correction_provider"),
        ("evaluate_provider", "evaluate_provider"),
        ("ocr_provider", "ocr_provider"),
    ):
        value = getattr(args, attr, "")
        if value and value not in valid:
            warnings.append(
                f"WARNING: Unknown {label} '{value}' — ignored. "
                f"Valid providers: {', '.join(sorted(valid))}."
            )
            setattr(args, attr, DEFAULTS.get(attr, ""))
    return warnings


def run_cli(
    argv: Sequence[str] | None = None,
    app_service: _CliAppService | None = None,
) -> int:
    """Parse args and execute the CLI flow, returning process exit code."""
    logging.basicConfig(
        level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s"
    )
    service = app_service or ProcessingApplicationService()
    providers = service.list_providers()

    # ── First pass: extract --config before the full parse ─────────────────
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    pre_args, _ = pre.parse_known_args(list(argv) if argv is not None else None)

    try:
        config_path = service.resolve_config_path(pre_args.config)
        file_defaults = load_config_file(config_path) if config_path else {}
    except ConfigFileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ConfigFileParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # ── Full parse: CLI values override file defaults ───────────────────────
    args = _parse_args(providers, file_defaults, argv)

    # Provider validation (moved out of load_config_file to keep config.py pure)
    valid_providers = frozenset(providers)
    for warning in _validate_providers(args, valid_providers):
        print(warning, file=sys.stderr)

    if args.input is None:
        print(
            "ERROR: No input folder specified. Use -i/--input or set 'input' in the config file.",
            file=sys.stderr,
        )
        return 1
    if args.output is None and args.docx_enabled:
        print(
            "ERROR: No output folder specified. "
            "Use -o/--output or set 'output' in the config file. "
            "(Output can be omitted only when --no-docx is set.)",
            file=sys.stderr,
        )
        return 1

    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"ERROR: Input folder does not exist: {input_dir}", file=sys.stderr)
        return 1

    config = Config(
        input_dir=input_dir,
        output_dir=Path(args.output) if args.output is not None else input_dir,
        recursive=args.recursive,
        debug=args.debug,
        ocr=OcrConfig(
            engine=args.ocr_engine,
            model=args.ocr_model,
            provider=args.ocr_provider,
            preprocess_method=args.preprocess_method,
            pdf_render_dpi=args.pdf_render_dpi,
            dewarp=args.preprocess_dewarp,
            deskew=args.preprocess_deskew,
            border_crop=args.preprocess_border_crop,
            denoise=args.preprocess_denoise,
            gamma=args.preprocess_gamma,
        ),
        language=args.language,
        ollama_url=args.ollama_url,
        ocr_timeout=args.ocr_timeout,
        correction_enabled=args.correction_enabled,
        anonymization_enabled=args.anonymization_enabled,
        correction_provider=args.correction_provider,
        correction_model=args.correction_model,
        correction_prompt=args.correction_prompt,
        evaluation_enabled=args.evaluation_enabled,
        evaluate_provider=args.evaluate_provider,
        evaluate_model=args.evaluate_model,
        evaluate_prompt=args.evaluate_prompt,
        docx_format=args.docx_format,
        docx_enabled=args.docx_enabled,
    )

    if args.preview_only:
        return service.run_preview_only(config)
    return service.run_selected(config, config_file=config_path)


def main(
    app_service: _CliAppService | None = None,
    *,
    argv: Sequence[str] | None = None,
    exit_fn: Callable[[int], None] = sys.exit,
) -> None:
    """CLI entry point that exits the process with the computed return code."""
    exit_fn(run_cli(argv=argv, app_service=app_service))


if __name__ == "__main__":
    main()
