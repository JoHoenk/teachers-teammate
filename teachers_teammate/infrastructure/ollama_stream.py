"""A streaming Ollama answer: its first token, its timeout, its stop, its errors."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
import queue
import threading
import time
from typing import TYPE_CHECKING

from ..exceptions import (
    OCRDeadlineError,
    OCRError,
    OCRTimeoutError,
    OllamaUnreachableError,
    RunStoppedError,
)
from ..messages import answer_abandoned, http_error, message_of, no_first_token, unreachable

if TYPE_CHECKING:
    import ollama

logger = logging.getLogger(__name__)


def mapped_stream[T](stream: Iterator[T], base_url: str) -> Iterator[T]:
    """Translate the errors a streaming Ollama request raises into the app's own errors.

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


def cut_connection(client: ollama.Client) -> None:
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


#: How often a waiting request looks at its cancel event (seconds): a stop takes effect within
#: this long, and the polling costs nothing measurable.
_CANCEL_POLL_S = 0.2


#: How often a request that has no answer yet asks the server what it is doing (seconds).
_WAITING_POLL_S = 2.0


def next_item[T](
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
        return next_item(item_queue, first_token_timeout, cancel_event)
    started = time.monotonic()
    deadline = started + first_token_timeout
    while True:
        step = max(min(_WAITING_POLL_S, deadline - time.monotonic()), _CANCEL_POLL_S)
        try:
            return next_item(item_queue, step, cancel_event)
        except queue.Empty:
            if waiting(time.monotonic() - started):
                deadline = time.monotonic() + first_token_timeout
                continue
            if time.monotonic() >= deadline:
                raise


def _pump[T](
    it: Iterator[T], item_queue: queue.Queue[T | BaseException | None], abandoned: threading.Event
) -> None:
    """Move the items of *it* into *item_queue*, then ``None``; an error goes in as itself.

    Runs in the reader thread of :func:`consume_with_first_token_timeout`.
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


def consume_with_first_token_timeout[T](
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
            # An error the stream already mapped (see mapped_stream) keeps its type: a caller
            # must be able to tell an unreachable server from a model that failed.
            if isinstance(item, OCRError):
                raise item
            raise OCRError(message_of(item)) from item
        results.append(item)
        try:
            item = next_item(
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
