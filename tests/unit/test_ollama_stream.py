"""Unit tests for teachers_teammate.infrastructure.ollama_{kind}."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.exceptions import (
    OCRDeadlineError,
    OCRError,
    OCRTimeoutError,
    RunStoppedError,
)
from teachers_teammate.infrastructure import ollama_stream
from teachers_teammate.messages import MessageKey


def test_an_answer_that_never_ends_is_abandoned_at_the_total_timeout() -> None:
    """
    Given  a stream that keeps producing chunks and never finishes
    When   it is consumed with a total timeout
    Then   OCRDeadlineError is raised after about that time, and the stream is closed so the
           server stops generating
    """
    import time  # noqa: PLC0415

    closed = []

    def _forever():
        try:
            while True:
                time.sleep(0.01)
                yield 1
        finally:
            closed.append(True)

    started = time.monotonic()
    with pytest.raises(OCRDeadlineError) as raised:
        ollama_stream.consume_with_first_token_timeout(_forever(), 1.0, total_timeout=0.2)

    assert raised.value.args[0].key is MessageKey.ANSWER_ABANDONED

    assert time.monotonic() - started < 1.0
    deadline = time.monotonic() + 2.0
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [True]


def test_a_cancel_event_ends_a_running_answer_quickly_and_closes_the_stream() -> None:
    """
    Given  a stream that keeps producing chunks and a cancel event set after a moment
    When   it is consumed with that event
    Then   RunStoppedError is raised within about half a second and the stream is closed so the
           server stops generating
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    closed = []
    cancel = threading.Event()

    def _forever():
        try:
            while True:
                time.sleep(0.01)
                yield 1
        finally:
            closed.append(True)

    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(RunStoppedError):
        ollama_stream.consume_with_first_token_timeout(_forever(), 5.0, cancel_event=cancel)

    assert time.monotonic() - started < 1.0
    deadline = time.monotonic() + 2.0
    while not closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert closed == [True]


def test_a_cancel_event_ends_the_wait_for_the_first_token() -> None:
    """
    Given  a stream that never produces a first chunk and a cancel event set after a moment
    When   it is consumed with a long first-token timeout
    Then   RunStoppedError is raised long before that timeout
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()

    def _silent():
        time.sleep(5)
        yield 1

    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(RunStoppedError):
        ollama_stream.consume_with_first_token_timeout(_silent(), 30.0, cancel_event=cancel)

    assert time.monotonic() - started < 1.5


def test_a_total_timeout_does_not_cut_a_stream_that_finishes_in_time() -> None:
    """
    Given  a short stream
    When   it is consumed with a generous total timeout
    Then   every item is returned, as without one
    """
    assert ollama_stream.consume_with_first_token_timeout(
        iter([1, 2, 3]), 1.0, total_timeout=5.0
    ) == [
        1,
        2,
        3,
    ]


# ── ollama_stream.consume_with_first_token_timeout ─────────────────────────────


def test_consume_iterator_returns_all_items_when_first_arrives_in_time() -> None:
    """
    Given  an iterator that yields three items promptly
    When   ollama_stream.consume_with_first_token_timeout() is called
    Then   all items are returned in order
    """
    assert ollama_stream.consume_with_first_token_timeout(iter([1, 2, 3]), 1.0) == [1, 2, 3]


def test_consume_iterator_raises_when_first_item_times_out() -> None:
    """
    Given  an iterator whose first item is delayed beyond the timeout
    When   ollama_stream.consume_with_first_token_timeout() is called
    Then   OCRError with the first-token key is raised
    """
    import time  # noqa: PLC0415

    def _slow():
        time.sleep(5)
        yield 1

    with pytest.raises(OCRError) as raised:
        ollama_stream.consume_with_first_token_timeout(_slow(), 0.05)

    assert raised.value.args[0].key is MessageKey.NO_FIRST_TOKEN


def test_a_wait_pauses_the_first_token_timeout() -> None:
    """
    Given  an iterator whose first item arrives after the timeout, and a hook that reports the
           server is busy with something else
    When   ollama_stream.consume_with_first_token_timeout() is called
    Then   the item is returned instead of a timeout: waiting behind another model is not a
           failure of this request
    """
    import time  # noqa: PLC0415

    def _slow():
        time.sleep(0.3)
        yield 1

    assert ollama_stream.consume_with_first_token_timeout(
        _slow(), 0.05, waiting=lambda _elapsed: True
    ) == [1]


def test_the_timeout_counts_again_once_the_wait_is_over() -> None:
    """
    Given  an iterator that never yields, and a hook that reports a wait only once
    When   ollama_stream.consume_with_first_token_timeout() is called
    Then   the timeout fires after the wait, so a server that stops answering still ends the
           request
    """
    import time  # noqa: PLC0415

    def _never():
        time.sleep(5)
        yield 1

    answers = [True, False]

    with pytest.raises(OCRError) as raised:
        ollama_stream.consume_with_first_token_timeout(
            _never(), 0.05, waiting=lambda _elapsed: answers.pop(0) if answers else False
        )

    assert raised.value.args[0].key is MessageKey.NO_FIRST_TOKEN


def test_the_first_token_hook_fires_once_when_the_answer_begins() -> None:
    """
    Given  an iterator that yields two items, and a hook for the first token
    When   ollama_stream.consume_with_first_token_timeout() is called
    Then   the hook is called exactly once, so the run's clock starts where the answer really
           began and not again at every chunk
    """
    seen: list[str] = []

    assert ollama_stream.consume_with_first_token_timeout(
        iter([1, 2]), 1.0, on_first_token=lambda: seen.append("token")
    ) == [1, 2]
    assert seen == ["token"]


def test_consume_iterator_propagates_reader_exception_as_ocr_error() -> None:
    """
    Given  an iterator that raises after yielding one item
    When   ollama_stream.consume_with_first_token_timeout() is called
    Then   the exception is re-raised as an OCRError
    """

    def _boom():
        yield 1
        raise RuntimeError("kaboom")

    with pytest.raises(OCRError, match="kaboom"):
        ollama_stream.consume_with_first_token_timeout(_boom(), 1.0)


def test_a_finished_answer_that_is_already_queued_survives_a_stop() -> None:
    """
    Given  a stream whose answer is complete while the cancel event is already set
    When   it is consumed
    Then   the finished answer is returned instead of being thrown away
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()

    def _answer():
        yield 1
        yield 2
        # Both chunks and the end marker are queued before the consumer looks again.
        time.sleep(0.05)

    stream = _answer()
    real_next_item = ollama_stream.next_item

    def next_item_after_stop(item_queue, timeout, cancel_event):
        if not cancel.is_set():
            deadline = time.monotonic() + 5
            while item_queue.qsize() < 3 and time.monotonic() < deadline:
                time.sleep(0.01)  # until both chunks and the end marker are queued
            cancel.set()  # ... then press "Stop now"
        return real_next_item(item_queue, timeout, cancel_event)

    with patch.object(ollama_stream, "next_item", next_item_after_stop):
        result = ollama_stream.consume_with_first_token_timeout(stream, 5.0, cancel_event=cancel)

    assert result == [1, 2]


