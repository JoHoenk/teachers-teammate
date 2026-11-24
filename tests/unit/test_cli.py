"""Unit tests for teachers_teammate.cli — argument parsing and main() entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from ..gui._service_stubs import services_of
from teachers_teammate.application.composition import build_app_services
from teachers_teammate.application.run import RunPreflight
from teachers_teammate.domain.data_location import DataFlow
from teachers_teammate.domain.output_files import FolderProblem
from teachers_teammate.domain.requirements import IssueKind, MemoryShortfall, RequirementIssue
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.domain.stages import Stage


class _AppServiceStub:
    """Application-service stub with harmless defaults for every method the CLI calls.

    The CLI calls a growing set of service methods, so each test overrides only the ones it
    cares about: a new method then breaks this class once instead of every fake in this file.
    """

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def resolve_config_path(self, explicit: str | None) -> Path | None:
        return None

    def describe_data_flow(self, config) -> DataFlow:
        return DataFlow(())

    def check_run_preflight(self, config, **kwargs) -> RunPreflight:
        return RunPreflight()

    def env_key(self, provider: str) -> str:
        return ""

    def download_reader(self, on_progress=None, cancel=None) -> None:
        raise AssertionError("nothing may be downloaded")

    def download_page_models(self, on_progress=None, cancel=None) -> None:
        raise AssertionError("nothing may be downloaded")

    def collect_input_files(self, config) -> list[Path]:
        return [Path("page.png")]

    def run_selected(self, config, files, /, **kwargs) -> RunSummary:
        raise AssertionError("the run must not start")

    def run_preview_only(self, config, files, /) -> RunSummary:
        raise AssertionError("preview-only path should not be used")


# ── _parse_args ────────────────────────────────────────────────────────────


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_defaults() -> None:
    """
    Given  no CLI arguments
    When   _parse_args() is called
    Then   all defaults are set as documented
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=[])

    assert args.input is None
    assert args.output is None
    assert args.recursive is False
    assert args.debug is False
    assert args.ocr_engine == "ollama"
    assert args.language == "English"
    assert args.correction_enabled is True
    assert args.docx_enabled is False
    assert args.docx_format == "table"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_explicit_input_output() -> None:
    """
    Given  -i /tmp/in -o /tmp/out
    When   _parse_args() is called
    Then   input and output are set correctly
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["-i", "/tmp/in", "-o", "/tmp/out"])

    assert args.input == "/tmp/in"
    assert args.output == "/tmp/out"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_no_recognition_switches_text_recognition_off() -> None:
    """
    Given  no flag, then --no-recognition, then --no-recognition --recognition
    When   _parse_args() is called
    Then   recognition is on by default, off with --no-recognition, and the last flag wins
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    base = ["-i", "/in", "-o", "/out"]

    assert _parse_args(argv=base).recognition_enabled is True
    assert _parse_args(argv=[*base, "--no-recognition"]).recognition_enabled is False
    assert (
        _parse_args(argv=[*base, "--no-recognition", "--recognition"]).recognition_enabled is True
    )


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_flags() -> None:
    """
    Given  --recursive --debug --no-correction --no-docx
    When   _parse_args() is called
    Then   all boolean flags are True
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(
        argv=["-i", "/in", "-o", "/out", "--recursive", "--debug", "--no-correction", "--no-docx"]
    )

    assert args.recursive is True
    assert args.debug is True
    assert args.correction_enabled is False
    assert args.docx_enabled is False


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_ocr_engine_ppocr() -> None:
    """
    Given  --ocr-engine ppocr
    When   _parse_args() is called
    Then   ocr_engine == 'ppocr'
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["--ocr-engine", "ppocr"])

    assert args.ocr_engine == "ppocr"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_correction_provider() -> None:
    """
    Given  --correction-provider openai
    When   _parse_args() is called
    Then   correction_provider == 'openai'
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["--correction-provider", "openai"])

    assert args.correction_provider == "openai"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_extra_defaults_override_builtin() -> None:
    """
    Given  extra_defaults={'language': 'German'}
    When   _parse_args() is called with no explicit --language
    Then   language == 'German'
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(extra_defaults={"language": "German"}, argv=[])

    assert args.language == "German"


# ── --version ──────────────────────────────────────────────────────────────


@pytest.mark.use_case("Headless_CLI_Batch")
def test_version_prints_the_notice_and_needs_no_folder(capsys, monkeypatch) -> None:
    """
    Given  --version, no input or output folder, and a config file that cannot be read
    When   main() is called
    Then   it exits 0 after printing the version, the copyright line and the license, before
           the log, the services or the config file are touched
    """
    from teachers_teammate import __version__  # noqa: PLC0415
    from teachers_teammate import cli  # noqa: PLC0415
    from teachers_teammate.texts import app as app_text  # noqa: PLC0415

    class _UntouchedAppService(_AppServiceStub):
        """Every answer --version could ask for fails loudly."""

        def list_providers(self) -> list[str]:
            raise AssertionError("--version must not reach the services")

        def resolve_config_path(self, explicit: str | None) -> Path | None:
            raise AssertionError("--version must not read the config file")

    def _no_log() -> None:
        raise AssertionError("--version must not start the log")

    monkeypatch.setattr(cli, "start_logging", _no_log)

    with pytest.raises(SystemExit) as exit_info:
        cli.main(
            services_of(_UntouchedAppService()),
            argv=["--config", "/nonexistent/ocr.toml", "--version"],
        )

    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    lines = printed.rstrip().splitlines()
    assert lines[0] == f"{app_text.NAME} {__version__}"
    assert app_text.COPYRIGHT in lines
    assert app_text.LICENSE_ID in printed
    assert app_text.LICENSE_URL in printed


