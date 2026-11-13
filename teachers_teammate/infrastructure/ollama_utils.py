"""Shared Ollama utilities: URL normalisation and the OllamaClient HTTP wrapper.

Centralises all Ollama HTTP access so pipeline stages, GUI helpers, and
connection-check threads use the same code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import functools
import ipaddress
import logging
import queue
import socket
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..domain.context_budget import (
    ContextBudget,
    answers_with_the_same_text,
)
from ..exceptions import OllamaConnectionError, RunStoppedError, stop_requested
from .reporting import Reporter, StdoutReporter
from .wsl import windows_host_addresses

if TYPE_CHECKING:
    import ollama

    from ..exceptions import OCRError

logger = logging.getLogger(__name__)

_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

# How long to wait for the TCP connection to be established.
_CONNECT_TIMEOUT = 10

#: Context window (tokens) requested for proofreading and content-review answers.
#:
#: Ollama otherwise applies its own default of 4k on graphics cards under 24 GiB, which a
#: one-page, error-dense essay in review mode overruns.  One fixed value for every text on
#: purpose: a changed ``num_ctx`` makes Ollama reload the model, so sizing it per text would
#: reload before every file.  8k fits a full page in the longest-tokenising language (German);
#: ``tools/verify_model_catalog.py`` shows when it does not.
#:
#: Two accepted costs.  A server started with a larger ``OLLAMA_CONTEXT_LENGTH`` is overridden
#: downwards for these requests.  And when one model both reads scans (OCR keeps Ollama's own
#: window) and proofreads, Ollama reloads it on every switch between the two stages, because the
#: window differs; that setup is rare, and with a separate OCR model on a small graphics card the
#: models swap anyway.
PROOFREADING_NUM_CTX = 8192

#: Model families that indicate a model can read images (used as a fallback on
#: older Ollama servers that do not report capabilities).
_KNOWN_VISION_FAMILIES = frozenset({"clip", "vision", "deepseekocr"})


def ensure_prompt_fits(
    system: str,
    prompt: str,
    num_ctx: int,
    *,
    answer_tokens: int | None = None,
) -> None:
    """Raise :exc:`~teachers_teammate.exceptions.ResponseTruncatedError` if a request is too long.

    Ollama silently drops the middle of a prompt that is longer than ``num_ctx`` and still reports
    ``done_reason="stop"``, so a prompt overflow cannot be seen in the answer.  (A *long answer*
    can: it ends with ``done_reason="length"``.)  The client library cannot send
    ``truncate=false`` either, so the size is checked here, by estimate, before the request goes
    out.  The rule itself lives in :class:`~teachers_teammate.domain.context_budget.ContextBudget`.

    Args:
        answer_tokens: Tokens reserved for the answer.  ``None`` — the answer repeats (part of)
            the text it was given, so the request may take at most half of the window.
    """
    from ..exceptions import ResponseTruncatedError  # noqa: PLC0415

    budget = ContextBudget(num_ctx)
    allowance = answers_with_the_same_text(num_ctx) if answer_tokens is None else answer_tokens
    if not budget.request_fits(len(system) + len(prompt), answer_tokens=allowance):
        raise ResponseTruncatedError()


def proofreading_options(temperature: float, num_ctx: int = PROOFREADING_NUM_CTX) -> dict:
    """Return the Ollama options of a proofreading or content-review request.

    The one place that decides what such a request sends, so the model check
    (``tools/verify_model_catalog.py``) exercises the same options as the app.
    """
    return {"temperature": temperature, "num_ctx": num_ctx}


def normalize_ollama_url(url: str) -> str:
    """Return a normalized Ollama base URL.

    Accepts bare host:port inputs (for example ``127.0.0.1:11434``) and
    prepends ``http://`` so HTTP clients can use it directly.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return _DEFAULT_OLLAMA_URL
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned.rstrip("/")


def ollama_host(url: str) -> str:
    """Return the host name of the Ollama server at *url* (the default server when empty)."""
    normalized = normalize_ollama_url(url)
    return urlparse(normalized).hostname or normalized


@functools.cache
def _own_host_names() -> frozenset[str]:
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
    except OSError:
        pass
    return frozenset(name.lower() for name in names if name)


