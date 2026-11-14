"""Unit tests for teachers_teammate.cli — argument parsing and main() entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.domain.data_location import DataFlow
from teachers_teammate.domain.run_summary import RunSummary


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

    def ocr_refusal_for_run(self, config) -> str:
        return ""

    def reader_refusal_for_run(self, config) -> str:
        return ""

    def page_models_notice_for_run(self, config) -> str:
        return ""

    def download_reader(self, on_progress=None, cancel=None) -> None:
        raise AssertionError("nothing may be downloaded")

    def download_page_models(self, on_progress=None, cancel=None) -> None:
        raise AssertionError("nothing may be downloaded")

    def output_refusal_for_run(self, config) -> str:
        return ""

    def memory_warning_for_run(self, config) -> str:
        return ""

    def run_selected(self, config, /, **kwargs) -> RunSummary:
        raise AssertionError("the run must not start")

    def run_preview_only(self, config) -> int:
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
        rc = run_cli(argv=["-o", str(tmp_path)])
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
        rc = run_cli(argv=["-i", str(tmp_path), "--docx"], app_service=_FakeAppService())
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
        def run_selected(self, config, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    monkeypatch.setattr(storage_module, "default_config_path", lambda: tmp_path / "ocr.toml")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=_FakeAppService())
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
        def run_selected(self, config, /, **kwargs) -> RunSummary:
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
            app_service=_FakeAppService(),
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
    assert run_cli(argv=["-i", str(non_existent), "-o", str(tmp_path)]) == 1


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

        def run_selected(self, config, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    rc = run_cli(
        argv=["--config", str(cfg_file), "-i", str(in_dir), "-o", str(out_dir)],
        app_service=_FakeAppService(),
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
        def run_selected(self, config, /, **kwargs) -> RunSummary:
            return RunSummary(failed=("a.png",))

    main(
        app_service=_FakeAppService(),
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

    rc = run_cli(argv=["--config", non_existent, "-i", str(input_dir)])
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
def test_parse_args_correction_prompt() -> None:
    """
    Given  --correction-prompt 'my custom prompt'
    When   _parse_args() is called
    Then   correction_prompt is set to the supplied string
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["--correction-prompt", "my custom prompt"])

    assert args.correction_prompt == "my custom prompt"


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
        def run_selected(self, config, /, **kwargs) -> RunSummary:
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
            app_service=_FakeAppService(),
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

        def run_selected(self, config, /, **kwargs) -> RunSummary:
            calls.append("run")
            return RunSummary()

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=_FakeAppService())

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
    Given  an application service that refuses the OCR settings of the run
    When   run_cli() is called
    Then   it prints the reason, exits with 1, and neither announces a data flow nor runs
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        def ocr_refusal_for_run(self, config) -> str:
            return "Text recognition must stay on your computer."

        def describe_data_flow(self, config) -> DataFlow:
            raise AssertionError("no data flow may be announced for a refused run")

        def run_selected(self, config, /, **kwargs) -> RunSummary:
            raise AssertionError("a refused run must not start")

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx", "--ocr-model", "gpt-oss:120b-cloud"],
        app_service=_FakeAppService(),
    )

    assert rc == 1
    assert "ERROR: Text recognition must stay on your computer." in capsys.readouterr().err


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_refuses_the_reader_when_it_is_not_downloaded(tmp_path: Path, capsys) -> None:
    """
    Given  an application service that says the built-in reader is not downloaded
    When   run_cli() is called with --ocr-engine ppocr
    Then   it prints the sentence, exits with 1 and starts nothing
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    class _FakeAppService(_AppServiceStub):
        def reader_refusal_for_run(self, config) -> str:
            return "The built-in reader is not downloaded yet. Use --download-reader."

        def describe_data_flow(self, config) -> DataFlow:
            raise AssertionError("no data flow may be announced for a refused run")

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx", "--ocr-engine", "ppocr"],
        app_service=_FakeAppService(),
    )

    assert rc == 1
    assert "--download-reader" in capsys.readouterr().err


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

    rc = run_cli(argv=["--download-reader"], app_service=_FakeAppService())

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

    rc = run_cli(argv=["--download-reader"], app_service=_FakeAppService())

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

    rc = run_cli(argv=["--download-page-models"], app_service=_FakeAppService())

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

    rc = run_cli(argv=["--download-page-models"], app_service=_FakeAppService())

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
    from teachers_teammate.domain.local_models import PAGE_MODELS_SKIPPED_NOTICE  # noqa: PLC0415

    started: list[bool] = []

    class _FakeAppService(_AppServiceStub):
        def page_models_notice_for_run(self, config) -> str:
            return PAGE_MODELS_SKIPPED_NOTICE

        def run_selected(self, config, /, **kwargs) -> RunSummary:
            started.append(True)
            return RunSummary()

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=_FakeAppService())

    assert rc == 0
    assert started == [True]
    assert (
        "Turning and flattening pages is skipped: page models not downloaded "
        "(--download-page-models)" in capsys.readouterr().err
    )


class _TextFlagsAppService(_AppServiceStub):
    """Application service stub for the text-file flags; records configs, refuses on demand."""

    def __init__(self, output_refusal: str = "", memory_warning: str = "") -> None:
        self.configs: list = []
        self._output_refusal = output_refusal
        self._memory_warning = memory_warning

    def memory_warning_for_run(self, config) -> str:
        return self._memory_warning

    def output_refusal_for_run(self, config) -> str:
        return self._output_refusal

    def run_selected(self, config, /, **kwargs) -> RunSummary:
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
        app_service=service,
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

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx", flag], app_service=service)

    assert rc == 1
    assert "No output folder specified" in capsys.readouterr().err
    assert service.configs == []


@pytest.mark.use_case("Headless_CLI_Batch")
def test_run_cli_refuses_an_output_folder_the_service_rejects(tmp_path: Path, capsys) -> None:
    """
    Given  an application service that refuses the output folder (text files in the inputs)
    When   run_cli() is called
    Then   it prints the reason, exits with 1 and does not start the run
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    service = _TextFlagsAppService(output_refusal="Text files cannot be saved there.")

    rc = run_cli(
        argv=["-i", str(tmp_path), "-o", str(tmp_path), "--no-docx", "--text"],
        app_service=service,
    )

    assert rc == 1
    assert "ERROR: Text files cannot be saved there." in capsys.readouterr().err
    assert service.configs == []


