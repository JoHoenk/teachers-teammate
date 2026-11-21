"""Guard tests for the one settings schema.

``teachers_teammate/settings_schema.py`` is the only place a settings key is declared.  These
tests fail when a key is added to one of the places that read it and not to the others: the
``Config`` fields, the panel's dict, the settings file.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest
from teachers_teammate.cli import run_cli
from teachers_teammate.config import Config, OcrConfig, dump_config_file, load_config_file
from teachers_teammate.domain.data_location import DataFlow
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.domain.settings_notice import SettingNoticeKind
from teachers_teammate.gui._types import SettingsDict
from teachers_teammate.settings_schema import DEFAULTS, SETTINGS, SettingScope, normalize_settings

from ..gui._service_stubs import services_of

_SCHEMA_KEYS = frozenset(setting.key for setting in SETTINGS)
_FILE_KEYS = frozenset(setting.key for setting in SETTINGS if setting.scope is SettingScope.FILE)
_RUN_KEYS = frozenset(setting.key for setting in SETTINGS if setting.scope is SettingScope.RUN)
_APP_KEYS = frozenset(setting.key for setting in SETTINGS if setting.scope is SettingScope.APP)

#: The OCR slice names two of its fields after the engine, not after the settings key.
_OCR_KEY_OF_FIELD = {"engine": "ocr_engine", "model": "ocr_model"}

#: A value every key accepts and that is not its default, so a round trip proves the key is read.
_SAMPLES: dict[str, object] = {
    "input": "/tmp/in",
    "output": "/tmp/out",
    "recursive": True,
    "ocr_engine": "ppocr",
    "ocr_model": "some-reader:1b",
    "language": "German",
    "preprocess_method": "none",
    "ollama_url": "http://box:11434",
    "ocr_timeout": 60,
    "recognition_enabled": False,
    "keep_awake": False,
    "check_for_updates": False,
    "pdf_render_dpi": 150,
    "pdf_text_layer": "never",
    "debug": True,
    "correction_enabled": False,
    "anonymization_enabled": True,
    "correction_provider": "openai",
    "correction_model": "gpt-x",
    "correction_notes": "Ignore missing capital letters in headings.",
    "correction_level": "C1",
    "explanation_language": "German",
    "correction_output": "text",
    "evaluation_enabled": True,
    "evaluate_provider": "openai",
    "evaluate_model": "gpt-y",
    "evaluate_prompt": "Review the content only.",
    "docx_format": "comments",
    "docx_enabled": True,
    "text_enabled": True,
    "text_anonymized_enabled": True,
    "anonymizer_primary_model": "de_core_news_sm",
    "anonymizer_secondary_model": "en_core_web_sm",
    "anonymizer_patterns": [("EMAIL", r"\S+@\S+")],
    "hardware_profile": "16gb",
    "proofreading_context": "16384",
}


class _RecordingService:
    """The application service as the command line uses it: nothing refuses, the run is recorded."""

    def __init__(self) -> None:
        self.configs: list[Config] = []

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def resolve_config_path(self, explicit: str | None) -> Path | None:
        return Path(explicit) if explicit else None

    def describe_data_flow(self, config: Config) -> DataFlow:
        return DataFlow(())

    def ocr_refusal_for_run(self, config: Config) -> str:
        return ""

    def reader_refusal_for_run(self, config: Config, files: Sequence[Path]) -> str:
        return ""

    def output_refusal_for_run(self, config: Config) -> str:
        return ""

    def memory_warning_for_run(self, config: Config) -> str:
        return ""

    def page_models_notice_for_run(self, config: Config) -> str:
        return ""

    def collect_input_files(self, config: Config) -> list[Path]:
        return []

    def run_preview_only(self, config: Config, files, /) -> RunSummary:
        raise AssertionError("the preview must not run")

    def download_reader(self, on_progress=None, cancel=None) -> None:
        raise AssertionError("nothing may be downloaded")

    def download_page_models(self, on_progress=None, cancel=None) -> None:
        raise AssertionError("nothing may be downloaded")

    def run_selected(self, config: Config, files, /, **kwargs: object) -> RunSummary:
        self.configs.append(config)
        return RunSummary()


def test_the_schema_lists_every_default_and_every_folder_key() -> None:
    """
    Given  the settings schema
    When   its keys are compared with the defaults and the folder keys
    Then   they are the same set, and no key is declared twice
    """
    assert _SCHEMA_KEYS == frozenset(DEFAULTS) | _FILE_KEYS
    assert len(_SCHEMA_KEYS) == len(SETTINGS)


def test_the_run_keys_are_the_config_fields() -> None:
    """
    Given  the settings schema
    When   its run and app keys are compared with the fields of Config and OcrConfig
    Then   they are the same set: every field a run or the window reads is a setting, and every
           setting a field (the folders, ``recursive`` and the OCR slice are the caller's; the
           OCR temperature is fixed for the app and only the Benchmark varies it)
    """
    config_fields = frozenset(field.name for field in dataclasses.fields(Config))
    ocr_fields = frozenset(
        _OCR_KEY_OF_FIELD.get(field.name, field.name) for field in dataclasses.fields(OcrConfig)
    )
    caller_keys = {"input_dir", "output_dir", "recursive", "ocr"}

    assert _RUN_KEYS | _APP_KEYS == (config_fields - caller_keys) | (ocr_fields - {"temperature"})


def test_the_panel_dict_holds_only_schema_keys() -> None:
    """
    Given  the dict the panel hands to the settings file
    When   its keys are compared with the schema
    Then   every one of them is a settings key
    """
    assert frozenset(SettingsDict.__annotations__) <= _SCHEMA_KEYS


def test_every_key_round_trips_through_the_file(tmp_path: Path) -> None:
    """
    Given  a value for every key of the schema, none of them the default
    When   they are written to a settings file and read back
    Then   every value comes back unchanged and nothing is reported as ignored
    """
    assert frozenset(_SAMPLES) == _SCHEMA_KEYS, "a new key needs a sample value"
    path = tmp_path / "ocr.toml"
    path.write_text(dump_config_file(dict(_SAMPLES)), encoding="utf-8")

    loaded = load_config_file(path)

    assert loaded.notices == ()
    for key, value in _SAMPLES.items():
        assert loaded.values[key] == value, key


def test_every_default_is_taken_as_it_is() -> None:
    """
    Given  the defaults of the schema
    When   they are normalised, as "restore defaults" does
    Then   every key comes back unchanged (the review prompt as "", its built-in marker) and
           nothing is reported as ignored, so a reset really resets every key
    """
    loaded = normalize_settings(DEFAULTS)

    assert loaded.notices == ()
    assert frozenset(loaded.values) == frozenset(DEFAULTS)
    assert loaded.values["anonymizer_patterns"] is None
    assert {key: value for key, value in loaded.values.items() if value != DEFAULTS[key]} == {
        "evaluate_prompt": ""
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("debug", "false"),
        ("keep_awake", 0),
        ("ocr_timeout", "soon"),
        ("pdf_render_dpi", 3.5),
        ("correction_provider", None),
        ("docx_format", "pdf"),
        ("proofreading_context", "4096"),
        ("hardware_profile", "gpu"),
        ("pdf_text_layer", "sometimes"),
        ("anonymizer_patterns", ["EMAIL"]),
    ],
)
def test_a_value_a_key_does_not_accept_is_left_out_and_reported_as_written(
    key: str, value: object
) -> None:
    """
    Given  a value that merely looks like what the key takes (a text for a switch, no value)
    When   the settings are normalised
    Then   the key is left out, so its default applies, and the notice carries the value as it
           was written, for ``texts/`` to turn into a sentence
    """
    loaded = normalize_settings({key: value})

    assert key not in loaded.values
    (notice,) = loaded.notices
    assert (notice.kind, notice.key, notice.detail) == (
        SettingNoticeKind.VALUE_IGNORED,
        key,
        str(value),
    )


def test_a_missing_folder_is_no_folder() -> None:
    """
    Given  the command line's namespace, where an unset -i and -o are None
    When   the settings are normalised
    Then   they are empty folders and nothing is reported
    """
    loaded = normalize_settings({"input": None, "output": None})

    assert loaded.values == {"input": "", "output": ""}
    assert loaded.notices == ()


def test_the_window_and_the_command_line_read_one_file_the_same(
    qtbot, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  one settings file with a value for every key
    When   the command line (through run_cli) and the window each build their Config from it
    Then   the two are equal field by field, so a run does not depend on who started it
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    path = tmp_path / "ocr.toml"
    path.write_text(
        dump_config_file(
            {
                **_SAMPLES,
                "input": str(input_dir),
                "output": str(output_dir),
                # The panel drops a review without proofreading, so both are on here.
                "correction_enabled": True,
                "evaluation_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    values = load_config_file(path).values

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.load_from_dict(values)

    service = _RecordingService()
    exit_code = run_cli(argv=["--config", str(path)], app_service=services_of(service))

    assert exit_code == 0, capsys.readouterr().err
    assert len(service.configs) == 1
    assert panel.to_config() == service.configs[0]


@pytest.mark.gui
def test_the_panel_writes_no_key_outside_the_schema(qtbot) -> None:
    """
    Given  a fresh panel
    When   it hands its settings to the file
    Then   every key it writes is a settings key
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    assert frozenset(panel.to_dict()) <= _SCHEMA_KEYS


