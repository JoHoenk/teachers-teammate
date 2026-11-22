"""The "Find the AI server" dialog: look for Ollama on this computer, then on the network.

Opens with the quick search (this computer, and the Windows computer behind a WSL session).
The search of the school network asks many computers, so it starts only from its own button,
after the person confirmed what it does.  Nothing is changed here: the dialog only reports
which server the person picked (:attr:`ServerSearchDialog.server_chosen`).
"""

from __future__ import annotations

from collections.abc import Callable
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...application.composition import AppServices
from ...domain.ollama_discovery import DiscoveredServer, DiscoveryScope, ServerKind
from ...texts import dialogs as dlg
from ...texts import servers
from .. import _dialogs
from .._background import LatestTask
from .._server_card import ServerCard


class ServerSearchDialog(QDialog):
    """Lists the servers a search found and lets the person pick one."""

    #: The URL of the server the person chose.
    server_chosen = Signal(str)
    #: The person wants to type the address instead.
    manual_entry_requested = Signal()

    def __init__(
        self,
        app_service: AppServices,
        parent: QWidget | None = None,
        *,
        start_search: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(servers.SEARCH_TITLE)
        self.setMinimumWidth(480)
        self._service = app_service
        self._searching = False
        self._stop_event = threading.Event()
        # Only the newest search counts; closing the dialog drops what one still reports.
        self._task = LatestTask(self, network=True)
        self._scope = DiscoveryScope.THIS_COMPUTER
        self._network_searched = False

        layout = QVBoxLayout(self)
        intro = QLabel(servers.SEARCH_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # One card per server (name and models, whose computer, *Use it*): a list that is tall
        # enough to show two or three of them without scrolling.
        self.list = QListWidget()
        self.list.setMinimumHeight(220)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.itemSelectionChanged.connect(self._sync_buttons)
        self.list.itemDoubleClicked.connect(lambda _item: self._use_selected())
        layout.addWidget(self.list)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.network_button = QPushButton(servers.SEARCH_NETWORK_BUTTON)
        self.network_button.clicked.connect(self._on_network_clicked)
        self.stop_button = QPushButton(servers.SEARCH_STOP_BUTTON)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._stop)
        self.type_button = QPushButton(servers.SEARCH_TYPE_BUTTON)
        self.type_button.clicked.connect(self._on_type_clicked)
        self.use_button = QPushButton(servers.SEARCH_USE_BUTTON)
        self.use_button.setEnabled(False)
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self._use_selected)
        close = QPushButton(servers.SEARCH_CLOSE_BUTTON)
        close.clicked.connect(self.reject)
        for button in (self.network_button, self.stop_button, self.type_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.use_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        if start_search:
            self._start(DiscoveryScope.THIS_COMPUTER)

    # ── searching ─────────────────────────────────────────────────────────────

    @property
    def searching(self) -> bool:
        """Whether a search is running."""
        return self._searching

    def _start(self, scope: DiscoveryScope) -> None:
        self._scope = scope
        self._searching = True
        self._stop_event.set()  # an earlier search, if one still runs, ends
        stop_event = threading.Event()
        self._stop_event = stop_event
        self.progress.setVisible(scope is DiscoveryScope.NETWORK)
        self.progress.setRange(0, 0)
        self.status.setText(
            servers.SEARCHING_NETWORK if scope is DiscoveryScope.NETWORK else servers.SEARCHING_HERE
        )
        self.network_button.setEnabled(False)
        self.stop_button.setVisible(scope is DiscoveryScope.NETWORK)

        def search(emit: Callable[[object], None]) -> list[DiscoveredServer]:
            return self._service.ollama.discover_ollama_servers(
                scope,
                stop_event=stop_event,
                on_found=lambda server: emit(("server", server)),
                on_progress=lambda asked, total: emit(("progress", asked, total)),
            )

        self._task.submit_streaming(search, self._on_item, self._on_done)

    def _on_item(self, payload: object) -> None:
        """Show one thing the search reports: a server it found, or how far it got."""
        if not isinstance(payload, tuple):
            return
        kind, *rest = payload
        if kind == "server":
            self._add_server(rest[0] if rest else None)
        elif kind == "progress":
            self._on_progress(int(rest[0]), int(rest[1]))

    def _on_network_clicked(self) -> None:
        if _dialogs.ask(self, dlg.search_network()):
            self._network_searched = True
            self._start(DiscoveryScope.NETWORK)

    def _stop(self) -> None:
        if self._searching:
            self._stop_event.set()
            self.status.setText(servers.SEARCH_STOPPING)

    def _on_progress(self, asked: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(asked)

    def _add_server(self, server: object) -> None:
        if not isinstance(server, DiscoveredServer):
            return
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == server.url:
                return
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, server.url)
        card = ServerCard()
        card.show_discovered(server)
        card.use_requested.connect(self._use)
        if server.kind is ServerKind.NETWORK:
            card.setToolTip(servers.SEARCH_NETWORK_TOOLTIP)
        item.setSizeHint(card.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, card)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _on_done(self, result: object, error: BaseException | None) -> None:
        """Show what the search ended with."""
        self._searching = False
        if error is None and isinstance(result, list):
            for server in result:
                self._add_server(server)
        self.progress.setVisible(False)
        self.stop_button.setVisible(False)
        self.network_button.setEnabled(not self._network_searched)
        count = self.list.count()
        if count:
            self.status.setText(servers.search_found(count))
        else:
            self.status.setText(
                servers.SEARCH_NOTHING_ON_NETWORK
                if self._network_searched
                else servers.SEARCH_NOTHING_HERE
            )

    # ── choosing ──────────────────────────────────────────────────────────────

    def cards(self) -> list[ServerCard]:
        """Return the cards of the servers found so far, in the order of the list."""
        found: list[ServerCard] = []
        for row in range(self.list.count()):
            card = self.list.itemWidget(self.list.item(row))
            if isinstance(card, ServerCard):
                found.append(card)
        return found

    def selected_url(self) -> str:
        """Return the URL of the selected entry, or ``""``."""
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def _sync_buttons(self) -> None:
        self.use_button.setEnabled(bool(self.selected_url()))

    def _use_selected(self) -> None:
        self._use(self.selected_url())

    def _use(self, url: str) -> None:
        """Report *url* as the server to use, and close."""
        if not url:
            return
        self.server_chosen.emit(url)
        self.accept()

    def _on_type_clicked(self) -> None:
        self.manual_entry_requested.emit()
        self.reject()

    def done(self, result: int) -> None:  # Qt override
        """Stop a running search and drop what it still reports."""
        self._task.cancel()
        self._searching = False
        self._stop_event.set()
        super().done(result)
