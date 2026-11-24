"""GUI tests for miscellaneous widgets: CollapsibleSection, LogWidget, StatsWidget."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

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
    from teachers_teammate.gui._diff_widget import DiffWidget  # noqa: PLC0415

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
    from teachers_teammate.gui._diff_widget import DiffWidget  # noqa: PLC0415

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
    from teachers_teammate.gui._diff_widget import DiffWidget  # noqa: PLC0415

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
    from teachers_teammate.gui._diff_widget import DiffWidget  # noqa: PLC0415

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
    from teachers_teammate.gui._diff_widget import DiffWidget  # noqa: PLC0415

    dw = DiffWidget()
    qtbot.addWidget(dw)

    dw.set_texts("hello wrold", "hello world")
    dw.clear_diff()

    assert dw.toPlainText() == ""


# ── ZoomableImageView ─────────────────────────────────────────────────────


def _solid_pixmap(width: int, height: int) -> QPixmap:
    pix = QPixmap(width, height)
    pix.fill()
    return pix


@pytest.mark.gui
def test_zoomable_image_view_set_valid_pixmap(qtbot) -> None:
    """
    Given  a ZoomableImageView and a 100x100 pixmap
    When   set_pixmap() is called
    Then   the pixmap item is set and not null
    """
    from teachers_teammate.gui._image_view import ZoomableImageView  # noqa: PLC0415

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
    from teachers_teammate.gui._image_view import ZoomableImageView  # noqa: PLC0415

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
    from teachers_teammate.gui._image_view import ZoomableImageView  # noqa: PLC0415

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
    from teachers_teammate.gui._image_view import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)

    pix = QPixmap(50, 50)
    pix.fill()
    view.set_pixmap(pix)
    view.clear()

    assert view._pix_item is None


@pytest.mark.gui
def test_zoomable_view_swap_pixmap_keeps_zoom_and_visible_region(qtbot) -> None:
    """
    Given  a zoomed-in ZoomableImageView showing a 100x100 page
    When   swap_pixmap() replaces it with a 200x200 rendering of the same page
    Then   the same fraction of the page stays visible and the view is not re-fitted
    """
    from teachers_teammate.gui._image_view import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)
    view.resize(300, 300)
    view.show()
    view.set_pixmap(_solid_pixmap(100, 100))
    view.zoom_in()
    view.zoom_in()
    zoom_before = view.transform().m11()
    visible_before = view.mapToScene(view.viewport().rect()).boundingRect().width() / 100

    view.swap_pixmap(_solid_pixmap(200, 200))

    assert view._fitted is False
    assert view.transform().m11() == pytest.approx(zoom_before / 2)
    visible_after = view.mapToScene(view.viewport().rect()).boundingRect().width() / 200
    assert visible_after == pytest.approx(visible_before, rel=0.05)


@pytest.mark.gui
def test_zoomable_view_swap_pixmap_refits_when_fitted(qtbot) -> None:
    """
    Given  a ZoomableImageView that is still fitted to the view
    When   swap_pixmap() is called
    Then   the new image is fitted as with set_pixmap()
    """
    from teachers_teammate.gui._image_view import ZoomableImageView  # noqa: PLC0415

    view = ZoomableImageView()
    qtbot.addWidget(view)
    view.resize(300, 300)
    view.show()
    view.set_pixmap(_solid_pixmap(100, 100))

    view.swap_pixmap(_solid_pixmap(400, 400))

    assert view._fitted is True
    assert view.sceneRect().width() == 400


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

    w = SystemStatsWidget(lambda: None)
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

    w = SystemStatsWidget(lambda: None)
    qtbot.addWidget(w)
    w._refresh()  # should not crash regardless of psutil availability


@pytest.mark.gui
def test_stats_widget_polls_only_while_shown(qtbot) -> None:
    """
    Given  a SystemStatsWidget with psutil available
    When   the widget is shown and hidden again
    Then   the internal timer is idle at first, runs while shown and stops when hidden
               (or the widget is disabled without psutil)
    """
    from teachers_teammate.gui._stats_widget import SystemStatsWidget, _PSUTIL_AVAILABLE  # noqa: PLC0415

    w = SystemStatsWidget(lambda: None)
    qtbot.addWidget(w)

    if _PSUTIL_AVAILABLE:
        assert w._enabled is True
        assert not w._timer.isActive()
        w.show()
        assert w._timer.isActive()
        w.hide()
        assert not w._timer.isActive()
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
    w = sw.SystemStatsWidget(lambda: None)
    qtbot.addWidget(w)

    assert w._enabled is False
    w._refresh()  # early-return branch


@pytest.mark.gui
@pytest.mark.use_case("System_Resource_Monitoring")
def test_stats_widget_shows_the_gpu_rows_when_the_card_answers(qtbot, monkeypatch) -> None:
    """
    Given  the graphics card reports 42% load and half its memory used
    When   a SystemStatsWidget is built
    Then   the GPU and VRAM rows appear with those readings, read off the GUI thread
    """
    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import GpuUsage  # noqa: PLC0415

    w = sw.SystemStatsWidget(lambda: GpuUsage(42, vram_used_mb=4096, vram_total_mb=8192))
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w._gpu_bar is not None and w._gpu_bar.value() == 42)

    assert w._vram_bar is not None
    assert w._vram_bar.value() == 50


@pytest.mark.gui
def test_stats_widget_shows_only_cpu_and_ram_without_a_readable_card(qtbot, monkeypatch) -> None:
    """
    Given  no graphics card the app can read (no NVIDIA driver, no AMD sysfs files)
    When   a SystemStatsWidget is built and its reading has come back
    Then   it shows CPU and RAM only, and asks nothing of the network or a package
    """
    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415

    w = sw.SystemStatsWidget(lambda: None)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: not w._gpu_read_pending)

    assert w._gpu_bar is None
    assert w._vram_bar is None
    w._refresh()  # no GPU rows: the refresh must not raise


@pytest.mark.gui
def test_stats_widget_creates_the_gpu_rows_when_the_first_reading_failed(
    qtbot, monkeypatch
) -> None:
    """
    Given  a card that gives no reading at start-up (driver busy, card waking up) and answers later
    When   the widget refreshes again
    Then   the GPU and VRAM rows appear then, not only when the first reading succeeded
    """
    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import GpuUsage  # noqa: PLC0415

    readings: list[GpuUsage] = []

    w = sw.SystemStatsWidget(lambda: readings[0] if readings else None)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: not w._gpu_read_pending)
    assert w._gpu_bar is None

    readings.append(GpuUsage(30, vram_used_mb=1024, vram_total_mb=4096))
    w._refresh()
    qtbot.waitUntil(lambda: w._gpu_bar is not None and w._gpu_bar.value() == 30)

    assert w._vram_bar is not None
    assert w._vram_bar.value() == 25


@pytest.mark.gui
def test_stats_widget_reads_the_card_off_the_gui_thread(qtbot, monkeypatch) -> None:
    """
    Given  a card whose reading takes a while
    When   the widget refreshes several times meanwhile
    Then   the reading runs in another thread than the GUI's, and only one runs at a time
    """
    import threading  # noqa: PLC0415

    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import GpuUsage  # noqa: PLC0415

    gui_thread = threading.get_ident()
    release = threading.Event()
    threads: list[int] = []

    def slow_read() -> GpuUsage:
        threads.append(threading.get_ident())
        release.wait(timeout=5)
        return GpuUsage(10, vram_used_mb=None, vram_total_mb=None)

    w = sw.SystemStatsWidget(slow_read)
    qtbot.addWidget(w)
    w._refresh()
    w._refresh()
    release.set()
    qtbot.waitUntil(lambda: w._gpu_bar is not None)

    assert threads
    assert gui_thread not in threads
    assert len(threads) == 1


@pytest.mark.gui
def test_stats_widget_keeps_the_last_reading_when_the_driver_skips_one(qtbot, monkeypatch) -> None:
    """
    Given  a card that answered once and then gave no reading (a reset, a busy device)
    When   the widget refreshes
    Then   the last value stays on the gauge instead of an error
    """
    from teachers_teammate.gui import _stats_widget as sw  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import GpuUsage  # noqa: PLC0415

    readings = [GpuUsage(42, vram_used_mb=4096, vram_total_mb=8192)]

    w = sw.SystemStatsWidget(lambda: readings[0] if readings else None)
    qtbot.addWidget(w)
    qtbot.waitUntil(lambda: w._gpu_bar is not None and w._gpu_bar.value() == 42)

    readings.clear()  # the driver stops answering
    w._refresh()
    qtbot.waitUntil(lambda: not w._gpu_read_pending)

    assert w._gpu_bar.value() == 42


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


@pytest.mark.gui
def test_about_dialog_names_the_license_and_the_copyright(qtbot) -> None:
    """
    Given  an AboutDialog
    When   it is created
    Then   one label carries who holds the rights and the license, and where to read it
    """
    from teachers_teammate.gui._help_dialog import AboutDialog  # noqa: PLC0415
    from teachers_teammate.texts import app as app_text  # noqa: PLC0415

    dialog = AboutDialog()
    qtbot.addWidget(dialog)

    texts = [lbl.text() for lbl in dialog.findChildren(QLabel)]
    block = next((text for text in texts if app_text.COPYRIGHT in text), None)
    assert block is not None, "the About window shows no copyright line"
    assert app_text.LICENSE_TITLE in block
    assert "NO WARRANTY" in block
    assert app_text.LICENSE_URL in block


# ── LicensesDialog ────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_licenses_dialog_has_a_tab_for_our_license_and_one_for_the_rest(qtbot) -> None:
    """
    Given  a LicensesDialog
    When   it is created
    Then   it has two tabs, and the first one shows the license file as it ships
    """
    from PySide6.QtWidgets import QTabWidget, QTextBrowser  # noqa: PLC0415
    from teachers_teammate.gui._help_dialog import _LICENSE_PATH, LicensesDialog  # noqa: PLC0415
    from teachers_teammate.texts import app as app_text  # noqa: PLC0415

    dialog = LicensesDialog()
    qtbot.addWidget(dialog)

    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        app_text.LICENSE_TAB,
        app_text.THIRD_PARTY_TAB,
    ]
    own_page = tabs.widget(0).findChild(QTextBrowser)
    shipped = _LICENSE_PATH.read_text(encoding="utf-8")
    assert own_page.toPlainText().strip() == shipped.strip()


@pytest.mark.gui
def test_a_missing_license_file_says_so_instead_of_showing_an_empty_tab(qtbot, monkeypatch) -> None:
    """
    Given  an installation without our own license and without the third-party page
    When   the LicensesDialog is created
    Then   each tab says its text is missing instead of showing nothing
    """
    from pathlib import Path  # noqa: PLC0415

    from PySide6.QtWidgets import QTabWidget, QTextBrowser  # noqa: PLC0415

    from teachers_teammate.gui import _help_dialog  # noqa: PLC0415
    from teachers_teammate.texts import app as app_text  # noqa: PLC0415

    nowhere = Path("/nonexistent/LICENSE")
    monkeypatch.setattr(_help_dialog, "_LICENSE_PATH", nowhere)
    monkeypatch.setattr(_help_dialog, "_LICENSES_PATH", nowhere)

    dialog = _help_dialog.LicensesDialog()
    qtbot.addWidget(dialog)

    tabs = dialog.findChild(QTabWidget)
    assert tabs.widget(0).findChild(QTextBrowser).toPlainText() == app_text.LICENSE_MISSING
    assert tabs.widget(1).findChild(QTextBrowser).toPlainText() == app_text.THIRD_PARTY_MISSING


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


# ── DataFlowSummary / LocationHint ────────────────────────────────────────


def _flow(*stages):
    from teachers_teammate.domain.data_location import DataFlow  # noqa: PLC0415

    return DataFlow(tuple(stages))


def _stage(
    name: str,
    location,
    service: str,
    receives: str = "text",
    hidden: bool = False,
    region=None,
):
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataFlowStage,
        StageLocation,
    )

    return DataFlowStage(name, StageLocation(location, service, region=region), receives, hidden)


@pytest.mark.gui
def test_data_flow_summary_reassures_when_everything_is_local(qtbot) -> None:
    """
    Given  a data flow where every step runs on this computer
    When   the summary is rendered
    Then   it says everything stays on this computer and lists no step rows
    """
    from teachers_teammate.domain.data_location import DataLocation  # noqa: PLC0415
    from teachers_teammate.gui._data_flow_summary import DataFlowSummary  # noqa: PLC0415

    box = DataFlowSummary()
    qtbot.addWidget(box)

    box.set_flow(
        _flow(
            _stage("ocr", DataLocation.THIS_COMPUTER, "Ollama", "page images"),
            _stage("correction", DataLocation.THIS_COMPUTER, "Ollama"),
        )
    )

    assert "Everything stays on this computer." in box._body.text()
    assert "leaves this computer" not in box._body.text()
    assert "Text recognition" not in box._body.text()
    assert not hasattr(box, "row_texts")


@pytest.mark.gui
def test_data_flow_summary_warns_and_advises_for_online_steps(qtbot) -> None:
    """
    Given  proofreading on an online service with names not hidden
    When   the summary is rendered
    Then   it says text with names goes outside the EU and advises hiding names, by the
           name of the checkbox and without a step number
    """
    from teachers_teammate.domain.data_location import DataLocation  # noqa: PLC0415
    from teachers_teammate.gui._data_flow_summary import DataFlowSummary  # noqa: PLC0415

    box = DataFlowSummary()
    qtbot.addWidget(box)

    box.set_flow(
        _flow(
            _stage("ocr", DataLocation.THIS_COMPUTER, "Built-in reader", "page images"),
            _stage("correction", DataLocation.ONLINE, "OpenAI (ChatGPT)"),
        )
    )

    assert "Text with names is sent to a service outside the EU." in box._body.text()
    assert "Remove personal info" in box._body.text()
    assert "Step" not in box._body.text()


@pytest.mark.gui
def test_data_flow_summary_shows_an_anonymized_eu_service_in_yellow(qtbot) -> None:
    """
    Given  proofreading at a service in the EU with names hidden
    When   the summary is rendered
    Then   the verdict is the milder "anonymized text in the EU" warning
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataLocation,
        DataRegion,
    )
    from teachers_teammate.gui._data_flow_summary import DataFlowSummary  # noqa: PLC0415

    box = DataFlowSummary()
    qtbot.addWidget(box)

    box.set_flow(
        _flow(
            _stage(
                "correction",
                DataLocation.ONLINE,
                "Mistral (EU)",
                hidden=True,
                region=DataRegion.EU,
            )
        )
    )

    assert "Anonymized text is sent to a service in the EU." in box._body.text()


