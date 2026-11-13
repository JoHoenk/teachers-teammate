"""Tests of the line under the local AI server address: its states, its buttons, its polling."""

from __future__ import annotations

import pytest

from teachers_teammate.application.service import OllamaStatus
from teachers_teammate.domain.ollama_discovery import (
    DiscoveredServer,
    DiscoveryScope,
    OllamaState,
    ServerKind,
)
from teachers_teammate.gui import _ollama_status_line as line_module
from teachers_teammate.gui._ollama_status_line import OllamaStatusLine

LOCAL = "http://127.0.0.1:11434"
REMOTE = "http://192.168.1.20:11434"


class _StatusService:
    """Answers with one fixed status, and records what it was asked."""

    def __init__(self, status: OllamaStatus, found: list[DiscoveredServer] | None = None) -> None:
        self.status = status
        self.found = found or []
        self.asked: list[str] = []
        self.searches: list[DiscoveryScope] = []
        self.started = 0

    def ollama_status(self, url: str) -> OllamaStatus:
        self.asked.append(url)
        return self.status

    def discover_ollama_servers(self, scope: DiscoveryScope) -> list[DiscoveredServer]:
        self.searches.append(scope)
        return self.found

    def start_ollama(self) -> None:
        self.started += 1


def _line(qtbot, service: _StatusService, url: str = LOCAL) -> OllamaStatusLine:
    widget = OllamaStatusLine(app_service=service, url=url)
    qtbot.addWidget(widget)
    return widget


def _buttons(widget: OllamaStatusLine) -> list[str]:
    return [b.text() for b in widget._buttons if not b.isHidden()]


@pytest.mark.gui
@pytest.mark.parametrize(
    ("status", "url", "expected_line", "expected_buttons"),
    [
        (
            OllamaStatus(OllamaState.RUNNING, version="0.12.3", model_count=2),
            LOCAL,
            "✓ Ollama 0.12.3 is running · 2 models downloaded",
            [],
        ),
        (
            OllamaStatus(OllamaState.RUNNING, version="0.12.3", model_count=1),
            LOCAL,
            "✓ Ollama 0.12.3 is running · 1 model downloaded",
            [],
        ),
        (
            OllamaStatus(OllamaState.NOT_RUNNING, can_start=True),
            LOCAL,
            "● Ollama is installed but isn't running.",
            ["Start Ollama"],
        ),
        (
            OllamaStatus(OllamaState.NOT_RUNNING),
            LOCAL,
            "● Ollama is installed but isn't running. Start the Ollama service, for example "
            "with `sudo systemctl start ollama`.",
            [],
        ),
        (
            OllamaStatus(OllamaState.NOT_FOUND),
            LOCAL,
            "● Ollama wasn't found on this computer. Install it like any other program; "
            "this line turns green by itself.",
            ["Get Ollama…"],
        ),
        (
            OllamaStatus(OllamaState.NO_ANSWER),
            REMOTE,
            "● No answer from 192.168.1.20. Is the server switched on?",
            ["Check again"],
        ),
    ],
)
def test_each_state_shows_its_line_and_its_buttons(
    qtbot, status: OllamaStatus, url: str, expected_line: str, expected_buttons: list[str]
) -> None:
    """
    Given  a server that answers, is installed but stopped, is missing, or is elsewhere and
           silent
    When   the line has its answer
    Then   it says what the teacher can do, and offers only the button that helps
    """
    service = _StatusService(status)
    widget = _line(qtbot, service, url)
    widget.show()

    qtbot.waitUntil(lambda: widget.status() is not None, timeout=3000)

    assert widget._label.text() == expected_line
    assert _buttons(widget) == expected_buttons


@pytest.mark.gui
def test_a_server_found_elsewhere_is_offered_and_never_taken_over(qtbot) -> None:
    """
    Given  a saved address that does not answer, and a server that answers on another port
    When   the silent search on this computer finds it
    Then   the line offers "Use it" with that address, and the address it asks about is
           unchanged until the teacher presses it
    """
    found = DiscoveredServer(url="http://127.0.0.1:11435", kind=ServerKind.THIS_COMPUTER)
    service = _StatusService(OllamaStatus(OllamaState.NOT_RUNNING, can_start=True), [found])
    widget = _line(qtbot, service)
    chosen: list[str] = []
    widget.use_server_requested.connect(chosen.append)
    widget.show()

    qtbot.waitUntil(lambda: _buttons(widget) == ["Use it"], timeout=3000)

    assert widget._label.text() == "✓ Ollama answers at http://127.0.0.1:11435 (this computer)"
    assert widget.url() == LOCAL
    assert chosen == []

    widget._buttons[0].click()

    assert chosen == ["http://127.0.0.1:11435"]
    assert widget.url() == LOCAL