# ── main() error paths ─────────────────────────────────────────────────────


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_exits_when_no_input_given(tmp_path: Path, monkeypatch) -> None:
    """
    Given  -o <dir> but no -i and no config file in CWD or storage root
    When   run_cli() is called
    Then   return code 1 is returned
    """
    import os  # noqa: PLC0415

    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)  # ensure no ocr.toml in CWD either
        rc = run_cli(argv=["-o", str(tmp_path)], app_service=build_app_services())
    finally:
        os.chdir(original_cwd)
    assert rc == 1


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_exits_when_no_output_and_docx_enabled(tmp_path: Path, monkeypatch) -> None:
    """
    Given  -i <dir> with --docx but no -o and no config file in CWD or storage root
    When   run_cli() is called
    Then   return code 1 is returned (the CLI guards: output required when docx_enabled)
    """
    import os  # noqa: PLC0415

    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        """No override: every CLI answer is harmless, so a run must not start."""

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)  # ensure no ocr.toml in CWD either
        rc = run_cli(
            argv=["-i", str(tmp_path), "--docx"], app_service=services_of(_FakeAppService())
        )
    finally:
        os.chdir(original_cwd)
    assert rc == 1


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_allows_missing_output_when_no_docx(tmp_path: Path, monkeypatch) -> None:
    """
    Given  -i <dir> with --no-docx and no -o and no config file
    When   run_cli() is called
    Then   the application service starts and receives output_dir fallback == input_dir
    """
    import os  # noqa: PLC0415

    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    captured: list = []

    class _FakeAppService(_AppServiceStub):
        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_cli(
            argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(_FakeAppService())
        )
    finally:
        os.chdir(original_cwd)

    assert rc == 0
    assert captured[0].docx_enabled is False
    assert captured[0].output_dir == tmp_path


