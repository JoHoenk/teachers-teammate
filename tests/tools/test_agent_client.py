"""Unit tests for the tool's side of the memory agent: the four requests and the wait."""

from __future__ import annotations

from contextlib import contextmanager
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools.model_eval import gpu_agent
from tools.model_eval.agent_client import AgentClient, AgentError, WindowStats

_MIB = 1024 * 1024


class _Source:
    """A sample source a test drives: the card's use and the main memory's availability."""

    def __init__(self, readings) -> None:
        self._readings = list(readings)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            if len(self._readings) > 1:
                return self._readings.pop(0)
            return self._readings[0]


def _agent(readings, *, sha="abc", cards=(), ram_total=None):
    """Return an agent whose sample source walks *readings*."""
    return gpu_agent.Agent(
        vendor="amd",
        source="sysfs",
        interval_s=0.2,
        keep_s=600.0,
        cards=list(cards),
        ram_total_bytes=ram_total,
        read_sample=_Source(readings),
        agent_sha256=sha,
    )


@contextmanager
def _serving(agent, token=None):
    """Run *agent* on a free loopback port and yield its base URL."""
    server = gpu_agent.AgentServer(("127.0.0.1", 0), agent, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _plain_server(payload):
    """Run a server that answers *payload* to every request and yield its base URL."""

    class _Handler(BaseHTTPRequestHandler):
        """Answers every request with *payload*, as a server that is not the agent would."""

        def do_GET(self) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # pylint: disable=W0622
            """Stay quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_the_client_reads_what_the_agent_is() -> None:
    """
    Given  an agent with one card and one sample
    When   the client asks what it is
    Then   the fields of /info come out as the client's own types
    """
    cards = [gpu_agent.CardReading(0, "AMD Radeon RX 9070 XT", 715128832, 17095983104)]
    agent = _agent([(715128832, 30100000000)], cards=cards, ram_total=33285996544)
    agent.take_sample()

    with _serving(agent) as url:
        info = AgentClient(url).info()

    assert info.agent == gpu_agent.AGENT_VERSION
    assert info.agent_sha256 == "abc"
    assert info.vendor == "amd"
    assert info.source == "sysfs"
    assert info.cards[0].name == "AMD Radeon RX 9070 XT"
    assert info.cards[0].total_bytes == 17095983104
    assert info.card_total_bytes == 17095983104
    assert info.ram_total_bytes == 33285996544
    assert info.card_used_bytes == 715128832
    assert info.ram_available_bytes == 30100000000


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_window_reports_the_peak_the_client_asked_for() -> None:
    """
    Given  an agent whose card rises to a known peak inside a window
    When   the client opens and closes the window
    Then   the peak, the increase and the main memory's fall come out
    """
    agent = _agent([(100, 1000), (100, 1000), (300, 900), (200, 800), (150, 700)])
    agent.take_sample()

    with _serving(agent) as url:
        client = AgentClient(url)
        window = client.open_window()
        agent.take_sample()
        agent.take_sample()
        stats = client.close_window(window)

    assert isinstance(stats, WindowStats)
    assert stats.card_peak_bytes == 300
    assert stats.card_increase_bytes == 200
    assert stats.ram_drop_bytes == 300
    assert stats.samples == 4


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_settle_waits_for_a_falling_baseline() -> None:
    """
    Given  an agent whose card is still giving memory back
    When   the client waits for it to settle
    Then   the wait ends once several readings in a row agree, and returns that figure
    """
    agent = _agent(
        [(900 * _MIB, None), (700 * _MIB, None)] + [(650 * _MIB, None)] * 6,
    )
    agent.take_sample()

    with _serving(agent) as url:
        client = AgentClient(url, sleep=lambda seconds: agent.take_sample())
        settled = client.settle(tolerance_bytes=64 * _MIB, every_s=0.0, max_s=10.0)

    assert settled == 650 * _MIB


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_settle_does_not_stop_on_a_pause_of_two_readings() -> None:
    """
    Given  a card that holds still for two readings and then gives much more memory back
    When   the client waits for it to settle
    Then   the pause is not taken for the idle figure
    """
    agent = _agent(
        [(900 * _MIB, None), (900 * _MIB, None)] + [(300 * _MIB, None)] * 8,
    )
    agent.take_sample()

    with _serving(agent) as url:
        client = AgentClient(url, sleep=lambda seconds: agent.take_sample())
        settled = client.settle(tolerance_bytes=64 * _MIB, every_s=0.0, max_s=10.0)

    assert settled == 300 * _MIB


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_settle_gives_up_after_the_longest_wait_and_returns_the_last_reading() -> None:
    """
    Given  an agent whose card keeps moving
    When   the client waits for it to settle
    Then   the wait ends after the longest time and returns the last figure it saw
    """
    agent = _agent([(900 * _MIB, None), (100 * _MIB, None), (900 * _MIB, None), (100 * _MIB, None)])
    agent.take_sample()

    with _serving(agent) as url:
        client = AgentClient(url, sleep=lambda seconds: agent.take_sample())
        settled = client.settle(tolerance_bytes=1, every_s=1.0, max_s=2.0)

    assert settled == 900 * _MIB


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_wrong_token_is_reported_as_such() -> None:
    """
    Given  an agent that wants a token
    When   the client sends none
    Then   the error says the token is wrong
    """
    agent = _agent([(100, None)])
    agent.take_sample()

    with _serving(agent, token="secret") as url:
        with pytest.raises(AgentError, match="wrong token"):
            AgentClient(url).info()


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_an_agent_without_samples_is_reported_as_such() -> None:
    """
    Given  an agent that was never started
    When   the client opens a window
    Then   the error says the agent gets no samples
    """
    with _serving(_agent([(100, None)])) as url:
        with pytest.raises(AgentError, match="gets no samples"):
            AgentClient(url).open_window()


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_server_that_stops_answering_is_an_agent_error() -> None:
    """
    Given  an agent that has been shut down
    When   the client asks it anything
    Then   the error is an OSError, so a caller that treats the server as gone catches it
    """
    agent = _agent([(100, None)])
    agent.take_sample()
    with _serving(agent) as url:
        pass

    with pytest.raises(AgentError) as raised:
        AgentClient(url, timeout_s=1.0).info()

    assert isinstance(raised.value, OSError)


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_an_answer_that_is_not_the_agent_is_refused() -> None:
    """
    Given  a server that answers JSON but is not the memory agent
    When   the client asks what it is
    Then   the answer is refused instead of being read as an agent's
    """
    with _plain_server({"version": "0.5.1"}) as url:
        with pytest.raises(AgentError, match="does not answer as the memory agent"):
            AgentClient(url).info()


@pytest.mark.use_case("Model_Eval_Gpu_Agent")
def test_a_window_that_is_not_open_is_reported() -> None:
    """
    Given  an agent with no window open
    When   the client closes window 7
    Then   the error says there is no such window
    """
    agent = _agent([(100, None)])
    agent.take_sample()

    with _serving(agent) as url:
        with pytest.raises(AgentError, match="no such window"):
            AgentClient(url).close_window(7)
