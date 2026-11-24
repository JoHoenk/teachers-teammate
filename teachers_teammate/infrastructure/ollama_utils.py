"""The OllamaClient HTTP wrapper, and the names this computer answers to.

Centralises all Ollama HTTP access so pipeline stages, GUI helpers, and connection-check threads
use the same code path.  What an address *is* — its normal form, its host, whether it points at
this computer — is :mod:`~teachers_teammate.domain.ollama_address`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
import functools
import logging
import queue
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

from ..domain.context_budget import (
    ContextBudget,
    answers_with_the_same_text,
)
from ..domain.model_catalog import LoadedModel, ServerFacts, normalize_tag
from ..domain.ollama_address import (
    is_local_ollama_url,
    models_include,
    normalize_ollama_url,
    ollama_host,
)
from ..domain.ollama_discovery import WaitDetail, WaitKind, is_private_host
from ..exceptions import OllamaUnreachableError, RunStoppedError, stop_requested
from ..interfaces import ConnectionCheck, Reporter
from ..messages import (
    answer_abandoned,
    cannot_connect,
    cannot_reach,
    connection_timed_out,
    contact_failed,
    empty_answer,
    empty_answer_cut_off,
    http_error,
    message_of,
    model_not_found,
    model_pull_failed,
    no_first_token,
    no_models_installed,
    unreachable,
)
from ..texts.servers import (
    CHECK_NO_MODELS,
    check_cannot_connect,
    check_connected,
    check_model_available,
    check_model_missing,
    check_timed_out,
    check_unexpected,
    model_not_loaded,
    vision_not_supported,
)
from .reporting import StdoutReporter
from .wsl import windows_host_addresses

if TYPE_CHECKING:
    import ollama

    from ..exceptions import OCRError

logger = logging.getLogger(__name__)

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


def proofreading_options(
    temperature: float,
    num_ctx: int = PROOFREADING_NUM_CTX,
    sampling: Mapping[str, float] | None = None,
) -> dict:
    """Return the Ollama options of a proofreading or content-review request.

    The one place that decides what such a request sends, so the model check
    (``tools/verify_model_catalog.py``) exercises the same options as the app.  *sampling* is the
    step's extra sampling values (:attr:`~teachers_teammate.domain.model_catalog.StepProfile.
    options`); without it the request carries nothing but the temperature and the window, and
    the model's own tag decides the rest.
    """
    return {**(sampling or {}), "temperature": temperature, "num_ctx": num_ctx}


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
class OllamaServer:
    """What the privacy rules need to know about the server at an Ollama URL."""

    host: str
    #: The server runs on this computer (or the Windows computer behind a WSL session).
    is_this_computer: bool
    #: The host is on a local network, so it is a computer the teacher controls.
    is_private: bool


def ollama_server(url: str) -> OllamaServer:
    """Return where the Ollama server at *url* is: the one place that reads the address.

    Every classification of a step on Ollama (the badge, the refusal of online text
    recognition, the number of tries) starts here, so they cannot disagree about a URL.
    """
    host = ollama_host(url)
    return OllamaServer(
        host=host,
        is_this_computer=is_local_ollama_url(
            url, own_addresses=own_host_names, windows_hosts=windows_host_addresses
        ),
        is_private=is_private_host(host),
    )


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

    from ..exceptions import OCRError  # noqa: PLC0415

    started = False
    try:
        for item in stream:
            started = True
            yield item
    except ollama.ResponseError as exc:
        raise OCRError(
            http_error(message_of(exc)), status_code=getattr(exc, "status_code", None)
        ) from exc
    except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError) as exc:
        if started:
            raise OCRError(message_of(exc)) from exc
        raise OllamaUnreachableError(unreachable(base_url, message_of(exc))) from exc
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
    close = getattr(client, "close", None)
    if close is None:
        # A version without the public close still has its httpx client under ``_client``.
        close = getattr(getattr(client, "_client", None), "close", None)
    if close is not None:
        close()


def _rejects_think(exc: OCRError) -> bool:
    """Return whether *exc* is Ollama refusing the ``think`` flag (a 400 that names it)."""
    return exc.status_code == 400 and "think" in str(exc).lower()


#: How often a waiting request looks at its cancel event (seconds): a stop takes effect within
#: this long, and the polling costs nothing measurable.
_CANCEL_POLL_S = 0.2

#: How often a request that has no answer yet asks the server what it is doing (seconds).
_WAITING_POLL_S = 2.0

#: How long a request may wait before the run is told why (seconds).  A model that is already
#: loaded answers well within this, so it never shows a note.
WAIT_DETAIL_AFTER_S = 3.0


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
            pass  # an empty queue is the ordinary case: poll again
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
        except Exception:  # cutting the connection is best effort
            logger.debug("could not cut the abandoned Ollama connection", exc_info=True)


def _first_item[T](
    item_queue: queue.Queue[T | BaseException | None],
    first_token_timeout: float,
    cancel_event: threading.Event | None,
    waiting: Callable[[float], bool] | None,
) -> T | BaseException | None:
    """Return the first item, pausing the timeout while *waiting* reports a wait.

    Waiting behind another model of the server is not a failure: while the server is busy the
    clock starts again, so the timeout only counts the time the request could have been
    answered.  Without *waiting* the timeout runs as it is.
    """
    if waiting is None:
        return _next_item(item_queue, first_token_timeout, cancel_event)
    started = time.monotonic()
    deadline = started + first_token_timeout
    while True:
        step = max(min(_WAITING_POLL_S, deadline - time.monotonic()), _CANCEL_POLL_S)
        try:
            return _next_item(item_queue, step, cancel_event)
        except queue.Empty:
            if waiting(time.monotonic() - started):
                deadline = time.monotonic() + first_token_timeout
                continue
            if time.monotonic() >= deadline:
                raise


def _chat_messages(prompt: str, images: list[str] | None, system: str) -> list[dict]:
    """Return the messages of a chat: the system message when there is one, then the user's."""
    message: dict = {"role": "user", "content": prompt}
    if images:
        message["images"] = images
    return [{"role": "system", "content": system}, message] if system else [message]


def _chat_request(
    model: str,
    prompt: str,
    *,
    images: list[str] | None,
    options: dict | None,
    response_format: dict | str | None,
    system: str,
) -> dict:
    """Return the body of one chat request, in the shape the Ollama client takes."""
    request: dict = {
        "model": model,
        "messages": _chat_messages(prompt, images, system),
        "options": options,
    }
    if response_format is not None:
        request["format"] = response_format
    return request


def _open_chat_stream(client: Any, request: dict, think: bool | str | None) -> Iterator:
    """Open a streaming chat for *request*; ``think=None`` leaves the argument out."""
    kwargs: dict = {**request, "stream": True}
    if think is not None:
        kwargs["think"] = think
    return client.chat(**kwargs)


def _pump[T](
    it: Iterator[T], item_queue: queue.Queue[T | BaseException | None], abandoned: threading.Event
) -> None:
    """Move the items of *it* into *item_queue*, then ``None``; an error goes in as itself.

    Runs in the reader thread of :func:`_consume_iterator_with_first_token_timeout`.
    """
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


def _consume_iterator_with_first_token_timeout[T](
    it: Iterator[T],
    first_token_timeout: float,
    total_timeout: float | None = None,
    cancel_event: threading.Event | None = None,
    on_abandon: Callable[[], None] | None = None,
    waiting: Callable[[float], bool] | None = None,
    on_first_token: Callable[[], None] | None = None,
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

    *waiting* is asked every :data:`_WAITING_POLL_S` seconds while the first item has not
    arrived, with the seconds elapsed since the request was sent; returning ``True`` means the
    server is busy with something else, which pauses the first-token timeout.  *on_first_token*
    is called once, when the first item arrives.
    """
    from ..exceptions import (  # noqa: PLC0415  # avoid circular import at module level
        OCRDeadlineError,
        OCRError,
        OCRTimeoutError,
    )

    item_queue: queue.Queue[T | BaseException | None] = queue.Queue()
    abandoned = threading.Event()

    t = threading.Thread(target=_pump, args=(it, item_queue, abandoned), daemon=True)
    t.start()
    deadline = None if total_timeout is None else time.monotonic() + total_timeout

    try:
        first = _first_item(item_queue, first_token_timeout, cancel_event, waiting)
    except queue.Empty:
        _abandon(abandoned, on_abandon)
        raise OCRTimeoutError(no_first_token(first_token_timeout)) from None
    except RunStoppedError:
        _abandon(abandoned, on_abandon)
        raise

    if on_first_token is not None:
        on_first_token()
    results: list[T] = []
    item: T | BaseException | None = first
    while item is not None:
        if isinstance(item, BaseException):
            # An error the stream already mapped (see _mapped_stream) keeps its type: a caller
            # must be able to tell an unreachable server from a model that failed.
            if isinstance(item, OCRError):
                raise item
            raise OCRError(message_of(item)) from item
        results.append(item)
        try:
            item = _next_item(
                item_queue,
                None if deadline is None else max(deadline - time.monotonic(), 0.0),
                cancel_event,
            )
        except queue.Empty:
            _abandon(abandoned, on_abandon)
            if total_timeout is None:
                # Only a deadline makes the queue time out; without one an empty queue stays.
                raise
            raise OCRDeadlineError(answer_abandoned(total_timeout)) from None
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
    waited: bool = False
    """The request waited behind another model of the server, so ``load_duration`` counts that
    wait as well and is no measurement of loading."""

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


def _http_json(url: str, timeout: float) -> dict:
    """Return the JSON answer of *url*, read with the standard library.

    The ``ollama`` client has no method for ``/api/version``, and a version is not worth a
    second HTTP library.  A module-level function so a test can replace it.
    """
    import json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    with urllib.request.urlopen(url, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8") or "{}")


@dataclass(frozen=True)
class ServerActivity:
    """What the server is doing right now, as far as ``/api/ps`` can tell.

    "Busy" is inferred: a model whose ``expires_at`` moved since the previous look is
    generating.  That is undocumented behaviour, so it only chooses the wording of a note and
    never what the app does.
    """

    loaded: tuple[LoadedModel, ...] = ()
    """The models the server holds in memory."""
    busy: tuple[str, ...] = ()
    """The models that were generating when the server was asked."""
    reachable: bool = True
    """The server answered.  ``False`` means nothing is known, not that nothing is loaded."""

    def has(self, model: str) -> bool:
        """Return whether *model* is in memory."""
        wanted = normalize_tag(model)
        return any(normalize_tag(item.name) == wanted for item in self.loaded)

    def loaded_names(self) -> tuple[str, ...]:
        """Return the names of the models in memory."""
        return tuple(item.name for item in self.loaded)

    def busy_other_than(self, model: str) -> str:
        """Return a model that is generating and is not *model*, or ``""``."""
        wanted = normalize_tag(model)
        for name in self.busy:
            if normalize_tag(name) != wanted:
                return name
        return ""

    def wait_detail(self, model: str) -> WaitDetail:
        """Return what to tell the teacher about a request for *model* that has no answer yet.

        Nothing when the model is in memory: it is reading the text, so the first token is
        close.  Otherwise another model is still generating, or *model* is being loaded.
        """
        if not self.reachable or self.has(model):
            return WaitDetail()
        other = self.busy_other_than(model)
        if other:
            return WaitDetail(WaitKind.BUSY, model, other)
        return WaitDetail(WaitKind.LOADING, model)


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
        stats_sink: Callable[[str, ChatStats], None] | None = None,
    ) -> None:
        self.base_url = normalize_ollama_url(url)
        self._timeout = timeout
        #: When set, a chat request that is waiting is abandoned (the server stops generating)
        #: and raises :exc:`~teachers_teammate.exceptions.RunStoppedError`.
        self._cancel_event = cancel_event
        #: Called as ``stats_sink(model, stats)`` after every chat request, with what the server
        #: reported about it.  A run shares one client between all its stages, so one sink sees
        #: every request of the run and can turn them into a measurement of the machine.
        self._stats_sink = stats_sink
        self._last_stats: ChatStats | None = None
        #: Called with a :class:`WaitDetail` while a request waits for its first token, so the
        #: run can say what the server is doing.  ``None`` outside a run.
        self.detail_sink: Callable[[WaitDetail], None] | None = None
        #: Called when a request's first token arrives: the run's clock starts there, not when
        #: the request was sent, so a wait behind another model does not move the bar.
        self.first_token_sink: Callable[[], None] | None = None
        #: ``{model: expires_at}`` of the previous look at ``/api/ps``, which is what tells a
        #: generating model from an idle one.
        self._last_expiry: dict[str, str] = {}

    @property
    def last_stats(self) -> ChatStats | None:
        """Return what the server reported about the most recent chat request.

        ``None`` before the first request and after one that never reached the server, so a
        caller that reads it right after its own request cannot pick up an older answer's
        numbers.
        """
        return self._last_stats

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

    # ── Server facts ───────────────────────────────────────────────────────

    def server_version(self) -> str:
        """Return the version of the Ollama server, or ``""`` when it does not say."""
        try:
            return str(_http_json(f"{self.base_url}/api/version", self._timeout).get("version", ""))
        except Exception:  # noqa: BLE001  # an old server or a hiccup: the version is optional
            return ""

    def model_digests(self) -> dict[str, str]:
        """Return ``{tag: digest}`` of the installed models, or ``{}`` on any error.

        The digest pins the weights: a tag is a name that can be re-pointed, so a measurement
        of ``gemma4:e4b`` says nothing about a later pull of the same tag.
        """
        try:
            resp = self._client().list()
            return {m.model: str(m.digest or "") for m in resp.models if m.model}
        except Exception:  # noqa: BLE001  # Ollama client/transport may raise anything
            return {}

    def _ps_models(self) -> tuple[LoadedModel, ...] | None:
        """Return the models in memory, or ``None`` when the server could not be asked."""
        try:
            resp = self._client().ps()
            return tuple(
                LoadedModel(
                    name=str(m.model or m.name or ""),
                    size_bytes=int(m.size or 0),
                    size_vram_bytes=int(m.size_vram or 0),
                    context_length=int(m.context_length or 0),
                    expires_at=str(getattr(m, "expires_at", "") or ""),
                )
                for m in resp.models
                if (m.model or m.name)
            )
        except Exception:  # noqa: BLE001  # older Ollama versions may not have /api/ps
            return None

    def loaded_models(self) -> tuple[LoadedModel, ...]:
        """Return the models the server holds in memory right now, or ``()`` on any error."""
        return self._ps_models() or ()

    def activity(self, model: str = "") -> ServerActivity:  # noqa: ARG002  # callers ask `has(model)`
        """Return what the server is doing, and whether *model* is in memory.

        A model whose ``expires_at`` moved since the previous call is generating.  That is
        undocumented behaviour, so it only chooses the wording of a note.  Never raises: an
        unreachable server reports nothing loaded and nothing busy.
        """
        loaded = self._ps_models()
        if loaded is None:
            return ServerActivity(reachable=False)
        busy: list[str] = []
        seen: dict[str, str] = {}
        for item in loaded:
            seen[item.name] = item.expires_at
            before = self._last_expiry.get(item.name)
            if before and item.expires_at and before != item.expires_at:
                busy.append(item.name)
        self._last_expiry = seen
        return ServerActivity(loaded=loaded, busy=tuple(busy))

    def unload(self, model: str) -> None:
        """Ask the server to drop *model* from memory (``keep_alive=0``).

        Never raises: a model that stays loaded only costs memory, and the caller is on its way
        out anyway.
        """
        try:
            self._client().generate(model=model, prompt="", keep_alive=0)
        except Exception:  # an old server or a hiccup: nothing to do about it
            logger.debug("could not unload %s from Ollama", model, exc_info=True)

    def server_facts(self) -> ServerFacts:
        """Return version, model digests and loaded models in one go.

        Never raises: a server that answers only part of it gives what it gave.  The caller
        decides what a missing version or digest means (a measurement without them cannot be
        told apart from one of another build).
        """
        return ServerFacts(
            version=self.server_version(),
            digests=self.model_digests(),
            loaded=self.loaded_models(),
        )

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
                return [], check_cannot_connect(self.base_url)
            if "timeout" in exc_str:
                return [], check_timed_out(self.base_url)
            return [], check_unexpected(exc)

    def check_connection(self, model: str = "") -> ConnectionCheck:
        """Return ``(connected, model_ok, message)`` for the Ollama server.

        * ``connected=False`` — server is unreachable.
        * ``connected=True, model_ok=False`` — server is up, but *model* is
          not installed (only meaningful when *model* is provided).
        * ``connected=True, model_ok=True`` — server is up and *model* is
          available (or no model was requested).
        """
        models, error = self.list_models_or_error()
        if error:
            return ConnectionCheck(False, False, error)

        if not models:
            return ConnectionCheck(True, False, CHECK_NO_MODELS)
        if not model:
            return ConnectionCheck(True, True, check_connected(len(models)))
        if models_include(model, models):
            return ConnectionCheck(True, True, check_model_available(model))
        return ConnectionCheck(True, False, check_model_missing(model, ", ".join(models)))

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
            :exc:`~teachers_teammate.exceptions.OllamaUnreachableError`: The server
                is unreachable or timed out, so callers can stop asking it.
        """
        try:
            resp = self._client().show(tag)
        except Exception as exc:  # the Ollama client may raise anything; classified below
            if _is_transport_error(exc):
                raise OllamaUnreachableError(cannot_reach(self.base_url, message_of(exc))) from exc
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
            :exc:`~teachers_teammate.exceptions.OllamaUnreachableError`: Server
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
                raise OllamaUnreachableError(cannot_connect(self.base_url)) from exc
            if "timeout" in exc_str:
                raise OllamaUnreachableError(connection_timed_out(self.base_url)) from exc
            raise OllamaUnreachableError(contact_failed(self.base_url, message_of(exc))) from exc

        if not available:
            raise OllamaUnreachableError(no_models_installed(self.base_url))

        if not models_include(model_name, available):
            avail_str = ", ".join(available)
            raise OllamaUnreachableError(model_not_found(model_name, avail_str))

        try:
            capabilities = self.model_capabilities(model_name)
        except OllamaUnreachableError:
            capabilities = None  # introspection is best-effort; skip the advisory
        if capabilities is not None and "vision" not in capabilities:
            reporter.warn(vision_not_supported(model_name))

        try:
            ps_resp = client.ps()
            running = [m.model for m in ps_resp.models if m.model]
            if running and model_name not in running:
                reporter.warn(model_not_loaded(model_name))
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
        think: bool | str | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,
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

                raise OCRError(empty_answer_cut_off())
        return content

    def chat_with_stats(  # noqa: PLR0913  # one keyword shape, shared with chat()
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | str | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,
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
                     Defaults to ``False`` to keep reasoning out of OCR output.  A family that
                     takes a reasoning level instead (``gpt-oss``) needs that level, which the
                     caller reads from ``RequestProfile.think``.
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
        if stop_requested(self._cancel_event):
            raise RunStoppedError
        self._last_stats = None
        request = _chat_request(
            model,
            prompt,
            images=images,
            options=options,
            response_format=format,
            system=system,
        )
        chunks, waited = self._stream_chunks(
            model, request, think=think, timeout=timeout, total_timeout=total_timeout
        )
        return self._answer_from_chunks(model, chunks, waited=waited)

    def _stream_chunks(
        self,
        model: str,
        request: dict,
        *,
        think: bool | str | None,
        timeout: float,
        total_timeout: float | None,
    ) -> tuple[list[Any], bool]:
        """Return the chunks of the answer, and whether the server was busy with another model.

        A model that rejects ``think`` is asked again without it.  Any other error is raised:
        a model that is not pulled or a server that is down does not answer twice as fast.
        """
        from ..exceptions import OCRError  # noqa: PLC0415

        client, stream = self._open_answer(request, think, timeout=timeout)
        waited = False
        reported: WaitDetail | None = None

        def _waiting(elapsed: float) -> bool:
            """Say what the server is doing; return whether it is busy with another model.

            Only a busy server pauses the timeout and marks the sample: loading the model is
            this request's own work, so its time stays in the timeout and in the load figure.
            """
            nonlocal waited, reported
            detail = self.activity(model).wait_detail(model)
            if not detail:
                return False
            if elapsed >= WAIT_DETAIL_AFTER_S and detail != reported:
                reported = detail
                if self.detail_sink is not None:
                    self.detail_sink(detail)
            if detail.kind is not WaitKind.BUSY:
                return False
            waited = True
            return True

        try:
            chunks = self._consume(stream, client, timeout, total_timeout, _waiting)
        except OCRError as exc:
            # A model that does not support ``think`` rejects it with a 400 that names it.
            if think is None or not _rejects_think(exc):
                raise
            client, stream = self._open_answer(request, None, timeout=timeout)
            chunks = self._consume(stream, client, timeout, total_timeout, _waiting)
        return chunks, waited

    def _open_answer(
        self, request: dict, think: bool | str | None, *, timeout: float
    ) -> tuple[Any, Iterator]:
        """Return the client and the stream of *request*; a client without ``think`` retries."""
        import ollama  # noqa: PLC0415

        try:
            client = ollama.Client(host=self.base_url, timeout=timeout)
            try:
                stream = _open_chat_stream(client, request, think)
            except TypeError:
                # Older ollama clients lack the ``think`` kwarg. The output cleaner strips any
                # leaked reasoning regardless.
                stream = _open_chat_stream(client, request, None)
        except Exception as exc:
            raise OllamaUnreachableError(unreachable(self.base_url, message_of(exc))) from exc
        return client, stream

    def _consume(
        self,
        stream: Iterator,
        client: Any,
        timeout: float,
        total_timeout: float | None,
        waiting: Callable[[float], bool],
    ) -> list[Any]:
        """Read the answer to its end, returning the chunks in the order they arrived."""
        return _consume_iterator_with_first_token_timeout(
            _mapped_stream(stream, self.base_url),
            timeout,
            total_timeout,
            self._cancel_event,
            on_abandon=lambda: _cut_connection(client),
            waiting=waiting,
            on_first_token=self.first_token_sink,
        )

    def _answer_from_chunks(
        self, model: str, chunks: list[Any], *, waited: bool
    ) -> tuple[str, ChatStats]:
        """Return the text of the answer and its statistics, and report the statistics."""
        from ..exceptions import OCRError  # noqa: PLC0415

        parts = [(chunk.message.content or "") for chunk in chunks if chunk.message]
        content = "".join(parts)
        # The last chunk carries the summary of the whole answer; earlier ones leave these unset.
        stats = _chunk_stats(chunks[-1]) if chunks else ChatStats()
        if waited:
            stats = replace(stats, waited=True)
        self._last_stats = stats
        # Before the checks below: a request that is refused here still cost the time the
        # server reported, and the sink decides for itself whether the numbers are usable.
        if self._stats_sink is not None:
            self._stats_sink(model, stats)
        # A reasoning model can spend the whole window on thinking it does not return: no text,
        # but a stop at the limit.  That is a cut-off answer, not an empty one -- say so.
        if not content.strip() and not stats.truncated:
            raise OCRError(empty_answer())
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
            raise OCRError(model_pull_failed(message_of(exc))) from exc
