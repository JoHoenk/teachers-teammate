"""The Ollama server: discovery, status, model sizes, pulls and what it has installed.

One place asks the server anything, so the address a teacher typed is used everywhere and
never replaced by a default of this computer.  What the server answered is kept here too: its
version and what it has installed are one answer, and a pull or a restart changes all of it at
once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from typing import TYPE_CHECKING

from ..domain.ollama_address import is_local_ollama_url, normalize_ollama_url
from ..domain.ollama_discovery import (
    DiscoveredServer,
    DiscoveryScope,
    OllamaState,
    WaitDetail,
    WaitKind,
    ollama_state,
)
from ..domain.ollama_snapshot import SNAPSHOT_TTL_S, ServerSnapshot
from ..domain.system_facts import ServerActivity
from ..exceptions import OllamaUnreachableError
from ..infrastructure.ollama_client import OllamaClient
from ..infrastructure.ollama_host import own_host_names, warm_up_own_host_names
from ..infrastructure.wsl import windows_host_addresses
from ..interfaces import ConnectionCheck
from ..messages import cannot_reach, detail

if TYPE_CHECKING:
    from .ports import OllamaClientFactory


@dataclass(frozen=True)
class OllamaStatus:
    """What the app knows about Ollama at an address, and what it can do about it."""

    state: OllamaState
    #: The server's version (``""`` when it did not answer).
    version: str = ""
    #: How many models it has downloaded (``None`` when it did not answer).
    model_count: int | None = None
    #: The app can start it from here (only ``NOT_RUNNING`` on Windows and macOS).
    can_start: bool = False


def ollama_client(
    url: str, *, cancel_event: threading.Event | None = None, timeout: float | None = None
) -> OllamaClient:
    """Build the Ollama client of one address: the one factory the app uses.

    *cancel_event* makes a waiting request abandonable (a run or a speed test the teacher
    stopped), *timeout* bounds a request that has to answer (``None``: the client's own).
    """
    if timeout is None:
        return OllamaClient(url, cancel_event=cancel_event)
    return OllamaClient(url, cancel_event=cancel_event, timeout=timeout)


class OllamaServerService:
    """Asks the Ollama server at the configured address what it has and what it is doing.

    It also remembers the answer: which models are installed, their digests and the server's
    version are one snapshot, taken again when it is older than ``SNAPSHOT_TTL_S``, when it
    listed no model, or when something changed the server (a pull).  What is in memory
    (``/api/ps``) is never kept.
    """

    def __init__(
        self,
        *,
        client_factory: OllamaClientFactory[OllamaClient] = ollama_client,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the service.

        Args:
            client_factory: Builds the client of one address.  One factory is shared with the
                speed test, so a test replaces it once and nothing else builds a client of its
                own.
            clock: What the age of a snapshot is measured with; a test replaces it.
        """
        self._client = client_factory
        self._clock = clock
        # One lock around the dicts, never held during a request: the GUI thread reads what is
        # held while a background thread asks the server.
        self._lock = threading.Lock()
        self._snapshots: dict[str, tuple[float, ServerSnapshot]] = {}
        # Counts the forgets per address: an answer asked for before a forget is not kept.
        self._generations: dict[str, int] = {}
        self._capabilities: dict[tuple[str, str], frozenset[str]] = {}

    # ── what the server answered ───────────────────────────────────────────

    def snapshot(
        self, url: str, *, max_age: float = SNAPSHOT_TTL_S, timeout: float | None = None
    ) -> ServerSnapshot:
        """Return what the server at *url* has, asking it when the held answer is too old.

        Blocking network call: run it on a background thread.  ``max_age=0`` always asks;
        *timeout* bounds the request (``None``: the client's own).  The latest answer is always
        held, but one that lists no model — nothing downloaded, or no connection — is asked
        again on the next look, so a model pulled in a terminal or a server started a moment
        later is noticed.
        """
        key = normalize_ollama_url(url)
        held = self._held(key, max_age)
        if held is not None and held.models:
            return held
        with self._lock:
            generation = self._generations.get(key, 0)
        snapshot = self._client(key, timeout=timeout).snapshot()
        with self._lock:
            if self._generations.get(key, 0) == generation:
                self._snapshots[key] = (self._clock(), snapshot)
        return snapshot

    def cached_snapshot(self, url: str, *, max_age: float | None = None) -> ServerSnapshot | None:
        """Return the latest answer held for *url* without asking the server, or ``None``.

        ``None`` means the app has not asked the server yet, or what it holds is older than
        *max_age* (``None``: any age).  The answer may be an unreachable one: that is how a
        reader tells "the server is gone" from "not asked yet".  Safe on the GUI thread.
        """
        return self._held(normalize_ollama_url(url), max_age)

    def forget(self, url: str) -> None:
        """Forget what the app knows about the server at *url*.

        For a pull (also one that failed or was stopped: it installed part of a model) and for
        a server that was restarted.  What a model can do is kept: a digest does not change
        meaning.
        """
        key = normalize_ollama_url(url)
        with self._lock:
            self._snapshots.pop(key, None)
            self._generations[key] = self._generations.get(key, 0) + 1

    def generation(self, url: str) -> int:
        """Return how often the answer of *url* was forgotten; read it before asking.

        Hand it back to :meth:`hold`, which keeps an answer only when nothing was forgotten in
        between.
        """
        key = normalize_ollama_url(url)
        with self._lock:
            return self._generations.get(key, 0)

    def hold(self, url: str, snapshot: ServerSnapshot, *, generation: int) -> None:
        """Keep *snapshot* as the latest answer of *url*, unless it was forgotten since.

        For a caller that asked the server itself (the speed test): what it measured and what
        the app knows then come from one answer.  *generation* is what :meth:`generation` said
        before the request went out, so a pull or a restart meanwhile wins over this answer.
        """
        key = normalize_ollama_url(url)
        with self._lock:
            if self._generations.get(key, 0) != generation:
                return
            self._snapshots[key] = (self._clock(), snapshot)

    def _held(self, key: str, max_age: float | None) -> ServerSnapshot | None:
        """Return the snapshot held for *key* if it is younger than *max_age* (``None``: any)."""
        with self._lock:
            entry = self._snapshots.get(key)
        if entry is None:
            return None
        return entry[1] if max_age is None or self._clock() - entry[0] < max_age else None

    def capabilities(
        self, url: str, tag: str, *, timeout: float, cached_only: bool = False
    ) -> frozenset[str] | None:
        """Return what *tag* can do on the server at *url*, or ``None`` when it cannot say.

        Held per digest of the tag: a tag is a name that can be re-pointed, so what an earlier
        pull of it could do says nothing about this one.  A tag the server reports no digest
        for is asked every time, and an unknown answer is never held.

        Raises:
            :exc:`~teachers_teammate.exceptions.OllamaUnreachableError`: *url* is unreachable,
                so a caller asking about several tags can stop asking.
        """
        key = normalize_ollama_url(url)
        if cached_only:
            snapshot = self.cached_snapshot(key)
        else:
            snapshot = self.snapshot(key, timeout=timeout)
            if snapshot.unreachable:
                raise OllamaUnreachableError(cannot_reach(key, detail(snapshot.reason)))
        digest = "" if snapshot is None else snapshot.digest_of(tag)
        if digest:
            with self._lock:
                known = self._capabilities.get((key, digest))
            if known is not None:
                return known
        if cached_only:
            return None
        capabilities = self._client(key, timeout=timeout).model_capabilities(tag)
        if capabilities is not None and digest:
            with self._lock:
                self._capabilities[(key, digest)] = capabilities
        return capabilities

    def discover_ollama_servers(
        self,
        scope: DiscoveryScope = DiscoveryScope.THIS_COMPUTER,
        *,
        stop_event: threading.Event | None = None,
        on_found: Callable[[DiscoveredServer], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[DiscoveredServer]:
        """Return the Ollama servers that answer, searching as far as *scope* says.

        ``THIS_COMPUTER`` asks this computer and, inside WSL, the Windows computer behind it:
        a few addresses, quick and quiet.  ``NETWORK`` also asks the private network around
        this computer (up to 254 addresses per network card): the caller must only ask for it
        when the person did.  Blocking network calls: run it on a background thread.  Changes
        no setting; *on_found* / *on_progress* are called from worker threads.
        """
        from ..infrastructure import ollama_discovery  # noqa: PLC0415

        found = ollama_discovery.discover_this_computer()
        if on_found is not None:
            for server in found:
                on_found(server)
        if scope is DiscoveryScope.NETWORK:
            known = {server.url for server in found}
            found += [
                server
                for server in ollama_discovery.scan_network(
                    stop_event=stop_event, on_found=on_found, on_progress=on_progress
                )
                if server.url not in known
            ]
        return found

    def ollama_status(self, url: str) -> OllamaStatus:
        """Return whether Ollama answers at *url*, and what the app can do about it.

        One probe; the program is looked for only when nothing answered.  Blocking network
        call: run it on a background thread.
        """
        from ..infrastructure import ollama_discovery  # noqa: PLC0415
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            can_start_ollama,
            find_ollama_program,
        )

        normalized = normalize_ollama_url(url)
        info = ollama_discovery.probe_server(normalized)
        program = None if info is not None else find_ollama_program()
        state = ollama_state(
            answered=info is not None,
            local_address=self.is_local(normalized, include_wsl_host=True),
            program_found=program is not None,
        )
        return OllamaStatus(
            state=state,
            version=info.version if info is not None else "",
            model_count=info.model_count if info is not None else None,
            can_start=state is OllamaState.NOT_RUNNING and can_start_ollama(),
        )

    def list_ollama_model_sizes(self, url: str) -> dict[str, int]:
        """Return ``{model_name: size_bytes}`` for models installed in Ollama."""
        return self.snapshot(url).sizes()

    def activity(self, url: str, model: str = "") -> ServerActivity:
        """Return what the server at *url* holds in memory, and whether *model* is there.

        Never raises: an unreachable server reports nothing loaded and nothing busy.
        """
        return self._client(url).activity(model)

    def unload(self, url: str, model: str) -> None:
        """Drop *model* out of the server's memory at *url*, so it costs no memory any more."""
        self._client(url).unload(model)

    def check_connection(self, url: str, model: str = "") -> ConnectionCheck:
        """Return ``(connected, model_ok, message)`` for the server at *url*."""
        return self._client(url).check_connection(model)

    def wait_for(self, url: str, model: str) -> WaitDetail:
        """Return why *model* has to wait for another one at *url*, or nothing.

        A note, not a refusal: another model is in memory and busy, so the server finishes it
        and loads *model* before the first answer.
        """
        activity = self.activity(url, model)
        if activity.has(model):
            return WaitDetail()
        other = activity.busy_other_than(model) or next(iter(activity.loaded_names()), "")
        if not other:
            return WaitDetail()
        return WaitDetail(WaitKind.QUEUED, model, other)

    def pull_ollama_model(
        self,
        url: str,
        model_name: str,
        progress_cb: Callable[[str, float, float], None] | None = None,
        abort_event: threading.Event | None = None,
    ) -> None:
        """Pull (download) *model_name* from Ollama at *url*.

        The server is forgotten afterwards, whatever came of the pull: a pull that failed or
        was stopped can still have installed part of it.
        """
        try:
            self._client(url).pull(model_name, progress_cb, abort_event)
        finally:
            self.forget(url)

    @staticmethod
    def program_found() -> bool:
        """Return whether the Ollama program is installed on this computer."""
        from ..infrastructure.ollama_install import find_ollama_program  # noqa: PLC0415

        return find_ollama_program() is not None

    @staticmethod
    def can_start() -> bool:
        """Return whether the app can start Ollama from here (Windows and macOS only)."""
        from ..infrastructure.ollama_install import can_start_ollama  # noqa: PLC0415

        return can_start_ollama()

    def start_ollama(self) -> None:
        """Start the Ollama program on this computer (background thread only).

        Does nothing when no program was found, or when the platform has no way to start it
        (on Linux the server is a system service).
        """
        from ..infrastructure.ollama_install import (  # noqa: PLC0415
            can_start_ollama,
            find_ollama_program,
            start_ollama,
        )

        program = find_ollama_program()
        if program is None or not can_start_ollama():
            return
        start_ollama(program)

    @staticmethod
    def warm_up_locality_checks() -> None:
        """Start, in the background, the lookup that tells whether an Ollama URL is this computer.

        Call it at start-up so classifying where a step runs never waits for the network
        on the GUI thread.
        """
        warm_up_own_host_names()

    @staticmethod
    def is_local(url: str, *, include_wsl_host: bool = False) -> bool:
        """Return whether *url* points at this computer (or the Windows host behind WSL)."""
        return is_local_ollama_url(
            url,
            own_addresses=own_host_names,
            windows_hosts=windows_host_addresses if include_wsl_host else tuple,
        )

    @staticmethod
    def normalize(url: str) -> str:
        """Return *url* in the form the app compares and stores."""
        return normalize_ollama_url(url)
