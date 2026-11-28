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
from pathlib import Path
import sys

from . import __version__
from ._model_defaults import default_model_for_task
from .application.composition import (
    AppServices,
    console_wanted,
    inject_addon_packages_dir,
    start_logging,
)
from .application.run_checks import RunPreflight
from .config import Config, load_config_file
from .domain.data_location import LOCAL_OCR_ENGINES, PROVIDER_OLLAMA, DataFlow
from .domain.output_files import folder_problem
from .domain.pdf_text_layer import PDF_TEXT_LAYER_MODES
from .domain.stages import Stage
from .exceptions import ConfigFileNotFoundError, ConfigFileParseError, ModelDownloadError
from .settings_schema import (
    CONTEXT_SETTINGS,
    DEFAULTS,
    OCR_TIMEOUT_RANGE,
    PDF_RENDER_DPI_RANGE,
    PREPROCESS_METHODS,
    LoadedSettings,
    LogDetail,
)
from .texts import addons as addon_text
from .texts import check as check_text
from .texts import cli as cli_text
from .texts import dialogs as dlg
from .texts import run as run_text
from .texts.issues import issue_text
from .texts.run import BATTERY_WARNING


def _bounded_int(
    bounds: tuple[int, int], out_of_range: Callable[[int, int, int], str]
) -> Callable[[str], int]:
    """Return an argparse type that takes a whole number inside *bounds*, as the settings do."""

    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(cli_text.not_an_integer(value)) from exc
        low, high = bounds
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(out_of_range(number, low, high))
        return number

    return parse


_pdf_dpi_type = _bounded_int(PDF_RENDER_DPI_RANGE, cli_text.dpi_out_of_range)
_ocr_timeout_type = _bounded_int(OCR_TIMEOUT_RANGE, cli_text.timeout_out_of_range)


def _add_correction_args(parser: argparse.ArgumentParser, providers: list[str] | None) -> None:
    """Add the correction argument group to *parser*."""
    corr_grp = parser.add_argument_group("correction")
    _add_proofreading_switches(corr_grp)
    _add_proofreading_details(corr_grp, providers)


def _add_proofreading_switches(corr_grp: argparse._ArgumentGroup) -> None:
    """Add what the proofreading does at all: corrections, names, task text."""
    corr_grp.add_argument(
        "--correction",
        dest="correction_enabled",
        action="store_true",
        default=DEFAULTS["correction_enabled"],
        help=cli_text.HELP_CORRECTION,
    )
    corr_grp.add_argument(
        "--no-correction",
        dest="correction_enabled",
        action="store_false",
        help=cli_text.HELP_NO_CORRECTION,
    )
    corr_grp.add_argument(
        "--anonymization",
        dest="anonymization_enabled",
        action="store_true",
        default=DEFAULTS["anonymization_enabled"],
        help=cli_text.HELP_ANONYMIZATION,
    )
    corr_grp.add_argument(
        "--no-anonymization",
        dest="anonymization_enabled",
        action="store_false",
        help=cli_text.HELP_NO_ANONYMIZATION,
    )
    corr_grp.add_argument(
        "--proofread-task-text",
        dest="skip_task_text",
        action="store_false",
        default=DEFAULTS["skip_task_text"],
        help=cli_text.HELP_PROOFREAD_TASK_TEXT,
    )


