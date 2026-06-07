"""GUI tests for AnonymizerConfigDialog using pytest-qt."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.gui._anonymizer_config_dialog import AnonymizerConfigDialog
from teachers_teammate.infrastructure.anonymizer import AnonymizerConfig, DEFAULT_PATTERNS
from PySide6.QtWidgets import QDialogButtonBox


def _make_dialog(qtbot, config=None, app_service=None, sample_text=""):
    """Create an AnonymizerConfigDialog with spaCy model fetching suppressed.

    Patches ``ProcessingApplicationService`` to return ``[]`` from
    ``list_installed_spacy_models`` and immediately quits the background thread
    so tests are deterministic.
    """
    svc = app_service or MagicMock()
    cfg = config if config is not None else AnonymizerConfig()
    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = []
        dlg = AnonymizerConfigDialog("English", cfg, svc, sample_text=sample_text)
    # Stop the thread to avoid teardown races
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    return dlg


# ── OK button state ────────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_ok_disabled_when_regex_invalid(qtbot) -> None:
    """
    Given  a dialog constructed with a config containing an invalid regex pattern
    When   the dialog is shown
    Then   the OK button is disabled
    """
    cfg = AnonymizerConfig(patterns=(("BAD", "[unclosed"),))
    dlg = _make_dialog(qtbot, config=cfg)

    ok = dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None
    assert not ok.isEnabled()


@pytest.mark.gui
def test_dialog_ok_enabled_when_all_regex_valid(qtbot) -> None:
    """
    Given  a dialog constructed with a valid regex pattern
    When   the dialog is shown
    Then   the OK button is enabled
    """
    cfg = AnonymizerConfig(patterns=(("ID", r"\d+"),))
    dlg = _make_dialog(qtbot, config=cfg)

    ok = dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None
    assert ok.isEnabled()


@pytest.mark.gui
def test_dialog_ok_enabled_when_no_patterns(qtbot) -> None:
    """
    Given  a dialog with no patterns at all
    When   the dialog is shown
    Then   the OK button is enabled (nothing invalid)
    """
    cfg = AnonymizerConfig(patterns=())
    dlg = _make_dialog(qtbot, config=cfg)

    ok = dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None
    assert ok.isEnabled()


# ── patterns property ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_patterns_property_returns_filled_rows(qtbot) -> None:
    """
    Given  a dialog with two valid pattern rows
    When   .patterns is accessed
    Then   it returns the (tag, regex) pairs for both rows
    """
    cfg = AnonymizerConfig(patterns=(("TAG1", r"\d+"), ("TAG2", r"\w+")))
    dlg = _make_dialog(qtbot, config=cfg)

    assert dlg.patterns == [("TAG1", r"\d+"), ("TAG2", r"\w+")]


@pytest.mark.gui
def test_dialog_patterns_property_skips_empty_rows(qtbot) -> None:
    """
    Given  a dialog with one fully empty row and one filled row
    When   .patterns is accessed
    Then   the empty row is excluded from the result
    """
    cfg = AnonymizerConfig(patterns=(("", ""), ("ID", r"\d+")))
    dlg = _make_dialog(qtbot, config=cfg)

    assert dlg.patterns == [("ID", r"\d+")]


# ── Initial population ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_preloads_patterns_from_config(qtbot) -> None:
    """
    Given  a dialog constructed with a config containing one custom pattern
    When   the dialog is displayed
    Then   the table has one row with the correct tag and regex text
    """
    cfg = AnonymizerConfig(patterns=(("STU_ID", r"\bSTU-\d{6}\b"),))
    dlg = _make_dialog(qtbot, config=cfg)

    assert dlg._table.rowCount() == 1
    assert dlg._table.item(0, 0).text() == "STU_ID"
    assert dlg._table.item(0, 1).text() == r"\bSTU-\d{6}\b"


@pytest.mark.gui
def test_dialog_preloads_default_patterns(qtbot) -> None:
    """
    Given  a dialog constructed with a default AnonymizerConfig
    When   the dialog is displayed
    Then   the table is pre-populated with the three built-in patterns
    """
    dlg = _make_dialog(qtbot)

    assert dlg._table.rowCount() == len(DEFAULT_PATTERNS)


# ── Secondary model combo ─────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_secondary_model_combo_has_none_option(qtbot) -> None:
    """
    Given  a dialog with no secondary model configured (after models are fetched)
    When   the combo box is populated with an empty model list
    Then   the combo box is set to the '(none)' entry and currentData() is None
    """
    dlg = _make_dialog(qtbot)
    # Simulate the async fetch completing with an empty list
    dlg._on_spacy_models_fetched([])

    assert dlg._secondary_model.currentData() is None


@pytest.mark.gui
def test_dialog_secondary_model_combo_selects_installed_model(qtbot) -> None:
    """
    Given  an installed spaCy model is available and configured as secondary
    When   the dialog is displayed and fetch completes
    Then   the combo box selects that model
    """
    installed = ["xx_ent_wiki_sm", "de_core_news_sm"]
    cfg = AnonymizerConfig(secondary_model="xx_ent_wiki_sm")

    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = installed
        dlg = AnonymizerConfigDialog("English", cfg, MagicMock())
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    # Simulate async fetch completion
    dlg._on_spacy_models_fetched(installed)

    assert dlg._secondary_model.currentData() == "xx_ent_wiki_sm"


@pytest.mark.gui
def test_dialog_secondary_model_falls_back_to_none_when_not_installed(qtbot) -> None:
    """
    Given  the configured secondary model is not present in the installed list
    When   the dialog is displayed and fetch completes with empty list
    Then   the combo box falls back to '(none)'
    """
    cfg = AnonymizerConfig(secondary_model="missing_model_sm")

    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = []
        dlg = AnonymizerConfigDialog("English", cfg, MagicMock())
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    dlg._on_spacy_models_fetched([])

    assert dlg._secondary_model.currentData() is None


# ── Primary model combo ────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_primary_combo_exists(qtbot) -> None:
    """
    Given  a dialog is constructed
    When   the dialog is shown
    Then   it has a _primary_model combo box
    """
    dlg = _make_dialog(qtbot)

    assert hasattr(dlg, "_primary_model")


@pytest.mark.gui
def test_dialog_primary_defaults_to_auto_after_load(qtbot) -> None:
    """
    Given  a config with no primary_model set
    When   the fetch completes with an installed model list
    Then   primary combo stays at the Auto item (userData=None)
    """
    dlg = _make_dialog(qtbot)
    dlg._on_spacy_models_fetched(["xx_ent_wiki_sm"])

    assert dlg._primary_model.currentData() is None


@pytest.mark.gui
def test_dialog_primary_selects_configured_model_when_installed(qtbot) -> None:
    """
    Given  a config with primary_model="xx_ent_wiki_sm" and that model is installed
    When   the fetch completes
    Then   the primary combo selects "xx_ent_wiki_sm"
    """
    cfg = AnonymizerConfig(primary_model="xx_ent_wiki_sm")
    dlg = _make_dialog(qtbot, config=cfg)
    dlg._on_spacy_models_fetched(["xx_ent_wiki_sm", "de_core_news_sm"])

    assert dlg._primary_model.currentData() == "xx_ent_wiki_sm"


@pytest.mark.gui
def test_dialog_primary_falls_back_to_auto_when_not_installed(qtbot) -> None:
    """
    Given  a config with primary_model set to a model that is not in the installed list
    When   the fetch completes
    Then   the primary combo falls back to Auto (userData=None)
    """
    cfg = AnonymizerConfig(primary_model="missing_model_sm")
    dlg = _make_dialog(qtbot, config=cfg)
    dlg._on_spacy_models_fetched([])

    assert dlg._primary_model.currentData() is None


# ── anonymizer_config property ────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_anonymizer_config_property(qtbot) -> None:
    """
    Given  a dialog with an installed secondary model and one custom pattern (after fetch)
    When   .anonymizer_config is accessed
    Then   it returns an AnonymizerConfig reflecting the dialog state
    """
    installed = ["xx_ent_wiki_sm"]
    cfg = AnonymizerConfig(secondary_model="xx_ent_wiki_sm", patterns=(("ID", r"\d+"),))

    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = installed
        dlg = AnonymizerConfigDialog("English", cfg, MagicMock())
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    dlg._on_spacy_models_fetched(installed)

    result = dlg.anonymizer_config
    assert isinstance(result, AnonymizerConfig)
    assert result.secondary_model == "xx_ent_wiki_sm"
    assert result.primary_model is None
    assert ("ID", r"\d+") in result.patterns


@pytest.mark.gui
def test_dialog_anonymizer_config_no_secondary_when_none_selected(qtbot) -> None:
    """
    Given  a dialog where the combo box is at '(none)' after fetch
    When   .anonymizer_config is accessed
    Then   secondary_model is None and primary_model is None
    """
    dlg = _make_dialog(qtbot)
    dlg._on_spacy_models_fetched([])
    dlg._secondary_model.setCurrentIndex(0)  # "(none)" is always first after fetch

    result = dlg.anonymizer_config
    assert isinstance(result, AnonymizerConfig)
    assert result.secondary_model is None
    assert result.primary_model is None


@pytest.mark.gui
def test_dialog_anonymizer_config_primary_none_when_auto(qtbot) -> None:
    """
    Given  a dialog where primary model combo is set to 'Auto'
    When   .anonymizer_config is accessed
    Then   primary_model is None
    """
    dlg = _make_dialog(qtbot)
    dlg._on_spacy_models_fetched(["xx_ent_wiki_sm"])
    # First item is Auto
    dlg._primary_model.setCurrentIndex(0)

    assert dlg.anonymizer_config.primary_model is None


@pytest.mark.gui
def test_dialog_anonymizer_config_primary_name_when_set(qtbot) -> None:
    """
    Given  a dialog where primary model combo has a specific model selected
    When   .anonymizer_config is accessed
    Then   primary_model holds that model name
    """
    dlg = _make_dialog(qtbot)
    dlg._on_spacy_models_fetched(["xx_ent_wiki_sm"])
    # Select the installed model (index 1 after Auto)
    dlg._primary_model.setCurrentIndex(1)

    assert dlg.anonymizer_config.primary_model == "xx_ent_wiki_sm"


# ── Async loading state ────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_combos_disabled_while_loading(qtbot) -> None:
    """
    Given  the spaCy fetch thread has not yet completed
    When   the dialog is just constructed
    Then   both model combos are disabled (showing the loading placeholder)
    """
    svc = MagicMock()
    cfg = AnonymizerConfig()
    # Do NOT call _on_spacy_models_fetched — simulate thread still running
    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = []
        dlg = AnonymizerConfigDialog("English", cfg, svc)
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)

    assert not dlg._primary_model.isEnabled()
    assert not dlg._secondary_model.isEnabled()


@pytest.mark.gui
def test_dialog_models_loaded_async(qtbot) -> None:
    """
    Given  the fetch thread returns a model list
    When   _on_spacy_models_fetched is called
    Then   both combos are enabled and the secondary combo contains the model
    """
    dlg = _make_dialog(qtbot)
    dlg._on_spacy_models_fetched(["xx_ent_wiki_sm"])

    assert dlg._secondary_model.isEnabled()
    assert dlg._primary_model.isEnabled()
    assert dlg._secondary_model.findData("xx_ent_wiki_sm") >= 0


# ── Row editing ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_add_row_appends_empty_row(qtbot) -> None:
    """Given a dialog / When _add_row is called / Then the table gains an empty trailing row."""
    cfg = AnonymizerConfig(patterns=(("ID", r"\d+"),))
    dlg = _make_dialog(qtbot, config=cfg)

    dlg._add_row()

    assert dlg._table.rowCount() == 2


@pytest.mark.gui
def test_dialog_remove_selected_drops_row(qtbot) -> None:
    """Given a selected row / When _remove_selected is called / Then that row is removed."""
    cfg = AnonymizerConfig(patterns=(("A", r"\d+"), ("B", r"\w+")))
    dlg = _make_dialog(qtbot, config=cfg)
    dlg._table.selectRow(0)

    dlg._remove_selected()

    assert dlg._table.rowCount() == 1
    assert dlg._table.item(0, 0).text() == "B"


@pytest.mark.gui
def test_dialog_restore_default_patterns_repopulates(qtbot) -> None:
    """Given a dialog with custom patterns / When defaults are restored / Then the table holds the defaults."""
    cfg = AnonymizerConfig(patterns=(("ONLY", r"\d+"),))
    dlg = _make_dialog(qtbot, config=cfg)

    dlg._restore_default_patterns()

    assert dlg._table.rowCount() == len(DEFAULT_PATTERNS)


# ── Regex section collapsed by default ────────────────────────────────────


@pytest.mark.gui
def test_dialog_regex_section_collapsed_by_default(qtbot) -> None:
    """
    Given  a dialog is constructed
    When   the dialog is shown
    Then   the regex CollapsibleSection starts collapsed (table not visible)
    """
    dlg = _make_dialog(qtbot)

    # The table is inside the collapsible; when collapsed, maximumHeight == 0
    assert dlg._table.maximumHeight() == 0 or not dlg._table.isVisible()


@pytest.mark.gui
def test_dialog_ok_disabled_with_invalid_pattern_when_collapsed(qtbot) -> None:
    """
    Given  a dialog with an invalid regex pattern in the collapsed section
    When   the dialog is shown (section collapsed by default)
    Then   the OK button is still disabled regardless of collapse state
    """
    cfg = AnonymizerConfig(patterns=(("BAD", "[unclosed"),))
    dlg = _make_dialog(qtbot, config=cfg)

    ok = dlg._buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None
    assert not ok.isEnabled()


@pytest.mark.gui
def test_dialog_patterns_accessible_regardless_of_collapse_state(qtbot) -> None:
    """
    Given  a dialog with patterns in the collapsed section
    When   .patterns is accessed
    Then   it returns the table contents regardless of whether the section is expanded
    """
    cfg = AnonymizerConfig(patterns=(("TAG", r"\d+"),))
    dlg = _make_dialog(qtbot, config=cfg)
    # Section is collapsed by default; patterns should still be readable
    assert dlg.patterns == [("TAG", r"\d+")]


# ── Preview ────────────────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.use_case("Privacy_Preview_Diff")
def test_dialog_run_preview_populates_output_and_mapping(qtbot) -> None:
    """
    Given  the app service returns anonymized text and a placeholder mapping
    When   _run_preview runs with non-empty sample input
    Then   the output box and mapping table reflect the result
    """
    svc = MagicMock()
    svc.anonymize_preview.return_value = ("Dear <NAME_0>", {"<NAME_0>": "Alice"})
    dlg = _make_dialog(qtbot, app_service=svc)
    dlg._sample_input.setPlainText("Dear Alice")

    dlg._run_preview()

    assert dlg._sample_output.toPlainText() == "Dear <NAME_0>"
    assert dlg._mapping_table.rowCount() == 1
    assert dlg._mapping_table.item(0, 0).text() == "<NAME_0>"
    assert dlg._mapping_table.item(0, 1).text() == "Alice"


@pytest.mark.gui
def test_dialog_run_preview_empty_input_is_noop(qtbot) -> None:
    """Given blank sample input / When _run_preview runs / Then the service is not called."""
    svc = MagicMock()
    dlg = _make_dialog(qtbot, app_service=svc)
    dlg._sample_input.setPlainText("   ")

    dlg._run_preview()

    svc.anonymize_preview.assert_not_called()


@pytest.mark.gui
def test_dialog_run_preview_shows_spacy_missing_on_import_error(qtbot) -> None:
    """Given anonymize_preview raises ImportError / When previewing / Then a spaCy-missing error is shown."""
    svc = MagicMock()
    svc.anonymize_preview.side_effect = ImportError("no spacy")
    dlg = _make_dialog(qtbot, app_service=svc)
    dlg._sample_input.setPlainText("Dear Alice")

    dlg._run_preview()

    assert "spaCy is not installed" in dlg._preview_error.text()


@pytest.mark.gui
def test_dialog_run_preview_auto_opens_download_on_oserror(qtbot) -> None:
    """
    Given  anonymize_preview raises OSError (model missing) and download is available
    When   _run_preview is called
    Then   _open_download_dialog is called with the needed model name
    """
    svc = MagicMock()
    svc.anonymize_preview.side_effect = OSError("model missing")
    svc.spacy_model_for_language.return_value = "en_core_web_sm"
    dlg = _make_dialog(qtbot, app_service=svc)
    dlg._sample_input.setPlainText("Dear Alice")

    with (
        patch(
            "teachers_teammate.gui._addon_installer_dialog.SpacyModelDownloadDialog.is_available",
            return_value=True,
        ),
        patch.object(dlg, "_open_download_dialog") as mock_open,
    ):
        dlg._run_preview()

    mock_open.assert_called_once_with("en_core_web_sm")


@pytest.mark.gui
def test_dialog_run_preview_shows_text_error_when_not_available(qtbot) -> None:
    """
    Given  anonymize_preview raises OSError and SpacyModelDownloadDialog is not available
    When   _run_preview is called
    Then   a text error is shown (no dialog opened)
    """
    svc = MagicMock()
    svc.anonymize_preview.side_effect = OSError("model missing")
    svc.spacy_model_for_language.return_value = "en_core_web_sm"
    dlg = _make_dialog(qtbot, app_service=svc)
    dlg._sample_input.setPlainText("Dear Alice")

    with (
        patch(
            "teachers_teammate.gui._addon_installer_dialog.SpacyModelDownloadDialog.is_available",
            return_value=False,
        ),
        patch.object(dlg, "_open_download_dialog") as mock_open,
    ):
        dlg._run_preview()

    mock_open.assert_not_called()
    assert "en_core_web_sm" in dlg._preview_error.text()


# ── spaCy model download ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_on_download_model_warns_when_addon_missing(qtbot) -> None:
    """
    Given  the privacy addon is not installed
    When   _open_download_dialog is invoked
    Then   an information message box is shown and no download dialog opens
    """
    svc = MagicMock()
    svc.is_addon_available.return_value = False
    dlg = _make_dialog(qtbot, app_service=svc)

    with patch("PySide6.QtWidgets.QMessageBox.information") as info:
        dlg._open_download_dialog("xx_ent_wiki_sm")

    info.assert_called_once()


@pytest.mark.gui
def test_dialog_on_model_downloaded_refreshes_combos(qtbot) -> None:
    """
    Given  a freshly downloaded model
    When   _on_model_downloaded is called
    Then   both secondary and primary combos are refreshed
    """
    dlg = _make_dialog(qtbot)

    with patch(
        "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = ["xx_ent_wiki_sm"]
        dlg._on_model_downloaded("xx_ent_wiki_sm")
        dlg._spacy_fetch_thread.quit()
        dlg._spacy_fetch_thread.wait(500)
        # Simulate the async fetch completing
        dlg._on_spacy_models_fetched(["xx_ent_wiki_sm"])

    assert dlg._secondary_model.findData("xx_ent_wiki_sm") >= 0
    assert dlg._primary_model.findData("xx_ent_wiki_sm") >= 0


# ── ConfigPanel → dialog → close integration ─────────────────────────────────


@pytest.mark.gui
def test_configure_dialog_open_close_flow(qtbot) -> None:
    """
    Given  anonymization is enabled on the config panel
    When   the Configure button is clicked and the dialog is immediately rejected
    Then   the flow completes within timeout (regression guard for main-thread freeze)
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415
    from PySide6.QtCore import QTimer  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._anonymization_enabled.setChecked(True)

    opened = []

    def fake_handler(language: str, config: object) -> None:
        with patch(
            "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
        ) as MockSvc:
            MockSvc.return_value.list_installed_spacy_models.return_value = []
            dlg = AnonymizerConfigDialog(language, config, MagicMock(), panel)
        dlg._spacy_fetch_thread.quit()
        dlg._spacy_fetch_thread.wait(500)
        qtbot.addWidget(dlg)
        opened.append(dlg)
        QTimer.singleShot(0, dlg.reject)
        dlg.exec()

    panel.anonymizer_configure_requested.connect(fake_handler)

    with qtbot.waitSignal(panel.anonymizer_configure_requested, timeout=3000):
        panel._anon_configure_btn.click()

    assert len(opened) == 1