def test_run_cli_refuses_a_run_that_would_make_the_computer_swap(tmp_path: Path, capsys) -> None:
    """
    Given  an application service that reports too little free memory for the model
    When   run_cli() is called without --ignore-memory-check
    Then   it prints the reason with the way to override it, exits with 1 and does not run
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    service = _TextFlagsAppService(memory_warning="qwen3.5:9b needs about 7 GB of free memory.")

    rc = run_cli(
        argv=["-i", str(tmp_path), "-o", str(tmp_path), "--no-docx", "--text"], app_service=service
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR: qwen3.5:9b needs about 7 GB of free memory." in err
    assert "Use --ignore-memory-check to run anyway." in err
    assert service.configs == []


def test_run_cli_runs_anyway_when_the_memory_check_is_ignored(tmp_path: Path, capsys) -> None:
    """
    Given  the same memory warning
    When   run_cli() is called with --ignore-memory-check
    Then   the run starts and the warning is still printed (as a WARNING, not an ERROR)
    """
    from teachers_teammate.cli import run_cli  # noqa: PLC0415

    service = _TextFlagsAppService(memory_warning="qwen3.5:9b needs about 7 GB of free memory.")

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
        app_service=service,
    )

    err = capsys.readouterr().err
    assert rc == 0
    assert "WARNING: qwen3.5:9b needs about 7 GB of free memory." in err
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

        def run_selected(self, config, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    rc = run_cli(
        argv=["--config", str(cfg_file), "-o", str(tmp_path / "out")],
        app_service=_FakeAppService(),
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
        def run_selected(self, config, /, **kwargs) -> RunSummary:
            return RunSummary(stopped=("a.png",), not_started=("b.png",))

    rc = run_cli(argv=["-i", str(tmp_path), "--no-docx"], app_service=_FakeAppService())

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
        def run_selected(self, config, /, **kwargs) -> RunSummary:
            captured.append(config)
            return RunSummary()

    run_cli(argv=["-i", str(tmp_path), "--no-docx", *flags], app_service=_FakeAppService())

    assert captured[0].keep_awake is expected
