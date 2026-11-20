"""Finding an Ollama server: where to look, and whose computer an answer comes from.

Pure rules, no I/O: the infrastructure reads the files and asks the network, these functions
say which addresses are worth asking and what a hit means.  The distinction that matters to a
teacher is *whose* computer a server runs on, because that decides what leaves the teacher's
own computer (see :mod:`~teachers_teammate.domain.data_location`):

* :attr:`ServerKind.THIS_COMPUTER` — the computer the app runs on.
* :attr:`ServerKind.WINDOWS_HOST` — the Windows computer that hosts a WSL session the app
  runs in.  Physically it is the same computer, only the Linux side cannot reach it as
  ``127.0.0.1``.
* :attr:`ServerKind.NETWORK` — another computer (a school server, a colleague's machine).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import ipaddress
from urllib.parse import urlparse

#: The port Ollama listens on unless its owner changed it.
OLLAMA_PORT = 11434
#: Where the app looks first, and what the setting defaults to.
DEFAULT_SERVER_URL = f"http://127.0.0.1:{OLLAMA_PORT}"
#: The largest network a scan asks: one ``/24``, so 254 addresses.  A wider network (a school
#: with a ``/16``) is cut down to the ``/24`` around the computer's own address.
SCAN_PREFIX = 24

#: Where a teacher downloads Ollama from, when the app offers to open the page.
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"

#: The ranges a local network uses: RFC 1918, carrier-grade NAT (Tailscale and the like),
#: link-local IPv4 and IPv6, IPv6 unique-local and loopback.  Spelled out rather than taken from :meth:`ipaddress.IPv4Address.is_private`,
#: which also counts the documentation ranges (``203.0.113.0/24``) as private.
_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "127.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
#: Name suffixes only a local network hands out: mDNS, the router defaults (FRITZ!Box), the
#: reserved internal ones and the ones an office or school network commonly uses.  A school's own
#: domain (``ollama.myschool.de``) cannot be told from a public name; its address can.
_PRIVATE_NAME_SUFFIXES = (
    ".local",
    ".lan",
    ".home.arpa",
    ".fritz.box",
    ".intern",
    ".internal",
    ".home",
    ".corp",
)


class ServerKind(StrEnum):
    """Whose computer a found server runs on."""

    THIS_COMPUTER = "this_computer"
    WINDOWS_HOST = "windows_host"
    NETWORK = "network"


class DiscoveryScope(StrEnum):
    """How far a search looks."""

    #: This computer, and the Windows computer behind a WSL session.  Cheap and quiet.
    THIS_COMPUTER = "this_computer"
    #: The local network.  Asks many computers, so it only ever runs on request.
    NETWORK = "network"


class OllamaState(StrEnum):
    """What the app knows about Ollama at an address, so it can say what to do next."""

    #: It answered: nothing to do.
    RUNNING = "running"
    #: Found on this computer, but it does not answer: it has to be started.
    NOT_RUNNING = "not_running"
    #: An address on this computer, no answer, and no program found: it has to be installed.
    NOT_FOUND = "not_found"
    #: An address on another computer that does not answer: nothing can be started here.
    NO_ANSWER = "no_answer"


def ollama_state(*, answered: bool, local_address: bool, program_found: bool) -> OllamaState:
    """Return what the app knows about Ollama, from what it could see.

    "Not found" rather than "not installed": a custom install path cannot be seen, so the
    wording never claims more than the app knows.

    Args:
        answered: The address answered as an Ollama server.
        local_address: The address is on this computer (or the Windows host of a WSL session).
        program_found: The Ollama program was found on this computer.
    """
    if answered:
        return OllamaState.RUNNING
    if not local_address:
        return OllamaState.NO_ANSWER
    return OllamaState.NOT_RUNNING if program_found else OllamaState.NOT_FOUND


@dataclass(frozen=True)
class DiscoveredServer:
    """An Ollama server that answered."""

    url: str
    kind: ServerKind
    version: str = ""
    model_count: int | None = None
    """How many models it has downloaded; ``None`` when that was not asked."""

    @property
    def host(self) -> str:
        """The host name or address part of :attr:`url`."""
        return urlparse(self.url).hostname or self.url

    @property
    def on_this_computer(self) -> bool:
        """Whether the server runs on the computer the teacher sits at."""
        return self.kind is not ServerKind.NETWORK


def server_url(host: str, port: int = OLLAMA_PORT, scheme: str = "http") -> str:
    """Return ``scheme://host:port`` (an IPv6 address gets its brackets)."""
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{shown}:{port}"


def ollama_host_url(value: str) -> str:
    """Return the URL to try for an ``OLLAMA_HOST`` value, or ``""`` when it names none.

    Ollama reads the variable as the address to *listen* on, so ``0.0.0.0`` and ``:11434``
    mean "this computer" to anyone who connects.  The scheme and port are optional.
    """
    text = value.strip()
    if not text:
        return ""
    scheme = "http"
    if "://" in text:
        scheme, _, text = text.partition("://")
    try:
        parsed = urlparse(f"{scheme}://{text.rstrip('/')}")
        host = parsed.hostname or ""
        port = parsed.port or OLLAMA_PORT
    except ValueError:
        return ""
    if host in ("", "0.0.0.0", "::"):
        host = "127.0.0.1"
    return server_url(host, port, scheme)