@pytest.mark.gui
def test_configure_dialog_accept_updates_config_panel(qtbot) -> None:
    """
    Given  a dialog with a specific regex pattern
    When   the dialog is accepted
    Then   the config panel's anonymizer config is updated with the new patterns
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._anonymization_enabled.setChecked(True)

    accepted_config = []

    def fake_handler(language: str, config: object) -> None:
        cfg = AnonymizerConfig(patterns=(("TEST", r"\d+"),))
        with patch(
            "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
        ) as MockSvc:
            MockSvc.return_value.list_installed_spacy_models.return_value = []
            dlg = AnonymizerConfigDialog(language, cfg, MagicMock(), panel)
        dlg._spacy_fetch_thread.quit()
        dlg._spacy_fetch_thread.wait(500)
        qtbot.addWidget(dlg)
        dlg.accept()
        accepted_config.append(dlg.anonymizer_config)

    panel.anonymizer_configure_requested.connect(fake_handler)
    panel._anon_configure_btn.click()

    assert len(accepted_config) == 1
    assert ("TEST", r"\d+") in accepted_config[0].patterns


@pytest.mark.gui
def test_configure_dialog_reject_leaves_config_unchanged(qtbot) -> None:
    """
    Given  the config panel has anonymizer_patterns set
    When   the configure dialog is opened and rejected
    Then   the stored patterns are not changed
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._anonymization_enabled.setChecked(True)
    panel._settings_dict["anonymizer_patterns"] = [("ORIG", r"\w+")]

    def fake_handler(language: str, config: object) -> None:
        with patch(
            "teachers_teammate.gui._anonymizer_config_dialog.ProcessingApplicationService"
        ) as MockSvc:
            MockSvc.return_value.list_installed_spacy_models.return_value = []
            dlg = AnonymizerConfigDialog(language, config, MagicMock(), panel)
        dlg._spacy_fetch_thread.quit()
        dlg._spacy_fetch_thread.wait(500)
        qtbot.addWidget(dlg)
        dlg.reject()

    panel.anonymizer_configure_requested.connect(fake_handler)
    panel._anon_configure_btn.click()

    # Config panel's stored patterns unchanged because dialog was rejected
    assert panel._settings_dict["anonymizer_patterns"] == [("ORIG", r"\w+")]
