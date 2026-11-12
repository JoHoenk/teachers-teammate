"""Talking to the memory agent on the machine with the graphics card.

The agent (:mod:`.gpu_agent`) is one standard-library file that runs where the card is and reports
what it reads over HTTP.  This is the tool's side of that conversation: the four requests, the
answers as frozen dataclasses, and the one wait the fit check needs — a baseline taken before the
card has given the memory back would make a model look smaller than it is.

Errors are :class:`AgentError`, an :class:`OSError`, so a caller that already treats an
unreachable server as gone catches this too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import time
from typing import Any
import urllib.error
import urllib.request

MIB = 1024 * 1024

#: Two readings of the card this close together count as idle.
SETTLE_TOLERANCE_BYTES = 64 * MIB

#: How long to wait for the card to settle before giving up on it.
SETTLE_MAX_S = 15.0

#: How often to look while waiting.
SETTLE_EVERY_S = 0.5

#: How many readings in a row have to agree before the card counts as idle.  Two are not
#: enough: the driver can give memory back more than half a second after the unload.
SETTLE_STABLE_READINGS = 4


class AgentError(OSError):
    """The memory agent could not be asked, or did not answer as the agent does."""


@dataclass(frozen=True)
class AgentCard:
    """One graphics card as the agent lists it."""

    index: int
    name: str
    total_bytes: int


@dataclass(frozen=True)
class AgentInfo:
    """What the agent is and what it last read."""

    agent: int
    agent_sha256: str
    platform: str
    vendor: str
    source: str
    interval_s: float
    cards: tuple[AgentCard, ...]
    card_total_bytes: int
    ram_total_bytes: int | None
    card_used_bytes: int | None
    ram_available_bytes: int | None


@dataclass(frozen=True)
class WindowStats:
    """What the card and the main memory did between a window's open and its close."""

    window: int
    card_start_bytes: int
    card_peak_bytes: int
    card_mean_bytes: int
    card_min_bytes: int
    card_end_bytes: int
    ram_start_available_bytes: int | None
    ram_min_available_bytes: int | None
    ram_end_available_bytes: int | None
    samples: int
    duration_s: float

    @property
    def card_increase_bytes(self) -> int:
        """Return how far the card's use rose above where the window opened."""
        return max(0, self.card_peak_bytes - self.card_start_bytes)

    @property
    def ram_drop_bytes(self) -> int | None:
        """Return how far the available main memory fell, or ``None`` when it was not read."""
        if self.ram_start_available_bytes is None or self.ram_min_available_bytes is None:
            return None
        return max(0, self.ram_start_available_bytes - self.ram_min_available_bytes)


