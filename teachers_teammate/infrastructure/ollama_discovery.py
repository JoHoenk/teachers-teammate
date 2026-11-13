"""Find Ollama servers: on this computer, on the Windows host of WSL, on the local network.

The rules (which addresses are worth asking, whose computer a hit is on) are in
:mod:`~teachers_teammate.domain.ollama_discovery`; this module asks.  What it sends is one
``GET /api/version`` per address — no document, no setting, nothing of the user's — and
only a JSON answer with a ``version`` counts as Ollama, so a web server on the same port is
not mistaken for one.

Two cautions shape it:

* The network search asks many computers, which a school's network protection may notice.
  It therefore only ever runs on request (see ``scan_network``), is limited to one private
  ``/24`` per network card, and can be stopped at any moment.
* Nothing here changes a setting.  A caller shows the hits and the person chooses.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
import http.client
import json
import os
import socket
import threading
from typing import Any, NamedTuple
import urllib.error
import urllib.request

from ..domain.ollama_discovery import (
    OLLAMA_PORT,
    SCAN_PREFIX,
    DiscoveredServer,
    classify_host,
    local_candidates,
    scan_targets,
    server_url,
)
from .wsl import windows_host_addresses

#: How long an address on this computer (or the Windows host) gets to answer.
LOCAL_TIMEOUT_S = 1.0
#: How long a computer on the network gets: a quiet address answers "no route" much sooner.
NETWORK_TIMEOUT_S = 0.3
#: Probes of a network search that run at once.
SCAN_WORKERS = 64
#: The most addresses one search asks, whatever the number of network cards.
MAX_SCAN_TARGETS = 1024
#: A reply larger than this is not Ollama's and is not read further.
_MAX_REPLY_BYTES = 256 * 1024


class ServerInfo(NamedTuple):
    """What a server told about itself."""

    version: str
    model_count: int | None


#: ``probe(url, timeout=..., count_models=...)`` → the server's answer, or ``None``.
ProbeFn = Callable[..., ServerInfo | None]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: Ollama does not send one, a router's login page might."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def _get_json(url: str, timeout: float) -> Any:
    """Return the JSON body of ``GET url``, or ``None`` on any failure."""
    # No proxy: the addresses are on this computer or its own network.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            return json.loads(response.read(_MAX_REPLY_BYTES))
    except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError):
        return None


def probe_server(
    url: str, *, timeout: float = LOCAL_TIMEOUT_S, count_models: bool = True
) -> ServerInfo | None:
    """Return what the Ollama server at *url* says about itself, or ``None`` if it is not one.

    *count_models* also asks how many models it has (a second, slower request): wanted for
    a hit, not for each of 254 addresses.
    """
    reply = _get_json(f"{url.rstrip('/')}/api/version", timeout)
    if not isinstance(reply, dict) or not isinstance(reply.get("version"), str):
        return None
    models: int | None = None
    if count_models:
        tags = _get_json(f"{url.rstrip('/')}/api/tags", max(timeout, 2.0))
        if isinstance(tags, dict) and isinstance(tags.get("models"), list):
            models = len(tags["models"])
    return ServerInfo(reply["version"], models)


def discover_this_computer(
    *,
    environment: Mapping[str, str] | None = None,
    windows_hosts: Collection[str] | None = None,
    probe: ProbeFn = probe_server,
) -> list[DiscoveredServer]:
    """Return the servers that answer on this computer and on the Windows host of WSL.

    Asks the address ``OLLAMA_HOST`` names, the default address, and — inside WSL — the
    addresses of the Windows computer.  Quick: a few addresses, answered or refused at once.
    """
    env = os.environ if environment is None else environment
    hosts = windows_host_addresses() if windows_hosts is None else tuple(windows_hosts)
    candidates = local_candidates(env, hosts)
    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        infos = list(pool.map(lambda c: probe(c[0], timeout=LOCAL_TIMEOUT_S), candidates))
    return [
        DiscoveredServer(url, kind, info.version, info.model_count)
        for (url, kind), info in zip(candidates, infos, strict=True)
        if info is not None
    ]


def _interfaces_from_psutil() -> list[tuple[str, int]] | None:
    """Return the IPv4 cards as psutil sees them, or ``None`` when psutil is not installed."""
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return None
    found: list[tuple[str, int]] = []
    for addresses in psutil.net_if_addrs().values():
        for entry in addresses:
            if entry.family != socket.AF_INET or not entry.address or not entry.netmask:
                continue
            prefix = sum(bin(int(part)).count("1") for part in entry.netmask.split("."))
            found.append((entry.address, prefix))
    return found


def private_interfaces() -> list[tuple[str, int]]:
    """Return ``(address, prefix length)`` of each IPv4 network card of this computer."""
    cards = _interfaces_from_psutil()
    if cards is not None:
        return cards
    try:
        _name, _aliases, addresses = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        return []
    return [(address, SCAN_PREFIX) for address in addresses]


def scan_network(
    *,
    stop_event: threading.Event | None = None,
    on_found: Callable[[DiscoveredServer], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    interfaces: list[tuple[str, int]] | None = None,
    windows_hosts: Collection[str] | None = None,
    port: int = OLLAMA_PORT,
    probe: ProbeFn = probe_server,
    workers: int = SCAN_WORKERS,
) -> list[DiscoveredServer]:
    """Ask the private network around this computer for Ollama servers.

    Each network card contributes the ``/24`` around its address (private networks only),
    this computer's own addresses excluded.  *on_found* is called from worker threads, once
    per hit; *on_progress* gets ``(asked, total)``.  Setting *stop_event* ends the search
    early with what was found so far.

    Never call this without the person asking for it: it contacts every address of the range.
    """
    cards = private_interfaces() if interfaces is None else interfaces
    own = {address for address, _prefix in cards}
    hosts = windows_host_addresses() if windows_hosts is None else tuple(windows_hosts)
    targets: list[str] = []
    for address, prefix in cards:
        for target in scan_targets(address, prefix):
            if target not in targets:
                targets.append(target)
    targets = targets[:MAX_SCAN_TARGETS]
    total = len(targets)
    found: list[DiscoveredServer] = []
    lock = threading.Lock()
    asked = 0

    def ask(host: str) -> DiscoveredServer | None:
        if stop_event is not None and stop_event.is_set():
            return None
        url = server_url(host, port)
        if probe(url, timeout=NETWORK_TIMEOUT_S, count_models=False) is None:
            return None
        info = probe(url, timeout=LOCAL_TIMEOUT_S, count_models=True)
        if info is None:
            return None
        kind = classify_host(host, own_addresses=own, windows_hosts=hosts)
        return DiscoveredServer(url, kind, info.version, info.model_count)

    if on_progress is not None:
        on_progress(0, total)
    if not total:
        return found
    with ThreadPoolExecutor(max_workers=min(workers, total)) as pool:
        futures = [pool.submit(ask, host) for host in targets]
        for future in as_completed(futures):
            if future.cancelled():
                continue
            hit = future.result()
            with lock:
                asked += 1
                done = asked
                if hit is not None:
                    found.append(hit)
            if hit is not None and on_found is not None:
                on_found(hit)
            if on_progress is not None:
                on_progress(done, total)
            if stop_event is not None and stop_event.is_set():
                for pending in futures:
                    pending.cancel()
    return sorted(found, key=lambda server: server.url)