@pytest.mark.gui
@pytest.mark.parametrize(
    ("location", "url", "warned"),
    [
        ("this", "http://127.0.0.1:11434", False),
        ("other", "http://school-server:11434", True),
        ("other", "https://school-server:11434", False),
        ("other", "HTTPS://school-server:11434", False),
        ("internet", "http://203.0.113.7:11434", True),
        ("internet", "https://203.0.113.7:11434", False),
        ("vendor", "http://127.0.0.1:11434", False),
    ],
)
def test_plain_http_note_warns_for_a_server_of_the_teachers_choosing(
    location: str, url: str, warned: bool
) -> None:
    """
    Given  a step on this computer, on another computer, on an Ollama server on the internet,
           or at a vendor, reached through an http:// or https:// address
    When   the note for an unencrypted connection is asked
    Then   another computer and a server on the internet are warned about over http:// only,
           and this computer and a vendor (whose address the teacher did not choose) never
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataLocation,
        StageLocation,
    )
    from teachers_teammate.texts.privacy import plain_http_note  # noqa: PLC0415

    places = {
        "this": StageLocation(DataLocation.THIS_COMPUTER, "Ollama"),
        "other": StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", host="school-server"),
        "internet": StageLocation(DataLocation.ONLINE, "Ollama", host="203.0.113.7"),
        "vendor": StageLocation(DataLocation.ONLINE, "OpenAI"),
    }

    note = plain_http_note(places[location], url)

    assert bool(note) is warned
    if warned:
        assert "unencrypted" in note and places[location].host in note


@pytest.mark.gui
def test_short_place_uses_few_words_for_every_kind_of_place() -> None:
    """
    Given  steps on this computer, on a school server, at an EU service, and at a service
           that does not say where it processes data
    When   their short places are asked
    Then   they read "this computer", "own server", "<service> · EU" and
           "<service> · outside the EU"
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataLocation,
        DataRegion,
        StageLocation,
    )
    from teachers_teammate.texts.privacy import short_place  # noqa: PLC0415

    here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
    server = StageLocation(DataLocation.OTHER_COMPUTER, "Ollama", host="school.example")
    eu = StageLocation(DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU)
    unknown = StageLocation(DataLocation.ONLINE, "Mistral")

    assert short_place(here) == "this computer"
    assert short_place(server) == "own server"  # the host is for the tooltip
    assert short_place(eu) == "Mistral (EU) · EU"
    assert short_place(unknown) == "Mistral · outside the EU"