@pytest.mark.use_case("Headless_CLI_Batch")
def test_the_proofreading_context_flag_reaches_the_config(tmp_path: Path, monkeypatch) -> None:
    """
    Given  --proofreading-context 16384
    When   run_cli() is called
    Then   the config the run gets carries that window
    """
    import os  # noqa: PLC0415

    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    captured: list = []

    class _FakeAppService(_AppServiceStub):
        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_cli(
            argv=[
                "-i",
                str(tmp_path),
                "--no-docx",
                "--proofreading-context",
                "16384",
            ],
            app_service=services_of(_FakeAppService()),
        )
    finally:
        os.chdir(original_cwd)

    assert rc == 0
    assert captured[0].proofreading_context == "16384"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_the_proofreading_context_flag_rejects_an_unknown_window() -> None:
    """
    Given  --proofreading-context 4096, which is not on the ladder
    When   the arguments are parsed
    Then   argparse refuses it instead of passing it on
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=["--proofreading-context", "4096"])


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_exits_when_input_dir_does_not_exist(tmp_path: Path) -> None:
    """
    Given  -i <non-existent path> -o <valid dir>
    When   run_cli() is called
    Then   return code 1 is returned
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    non_existent = tmp_path / "does_not_exist"
    assert (
        run_cli(
            argv=["-i", str(non_existent), "-o", str(tmp_path)],
            app_service=build_app_services(),
        )
        == 1
    )


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_loads_config_file_defaults(tmp_path: Path) -> None:
    """
    Given  a TOML config file with language = 'German' and a valid input dir
    When   run_cli() is called with --config pointing at that file
    Then   the application service run path is called and config has language='German'
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    out_dir = tmp_path / "output"
    cfg_file = tmp_path / "test.toml"
    cfg_file.write_text(f'language = "German"\ninput = "{in_dir}"\noutput = "{out_dir}"\n')

    captured: list = []

    class _FakeAppService(_AppServiceStub):
        def resolve_config_path(self, explicit: str | None) -> Path | None:
            return Path(explicit) if explicit else None

        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    rc = run_cli(
        argv=["--config", str(cfg_file), "-i", str(in_dir), "-o", str(out_dir)],
        app_service=services_of(_FakeAppService()),
    )

    assert rc == 0
    assert captured[0].language == "German"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_uses_exit_fn_with_run_cli_result(tmp_path: Path) -> None:
    """
    Given  a valid input/output configuration
    When   main() is called with an injected app_service and exit_fn callback
    Then   exit_fn receives the exit code of the run summary (1: a file failed)
    """
    from teachers_teammate.cli import main  # noqa: PLC0415

    out_dir = tmp_path / "out"
    captured_codes: list[int] = []

    class _FakeAppService(_AppServiceStub):
        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            return RunSummary(failed=("a.png",))

    main(
        app_service=services_of(_FakeAppService()),
        argv=["-i", str(tmp_path), "-o", str(out_dir)],
        exit_fn=lambda code: captured_codes.append(code),
    )

    assert captured_codes == [1]


@pytest.mark.use_case("Headless_CLI_Batch")
def test_main_exits_1_when_config_file_missing(tmp_path: Path) -> None:
    """
    Given  --config pointing to a path that does not exist
    When   run_cli() is called
    Then   run_cli returns 1 (ConfigFileNotFoundError is caught and reported)
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    non_existent = str(tmp_path / "no_such_config.toml")
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    rc = run_cli(
        argv=["--config", non_existent, "-i", str(input_dir)],
        app_service=build_app_services(),
    )
    assert rc == 1


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_evaluate_flag() -> None:
    """
    Given  --evaluation and --no-evaluation flags
    When   _parse_args() is called with each variant
    Then   evaluation_enabled is set correctly (default False)
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args_on = _parse_args(argv=["--evaluation"])
    args_off = _parse_args(argv=["--no-evaluation"])
    args_default = _parse_args(argv=[])

    assert args_on.evaluation_enabled is True
    assert args_off.evaluation_enabled is False
    assert args_default.evaluation_enabled is False


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_parse_args_anonymize_flag() -> None:
    """
    Given  --anonymization and --no-anonymization flags
    When   _parse_args() is called with each variant
    Then   anonymization_enabled is set correctly (default False)
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args_on = _parse_args(argv=["--anonymization"])
    args_off = _parse_args(argv=["--no-anonymization"])
    args_default = _parse_args(argv=[])

    assert args_on.anonymization_enabled is True
    assert args_off.anonymization_enabled is False
    assert args_default.anonymization_enabled is False


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_correction_notes() -> None:
    """
    Given  --correction-notes 'Year 9, second foreign language'
    When   _parse_args() is called
    Then   correction_notes is set to the supplied string
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["--correction-notes", "Year 9, second foreign language"])

    assert args.correction_notes == "Year 9, second foreign language"


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_rejects_the_removed_correction_prompt() -> None:
    """
    Given  --correction-prompt, which the app no longer has
    When   _parse_args() is called
    Then   it fails, so an old script does not silently send nothing
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=["--correction-prompt", "my custom prompt"])


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_preprocess_method_and_dpi() -> None:
    """
    Given  --preprocess-method none plus --pdf-render-dpi 600
    When   _parse_args() is called
    Then   both are parsed, and without the flag the method is "auto"
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["--preprocess-method", "none", "--pdf-render-dpi", "600"])

    assert args.preprocess_method == "none"
    assert args.pdf_render_dpi == 600
    assert _parse_args(argv=[]).preprocess_method == "auto"


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize("bad", ["0", "-100", "50", "700"])
def test_parse_args_pdf_dpi_out_of_range_exits(bad: str) -> None:
    """
    Given  --pdf-render-dpi outside the 72-600 range
    When   _parse_args() is called
    Then   argparse rejects it with SystemExit (mirrors the GUI spin-box bounds)
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=["--pdf-render-dpi", bad])


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize(
    "removed",
    [
        "--preprocess-dewarp",
        "--no-preprocess-dewarp",
        "--preprocess-deskew",
        "--no-preprocess-deskew",
        "--preprocess-border-crop",
        "--preprocess-denoise",
        "--no-preprocess-denoise",
        "--preprocess-gamma",
    ],
)
def test_the_switches_of_the_old_preparation_are_gone(removed: str) -> None:
    """
    Given  one of the switch flags of the old image preparation
    When   _parse_args() is called with it
    Then   argparse rejects it with SystemExit
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=[removed])


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize("old_method", ["grayscale", "clahe", "adaptive_threshold"])
def test_the_methods_of_the_old_preparation_are_not_choices(old_method: str) -> None:
    """
    Given  a method of the old preparation on the command line
    When   _parse_args() is called
    Then   argparse rejects it: the choices are auto and none
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=["--preprocess-method", old_method])


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_propagates_preprocessing_to_config(tmp_path: Path, monkeypatch) -> None:
    """
    Given  --preprocess-method none and a PDF DPI on the command line
    When   run_cli() builds the Config and hands it to the application service
    Then   the OcrConfig carries every preprocessing setting
    """
    import os  # noqa: PLC0415

    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    captured: list = []

    class _FakeAppService(_AppServiceStub):
        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_cli(
            argv=[
                "-i",
                str(tmp_path),
                "--no-docx",
                "--preprocess-method",
                "none",
                "--pdf-render-dpi",
                "150",
            ],
            app_service=services_of(_FakeAppService()),
        )
    finally:
        os.chdir(original_cwd)

    assert rc == 0
    ocr = captured[0].ocr
    assert ocr.preprocess_method == "none"
    assert ocr.pdf_render_dpi == 150


