"""GUI tests for the scan pane: every stored page in one scroll."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPixmap, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsTextItem

from teachers_teammate.gui.check._scan_view import ScanPane, ScanView


def _page(path: Path, width: int, height: int, colour: str) -> Path:
    """Write a solid page image of *width* x *height* and return its path."""
    pix = QPixmap(width, height)
    pix.fill(QColor(colour))
    assert pix.save(str(path))
    return path


@pytest.fixture
def page_files(tmp_path: Path) -> list[Path]:
    """Three prepared pages, each taller than the pane."""
    return [
        _page(tmp_path / f"page{index}.png", 400, 900, colour)
        for index, colour in enumerate(("#ffffff", "#eeeeee", "#dddddd"), start=1)
    ]


@pytest.fixture
def original_files(tmp_path: Path) -> list[Path]:
    """The pages as they were scanned, at another size so a swap is visible."""
    return [
        _page(tmp_path / f"original{index}.png", 200, 450, colour)
        for index, colour in enumerate(("#ff0000", "#00ff00", "#0000ff"), start=1)
    ]


def _messages(view: ScanView) -> list[str]:
    """The texts the scan writes where a page is missing."""
    return [
        item.toPlainText() for item in view._scene.items() if isinstance(item, QGraphicsTextItem)
    ]


def _view(qtbot) -> ScanView:
    view = ScanView()
    qtbot.addWidget(view)
    view.resize(400, 300)
    view.show()
    qtbot.wait(20)  # let the resize reach the viewport, as it does in the window
    return view


# ── The pages ─────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_scan_view_stacks_every_page_in_one_scene(qtbot, page_files: list[Path]) -> None:
    """
    Given  three prepared pages
    When   they are shown
    Then   all three sit in one scene, in order, each as wide as the pane (fit width)
    """
    view = _view(qtbot)
    view.set_fit_page(False)

    view.set_pages(page_files)

    items = [item for item in view._items if item is not None]
    assert len(items) == 3
    tops = [item.pos().y() for item in items]
    assert tops == sorted(tops)
    width = view.viewport().width() - 16
    assert all(item.pixmap().width() == width for item in items)
    assert view.page_count == 3


@pytest.mark.gui
def test_scan_view_shows_a_message_for_a_missing_page_only(
    qtbot, page_files: list[Path], tmp_path: Path
) -> None:
    """
    Given  a document whose second page image is gone
    When   it is shown
    Then   the other two pages are drawn and the missing one says so
    """
    view = _view(qtbot)

    view.set_pages([page_files[0], tmp_path / "gone.png", page_files[2]])

    assert [item is not None for item in view._items] == [True, False, True]
    assert _messages(view) == ["(image unavailable)"]


@pytest.mark.gui
def test_scan_view_without_pages_says_so(qtbot) -> None:
    """
    Given  a document with no stored page
    When   it is shown
    Then   the pane says there is no page image
    """
    view = _view(qtbot)

    view.set_pages([])

    assert _messages(view) == ["(no page image)"]


# ── Fit page and fit width ────────────────────────────────────────────────


@pytest.mark.gui
def test_scan_view_opens_with_a_whole_page_in_view(qtbot, page_files: list[Path]) -> None:
    """
    Given  tall pages (400 x 900) and a pane of 400 x 300
    When   they are shown
    Then   each page is as high as the pane lets it be and narrower than the pane, centred, so a
           whole page is in view
    """
    view = _view(qtbot)

    view.set_pages(page_files)

    assert view.fit_page
    available_height = view.viewport().height() - 16
    first = view._items[0]
    assert first is not None
    assert first.pixmap().height() <= available_height
    assert first.pixmap().width() < view.viewport().width() - 16
    # Centred: the same space on both sides.
    left = first.pos().x()
    right = view.sceneRect().width() - left - first.pixmap().width()
    assert left == pytest.approx(right, abs=1)


@pytest.mark.gui
def test_scan_view_fits_a_wide_page_to_the_width(qtbot, tmp_path: Path) -> None:
    """
    Given  a page wider than the pane is high (a landscape page)
    When   it is shown fitted to the page
    Then   it is as wide as the pane, because that is what fits
    """
    view = _view(qtbot)

    view.set_pages([_page(tmp_path / "wide.png", 1200, 400, "#ffffff")])

    item = view._items[0]
    assert item is not None
    assert item.pixmap().width() == view.viewport().width() - 16


@pytest.mark.gui
def test_a_double_click_switches_between_fit_page_and_fit_width(
    qtbot, page_files: list[Path]
) -> None:
    """
    Given  a scan fitted to the page
    When   it is double-clicked twice
    Then   it is fitted to the width and then to the page again, and after a zoom the first
           double click only fits again in the mode that was on
    """
    view = _view(qtbot)
    view.set_pages(page_files)
    narrow = view._items[0].pixmap().width()

    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton)
    assert not view.fit_page
    assert view._items[0].pixmap().width() == view.viewport().width() - 16

    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton)
    assert view.fit_page
    assert view._items[0].pixmap().width() == narrow

    view.zoom_in()
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton)
    assert view.fit_page
    assert view.transform().m11() == pytest.approx(1.0)


@pytest.mark.gui
def test_the_fit_mode_stays_for_the_next_document(qtbot, page_files: list[Path]) -> None:
    """
    Given  a scan the teacher switched to fit width
    When   the pages of another document are shown
    Then   they are fitted to the width as well
    """
    view = _view(qtbot)
    view.set_pages(page_files)
    view.set_fit_page(False)

    view.set_pages(page_files[:2])

    assert not view.fit_page
    assert view._items[0].pixmap().width() == view.viewport().width() - 16


# ── Original ──────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_scan_view_swaps_every_page_that_has_an_original(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  three prepared pages and the original of the first and the third
    When   the original is shown
    Then   those two pages are the scanned ones and the middle stays prepared
    """
    view = _view(qtbot)
    view.set_pages(page_files, [original_files[0], None, original_files[2]])

    view.show_originals(True)

    assert view.showing_originals
    assert [pix.width() for pix in view._pixmaps] == [200, 400, 200]

    view.show_originals(False)

    assert [pix.width() for pix in view._pixmaps] == [400, 400, 400]


