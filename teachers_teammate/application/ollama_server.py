"""The Ollama server: discovery, status, model sizes and pulls.

One place asks the server anything, so the address a teacher typed is used everywhere and
never replaced by a default of this computer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading

from ..domain.ollama_discovery import (
    DiscoveredServer,
    DiscoveryScope,
    OllamaState,
    ollama_state,
)
from ..infrastructure.ollama_utils import ServerActivity, WaitDetail, warm_up_own_host_names


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


class OllamaServerService:
    """Asks the Ollama server at the configured address what it has and what it is doing."""

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
        from ..infrastructure.ollama_utils import (  # noqa: PLC0415
            is_local_ollama_url,
            normalize_ollama_url,
        )

        normalized = normalize_ollama_url(url)
        info = ollama_discovery.probe_server(normalized)
        program = None if info is not None else find_ollama_program()
        state = ollama_state(
            answered=info is not None,
            local_address=is_local_ollama_url(normalized, include_wsl_host=True),
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
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return dict(OllamaClient(url).list_models_with_size())

    def listed_models(self, url: str) -> tuple[list[str], str]:
        """Return ``(installed, unreachable)`` for the server at *url*, asking it now.

        *unreachable* is ``""`` when the server answered — also when it has no model
        downloaded yet — and the connection message otherwise.  Makes one request: a caller
        that asks repeatedly keeps the answer.
        """
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return OllamaClient(url).list_models_or_error()

    def activity(self, url: str, model: str = "") -> ServerActivity:
        """Return what the server at *url* holds in memory, and whether *model* is there.

        Never raises: an unreachable server reports nothing loaded and nothing busy.
        """
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        return OllamaClient(url).activity(model)

    def unload(self, url: str, model: str) -> None:
        """Drop *model* out of the server's memory at *url*, so it costs no memory any more."""
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        OllamaClient(url).unload(model)

    def wait_for(self, url: str, model: str) -> WaitDetail:
        """Return why *model* has to wait for another one at *url*, or nothing.

        A note, not a refusal: another model is in memory and busy, so the server finishes it
        and loads *model* before the first answer.
        """
        from ..infrastructure.ollama_utils import WaitKind  # noqa: PLC0415

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
        """Pull (download) *model_name* from Ollama at *url*."""
        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        OllamaClient(url).pull(model_name, progress_cb, abort_event)

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
        from ..infrastructure.ollama_utils import is_local_ollama_url  # noqa: PLC0415

        return is_local_ollama_url(url, include_wsl_host=include_wsl_host)

    @staticmethod
    def normalize(url: str) -> str:
        """Return *url* in the form the app compares and stores."""
        from ..infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

        return normalize_ollama_url(url)
