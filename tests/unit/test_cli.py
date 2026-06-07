"""Unit tests for teachers_teammate.cli — argument parsing and main() entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

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
def test_parse_args_ocr_engine_tesseract() -> None:
    """
    Given  --ocr-engine tesseract
    When   _parse_args() is called
    Then   ocr_engine == 'tesseract'
    """
    from teachers_teammate.cli import _parse_args  # noqa: PLC0415

    args = _parse_args(argv=["--ocr-engine", "tesseract"])

    assert args.ocr_engine == "tesseract"


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

    class _FakeAppService:
        def list_providers(self) -> list[str]:
            return ["ollama", "openai"]

        def resolve_config_path(self, explicit: str | None) -> None:
            return None

        def run_selected(self, config, /, **kwargs):
            raise AssertionError("should not reach pipeline — guard must exit before run")

        def run_preview_only(self, config):
            raise AssertionError("should not reach pipeline — guard must exit before run")

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

    class _FakeAppService:
        def list_providers(self) -> list[str]:
            return ["ollama", "openai"]

        def resolve_config_path(self, explicit: str | None) -> None:
            return None

        def run_selected(self, config, /, **kwargs):
            captured.append(config)
            return 0

        def run_preview_only(self, _config):
            raise AssertionError("preview-only path should not be used")

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

    class _FakeAppService:
        def list_providers(self) -> list[str]:
            return ["ollama", "openai"]

        def resolve_config_path(self, explicit: str | None):
            return Path(explicit) if explicit else None

        def run_selected(self, config, /, **kwargs):
            captured.append(config)
            return 0

        def run_preview_only(self, _config):
            raise AssertionError("preview-only path should not be used")

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
    Then   exit_fn receives the return code from run_cli
    """
    from teachers_teammate.cli import main  # noqa: PLC0415

    out_dir = tmp_path / "out"
    captured_codes: list[int] = []

    class _FakeAppService:
        def list_providers(self) -> list[str]:
            return ["ollama", "openai"]

        def resolve_config_path(self, explicit: str | None) -> None:
            return None

        def run_selected(self, _config, /, **kwargs):
            return 7

        def run_preview_only(self, _config):
            raise AssertionError("preview-only path should not be used")

    main(
        app_service=_FakeAppService(),
        argv=["-i", str(tmp_path), "-o", str(out_dir)],
        exit_fn=lambda code: captured_codes.append(code),
    )

    assert captured_codes == [7]


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
