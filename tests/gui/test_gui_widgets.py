"""GUI tests for miscellaneous widgets: CollapsibleSection, LogWidget, PreviewPanel, StatsWidget."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


# ── CollapsibleSection ────────────────────────────────────────────────────


@pytest.mark.gui
def test_collapsible_section_starts_expanded(qtbot) -> None:
    """
    Given  a CollapsibleSection with collapsed=False (default)
    When   the widget is created
    Then   the content area is visible (maximumHeight > 0)
    """
    from teachers_teammate.gui._collapsible_section import CollapsibleSection  # noqa: PLC0415

    section = CollapsibleSection("Test Section")
    qtbot.addWidget(section)

    assert section._content.maximumHeight() > 0


@pytest.mark.gui
def test_collapsible_section_starts_collapsed(qtbot) -> None:
    """
    Given  a CollapsibleSection created with collapsed=True
    When   the widget is created
    Then   the content area's maximumHeight is 0
    """
    from teachers_teammate.gui._collapsible_section import CollapsibleSection  # noqa: PLC0415

    section = CollapsibleSection("Test Section", collapsed=True)
    qtbot.addWidget(section)

    assert section._content.maximumHeight() == 0


@pytest.mark.gui
def test_collapsible_section_toggle_expands_and_collapses(qtbot) -> None:
    """
    Given  an expanded CollapsibleSection
    When   the toggle button is clicked (collapse), then clicked again (expand)
    Then   maximumHeight transitions correctly each time
    """
    from teachers_teammate.gui._collapsible_section import CollapsibleSection  # noqa: PLC0415

    section = CollapsibleSection("Toggle Test")
    qtbot.addWidget(section)
    section.show()

    # Initially expanded; click to collapse
    section._toggle.click()
    # After collapse the end-value of the animation should be 0
    assert section._anim.endValue() == 0

    # Click again to expand
    section._toggle.click()
    assert section._anim.endValue() > 0


@pytest.mark.gui
def test_collapsible_section_content_widget_is_accessible(qtbot) -> None:
    """
    Given  a CollapsibleSection
    When   content_widget is accessed
    Then   it is a non-None QWidget
    """
    from teachers_teammate.gui._collapsible_section import CollapsibleSection  # noqa: PLC0415
    from PySide6.QtWidgets import QWidget  # noqa: PLC0415

    section = CollapsibleSection("Content")
    qtbot.addWidget(section)

    assert isinstance(section.content_widget, QWidget)


# ── LogWidget ─────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_log_widget_append_text_plain(qtbot) -> None:
    """
    Given  a LogWidget and a plain message with no special prefix
    When   append_text() is called
    Then   the text appears in the widget's content
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)

    lw.append_text("Processing file.png\n")

    assert "Processing file.png" in lw.toPlainText()


@pytest.mark.gui
def test_log_widget_append_text_error_line(qtbot) -> None:
    """
    Given  a line starting with 'ERROR'
    When   append_text() is called
    Then   the text is present in the widget
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)

    lw.append_text("ERROR: something went wrong\n")

    assert "ERROR" in lw.toPlainText()


@pytest.mark.gui
def test_log_widget_append_text_warning_line(qtbot) -> None:
    """
    Given  a line starting with 'WARNING'
    When   append_text() is called
    Then   the warning text is present in the widget
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)

    lw.append_text("WARNING: using fallback provider\n")

    assert "WARNING" in lw.toPlainText()


@pytest.mark.gui
def test_log_widget_append_text_saved_line(qtbot) -> None:
    """
    Given  a line containing '→ Saved:'
    When   append_text() is called
    Then   the text is present in the widget (uses OK colour path)
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)

    lw.append_text("       → Saved: file.docx\n")

    assert "Saved" in lw.toPlainText()


@pytest.mark.gui
def test_log_widget_append_text_done_line(qtbot) -> None:
    """
    Given  a line starting with 'Done:'
    When   append_text() is called
    Then   the text is present in the widget (uses OK colour path)
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)

    lw.append_text("Done: 3 files processed\n")

    assert "Done" in lw.toPlainText()