@pytest.mark.use_case("Headless_CLI_Batch")
def test_correction_model_help_names_the_catalog_default(capsys) -> None:
    """
    Given  the CLI help for --correction-model
    When   --help is rendered
    Then   the Ollama model named there is the catalog default (not a stale literal)
    """
    from teachers_teammate._model_defaults import default_model_for_task  # noqa: PLC0415
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=["--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert f"{default_model_for_task('ollama', 'correction')} (ollama)" in help_text


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize("engine", ["langchain", "openai"])
def test_ocr_engine_choices_exclude_online_engines(engine: str, capsys) -> None:
    """
    Given  --ocr-engine naming an online engine
    When   the arguments are parsed
    Then   argparse rejects it: text recognition only offers engines on the user's computer
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(["ollama"], {}, ["-i", ".", "--ocr-engine", engine])

    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_ocr_provider_option_is_gone(capsys) -> None:
    """
    Given  the removed --ocr-provider option
    When   the arguments are parsed
    Then   argparse reports it as unrecognised
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(["ollama"], {}, ["-i", ".", "--ocr-provider", "openai"])

    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_tells_where_documents_are_processed(tmp_path: Path, capsys) -> None:
    """
    Given  a run whose proofreading goes to an online service
    When   run_cli() starts the pipeline
    Then   stderr lists every step, flags the one that leaves, and warns about it first
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataFlowStage,
        DataLocation,
        DataRegion,
        StageLocation,
    )

    flow = DataFlow(
        (
            DataFlowStage(
                "ocr", StageLocation(DataLocation.THIS_COMPUTER, "Built-in reader"), "page images"
            ),
            DataFlowStage(
                "correction",
                StageLocation(DataLocation.ONLINE, "OpenAI", region=DataRegion.NON_EU),
                "text",
                False,
            ),
        )
    )
    calls: list[str] = []

    class _FakeAppService(_AppServiceStub):
        def describe_data_flow(self, config) -> DataFlow:
            calls.append("describe")
            return flow

        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            calls.append("run")
            return RunSummary()

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(_FakeAppService())
    )

    err = capsys.readouterr().err
    assert rc == 0
    assert calls == ["describe", "run"]
    assert "- Text recognition: On this computer" in err
    assert "! Proofreading: Online service (OpenAI) · outside the EU · names as written" in err
    assert "=> Text with names is sent to a service outside the EU." in err
    assert "Data leaves this computer" in err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_refuses_online_text_recognition_before_anything_starts(
    tmp_path: Path, capsys
) -> None:
    """
    Given  a run preflight that refuses the OCR settings of the run
    When   run_cli() is called
    Then   it prints the reason, exits with 1, and neither announces a data flow nor runs
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        def check_run_preflight(self, config, **kwargs) -> RunPreflight:
            return RunPreflight(ocr_not_allowed="Text recognition must stay on your computer.")

        def describe_data_flow(self, config) -> DataFlow:
            raise AssertionError("no data flow may be announced for a refused run")

        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            raise AssertionError("a refused run must not start")

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx", "--ocr-model", "gpt-oss:120b-cloud"],
        app_service=services_of(_FakeAppService()),
    )

    assert rc == 1
    assert "ERROR: Text recognition must stay on your computer." in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_says_how_to_download_a_missing_reader(tmp_path: Path, capsys) -> None:
    """
    Given  a preflight blocked because the built-in reader is not downloaded
    When   run_cli() is called with --ocr-engine ppocr
    Then   it prints the sentence of the issue and the download command, and starts nothing
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.cli import cli_issue_hint  # noqa: PLC0415
    from teachers_teammate.texts.issues import issue_text  # noqa: PLC0415

    issue = RequirementIssue(IssueKind.READER_MODEL_MISSING)
    service = _TextFlagsAppService(preflight=RunPreflight(stage=Stage.OCR, stage_issues=(issue,)))

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx", "--ocr-engine", "ppocr"],
        app_service=services_of(service),
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {issue_text(issue)}" in err
    assert cli_issue_hint(issue) in err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_names_the_environment_variable_of_a_missing_key(tmp_path: Path, capsys) -> None:
    """
    Given  a preflight blocked by a proofreading provider whose API key is not set
    When   run_cli() is called
    Then   it prints the sentence of the issue and names the environment variable to set
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.issues import issue_text  # noqa: PLC0415

    issue = RequirementIssue(IssueKind.KEY_MISSING, provider="openai", name="OpenAI")
    service = _TextFlagsAppService(
        preflight=RunPreflight(stage=Stage.CORRECTION, stage_issues=(issue,)),
        env_key="OPENAI_API_KEY",
    )

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(service))

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {issue_text(issue)}" in err
    assert "OPENAI_API_KEY" in err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_says_how_to_download_a_missing_ollama_model(tmp_path: Path, capsys) -> None:
    """
    Given  a preflight blocked by a model that is not downloaded in Ollama
    When   run_cli() is called
    Then   it prints the sentence of the issue and the command that downloads the model
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.issues import issue_text  # noqa: PLC0415

    issue = RequirementIssue(IssueKind.MODEL_MISSING, models=("qwen3-vl:8b",))
    service = _TextFlagsAppService(preflight=RunPreflight(missing_models=("qwen3-vl:8b",)))

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(service))

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {issue_text(issue)}" in err
    assert "ollama pull qwen3-vl:8b" in err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_stops_when_ollama_is_not_running(tmp_path: Path, capsys) -> None:
    """
    Given  a preflight that cannot reach the Ollama server, which is installed but not started
    When   run_cli() is called
    Then   it prints the sentence of the issue and says to start Ollama, and starts nothing
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.issues import issue_text  # noqa: PLC0415

    issue = RequirementIssue(IssueKind.OLLAMA_NOT_RUNNING)
    service = _TextFlagsAppService(
        preflight=RunPreflight(ollama_unreachable="Connection refused", ollama_issue=issue)
    )

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(service))

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {issue_text(issue)}" in err
    assert "Start Ollama" in err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_says_when_the_first_file_waits_for_another_model(tmp_path: Path, capsys) -> None:
    """
    Given  a preflight that found Ollama busy with another model
    When   run_cli() is called
    Then   it prints the note that the first file waits, and the run goes ahead
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.domain.ollama_discovery import WaitDetail, WaitKind  # noqa: PLC0415
    from teachers_teammate.texts.check import wait_detail_text  # noqa: PLC0415

    wait = WaitDetail(WaitKind.QUEUED, model="qwen3:8b", other="gemma3:4b")
    service = _TextFlagsAppService(preflight=RunPreflight(ollama_wait=wait))

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(service))

    assert rc == 0
    assert wait_detail_text(wait) in capsys.readouterr().err
    assert len(service.configs) == 1


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_stops_when_no_queued_file_can_be_used_without_recognition(
    tmp_path: Path, capsys
) -> None:
    """
    Given  a preflight that found no file to use with text recognition switched off
    When   run_cli() is called with --no-recognition
    Then   it prints the sentence of the issue and starts nothing
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.issues import issue_text  # noqa: PLC0415

    issue = RequirementIssue(IssueKind.NOTHING_TO_READ)
    service = _TextFlagsAppService(preflight=RunPreflight(nothing_to_read=True))

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx", "--no-recognition"],
        app_service=services_of(service),
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {issue_text(issue)}" in err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_download_reader_flag_downloads_and_exits_without_input(capsys) -> None:
    """
    Given  --download-reader and no input folder
    When   run_cli() is called
    Then   the reader is downloaded with a progress line on stderr, and the exit code is 0
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    calls: list[str] = []

    class _FakeAppService(_AppServiceStub):
        def download_reader(self, on_progress=None, cancel=None) -> None:
            calls.append("download")
            assert on_progress is not None
            on_progress(5_000_000, 31_000_000)

    rc = run_cli(argv=["--download-reader"], app_service=services_of(_FakeAppService()))

    err = capsys.readouterr().err
    assert rc == 0
    assert calls == ["download"]
    assert "5 of 31 MB" in err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_download_reader_flag_reports_the_error_sentence(capsys) -> None:
    """
    Given  a download that fails with a sentence
    When   run_cli() is called with --download-reader
    Then   the sentence is printed after ERROR and the exit code is 1
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.exceptions import ModelDownloadError  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        def download_reader(self, on_progress=None, cancel=None) -> None:
            raise ModelDownloadError("Could not reach huggingface.co.")

    rc = run_cli(argv=["--download-reader"], app_service=services_of(_FakeAppService()))

    assert rc == 1
    assert "ERROR: Could not reach huggingface.co." in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_download_page_models_flag_downloads_and_exits_without_input(capsys) -> None:
    """
    Given  --download-page-models and no input folder
    When   run_cli() is called
    Then   the page models are downloaded with a progress line on stderr, and the exit code is 0
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    calls: list[str] = []

    class _FakeAppService(_AppServiceStub):
        def download_page_models(self, on_progress=None, cancel=None) -> None:
            calls.append("download")
            assert on_progress is not None
            on_progress(5_000_000, 38_000_000)

    rc = run_cli(argv=["--download-page-models"], app_service=services_of(_FakeAppService()))

    err = capsys.readouterr().err
    assert rc == 0
    assert calls == ["download"]
    assert "5 of 38 MB" in err
    assert "The page models are ready." in err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_download_page_models_flag_reports_the_error_sentence(capsys) -> None:
    """
    Given  a page-models download that fails with a sentence
    When   run_cli() is called with --download-page-models
    Then   the sentence is printed after ERROR and the exit code is 1
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.exceptions import ModelDownloadError  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        def download_page_models(self, on_progress=None, cancel=None) -> None:
            raise ModelDownloadError("Could not reach huggingface.co.")

    rc = run_cli(argv=["--download-page-models"], app_service=services_of(_FakeAppService()))

    assert rc == 1
    assert "ERROR: Could not reach huggingface.co." in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_a_run_with_missing_page_models_prints_one_line_and_still_runs(
    tmp_path: Path, capsys
) -> None:
    """
    Given  a service that reports the page models as missing for the run
    When   run_cli() runs
    Then   the one-line notice goes to stderr and the run starts (it is a notice, not a refusal)
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    started: list[bool] = []

    class _FakeAppService(_AppServiceStub):
        def check_run_preflight(self, config, **kwargs) -> RunPreflight:
            return RunPreflight(page_models_skipped=True)

        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            started.append(True)
            return RunSummary()

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(_FakeAppService())
    )

    assert rc == 0
    assert started == [True]
    assert (
        "Turning and flattening pages is skipped: page models not downloaded "
        "(--download-page-models)" in capsys.readouterr().err
    )


class _TextFlagsAppService(_AppServiceStub):
    """Application service stub for the text-file flags; records configs, answers the preflight."""

    def __init__(self, preflight: RunPreflight | None = None, env_key: str = "") -> None:
        self.configs: list = []
        self._preflight = preflight or RunPreflight()
        self._env_key = env_key

    def check_run_preflight(self, config, **kwargs) -> RunPreflight:
        return self._preflight

    def env_key(self, provider: str) -> str:
        return self._env_key

    def run_selected(self, config, files, /, **kwargs) -> RunSummary:
        self.configs.append(config)
        return RunSummary()


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_text_file_flags() -> None:
    """
    Given  --text and --text-anonymized, and a call without them
    When   _parse_args() is called
    Then   the flags are True only when given
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    given = _parse_args(argv=["-i", "/in", "-o", "/out", "--text", "--text-anonymized"])
    absent = _parse_args(argv=["-i", "/in", "-o", "/out"])

    assert (given.text_enabled, given.text_anonymized_enabled) == (True, True)
    assert (absent.text_enabled, absent.text_anonymized_enabled) == (False, False)


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_passes_text_file_flags_to_config(tmp_path: Path) -> None:
    """
    Given  --no-docx --text --text-anonymized with an output folder
    When   run_cli() is called
    Then   the Config asks for both text files and no Word document
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    service = _TextFlagsAppService()

    rc = run_cli(
        argv=[
            "-i",
            str(tmp_path),
            "-o",
            str(tmp_path / "out"),
            "--no-docx",
            "--text",
            "--text-anonymized",
        ],
        app_service=services_of(service),
    )

    assert rc == 0
    config = service.configs[0]
    assert (config.docx_enabled, config.text_enabled, config.text_anonymized_enabled) == (
        False,
        True,
        True,
    )
    assert config.writes_text_files is True


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize("flag", ["--text", "--text-anonymized"])
def test_run_cli_text_files_need_an_output_folder(
    tmp_path: Path, monkeypatch, capsys, flag: str
) -> None:
    """
    Given  a text-file flag but no -o, no config file and no Word output
    When   run_cli() is called
    Then   it exits with 1 and says an output folder is needed, without starting a run
    """
    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    monkeypatch.chdir(tmp_path)
    service = _TextFlagsAppService()

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx", flag], app_service=services_of(service))

    assert rc == 1
    assert "No output folder specified. Use -o/--output" in capsys.readouterr().err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_without_an_input_folder_says_how_to_give_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  no -i and no config file
    When   run_cli() is called
    Then   it exits with 1 and names the option and the config key that give the folder
    """
    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    monkeypatch.chdir(tmp_path)
    service = _TextFlagsAppService()

    rc = run_cli(argv=[], app_service=services_of(service))

    assert rc == 1
    assert (
        "ERROR: No input folder specified. Use -i/--input or set 'input' in the config file."
        in capsys.readouterr().err
    )
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_with_a_missing_input_folder_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  an input folder that does not exist
    When   run_cli() is called
    Then   it exits with 1 and names the folder
    """
    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "nowhere"

    rc = run_cli(argv=["-i", str(missing)], app_service=services_of(_TextFlagsAppService()))

    assert rc == 1
    assert f"ERROR: Input folder does not exist: {missing}" in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_refuses_an_output_folder_the_service_rejects(tmp_path: Path, capsys) -> None:
    """
    Given  an application service that refuses the output folder (text files in the inputs)
    When   run_cli() is called
    Then   it prints the reason, exits with 1 and does not start the run
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.issues import issue_text  # noqa: PLC0415

    issue = RequirementIssue(IssueKind.OUTPUT_IN_INPUT_FOLDER)
    service = _TextFlagsAppService(
        preflight=RunPreflight(output_problem=FolderProblem.TEXT_IN_INPUT)
    )

    rc = run_cli(
        argv=["-i", str(tmp_path), "-o", str(tmp_path), "--no-docx", "--text"],
        app_service=services_of(service),
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {issue_text(issue)}" in err
    assert "Choose an output folder with -o/--output" in err
    assert service.configs == []


def test_run_cli_over_no_files_does_not_ask_the_preflight(tmp_path: Path) -> None:
    """
    Given  an input folder with no supported file and a preflight that would refuse the run
    When   run_cli() is called
    Then   the preflight is not asked, so the run itself says there is nothing to do
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    asked: list[bool] = []

    class _FakeAppService(_TextFlagsAppService):
        def collect_input_files(self, config) -> list[Path]:
            return []

        def check_run_preflight(self, config, **kwargs) -> RunPreflight:
            asked.append(True)
            return RunPreflight(ollama_unreachable="Connection refused")

    service = _FakeAppService()

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(service))

    assert rc == 0
    assert asked == []
    assert len(service.configs) == 1


def test_run_cli_refuses_a_run_that_would_make_the_computer_swap(tmp_path: Path, capsys) -> None:
    """
    Given  an application service that reports too little free memory for the model
    When   run_cli() is called without --ignore-memory-check
    Then   it prints the reason with the way to override it, exits with 1 and does not run
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.dialogs import memory_shortfall_text  # noqa: PLC0415

    shortfall = MemoryShortfall("qwen3.5:9b", 7.0, 6.0)
    service = _TextFlagsAppService(preflight=RunPreflight(memory_shortfall=shortfall))

    rc = run_cli(
        argv=["-i", str(tmp_path), "-o", str(tmp_path), "--no-docx", "--text"],
        app_service=services_of(service),
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert f"ERROR: {memory_shortfall_text(shortfall)}" in err
    assert "Use --ignore-memory-check to run anyway." in err
    assert service.configs == []


def test_run_cli_runs_anyway_when_the_memory_check_is_ignored(tmp_path: Path, capsys) -> None:
    """
    Given  the same memory warning
    When   run_cli() is called with --ignore-memory-check
    Then   the run starts and the warning is still printed (as a WARNING, not an ERROR)
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415
    from teachers_teammate.texts.dialogs import memory_shortfall_text  # noqa: PLC0415

    shortfall = MemoryShortfall("qwen3.5:9b", 7.0, 6.0)
    service = _TextFlagsAppService(preflight=RunPreflight(memory_shortfall=shortfall))

    rc = run_cli(
        argv=[
            "-i",
            str(tmp_path),
            "-o",
            str(tmp_path),
            "--no-docx",
            "--text",
            "--ignore-memory-check",
        ],
        app_service=services_of(service),
    )

    err = capsys.readouterr().err
    assert rc == 0
    assert f"WARNING: {memory_shortfall_text(shortfall)}" in err
    assert "ERROR:" not in err
    assert len(service.configs) == 1


@pytest.mark.use_case("Headless_CLI_Batch")
def test_parse_args_explanations_flag_overrides_a_config_file_that_turned_them_off() -> None:
    """
    Given  defaults that say correction_output = "text" (as a config file would set them)
    When   --explanations is passed, or --corrections-only, or neither
    Then   the flag decides, and without a flag the config value stays
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    defaults = {"correction_output": "text"}

    assert _parse_args(argv=["--explanations"], extra_defaults=defaults).correction_output == (
        "review"
    )
    assert _parse_args(argv=["--corrections-only"]).correction_output == "text"
    assert _parse_args(argv=[], extra_defaults=defaults).correction_output == "text"
    assert _parse_args(argv=[]).correction_output == "review"


def test_run_cli_passes_anonymizer_settings_from_the_config_file(tmp_path: Path) -> None:
    """
    Given  a config file with an extra detection model, no extra model and custom patterns
    When   run_cli() builds the Config
    Then   the Config carries them, so a CLI run and a GUI run share placeholders and cache key
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    in_dir = tmp_path / "input"
    in_dir.mkdir()
    cfg_file = tmp_path / "test.toml"
    cfg_file.write_text(
        f'input = "{in_dir}"\n'
        'anonymizer_primary_model = "de_core_news_sm"\n'
        'anonymizer_secondary_model = "none"\n'
        'anonymizer_patterns = [["EMAIL", "x@y"]]\n'
    )
    captured: list = []

    class _FakeAppService(_AppServiceStub):
        def resolve_config_path(self, explicit: str | None) -> Path | None:
            return Path(explicit) if explicit else None

        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    rc = run_cli(
        argv=["--config", str(cfg_file), "-o", str(tmp_path / "out")],
        app_service=services_of(_FakeAppService()),
    )

    assert rc == 0
    assert captured[0].anonymizer_primary_model == "de_core_news_sm"
    assert captured[0].anonymizer_secondary_model == "none"
    assert captured[0].anonymizer_patterns == [("EMAIL", "x@y")]


@pytest.mark.use_case("Headless_CLI_Batch")
def test_a_stopped_run_exits_with_zero(tmp_path: Path) -> None:
    """
    Given  a run that was stopped: one file unfinished, one never started, none failed
    When   run_cli() is called
    Then   the exit code is 0, a stop is not an error
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            return RunSummary(stopped=("a.png",), not_started=("b.png",))

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx"], app_service=services_of(_FakeAppService())
    )

    assert rc == 0


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize(
    ("flags", "expected"),
    [([], True), (["--allow-sleep"], False), (["--allow-sleep", "--keep-awake"], True)],
)
def test_the_keep_awake_flags_reach_the_config(tmp_path: Path, flags, expected) -> None:
    """
    Given  no flag, --allow-sleep, or both flags (the last one wins)
    When   run_cli() is called
    Then   the config the run gets has keep_awake set accordingly (on by default)
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    captured: list = []

    class _FakeAppService(_AppServiceStub):
        def run_selected(self, config, files, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    run_cli(
        argv=["-i", str(tmp_path), "--no-docx", *flags], app_service=services_of(_FakeAppService())
    )

    assert captured[0].keep_awake is expected


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_main_injects_the_add_on_folder_before_it_runs(monkeypatch) -> None:
    """
    Given  a wheel install, started through the cli entry point
    When   main() runs
    Then   the add-on folder is injected first, then the run's exit code is passed on
    """
    from teachers_teammate import cli  # noqa: PLC0415

    calls: list[object] = []
    monkeypatch.setattr(cli, "inject_addon_packages_dir", lambda: calls.append("inject"))
    monkeypatch.setattr(cli, "run_cli", lambda **_k: calls.append("run") or 7)

    cli.main(exit_fn=calls.append)

    assert calls == ["inject", "run", 7]


@pytest.mark.use_case("Headless_CLI_Batch")
@pytest.mark.parametrize("bad", ["0", "-5", "86401", "soon"])
def test_parse_args_ocr_timeout_out_of_range_exits(bad: str) -> None:
    """
    Given  --ocr-timeout outside the range the settings file takes, or no number
    When   _parse_args() is called
    Then   argparse rejects it with SystemExit
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    with pytest.raises(SystemExit):
        _parse_args(argv=["--ocr-timeout", bad])


@pytest.mark.unit
def test_the_hint_names_every_missing_model() -> None:
    """
    Given  an issue that names two models that are not downloaded
    When   the command line asks for its hint
    Then   the hint pulls both, so one run of it fixes the run
    """
    from teachers_teammate.texts.cli import cli_issue_hint  # noqa: PLC0415

    hint = cli_issue_hint(
        RequirementIssue(IssueKind.MODEL_MISSING, models=("qwen3-vl:8b", "qwen3.5:9b"))
    )

    assert "ollama pull qwen3-vl:8b" in hint
    assert "ollama pull qwen3.5:9b" in hint


@pytest.mark.unit
def test_the_hint_for_no_model_at_all_has_no_placeholder() -> None:
    """
    Given  an Ollama without any model and an issue that names none
    When   the command line asks for its hint
    Then   the hint says to pull a model, without a placeholder in it
    """
    from teachers_teammate.texts.cli import cli_issue_hint  # noqa: PLC0415

    hint = cli_issue_hint(RequirementIssue(IssueKind.NO_OLLAMA_MODEL))

    assert "ollama pull" in hint
    assert "<" not in hint


@pytest.mark.unit
def test_a_missing_package_can_be_installed_without_the_app() -> None:
    """
    Given  a provider package and spaCy that are not installed
    When   the command line asks for their hints
    Then   each names the app and the pip extra that installs it from the wheel
    """
    from teachers_teammate.texts.cli import cli_issue_hint  # noqa: PLC0415

    package = cli_issue_hint(RequirementIssue(IssueKind.PACKAGE_MISSING, provider="openai"))
    spacy = cli_issue_hint(RequirementIssue(IssueKind.PRIVACY_ADDON_MISSING))

    assert "Add-ons" in package
    assert "[llm]" in package
    assert "Add-ons" in spacy
    assert "[privacy]" in spacy


@pytest.mark.unit
def test_an_add_on_that_waits_for_a_restart_says_to_run_again() -> None:
    """
    Given  an add-on that is installed and waits for the next start
    When   the command line asks for its hint
    Then   it says to run the command again, and does not send the teacher to the Add-ons page
    """
    from teachers_teammate.texts.cli import cli_issue_hint  # noqa: PLC0415

    hint = cli_issue_hint(RequirementIssue(IssueKind.ADDON_RESTART_NEEDED, name="spaCy"))

    assert "again" in hint
    assert "Add-ons" not in hint


@pytest.mark.unit
def test_an_ollama_that_does_not_answer_names_the_address_flag() -> None:
    """
    Given  an Ollama address that does not answer and nothing to start from here
    When   the command line asks for its hint
    Then   the hint names --ollama-url
    """
    from teachers_teammate.texts.cli import cli_issue_hint  # noqa: PLC0415

    hint = cli_issue_hint(RequirementIssue(IssueKind.OLLAMA_UNREACHABLE, reason="timed out"))

    assert "--ollama-url" in hint