def warm_up_own_host_names() -> None:
    """Resolve this computer's own names in a background thread (idempotent, non-blocking)."""
    threading.Thread(target=_own_host_names, name="own-host-names", daemon=True).start()


def is_local_ollama_url(url: str, *, include_wsl_host: bool = False) -> bool:
    """Return whether *url* points at this computer.

    True for ``localhost``, the whole loopback range (``127.0.0.0/8``, ``::1``),
    ``0.0.0.0`` and this computer's own host name or address (so the machine's LAN
    IP counts as local too); false for anything else.

    With *include_wsl_host*, the Windows computer behind a WSL session counts too: it is the
    same physical computer, so nothing sent to it leaves the teacher's computer.  Callers that
    read this computer's graphics card or memory leave it off: from inside WSL those figures
    are the Linux side's, not the Windows computer's that actually runs Ollama.
    """
    host = (urlparse(normalize_ollama_url(url)).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "0.0.0.0"):
        return True
    try:
        if ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        pass
    # Only now look up the computer's own names: the cheap checks above answer the
    # common (default) URL without ever waiting on the network.
    if host in _own_host_names():
        return True
    return include_wsl_host and host in windows_host_addresses()


def models_include(model_name: str, available: list[str]) -> bool:
    """Return whether *model_name* is present in *available*.

    Matches an exact name (``"llama3:8b"``) and also a bare base name
    (``"llama3"``) against any installed tag, since Ollama model identifiers
    carry an optional ``:tag`` suffix that callers may omit.
    """
    if not model_name:
        return False
    model_base = model_name.split(":", maxsplit=1)[0]
    return any(m == model_name or m.split(":", maxsplit=1)[0] == model_base for m in available)


def _mapped_stream[T](stream: Iterator[T], base_url: str) -> Iterator[T]:
    """Translate the errors a streaming Ollama request raises into this module's errors.

    A streaming call only *creates* a generator; the HTTP request is made when the generator is
    first read.  So a refused connection and an HTTP error both arrive from the stream, not from
    the call, and the ``ollama`` client leaves them untranslated (``httpx.ConnectError``,
    ``ollama.ResponseError``).  Mapping them here is what lets a caller tell an unreachable
    server from a model that failed.

    A connection that breaks *after* the first chunk is a plain
    :exc:`~teachers_teammate.exceptions.OCRError`: a server that dies in the middle of an answer
    can be the model's doing (a CUDA crash), and the evaluation tools ask the server afterwards
    and decide there.
    """
    import httpx  # noqa: PLC0415  # comes with ollama, a core dependency
    import ollama  # noqa: PLC0415

    from ..exceptions import OCRError, OllamaUnreachableError  # noqa: PLC0415

    started = False
    try:
        for item in stream:
            started = True
            yield item
    except ollama.ResponseError as exc:
        raise OCRError(
            f"HTTP error from Ollama: {exc}", status_code=getattr(exc, "status_code", None)
        ) from exc
    except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError) as exc:
        if started:
            raise OCRError(str(exc)) from exc
        raise OllamaUnreachableError(f"cannot connect to Ollama at {base_url} — {exc}") from exc
    finally:
        # The reader thread closes the outer generator to stop the server generating; without
        # this the inner HTTP response would stay open until garbage collection.
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001  # a stream that cannot be closed is already gone
                pass


def _cut_connection(client: ollama.Client) -> None:
    """Close *client*'s HTTP connections, which ends a request that is still waiting.

    Each request makes its own ``ollama.Client``, so nothing else is using this one.  Closing it
    from another thread makes the blocked read fail at once and the server see the dropped
    connection, which is what stops it working on an answer nobody waits for.
    """
    http = getattr(client, "_client", None)  # httpx.Client; ollama has no public close()
    close = getattr(http, "close", None)
    if close is not None:
        close()


def _rejects_think(exc: OCRError) -> bool:
    """Return whether *exc* is Ollama refusing the ``think`` flag (a 400 that names it)."""
    return exc.status_code == 400 and "think" in str(exc).lower()


#: How often a waiting request looks at its cancel event (seconds): a stop takes effect within
#: this long, and the polling costs nothing measurable.
_CANCEL_POLL_S = 0.2


