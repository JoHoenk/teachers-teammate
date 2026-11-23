"""Tests for the background-task helper that replaces per-task QThread subclasses."""

from __future__ import annotations

import threading

from PySide6.QtWidgets import QWidget

from teachers_teammate.gui._background import (
    LatestTask,
    _MAX_NETWORK_THREADS,
    _owner_alive,
    background_activity,
    run_in_background,
    run_streaming,
    wait_for_background_tasks,
)


def test_result_is_delivered_on_the_gui_thread(qtbot) -> None:
    """
    Given  a function running in the background
    When   it returns
    Then   on_done receives the result on the GUI thread while the function itself
           ran on another thread
    """
    ran_on: list[int] = []
    delivered: list[tuple[object, BaseException | None, int]] = []

    def work() -> str:
        ran_on.append(threading.get_ident())
        return "result"

    run_in_background(
        work, lambda result, error: delivered.append((result, error, threading.get_ident()))
    )
    qtbot.waitUntil(lambda: bool(delivered), timeout=5000)

    result, error, delivered_on = delivered[0]
    assert (result, error) == ("result", None)
    assert delivered_on == threading.get_ident()
    assert ran_on[0] != threading.get_ident()


def test_error_is_delivered_instead_of_raised(qtbot) -> None:
    """
    Given  a function that raises
    When   it runs in the background
    Then   on_done receives the exception and nothing is raised in the pool
    """
    delivered: list[tuple[object, BaseException | None]] = []

    def work() -> None:
        raise RuntimeError("boom")

    run_in_background(work, lambda result, error: delivered.append((result, error)))
    qtbot.waitUntil(lambda: bool(delivered), timeout=5000)

    assert delivered[0][0] is None
    assert isinstance(delivered[0][1], RuntimeError)
    assert str(delivered[0][1]) == "boom"


def test_latest_task_drops_results_of_superseded_tasks(qtbot) -> None:
    """
    Given  a slow task followed by a newer one on the same LatestTask
    When   both finish
    Then   only the newer result is delivered (a stale answer can never
           overwrite a newer one)
    """
    gate = threading.Event()
    delivered: list[object] = []
    task = LatestTask()

    def slow() -> str:
        gate.wait(timeout=5)
        return "old"

    task.submit(slow, lambda result, error: delivered.append(result))
    task.submit(lambda: "new", lambda result, error: delivered.append(result))
    qtbot.waitUntil(lambda: delivered == ["new"], timeout=5000)

    gate.set()
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)  # let a (wrongly) queued stale delivery arrive
    assert delivered == ["new"]


def test_cancel_makes_pending_results_stale(qtbot) -> None:
    """
    Given  a running task
    When   LatestTask.cancel() is called before it finishes
    Then   its result is never delivered
    """
    gate = threading.Event()
    delivered: list[object] = []
    task = LatestTask()
    task.submit(lambda: gate.wait(timeout=5), lambda result, error: delivered.append(result))
    task.cancel()
    gate.set()
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)
    assert delivered == []


def test_result_for_a_destroyed_owner_is_dropped(qtbot) -> None:
    """
    Given  a task whose owner widget is destroyed while it runs
    When   the task finishes
    Then   on_done is not called (it would touch deleted widgets) and nothing crashes
    """
    gate = threading.Event()
    delivered: list[object] = []
    owner = QWidget()
    run_in_background(
        lambda: gate.wait(timeout=5),
        lambda result, error: delivered.append(result),
        owner=owner,
    )
    owner.deleteLater()
    qtbot.waitUntil(lambda: not _owner_alive(owner), timeout=5000)

    gate.set()
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)
    assert delivered == []


def test_wait_for_background_tasks_times_out_when_a_task_hangs(qtbot) -> None:
    """
    Given  a task that does not finish in time
    When   wait_for_background_tasks is called with a short timeout
    Then   it returns False instead of blocking the application's shutdown
    """
    gate = threading.Event()
    run_in_background(lambda: gate.wait(timeout=5), lambda result, error: None)
    assert wait_for_background_tasks(50) is False
    gate.set()
    assert wait_for_background_tasks(5000) is True


def test_streamed_items_arrive_in_order_before_the_end_on_the_gui_thread(qtbot) -> None:
    """
    Given  a streaming task that reports three items and then returns
    When   it runs in the background
    Then   the items reach on_item in order, on the GUI thread, and on_done comes last
    """
    events: list[tuple[str, object, int]] = []

    def work(emit) -> str:
        for number in (1, 2, 3):
            emit(number)
        return "end"

    run_streaming(
        work,
        lambda item: events.append(("item", item, threading.get_ident())),
        lambda result, error: events.append(("done", (result, error), threading.get_ident())),
    )
    qtbot.waitUntil(lambda: any(kind == "done" for kind, *_ in events), timeout=5000)

    assert [(kind, payload) for kind, payload, _ in events] == [
        ("item", 1),
        ("item", 2),
        ("item", 3),
        ("done", ("end", None)),
    ]
    assert {ident for *_, ident in events} == {threading.get_ident()}


