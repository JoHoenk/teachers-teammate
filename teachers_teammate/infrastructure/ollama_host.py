"""The names this computer answers to, and where an Ollama server is."""

from __future__ import annotations

from dataclasses import dataclass
import functools
import logging
import socket
import threading

from ..domain.ollama_address import is_local_ollama_url, ollama_host
from ..domain.ollama_discovery import is_private_host
from .wsl import windows_host_addresses

logger = logging.getLogger(__name__)


@functools.cache
def own_host_names() -> frozenset[str]:
    """Return the names and addresses under which this computer knows itself.

    Only the machine's own host name is resolved — never an arbitrary host — so
    the lookup does not leak the Ollama URL to a DNS server.  It can still be slow on a
    misconfigured network: call :func:`warm_up_own_host_names` early so the answer is
    ready before a GUI thread asks.
    """
    names: set[str] = set()
    try:
        hostname = socket.gethostname()
        names.update({hostname, socket.getfqdn()})
        names.update(socket.gethostbyname_ex(hostname)[2])
    except OSError as exc:
        logger.debug("Cannot resolve the host names of this computer: %s", exc)
    return frozenset(name.lower() for name in names if name)


def warm_up_own_host_names() -> None:
    """Resolve this computer's own names in a background thread (idempotent, non-blocking)."""
    threading.Thread(target=own_host_names, name="own-host-names", daemon=True).start()


@dataclass(frozen=True)
class ServerAddress:
    """What the privacy rules need to know about the server at an Ollama URL."""

    host: str
    #: The server runs on this computer (or the Windows computer behind a WSL session).
    is_this_computer: bool
    #: The host is on a local network, so it is a computer the teacher controls.
    is_private: bool


def ollama_server(url: str) -> ServerAddress:
    """Return where the Ollama server at *url* is: the one place that reads the address.

    Every classification of a step on Ollama (the badge, the refusal of online text
    recognition, the number of tries) starts here, so they cannot disagree about a URL.
    """
    host = ollama_host(url)
    return ServerAddress(
        host=host,
        is_this_computer=is_local_ollama_url(
            url, own_addresses=own_host_names, windows_hosts=windows_host_addresses
        ),
        is_private=is_private_host(host),
    )