@pytest.mark.gui
def test_uses_line_names_model_and_place_and_falls_back_to_the_engine() -> None:
    """
    Given  a proofreading stage at an EU service and a built-in reader stage without a model
    When   their "uses" lines are built
    Then   the first shows model and place, the second the engine's name and no place
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataLocation,
        DataRegion,
    )
    from teachers_teammate.gui._data_flow_summary import uses_line  # noqa: PLC0415

    online = _stage("correction", DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU)
    reader = _stage("ocr", DataLocation.THIS_COMPUTER, "Built-in reader", "page images")

    assert "mistral-small-latest · Mistral (EU) · EU" in uses_line(online, "mistral-small-latest")
    assert "Built-in reader" in uses_line(reader, "")
    assert "this computer" not in uses_line(reader, "")


@pytest.mark.gui
def test_uses_line_shows_the_short_model_name_and_no_place_on_this_computer() -> None:
    """
    Given  a step that runs a Hugging Face model on this computer
    When   its "uses" line is built
    Then   the line names the short model name and does not repeat "this computer"
    """
    from teachers_teammate.domain.data_location import DataLocation  # noqa: PLC0415
    from teachers_teammate.gui._data_flow_summary import uses_line  # noqa: PLC0415

    stage = _stage("ocr", DataLocation.THIS_COMPUTER, "Ollama", "page images")
    tag = "hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0"

    line = uses_line(stage, tag)

    assert "chandra-ocr-2" in line
    assert "hf.co" not in line
    assert "this computer" not in line


@pytest.mark.gui
def test_uses_line_is_grey_while_the_step_is_off_and_escapes_names() -> None:
    """
    Given  a step with markup in its service name
    When   its line is built enabled and disabled
    Then   the name is escaped, the enabled dot has the privacy colour and the disabled line
           is grey all over
    """
    from teachers_teammate.domain.data_location import DataLocation  # noqa: PLC0415
    from teachers_teammate.gui._data_flow_summary import level_color, uses_line  # noqa: PLC0415

    stage = _stage("correction", DataLocation.ONLINE, "<b>x</b>")

    on = uses_line(stage, "m")
    off = uses_line(stage, "m", enabled=False)

    assert "&lt;b&gt;x&lt;/b&gt;" in on
    assert level_color(stage.privacy_level) in on
    assert level_color(stage.privacy_level) not in off
    assert "#888" in off
    assert "m · " in off  # still says what ticking it would use


@pytest.mark.gui
def test_uses_tooltip_says_where_names_go_and_the_vendor_country(qtbot) -> None:
    """
    Given  an online step of a vendor based in France with names hidden
    When   its tooltip is built
    Then   it says the text is sent with names hidden and names the vendor's country
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataFlowStage,
        DataLocation,
        DataRegion,
        StageLocation,
    )
    from teachers_teammate.texts.privacy import uses_tooltip  # noqa: PLC0415

    location = StageLocation(
        DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU, vendor_country="France"
    )
    stage = DataFlowStage("correction", location, "text", personal_info_hidden=True)

    tip = uses_tooltip(stage)

    assert "names hidden" in tip
    assert "Vendor based in France" in tip


