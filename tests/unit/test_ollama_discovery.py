"""Tests of the Ollama discovery: the pure rules, the probe, the local and network search."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
from typing import ClassVar

import pytest

from teachers_teammate.domain.ollama_discovery import (
    DEFAULT_SERVER_URL,
    DiscoveredServer,
    OllamaState,
    ServerKind,
    classify_host,
    default_gateway,
    local_candidates,
    nameservers,
    ollama_host_url,
    ollama_state,
    scan_targets,
    windows_host_addresses,
)
from teachers_teammate.infrastructure import ollama_discovery
from teachers_teammate.infrastructure.ollama_discovery import ServerInfo

_ROUTE = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t01001FAC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t00001FAC\t00000000\t0001\t0\t0\t0\t00F0FFFF\t0\t0\t0\n"
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("0.0.0.0", "http://127.0.0.1:11434"),
        (":11500", "http://127.0.0.1:11500"),
        ("192.168.1.20:11434", "http://192.168.1.20:11434"),
        ("https://ollama.school.example", "https://ollama.school.example:11434"),
        ("host:notaport", ""),
    ],
)
def test_ollama_host_url_turns_the_listen_address_into_a_url(value: str, expected: str) -> None:
    """
    Given  an OLLAMA_HOST value in one of the forms Ollama accepts
    When   it is turned into a URL to try
    Then   a listen-all address means this computer, and a missing port means 11434
    """
    assert ollama_host_url(value) == expected


def test_default_gateway_is_read_from_the_route_table() -> None:
    """
    Given  a /proc/net/route listing whose default route goes via 172.31.0.1 (hex, little-endian)
    When   the gateway is read
    Then   it is returned as a dotted address; a table without default route gives ""
    """
    assert default_gateway(_ROUTE) == "172.31.0.1"
    assert default_gateway(_ROUTE.splitlines()[0] + "\n") == ""


def test_windows_host_addresses_list_the_gateway_first_without_loopback_or_repeats() -> None:
    """
    Given  a route table and a resolv.conf naming the gateway, a loopback and a new address
    When   the Windows host addresses are derived
    Then   the gateway comes first, the loopback is dropped and nothing repeats
    """
    resolv = "nameserver 172.31.0.1\nnameserver 127.0.0.53\nnameserver 10.255.255.254\n"

    assert nameservers(resolv) == ("172.31.0.1", "127.0.0.53", "10.255.255.254")
    assert windows_host_addresses(_ROUTE, resolv) == ("172.31.0.1", "10.255.255.254")


def test_classify_host_tells_this_computer_the_windows_host_and_the_network_apart() -> None:
    """
    Given  a loopback name, an own address, a WSL host address and a stranger
    When   each is classified
    Then   the first two are this computer, the third the Windows host, the last the network
    """
    kwargs = {"own_addresses": {"192.168.1.5"}, "windows_hosts": {"172.31.0.1"}}

    assert classify_host("127.0.0.1", **kwargs) is ServerKind.THIS_COMPUTER
    assert classify_host("localhost", **kwargs) is ServerKind.THIS_COMPUTER
    assert classify_host("192.168.1.5", **kwargs) is ServerKind.THIS_COMPUTER
    assert classify_host("172.31.0.1", **kwargs) is ServerKind.WINDOWS_HOST
    assert classify_host("192.168.1.77", **kwargs) is ServerKind.NETWORK


def test_local_candidates_name_each_url_once_in_order() -> None:
    """
    Given  an OLLAMA_HOST that repeats the default, and one Windows host
    When   the candidates are listed
    Then   the default is asked once, then the Windows host with its own kind
    """
    got = local_candidates({"OLLAMA_HOST": "0.0.0.0"}, ["172.31.0.1"])

    assert got == [
        (DEFAULT_SERVER_URL, ServerKind.THIS_COMPUTER),
        ("http://172.31.0.1:11434", ServerKind.WINDOWS_HOST),
    ]


def test_scan_targets_stay_within_one_private_slash_24_without_the_own_address() -> None:
    """
    Given  a /16 card, a /28 card, a public address and a loopback
    When   the scan targets are computed
    Then   the /16 is cut to the /24 around the address, the /28 stays small, and the public
           address and the loopback give nothing
    """
    wide = scan_targets("192.168.7.9", 16)
    assert len(wide) == 253
    assert "192.168.7.9" not in wide
    assert wide[0] == "192.168.7.1" and wide[-1] == "192.168.7.254"
    assert len(scan_targets("10.0.0.1", 28)) == 13
    assert scan_targets("8.8.8.8", 24) == []
    assert scan_targets("127.0.0.1", 8) == []
    assert scan_targets("not an address", 24) == []


# ── probe against a real (local) HTTP server ─────────────────────────────────


class _Handler(BaseHTTPRequestHandler):
    body_for: ClassVar[dict[str, object]] = {
        "/api/version": {"version": "0.12.3"},
        "/api/tags": {"models": [{"name": "a"}, {"name": "b"}]},
    }

    def do_GET(self) -> None:
        body = self.body_for.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(name="fake_ollama")
def _fake_ollama():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_probe_reads_version_and_model_count_from_an_ollama_server(fake_ollama: str) -> None:
    """
    Given  a server that answers like Ollama
    When   it is probed with and without counting models
    Then   the version is read, and the model count only when asked
    """
    assert ollama_discovery.probe_server(fake_ollama) == ServerInfo("0.12.3", 2)
    assert ollama_discovery.probe_server(fake_ollama, count_models=False) == ServerInfo(
        "0.12.3", None
    )


def test_probe_ignores_a_server_that_is_not_ollama(fake_ollama: str, monkeypatch) -> None:
    """
    Given  a web server that answers 200 with something else, and an address nobody listens on
    When   each is probed
    Then   neither counts as Ollama
    """
    monkeypatch.setitem(_Handler.body_for, "/api/version", {"hello": "router login"})

    assert ollama_discovery.probe_server(fake_ollama) is None
    assert ollama_discovery.probe_server("http://127.0.0.1:1", timeout=0.2) is None


# ── the searches, with the probe injected ────────────────────────────────────


def _answers(*urls: str):
    def probe(url: str, *, timeout: float, count_models: bool = True):
        _ = timeout
        return ServerInfo("1.0", 3 if count_models else None) if url in urls else None

    return probe


def test_this_computer_search_reports_hits_with_their_kind() -> None:
    """
    Given  Ollama answering on the default address and on the Windows host only
    When   this computer is searched
    Then   both are returned, as this computer and as the Windows host
    """
    found = ollama_discovery.discover_this_computer(
        environment={},
        windows_hosts=["172.31.0.1"],
        probe=_answers(DEFAULT_SERVER_URL, "http://172.31.0.1:11434"),
    )

    assert [(s.url, s.kind) for s in found] == [
        (DEFAULT_SERVER_URL, ServerKind.THIS_COMPUTER),
        ("http://172.31.0.1:11434", ServerKind.WINDOWS_HOST),
    ]
    assert found[0].model_count == 3


def test_network_search_asks_only_the_slash_24_and_reports_progress() -> None:
    """
    Given  one card at 192.168.1.5/24 and Ollama on 192.168.1.20 (and on the WSL host address)
    When   the network is scanned
    Then   253 addresses are asked, own address excluded, and the hit is reported once with
           the network kind
    """
    asked: list[str] = []
    answers = _answers("http://192.168.1.20:11434")

    def probe(url: str, *, timeout: float, count_models: bool = True):
        if not count_models:
            asked.append(url)
        return answers(url, timeout=timeout, count_models=count_models)

    progress: list[tuple[int, int]] = []
    hits: list[DiscoveredServer] = []
    found = ollama_discovery.scan_network(
        interfaces=[("192.168.1.5", 24)],
        windows_hosts=[],
        probe=probe,
        on_found=hits.append,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert len(asked) == 253
    assert "http://192.168.1.5:11434" not in asked
    assert [(s.url, s.kind) for s in found] == [("http://192.168.1.20:11434", ServerKind.NETWORK)]
    assert hits == found
    assert progress[0] == (0, 253) and progress[-1] == (253, 253)


def test_network_search_stops_when_asked() -> None:
    """
    Given  a stop event that is already set
    When   the network is scanned
    Then   nothing is probed
    """
    stop = threading.Event()
    stop.set()
    calls: list[str] = []

    def probe(url: str, *, timeout: float, count_models: bool = True):
        calls.append(url)

    ollama_discovery.scan_network(
        interfaces=[("192.168.1.5", 24)], windows_hosts=[], probe=probe, stop_event=stop
    )

    assert calls == []


def test_the_wsl_host_counts_as_this_computer_for_privacy_only() -> None:
    """
    Given  a WSL session whose Windows host is 172.31.0.1
    When   is_local_ollama_url is asked about that address
    Then   it is local only when the caller asks for the WSL host to count (data location),
           not for hardware probing
    """
    from unittest.mock import patch  # noqa: PLC0415

    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    with patch.object(ollama_utils, "windows_host_addresses", return_value=("172.31.0.1",)):
        url = "http://172.31.0.1:11434"
        assert ollama_utils.is_local_ollama_url(url) is False
        assert ollama_utils.is_local_ollama_url(url, include_wsl_host=True) is True


@pytest.mark.parametrize(
    ("answered", "local_address", "program_found", "expected"),
    [
        (True, True, True, "running"),
        (True, False, False, "running"),
        (False, True, True, "not_running"),
        (False, True, False, "not_found"),
        (False, False, True, "no_answer"),
        (False, False, False, "no_answer"),
    ],
)
def test_ollama_state_says_what_to_do_next(
    answered: bool, local_address: bool, program_found: bool, expected: str
) -> None:
    """
    Given  what the app could see: whether the address answered, whether it is on this
           computer, and whether the program was found
    When   ollama_state is asked
    Then   an answer means running; no answer on this computer means not running when the
           program is there and not found when it isn't; elsewhere it is only no answer
    """
    state = ollama_state(
        answered=answered, local_address=local_address, program_found=program_found
    )

    assert state is OllamaState(expected)
