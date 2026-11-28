"""Unit tests for teachers_teammate.infrastructure.ollama_{kind}."""

from __future__ import annotations

import socket
import threading

import pytest

from teachers_teammate.domain import ollama_address
from teachers_teammate.infrastructure import ollama_host


_REAL_OWN_HOST_NAMES = ollama_host.own_host_names  # tests below monkeypatch the module name


@pytest.fixture(autouse=True)
def _fresh_own_host_names():
    """Keep the cached lookup of the computer's own names from leaking between tests."""
    _REAL_OWN_HOST_NAMES.cache_clear()
    yield
    _REAL_OWN_HOST_NAMES.cache_clear()


# ── ollama_server, and the names this computer answers to ──────────────────


@pytest.mark.parametrize(
    ("url", "this_computer", "private"),
    [
        ("", True, True),
        ("http://localhost:11434", True, True),
        ("http://172.28.0.1:11434", True, True),  # the Windows computer behind WSL
        ("http://192.168.1.5:11434", False, True),
        ("nas.fritz.box:11434", False, True),
        ("http://203.0.113.7:11434", False, False),
        ("https://ollama.example.com", False, False),
    ],
)
def test_ollama_server_says_where_the_address_is(
    url: str, this_computer: bool, private: bool, monkeypatch
) -> None:
    """
    Given  an Ollama URL (and a computer that knows itself under no other name, behind a WSL
           session whose Windows host is 172.28.0.1)
    When   ollama_server() reads it
    Then   it names the host, and says whether it is this computer and whether it is on a
           local network, the one reading every privacy rule starts from
    """
    monkeypatch.setattr(ollama_host, "own_host_names", lambda: frozenset())
    monkeypatch.setattr(ollama_host, "windows_host_addresses", lambda: ("172.28.0.1",))

    server = ollama_host.ollama_server(url)

    assert server.host == ollama_address.ollama_host(url)
    assert (server.is_this_computer, server.is_private) == (this_computer, private)


@pytest.mark.use_case("Hardware_Profile_Override")
def test_own_host_names_collects_name_and_addresses_and_survives_lookup_errors(
    monkeypatch,
) -> None:
    """
    Given  the host-name lookups of the socket module
    When   the own names are collected
    Then   host name, FQDN and addresses are lower-cased into one set, and a failing
           lookup yields an empty set instead of an error
    """

    def uncached() -> frozenset[str]:
        ollama_host.own_host_names.cache_clear()
        return ollama_host.own_host_names()

    monkeypatch.setattr(ollama_host.socket, "gethostname", lambda: "Studio-PC")
    monkeypatch.setattr(ollama_host.socket, "getfqdn", lambda: "Studio-PC.local")
    monkeypatch.setattr(
        ollama_host.socket, "gethostbyname_ex", lambda _h: ("Studio-PC", [], ["10.0.0.7"])
    )
    assert uncached() == frozenset({"studio-pc", "studio-pc.local", "10.0.0.7"})

    def failing(_host: str):
        raise OSError("no resolver")

    monkeypatch.setattr(ollama_host.socket, "gethostbyname_ex", failing)
    # Names found before the failing lookup are kept.
    assert uncached() == frozenset({"studio-pc", "studio-pc.local"})


@pytest.mark.use_case("Hardware_Profile_Override")
def test_warm_up_resolves_own_names_once_in_the_background(monkeypatch) -> None:
    """
    Given  an empty cache of this computer's own names
    When   the warm-up runs and the names are then asked for
    Then   the lookup ran once, off the calling thread, and later asks reuse its answer
    """
    import threading  # noqa: PLC0415

    calls: list[str] = []

    def hostname() -> str:
        calls.append(threading.current_thread().name)
        return "studio-pc"

    ollama_host.own_host_names.cache_clear()
    monkeypatch.setattr(ollama_host.socket, "gethostname", hostname)
    monkeypatch.setattr(ollama_host.socket, "getfqdn", lambda: "studio-pc")
    monkeypatch.setattr(ollama_host.socket, "gethostbyname_ex", lambda _h: ("", [], []))

    ollama_host.warm_up_own_host_names()
    names = ollama_host.own_host_names()

    assert names == frozenset({"studio-pc"})
    assert calls == ["own-host-names"]