def _add_proofreading_details(
    corr_grp: argparse._ArgumentGroup, providers: list[str] | None
) -> None:
    """Add how the proofreading works: provider, model, level, output, context."""
    corr_grp.add_argument(
        "--correction-provider",
        default=DEFAULTS["correction_provider"],
        choices=providers,
        metavar="PROVIDER",
        help=cli_text.provider_help(Stage.CORRECTION, providers or []),
    )
    corr_grp.add_argument(
        "--correction-model",
        default=DEFAULTS["correction_model"],
        metavar="MODEL",
        help=(
            cli_text.correction_model_help(
                [
                    (PROVIDER_OLLAMA, default_model_for_task(PROVIDER_OLLAMA, Stage.CORRECTION)),
                    ("openai", default_model_for_task("openai", Stage.CORRECTION)),
                    ("anthropic", default_model_for_task("anthropic", Stage.CORRECTION)),
                ]
            )
        ),
    )
    corr_grp.add_argument(
        "--correction-notes",
        default=DEFAULTS["correction_notes"],
        metavar="TEXT",
        help=cli_text.HELP_CORRECTION_NOTES,
    )
    corr_grp.add_argument(
        "--correction-level",
        default=DEFAULTS["correction_level"],
        choices=["A1", "A2", "B1", "B2", "C1", "C2"],
        metavar="LEVEL",
        help=cli_text.HELP_CORRECTION_LEVEL,
    )
    corr_grp.add_argument(
        "--explanation-language",
        default=DEFAULTS["explanation_language"],
        metavar="LANGUAGE",
        help=cli_text.HELP_EXPLANATION_LANGUAGE,
    )
    corr_grp.add_argument(
        "--corrections-only",
        dest="correction_output",
        action="store_const",
        const="text",
        default=DEFAULTS["correction_output"],
        help=cli_text.HELP_CORRECTIONS_ONLY,
    )
    corr_grp.add_argument(
        "--explanations",
        dest="correction_output",
        action="store_const",
        const="review",
        help=cli_text.HELP_EXPLANATIONS,
    )
    corr_grp.add_argument(
        "--proofreading-context",
        default=DEFAULTS["proofreading_context"],
        choices=CONTEXT_SETTINGS,
        metavar="TOKENS",
        help=cli_text.HELP_PROOFREADING_CONTEXT,
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
    parser = _make_parser(providers)
    if extra_defaults:
        parser.set_defaults(**extra_defaults)
    return parser.parse_args(list(argv) if argv is not None else None)


class _VersionNotice(argparse.Action):
    """Print the version notice as written, then exit 0.

    argparse's own ``version`` action reflows the text into one paragraph, which joins the
    version and the copyright line.
    """

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        _namespace: argparse.Namespace,
        _values: str | Sequence[str] | None,
        _option_string: str | None = None,
    ) -> None:
        print(cli_text.version_notice(__version__))
        parser.exit()


def _add_version_arg(parser: argparse.ArgumentParser) -> None:
    """Add ``--version``: the notice with the copyright and the license, then exit 0."""
    parser.add_argument("--version", action=_VersionNotice, nargs=0, help=cli_text.HELP_VERSION)


def _add_log_args(parser: argparse.ArgumentParser) -> None:
    """Add what the log file holds, and whether the console gets the same lines."""
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=None,
        choices=[member.value for member in LogDetail],
        metavar="LEVEL",
        help=cli_text.HELP_LOG_LEVEL,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help=cli_text.HELP_VERBOSE)


def exit_on_version(argv: Sequence[str] | None) -> None:
    """Print the version notice and exit 0 when *argv* asks for it.

    ``main()`` calls it before the services are built, the log starts or the config file is read,
    so a broken setup does not hide the version.
    """
    early = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    _add_version_arg(early)
    early.parse_known_args(list(argv) if argv is not None else None)


def _make_parser(providers: list[str] | None) -> argparse.ArgumentParser:
    """Return the command line, with one function per group of arguments."""
    parser = argparse.ArgumentParser(
        prog="teachers_teammate",
        description=cli_text.DESCRIPTION,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="FILE",
        help=cli_text.HELP_CONFIG,
    )
    _add_version_arg(parser)
    _add_log_args(parser)
    _add_io_args(parser)
    _add_ocr_args(parser)
    _add_scan_marks_args(parser)
    _add_correction_args(parser, providers)
    _add_evaluation_args(parser, providers)
    _add_output_args(parser)
    return parser


def _add_io_args(parser: argparse.ArgumentParser) -> None:
    """Add the folder, progress and power arguments to *parser*."""
    io_grp = parser.add_argument_group("input / output")
    io_grp.add_argument(
        "-i",
        "--input",
        default=None,
        metavar="FOLDER",
        help=cli_text.HELP_INPUT,
    )
    io_grp.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FOLDER",
        help=cli_text.HELP_OUTPUT,
    )
    io_grp.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help=cli_text.HELP_RECURSIVE,
    )
    io_grp.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help=cli_text.HELP_NO_RECURSIVE,
    )
    io_grp.add_argument(
        "--debug",
        action="store_true",
        help=cli_text.HELP_DEBUG,
    )
    io_grp.add_argument(
        "--no-debug",
        dest="debug",
        action="store_false",
        help=cli_text.HELP_NO_DEBUG,
    )
    io_grp.add_argument(
        "--keep-awake",
        dest="keep_awake",
        action="store_true",
        default=DEFAULTS["keep_awake"],
        help=cli_text.HELP_KEEP_AWAKE,
    )
    io_grp.add_argument(
        "--allow-sleep",
        dest="keep_awake",
        action="store_false",
        help=cli_text.HELP_ALLOW_SLEEP,
    )