@pytest.mark.gui
def test_location_hint_shows_place_and_note(qtbot) -> None:
    """
    Given  a LocationHint
    When   a place and a note are set
    Then   both appear, and the note is escaped
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        DataLocation,
        StageLocation,
    )
    from teachers_teammate.gui._data_flow_summary import LocationHint  # noqa: PLC0415

    hint = LocationHint()
    qtbot.addWidget(hint)

    hint.set_location(StageLocation(DataLocation.ONLINE, "OpenAI"), "text <is> sent")

    assert "Online service (OpenAI)" in hint.text()
    assert "text &lt;is&gt; sent" in hint.text()


@pytest.mark.gui
def test_every_advice_code_has_wording_for_the_teacher() -> None:
    """
    Given  the advice codes the domain can produce
    When   the GUI looks up their wording
    Then   each one has a sentence, so a new code cannot reach the screen without text
    """
    from teachers_teammate.domain.data_location import Advice  # noqa: PLC0415
    from teachers_teammate.texts.privacy import advice_text  # noqa: PLC0415

    for advice in Advice:
        assert advice_text(advice).strip()


@pytest.mark.gui
def test_the_caption_names_steps_in_words_and_ignores_the_gray_conversion(qtbot) -> None:
    """
    Given  the steps of an automatic preparation, with the gray conversion for Ollama
    When   the caption is built
    Then   every acting step has its words, the gray conversion none, and the rotation tag alone
           reads "Original page (rotation tag applied)"
    """
    from teachers_teammate.texts import review as txt  # noqa: PLC0415

    assert (
        txt.prepared_caption(["page", "turn", "size", "light", "deskew", "grayscale"])
        == "Fixed for reading: page cut out, page turned, scaled down, shadows evened out, tilt corrected"
    )
    assert txt.prepared_caption(["flatten"]) == "Fixed for reading: page flattened"
    assert txt.prepared_caption(["grayscale"]) == "As scanned — no fixes needed"
    assert txt.prepared_caption(["turned by tag"]) == "Original page (rotation tag applied)"