def _next_item[T](
    item_queue: queue.Queue[T],
    timeout: float | None,
    cancel_event: threading.Event | None,
) -> T:
    """Return the next queue item, waiting at most *timeout* seconds (``None`` = for ever).

    Raises :exc:`queue.Empty` when the time is up and :exc:`RunStoppedError` as soon as
    *cancel_event* is set and nothing is waiting in the queue: an item that already arrived
    (the end of a finished answer included) is handed over first, never thrown away.
    """
    if cancel_event is None:
        return item_queue.get() if timeout is None else item_queue.get(timeout=timeout)
    end = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            return item_queue.get_nowait()
        except queue.Empty:
            pass
        if cancel_event.is_set():
            raise RunStoppedError
        step = _CANCEL_POLL_S
        if end is not None:
            step = min(step, max(end - time.monotonic(), 0.0))
        try:
            return item_queue.get(timeout=step)
        except queue.Empty:
            if end is not None and time.monotonic() >= end:
                raise


def _abandon(abandoned: threading.Event, on_abandon: Callable[[], None] | None) -> None:
    """Tell the reader thread to stop, and cut the connection when the caller can."""
    abandoned.set()
    if on_abandon is not None:
        try:
            on_abandon()
        except Exception:  # noqa: BLE001  # cutting the connection is best effort
            logger.debug("could not cut the abandoned Ollama connection", exc_info=True)


