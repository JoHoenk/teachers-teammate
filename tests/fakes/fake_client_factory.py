"""A client factory for tests: it builds a real client and remembers what it was given.

What a run, a comparison or the speed test adds to a client — the stop event that abandons a
waiting request and the sink that turns what the server reported into a sample — is only visible
where the client is built.  This factory is that place, so a test can read it back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading

from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.ollama_request import ChatStats


@dataclass(frozen=True)
class ClientBuild:
    """What one build was asked for: the address, the event and the sink of the statistics."""

    url: str
    cancel_event: threading.Event | None
    timeout: float | None
    stats_sink: Callable[[str, ChatStats], None] | None


class RecordingClientFactory:
    """The app's factory, recording every build; the client it returns is the real one."""

    def __init__(self) -> None:
        self.builds: list[ClientBuild] = []

    def __call__(
        self,
        url: str,
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        stats_sink: Callable[[str, ChatStats], None] | None = None,
    ) -> OllamaClient:
        """Build the client of *url* with what the caller passes, and remember it."""
        self.builds.append(ClientBuild(url, cancel_event, timeout, stats_sink))
        return OllamaClient(url, cancel_event=cancel_event, timeout=timeout, stats_sink=stats_sink)

    def only_build(self) -> ClientBuild:
        """Return the one build that happened."""
        (build,) = self.builds
        return build
