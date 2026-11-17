"""GUI tests for the scan pane: every stored page in one scroll."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPixmap
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
    Then   all three sit in one scene, in order, each as wide as the pane
    """
    view = _view(qtbot)

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
    Then   the pages are laid out at the new width
    """
    view = _view(qtbot)
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

    pane.set_pages(page_files, original_files, ("grayscale",))
    assert pane.original_offered

    pane.set_pages(page_files, [None, None, None], ("grayscale",))
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
    pane.set_pages(page_files, original_files, ("grayscale",))
    assert pane._caption.text().startswith("Prepared")

    pane._original_btn.setChecked(True)

    assert pane.showing_originals
    assert pane._caption.text() == "Showing the original pages"

    pane._original_btn.setChecked(False)

    assert not pane.showing_originals
    assert pane._caption.text().startswith("Prepared")


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
    pane.set_pages(page_files, original_files, ())
    pane._original_btn.setChecked(True)

    pane.set_pages(page_files, original_files, ())

    assert pane.showing_originals
    assert [pix.width() for pix in pane.view._pixmaps] == [200, 200, 200]