@pytest.mark.parametrize("reason", ["stop", "first_token_timeout", "deadline"])
def test_giving_up_on_a_request_cuts_its_connection(reason: str) -> None:
    """
    Given  a stream that never answers (or never ends) and a caller that can cut the connection
    When   the request is stopped, times out waiting for the first token, or hits its deadline
    Then   on_abandon is called once, so the server stops working on the abandoned request
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()
    cut: list[bool] = []

    def _silent():
        time.sleep(3)
        yield 1

    def _endless():
        while True:
            time.sleep(0.01)
            yield 1

    kwargs: dict = {"on_abandon": lambda: cut.append(True)}
    if reason == "stop":
        kwargs["cancel_event"] = cancel
        threading.Timer(0.1, cancel.set).start()
        stream, first, expected = _silent(), 30.0, RunStoppedError
    elif reason == "first_token_timeout":
        stream, first, expected = _silent(), 0.1, OCRTimeoutError
    else:
        kwargs["total_timeout"] = 0.1
        stream, first, expected = _endless(), 5.0, OCRDeadlineError

    with pytest.raises(expected):
        ollama_stream.consume_with_first_token_timeout(stream, first, **kwargs)

    assert cut == [True]


def test_a_failing_on_abandon_does_not_hide_the_stop() -> None:
    """
    Given  an on_abandon callback that raises
    When   the request is stopped
    Then   RunStoppedError is still what the caller sees
    """
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415

    cancel = threading.Event()
    cancel.set()

    def _silent():
        time.sleep(1)
        yield 1

    def boom() -> None:
        raise RuntimeError("already closed")

    with pytest.raises(RunStoppedError):
        ollama_stream.consume_with_first_token_timeout(
            _silent(), 30.0, cancel_event=cancel, on_abandon=boom
        )


def test_cut_connection_closes_the_client_of_the_request() -> None:
    """
    Given  an ollama client
    When   its connection is cut
    Then   the client is closed (a blocked read fails and the server sees the drop)
    """
    client = MagicMock()

    ollama_stream.cut_connection(client)

    client.close.assert_called_once_with()


def test_cut_connection_falls_back_to_the_http_client_of_an_older_client() -> None:
    """
    Given  a client without the public close
    When   its connection is cut
    Then   the httpx client under it is closed instead
    """
    http = MagicMock()
    older_client: Any = SimpleNamespace(_client=http)  # a client from before `close()`

    ollama_stream.cut_connection(older_client)

    http.close.assert_called_once_with()