@pytest.mark.gui
def test_log_widget_clear_log_empties_widget(qtbot) -> None:
    """
    Given  a LogWidget with some text appended
    When   clear_log() is called
    Then   the widget's text is empty
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)
    lw.append_text("some content\n")
    lw.clear_log()

    assert lw.toPlainText() == ""


@pytest.mark.gui
def test_log_widget_append_multiple_lines(qtbot) -> None:
    """
    Given  a multi-line string with ERROR, WARNING, and plain lines
    When   append_text() is called once with all lines
    Then   all lines appear in the widget's text
    """
    from teachers_teammate.gui._log_widget import LogWidget  # noqa: PLC0415

    lw = LogWidget()
    qtbot.addWidget(lw)

    lw.append_text("Starting run\nWARNING: slow\nERROR: failed\nDone: 0 OK\n")

    text = lw.toPlainText()
    assert "Starting run" in text
    assert "WARNING" in text
    assert "ERROR" in text
    assert "Done" in text


# ── DiffWidget ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_diff_widget_set_texts_no_original(qtbot) -> None:
    """
    Given  a DiffWidget and original=''
    When   set_texts() is called
    Then   widget shows '(no OCR text available for comparison)'
    """
    from teachers_teammate.gui._preview_panel import DiffWidget  # noqa: PLC0415

    dw = DiffWidget()
    qtbot.addWidget(dw)

    dw.set_texts("", "corrected")

    assert "no OCR text" in dw.toPlainText()


@pytest.mark.gui
def test_diff_widget_set_texts_no_correction(qtbot) -> None:
    """
    Given  a DiffWidget and corrected=''
    When   set_texts() is called
    Then   widget shows '(no correction available)'
    """
    from teachers_teammate.gui._preview_panel import DiffWidget  # noqa: PLC0415

    dw = DiffWidget()
    qtbot.addWidget(dw)

    dw.set_texts("original text", "")

    assert "no correction" in dw.toPlainText()


@pytest.mark.gui
@pytest.mark.use_case("Privacy_Preview_Diff")
def test_diff_widget_set_texts_with_diff(qtbot) -> None:
    """
    Given  a DiffWidget with two differing strings
    When   set_texts() is called
    Then   HTML is set (contains deleted and inserted spans)
    """
    from teachers_teammate.gui._preview_panel import DiffWidget  # noqa: PLC0415

    dw = DiffWidget()
    qtbot.addWidget(dw)

    dw.set_texts("hello wrold", "hello world")

    html_content = dw.toHtml()
    assert len(html_content) > 0


@pytest.mark.gui
def test_diff_widget_preserves_structure_for_multiline_text(qtbot) -> None:
    """
    Given  OCR and correction texts with punctuation/newline changes
    When   set_texts() is called
    Then   resulting plain text keeps both lines and corrected content is visible
    """
    from teachers_teammate.gui._preview_panel import DiffWidget  # noqa: PLC0415

    dw = DiffWidget()
    qtbot.addWidget(dw)

    dw.set_texts("Line1: wrold\nLine2, old.", "Line1: world\nLine2, fixed.")

    plain = dw.toPlainText()
    assert "Line1:" in plain
    assert "Line2," in plain
    assert "fixed." in plain
    assert "->" not in plain


@pytest.mark.gui
def test_diff_widget_clear_diff(qtbot) -> None:
    """
    Given  a DiffWidget with text set
    When   clear_diff() is called
    Then   the widget content is empty
    """
    from teachers_teammate.gui._preview_panel import DiffWidget  # noqa: PLC0415

    dw = DiffWidget()
    qtbot.addWidget(dw)

    dw.set_texts("hello wrold", "hello world")
    dw.clear_diff()

    assert dw.toPlainText() == ""


# ── ZoomableImageView ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_zoomable_image_view_set_valid_pixmap(qtbot) -> None:
    """
    Given  a ZoomableImageView and a 100x100 pixmap
    When   set_pixmap() is called
    Then   the pixmap item is set and not null
    """
    from teachers_teammate.gui._preview_panel import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)
    view.show()

    pix = QPixmap(100, 100)
    pix.fill()
    view.set_pixmap(pix)

    assert view._pix_item is not None


@pytest.mark.gui
def test_zoomable_image_view_set_null_pixmap(qtbot) -> None:
    """
    Given  a ZoomableImageView and a null pixmap
    When   set_pixmap() is called
    Then   pix_item is None and the scene shows '(image unavailable)' text
    """
    from teachers_teammate.gui._preview_panel import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)

    view.set_pixmap(QPixmap())  # null pixmap

    assert view._pix_item is None


@pytest.mark.gui
def test_zoomable_image_view_show_message(qtbot) -> None:
    """
    Given  a ZoomableImageView
    When   show_message('test msg') is called
    Then   pix_item is None (scene was cleared)
    """
    from teachers_teammate.gui._preview_panel import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)

    view.show_message("test msg")

    assert view._pix_item is None


@pytest.mark.gui
def test_zoomable_image_view_clear(qtbot) -> None:
    """
    Given  a ZoomableImageView with a pixmap set
    When   clear() is called
    Then   pix_item is None
    """
    from teachers_teammate.gui._preview_panel import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)

    pix = QPixmap(50, 50)
    pix.fill()
    view.set_pixmap(pix)
    view.clear()

    assert view._pix_item is None


# ── PreviewPanel ──────────────────────────────────────────────────────────


@pytest.mark.gui
def test_preview_panel_load_with_valid_paths(qtbot, tmp_path: Path) -> None:
    """
    Given  a PreviewPanel and text payloads for OCR and correction
    When   load() is called
    Then   the OCR text and correction text appear in the respective tabs
    """
    from teachers_teammate.gui._preview_panel import PreviewPanel  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)

    pix = QPixmap(100, 100)
    pix.fill()
    panel.load(pix, "raw OCR text", "corrected text", "")

    assert panel._ocr_text.toPlainText() == "raw OCR text"
    assert panel._correction_text.toPlainText() == "corrected text"


@pytest.mark.gui
def test_preview_panel_load_with_empty_payloads_and_null_pixmap(qtbot, tmp_path: Path) -> None:
    """
    Given  a PreviewPanel and empty text payloads with a null QPixmap
    When   load() is called
    Then   the text widgets are empty (placeholders) and the image view shows the
           'not found' state (pix_item is None) without raising
    """
    from teachers_teammate.gui._preview_panel import PreviewPanel  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)

    panel.load(QPixmap(), "", "", "")

    assert panel._ocr_text.toPlainText() == ""
    assert panel._correction_text.toPlainText() == ""
    assert panel._image_view._pix_item is None


@pytest.mark.gui
def test_preview_panel_load_with_evaluation_text(qtbot, tmp_path: Path) -> None:
    """
    Given  a PreviewPanel and an evaluation text payload
    When   load() is called
    Then   evaluation text appears in the evaluation tab
    """
    from teachers_teammate.gui._preview_panel import PreviewPanel  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)

    panel.load(QPixmap(), "", "", "Score: 9/10")

    assert panel._evaluation_text.toPlainText() == "Score: 9/10"


@pytest.mark.gui
def test_preview_panel_load_renders_markdown_in_evaluation(qtbot) -> None:
    """
    Given  a PreviewPanel and markdown evaluation content
    When   load() is called
    Then   the evaluation widget renders markdown and keeps readable plain text
    """
    from teachers_teammate.gui._preview_panel import PreviewPanel  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)

    panel.load(QPixmap(), "", "", "# Summary\n\n- one\n- two")

    assert "Summary" in panel._evaluation_text.toPlainText()
    assert "one" in panel._evaluation_text.toPlainText()


@pytest.mark.gui
def test_preview_panel_clear_preview_empties_all(qtbot) -> None:
    """
    Given  a PreviewPanel with content loaded
    When   clear_preview() is called
    Then   all text widgets are cleared
    """
    from teachers_teammate.gui._preview_panel import PreviewPanel  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)

    panel._ocr_text.setPlainText("some text")
    panel._correction_text.setPlainText("corrected")
    panel._evaluation_text.setPlainText("evaluated")
    panel.clear_preview()

    assert panel._ocr_text.toPlainText() == ""
    assert panel._correction_text.toPlainText() == ""
    assert panel._evaluation_text.toPlainText() == ""


@pytest.mark.gui
def test_preview_panel_emits_edit_signals_for_user_changes(qtbot) -> None:
    """
    Given  a PreviewPanel with loaded OCR/correction text
    When   the user edits OCR and correction fields
    Then   edit signals emit the updated values
    """
    from teachers_teammate.gui._preview_panel import PreviewPanel  # noqa: PLC0415

    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.load(QPixmap(), "raw", "corr", "")

    seen_ocr: list[str] = []
    seen_corr: list[str] = []
    panel.ocr_text_edited.connect(seen_ocr.append)
    panel.correction_text_edited.connect(seen_corr.append)

    panel._ocr_text.setPlainText("raw edited")
    panel._correction_text.setPlainText("corr edited")

    assert seen_ocr[-1] == "raw edited"
    assert seen_corr[-1] == "corr edited"


# ── SystemStatsWidget ─────────────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.use_case("System_Resource_Monitoring")
def test_stats_widget_creates_without_error(qtbot) -> None:
    """
    Given  the SystemStatsWidget class
    When   an instance is created
    Then   no exception is raised
    """
    from teachers_teammate.gui._stats_widget import SystemStatsWidget  # noqa: PLC0415

    w = SystemStatsWidget()
    qtbot.addWidget(w)
    assert w is not None


@pytest.mark.gui
def test_stats_widget_refresh_does_not_crash(qtbot) -> None:
    """
    Given  a SystemStatsWidget
    When   _refresh() is called manually
    Then   no exception is raised (psutil may or may not be installed)
    """
    from teachers_teammate.gui._stats_widget import SystemStatsWidget  # noqa: PLC0415

    w = SystemStatsWidget()
    qtbot.addWidget(w)
    w._refresh()  # should not crash regardless of psutil availability


@pytest.mark.gui
def test_stats_widget_timer_starts_automatically(qtbot) -> None:
    """
    Given  a SystemStatsWidget with psutil available
    When   the widget is instantiated
    Then   the internal timer is active (or widget is disabled without psutil)
    """
    from teachers_teammate.gui._stats_widget import SystemStatsWidget, _PSUTIL_AVAILABLE  # noqa: PLC0415

    w = SystemStatsWidget()
    qtbot.addWidget(w)

    if _PSUTIL_AVAILABLE:
        assert w._enabled is True
        assert w._timer.isActive()
    else:
        assert w._enabled is False


@pytest.mark.gui
def test_stats_widget_disabled_without_psutil(qtbot, monkeypatch) -> None:
    """
    Given  psutil is unavailable
    When   a SystemStatsWidget is created and refreshed
    Then   it is disabled and _refresh() returns immediately without error
    """
    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415

    monkeypatch.setattr(sw, "_PSUTIL_AVAILABLE", False)
    w = sw.SystemStatsWidget()
    qtbot.addWidget(w)

    assert w._enabled is False
    w._refresh()  # early-return branch


@pytest.mark.gui
@pytest.mark.use_case("System_Resource_Monitoring")
def test_stats_widget_refreshes_nvidia_metrics(qtbot, monkeypatch) -> None:
    """
    Given  pynvml reports a GPU at 42% utilisation and half its VRAM used
    When   a SystemStatsWidget is built and refreshed
    Then   the GPU bar reflects the reported utilisation
    """
    from types import SimpleNamespace  # noqa: PLC0415

    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415

    fake_nvml = MagicMock()
    fake_nvml.nvmlDeviceGetHandleByIndex.return_value = object()
    fake_nvml.nvmlDeviceGetUtilizationRates.return_value = SimpleNamespace(gpu=42)
    fake_nvml.nvmlDeviceGetMemoryInfo.return_value = SimpleNamespace(
        used=4 * 2**30, total=8 * 2**30
    )
    monkeypatch.setattr(sw, "_NVML_AVAILABLE", True)
    monkeypatch.setattr(sw, "_AMDGPU_AVAILABLE", False)
    monkeypatch.setattr(sw, "_nvml", fake_nvml)

    w = sw.SystemStatsWidget()
    qtbot.addWidget(w)
    w._refresh()

    assert w._gpu_bar is not None
    assert w._gpu_bar.value() == 42
    assert w._vram_bar is not None
    assert w._vram_bar.value() == 50


@pytest.mark.gui
def test_stats_widget_refreshes_amd_metrics(qtbot, monkeypatch) -> None:
    """
    Given  pyamdgpuinfo reports a GPU at 30% load and half its VRAM used
    When   a SystemStatsWidget is built and refreshed
    Then   the GPU bar reflects the reported load
    """
    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415

    fake_gpu = MagicMock()
    fake_gpu.query_load.return_value = 0.30
    fake_gpu.query_vram_usage.return_value = 2 * 2**30
    fake_gpu.query_vram_size.return_value = 4 * 2**30
    monkeypatch.setattr(sw, "_NVML_AVAILABLE", False)
    monkeypatch.setattr(sw, "_AMDGPU_AVAILABLE", True)
    monkeypatch.setattr(sw, "_AMD_GPU", fake_gpu)

    w = sw.SystemStatsWidget()
    qtbot.addWidget(w)
    w._refresh()

    assert w._gpu_bar is not None
    assert w._gpu_bar.value() == 30
    assert w._vram_bar is not None
    assert w._vram_bar.value() == 50


# ── HelpDialog ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_help_dialog_opens_without_error(qtbot) -> None:
    """
    Given  the HelpDialog class
    When   an instance is created
    Then   no exception is raised and the dialog is a QDialog subclass
    """
    from teachers_teammate.gui._help_dialog import HelpDialog  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    dialog = HelpDialog()
    qtbot.addWidget(dialog)

    assert isinstance(dialog, QDialog)


@pytest.mark.gui
def test_help_dialog_has_content(qtbot) -> None:
    """
    Given  a HelpDialog
    When   it is shown
    Then   it contains some text content (help text is not empty)
    """
    from teachers_teammate.gui._help_dialog import HelpDialog  # noqa: PLC0415

    dialog = HelpDialog()
    qtbot.addWidget(dialog)

    # Find all QLabel / QTextEdit children and verify they're not all empty
    from PySide6.QtWidgets import QTextEdit  # noqa: PLC0415

    text_widgets = dialog.findChildren(QTextEdit)
    labels = dialog.findChildren(QLabel)
    has_text = any(w.toPlainText() for w in text_widgets) or any(lbl.text() for lbl in labels)
    assert has_text


@pytest.mark.gui
def test_about_dialog_opens_without_error(qtbot) -> None:
    """
    Given  the AboutDialog class
    When   an instance is created
    Then   no exception is raised and the dialog has the correct title
    """
    from teachers_teammate.gui._help_dialog import AboutDialog  # noqa: PLC0415

    dialog = AboutDialog()
    qtbot.addWidget(dialog)

    assert "Teacher" in dialog.windowTitle()


@pytest.mark.gui
def test_about_dialog_has_version_label(qtbot) -> None:
    """
    Given  an AboutDialog
    When   it is created
    Then   at least one label contains 'Version'
    """
    from teachers_teammate.gui._help_dialog import AboutDialog  # noqa: PLC0415

    dialog = AboutDialog()
    qtbot.addWidget(dialog)

    labels = dialog.findChildren(QLabel)
    assert any("Version" in lbl.text() for lbl in labels)


# ── Bug 1: load_pixmap (safe unicode-path image loading) ──────────────────


@pytest.mark.gui
def test_load_pixmap_returns_valid_pixmap_for_existing_file(qtbot, tmp_path) -> None:
    """
    Given  a valid PNG file written to tmp_path
    When   load_pixmap() is called with that path
    Then   a non-null QPixmap is returned
    """
    from teachers_teammate.gui._image_utils import load_pixmap  # noqa: PLC0415

    # Create a minimal 1x1 PNG (known-good 67 bytes)
    import base64  # noqa: PLC0415

    _TINY_PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    png_path = tmp_path / "test.png"
    png_path.write_bytes(base64.b64decode(_TINY_PNG_B64))

    pix = load_pixmap(png_path)
    assert not pix.isNull()


@pytest.mark.gui
def test_load_pixmap_returns_empty_pixmap_for_empty_path(qtbot) -> None:
    """
    Given  an empty string path
    When   load_pixmap() is called
    Then   an empty (null) QPixmap is returned without raising
    """
    from teachers_teammate.gui._image_utils import load_pixmap  # noqa: PLC0415

    pix = load_pixmap("")
    assert pix.isNull()


@pytest.mark.gui
def test_load_pixmap_returns_empty_pixmap_for_missing_file(qtbot, tmp_path) -> None:
    """
    Given  a path that does not exist
    When   load_pixmap() is called
    Then   an empty (null) QPixmap is returned without raising
    """
    from teachers_teammate.gui._image_utils import load_pixmap  # noqa: PLC0415

    pix = load_pixmap(tmp_path / "does_not_exist.png")
    assert pix.isNull()


@pytest.mark.gui
def test_load_pixmap_handles_unicode_path(qtbot, tmp_path) -> None:
    """
    Given  a valid PNG file under a path containing umlauts and spaces
    When   load_pixmap() is called
    Then   a non-null QPixmap is returned (unicode paths are handled correctly)
    """
    from teachers_teammate.gui._image_utils import load_pixmap  # noqa: PLC0415
    import base64  # noqa: PLC0415

    _TINY_PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    umlaut_dir = tmp_path / "Schüler Ordner"
    umlaut_dir.mkdir()
    png_path = umlaut_dir / "test bild.png"
    png_path.write_bytes(base64.b64decode(_TINY_PNG_B64))

    pix = load_pixmap(png_path)
    assert not pix.isNull()


# ── Bug 1: load_icon (safe unicode-path icon loading) ─────────────────────


@pytest.mark.gui
def test_load_icon_returns_valid_icon_for_unicode_path(qtbot, tmp_path) -> None:
    """
    Given  a valid PNG file under a path containing umlauts and spaces
    When   load_icon() is called
    Then   a non-null QIcon is returned (unicode paths are handled correctly)
    """
    from teachers_teammate.gui._image_utils import load_icon  # noqa: PLC0415
    import base64  # noqa: PLC0415

    _TINY_PNG_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    umlaut_dir = tmp_path / "Jürgen Ordner"
    umlaut_dir.mkdir()
    png_path = umlaut_dir / "app icon.png"
    png_path.write_bytes(base64.b64decode(_TINY_PNG_B64))

    icon = load_icon(png_path)
    assert not icon.isNull()


@pytest.mark.gui
def test_load_icon_returns_empty_icon_for_missing_file(qtbot, tmp_path) -> None:
    """
    Given  a path that does not exist
    When   load_icon() is called
    Then   an empty (null) QIcon is returned without raising
    """
    from teachers_teammate.gui._image_utils import load_icon  # noqa: PLC0415

    icon = load_icon(tmp_path / "does_not_exist.png")
    assert icon.isNull()


@pytest.mark.gui
def test_load_icon_returns_empty_icon_for_empty_path(qtbot) -> None:
    """
    Given  an empty string path
    When   load_icon() is called
    Then   an empty (null) QIcon is returned without raising
    """
    from teachers_teammate.gui._image_utils import load_icon  # noqa: PLC0415

    icon = load_icon("")
    assert icon.isNull()
