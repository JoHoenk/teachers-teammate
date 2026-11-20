"""The card that describes one Ollama server: name and models, where it is, and *Use it*.

One widget for the three places a found server is shown (the assistant's *Local AI* page,
Settings → *This computer*, and the *Find the AI server* dialog), so a server reads the same
everywhere.  It only shows; what *Use it* does is the owner's to decide.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..domain.ollama_discovery import DiscoveredServer, ServerKind
from ..texts import servers as text

#: The title is the one line that matters, so it is bigger than the text around it.
_TITLE_STYLE = "font-weight: bold; font-size: 13px;"
_PLACE_STYLE = "color: #888;"


class ServerCard(QFrame):
    """A framed card: ``Ollama 0.12.3 · 4 models`` over ``on this PC · http://localhost:11434``."""

    #: *Use it* was pressed: the address of the server the card shows.
    use_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._url = ""
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("serverCard")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        column = QVBoxLayout()
        column.setSpacing(0)
        self._title = QLabel()
        self._title.setStyleSheet(_TITLE_STYLE)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._place = QLabel()
        self._place.setStyleSheet(_PLACE_STYLE)
        self._place.setWordWrap(True)
        column.addWidget(self._title)
        column.addWidget(self._place)
        row.addLayout(column, stretch=1)
        self._use = QPushButton(text.CARD_USE)
        self._use.setToolTip(text.CARD_USE_TOOLTIP)
        self._use.clicked.connect(lambda _checked=False: self.use_requested.emit(self._url))
        row.addWidget(self._use)

    def show_server(
        self,
        *,
        url: str,
        kind: ServerKind,
        version: str = "",
        model_count: int | None = None,
        offer_use: bool = True,
    ) -> None:
        """Describe the server at *url*; *offer_use* is off for the one already in use."""
        self._url = url
        self._title.setText(text.card_title(version, model_count))
        self._place.setText(text.card_place(kind, url))
        self._use.setVisible(offer_use)

    def show_discovered(self, server: DiscoveredServer, *, offer_use: bool = True) -> None:
        """Describe a server a search found."""
        self.show_server(
            url=server.url,
            kind=server.kind,
            version=server.version,
            model_count=server.model_count,
            offer_use=offer_use,
        )

    @property
    def url(self) -> str:
        """The address of the server the card shows."""
        return self._url

    @property
    def title(self) -> str:
        """The bold line."""
        return self._title.text()

    @property
    def place(self) -> str:
        """The line under it."""
        return self._place.text()

    @property
    def use_button(self) -> QPushButton:
        """The *Use it* button."""
        return self._use

    def text(self) -> str:
        """Both lines as one string, for tests and tooltips."""
        return f"{self.title} — {self.place}"