@pytest.mark.gui
def test_scan_view_keeps_the_place_when_the_original_is_shown(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  a scan scrolled to the middle of the document
    When   the original is shown
    Then   the teacher is still reading the same part of it
    """
    view = _view(qtbot)
    view.set_pages(page_files, original_files)
    bar = view.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum() // 2)
    before = bar.value() / bar.maximum()

    view.show_originals(True)

    assert bar.value() / bar.maximum() == pytest.approx(before, abs=0.05)


@pytest.mark.gui
def test_scan_view_fits_the_pages_to_a_new_width(qtbot, page_files: list[Path]) -> None:
    """
    Given  a scan fitted to a narrow pane
    When   the pane grows
    Then   the pages are laid out at the new width (fit width)
    """
    view = _view(qtbot)
    view.set_fit_page(False)
    view.set_pages(page_files)

    view.resize(700, 300)

    width = view.viewport().width() - 16
    assert all(item is not None and item.pixmap().width() == width for item in view._items)


@pytest.mark.gui
def test_scan_view_keeps_the_zoom_when_the_pane_is_resized(qtbot, page_files: list[Path]) -> None:
    """
    Given  a scan the teacher zoomed into
    When   the pane is resized
    Then   the zoom is kept instead of being fitted away
    """
    view = _view(qtbot)
    view.set_pages(page_files)
    view.zoom_in()
    zoom = view.transform().m11()

    view.resize(700, 300)

    assert view.transform().m11() == pytest.approx(zoom)


# ── The pane: Original and the caption ────────────────────────────────────


def _pane(qtbot) -> ScanPane:
    pane = ScanPane()
    qtbot.addWidget(pane)
    pane.resize(400, 400)
    pane.show()
    qtbot.wait(20)
    return pane


@pytest.mark.gui
def test_scan_pane_offers_original_only_where_a_page_has_one(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  a pane that shows pages with originals
    When   pages without any original are shown next
    Then   the Original toggle is on offer for the first and gone for the second
    """
    pane = _pane(qtbot)

    pane.set_pages(page_files, original_files, ("turn",))
    assert pane.original_offered

    pane.set_pages(page_files, [None, None, None], ("turn",))
    assert not pane.original_offered


@pytest.mark.gui
def test_scan_pane_original_toggle_swaps_the_pages_and_the_caption(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  a pane that shows prepared pages, their originals and the steps that prepared them
    When   the teacher turns Original on and off
    Then   the pages are the scanned ones and the caption says so, and then they are prepared again
    """
    pane = _pane(qtbot)
    pane.set_pages(page_files, original_files, ("turn",))
    assert pane._caption.text() == "Fixed for reading: page turned"

    pane._original_btn.setChecked(True)

    assert pane.showing_originals
    assert pane._caption.text() == "Showing the original pages"

    pane._prepared_btn.setChecked(True)

    assert not pane.showing_originals
    assert pane._caption.text() == "Fixed for reading: page turned"


@pytest.mark.gui
def test_scan_pane_original_stays_on_for_the_next_document(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  a pane showing the originals of one document
    When   the pages of another document with originals are shown
    Then   the originals are still the ones on screen
    """
    pane = _pane(qtbot)
    pane.set_pages(page_files, original_files, ("turn",))
    pane._original_btn.setChecked(True)

    pane.set_pages(page_files, original_files, ("light",))

    assert pane.showing_originals
    assert [pix.width() for pix in pane.view._pixmaps] == [200, 200, 200]


@pytest.mark.gui
def test_scan_pane_has_no_switch_when_nothing_was_done_to_the_pages(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  pages that have an original, but where no preparation step acted
    When   they are shown
    Then   there is no Prepared · Original switch, since the original is the same picture, and
           the caption says the pages are as scanned; and when the originals were on, they are
           taken off
    """
    pane = _pane(qtbot)
    pane.set_pages(page_files, original_files, ("turn",))
    pane._original_btn.setChecked(True)

    pane.set_pages(page_files, original_files, ("grayscale",))

    assert not pane.original_offered
    assert not pane.showing_originals
    assert pane._caption.text() == "As scanned — no fixes needed"


@pytest.mark.gui
def test_scan_pane_switch_names_what_is_shown(
    qtbot, page_files: list[Path], original_files: list[Path]
) -> None:
    """
    Given  a pane with the switch on offer
    When   Original and then Prepared is pressed
    Then   exactly one of the two buttons is pressed at any time, the one that says what is shown
    """
    pane = _pane(qtbot)
    pane.set_pages(page_files, original_files, ("turn",))
    assert pane._prepared_btn.isChecked()
    assert not pane._original_btn.isChecked()

    pane._original_btn.click()
    assert pane._original_btn.isChecked()
    assert not pane._prepared_btn.isChecked()

    pane._prepared_btn.click()
    assert pane._prepared_btn.isChecked()
    assert not pane.showing_originals


# ── The layout settles; zoom ──────────────────────────────────────────────


@pytest.mark.gui
@pytest.mark.parametrize("fit_page", [True, False])
@pytest.mark.parametrize("size", [(300, 300), (600, 700), (900, 500), (400, 1000), (1200, 800)])
@pytest.mark.parametrize("page", [(1240, 1754), (1754, 1240)])
def test_scan_view_settles_in_every_pane_size(
    qtbot, tmp_path: Path, size: tuple[int, int], page: tuple[int, int], fit_page: bool
) -> None:
    """
    Given  a portrait or a landscape page and a pane of any size, fitted to the page or the width
    When   the page is shown and the events have run
    Then   the layout was built a few times at most and does not start again by itself (a
           scroll bar that came and went with the content once changed the size, and with it the
           layout, for ever)
    """
    view = ScanView()
    qtbot.addWidget(view)
    view.set_fit_page(fit_page)
    count = 0
    original = view._layout

    def counted(*args, **kwargs) -> None:
        nonlocal count
        count += 1
        if count > 50:
            raise AssertionError("the layout does not settle")
        original(*args, **kwargs)

    view._layout = counted  # ty: ignore[invalid-assignment]
    view.resize(*size)
    view.show()
    view.set_pages([_page(tmp_path / "page.png", *page, "#ffffff")])
    qtbot.wait(50)
    settled = count

    qtbot.wait(100)

    assert settled <= 5
    assert count == settled


@pytest.mark.gui
def test_a_fitted_page_does_not_scroll_by_itself(qtbot, tmp_path: Path) -> None:
    """
    Given  one page fitted to the pane
    When   it is shown
    Then   it fills the pane exactly: there is nothing to scroll to
    """
    view = _view(qtbot)

    view.set_pages([_page(tmp_path / "page.png", 400, 900, "#ffffff")])

    assert view.verticalScrollBar().maximum() == 0
    assert not view.horizontalScrollBar().isVisible()


@pytest.mark.gui
def test_zoom_stays_between_its_limits(qtbot, page_files: list[Path]) -> None:
    """
    Given  a fitted scan
    When   it is zoomed in and out many times
    Then   the scale stops at half and at four times the fitted page, and a zoomed scan is not
           fitted any more
    """
    view = _view(qtbot)
    view.set_pages(page_files)

    for _ in range(20):
        view.zoom_in()
    assert view.transform().m11() == pytest.approx(4.0)
    assert view.zoomed

    for _ in range(40):
        view.zoom_out()
    assert view.transform().m11() == pytest.approx(0.5)


@pytest.mark.gui
def test_only_ctrl_and_the_wheel_zoom(qtbot, page_files: list[Path]) -> None:
    """
    Given  a fitted scan
    When   the wheel turns without Ctrl, and then with it
    Then   the first scrolls and leaves the zoom, the second zooms
    """
    view = _view(qtbot)
    view.set_pages(page_files)
    centre = view.viewport().rect().center()

    QTest.mouseMove(view.viewport(), centre)
    wheel = QWheelEvent(
        centre,
        view.viewport().mapToGlobal(centre),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(wheel)
    assert not view.zoomed

    ctrl_wheel = QWheelEvent(
        centre,
        view.viewport().mapToGlobal(centre),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(ctrl_wheel)
    assert view.zoomed


@pytest.mark.gui
def test_the_zoom_buttons_zoom_and_show_the_fit_that_is_on(qtbot, page_files: list[Path]) -> None:
    """
    Given  a pane with a fitted scan
    When   the teacher presses +, Fit width, Fit page
    Then   + zooms and leaves no fit pressed, Fit width and Fit page fit again and press their button
    """
    pane = _pane(qtbot)
    pane.set_pages(page_files)
    assert pane._fit_page_btn.isChecked()
    assert not pane._fit_width_btn.isChecked()

    pane._zoom_in_btn.click()
    assert pane.view.zoomed
    assert not pane._fit_page_btn.isChecked()
    assert not pane._fit_width_btn.isChecked()

    pane._fit_width_btn.click()
    assert not pane.view.zoomed
    assert not pane.view.fit_page
    assert pane._fit_width_btn.isChecked()
    assert not pane._fit_page_btn.isChecked()

    pane._fit_page_btn.click()
    assert pane.view.fit_page
    assert pane._fit_page_btn.isChecked()

    pane._fit_page_btn.click()  # a second press keeps the mode instead of unpressing it
    assert pane._fit_page_btn.isChecked()
