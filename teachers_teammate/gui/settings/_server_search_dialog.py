"""The "Find the AI server" dialog: look for Ollama on this computer, then on the network.

Opens with the quick search (this computer, and the Windows computer behind a WSL session).
The search of the school network asks many computers, so it starts only from its own button,
after the person confirmed what it does.  Nothing is changed here: the dialog only reports
which server the person picked (:attr:`ServerSearchDialog.server_chosen`).
"""

from __future__ import annotations

import threading
from typing import Protocol

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...domain.ollama_discovery import DiscoveredServer, DiscoveryScope, ServerKind
from .._model_fetch import _retire_thread, _start_thread

TITLE = "Find the AI server"
INTRO = (
    "Looks for a local AI server (Ollama) that answers. Nothing is changed until you choose "
    "one and press “Use this server”."
)
SEARCHING_HERE = "Searching this computer…"
SEARCHING_NETWORK = "Searching the network…"
NOTHING_HERE = (
    "No server answered on this computer.\n\n"
    "• Is Ollama started? Open the Ollama app, or run “ollama serve”.\n"
    "• Ollama on the Windows computer, and this app in WSL: Ollama has to listen on the "
    "network there (set OLLAMA_HOST=0.0.0.0 and restart it), and the firewall has to allow "
    "it.\n"
    "• A school server: use “Search the network…”."
)
NOTHING_ON_NETWORK = (
    "No server answered on the network. It may be switched off, listen only on its own "
    "computer, or sit behind a firewall. Ask your IT department for its address and type it "
    "in by hand."
)
NETWORK_BUTTON = "Search the network…"
STOP_BUTTON = "Stop"
USE_BUTTON = "Use this server"
TYPE_BUTTON = "Enter address myself"
CLOSE_BUTTON = "Close"
NETWORK_CONFIRM_TITLE = "Search the network?"
NETWORK_CONFIRM = (
    "The app will ask every computer in your network range (up to 254 addresses per "
    "network connection) whether it runs Ollama. No documents or settings are sent.\n\n"
    "Some school networks notice such a search. Only continue if your IT department allows "
    "it."
)
_KIND_NAMES = {
    ServerKind.THIS_COMPUTER: "This computer",
    ServerKind.WINDOWS_HOST: "Windows (this computer)",
    ServerKind.NETWORK: "Another computer on your network",
}
_NETWORK_TOOLTIP = (
    "Your documents would be sent to that computer. Only choose a server that your school runs."
)


def server_label(server: DiscoveredServer) -> str:
    """Return the two-line text of a list entry: whose computer, then address and details."""
    details = [server.url]
    if server.version:
        details.append(f"Ollama {server.version}")
    if server.model_count is not None:
        noun = "model" if server.model_count == 1 else "models"
        details.append(f"{server.model_count} {noun}")
    return f"{_KIND_NAMES[server.kind]}\n{' · '.join(details)}"


class _DiscoveryService(Protocol):
    def discover_ollama_servers(
        self,
        scope: DiscoveryScope = ...,
        *,
        stop_event: threading.Event | None = ...,
        on_found=...,
        on_progress=...,
    ) -> list[DiscoveredServer]: ...


class _SearchThread(QThread):
    """Runs one search off the GUI thread and reports hits as they come."""

    found = Signal(object)  # DiscoveredServer
    progress = Signal(int, int)  # asked, total
    search_done = Signal(list)

    def __init__(self, service: _DiscoveryService, scope: DiscoveryScope) -> None:
        super().__init__()
        self._service = service
        self._scope = scope
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            servers = self._service.discover_ollama_servers(
                self._scope,
                stop_event=self.stop_event,
                on_found=self.found.emit,
                on_progress=self.progress.emit,
            )
        except Exception:  # noqa: BLE001  # a search that fails finds nothing; it never raises into Qt
            servers = []
        self.search_done.emit(servers)


