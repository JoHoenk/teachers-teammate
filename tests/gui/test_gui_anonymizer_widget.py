"""GUI tests for AnonymizerConfigWidget (the rules of Settings → Privacy) using pytest-qt."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.gui._anonymizer_config_widget import AnonymizerConfigWidget
from teachers_teammate.config import SECONDARY_MODEL_AUTO
from teachers_teammate.infrastructure.anonymizer import AnonymizerConfig, DEFAULT_PATTERNS


def _make_dialog(qtbot, config=None, app_service=None, sample_text=""):
    """Create an AnonymizerConfigWidget with spaCy model fetching suppressed.

    Patches ``ProcessingApplicationService`` to return ``[]`` from
    ``list_installed_spacy_models`` and immediately quits the background thread
    so tests are deterministic.
    """
    svc = app_service or MagicMock()
    cfg = config if config is not None else AnonymizerConfig()
    with patch(
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = []
        dlg = AnonymizerConfigWidget("English", cfg, svc, sample_text=sample_text)
    # Stop the thread to avoid teardown races
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    return dlg


# ── Patterns can be saved ────────────────────────────────────────────────────────


@pytest.mark.gui
def test_dialog_ok_disabled_when_regex_invalid(qtbot) -> None:
    """
    Given  a dialog constructed with a config containing an invalid regex pattern
    When   the dialog is shown
    Then   the rules report that they cannot be saved (is_valid() is False)
    """
    cfg = AnonymizerConfig(patterns=(("BAD", "[unclosed"),))
    dlg = _make_dialog(qtbot, config=cfg)

    assert not dlg.is_valid()


@pytest.mark.gui
def test_dialog_ok_enabled_when_all_regex_valid(qtbot) -> None:
    """
    Given  a dialog constructed with a valid regex pattern
    When   the dialog is shown
    Then   the rules report that they can be saved
    """
    cfg = AnonymizerConfig(patterns=(("ID", r"\d+"),))
    dlg = _make_dialog(qtbot, config=cfg)

    assert dlg.is_valid()


@pytest.mark.gui
def test_dialog_ok_enabled_when_no_patterns(qtbot) -> None:
    """
    Given  a dialog with no patterns at all
    When   the dialog is shown
    Then   the rules can be saved (nothing invalid)
    """
    cfg = AnonymizerConfig(patterns=())
    dlg = _make_dialog(qtbot, config=cfg)

    assert dlg.is_valid()


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
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = installed
        dlg = AnonymizerConfigWidget("English", cfg, MagicMock())
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
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = []
        dlg = AnonymizerConfigWidget("English", cfg, MagicMock())
    dlg._spacy_fetch_thread.quit()
    dlg._spacy_fetch_thread.wait(500)
    qtbot.addWidget(dlg)
    dlg._on_spacy_models_fetched([])

    assert dlg._secondary_model.currentData() is None


@pytest.mark.gui
def test_dialog_secondary_model_auto_entry_is_first(qtbot) -> None:
    """
    Given  a dialog whose saved choice follows the system language
    When   the fetch completes
    Then   the first entry is the automatic choice and it is selected
    """
    cfg = AnonymizerConfig(secondary_model=SECONDARY_MODEL_AUTO)
    dlg = _make_dialog(qtbot, config=cfg)
    dlg._on_spacy_models_fetched(["de_core_news_sm"])

    assert dlg._secondary_model.itemData(0) == SECONDARY_MODEL_AUTO
    assert dlg._secondary_model.currentData() == SECONDARY_MODEL_AUTO
    assert dlg.anonymizer_config.secondary_model == SECONDARY_MODEL_AUTO


@pytest.mark.gui
def test_dialog_secondary_auto_entry_names_the_system_model(qtbot) -> None:
    """
    Given  the system language maps to a model other than the document language's
    When   the fetch completes
    Then   the automatic entry says which model it will use
    """
    dlg = _make_dialog(qtbot)
    with patch.object(
        AnonymizerConfigWidget, "_system_spacy_model", return_value="de_core_news_sm"
    ):
        dlg._on_spacy_models_fetched(["de_core_news_sm"])

    assert "de_core_news_sm" in dlg._secondary_model.itemText(0)


@pytest.mark.gui
def test_dialog_secondary_auto_entry_says_none_for_the_same_language(qtbot) -> None:
    """
    Given  the system language is the document language
    When   the fetch completes
    Then   the automatic entry says no extra model is used
    """
    dlg = _make_dialog(qtbot)
    # English document, English system: the primary model already covers it.
    with patch.object(AnonymizerConfigWidget, "_system_spacy_model", return_value="en_core_web_sm"):
        dlg._on_spacy_models_fetched(["en_core_web_sm"])

    assert "same as document language" in dlg._secondary_model.itemText(0)


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
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = installed
        dlg = AnonymizerConfigWidget("English", cfg, MagicMock())
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
    dlg._secondary_model.setCurrentIndex(dlg._secondary_model.findData(None))  # "(none)"

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
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = []
        dlg = AnonymizerConfigWidget("English", cfg, svc)
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
    """
    Given  a dialog with custom patterns
    When   defaults are restored
    Then   the table holds the defaults
    """
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
    Then   the rules still cannot be saved regardless of collapse state
    """
    cfg = AnonymizerConfig(patterns=(("BAD", "[unclosed"),))
    dlg = _make_dialog(qtbot, config=cfg)

    assert not dlg.is_valid()


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
    """
    Given  anonymize_preview raises ImportError
    When   previewing
    Then   a spaCy-missing error is shown
    """
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
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.return_value = ["xx_ent_wiki_sm"]
        dlg._on_model_downloaded("xx_ent_wiki_sm")
        dlg._spacy_fetch_thread.quit()
        dlg._spacy_fetch_thread.wait(500)
        # Simulate the async fetch completing
        dlg._on_spacy_models_fetched(["xx_ent_wiki_sm"])

    assert dlg._secondary_model.findData("xx_ent_wiki_sm") >= 0
    assert dlg._primary_model.findData("xx_ent_wiki_sm") >= 0


