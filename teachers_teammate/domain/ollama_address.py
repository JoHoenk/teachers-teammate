"""An Ollama address read the same way everywhere: its form, its host, and whose computer it is.

Pure: what an address is made of, given as strings and as lookups handed in by the caller.  Which
names this computer answers to — its host name, its addresses, the Windows host behind a WSL
session — is read by :mod:`~teachers_teammate.infrastructure.ollama_host`; whoever asks (that
module, and ``OllamaServerService.is_local``) passes the lookups.  The cheap cases are answered
without ever calling them.

Every classification of a step on Ollama (the badge, the refusal of online text recognition, the
number of tries) starts in :func:`is_local_ollama_url`, so they cannot disagree about an address.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from urllib.parse import urlparse

from .ollama_discovery import DEFAULT_SERVER_URL, ServerKind, classify_host


def normalize_ollama_url(url: str) -> str:
    """Return a normalized Ollama base URL.

    Accepts bare host:port inputs (for example ``127.0.0.1:11434``) and prepends ``http://`` so
    HTTP clients can use it directly.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return DEFAULT_SERVER_URL
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned.rstrip("/")


def ollama_host(url: str) -> str:
    """Return the host name of the Ollama server at *url* (the default server when empty)."""
    normalized = normalize_ollama_url(url)
    return urlparse(normalized).hostname or normalized


def is_local_ollama_url(
    url: str,
    *,
    own_addresses: Callable[[], Collection[str]],
    windows_hosts: Callable[[], Collection[str]],
) -> bool:
    """Return whether *url* points at this computer.

    True for ``localhost``, the whole loopback range (``127.0.0.0/8``, ``::1``), ``0.0.0.0`` and —
    when *own_addresses* names them — this computer's own host name or address, so the machine's
    LAN IP counts as local too.  With *windows_hosts*, the Windows computer behind a WSL session
    counts as well: it is the same physical computer, so nothing sent to it leaves the teacher's
    computer.  A caller that reads this computer's graphics card or memory passes ``tuple`` for
    *windows_hosts*: from inside WSL those figures are the Linux side's, not the Windows
    computer's that actually runs Ollama.

    Both lookups are called only when the cheap checks did not answer, so the usual address costs
    nothing.  False for a server on the network or on the internet, and for a name no list holds:
    the address itself is never looked up (that would tell a name server where it is).
    """
    host = ollama_host(url)
    if classify_host(host) is ServerKind.THIS_COMPUTER:
        return True
    kind = classify_host(host, own_addresses=own_addresses(), windows_hosts=windows_hosts())
    return kind is not ServerKind.NETWORK


def models_include(model_name: str, available: Collection[str]) -> bool:
    """Return whether *model_name* is present in *available*.

    Matches an exact name (``"llama3:8b"``) and also a bare base name (``"llama3"``) against any
    installed tag, since Ollama model identifiers carry an optional ``:tag`` suffix that callers
    may omit.
    """
    if not model_name:
        return False
    model_base = model_name.split(":", maxsplit=1)[0]
    return any(m == model_name or m.split(":", maxsplit=1)[0] == model_base for m in available)