def default_gateway(route_table: str) -> str:
    """Return the default gateway in a ``/proc/net/route`` listing, or ``""``.

    In WSL's default (NAT) network mode the gateway is the Windows computer.  The table prints
    addresses as hexadecimal numbers in the byte order of the machine that wrote them.
    """
    for line in route_table.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000" or fields[2] == "00000000":
            continue
        try:
            octets = bytes.fromhex(fields[2])
        except ValueError:
            continue
        if len(octets) == 4:
            return str(ipaddress.IPv4Address(octets[::-1]))
    return ""


def nameservers(resolv_conf: str) -> tuple[str, ...]:
    """Return the ``nameserver`` addresses of a ``resolv.conf`` text, in order.

    Older WSL versions point it at the Windows computer.
    """
    found: list[str] = []
    for line in resolv_conf.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            try:
                found.append(str(ipaddress.ip_address(parts[1])))
            except ValueError:
                continue
    return tuple(found)


def windows_host_addresses(route_table: str, resolv_conf: str) -> tuple[str, ...]:
    """Return the addresses under which a WSL session reaches the Windows computer.

    The gateway first, then the name servers; loopback addresses are left out (they are this
    side of the session) and so are duplicates.
    """
    candidates = [default_gateway(route_table), *nameservers(resolv_conf)]
    result: list[str] = []
    for address in candidates:
        if not address or address in result:
            continue
        if ipaddress.ip_address(address).is_loopback:
            continue
        result.append(address)
    return tuple(result)


def classify_host(
    host: str,
    *,
    own_addresses: Collection[str] = (),
    windows_hosts: Collection[str] = (),
) -> ServerKind:
    """Return whose computer *host* is: loopback and own addresses, the WSL host, or another."""
    name = host.strip("[]").lower()
    if name in ("localhost", "0.0.0.0") or name in own_addresses:
        return ServerKind.THIS_COMPUTER
    try:
        if ipaddress.ip_address(name).is_loopback:
            return ServerKind.THIS_COMPUTER
    except ValueError:
        pass
    if name in windows_hosts:
        return ServerKind.WINDOWS_HOST
    return ServerKind.NETWORK


def local_candidates(
    environment: Mapping[str, str], windows_hosts: Iterable[str]
) -> list[tuple[str, ServerKind]]:
    """Return the ``(url, kind)`` pairs worth asking on this computer, best first.

    The server named by ``OLLAMA_HOST`` (when the person set one), the default address, and
    the Windows computer behind a WSL session.  Each URL once.
    """
    hosts = tuple(windows_hosts)
    urls = [ollama_host_url(environment.get("OLLAMA_HOST", "")), DEFAULT_SERVER_URL]
    urls += [server_url(address) for address in hosts]
    seen: set[str] = set()
    result: list[tuple[str, ServerKind]] = []
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        host = urlparse(url).hostname or ""
        result.append((url, classify_host(host, windows_hosts=hosts)))
    return result


def is_private_host(host: str) -> bool:
    """Return whether *host* is an address on a local network rather than on the internet.

    True for the private and link-local ranges of IPv4 and IPv6, for loopback, for the names
    a local network hands out (``nas.local``, ``nas.lan``, ``nas.home.arpa``) and for a
    single-label name (``nas``), which no public name server resolves.  False for everything
    else, including a public address and any other name: the name is not looked up (that would
    tell a name server where the teacher's server is), so a name under a school's own domain
    counts as public until the teacher enters its address.

    The one rule for "this address is on a network the teacher controls", used by the privacy
    classification (:func:`~teachers_teammate.domain.data_location.classify_ollama`).  It is
    wider than the server search (:func:`scan_targets`), which only asks the ``/24`` of a
    private IPv4 card, so every server the search finds counts as the teacher's own.
    """
    name = host.strip().strip("[]").lower().rstrip(".")
    if not name:
        return False
    name = name.partition("%")[0]  # a zone id (``fe80::1%eth0``) names the card, not the host
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return name.endswith(_PRIVATE_NAME_SUFFIXES) or "." not in name
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in _PRIVATE_NETWORKS)


def scan_targets(address: str, prefix: int = SCAN_PREFIX) -> list[str]:
    """Return the addresses to ask on the network that *address*/*prefix* belongs to.

    Only private IPv4 networks are scanned (a school or home network, never the internet), at
    most the ``/24`` around *address*, and never the computer's own address.  An address that
    is not private, or not IPv4, gives nothing.
    """
    try:
        own = ipaddress.ip_address(address)
    except ValueError:
        return []
    if own.version != 4 or own.is_loopback or own.is_link_local:
        return []
    if not (own.is_private and is_private_host(address)):
        return []
    network = ipaddress.ip_network(f"{address}/{max(prefix, SCAN_PREFIX)}", strict=False)
    return [str(host) for host in network.hosts() if host != own]


class WaitKind(StrEnum):
    """Why a request to Ollama has no answer yet."""

    NONE = ""
    """It has one, or the model is in memory and reading the text."""
    BUSY = "busy"
    """Another model is still generating, so this request is queued behind it."""
    LOADING = "loading"
    """The model asked for is not in memory yet."""
    QUEUED = "queued"
    """Another model is in the way before the run even starts."""


@dataclass(frozen=True)
class WaitDetail:
    """What to tell the teacher about a request that has no answer yet."""

    kind: WaitKind = WaitKind.NONE
    model: str = ""
    """The model the request is for."""
    other: str = ""
    """The model in the way: generating, or loaded while this one is not."""

    def __bool__(self) -> bool:
        """Whether there is anything to say."""
        return self.kind is not WaitKind.NONE