def _consume_iterator_with_first_token_timeout[T](
    it: Iterator[T],
    first_token_timeout: float,
    total_timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    on_abandon: Callable[[], None] | None = None,
) -> list[T]:
    """Consume *it* in a background thread, enforcing a timeout on the first item.

    Returns the full list of items, or raises :exc:`~teachers_teammate.exceptions.OCRError`
    when the first item does not arrive within *first_token_timeout* seconds.  Once the answer
    has begun it may take as long as it takes, unless *total_timeout* is given: then the whole
    answer has that many seconds, :exc:`~teachers_teammate.exceptions.OCRDeadlineError` is
    raised when it is still running, and the stream is closed so the server stops generating.

    Setting *cancel_event* ends the wait within :data:`_CANCEL_POLL_S` seconds with
    :exc:`~teachers_teammate.exceptions.RunStoppedError`; the stream is closed the same way.

    The reader thread can only close the stream between two chunks.  While the server is still
    loading the model or reading the prompt no chunk comes, so a caller that can cut the
    connection from outside passes *on_abandon*: it is called (never raising) whenever the
    request is given up on -- a timeout, the deadline or a stop.
    """
    from ..exceptions import (  # noqa: PLC0415  # avoid circular import at module level
        OCRDeadlineError,
        OCRError,
        OCRTimeoutError,
    )

    item_queue: queue.Queue[T | BaseException | None] = queue.Queue()
    abandoned = threading.Event()

    def _reader() -> None:
        try:
            for item in it:
                if abandoned.is_set():
                    break
                item_queue.put(item)
        except Exception as exc:  # noqa: BLE001  # forward any iterator/transport error to the consumer thread via the queue
            item_queue.put(exc)
        finally:
            # Closing a generator closes its HTTP response, which tells the server to stop.
            close = getattr(it, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001  # a stream that cannot be closed is already gone
                    pass
            item_queue.put(None)  # sentinel: stream finished

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    deadline = None if total_timeout is None else time.monotonic() + total_timeout

    try:
        first = _next_item(item_queue, first_token_timeout, cancel_event)
    except queue.Empty:
        _abandon(abandoned, on_abandon)
        raise OCRTimeoutError(
            f"No output within {first_token_timeout}s — the model may still be loading into "
            "memory. Try again in a moment, or check that Ollama is running."
        ) from None
    except RunStoppedError:
        _abandon(abandoned, on_abandon)
        raise

    results: list[T] = []
    item: T | BaseException | None = first
    while item is not None:
        if isinstance(item, BaseException):
            # An error the stream already mapped (see _mapped_stream) keeps its type: a caller
            # must be able to tell an unreachable server from a model that failed.
            if isinstance(item, OCRError):
                raise item
            raise OCRError(str(item)) from item
        results.append(item)
        try:
            item = _next_item(
                item_queue,
                None if deadline is None else max(deadline - time.monotonic(), 0.0),
                cancel_event,
            )
        except queue.Empty:
            _abandon(abandoned, on_abandon)
            raise OCRDeadlineError(
                f"the answer was still being written after {total_timeout:g}s; "
                "the request was abandoned"
            ) from None
        except RunStoppedError:
            _abandon(abandoned, on_abandon)
            raise

    t.join()
    return results


@dataclass(frozen=True)
class ChatStats:
    """What Ollama reported about the answer it just finished (defaults when it said nothing).

    Kept so a caller can tell a complete answer from one the context window cut off, and so
    the token counts of a real run are visible in the log and in the model check.
    """

    done_reason: str = ""
    """``"stop"`` for a complete answer, ``"length"`` when the context window was full."""
    prompt_eval_count: int = 0
    """Tokens of the request (system instructions, answer format and text)."""
    eval_count: int = 0
    """Tokens of the answer."""
    total_duration: int = 0
    """Nanoseconds the server spent on the whole request (loading the model included)."""
    load_duration: int = 0
    """Nanoseconds spent loading the model; near zero when it was already in memory."""
    prompt_eval_duration: int = 0
    """Nanoseconds spent reading the request."""
    eval_duration: int = 0
    """Nanoseconds spent writing the answer: ``eval_count`` tokens over this is the speed."""

    @property
    def truncated(self) -> bool:
        """Whether the model stopped because it ran out of context, not because it was done."""
        return self.done_reason == "length"


def _chunk_stats(chunk: object) -> ChatStats:
    """Return the statistics of one stream chunk (empty ones for a chunk that reports none)."""
    done_reason = getattr(chunk, "done_reason", "")

    def count(name: str) -> int:
        value = getattr(chunk, name, 0)
        return value if isinstance(value, int) else 0

    return ChatStats(
        done_reason=done_reason if isinstance(done_reason, str) else "",
        prompt_eval_count=count("prompt_eval_count"),
        eval_count=count("eval_count"),
        total_duration=count("total_duration"),
        load_duration=count("load_duration"),
        prompt_eval_duration=count("prompt_eval_duration"),
        eval_duration=count("eval_duration"),
    )


def _is_transport_error(exc: BaseException) -> bool:
    """Return whether *exc* means "server unreachable or timed out".

    Anything else raised by the client (for example ``ollama.ResponseError`` for
    an unknown model) means the server did answer.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return type(exc).__module__.split(".", 1)[0] == "httpx"


class OllamaClient:
    """All HTTP access to a single Ollama server.

    Holds the normalised base URL so callers never repeat URL normalisation.
    All methods delegate to the ``ollama`` library (lazy-imported via
    :meth:`_client`).
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = _CONNECT_TIMEOUT,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.base_url = normalize_ollama_url(url)
        self._timeout = timeout
        #: When set, a chat request that is waiting is abandoned (the server stops generating)
        #: and raises :exc:`~teachers_teammate.exceptions.RunStoppedError`.
        self._cancel_event = cancel_event

    def _client(self) -> ollama.Client:
        """Return an ``ollama.Client`` for :attr:`base_url`."""
        import ollama  # noqa: PLC0415

        return ollama.Client(host=self.base_url, timeout=self._timeout)

    # ── Model discovery ────────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return model names from Ollama, or ``[]`` on any error."""
        try:
            resp = self._client().list()
            return [m.model for m in resp.models if m.model]
        except Exception:  # noqa: BLE001  # Ollama client/transport may raise anything; degrade to an empty list
            return []

    def list_models_with_size(self) -> list[tuple[str, int]]:
        """Return ``[(name, size_bytes)]`` for installed models, or ``[]`` on any error."""
        try:
            resp = self._client().list()
            return [(m.model, int(m.size or 0)) for m in resp.models if m.model]
        except Exception:  # noqa: BLE001  # Ollama client/transport may raise anything; degrade to an empty list
            return []

    # ── Connectivity checks ────────────────────────────────────────────────

    def list_models_or_error(self) -> tuple[list[str], str]:
        """Return ``(models, error)`` from a single request to the server.

        *error* is ``""`` when the server answered (even with no model downloaded)
        and a user-facing message when it could not be reached.  Unlike
        :meth:`list_models` this tells "unreachable" apart from "no models" without
        a second round-trip, which matters when the server times out.
        """
        try:
            resp = self._client().list()
            return [m.model for m in resp.models if m.model], ""
        except Exception as exc:  # noqa: BLE001  # any client error means unreachable; the message is classified below
            exc_str = str(exc).lower()
            if any(w in exc_str for w in ("connect", "refused", "network", "unreachable")):
                return (
                    [],
                    f"✗ Cannot connect to Ollama at {self.base_url} — is it running? (ollama serve)",
                )
            if "timeout" in exc_str:
                return [], f"✗ Connection timed out at {self.base_url} — check URL and network"
            return [], f"✗ Unexpected error contacting Ollama: {exc}"

    def check_connection(self, model: str = "") -> tuple[bool, bool, str]:
        """Return ``(connected, model_ok, message)`` for the Ollama server.

        * ``connected=False`` — server is unreachable.
        * ``connected=True, model_ok=False`` — server is up, but *model* is
          not installed (only meaningful when *model* is provided).
        * ``connected=True, model_ok=True`` — server is up and *model* is
          available (or no model was requested).
        """
        models, error = self.list_models_or_error()
        if error:
            return False, False, error

        if not models:
            return (
                True,
                False,
                "✗ Connected to Ollama but no models are pulled — run: ollama pull <model>",
            )
        if not model:
            return True, True, f"✓ Connected — {len(models)} model(s) available"
        if models_include(model, models):
            return True, True, f"✓ Connected — model '{model}' is available"
        avail_str = ", ".join(models)
        return True, False, f"✗ Model '{model}' not found. Available: {avail_str}"

    def model_capabilities(self, tag: str) -> frozenset[str] | None:
        """Return the capabilities of *tag* (e.g. ``{"completion", "vision"}``).

        Uses ``show().capabilities`` on newer Ollama servers.  On older servers
        it falls back to the model-families heuristic: a family in
        ``{"clip", "vision", "deepseekocr"}`` means the model can read images,
        any other family means text-only completion.

        Returns:
            Frozenset of capability names, or ``None`` when the model is unknown
            to the server or the server reports neither capabilities nor families.

        Raises:
            :exc:`~teachers_teammate.exceptions.OllamaConnectionError`: The server
                is unreachable or timed out, so callers can stop asking it.
        """
        try:
            resp = self._client().show(tag)
        except Exception as exc:  # the Ollama client may raise anything; classified below
            if _is_transport_error(exc):
                raise OllamaConnectionError(
                    f"Cannot reach Ollama at {self.base_url}: {exc}"
                ) from exc
            return None  # the server answered but cannot describe the model

        try:
            caps = getattr(resp, "capabilities", None)
            if isinstance(caps, (list, tuple, frozenset)) and caps:
                return frozenset(str(c).lower() for c in caps)

            details = getattr(resp, "details", None)
            families = list(details.families or []) if details else []
        except Exception:  # noqa: BLE001  # an unexpected response shape is "no information"
            return None
        if any(f.lower() in _KNOWN_VISION_FAMILIES for f in families):
            return frozenset({"vision"})
        if families:
            return frozenset({"completion"})
        return None

    def check_model(self, model_name: str, reporter: Reporter | None = None) -> None:
        """Verify the server is reachable and *model_name* is available.

        Also warns when the model does not appear vision-capable, and notes
        when the model is not yet loaded (first request will be slow).  Advisory
        lines go through *reporter* (defaulting to :class:`StdoutReporter`, i.e.
        stderr) so adapters can route them to a log pane.

        Raises:
            :exc:`~teachers_teammate.exceptions.OllamaConnectionError`: Server
                is unreachable, or the model is not available.
        """
        import ollama  # noqa: PLC0415

        reporter = reporter or StdoutReporter()
        client = ollama.Client(host=self.base_url)

        try:
            resp = client.list()
            available = [m.model for m in resp.models if m.model]
        except Exception as exc:
            exc_str = str(exc).lower()
            if any(w in exc_str for w in ("connect", "refused", "network", "unreachable")):
                raise OllamaConnectionError(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Is it running? Start it with: ollama serve"
                ) from exc
            if "timeout" in exc_str:
                raise OllamaConnectionError(
                    f"Connection to Ollama at {self.base_url} timed out. "
                    "Check the URL and network settings."
                ) from exc
            raise OllamaConnectionError(
                f"Unexpected error contacting Ollama at {self.base_url}: {exc}"
            ) from exc

        if not available:
            raise OllamaConnectionError(
                f"Connected to Ollama at {self.base_url} but no models are installed. "
                "Pull a model first: ollama pull <model-name>"
            )

        if not models_include(model_name, available):
            avail_str = ", ".join(available)
            raise OllamaConnectionError(
                f"Model '{model_name}' not found in Ollama. Available: {avail_str}"
            )

        try:
            capabilities = self.model_capabilities(model_name)
        except OllamaConnectionError:
            capabilities = None  # introspection is best-effort; skip the advisory
        if capabilities is not None and "vision" not in capabilities:
            reporter.warn(
                f"\nWARNING: Model '{model_name}' may not support vision — "
                "it cannot read scans. OCR results may be poor."
            )

        try:
            ps_resp = client.ps()
            running = [m.model for m in ps_resp.models if m.model]
            if running and model_name not in running:
                reporter.warn(
                    f"\nNOTE: '{model_name}' is not currently loaded in Ollama — "
                    "the first OCR request will be slow while the model loads."
                )
        except Exception:  # noqa: BLE001  # older Ollama versions may not have /api/ps; the note is optional
            pass

    # ── Generation ─────────────────────────────────────────────────────────

    def chat(  # noqa: PLR0913  # one keyword shape, shared with chat_with_stats()
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin  # the ollama API's name
        system: str = "",
        allow_truncated: bool = False,
        answer_tokens: int | None = None,
    ) -> str:
        """Send a chat request and return the response text, refusing a cut-off one.

        See :meth:`chat_with_stats` for the arguments.  An answer Ollama stopped at a limit
        (``done_reason="length"``: the context window was full, or ``num_predict`` was reached)
        would be a beginning only, so this raises instead of returning it — the text it was asked
        about goes on past the end of such an answer.  A request that sets ``num_ctx`` and is too
        long for it is refused up front (see :func:`ensure_prompt_fits`).

        Args:
            allow_truncated: Return a cut-off answer instead of raising (logging a warning).
                For callers whose output limit is deliberate, like the OCR page limit, where a
                partial page is still the best available result.
            answer_tokens: How many tokens the answer may take.  ``None`` — as many as the
                request, which is the proofreading shape (the answer repeats the text).

        Raises:
            :exc:`~teachers_teammate.exceptions.ResponseTruncatedError`: the request did not fit
                the context window, or the answer was cut off.
            :exc:`~teachers_teammate.exceptions.OCRError`: on any other failure.
        """
        from ..exceptions import ResponseTruncatedError  # noqa: PLC0415

        num_ctx = (options or {}).get("num_ctx")
        if num_ctx and not images:
            ensure_prompt_fits(system, prompt, int(num_ctx), answer_tokens=answer_tokens)
        content, stats = self.chat_with_stats(
            model,
            prompt,
            images=images,
            options=options,
            think=think,
            timeout=timeout,
            format=format,
            system=system,
        )
        if stats.truncated:
            if not allow_truncated:
                raise ResponseTruncatedError()
            logger.warning("ollama chat %s: answer cut off at its output limit", model)
            if not content.strip():
                # A cut-off answer without any text is no partial page: it is nothing, and
                # returning "" would silently drop the page from the document.
                from ..exceptions import OCRError  # noqa: PLC0415

                raise OCRError("model returned an empty response (stopped at its output limit)")
        return content

    def chat_with_stats(  # noqa: PLR0913  # one keyword shape, shared with chat()
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin  # the ollama API's name
        system: str = "",
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Send a chat request and return ``(text, stats)``.

        Unlike :meth:`chat` this returns a cut-off answer too, together with the statistics that
        mark it as cut off, so a caller can report what happened instead of guessing.

        Args:
            model:   Ollama model tag.
            prompt:  User message text.
            images:  Optional list of base64-encoded images (for vision models).
            options: Optional Ollama options dict (e.g. ``{"temperature": 0}``).
            think:   Whether to let reasoning models emit ``<think>`` blocks.
                     Defaults to ``False`` to keep reasoning out of OCR output.
                     Forwarded to Ollama; ignored (with a no-``think`` retry) on
                     older clients or models that don't support it.
            timeout: Seconds to wait for the first token of the answer.
            system:  Optional system message sent before the user message.
            total_timeout: Seconds the whole answer may take; ``None`` (the app's choice) sets no
                     limit once the answer has begun.  Evaluation tools set it so a model that
                     loops cannot hold a run for a quarter of an hour.
            format:  Optional response format.  A JSON schema (dict) makes Ollama
                     constrain the decoding to that schema, which is how proofreading
                     gets a machine-readable answer; ``None`` leaves it free-form.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on any failure.
        """
        import ollama  # noqa: PLC0415

        from ..exceptions import OCRError, OllamaUnreachableError  # noqa: PLC0415

        if stop_requested(self._cancel_event):
            raise RunStoppedError
        message: dict = {"role": "user", "content": prompt}
        if images:
            message["images"] = images
        messages = [{"role": "system", "content": system}, message] if system else [message]

        structured: dict = {} if format is None else {"format": format}

        def open_stream(think_flag: bool | None) -> Iterator:
            kwargs: dict = {
                "model": model,
                "messages": messages,
                "stream": True,
                "options": options,
                **structured,
            }
            if think_flag is not None:
                kwargs["think"] = think_flag
            return client.chat(**kwargs)

        try:
            client = ollama.Client(host=self.base_url, timeout=timeout)
            try:
                stream = open_stream(think)
            except TypeError:
                # Older ollama clients lack the ``think`` kwarg. The output cleaner strips any
                # leaked reasoning regardless.
                stream = open_stream(None)
        except Exception as exc:
            raise OllamaUnreachableError(
                f"cannot connect to Ollama at {self.base_url} — {exc}"
            ) from exc

        try:
            chunks = _consume_iterator_with_first_token_timeout(
                _mapped_stream(stream, self.base_url),
                timeout,
                total_timeout,
                self._cancel_event,
                on_abandon=lambda: _cut_connection(client),
            )
        except OCRError as exc:
            # A model that does not support ``think`` rejects it with a 400 that names it.  Any
            # other error (a model that is not pulled, an outage) is not retried: retrying would
            # only double the wait.
            if think is None or not _rejects_think(exc):
                raise
            chunks = _consume_iterator_with_first_token_timeout(
                _mapped_stream(open_stream(None), self.base_url),
                timeout,
                total_timeout,
                self._cancel_event,
                on_abandon=lambda: _cut_connection(client),
            )
        parts = [(chunk.message.content or "") for chunk in chunks if chunk.message]
        content = "".join(parts)
        # The last chunk carries the summary of the whole answer; earlier ones leave these unset.
        stats = _chunk_stats(chunks[-1]) if chunks else ChatStats()
        # A reasoning model can spend the whole window on thinking it does not return: no text,
        # but a stop at the limit.  That is a cut-off answer, not an empty one -- say so.
        if not content.strip() and not stats.truncated:
            raise OCRError("model returned an empty response")
        logger.debug(
            "ollama chat %s: done_reason=%r prompt_eval_count=%d eval_count=%d",
            model,
            stats.done_reason,
            stats.prompt_eval_count,
            stats.eval_count,
        )
        return content, stats

    # ── Model management ───────────────────────────────────────────────────

    def pull(
        self,
        model_name: str,
        progress_cb: Callable[[str, float, float], None] | None = None,
        abort_event: threading.Event | None = None,
    ) -> None:
        """Pull (download) *model_name* from Ollama.

        Args:
            model_name:  Model tag to pull (e.g. ``"llama3.2:latest"``).
            progress_cb: Optional callback ``progress_cb(status, completed, total)``.
            abort_event: When set, stops consuming the stream after the current chunk.
        """
        import ollama  # noqa: PLC0415

        from ..exceptions import OCRError  # noqa: PLC0415

        client = ollama.Client(host=self.base_url)
        try:
            for resp in client.pull(model_name, stream=True):
                if abort_event and abort_event.is_set():
                    return
                if progress_cb:
                    progress_cb(
                        resp.status or "", float(resp.completed or 0), float(resp.total or 0)
                    )
        except Exception as exc:
            raise OCRError(f"Download failed: {exc}") from exc
