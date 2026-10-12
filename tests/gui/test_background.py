"""Tests for the background-task helper that replaces per-task QThread subclasses."""

from __future__ import annotations

import threading

from PySide6.QtWidgets import QWidget

from teachers_teammate.gui._background import (
    LatestTask,
    _owner_alive,
    run_in_background,
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
