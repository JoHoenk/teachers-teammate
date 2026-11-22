"""The line under the local AI server address: is Ollama there, and what to do if it isn't.

A teacher who has just installed the app has never heard of Ollama.  "Cannot connect" tells
them nothing; this line says whether the program is missing, installed but stopped, or running,
and offers the one button that helps.  The same widget sits under the address on *This computer*
and on the assistant's Local AI page, so both say the same thing.

Three rules shape it:

* **It asks only while it is on screen.**  A check every 3 s on this computer and every 10 s on
  a server elsewhere is cheap, but a hidden page has no reason to ask at all.
* **It never changes the address by itself.**  When a server answers somewhere else on this
  computer, the line offers "Use it" and waits for the click.
* **Nothing is installed or started without a click.**  "Get Ollama…" opens the download page;
  "Start Ollama" starts the program that is already there.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QHideEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..application.composition import AppServices
from ..application.ollama_server import OllamaStatus
from ..domain.ollama_discovery import (
    OLLAMA_DOWNLOAD_URL,
    DiscoveredServer,
    DiscoveryScope,
    OllamaState,
    ServerKind,
)
from ..texts import servers
from ._background import LatestTask, run_in_background
from ._server_card import ServerCard

#: How often the line asks again while it is on screen.
LOCAL_INTERVAL_MS = 3000
REMOTE_INTERVAL_MS = 10000
#: How long Ollama is given to come up after "Start Ollama" before the line looks again.
START_GRACE_MS = 3000


class OllamaStatusLine(QWidget):
    """One line and up to two buttons: whether Ollama answers, and what to do about it."""

    #: "Use it" was pressed: the address of the server that answered elsewhere on this computer.
    use_server_requested = Signal(str)

    def __init__(
        self,
        *,
        app_service: AppServices,
        url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """Create the line.

        Args:
            app_service: The application services; only :class:`OllamaServerService` is used.
            url: The address to ask about; :meth:`set_url` follows the page's field.
        """
        super().__init__(parent)
        self._service = app_service
        self._url = url
        self._status: OllamaStatus | None = None
        self._found: DiscoveredServer | None = None
        #: Why the last "Start Ollama" failed; shown under the not-running line, cleared on success.
        self._start_error = ""
        #: The address the silent local search ran for, so it runs once per address.
        self._searched_for = ""
        self._task = LatestTask(self)
        self._search_task = LatestTask(self)
        self._handlers: dict[QPushButton, Callable[[], None]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # A server that answers is shown as the card every place shows servers with; the other
        # states are a sentence with the button that helps.
        self._found_label = QLabel()
        self._found_label.setVisible(False)
        outer.addWidget(self._found_label)
        self._card = ServerCard()
        self._card.use_requested.connect(self.use_server_requested)
        self._card.setVisible(False)
        outer.addWidget(self._card)
        self._text_row = QWidget()
        layout = QHBoxLayout(self._text_row)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel(servers.STATUS_CHECKING)
        self._label.setWordWrap(True)
        layout.addWidget(self._label, stretch=1)
        self._buttons = [QPushButton(""), QPushButton("")]
        for button in self._buttons:
            button.setVisible(False)
            button.clicked.connect(lambda _checked=False, b=button: self._on_clicked(b))
            layout.addWidget(button)
        outer.addWidget(self._text_row)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)

    # ── public API ─────────────────────────────────────────────────────────

    def set_url(self, url: str) -> None:
        """Follow the address the page shows; another server is another question."""
        if url == self._url:
            return
        self._url = url
        self._status = None
        self._found = None
        self._searched_for = ""
        self._render()
        if self.isVisible():
            self._check()

    def url(self) -> str:
        """Return the address the line asks about."""
        return self._url

    def card(self) -> ServerCard:
        """Return the card a server that answers is shown on."""
        return self._card

    def found_caption(self) -> str:
        """Return the line above the card, or ``""`` while no server is shown."""
        return "" if self._found_label.isHidden() else self._found_label.text()

    def text(self) -> str:
        """Return what the line says now: the card's two lines, or the sentence."""
        return self._card.text() if not self._card.isHidden() else self._label.text()

    def status(self) -> OllamaStatus | None:
        """Return the last answer, or ``None`` while the first one is on its way."""
        return self._status

    def shutdown(self) -> None:
        """Drop pending answers: the page is closing."""
        self._timer.stop()
        self._task.cancel()
        self._search_task.cancel()

    # ── asking ─────────────────────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:
        """Start asking: the line is on screen now."""
        super().showEvent(event)
        self._timer.start(self._interval_ms())
        self._check()

    def hideEvent(self, event: QHideEvent) -> None:
        """Stop asking: a hidden line has nothing to say."""
        super().hideEvent(event)
        self._timer.stop()

    def _interval_ms(self) -> int:
        """Return how often to ask: often on this computer, rarely on a server elsewhere."""
        local = self._service.ollama.is_local(self._url, include_wsl_host=True)
        return LOCAL_INTERVAL_MS if local else REMOTE_INTERVAL_MS

    def _check(self) -> None:
        """Ask the service about the address, off the GUI thread."""
        url = self._url
        if not url:
            return
        self._timer.setInterval(self._interval_ms())
        self._task.submit(lambda: self._service.ollama.ollama_status(url), self._on_status)

    def _on_status(self, result: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(result, OllamaStatus):
            return
        self._status = result
        if result.state in (OllamaState.NOT_RUNNING, OllamaState.NOT_FOUND):
            self._search_local_server()
        self._render()

    def _search_local_server(self) -> None:
        """Look quietly for Ollama on this computer, once per address.

        Only this computer is asked, never the network.  A hit is offered; the address changes
        only when the teacher presses the button.
        """
        url = self._url
        if self._searched_for == url:
            return
        self._searched_for = url
        self._search_task.submit(
            lambda: self._service.ollama.discover_ollama_servers(DiscoveryScope.THIS_COMPUTER),
            lambda result, error: self._on_search_done(url, result, error),
        )

    def _on_search_done(self, url: str, result: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(result, list) or url != self._url:
            return
        saved = url.rstrip("/")
        self._found = next(
            (s for s in result if isinstance(s, DiscoveredServer) and s.url.rstrip("/") != saved),
            None,
        )
        self._render()

    # ── drawing ────────────────────────────────────────────────────────────

    def _render(self) -> None:
        """Show the card or the line, and the buttons that fit the last answer."""
        status = self._status
        found = self._found
        if status is not None and status.state is OllamaState.RUNNING:
            self._card.show_server(
                url=self._url,
                kind=_kind_of(self._url, self._service),
                version=status.version,
                model_count=status.model_count,
                offer_use=False,
            )
            self._found_label.setText(servers.card_found(self._url))
            self._show_card(True)
            return
        if status is not None and found is not None:
            self._card.show_discovered(found)
            self._found_label.setText(servers.card_found(found.url))
            self._show_card(True)
            return
        self._show_card(False)
        text, actions = self._state_text()
        self._label.setText(text)
        self._handlers = {}
        for button, (label, handler) in zip(self._buttons, actions, strict=False):
            button.setText(label)
            button.setVisible(True)
            self._handlers[button] = handler
        for button in self._buttons[len(actions) :]:
            button.setVisible(False)

    def _show_card(self, on: bool) -> None:
        """Show the card instead of the sentence, or the sentence instead of the card."""
        self._card.setVisible(on)
        self._found_label.setVisible(on)
        self._text_row.setVisible(not on)

    def _state_text(self) -> tuple[str, list[tuple[str, Callable[[], None]]]]:
        """Return the line and the buttons for a state with no server to show."""
        status = self._status
        if status is None:
            return servers.STATUS_CHECKING, []
        if status.state is OllamaState.NOT_FOUND:
            return servers.STATUS_NOT_FOUND, [(servers.BUTTON_GET_OLLAMA, self._open_download_page)]
        if status.state is OllamaState.NOT_RUNNING:
            if status.can_start:
                text = (
                    servers.status_start_failed(servers.STATUS_NOT_RUNNING, self._start_error)
                    if self._start_error
                    else servers.STATUS_NOT_RUNNING
                )
                return text, [(servers.BUTTON_START_OLLAMA, self._start_ollama)]
            return servers.status_not_running_hint(), []
        return servers.status_no_answer(_host(self._url)), [
            (servers.BUTTON_CHECK_AGAIN, self._check)
        ]

    def _on_clicked(self, button: QPushButton) -> None:
        handler = self._handlers.get(button)
        if handler is not None:
            handler()

    # ── the buttons ────────────────────────────────────────────────────────

    def _open_download_page(self) -> None:
        """Open Ollama's download page in the browser; nothing is installed by the app."""
        QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))

    def _start_ollama(self) -> None:
        """Start the installed Ollama, then look again once it has had time to come up."""
        self._start_error = ""
        run_in_background(self._service.ollama.start_ollama, self._on_start_done)

    def _on_start_done(self, _result: object, error: BaseException | None) -> None:
        """Look again once the program had time to come up, or say why it did not start."""
        if error is not None:
            self._start_error = str(error) or type(error).__name__
            self._render()
            return
        QTimer.singleShot(START_GRACE_MS, self._check)


def _kind_of(url: str, service: AppServices) -> ServerKind:
    """Return whose computer *url* is on: this one, the Windows PC behind WSL, or another."""
    if service.ollama.is_local(url):
        return ServerKind.THIS_COMPUTER
    if service.ollama.is_local(url, include_wsl_host=True):
        return ServerKind.WINDOWS_HOST
    return ServerKind.NETWORK


def _host(url: str) -> str:
    """Return the host part of *url*, as the line names the server."""
    return urlparse(url).hostname or url