@pytest.mark.gui
def test_restoring_the_defaults_resets_the_anonymizer_patterns(qtbot) -> None:
    """
    Given  a panel with a pattern list of its own (here: none at all)
    When   the defaults are restored
    Then   the list is the anonymizer's own again, not the teacher's earlier choice
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.load_from_dict({"anonymizer_patterns": []})
    assert panel.to_dict().get("anonymizer_patterns") == []

    panel.restore_defaults()

    assert panel.get_settings_dict()["anonymizer_patterns"] is None


@pytest.mark.gui
def test_a_loaded_recursive_is_not_kept_by_the_panel(qtbot) -> None:
    """
    Given  a settings file that holds ``recursive`` (the command line reads it)
    When   the panel loads it and hands its settings back
    Then   it writes no ``recursive``: the window always searches subfolders and has no toggle
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict({"recursive": True, "debug": True})

    assert "recursive" not in panel.to_dict()
    assert panel.to_dict()["debug"] is True


@pytest.mark.gui
def test_a_language_typed_by_hand_survives_a_reload(qtbot) -> None:
    """
    Given  a settings file naming a language that is not in the panel's list
    When   the panel loads it
    Then   the language box shows it and the Config carries it
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict({"language": "Swahili"})

    assert panel.lookup_language() == "Swahili"
    assert panel.lookup_config().language == "Swahili"