# ── ConfigPanel → Settings → Privacy → ConfigPanel ───────────────────────────


@pytest.mark.gui
def test_privacy_rules_button_asks_for_the_privacy_page(qtbot) -> None:
    """
    Given  anonymization is enabled on the config panel
    When   the "Privacy rules…" button is clicked
    Then   the panel asks for the rules of its document language and current anonymizer config
           (the main window opens Settings → Privacy on the detection tab for it)
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._anonymization_enabled.setChecked(True)

    with qtbot.waitSignal(panel.anonymizer_configure_requested, timeout=3000) as blocker:
        panel._anon_configure_btn.click()

    language, config = blocker.args
    assert language == panel.lookup_language()
    assert isinstance(config, AnonymizerConfig)


@pytest.mark.gui
def test_changed_rules_update_the_config_panel(qtbot) -> None:
    """
    Given  a rules widget with a specific regex pattern
    When   its anonymizer_config is handed to the config panel
    Then   the panel's anonymizer config holds the new patterns
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    widget = _make_dialog(qtbot, config=AnonymizerConfig(patterns=(("TEST", r"\d+"),)))

    panel.update_anonymizer_config(widget.anonymizer_config)

    assert ("TEST", r"\d+") in panel.build_anonymizer_config().patterns


@pytest.mark.gui
def test_set_config_shows_another_config_in_the_rules(qtbot) -> None:
    """
    Given  a rules widget showing one pattern
    When   set_config() is called with another config (as "Restore defaults → This page" does)
    Then   the table shows the new patterns and validity is re-evaluated
    """
    dlg = _make_dialog(qtbot, config=AnonymizerConfig(patterns=(("BAD", "[unclosed"),)))
    assert not dlg.is_valid()
    validity: list[bool] = []
    dlg.validity_changed.connect(validity.append)

    dlg.set_config(AnonymizerConfig(patterns=(("ID", r"\d+"),)))

    assert dlg.patterns == [("ID", r"\d+")]
    assert dlg.is_valid()
    assert validity == [True]


@pytest.mark.gui
def test_validity_changed_is_emitted_only_when_validity_flips(qtbot) -> None:
    """
    Given  a rules widget with one valid pattern
    When   the pattern is broken, edited while still broken, and then fixed
    Then   validity_changed reports False once and True once
    """
    dlg = _make_dialog(qtbot, config=AnonymizerConfig(patterns=(("ID", r"\d+"),)))
    validity: list[bool] = []
    dlg.validity_changed.connect(validity.append)

    dlg._table.item(0, 1).setText("[open")
    dlg._table.item(0, 1).setText("[open2")
    dlg._table.item(0, 1).setText(r"\d+")

    assert validity == [False, True]


@pytest.mark.gui
def test_shutdown_parks_a_running_model_discovery(qtbot) -> None:
    """
    Given  a rules widget whose spaCy model discovery is still running
    When   shutdown() is called
    Then   the thread is not waited for and its late answer no longer reaches the widget
    """
    import threading  # noqa: PLC0415

    release = threading.Event()
    svc = MagicMock()
    with patch(
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService"
    ) as MockSvc:
        MockSvc.return_value.list_installed_spacy_models.side_effect = lambda: (
            release.wait(10) and []
        )
        dlg = AnonymizerConfigWidget("English", AnonymizerConfig(), svc)
        qtbot.addWidget(dlg)
        thread = dlg._spacy_fetch_thread
        assert thread is not None and thread.isRunning()

        dlg.shutdown()
        release.set()
        qtbot.waitUntil(thread.isFinished, timeout=5000)

    assert not dlg._primary_model.isEnabled()  # the late answer was not applied


