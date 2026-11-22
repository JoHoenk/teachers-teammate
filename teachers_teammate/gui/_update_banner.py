"""The banner at the top of the main window that offers a newer version."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from ..texts import app as app_text

#: The download link, readable on the banner's dark amber background.
_LINK_COLOUR = "#ffd966"
_LABEL_NAME = "UpdateBannerLabel"


def build_update_banner() -> QFrame:
    """Return the banner, hidden until :func:`show_update` is called."""
    banner = QFrame()
    banner.setObjectName("UpdateBanner")
    # Never grow taller than the content: the splitter below takes all spare height.
    banner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    banner.setStyleSheet("#UpdateBanner { background: #6a5200; border-radius: 4px; padding: 2px; }")
    layout = QHBoxLayout(banner)
    layout.setContentsMargins(8, 4, 8, 4)
    label = QLabel()
    label.setObjectName(_LABEL_NAME)
    label.setOpenExternalLinks(True)
    palette = label.palette()
    palette.setColor(QPalette.ColorRole.Link, QColor(_LINK_COLOUR))
    label.setPalette(palette)
    layout.addWidget(label, 1)
    close_btn = QPushButton(app_text.DISMISS_MARK)
    close_btn.setFixedSize(20, 20)
    close_btn.setFlat(True)
    close_btn.clicked.connect(banner.hide)
    layout.addWidget(close_btn)
    banner.hide()
    return banner


def show_update(banner: QFrame, version: str, url: str) -> None:
    """Show *banner* with the offer of *version*, downloadable at *url*."""
    label = banner.findChild(QLabel, _LABEL_NAME)
    if label is not None:
        label.setText(app_text.update_available(version, url))
    banner.show()
