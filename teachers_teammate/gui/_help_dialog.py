"""Help and About dialogs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..texts import app as app_text
from ._image_utils import load_pixmap

_MEIPASS = getattr(sys, "_MEIPASS", None)
_ROOT = Path(_MEIPASS) if _MEIPASS is not None else Path(__file__).resolve().parent.parent.parent
#: The user guide: ``docs/`` in a source checkout and in the bundle, and ``_docs/`` inside the
#: package in a wheel (the wheel build copies the three pages there).
_DOCS = next(
    (
        folder
        for folder in (_ROOT / "docs", Path(__file__).resolve().parent.parent / "_docs")
        if folder.is_dir()
    ),
    _ROOT / "docs",
)
_IMG_PATH = _ROOT / "teachers_teammate" / "assets" / "teachers_teammate.png"
#: Our own license, written by ``tools/licenses/inventory.py`` from the repository's ``LICENSE``.
_LICENSE_PATH = _ROOT / "teachers_teammate" / "assets" / "LICENSE.txt"
_LICENSES_PATH = _ROOT / "teachers_teammate" / "assets" / "third_party_licenses.txt"
#: The Linux build's host libraries, written into ``_internal/`` by the build.  No other platform
#: has one: Windows and macOS take every library from a wheel.
_HOST_LIBRARIES_PATH = _ROOT / "third_party_host_libraries.txt"

_USER_GUIDE_PAGES: list[tuple[str, str]] = [
    (app_text.GUIDE_OVERVIEW, "index.md"),
    (app_text.GUIDE_USING, "using_the_app.md"),
    (app_text.GUIDE_ADVANCED, "advanced_user_guide.md"),
]


#: A fenced block whose info string is a Sphinx directive (```` ```{toctree} ````): it builds the
#: website's navigation and means nothing in the app, so the Help window leaves it out.
_SPHINX_DIRECTIVE = re.compile(r"^```\{[^}]*\}.*?^```[ \t]*$\n?", re.MULTILINE | re.DOTALL)


def _make_browser(path: Path) -> QTextBrowser:
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    if path.exists():
        browser.setMarkdown(_SPHINX_DIRECTIVE.sub("", path.read_text(encoding="utf-8")))
    else:
        browser.setPlainText(app_text.guide_page_missing(path.name))
    return browser


class HelpDialog(QDialog):
    """Dialog showing the user guide across three tabs (Overview, Using the App, Advanced)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(app_text.GUIDE_TITLE)
        self.resize(900, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        for label, filename in _USER_GUIDE_PAGES:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 4, 0, 0)
            page_layout.addWidget(_make_browser(_DOCS / filename))
            tabs.addTab(page, label)
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class AboutDialog(QDialog):
    """About dialog showing the app image, version, license, and credits."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(app_text.ABOUT_TITLE)
        self.setFixedSize(460, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        if _IMG_PATH.exists():
            img_label = QLabel()
            pix = load_pixmap(_IMG_PATH).scaledToWidth(
                240, Qt.TransformationMode.SmoothTransformation
            )
            img_label.setPixmap(pix)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(img_label)

        name_label = QLabel(app_text.ABOUT_NAME)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        from teachers_teammate import __version__ as ver  # noqa: PLC0415

        desc = QLabel(app_text.about_version(ver) + app_text.ABOUT_DESCRIPTION)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        license_lbl = QLabel(app_text.ABOUT_LICENSE)
        license_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        license_lbl.setOpenExternalLinks(True)
        license_lbl.setWordWrap(True)
        layout.addWidget(license_lbl)

        credits_lbl = QLabel(app_text.ABOUT_CREDITS)
        credits_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_lbl.setOpenExternalLinks(True)
        credits_lbl.setWordWrap(True)
        layout.addWidget(credits_lbl)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class LicensesDialog(QDialog):
    """Dialog showing our own license and the bundled third-party license and notice texts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(app_text.LICENSES_TITLE)
        self.resize(860, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        tabs.addTab(
            self._tab(self._own_license_text, app_text.LICENSE_MISSING),
            app_text.LICENSE_TAB,
        )
        tabs.addTab(
            self._tab(self._third_party_text, app_text.THIRD_PARTY_MISSING),
            app_text.THIRD_PARTY_TAB,
        )
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _tab(self, read: Callable[[], str | None], missing: str) -> QWidget:
        """Return one tab: the text *read* returns, or *missing* when it did not ship."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 4, 0, 0)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        text = read()
        browser.setPlainText(missing if text is None else text)
        page_layout.addWidget(browser)
        return page

    @staticmethod
    def _own_license_text() -> str | None:
        """Return our own license, or ``None`` when it did not ship."""
        if not _LICENSE_PATH.exists():
            return None
        return _LICENSE_PATH.read_text(encoding="utf-8")

    @staticmethod
    def _third_party_text() -> str | None:
        """Return the third-party page, with the host libraries, or ``None`` when it did not ship."""
        if not _LICENSES_PATH.exists():
            return None
        text = _LICENSES_PATH.read_text(encoding="utf-8")
        if _HOST_LIBRARIES_PATH.exists():
            text += "\n" + _HOST_LIBRARIES_PATH.read_text(encoding="utf-8")
        return text
