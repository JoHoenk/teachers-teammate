"""How the app reads an Ollama address: its normal form, its host, and whose computer it is.

Pure rules, so the test hands in the names this computer answers to instead of reading them.
"""

from __future__ import annotations

import pytest

from teachers_teammate.domain.ollama_address import (
    is_local_ollama_url,
    models_include,
    normalize_ollama_url,
    ollama_host,
)

# ── normalize_ollama_url ───────────────────────────────────────────────────


def test_normalize_ollama_url_prepends_scheme_to_bare_host_port() -> None:
    """
    Given  a bare host:port string with no scheme
    When   normalize_ollama_url() is called
    Then   http:// is prepended and the result has no trailing slash
    """
    assert normalize_ollama_url("127.0.0.1:11434") == "http://127.0.0.1:11434"


def test_normalize_ollama_url_returns_default_for_empty_string() -> None:
    """
    Given  an empty string
    When   normalize_ollama_url() is called
    Then   the default URL "http://127.0.0.1:11434" is returned
    """
    assert normalize_ollama_url("") == "http://127.0.0.1:11434"


def test_normalize_ollama_url_strips_trailing_slash() -> None:
    """
    Given  a fully-qualified URL with a trailing slash
    When   normalize_ollama_url() is called
    Then   the trailing slash is removed
    """
    assert normalize_ollama_url("http://localhost:11434/") == "http://localhost:11434"


def test_normalize_ollama_url_leaves_well_formed_url_unchanged() -> None:
    """
    Given  a URL that already has a scheme and no trailing slash
    When   normalize_ollama_url() is called
    Then   the URL is returned as-is
    """
    url = "http://192.168.1.10:11434"
    assert normalize_ollama_url(url) == url


# ── ollama_host ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("http://192.168.1.5:11434", "192.168.1.5"),
        ("192.168.1.5:11434", "192.168.1.5"),
        ("https://ollama.example.com/", "ollama.example.com"),
        ("http://[::1]:11434", "::1"),
        ("", "127.0.0.1"),
    ],
)
def test_ollama_host_names_the_server_a_url_points_at(url: str, host: str) -> None:
    """
    Given  an Ollama URL in any of the forms the app accepts
    When   ollama_host() reads it
    Then   the host name is returned, and an empty URL gives this computer
    """
    assert ollama_host(url) == host


# ── is_local_ollama_url ────────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434", True),
        ("127.0.0.1:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://127.8.8.8:11434", True),  # whole 127.0.0.0/8 loopback range
        ("http://[::1]:11434", True),
        ("http://0.0.0.0:11434", True),
        ("", True),  # default URL is local
        ("http://192.168.1.50:11434", False),
        ("myserver.lan:11434", False),
        ("http://ollama.example.com", False),
        ("http://127.example.com:11434", False),  # a host NAME that merely starts with "127."
    ],
)
def test_is_local_ollama_url(url: str, expected: bool) -> None:
    """
    Given  an Ollama server URL (and no own addresses handed in)
    When   is_local_ollama_url is called
    Then   loopback addresses (and the default URL) count as local, hosts do not
    """
    assert is_local_ollama_url(url, own_addresses=tuple, windows_hosts=tuple) is expected


@pytest.mark.parametrize(
    "url", ["", "localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"]
)
def test_is_local_ollama_url_answers_loopback_without_looking_up_own_names(url: str) -> None:
    """
    Given  a lookup of the computer's own names that must never run
    When   is_local_ollama_url is called with the default or a loopback URL
    Then   it answers True without that (possibly slow) lookup
    """

    def forbidden() -> frozenset[str]:
        raise AssertionError("own names looked up for a loopback URL")

    assert is_local_ollama_url(url, own_addresses=forbidden, windows_hosts=forbidden) is True


@pytest.mark.use_case("Hardware_Profile_Override")
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://studio-pc:11434", True),  # this computer's host name
        ("http://STUDIO-PC.local:11434", True),  # case does not matter
        ("http://10.0.0.7:11434", True),  # this computer's own LAN address
        ("http://10.0.0.8:11434", False),  # another computer on the same network
    ],
)
def test_is_local_ollama_url_recognises_the_computers_own_names(url: str, expected: bool) -> None:
    """
    Given  a computer that knows itself as "studio-pc", "studio-pc.local" and 10.0.0.7
    When   the Ollama URL names one of these
    Then   it counts as local (the GPU of this computer is the one that runs the models)
    """

    def names() -> frozenset[str]:
        return frozenset({"studio-pc", "studio-pc.local", "10.0.0.7"})

    assert is_local_ollama_url(url, own_addresses=names, windows_hosts=tuple) is expected


@pytest.mark.use_case("Hardware_Profile_Override")
@pytest.mark.parametrize(
    ("url", "wsl", "expected"),
    [
        ("http://172.28.0.1:11434", True, True),  # the Windows computer behind WSL
        ("http://172.28.0.1:11434", False, False),  # a figure of the Linux side is not its own
    ],
)
def test_the_windows_host_of_wsl_counts_only_when_it_is_asked_for(
    url: str, wsl: bool, expected: bool
) -> None:
    """
    Given  the address of the Windows computer behind this WSL session
    When   is_local_ollama_url is asked with and without that host
    Then   it counts as this computer only for a caller that hands it in
    """

    def windows_hosts() -> frozenset[str]:
        return frozenset({"172.28.0.1"})

    assert (
        is_local_ollama_url(url, own_addresses=tuple, windows_hosts=windows_hosts if wsl else tuple)
        is expected
    )


# ── models_include ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "installed", "expected"),
    [
        ("llama3:8b", ["llama3:8b", "qwen3:4b"], True),
        ("llama3", ["llama3:8b"], True),  # a bare base name matches any tag of it
        ("llama3", ["llama3.1:8b"], False),  # a longer name is another model
        ("qwen3:4b", [], False),
        ("", ["llama3:8b"], False),
    ],
)
def test_models_include_matches_the_name_or_its_base(
    model: str, installed: list[str], expected: bool
) -> None:
    """
    Given  an installed model list
    When   a configured model is looked for
    Then   an exact tag and a bare base name are found, and nothing else is
    """
    assert models_include(model, installed) is expected