def test_a_streaming_error_is_delivered_after_the_items(qtbot) -> None:
    """
    Given  a streaming task that reports one item and then raises
    When   it runs
    Then   the item arrives and on_done receives the exception
    """
    events: list[object] = []

    def work(emit) -> None:
        emit("half")
        raise RuntimeError("boom")

    run_streaming(
        work,
        lambda item: events.append(item),
        lambda result, error: events.append(error),
    )
    qtbot.waitUntil(lambda: len(events) == 2, timeout=5000)

    assert events[0] == "half"
    assert isinstance(events[1], RuntimeError)


def test_streamed_items_for_a_destroyed_owner_are_dropped(qtbot) -> None:
    """
    Given  a streaming task whose owner widget is destroyed while it runs
    When   it reports items and ends
    Then   neither on_item nor on_done is called
    """
    gate = threading.Event()
    delivered: list[object] = []
    owner = QWidget()

    def work(emit) -> str:
        gate.wait(timeout=5)
        emit("late")
        return "late"

    run_streaming(
        work,
        delivered.append,
        lambda result, error: delivered.append(result),
        owner=owner,
    )
    owner.deleteLater()
    qtbot.waitUntil(lambda: not _owner_alive(owner), timeout=5000)

    gate.set()
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)
    assert delivered == []


def test_latest_task_drops_the_items_of_a_superseded_streaming_task(qtbot) -> None:
    """
    Given  a slow streaming task followed by a newer one on the same LatestTask
    When   both report items and end
    Then   only the newer one's items and end are delivered
    """
    gate = threading.Event()
    delivered: list[object] = []
    task = LatestTask()

    def slow(emit) -> str:
        gate.wait(timeout=5)
        emit("old item")
        return "old"

    def fast(emit) -> str:
        emit("new item")
        return "new"

    task.submit_streaming(slow, delivered.append, lambda result, error: delivered.append(result))
    task.submit_streaming(fast, delivered.append, lambda result, error: delivered.append(result))
    qtbot.waitUntil(lambda: delivered == ["new item", "new"], timeout=5000)

    gate.set()
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)
    assert delivered == ["new item", "new"]


def test_stalled_network_tasks_do_not_hold_up_the_local_ones(qtbot) -> None:
    """
    Given  every thread of the network pool held by a task that waits on a server
    When   a local task and one more network task are started
    Then   the local task is delivered at once, and the extra network task waits its turn
    """
    gate = threading.Event()
    delivered: list[str] = []
    for _ in range(_MAX_NETWORK_THREADS):
        run_in_background(lambda: gate.wait(timeout=10), lambda r, e: None, network=True)
    run_in_background(lambda: "queued", lambda r, e: delivered.append("network"), network=True)

    run_in_background(lambda: "local", lambda r, e: delivered.append("local"))
    qtbot.waitUntil(lambda: "local" in delivered, timeout=5000)
    qtbot.wait(50)
    assert delivered == ["local"]

    gate.set()
    qtbot.waitUntil(lambda: "network" in delivered, timeout=5000)
    assert wait_for_background_tasks(5000)


def test_wait_for_background_tasks_waits_for_the_network_pool_too(qtbot) -> None:
    """
    Given  a task on the network pool that does not finish in time
    When   wait_for_background_tasks is called with a short timeout
    Then   it returns False, and True once the task has ended
    """
    gate = threading.Event()
    run_in_background(lambda: gate.wait(timeout=5), lambda result, error: None, network=True)
    assert wait_for_background_tasks(50) is False
    gate.set()
    assert wait_for_background_tasks(5000) is True


def test_background_activity_counts_the_tasks_running_on_each_pool(qtbot) -> None:
    """
    Given  one task that waits on the local pool
    When   the activity is read
    Then   the local pool counts it, the network pool counts none, and both drop to 0 after
    """
    release = threading.Event()
    started = threading.Event()

    def work() -> None:
        started.set()
        release.wait(timeout=5)

    run_in_background(work, lambda _result, _error: None)
    assert started.wait(timeout=5)
    busy = background_activity()
    release.set()
    qtbot.waitUntil(lambda: background_activity() == {"local": 0, "network": 0}, timeout=5000)

    assert busy == {"local": 1, "network": 0}
