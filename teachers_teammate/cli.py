"""Teacher's Teammate — command-line entry point.

Parses arguments, resolves configuration, and delegates to
:class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline`.

Usage::

    python -m teachers_teammate -i <input_folder> -o <output_folder> [options]

Supported input formats: PDF, JPG, JPEG, PNG, TXT
Output per file: <stem>.docx, <stem>.txt (--text), <stem>_anonymized.txt (--text-anonymized)
                 in the output folder, below the same sub-folder as the input has
                 OCR/correction/evaluation stage text is cached in JSON state (in temp cache dir)
Preprocessing images are cleaned up after each run (kept with --debug).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import logging
from pathlib import Path
import sys
import threading
from typing import Protocol

from ._model_defaults import default_model_for_task
from .application.service import ProcessingApplicationService
from .config import (
    CONTEXT_SETTINGS,
    DEFAULTS,
    PREPROCESS_METHODS,
    Config,
    OcrConfig,
    load_config_file,
)
from .domain.data_location import LOCAL_OCR_ENGINES, DataFlow, PrivacyLevel
from .domain.local_models import (
    HUGGING_FACE_HOST,
    PAGE_MODELS_SIZE_LABEL,
    READER_SIZE_LABEL,
)
from .domain.pdf_text_layer import PDF_TEXT_LAYER_MODES
from .domain.run_summary import RunSummary
from .exceptions import ConfigFileNotFoundError, ConfigFileParseError, ModelDownloadError


class _CliAppService(Protocol):
    def list_providers(self) -> list[str]: ...

    def resolve_config_path(self, explicit: str | None) -> Path | None: ...

    def run_selected(self, config: Config, /, *, config_file: Path | None = None) -> RunSummary: ...

    def describe_data_flow(self, config: Config, /) -> DataFlow: ...

    def ocr_refusal_for_run(self, config: Config, /) -> str: ...

    def output_refusal_for_run(self, config: Config, /) -> str: ...

    def memory_warning_for_run(self, config: Config, /) -> str: ...

    def run_preview_only(self, config: Config, /) -> int: ...

    def reader_refusal_for_run(self, config: Config, /) -> str: ...

    def page_models_notice_for_run(self, config: Config, /) -> str: ...

    def download_page_models(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None: ...

    def download_reader(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None: ...


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


def _add_correction_args(parser: argparse.ArgumentParser, providers: list[str] | None) -> None:
    """Add the correction argument group to *parser*."""
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
            f"{default_model_for_task('ollama', 'correction')} (ollama), "
            f"{default_model_for_task('openai', 'correction')} (openai), "
            f"{default_model_for_task('anthropic', 'correction')} (anthropic)."
        ),
    )
    corr_grp.add_argument(
        "--correction-prompt",
        default=DEFAULTS["correction_prompt"],
        metavar="PROMPT",
        help=(
            "System prompt for the correction LLM. "
            "Built-in presets: english (default), german, french, spanish, italian, "
            "portuguese, dutch, or a custom system prompt string. "
            "A prompt without {level} ignores --correction-level."
        ),
    )
    corr_grp.add_argument(
        "--correction-level",
        default=DEFAULTS["correction_level"],
        choices=["A1", "A2", "B1", "B2", "C1", "C2"],
        metavar="LEVEL",
        help="CEFR level the correction aims at (default: %(default)s).",
    )
    corr_grp.add_argument(
        "--explanation-language",
        default=DEFAULTS["explanation_language"],
        metavar="LANGUAGE",
        help=(
            "Language of the change explanations and the unclear table "
            "(preset name such as 'german'). Default: the language of the text."
        ),
    )
    corr_grp.add_argument(
        "--corrections-only",
        dest="correction_output",
        action="store_const",
        const="text",
        default=DEFAULTS["correction_output"],
        help=(
            "Skip the explanations and the unclear table (faster); "
            "only the corrected text is produced."
        ),
    )
    corr_grp.add_argument(
        "--explanations",
        dest="correction_output",
        action="store_const",
        const="review",
        help="Explain the changes and list unclear expressions (default; overrides a config "
        'file that sets correction_output = "text").',
    )
    corr_grp.add_argument(
        "--proofreading-context",
        default=DEFAULTS["proofreading_context"],
        choices=CONTEXT_SETTINGS,
        metavar="TOKENS",
        help=(
            "Context window for the content review when it runs on another Ollama model than "
            "proofreading (proofreading uses 8192): 'auto' grows it with the size of the "
            "graphics card (8192, 16384 or 32768, whichever still fits), a fixed value pins it "
            "— useful for a remote server whose card the app cannot see."
        ),
    )


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
    io_grp.add_argument(
        "--keep-awake",
        dest="keep_awake",
        action="store_true",
        default=DEFAULTS["keep_awake"],
        help="Keep the computer from going to sleep during the run (default).",
    )
    io_grp.add_argument(
        "--allow-sleep",
        dest="keep_awake",
        action="store_false",
        help="Let the computer go to sleep during the run (by default it is kept awake).",
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
        choices=list(LOCAL_OCR_ENGINES),
        metavar="ENGINE",
        help=(
            "OCR engine: ollama (AI vision model through Ollama, default) or ppocr (the "
            "built-in reader: no server, best on printed pages; download it first with "
            "--download-reader). Online services are not offered: reading a page needs the "
            "whole page, so personal information cannot be hidden first."
        ),
    )
    ocr_grp.add_argument(
        "--download-reader",
        action="store_true",
        default=False,
        help=(
            f"Download the built-in reader's model files once ({READER_SIZE_LABEL}, from "
            f"{HUGGING_FACE_HOST}) and exit."
        ),
    )
    ocr_grp.add_argument(
        "--download-page-models",
        action="store_true",
        default=False,
        help=(
            "Download the page models of the automatic image preparation once "
            f"({PAGE_MODELS_SIZE_LABEL}, from {HUGGING_FACE_HOST}) and exit. They turn sideways "
            "pages and flatten curved ones; without them those two steps are skipped."
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
        choices=list(PREPROCESS_METHODS),
        metavar="METHOD",
        help=(
            "Image preparation: auto (default; looks at each page and straightens, turns and "
            "evens out photographed pages, clean scans pass unchanged) or none (the original "
            "page, with the photo's rotation tag applied)."
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
        "--recognition",
        dest="recognition_enabled",
        action="store_true",
        default=DEFAULTS["recognition_enabled"],
        help="Read scans (images, PDFs without a text layer) with the OCR engine (default).",
    )
    ocr_grp.add_argument(
        "--no-recognition",
        dest="recognition_enabled",
        action="store_false",
        help=(
            "Do not read scans: use only text that already exists (TXT files, PDFs with a "
            "text layer) and skip images and scanned PDFs. No OCR engine or model is needed."
        ),
    )
    ocr_grp.add_argument(
        "--pdf-text-layer",
        choices=PDF_TEXT_LAYER_MODES,
        default=DEFAULTS["pdf_text_layer"],
        dest="pdf_text_layer",
        help=(
            "Use a PDF's embedded text instead of OCR. "
            "auto = only when every page is clean, text-only (no scans, no handwriting, "
            "annotations or form fields) (default); always = whenever every page has any text; "
            "never = always OCR. Has no effect on image or TXT inputs."
        ),
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
    _add_correction_args(parser, providers)

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
        "--ignore-memory-check",
        action="store_true",
        default=False,
        dest="ignore_memory_check",
        help=(
            "Run even when the models do not fit in the memory that is free now. "
            "Without this flag such a run is refused, because the computer would have to "
            "use its disk as memory and become extremely slow."
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
        "--text",
        dest="text_enabled",
        action="store_true",
        default=DEFAULTS["text_enabled"],
        help="Also save the extracted text of each file as <name>.txt.",
    )
    out_grp.add_argument(
        "--text-anonymized",
        dest="text_anonymized_enabled",
        action="store_true",
        default=DEFAULTS["text_anonymized_enabled"],
        help=(
            "Also save the extracted text with personal data replaced by placeholders "
            "as <name>_anonymized.txt (needs spaCy)."
        ),
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
    ):
        value = getattr(args, attr, "")
        if value and value not in valid:
            warnings.append(
                f"WARNING: Unknown {label} '{value}' — ignored. "
                f"Valid providers: {', '.join(sorted(valid))}."
            )
            setattr(args, attr, DEFAULTS.get(attr, ""))
    return warnings


#: What each privacy level means, in the console's own words.
_LEVEL_TEXT = {
    PrivacyLevel.LOCAL: "Everything stays on this computer.",
    PrivacyLevel.OWN_SERVER: "Data goes to another computer you control.",
    PrivacyLevel.ANON_EU: "Anonymized text is sent to a service in the EU.",
    PrivacyLevel.ANON_NON_EU: "Anonymized text is sent to a service outside the EU.",
    PrivacyLevel.NAMES_EU: "Text with names is sent to a service in the EU.",
    PrivacyLevel.NAMES_NON_EU: "Text with names is sent to a service outside the EU.",
}


def _print_data_flow(flow: DataFlow) -> None:
    """Tell the user where their documents are processed before anything starts."""
    print("Where your documents are processed:", file=sys.stderr)
    for stage in flow.stages:
        marker = "!" if stage.leaves_computer else "-"
        print(f"  {marker} {stage.title}: {stage.badge}", file=sys.stderr)
    print(f"  => {_LEVEL_TEXT[flow.level]}", file=sys.stderr)
    if flow.leaves_computer:
        print(
            "  ! Data leaves this computer. Check that this is allowed for your documents.",
            file=sys.stderr,
        )


def _download_models(
    download: Callable[[Callable[[int, int], None]], None], name: str, ready: str
) -> int:
    """Run *download* with a progress line on stderr; return the exit code.

    *name* is what the progress line says is downloading, *ready* the sentence on success.
    """

    def progress(done: int, total: int) -> None:
        if total:
            print(
                f"\rDownloading {name}: {done // 1_000_000} of {total // 1_000_000} MB",
                end="",
                file=sys.stderr,
                flush=True,
            )

    try:
        download(progress)
    except ModelDownloadError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\n{ready}", file=sys.stderr)
    return 0


def _download_reader(service: _CliAppService) -> int:
    """Download the built-in reader with a progress line on stderr; return the exit code."""
    return _download_models(
        service.download_reader, "the built-in reader", "The built-in reader is ready."
    )


def _download_page_models(service: _CliAppService) -> int:
    """Download the page models with a progress line on stderr; return the exit code."""
    return _download_models(
        service.download_page_models, "the page models", "The page models are ready."
    )


def _download_only(args: argparse.Namespace, service: _CliAppService) -> int | None:
    """Run the download the flags ask for and return its exit code; ``None`` if none was asked."""
    if args.download_reader:
        return _download_reader(service)
    if args.download_page_models:
        return _download_page_models(service)
    return None


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
    except (ConfigFileNotFoundError, ConfigFileParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # ── Full parse: CLI values override file defaults ───────────────────────
    args = _parse_args(providers, file_defaults, argv)

    download_exit = _download_only(args, service)
    if download_exit is not None:
        return download_exit

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
    if args.output is None and (
        args.docx_enabled or args.text_enabled or args.text_anonymized_enabled
    ):
        print(
            "ERROR: No output folder specified. "
            "Use -o/--output or set 'output' in the config file. "
            "(Output can be omitted only when no output files are written.)",
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
        keep_awake=args.keep_awake,
        ocr=OcrConfig(
            engine=args.ocr_engine,
            model=args.ocr_model,
            preprocess_method=args.preprocess_method,
            pdf_render_dpi=args.pdf_render_dpi,
            pdf_text_layer=args.pdf_text_layer,
        ),
        language=args.language,
        ollama_url=args.ollama_url,
        hardware_profile=getattr(args, "hardware_profile", DEFAULTS["hardware_profile"]),
        proofreading_context=getattr(
            args, "proofreading_context", DEFAULTS["proofreading_context"]
        ),
        ocr_timeout=args.ocr_timeout,
        recognition_enabled=args.recognition_enabled,
        correction_enabled=args.correction_enabled,
        anonymization_enabled=args.anonymization_enabled,
        # No command-line flags for these: they come from the config file, like in the GUI, so a
        # run from either gives the same placeholders (and the same cache key).
        anonymizer_primary_model=getattr(
            args, "anonymizer_primary_model", DEFAULTS["anonymizer_primary_model"]
        ),
        anonymizer_secondary_model=getattr(
            args, "anonymizer_secondary_model", DEFAULTS["anonymizer_secondary_model"]
        ),
        anonymizer_patterns=getattr(args, "anonymizer_patterns", None),
        correction_provider=args.correction_provider,
        correction_model=args.correction_model,
        correction_prompt=args.correction_prompt,
        correction_level=args.correction_level,
        explanation_language=args.explanation_language,
        correction_output=args.correction_output,
        answer_format_review=getattr(args, "answer_format_review", ""),
        answer_format_text=getattr(args, "answer_format_text", ""),
        evaluation_enabled=args.evaluation_enabled,
        evaluate_provider=args.evaluate_provider,
        evaluate_model=args.evaluate_model,
        evaluate_prompt=args.evaluate_prompt,
        docx_format=args.docx_format,
        docx_enabled=args.docx_enabled,
        text_enabled=args.text_enabled,
        text_anonymized_enabled=args.text_anonymized_enabled,
    )

    if args.preview_only:
        return service.run_preview_only(config)
    refusal = (
        service.ocr_refusal_for_run(config)
        or service.reader_refusal_for_run(config)
        or service.output_refusal_for_run(config)
    )
    if refusal:
        print(f"ERROR: {refusal}", file=sys.stderr)
        return 1
    warning = service.memory_warning_for_run(config)
    if warning:
        if not args.ignore_memory_check:
            print(f"ERROR: {warning}", file=sys.stderr)
            print("Use --ignore-memory-check to run anyway.", file=sys.stderr)
            return 1
        print(f"WARNING: {warning}", file=sys.stderr)
    notice = service.page_models_notice_for_run(config)
    if notice:
        print(notice, file=sys.stderr)
    _print_data_flow(service.describe_data_flow(config))
    return service.run_selected(config, config_file=config_path).exit_code


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