class ServerSearchDialog(QDialog):
    """Lists the servers a search found and lets the person pick one."""

    #: The URL of the server the person chose.
    server_chosen = Signal(str)
    #: The person wants to type the address instead.
    manual_entry_requested = Signal()

    def __init__(
        self,
        app_service: _DiscoveryService,
        parent: QWidget | None = None,
        *,
        start_search: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(480)
        self._service = app_service
        self._thread: _SearchThread | None = None
        self._scope = DiscoveryScope.THIS_COMPUTER
        self._network_searched = False

        layout = QVBoxLayout(self)
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list = QListWidget()
        self.list.setMinimumHeight(120)
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
        self.network_button = QPushButton(NETWORK_BUTTON)
        self.network_button.clicked.connect(self._on_network_clicked)
        self.stop_button = QPushButton(STOP_BUTTON)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._stop)
        self.type_button = QPushButton(TYPE_BUTTON)
        self.type_button.clicked.connect(self._on_type_clicked)
        self.use_button = QPushButton(USE_BUTTON)
        self.use_button.setEnabled(False)
        self.use_button.setDefault(True)
        self.use_button.clicked.connect(self._use_selected)
        close = QPushButton(CLOSE_BUTTON)
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
        return self._thread is not None and self._thread.isRunning()

    def _start(self, scope: DiscoveryScope) -> None:
        self._scope = scope
        self.progress.setVisible(scope is DiscoveryScope.NETWORK)
        self.progress.setRange(0, 0)
        self.status.setText(
            SEARCHING_NETWORK if scope is DiscoveryScope.NETWORK else SEARCHING_HERE
        )
        self.network_button.setEnabled(False)
        self.stop_button.setVisible(scope is DiscoveryScope.NETWORK)
        thread = _SearchThread(self._service, scope)
        thread.found.connect(self._add_server)
        thread.progress.connect(self._on_progress)
        thread.search_done.connect(self._on_done)
        self._thread = thread
        _start_thread(thread)

    def _on_network_clicked(self) -> None:
        answer = QMessageBox.question(
            self,
            NETWORK_CONFIRM_TITLE,
            NETWORK_CONFIRM,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._network_searched = True
            self._start(DiscoveryScope.NETWORK)

    def _stop(self) -> None:
        if self._thread is not None:
            self._thread.stop_event.set()
            self.status.setText("Stopping…")

    def _on_progress(self, asked: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(asked)

    def _add_server(self, server: object) -> None:
        if not isinstance(server, DiscoveredServer):
            return
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == server.url:
                return
        item = QListWidgetItem(server_label(server))
        item.setData(Qt.ItemDataRole.UserRole, server.url)
        if server.kind is ServerKind.NETWORK:
            item.setToolTip(_NETWORK_TOOLTIP)
        self.list.addItem(item)
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def _on_done(self, servers: object) -> None:
        if isinstance(servers, list):
            for server in servers:
                self._add_server(server)
        self.progress.setVisible(False)
        self.stop_button.setVisible(False)
        self.network_button.setEnabled(not self._network_searched)
        count = self.list.count()
        if count:
            noun = "server" if count == 1 else "servers"
            self.status.setText(f"Found {count} {noun}. Choose one.")
        else:
            self.status.setText(NOTHING_ON_NETWORK if self._network_searched else NOTHING_HERE)

    # ── choosing ──────────────────────────────────────────────────────────────

    def selected_url(self) -> str:
        """Return the URL of the selected entry, or ``""``."""
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def _sync_buttons(self) -> None:
        self.use_button.setEnabled(bool(self.selected_url()))

    def _use_selected(self) -> None:
        url = self.selected_url()
        if not url:
            return
        self.server_chosen.emit(url)
        self.accept()

    def _on_type_clicked(self) -> None:
        self.manual_entry_requested.emit()
        self.reject()

    def done(self, result: int) -> None:  # Qt override
        """Cut the running search loose before the dialog goes away."""
        if self._thread is not None:
            self._thread.stop_event.set()
            _retire_thread(self._thread)
        super().done(result)