@pytest.mark.gui
def test_primary_model_survives_save_and_reload(qtbot, tmp_path) -> None:
    """
    Given  an anonymizer config whose primary model was changed from Auto to German
    When   the settings are saved to ocr.toml and loaded into a fresh config panel
    Then   the primary model is restored and reaches both the pipeline Config and the dialog
    """
    from teachers_teammate.config import dump_config_file, load_config_file  # noqa: PLC0415
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    panel.set_input_dir(str(tmp_path / "in"))
    panel._settings_dict["output_dir"] = str(tmp_path / "out")
    panel.update_anonymizer_config(AnonymizerConfig(primary_model="de_core_news_sm"))
    config_path = tmp_path / "ocr.toml"
    config_path.write_text(dump_config_file(panel.to_dict()), encoding="utf-8")

    reloaded = ConfigPanel()
    qtbot.addWidget(reloaded)
    reloaded.load_from_dict(load_config_file(config_path))

    assert reloaded.build_anonymizer_config().primary_model == "de_core_news_sm"
    assert reloaded.to_config().anonymizer_primary_model == "de_core_news_sm"


@pytest.mark.gui
def test_primary_auto_is_not_persisted_as_a_model(qtbot, tmp_path) -> None:
    """
    Given  an anonymizer config whose primary model is Auto (None)
    When   it is written back into the config panel
    Then   no primary model is stored and the language decides the model again
    """
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    panel.set_input_dir(str(tmp_path / "in"))
    panel._settings_dict["output_dir"] = str(tmp_path / "out")
    panel.update_anonymizer_config(AnonymizerConfig(primary_model="de_core_news_sm"))
    panel.update_anonymizer_config(AnonymizerConfig(primary_model=None))

    assert panel.build_anonymizer_config().primary_model is None
    assert panel.to_config().anonymizer_primary_model is None


def _panel_with_dirs(qtbot, tmp_path):
    """Return a ConfigPanel with valid input and output folders."""
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    (tmp_path / "in").mkdir()
    (tmp_path / "out").mkdir()
    panel.set_input_dir(str(tmp_path / "in"))
    panel._settings_dict["output_dir"] = str(tmp_path / "out")
    return panel


@pytest.mark.gui
def test_earlier_default_patterns_resolve_to_current_defaults(qtbot, tmp_path) -> None:
    """
    Given  settings that still hold the earlier IBAN / EMAIL / PHONE pattern list
    When   the anonymizer config is built for the dialog, the export menu and the preview
    Then   it holds the current default patterns
    """
    from teachers_teammate.infrastructure.anonymizer import (  # noqa: PLC0415
        _LEGACY_DEFAULT_PATTERNS,
    )

    panel = _panel_with_dirs(qtbot, tmp_path)
    panel._settings_dict["anonymizer_patterns"] = list(_LEGACY_DEFAULT_PATTERNS)

    assert panel.build_anonymizer_config().patterns == tuple(DEFAULT_PATTERNS)


@pytest.mark.gui
def test_unchanged_default_patterns_are_stored_as_none(qtbot, tmp_path) -> None:
    """
    Given  a dialog result whose patterns are exactly the current defaults
    When   it is written back into the config panel
    Then   no pattern list is stored, so later changes to the defaults reach this user
    """
    panel = _panel_with_dirs(qtbot, tmp_path)
    panel._settings_dict["anonymizer_patterns"] = [("ORIG", r"\w+")]

    panel.update_anonymizer_config(AnonymizerConfig())

    assert panel._settings_dict["anonymizer_patterns"] is None
    assert panel.to_config().anonymizer_patterns is None


@pytest.mark.gui
def test_empty_pattern_list_survives_save_and_reload(qtbot, tmp_path) -> None:
    """
    Given  an anonymizer config with all regex patterns removed
    When   the settings are saved to ocr.toml and loaded into a fresh config panel
    Then   the list stays empty in the panel, the pipeline Config and the anonymizer config
    """
    from teachers_teammate.config import dump_config_file, load_config_file  # noqa: PLC0415
    from teachers_teammate.gui._config_panel import ConfigPanel  # noqa: PLC0415

    panel = _panel_with_dirs(qtbot, tmp_path)
    panel.update_anonymizer_config(AnonymizerConfig(patterns=()))
    assert panel.to_config().anonymizer_patterns == []
    config_path = tmp_path / "ocr.toml"
    config_path.write_text(dump_config_file(panel.to_dict()), encoding="utf-8")

    reloaded = ConfigPanel()
    qtbot.addWidget(reloaded)
    reloaded.load_from_dict(load_config_file(config_path))

    assert reloaded.build_anonymizer_config().patterns == ()
    assert reloaded.to_config().anonymizer_patterns == []
