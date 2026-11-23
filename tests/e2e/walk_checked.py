"""Run the app walkthrough in this process with one thing broken, for a test to check the report.

``tests/gui/test_gui_main_walkthrough.py`` starts this as a process of its own, one per walk: a
walk that hangs or aborts ends its own process and leaves a result for the test to read, where the
same walk in the test's process would end the whole test run.  Not part of the bundle: the bundle
runs ``walkthrough.py`` alone.

    python -m tests.e2e.walk_checked <fault> <result.json>

``plain`` breaks nothing and then checks that no thread of Python's is left running; exit code 3
says one is.  ``proofreading`` breaks nothing either and lets the run proofread against
:class:`~tests.fakes.fake_ollama.FakeOllama`, which stays open for the whole walk.
"""

from __future__ import annotations

from collections.abc import Callable
import sys
import threading

from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from teachers_teammate.gui import _dialogs
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._run_controller import RunController
from teachers_teammate.infrastructure import stage_builder
from teachers_teammate.texts import dialogs as dlg
from tests.e2e import walkthrough
from tests.fakes.fake_ollama import FakeOllama, ProofreadingAnswers

LEFT_RUNNING = 3

#: The watchdog of the walk that hangs: long enough for a slow machine to reach the run step.
HANG_WALK_S = 60.0

#: The model tag the proofreading stand-in serves.  Not in the catalog, so no run warns about the
#: memory it would need and nothing is downloaded.
PROOFREADING_MODEL = "walkthrough-proofreader:1b"

#: The servers a walk talks to, kept open for as long as the walk runs.
_SERVERS: list[FakeOllama] = []


def _reader_raises() -> None:
    """The text reader raises when it is built."""

    def broken(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("the reader is broken")

    stage_builder.PpOcrProcessor = broken  # ty: ignore[invalid-assignment]


def _unexpected_dialog() -> None:
    """Run asks a question the step did not announce."""
    run_as_asked = MainWindow._on_run

    def noisy_on_run(self: MainWindow) -> None:
        _dialogs.notify(self, dlg.no_files_selected())
        run_as_asked(self)

    MainWindow._on_run = noisy_on_run


def _unexpected_window() -> None:
    """Run opens a window of its own, after the run has started."""
    run_as_asked = MainWindow._on_run

    def modal_on_run(self: MainWindow) -> None:
        run_as_asked(self)
        dialog = QDialog(self)
        dialog.setWindowTitle("The run can't start")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("<b>The run can't start:</b>"))
        layout.addWidget(QLabel("The built-in reader is not downloaded yet."))
        dialog.exec()

    MainWindow._on_run = modal_on_run


def _slot_raises() -> None:
    """A slot of the run controller raises while the run goes on."""

    def broken(self: RunController, _steps: int) -> None:
        raise RuntimeError("a slot broke")

    RunController._set_steps_done = broken  # ty: ignore[invalid-assignment]


def _proofreading() -> walkthrough.ProofreadingSetup:
    """Answer the proofreading like a model would, and let the walk work with the changed text."""
    server = FakeOllama()
    _SERVERS.append(server)
    server.install(PROOFREADING_MODEL)
    setup = walkthrough.ProofreadingSetup(url=server.url, model=PROOFREADING_MODEL)
    server.answer_chats(
        ProofreadingAnswers(
            last_word=setup.word, explanation=setup.explanation, category=setup.category
        )
    )
    return setup


def _hangs() -> None:
    """The reader never returns, so the watchdog has to end the walk."""
    import time  # noqa: PLC0415

    def stuck(*_args: object, **_kwargs: object) -> object:
        time.sleep(3600)

    stage_builder.PpOcrProcessor = stuck  # ty: ignore[invalid-assignment]
    walkthrough.WALK_S = HANG_WALK_S


FAULTS: dict[str, Callable[[], None]] = {
    "plain": lambda: None,
    "reader-raises": _reader_raises,
    "unexpected-dialog": _unexpected_dialog,
    "unexpected-window": _unexpected_window,
    "slot-raises": _slot_raises,
    "hangs": _hangs,
}

#: The walk that breaks nothing and lets the run proofread.
PROOFREADING = "proofreading"


def main(argv: list[str]) -> int:
    """Break what *argv* names, walk, and return the walk's exit code."""
    fault, out = argv
    proofreading = _proofreading() if fault == PROOFREADING else None
    if fault != PROOFREADING:
        FAULTS[fault]()
    before = set(threading.enumerate())
    code = walkthrough.main([out], proofreading=proofreading)
    if fault == "plain" and code == 0 and set(threading.enumerate()) - before:
        return LEFT_RUNNING
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