def _add_scan_marks_args(parser: argparse.ArgumentParser) -> None:
    """Add the switch of the scan's marks: what a run keeps for the window's Check tab."""
    group = parser.add_argument_group("scan marks")
    group.add_argument(
        "--scan-marks",
        dest="scan_marks",
        action="store_true",
        default=DEFAULTS["scan_marks"],
        help=cli_text.HELP_SCAN_MARKS,
    )
    group.add_argument(
        "--no-scan-marks",
        dest="scan_marks",
        action="store_false",
        help=cli_text.HELP_NO_SCAN_MARKS,
    )


def _add_ocr_args(parser: argparse.ArgumentParser) -> None:
    """Add the text recognition arguments to *parser*."""
    ocr_grp = parser.add_argument_group("OCR parameters")
    ocr_grp.add_argument(
        "-m",
        "--ocr-model",
        default=DEFAULTS["ocr_model"],
        metavar="MODEL",
        help=cli_text.HELP_OCR_MODEL,
    )
    ocr_grp.add_argument(
        "--ocr-engine",
        default=DEFAULTS["ocr_engine"],
        choices=list(LOCAL_OCR_ENGINES),
        metavar="ENGINE",
        help=cli_text.HELP_OCR_ENGINE,
    )
    ocr_grp.add_argument(
        "--download-reader",
        action="store_true",
        default=False,
        help=cli_text.HELP_DOWNLOAD_READER,
    )
    ocr_grp.add_argument(
        "--download-page-models",
        action="store_true",
        default=False,
        help=cli_text.HELP_DOWNLOAD_PAGE_MODELS,
    )
    ocr_grp.add_argument(
        "-l",
        "--language",
        default=DEFAULTS["language"],
        metavar="LANGUAGE",
        help=cli_text.HELP_LANGUAGE,
    )
    ocr_grp.add_argument(
        "--preprocess-method",
        default=DEFAULTS["preprocess_method"],
        choices=list(PREPROCESS_METHODS),
        metavar="METHOD",
        help=cli_text.HELP_PREPROCESS_METHOD,
    )
    ocr_grp.add_argument(
        "--pdf-render-dpi",
        type=_pdf_dpi_type,
        default=DEFAULTS["pdf_render_dpi"],
        metavar="DPI",
        dest="pdf_render_dpi",
        help=cli_text.HELP_PDF_RENDER_DPI,
    )
    ocr_grp.add_argument(
        "--recognition",
        dest="recognition_enabled",
        action="store_true",
        default=DEFAULTS["recognition_enabled"],
        help=cli_text.HELP_RECOGNITION,
    )
    ocr_grp.add_argument(
        "--no-recognition",
        dest="recognition_enabled",
        action="store_false",
        help=cli_text.HELP_NO_RECOGNITION,
    )
    ocr_grp.add_argument(
        "--pdf-text-layer",
        choices=PDF_TEXT_LAYER_MODES,
        default=DEFAULTS["pdf_text_layer"],
        dest="pdf_text_layer",
        help=cli_text.HELP_PDF_TEXT_LAYER,
    )
    ocr_grp.add_argument(
        "--ollama-url",
        default=DEFAULTS["ollama_url"],
        metavar="URL",
        help=cli_text.HELP_OLLAMA_URL,
    )
    ocr_grp.add_argument(
        "--ocr-timeout",
        type=_ocr_timeout_type,
        default=DEFAULTS["ocr_timeout"],
        metavar="SECONDS",
        dest="ocr_timeout",
        help=cli_text.HELP_OCR_TIMEOUT,
    )


def _add_evaluation_args(parser: argparse.ArgumentParser, providers: list[str] | None) -> None:
    """Add the content review arguments to *parser*."""
    eval_grp = parser.add_argument_group("evaluation")
    eval_grp.add_argument(
        "--evaluation",
        dest="evaluation_enabled",
        action="store_true",
        default=DEFAULTS["evaluation_enabled"],
        help=cli_text.HELP_EVALUATION,
    )
    eval_grp.add_argument(
        "--no-evaluation",
        dest="evaluation_enabled",
        action="store_false",
        help=cli_text.HELP_NO_EVALUATION,
    )
    eval_grp.add_argument(
        "--evaluate-provider",
        default=DEFAULTS["evaluate_provider"],
        choices=providers,
        metavar="PROVIDER",
        help=cli_text.provider_help(Stage.EVALUATION, providers or []),
    )
    eval_grp.add_argument(
        "--evaluate-model",
        default=DEFAULTS["evaluate_model"],
        metavar="MODEL",
        help=cli_text.HELP_EVALUATE_MODEL,
    )
    eval_grp.add_argument(
        "--evaluate-prompt",
        default=DEFAULTS["evaluate_prompt"],
        metavar="PROMPT",
        help=cli_text.HELP_EVALUATE_PROMPT,
    )


