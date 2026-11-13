"""Entry-point / bootstrap regression guards for the GUI apps.

These cover the seams that previously shipped untested and let launch-time aborts
through: ``create_app`` icon/theme ordering, the ``main_gui`` / ``main_benchmark``
launch path, and the windows' ``closeEvent`` thread teardown.
"""

from __future__ import annotations

import sys
import threading

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from teachers_teammate.gui._app_bootstrap import create_app, finish_process
from teachers_teammate.gui.benchmark._benchmark_window import main_benchmark
from teachers_teammate.gui.benchmark.composition import build_benchmark_window
from teachers_teammate.gui._main_window import main_gui
from teachers_teammate.gui.composition import build_main_window

from ._gui_harness import assert_no_running_threads, neutralize_gui_threads


@pytest.fixture(autouse=True)
def _hermetic_gui(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path / "storage"))
    neutralize_gui_threads(monkeypatch)


@pytest.mark.gui
def test_create_app_returns_configured_singleton(qtbot) -> None:
    """
    Given  a running Qt test session
    When   create_app() is called twice
    Then   it returns the same QApplication instance with the app name set
    """
    app1 = create_app(apply_theme=False)
    app2 = create_app(apply_theme=False)
    assert app1 is app2
    assert app1.applicationName() == "teachers-teammate"


@pytest.mark.gui
def test_create_app_invokes_icon_factory_after_app_exists(qtbot) -> None:
    """
    Given  an icon_factory that inspects whether a QApplication exists
    When   create_app() is called with it
    Then   the factory runs only after the QApplication is constructed
           (guards the 'QPixmap before QGuiApplication' regression)
    """
    seen: dict[str, bool] = {}

    def factory():
        seen["app_existed"] = QApplication.instance() is not None

    create_app(icon_factory=factory, apply_theme=False)
    assert seen["app_existed"] is True


@pytest.mark.gui
def test_create_app_apply_theme_sets_stylesheet(qtbot) -> None:
    """
    Given  the application
    When   create_app(apply_theme=True) is called
    Then   a (qdarkstyle) stylesheet is applied
    """
    app = create_app(apply_theme=True)
    assert app.styleSheet() != ""


@pytest.mark.gui
def test_main_gui_constructs_and_shows_window(qtbot, monkeypatch) -> None:
    """
    Given  exec/exit stubbed so launch does not block
    When   main_gui() runs with an injected window factory
    Then   it constructs and shows the window without raising
    """
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(sys, "exit", lambda *a: None)
    shown: list[QWidget] = []

    def factory():
        win = build_main_window()
        qtbot.addWidget(win)
        shown.append(win)
        return win

    main_gui(window_factory=factory)
    assert shown and shown[0].isVisible()


@pytest.mark.gui
def test_main_benchmark_constructs_and_shows_window(qtbot, monkeypatch) -> None:
    """
    Given  exec/exit stubbed so launch does not block
    When   main_benchmark() runs with an injected window factory
    Then   it constructs and shows the benchmark window without raising
    """
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)
    monkeypatch.setattr(sys, "exit", lambda *a: None)
    shown: list[QWidget] = []

    def factory():
        win = build_benchmark_window()
        qtbot.addWidget(win)
        shown.append(win)
        return win

    main_benchmark(window_factory=factory)
    assert shown and shown[0].isVisible()


@pytest.mark.gui
def test_main_window_close_saves_settings_and_stops_threads(qtbot, monkeypatch) -> None:
    """
    Given  an open main window
    When   it is closed
    Then   settings are saved and no background thread is left running
    """
    win = build_main_window()
    qtbot.addWidget(win)
    saved: list[bool] = []
    monkeypatch.setattr(win, "_save_settings", lambda **_kw: saved.append(True))
    win.show()
    qtbot.wait(20)
    win.close()
    qtbot.wait(20)
    assert saved == [True]
    assert_no_running_threads(win)


@pytest.mark.gui
def test_benchmark_window_close_stops_running_worker(qtbot) -> None:
    """
    Given  a benchmark window with a running worker
    When   the window is closed
    Then   the worker's stop_event is set and it is waited on
    """
    win = build_benchmark_window()
    qtbot.addWidget(win)

    class _FakeWorker:
        def __init__(self) -> None:
            self.stop_event = threading.Event()
            self.waited = False

        def isRunning(self) -> bool:
            return True

        def wait(self, _ms: int) -> bool:
            self.waited = True
            return True

    worker = _FakeWorker()
    win._worker = worker  # ty: ignore[invalid-assignment]  # test injects a running worker stub
    win.show()
    qtbot.wait(20)
    win.close()
    assert worker.stop_event.is_set()
    assert worker.waited is True


@pytest.mark.gui
def test_finish_process_exits_normally_without_a_detached_worker(monkeypatch) -> None:
    """
    Given  no worker left running after its window closed
    When   the process is finished with code 3
    Then   the normal exit function gets the code and no hard exit happens
    """
    from teachers_teammate.gui import _worker  # noqa: PLC0415

    monkeypatch.setattr(_worker, "_DETACHED", [])
    exits: list[int] = []
    hard: list[int] = []

    finish_process(3, exit_fn=exits.append, hard_exit=hard.append)

    assert exits == [3]
    assert hard == []


@pytest.mark.gui
def test_finish_process_hard_exits_while_a_detached_worker_runs(monkeypatch) -> None:
    """
    Given  a detached worker that is still running (destroying it would abort the process)
    When   the process is finished with code 0
    Then   the process ends without the normal exit, which would destroy the thread
    """
    from teachers_teammate.gui import _worker  # noqa: PLC0415

    class _Running:
        def isRunning(self) -> bool:
            return True

    monkeypatch.setattr(_worker, "_DETACHED", [_Running()])
    exits: list[int] = []
    hard: list[int] = []

    finish_process(0, exit_fn=exits.append, hard_exit=hard.append, shutdown_logging=lambda: None)

    assert hard == [0]
    assert exits == []


@pytest.mark.gui
def test_a_detached_worker_that_finished_does_not_force_a_hard_exit(monkeypatch) -> None:
    """
    Given  a detached worker that has finished meanwhile
    When   the process is finished
    Then   the normal exit is used
    """
    from teachers_teammate.gui import _worker  # noqa: PLC0415

    class _Done:
        def isRunning(self) -> bool:
            return False

    monkeypatch.setattr(_worker, "_DETACHED", [_Done()])
    exits: list[int] = []

    finish_process(0, exit_fn=exits.append, hard_exit=lambda _c: exits.append(-1))

    assert exits == [0]