class AgentClient:
    """The four requests of the agent's API, over urllib."""

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url.rstrip("/")
        self._token = token
        self._timeout_s = timeout_s
        self._sleep = sleep

    def info(self) -> AgentInfo:
        """Return what the agent is and what it last read."""
        answer = self._call("GET", "/info")
        if "agent" not in answer:
            raise AgentError(f"{self.url} does not answer as the memory agent does")
        now = answer.get("now") or {}
        return AgentInfo(
            agent=int(answer.get("agent", 0)),
            agent_sha256=str(answer.get("agent_sha256", "")),
            platform=str(answer.get("platform", "")),
            vendor=str(answer.get("vendor", "")),
            source=str(answer.get("source", "")),
            interval_s=float(answer.get("interval_s", 0.0)),
            cards=tuple(
                AgentCard(
                    index=int(card.get("index", 0)),
                    name=str(card.get("name", "")),
                    total_bytes=int(card.get("total_bytes", 0)),
                )
                for card in answer.get("cards", ())
            ),
            card_total_bytes=int(answer.get("card_total_bytes", 0)),
            ram_total_bytes=_optional_int(answer.get("ram_total_bytes")),
            card_used_bytes=_optional_int(now.get("card_used_bytes")),
            ram_available_bytes=_optional_int(now.get("ram_available_bytes")),
        )

    def open_window(self) -> int:
        """Open a window on the agent and return its number."""
        answer = self._call("POST", "/window")
        if "window" not in answer:
            raise AgentError(f"{self.url} did not open a window")
        return int(answer["window"])

    def close_window(self, window: int) -> WindowStats:
        """Close *window* and return what the card and the main memory did inside it."""
        answer = self._call("POST", f"/window/{window}/close")
        card = answer.get("card") or {}
        ram = answer.get("ram") or {}
        if "card" not in answer:
            raise AgentError(f"{self.url} did not close window {window}")
        return WindowStats(
            window=int(answer.get("window", window)),
            card_start_bytes=int(card.get("start_bytes", 0)),
            card_peak_bytes=int(card.get("peak_bytes", 0)),
            card_mean_bytes=int(card.get("mean_bytes", 0)),
            card_min_bytes=int(card.get("min_bytes", 0)),
            card_end_bytes=int(card.get("end_bytes", 0)),
            ram_start_available_bytes=_optional_int(ram.get("start_available_bytes")),
            ram_min_available_bytes=_optional_int(ram.get("min_available_bytes")),
            ram_end_available_bytes=_optional_int(ram.get("end_available_bytes")),
            samples=int(answer.get("samples", 0)),
            duration_s=float(answer.get("duration_s", 0.0)),
        )

    def settle(
        self,
        *,
        tolerance_bytes: int = SETTLE_TOLERANCE_BYTES,
        every_s: float = SETTLE_EVERY_S,
        max_s: float = SETTLE_MAX_S,
        stable_readings: int = SETTLE_STABLE_READINGS,
    ) -> int:
        """Wait until the card's use stops falling and return that idle figure.

        Card memory is given back a moment after Ollama reports a model unloaded, so a baseline
        read too early would make the next model look smaller than it is.  The card counts as
        settled when *stable_readings* readings in a row, one every *every_s* seconds, lie within
        *tolerance_bytes* of the first of them; the last one is returned.  After *max_s* the last
        reading is returned anyway, because a card that keeps moving is a fact the caller should
        see, not an error.
        """
        anchor = self.info().card_used_bytes
        if anchor is None:
            raise AgentError(f"{self.url} reports no card reading")
        stable = 1
        current = anchor
        waited = 0.0
        while waited < max_s:
            self._sleep(every_s)
            waited += every_s
            reading = self.info().card_used_bytes
            if reading is None:
                raise AgentError(f"{self.url} reports no card reading")
            current = reading
            if abs(current - anchor) <= tolerance_bytes:
                stable += 1
                if stable >= stable_readings:
                    return current
            else:
                anchor, stable = current, 1
        return current

    def _call(self, method: str, path: str) -> dict[str, Any]:
        """Send one request and return the JSON answer, or raise :class:`AgentError`."""
        headers = {"Content-Type": "application/json"}
        if self._token is not None:
            headers["X-Token"] = self._token
        request = urllib.request.Request(
            self.url + path, data=b"" if method == "POST" else None, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as answer:
                return json.loads(answer.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            raise AgentError(f"{self.url}{path}: {_reason(error.code)}") from error
        except urllib.error.URLError as error:
            raise AgentError(
                f"the memory agent at {self.url} is not reachable: {error.reason}"
            ) from error
        except (OSError, ValueError) as error:
            raise AgentError(f"{self.url}{path}: {error}") from error


def _reason(status: int) -> str:
    """Return what a status code means for this API."""
    if status == 401:
        return "wrong token"
    if status == 404:
        return "no such window"
    if status == 413:
        return "the request body is too large"
    if status == 503:
        return "the agent gets no samples"
    return f"the agent answered {status}"


def _optional_int(value: object) -> int | None:
    """Return *value* as an integer, or ``None`` when it is not one."""
    return int(value) if isinstance(value, (int, float)) else None