def _add_output_args(parser: argparse.ArgumentParser) -> None:
    """Add the arguments about what a run writes to *parser*."""
    out_grp = parser.add_argument_group("output")
    out_grp.add_argument(
        "--preview-only",
        action="store_true",
        default=False,
        dest="preview_only",
        help=cli_text.HELP_PREVIEW_ONLY,
    )
    out_grp.add_argument(
        "--ignore-memory-check",
        action="store_true",
        default=False,
        dest="ignore_memory_check",
        help=cli_text.HELP_IGNORE_MEMORY_CHECK,
    )
    out_grp.add_argument(
        "--docx",
        dest="docx_enabled",
        action="store_true",
        default=DEFAULTS["docx_enabled"],
        help=cli_text.HELP_DOCX,
    )
    out_grp.add_argument(
        "--no-docx",
        dest="docx_enabled",
        action="store_false",
        help=cli_text.HELP_NO_DOCX,
    )
    text_grp = out_grp.add_mutually_exclusive_group()
    text_grp.add_argument(
        "--text",
        dest="text_enabled",
        action="store_true",
        default=DEFAULTS["text_enabled"],
        help=cli_text.HELP_TEXT,
    )
    text_grp.add_argument(
        "--text-anonymized",
        dest="text_anonymized_enabled",
        action="store_true",
        default=DEFAULTS["text_anonymized_enabled"],
        help=cli_text.HELP_TEXT_ANONYMIZED,
    )
    out_grp.add_argument(
        "--docx-format",
        default=DEFAULTS["docx_format"],
        choices=["table", "comments"],
        dest="docx_format",
        help=cli_text.HELP_DOCX_FORMAT,
    )


def _validate_providers(args: argparse.Namespace, valid: frozenset[str]) -> list[str]:
    """Warn about unknown provider values in *args* and return list of warning messages."""
    warnings: list[str] = []
    for attr, label in (
        ("correction_provider", "correction_provider"),
        ("evaluate_provider", "evaluate_provider"),
    ):
        value = getattr(args, attr, "")
        if value and value not in valid:
            warnings.append(dlg.cli_warning(cli_text.unknown_provider(label, value, valid)))
            setattr(args, attr, DEFAULTS.get(attr, ""))
    return warnings


def _print_data_flow(flow: DataFlow) -> None:
    """Tell the user where their documents are processed before anything starts."""
    for line in cli_text.data_flow_lines(flow):
        print(line, file=sys.stderr)


def _download_models(
    download: Callable[[Callable[[int, int], None]], None], name: str, ready: str
) -> int:
    """Run *download* with a progress line on stderr; return the exit code.

    *name* is what the progress line says is downloading, *ready* the sentence on success.
    """

    def progress(done: int, total: int) -> None:
        if total:
            print(
                cli_text.downloading(name, done, total),
                end="",
                file=sys.stderr,
                flush=True,
            )

    try:
        download(progress)
    except ModelDownloadError as exc:
        print(file=sys.stderr)
        print(dlg.cli_error(str(exc)), file=sys.stderr)
        return 1
    print(file=sys.stderr)
    print(ready, file=sys.stderr)
    return 0


def _download_reader(service: AppServices) -> int:
    """Download the built-in reader with a progress line on stderr; return the exit code."""
    return _download_models(service.reader.download_reader, cli_text.READER, cli_text.READER_READY)


def _download_page_models(service: AppServices) -> int:
    """Download the page models with a progress line on stderr; return the exit code."""
    return _download_models(
        service.reader.download_page_models, cli_text.PAGE_MODELS, cli_text.PAGE_MODELS_READY
    )


def _download_only(args: argparse.Namespace, service: AppServices) -> int | None:
    """Run the download the flags ask for and return its exit code; ``None`` if none was asked."""
    if args.download_reader:
        return _download_reader(service)
    if args.download_page_models:
        return _download_page_models(service)
    return None