@pytest.mark.gui
def test_the_line_asks_only_while_it_is_on_screen(qtbot) -> None:
    """
    Given  a line that is not on screen
    When   it is shown and later hidden again
    Then   it asks nothing while hidden, asks at once when shown, and stops asking when hidden
    """
    service = _StatusService(OllamaStatus(OllamaState.RUNNING, version="0.12.3", model_count=0))
    widget = _line(qtbot, service)

    assert service.asked == []
    assert widget._timer.isActive() is False

    widget.show()
    qtbot.waitUntil(lambda: service.asked == [LOCAL], timeout=3000)
    assert widget._timer.isActive() is True

    widget.hide()

    assert widget._timer.isActive() is False


@pytest.mark.gui
def test_the_interval_follows_the_address(qtbot) -> None:
    """
    Given  a local address and one on another computer
    When   the line starts asking
    Then   it asks every 3 s on this computer and every 10 s elsewhere
    """
    local = _line(qtbot, _StatusService(OllamaStatus(OllamaState.RUNNING)), LOCAL)
    remote = _line(qtbot, _StatusService(OllamaStatus(OllamaState.RUNNING)), REMOTE)

    local.show()
    remote.show()

    assert local._timer.interval() == line_module.LOCAL_INTERVAL_MS
    assert remote._timer.interval() == line_module.REMOTE_INTERVAL_MS


@pytest.mark.gui
def test_set_url_asks_at_once_about_the_new_address(qtbot) -> None:
    """
    Given  a line that is on screen and has answered for the saved address
    When   the page hands it another address
    Then   it asks about the new one at once, and forgets the server it had found
    """
    service = _StatusService(OllamaStatus(OllamaState.RUNNING, version="0.12.3", model_count=0))
    widget = _line(qtbot, service)
    widget.show()
    qtbot.waitUntil(lambda: service.asked == [LOCAL], timeout=3000)

    widget.set_url(REMOTE)

    qtbot.waitUntil(lambda: service.asked == [LOCAL, REMOTE], timeout=3000)
    assert widget.url() == REMOTE


@pytest.mark.gui
def test_start_ollama_starts_the_program_and_looks_again(qtbot, monkeypatch) -> None:
    """
    Given  a line saying Ollama is installed but isn't running
    When   "Start Ollama" is pressed
    Then   the program is started off the GUI thread, and the line looks again a moment later
    """
    service = _StatusService(OllamaStatus(OllamaState.NOT_RUNNING, can_start=True))
    widget = _line(qtbot, service)
    widget.show()
    qtbot.waitUntil(lambda: _buttons(widget) == ["Start Ollama"], timeout=3000)
    monkeypatch.setattr(
        line_module, "run_in_background", lambda fn, on_done, **_kw: (fn(), on_done(None, None))
    )
    monkeypatch.setattr(line_module.QTimer, "singleShot", lambda _ms, fn: fn())
    service.asked.clear()

    widget._buttons[0].click()

    assert service.started == 1
    qtbot.waitUntil(lambda: service.asked == [LOCAL], timeout=3000)


@pytest.mark.gui
def test_a_failed_start_says_why(qtbot, monkeypatch) -> None:
    """
    Given  a line saying Ollama is installed but isn't running
    When   "Start Ollama" is pressed and the program cannot be started
    Then   the line says why, instead of silently going on saying it isn't running
    """
    service = _StatusService(OllamaStatus(OllamaState.NOT_RUNNING, can_start=True))
    widget = _line(qtbot, service)
    widget.show()
    qtbot.waitUntil(lambda: _buttons(widget) == ["Start Ollama"], timeout=3000)
    monkeypatch.setattr(
        line_module,
        "run_in_background",
        lambda _fn, on_done, **_kw: on_done(None, OSError("exec format error")),
    )

    widget._buttons[0].click()

    assert "exec format error" in widget._label.text()


@pytest.mark.gui
def test_get_ollama_opens_the_download_page(qtbot, monkeypatch) -> None:
    """
    Given  a line saying Ollama wasn't found on this computer
    When   "Get Ollama…" is pressed
    Then   the download page is opened in the browser, and nothing is installed by the app
    """
    opened: list[str] = []
    monkeypatch.setattr(
        line_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString())
    )
    service = _StatusService(OllamaStatus(OllamaState.NOT_FOUND))
    widget = _line(qtbot, service)
    widget.show()
    qtbot.waitUntil(lambda: _buttons(widget) == ["Get Ollama…"], timeout=3000)

    widget._buttons[0].click()

    assert opened == [line_module.OLLAMA_DOWNLOAD_URL]
