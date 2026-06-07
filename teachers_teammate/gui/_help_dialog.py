"""Help and About dialogs."""

from __future__ import annotations

from pathlib import Path
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

from ._image_utils import load_pixmap

_MEIPASS = getattr(sys, "_MEIPASS", None)
_ROOT = Path(_MEIPASS) if _MEIPASS is not None else Path(__file__).resolve().parent.parent.parent
_DOCS = _ROOT / "docs"
_IMG_PATH = _ROOT / "teachers_teammate" / "assets" / "teachers_teammate.png"
_LICENSES_PATH = _ROOT / "teachers_teammate" / "assets" / "third_party_licenses.md"

_USER_GUIDE_PAGES: list[tuple[str, str]] = [
    ("Overview", "index.md"),
    ("Using the App", "using_the_app.md"),
    ("Advanced Guide", "advanced_user_guide.md"),
]


def _make_browser(path: Path) -> QTextBrowser:
    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    if path.exists():
        browser.setMarkdown(path.read_text(encoding="utf-8"))
    else:
        browser.setPlainText(f"Page not found: {path.name}")
    return browser


class HelpDialog(QDialog):
    """Dialog showing the user guide across three tabs (Overview, Using the App, Advanced)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("User Guide — Teacher's Teammate")
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
    """About dialog showing the app image, version, and credits."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Teacher's Teammate")
        self.setFixedSize(440, 480)

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

        name_label = QLabel("<h2>Teacher's Teammate</h2>")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        from teachers_teammate import __version__ as ver  # noqa: PLC0415

        desc = QLabel(
            f"<p>Version <b>{ver}</b></p>"
            "<p>Batch OCR pipeline for scanned documents and handwritten notes.<br>"
            "Extracts text from PDFs and images, optionally produces<br>"
            "proofread DOCX reports via a local language model.</p>"
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        layout.addWidget(desc)

        credits_lbl = QLabel(
            '<p style="color:#888; font-size:8pt;">'
            "Powered by Ollama &bull; Tesseract OCR &bull; LangChain<br>"
            "pypdfium2 &bull; PySide6 &bull; qdarkstyle<br><br>"
            'Inspired by <a href="https://github.com/imanoop7/Ollama-OCR">'
            "Ollama-OCR</a> by imanoop7."
            "</p>"
        )
        credits_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credits_lbl.setOpenExternalLinks(True)
        credits_lbl.setWordWrap(True)
        layout.addWidget(credits_lbl)

        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ThirdPartyLicensesDialog(QDialog):
    """Dialog showing the bundled third-party license and notice texts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Third-Party Licenses — Teacher's Teammate")
        self.resize(860, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        if _LICENSES_PATH.exists():
            browser.setMarkdown(_LICENSES_PATH.read_text(encoding="utf-8"))
        else:
            browser.setPlainText(
                "Third-party license information is not available in this installation.\n\n"
                "Run tools/release/update_licenses.py to generate it."
            )
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