def _preflight_exit(
    preflight: RunPreflight, service: AppServices, *, ignore_memory_check: bool
) -> int | None:
    """Print what the preflight found on stderr; return the exit code, or ``None`` to go on.

    A blocker stops the run and names the way to fix it; a memory shortfall stops it unless
    *ignore_memory_check* says the teacher decided otherwise; the page models, the skipped
    scans and a wait for Ollama are notices before a run that goes ahead.
    """
    blocker = preflight.blocker
    if blocker is not None:
        _, issue = blocker
        env_key = service.providers.env_key(issue.provider) if issue.provider else ""
        print(dlg.cli_error(issue_text(issue)), file=sys.stderr)
        hint = cli_text.cli_issue_hint(issue, env_key=env_key)
        if hint:
            print(hint, file=sys.stderr)
        return 1
    if preflight.memory_shortfall is not None:
        shortfall = dlg.memory_shortfall_text(preflight.memory_shortfall)
        if not ignore_memory_check:
            print(dlg.cli_error(shortfall), file=sys.stderr)
            print(cli_text.IGNORE_MEMORY_CHECK_HINT, file=sys.stderr)
            return 1
        print(dlg.cli_warning(shortfall), file=sys.stderr)
    if preflight.page_models_skipped:
        print(addon_text.PAGE_MODELS_SKIPPED_NOTICE, file=sys.stderr)
    wait = check_text.wait_detail_text(preflight.ollama_wait)
    if wait:
        print(wait, file=sys.stderr)
    if preflight.skipped:
        print(dlg.cli_warning(run_text.scans_skipped_note(len(preflight.skipped))), file=sys.stderr)
    return None


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    app_service: AppServices,
) -> int:
    """Parse args and execute the CLI flow, returning process exit code."""
    # Where a warning or an unexpected error goes: always the log file, and the console with -v.
    start_logging(console=console_wanted(argv), argv=argv)
    service = app_service
    providers = service.providers.list_providers()

    # ── First pass: extract --config before the full parse ─────────────────
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    pre_args, _ = pre.parse_known_args(list(argv) if argv is not None else None)

    try:
        config_path = service.setup.resolve_config_path(pre_args.config)
        loaded = load_config_file(config_path) if config_path else LoadedSettings({})
    except (ConfigFileNotFoundError, ConfigFileParseError) as exc:
        print(dlg.cli_error(str(exc)), file=sys.stderr)
        return 1
    for notice in loaded.notices:
        print(dlg.cli_warning(dlg.setting_notice(notice).body), file=sys.stderr)

    # ── Full parse: CLI values override file defaults ───────────────────────
    args = _parse_args(providers, loaded.values, argv)

    download_exit = _download_only(args, service)
    if download_exit is not None:
        return download_exit

    # Provider validation (moved out of load_config_file to keep config.py pure)
    valid_providers = frozenset(providers)
    for warning in _validate_providers(args, valid_providers):
        print(warning, file=sys.stderr)

    input_text = args.input or ""
    output_text = args.output or ""
    problem = folder_problem(
        input_text,
        output_text,
        input_exists=Path(input_text).is_dir() if input_text else False,
        writes_output_files=bool(
            args.docx_enabled or args.text_enabled or args.text_anonymized_enabled
        ),
    )
    if problem is not None:
        print(
            dlg.cli_error(dlg.cli_folder_problem_text(problem, input_text=input_text)),
            file=sys.stderr,
        )
        return 1

    input_dir = Path(input_text)
    config = Config.from_settings(
        vars(args),
        input_dir=input_dir,
        output_dir=Path(output_text) if output_text else input_dir,
        recursive=args.recursive,
    )

    files = service.run.collect_input_files(config)
    if args.preview_only:
        return service.run.run_preview_only(config, files).exit_code
    # The one answer to "can this run start?", the same one the window asks.  A run over no
    # file says so itself, without asking a server or a key first.
    if files:
        stopped = _preflight_exit(
            service.run_checks.check_run_preflight(config, files=files, fresh=True),
            service,
            ignore_memory_check=args.ignore_memory_check,
        )
        if stopped is not None:
            return stopped
    _print_data_flow(service.data_flow.describe_data_flow(config))
    return service.run.run_selected(
        config, files, config_file=config_path, battery_warning=BATTERY_WARNING
    ).exit_code


def main(
    app_service: AppServices | None = None,
    *,
    argv: Sequence[str] | None = None,
    exit_fn: Callable[[int], None] = sys.exit,
) -> None:
    """CLI entry point that exits the process with the computed return code."""
    # A wheel install starts here, not in __main__ or frozen_entry.py: packages added under
    # Settings → Add-ons must be importable after a restart in every way of starting the app.
    exit_on_version(argv)
    inject_addon_packages_dir()
    if app_service is None:
        from .cli_composition import build_cli_app_service  # noqa: PLC0415  # imports this module

        app_service = build_cli_app_service()
    exit_fn(run_cli(argv=argv, app_service=app_service))


if __name__ == "__main__":
    main()
