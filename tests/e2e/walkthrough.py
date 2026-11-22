"""The app walkthrough: a script that plays teacher in the real main window.

The app runs this file with ``--_run_script <this.py> <result.json>``
(:func:`~teachers_teammate.frozen_modes.run_script`), so it needs no test build and a changed
script runs against a downloaded bundle.  ``tests/gui/test_gui_main_walkthrough.py`` imports the
same file and runs the same entry on Bazel's real model files; one file, so a container mounts
one path.

The walk builds the window the app builds, clicks what a teacher clicks, types what a teacher
types, and then checks what a teacher would see: the rows of the queue, the Check tab, the stored
texts, the Word files on disk and the summary of every run.  Nothing is shown and nothing is
asked: the boxes and pickers are answered
(:func:`~teachers_teammate.gui._dialogs.answering`), one temporary folder holds the cache, the
settings and the data root, and no API key is read or written.

The result is one JSON file, written when a step fails too, because a windowed build on Windows
has no console.  The exit code is 0 only when every step passed.

What this file may import is the list in ``tests/unit/test_walkthrough_imports.py``: it runs
inside the bundle, which carries the app's own modules, Qt, Pillow and python-docx, and no test
framework.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import dataclasses
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import traceback
from typing import Any

from docx import Document
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, qVersion
from PySide6.QtGui import QAction, QKeyEvent, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QLineEdit,
    QTabWidget,
    QTextEdit,
    QWidget,
)

from teachers_teammate import __version__
from teachers_teammate.application.composition import AppServices, build_app_services
from teachers_teammate.application.queue import StageState
from teachers_teammate.config import Config, load_config_file
from teachers_teammate.domain.output_files import DOCX_SUFFIX, output_path
from teachers_teammate.domain.run_summary import FileStatus, RunStatus, RunSummary
from teachers_teammate.domain.stages import ProgressStage, Stage
from teachers_teammate.gui import _handles
from teachers_teammate.gui._app_bootstrap import create_app
from teachers_teammate.gui._background import wait_for_background_tasks
from teachers_teammate.gui._dialogs import Asked, answering
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._worker import OCRWorker, detached_workers_running
from teachers_teammate.gui.composition import build_main_window
from teachers_teammate.gui.settings import SettingsPage, SettingsWindow
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import Answer

# ── what the walk waits for ──────────────────────────────────────────────────
#: A step that opens a window or waits for a redraw.
UI_S = 10.0
#: One run over the three documents.
RUN_S = 180.0
#: The whole walk; the watchdog ends the process at this point, so a deadlock cannot run into
#: the CI job's own limit.  Well under the Bazel target's timeout, so the result is written first.
WALK_S = 600.0
#: How often the modal guard looks for a window the step did not open.
MODAL_POLL_MS = 200
#: ``EDIT_COMMIT_DELAY_MS``: the Check tab stores 400 ms after the last key.
EDIT_COMMIT_S = 0.4

# ── the inputs ───────────────────────────────────────────────────────────────
#: The folder's name carries a space, and two of the three names carry a non-ASCII character:
#: a path the app mishandles shows here.  The words drawn on the pages are ASCII, so a reader
#: slip cannot fail the walk.
INPUT_FOLDER = "Eingang mit Leerzeichen"
OUTPUT_FOLDER = "Ausgabe"
EXPORT_FOLDER = "Export"
PNG_NAME = "Tafel Übung.png"
PDF_NAME = "Brief an Eltern.pdf"
TXT_NAME = "Notizen.txt"
PNG_WORDS = "Hello World"
PDF_PAGES = ("First Page", "Second Page")
TXT_TEXT = "Notizen für Montag"
#: What the walk types into the transcription in the Check tab.
TYPED = " Walkthrough"
TYPED_WORD = TYPED.strip()

#: The steps, in order; the first failure ends the walk.
STEPS: tuple[tuple[str, str], ...] = (
    ("start", "_step_start"),
    ("choose-folder", "_step_choose_folder"),
    ("run", "_step_run"),
    ("open", "_step_open"),
    ("correct", "_step_correct"),
    ("export", "_step_export"),
    ("run-again", "_step_run_again"),
    ("stop-after-file", "_step_stop_after_file"),
    ("stop-now", "_step_stop_now"),
    ("finish", "_step_finish"),
    ("restart", "_step_restart"),
    ("other-windows", "_step_other_windows"),
    ("shut-down", "_step_shut_down"),
)


class _Failed(Exception):
    """One condition of a step did not hold; the message says which."""


# ── the settings file and the inputs ─────────────────────────────────────────


def write_settings(root: Path) -> Path:
    """Write the settings the walk runs with, and return the file's path.

    ``input`` is left out on purpose: step 2 chooses the folder the teacher's way.  Preprocessing
    stays at its default (automatic), so the walk takes the same path through the page models as
    a teacher would.  Nothing here may be a key the schema drops or corrects — a notice at load
    is a box, and step 1 fails on it.
    """
    path = root / "ocr.toml"
    text = "\n".join(
        (
            "check_for_updates = false",
            # Nothing listens here: the requirements check finds a stopped server and says so
            # instead of contacting whatever runs on this computer.
            'ollama_url = "http://127.0.0.1:9"',
            'ocr_engine = "ppocr"',
            'language = "English"',
            "correction_enabled = false",
            "evaluation_enabled = false",
            "anonymization_enabled = false",
            "docx_enabled = true",
            # A text file would be refused in an input folder, which is where the walk's
            # output folder would end up if it were the same one.
            "text_enabled = false",
            "text_anonymized_enabled = false",
            f'output = "{(root / OUTPUT_FOLDER).as_posix()}"',
            "",
        )
    )
    path.write_text(text, encoding="utf-8")
    return path


def saved_config(root: Path) -> Config:
    """Return the configuration the settings file names, built as the window builds it."""
    values = dict(load_config_file(root / "ocr.toml").values)
    input_text = str(values.get("input", ""))
    input_dir = Path(input_text) if input_text else Path(".")
    output_text = str(values.get("output", ""))
    return Config.from_settings(
        values,
        input_dir=input_dir,
        output_dir=Path(output_text) if output_text else input_dir,
        recursive=True,
    )


def write_inputs(folder: Path) -> None:
    """Draw the three documents the walk reads: an image, a two-page scan, a text file."""
    folder.mkdir(parents=True, exist_ok=True)
    _page(PNG_WORDS).save(str(folder / PNG_NAME), "PNG")
    pages = [_page(words) for words in PDF_PAGES]
    pages[0].save(str(folder / PDF_NAME), "PDF", save_all=True, append_images=pages[1:])
    (folder / TXT_NAME).write_text(TXT_TEXT, encoding="utf-8")


def _page(words: str) -> Image.Image:
    """Return one white page with *words* written on it, as the reader tests draw theirs."""
    image = Image.new("RGB", (600, 200), color=(255, 255, 255))
    ImageDraw.Draw(image).text(
        (40, 80), words, fill=(0, 0, 0), font=ImageFont.load_default(size=48)
    )
    return image


def docx_text(path: Path) -> str:
    """Return every line of *path*, paragraphs and table cells alike.

    The layout is a setting (one table per document, or plain paragraphs), so a check reads all
    of it instead of assuming one.
    """

    def of(container: Any) -> list[str]:
        lines = [paragraph.text for paragraph in container.paragraphs]
        for table in container.tables:
            for row in table.rows:
                for cell in row.cells:
                    lines.extend(of(cell))
        return lines

    return "\n".join(of(Document(str(path))))


@contextmanager
def isolated() -> Iterator[Path]:
    """Run with one temporary folder for the cache, the config root and the data root.

    ``TEACHERS_TEAMMATE_TMPDIR`` is what the app reads (``config.override_root``), and under it
    the app also keeps its GUI settings in a file instead of the platform's registry or plist.
    ``QT_QPA_PLATFORM`` is left alone — native in a bundle, offscreen under Bazel — and
    ``QT_PLUGIN_PATH`` is never set: a bundle also carries OpenCV's Qt 5 platform plugin, which
    a set path would find before Qt's own.
    """
    names = ("TEACHERS_TEAMMATE_TMPDIR",)
    previous = {name: os.environ.get(name) for name in names}
    folder = tempfile.mkdtemp(prefix="teachers-teammate-walk-")
    os.environ["TEACHERS_TEAMMATE_TMPDIR"] = folder
    try:
        yield Path(folder)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(folder, onexc=lambda *_exc: None)


# ── the guards ───────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class _Expected:
    """One box or picker a step says the GUI will ask, and what the teacher answers."""

    call: str
    title: str = ""
    start: Path | None = None
    mention: tuple[str, ...] = ()
    choice: str = ""
    reply: Path | None = None

    def matches(self, asked: Asked) -> bool:
        """Whether *asked* is this one: the same call, title, proposal and names."""
        if asked.call != self.call:
            return False
        item = asked.item
        if self.title and getattr(item, "title", "") != self.title:
            return False
        if self.start is not None and Path(str(getattr(item, "start", ""))) != self.start:
            return False
        body = str(getattr(item, "body", ""))
        return all(name in body for name in self.mention)


class Answers:
    """The teacher's answers: what each step expects, and everything that was asked.

    A call a step did not expect is recorded and fails that step, and so is an expectation the
    GUI never made.  Until then an unexpected question is declined and an unexpected picker
    cancelled, so the walk goes on to the assertion that would have failed anyway.
    """

    def __init__(self, step: Callable[[], str]) -> None:
        self._step = step
        #: One entry per call, in the order the GUI made them.
        self.record: list[dict[str, Any]] = []
        #: The calls no step expected, as they are said in the result.
        self.unexpected: list[str] = []
        self._expected: list[_Expected] = []

    def expect(
        self,
        call: str,
        *,
        title: str = "",
        start: Path | None = None,
        mention: Sequence[str] = (),
        choice: str = "",
        reply: Path | None = None,
    ) -> None:
        """Say that the current step will be asked *call*, and how the teacher answers."""
        self._expected.append(_Expected(call, title, start, tuple(mention), choice, reply))

    def __call__(self, asked: Asked) -> Any:
        """Answer *asked* the way the step said, or record it as unexpected."""
        for index, expected in enumerate(self._expected):
            if expected.matches(asked):
                del self._expected[index]
                self._record(asked, expected=True)
                return self._answer(asked, expected)
        self._record(asked, expected=False)
        self.unexpected.append(self._describe(asked))
        return None

    def take_unexpected(self) -> list[str]:
        """Return the calls no step expected since the last time it was asked."""
        recorded, self.unexpected = self.unexpected, []
        return recorded

    def unasked(self) -> list[str]:
        """Return what a step announced the GUI would ask and the GUI did not."""
        return [f"{expected.call}: {expected.title or '-'}" for expected in self._expected]

    def clear(self) -> None:
        """Forget what was announced: the step it belonged to is over."""
        self._expected.clear()

    def _record(self, asked: Asked, *, expected: bool) -> None:
        """Add *asked* to the record, with the step that was running."""
        self.record.append(
            {
                "step": self._step(),
                "call": asked.call,
                "title": str(getattr(asked.item, "title", "")),
                "expected": expected,
            }
        )

    @staticmethod
    def _describe(asked: Asked) -> str:
        """Return *asked* as one line for the result's ``failed`` list."""
        title = str(getattr(asked.item, "title", ""))
        return f"an unexpected {asked.call} ({title})"

    @staticmethod
    def _answer(asked: Asked, expected: _Expected) -> Any:
        """Return what the teacher does with *asked*, as the step announced it."""
        if asked.call == "notify":
            return None
        if asked.call == "choose":
            choices = getattr(asked.item, "choices", ())
            chosen = next((one for one in choices if one.id == expected.choice), None)
            if chosen is None:
                raise _Failed(
                    f"the question has no button {expected.choice!r}: {[one.id for one in choices]}"
                )
            return Answer(chosen, checked=False)
        if asked.call == "pick_files":
            return [] if expected.reply is None else [expected.reply]
        return expected.reply


class Modals:
    """A window the step did not open: recorded, then closed, so the walk cannot hang in it."""

    def __init__(self) -> None:
        #: One line per window that was closed this way, for the result.
        self.record: list[str] = []
        #: The windows the current step's check has not seen yet.
        self.unexpected: list[str] = []
        #: How many windows the current step opens itself.
        self.expected = 0
        self._closed: list[QWidget] = []

    def caught(self, window: QWidget) -> None:
        """Record *window* the first time it is caught; a second look at it says nothing."""
        if any(window is one for one in self._closed):
            return
        self._closed.append(window)
        line = window.objectName() or type(window).__name__
        self.record.append(line)
        self.unexpected.append(line)

    def take_unexpected(self) -> list[str]:
        """Return the windows closed since the last time it was asked."""
        recorded, self.unexpected = self.unexpected, []
        return recorded


class Warnings(logging.Handler):
    """Collects what the app logs at WARNING and above; a warning is reported, not a failure."""

    def __init__(self, into: list[str]) -> None:
        super().__init__(level=logging.WARNING)
        self._into = into

    def emit(self, record: logging.LogRecord) -> None:
        """Add *record* to the result's ``warnings``."""
        try:
            self._into.append(f"{record.name}: {record.getMessage()}")
        except Exception:  # noqa: BLE001  # a logging handler never breaks the walk
            self._into.append(f"{record.name}: <unprintable record>")


@dataclasses.dataclass(frozen=True)
class _StopPoint:
    """Where a run is to be stopped: the signal, which emission, and how often Stop is clicked."""

    signal: str
    when: Callable[[tuple[Any, ...]], bool]
    clicks: int


class Recorder(QObject):
    """The worker the window starts, listened to before the window listens.

    It is the window's ``worker_factory``: it builds the real
    :class:`~teachers_teammate.gui._worker.OCRWorker`, connects its own slots first (so they run
    before the window's, which then sees a consistent table) and hands it over.  Steps 8a and 8b
    ride on the same seam: a *stop point* is a blocking connection, so the worker waits while the
    GUI thread clicks Stop, and no timing decides the outcome.
    """

    def __init__(self, *, on_stop: Callable[[], None]) -> None:
        super().__init__()
        self._on_stop = on_stop
        #: The steps each file went through, keyed by source id, in the order they started.
        self.stages: dict[str, list[str]] = {}
        #: The source ids of the files the run started, in order.
        self.started: list[str] = []
        #: What the run ended with, one per file.
        self.outcomes: list[Any] = []
        #: The whole run's summary; ``None`` until it ended.
        self.summary: RunSummary | None = None
        self.worker: OCRWorker | None = None
        self._stop_point: _StopPoint | None = None

    def begin(self) -> None:
        """Forget the last run's events, so a step reads only its own."""
        self.stages.clear()
        self.started.clear()
        self.outcomes.clear()
        self.summary = None
        self._stop_point = None
        self.worker = None

    def arm_stop_point(
        self, signal: str, *, when: Callable[[tuple[Any, ...]], bool], clicks: int
    ) -> None:
        """Click Stop *clicks* times when *signal* carries an emission *when* accepts."""
        self._stop_point = _StopPoint(signal, when, clicks)

    def build(self, config: Config, files: Sequence[Path], *, app_service: AppServices) -> Any:
        """Build the real worker and listen to it first; the window's factory is this method."""
        worker = OCRWorker(config, files, app_service=app_service)
        worker.file_started.connect(self._on_file_started)
        worker.stage_started.connect(self._on_stage_started)
        worker.file_done.connect(self._on_file_done)
        worker.run_finished.connect(self._on_run_finished)
        self.worker = worker
        point = self._stop_point
        if point is not None:
            signal = getattr(worker, point.signal)
            signal.connect(self._stop_here, Qt.ConnectionType.BlockingQueuedConnection)
        return worker

    def _on_file_started(self, source_id: str, _name: str, _index: int, _total: int) -> None:
        self.started.append(source_id)

    def _on_stage_started(
        self, source_id: str, _name: str, stage: str, _index: int, _total: int
    ) -> None:
        self.stages.setdefault(source_id, []).append(str(stage))

    def _on_file_done(self, event: Any) -> None:
        self.outcomes.append(event.outcome)

    def _on_run_finished(self, summary: Any) -> None:
        self.summary = summary

    def _stop_here(self, *args: Any) -> None:
        """The worker waits here while the GUI clicks Stop, so the stop has a fixed place."""
        point = self._stop_point
        if point is None or not point.when(args):
            return
        self._stop_point = None
        worker = self.worker
        if worker is not None:
            try:
                getattr(worker, point.signal).disconnect(self._stop_here)
            except RuntimeError, TypeError:
                pass  # already gone with the worker
        for _ in range(point.clicks):
            self._on_stop()


# ── the walk ─────────────────────────────────────────────────────────────────


class Walk:
    """One walk through the app: the window, the steps and what they found.

    ``root`` is the isolated folder the settings, the documents and the results live in;
    ``screenshots`` a folder for one picture per window state (left empty under Bazel);
    ``out`` where the watchdog writes the result if the walk hangs (``None``: it only exits).
    """

    def __init__(
        self,
        *,
        root: Path,
        service: AppServices,
        screenshots: Path | None = None,
        out: Path | None = None,
    ) -> None:
        self._root = root
        self._service = service
        self._screenshots = screenshots
        self._out = out
        self._current = "before the first step"
        self._app: QApplication | None = None
        self._window: MainWindow | None = None
        self._recorder = Recorder(on_stop=self._click_stop)
        self._answers = Answers(step=lambda: self._current)
        self._modals = Modals()
        self._exceptions: list[str] = []
        self._last_exception = ""
        self._warnings: list[str] = []
        self._pictures: list[str] = []
        self._failed: list[str] = []
        self._steps: list[dict[str, Any]] = []
        self.platform = ""
        self.screen = ""
        self.versions: dict[str, str] = {}

    # ── running it ────────────────────────────────────────────────────────

    def run(self) -> dict[str, Any]:
        """Walk every step and return the result; the first failure ends the walk."""
        try:
            self._app = create_app()  # before the guards: the modal poll's timer needs it
        except Exception as exc:  # noqa: BLE001 - the result says what happened
            self._failed.append(f"no QApplication: {type(exc).__name__}: {exc}")
            return self.result()
        watchdog = threading.Timer(WALK_S, self._timed_out)
        watchdog.daemon = True
        with self._guards():
            watchdog.start()
            try:
                for name, method in STEPS:
                    if not self._run_step(name, getattr(self, method)):
                        break
            finally:
                watchdog.cancel()
                watchdog.join()
                try:
                    self._shut_down()
                except Exception as exc:  # noqa: BLE001 - the result is written all the same
                    self._failed.append(f"shut down: {type(exc).__name__}: {exc}")
        return self.result()

    def result(self) -> dict[str, Any]:
        """Return what the walk found, in the shape the CI job uploads."""
        return {
            "ok": not self._failed,
            "platform": self.platform,
            "screen": self.screen,
            "versions": dict(self.versions),
            "failed": list(self._failed),
            "steps": list(self._steps),
            "dialogs": list(self._answers.record),
            "modals": list(self._modals.record),
            "exceptions": list(self._exceptions),
            "warnings": list(self._warnings),
            "screenshots": list(self._pictures),
        }

    def _run_step(self, name: str, step: Callable[[], None]) -> bool:
        """Run one step; record what it did and whether it passed."""
        self._current = name
        started = time.monotonic()
        detail = ""
        passed = True
        try:
            step()
            self._check_guards()
        except _Failed as exc:
            passed = False
            detail = str(exc)
        except Exception as exc:  # noqa: BLE001 - a bug in a step is a failed step, not a lost result
            passed = False
            detail = f"{type(exc).__name__}: {exc}"
        if not passed:
            self._failed.append(f"{name}: {detail}")
        self._steps.append(
            {
                "name": name,
                "ok": passed,
                "seconds": round(time.monotonic() - started, 1),
                "detail": detail,
            }
        )
        self._answers.clear()
        return passed

    def _timed_out(self) -> None:
        """Write what the walk found and end the process: a deadlock is not a six-hour job."""
        result = self.result()
        result["failed"] = [*result["failed"], f"timed out in step {self._current}"]
        result["ok"] = False
        if self._out is not None:
            write_result(self._out, result)
        os._exit(1)

    @contextmanager
    def _guards(self) -> Iterator[None]:
        """Install the guards for the whole walk and put every one of them back."""
        previous_hook = sys.excepthook
        handler = Warnings(self._warnings)
        logging.getLogger().addHandler(handler)
        timer = QTimer()
        timer.setInterval(MODAL_POLL_MS)
        timer.timeout.connect(self._poll_modals)
        sys.excepthook = self._on_exception
        try:
            with answering(self._answers):
                timer.start()
                try:
                    yield
                finally:
                    timer.stop()
        finally:
            sys.excepthook = previous_hook
            logging.getLogger().removeHandler(handler)

    def _on_exception(self, kind: type[BaseException], exc: BaseException, tb: Any) -> None:
        """A slot raised: PySide6 hands it here and carries on, so the walk records it."""
        self._last_exception = f"{kind.__name__}: {exc}"
        self._exceptions.append(self._last_exception)
        self._exceptions.extend(
            line.strip() for line in traceback.format_exception(kind, exc, tb)[-3:]
        )

    def _poll_modals(self) -> None:
        """Close a window or popup the step did not open, and record it."""
        if self._modals.expected:
            return
        modal = QApplication.activeModalWidget()
        if modal is not None:
            self._close_unexpected(modal)
            return
        popup = QApplication.activePopupWidget()
        if popup is not None:
            self._close_unexpected(popup)

    def _close_unexpected(self, window: QWidget) -> None:
        """Record *window* and close it, so the walk goes on to its own assertion."""
        self._modals.caught(window)
        if isinstance(window, QDialog):
            window.reject()
        window.close()

    def _check_guards(self) -> None:
        """Fail the step on what no step expected: a call, a window, a slot's exception."""
        found = [
            *self._answers.take_unexpected(),
            *(f"a window opened: {name}" for name in self._modals.take_unexpected()),
            *(f"an exception in a slot: {line}" for line in self._exceptions),
            *(f"never asked: {line}" for line in self._answers.unasked()),
        ]
        if found:
            raise _Failed("; ".join(found))

    def _shut_down(self) -> None:
        """Close the window and wait for the app's threads, whatever the walk ended with."""
        worker = self._recorder.worker
        if worker is not None and worker.isRunning():
            worker.request_stop(now=True)
            self._settle(lambda: not worker.isRunning(), "the run to stop", RUN_S)
        window = self._window
        if window is not None:
            window.close()
            self._settle(lambda: not window.isVisible(), "the window to close", UI_S)
        wait_for_background_tasks()

    # ── what every step uses ──────────────────────────────────────────────

    @property
    def _app_or_fail(self) -> QApplication:
        """Return the application object; a step before the first one has none."""
        app = self._app
        if app is None:
            raise _Failed("no QApplication was built")
        return app

    def _settle(self, condition: Callable[[], bool], what: str, deadline_s: float) -> None:
        """Pump the event loop until *condition* holds; raise :class:`_Failed` when it does not."""
        app = self._app
        deadline = time.monotonic() + deadline_s
        while True:
            if app is not None:
                app.processEvents()
            try:
                if condition():
                    return
            except RuntimeError:
                pass  # a widget that went away: the condition does not hold yet
            if time.monotonic() > deadline:
                # A slot that raised is why the window never came: say so, not only the wait.
                broken = f" (a slot raised: {self._last_exception})" if self._exceptions else ""
                raise _Failed(f"{what} did not happen within {deadline_s:g} s{broken}")
            time.sleep(0.01)

    def _child(self, kind: type[QObject], name: str) -> Any:
        """Return the widget named *name* under the window, or fail the step naming it."""
        window = self._window
        if window is None:
            raise _Failed("no window was built")
        found = window.findChild(kind, name)
        if found is None:
            raise _Failed(f"the window has no {name}")
        return found

    def _button(self, name: str) -> QAbstractButton:
        """Return the button named *name*."""
        return self._child(QAbstractButton, name)

    def _action(self, name: str) -> QAction:
        """Return the menu entry named *name*."""
        return self._child(QAction, name)

    def _tabs(self) -> QTabWidget:
        """Return the window's tabs."""
        return self._child(QTabWidget, _handles.MAIN_TABS)

    def _table(self) -> ResultsTable:
        """Return the queue's table."""
        return self._child(ResultsTable, _handles.RESULTS_TABLE)

    def _field(self, name: str) -> QLineEdit:
        """Return the text field named *name*."""
        return self._child(QLineEdit, name)

    def _source(self, name: str) -> str:
        """Return the source id of the queued document called *name*."""
        for source_id in self._table().queued_source_ids():
            if Path(source_id).name == name:
                return source_id
        raise _Failed(f"no row for {name}")

    def _names(self, paths: Sequence[str]) -> list[str]:
        """Return the file names of *paths*, in their order."""
        return [Path(path).name for path in paths]

    def _config(self) -> Config:
        """Return the configuration the saved settings file names."""
        return saved_config(self._root)

    def _inputs(self) -> Path:
        """Return the folder the documents are read from."""
        return self._root / INPUT_FOLDER

    def _output(self) -> Path:
        """Return the folder a run writes its results into."""
        return self._root / OUTPUT_FOLDER

    def _run_docx(self, name: str) -> Path:
        """Return where a run would write the Word file of the document called *name*."""
        source = self._inputs() / name
        return output_path(source, self._inputs(), self._output(), DOCX_SUFFIX)

    def _screenshot(self, name: str) -> None:
        """Save a picture of the window (or, inside a modal, of the modal)."""
        self._save_picture(self._window.grab() if self._window is not None else None, name)

    def _save_picture(self, picture: QPixmap | None, name: str) -> None:
        """Write *picture* as ``<name>.png`` into the screenshots folder."""
        if self._screenshots is None or picture is None:
            return
        self._screenshots.mkdir(parents=True, exist_ok=True)
        path = self._screenshots / f"{name}.png"
        if picture.save(str(path)):
            self._pictures.append(path.name)

    def _wait_run(self, what: str) -> RunSummary:
        """Wait for the run to end and return its summary."""
        self._settle(lambda: self._recorder.summary is not None, f"{what} to end", RUN_S)
        summary = self._recorder.summary
        if summary is None:  # pragma: no cover - the wait above raises first
            raise _Failed(f"{what} ended without a summary")
        return summary

    def _click_run(self) -> None:
        """Click Run, as a teacher does; a disabled button would do nothing and hang the walk."""
        button = self._button(_handles.RUN_BUTTON)
        if not button.isEnabled():
            raise _Failed("Run is disabled")
        button.click()

    def _click_stop(self) -> None:
        """Click Stop once."""
        self._button(_handles.STOP_BUTTON).click()

    def _in_modal(self, trigger: Callable[[], None], inside: Callable[[QDialog], None]) -> None:
        """Run *inside* on the window *trigger* opens, then close it.

        The callback runs inside ``exec()``; PySide6 would swallow what it raises, so the
        exception is carried out and raised after *trigger* returned.
        """
        problems: list[BaseException] = []
        opened: list[bool] = []

        def handle() -> None:
            opened.append(True)
            modal = QApplication.activeModalWidget()
            if modal is None:
                problems.append(_Failed("no window opened"))
                return
            self._modals.expected += 1
            try:
                if not isinstance(modal, QDialog):  # pragma: no cover - type check of the app
                    problems.append(_Failed(f"not a dialog but {type(modal).__name__}"))
                    return
                inside(modal)
            except BaseException as exc:  # noqa: BLE001 - carried out of the slot
                problems.append(exc)
            finally:
                self._modals.expected -= 1
                modal.close()

        starter = QTimer()
        starter.setSingleShot(True)
        starter.timeout.connect(handle)
        starter.start(0)
        try:
            trigger()
        finally:
            starter.stop()  # a trigger that opened nothing must not leave *handle* for later
        if problems:
            raise problems[0]
        if not opened:
            raise _Failed("no window opened")

    def _shown(self, name: str) -> QWidget | None:
        """Return the *shown* window named *name*.

        Not ``_top_level``: a window this walk closed before is still listed, and in a process
        that walks the app more than once (a test session) an earlier walk's window would be
        found first.
        """
        return next(
            (
                widget
                for widget in QApplication.topLevelWidgets()
                if widget.objectName() == name and widget.isVisible()
            ),
            None,
        )

    def _assert_docx(self, path: Path, words: Sequence[str]) -> None:
        """Fail the step unless the Word file at *path* holds every one of *words*."""
        if not path.is_file():
            raise _Failed(f"no Word file at {path}")
        found = docx_text(path)
        missing = [word for word in words if word not in found]
        if missing:
            raise _Failed(f"{path.name} does not hold {', '.join(missing)}: {found[:200]!r}")

    def _stored_transcription(self, source_id: str) -> bool:
        """Whether the store has the walk's typed word in *source_id*'s transcription."""
        document = self._service.documents.check_document(self._config(), Path(source_id))
        return document is not None and TYPED_WORD in document.raw_text

    def _transcription_worked_on(self, source_id: str) -> bool:
        """Whether the store says the teacher worked on *source_id*'s transcription."""
        worked = self._service.documents.worked_on_documents(
            self._config(), [Path(source_id)], run_proofreads=False
        )
        return Path(source_id) in worked.transcribed

    # ── the steps ─────────────────────────────────────────────────────────

    def _step_start(self) -> None:
        """Build the real window and show it; nothing is checked yet, nothing is queued."""
        self._window = build_main_window(
            app_service=self._service, worker_factory=self._recorder.build
        )
        self._window.show()
        self._settle(lambda: self._window.isVisible(), "the window to be shown", UI_S)
        if self._tabs().isTabEnabled(1):
            raise _Failed("the Check tab is enabled before a document was read")
        if self._button(_handles.EXPORT_BUTTON).isEnabled():
            raise _Failed("Export is enabled before there is a result")
        screen = QApplication.primaryScreen()
        self.platform = QApplication.platformName()
        self.screen = "" if screen is None else f"{screen.size().width()}x{screen.size().height()}"
        self.versions = {"app": __version__, "qt": qVersion()}

    def _step_choose_folder(self) -> None:
        """Write the documents, then choose their folder the way a teacher does."""
        write_inputs(self._inputs())
        self._answers.expect(
            "pick_folder", title=dlg.select_input_folder().title, reply=self._inputs()
        )
        self._button(_handles.INPUT_FOLDER_BUTTON).click()
        self._settle(
            lambda: Path(self._field(_handles.INPUT_PATH).text()) == self._inputs(),
            "the chosen folder to be shown",
            UI_S,
        )
        table = self._table()
        self._settle(lambda: len(table.queued_source_ids()) == 3, "the three rows", UI_S)
        names = self._names(table.queued_source_ids())
        if sorted(names) != sorted((PNG_NAME, PDF_NAME, TXT_NAME)):
            raise _Failed(f"the queue holds {names}")
        for source_id in table.queued_source_ids():
            row = table.row_for(source_id)
            if row is None or row.ocr is not StageState.NONE:
                raise _Failed(f"{Path(source_id).name} already has a reading")

    def _step_run(self) -> None:
        """Run the whole queue and check what each document got."""
        table = self._table()
        table.selectAll()
        self._recorder.begin()
        self._click_run()
        summary = self._wait_run("the run")
        if summary.status is not RunStatus.DONE:
            raise _Failed(
                f"the run ended {summary.status}: {summary.error or list(summary.failed)}"
            )
        if sorted(summary.succeeded) != sorted((PNG_NAME, PDF_NAME, TXT_NAME)):
            raise _Failed(f"succeeded is {list(summary.succeeded)}")
        if summary.failed or summary.skipped:
            raise _Failed(f"failed {list(summary.failed)}, skipped {list(summary.skipped)}")
        image, pdf, text = (
            self._source(PNG_NAME),
            self._source(PDF_NAME),
            self._source(TXT_NAME),
        )
        for source_id in (image, pdf):
            if ProgressStage.OCR not in self._recorder.stages.get(source_id, []):
                raise _Failed(f"{Path(source_id).name} was not read")
        if self._recorder.stages.get(text):
            raise _Failed(f"the text file went through {self._recorder.stages[text]}")
        for source_id in table.queued_source_ids():
            if not table.has_text(source_id):
                raise _Failed(f"{Path(source_id).name} has no text after the run")
        self._assert_docx(self._run_docx(PNG_NAME), (PNG_WORDS,))
        self._assert_docx(self._run_docx(PDF_NAME), PDF_PAGES)
        self._assert_docx(self._run_docx(TXT_NAME), (TXT_TEXT,))
        self._settle(
            lambda: self._button(_handles.EXPORT_BUTTON).isEnabled(), "Export to be enabled", UI_S
        )
        if not self._tabs().isTabEnabled(1):
            raise _Failed("the Check tab stays disabled after a run")
        self._screenshot("run")

    def _step_open(self) -> None:
        """Open the image in the Check tab and read what is shown against the store."""
        image = self._source(PNG_NAME)
        buttons = self._window.findChildren(QAbstractButton, _handles.OPEN_DOCUMENT)
        opener = next(
            (button for button in buttons if button.property(_handles.SOURCE_ID_PROPERTY) == image),
            None,
        )
        if opener is None:
            raise _Failed(f"the row of {PNG_NAME} has no Open button")
        opener.click()
        self._settle(lambda: self._tabs().currentIndex() == 1, "the Check tab", UI_S)
        document = self._service.documents.check_document(self._config(), Path(image))
        if document is None:
            raise _Failed("the store has no document for the image")
        view = self._child(QTextEdit, _handles.READ_VIEW)
        self._settle(
            lambda: view.toPlainText().rstrip("\n") == document.raw_plain.rstrip("\n"),
            "the transcription in the Check tab",
            UI_S,
        )
        if PNG_WORDS not in view.toPlainText():
            raise _Failed(f"the Check tab does not show {PNG_WORDS!r}")
        if self._table().selected_source_ids() != [image]:
            raise _Failed(f"the selection is {self._table().selected_source_ids()}")
        self._screenshot("check")

    def _step_correct(self) -> None:
        """Type into the transcription and wait for the store to carry it."""
        image = self._source(PNG_NAME)
        view = self._child(QTextEdit, _handles.READ_VIEW)
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        view.setTextCursor(cursor)
        started = time.monotonic()
        for character in TYPED:
            QApplication.sendEvent(
                view,
                QKeyEvent(QEvent.Type.KeyPress, 0, Qt.KeyboardModifier.NoModifier, character),
            )
        self._settle(
            lambda: (
                time.monotonic() - started >= EDIT_COMMIT_S and self._stored_transcription(image)
            ),
            "the typed text in the store",
            UI_S,
        )
        self._settle(
            lambda: self._transcription_worked_on(image),
            "the store to know the transcription was worked on",
            UI_S,
        )

    def _step_export(self) -> None:
        """Export the open document as a Word file, the way a teacher saves one."""
        self._tabs().setCurrentIndex(0)
        image = self._source(PNG_NAME)
        self._table().select_source(image)
        target = self._root / EXPORT_FOLDER / (Path(PNG_NAME).stem + DOCX_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        export = self._button(_handles.EXPORT_BUTTON)
        menu = export.menu()
        if menu is None:
            raise _Failed("the Export button has no menu")
        action = next(
            (entry for entry in menu.actions() if entry.text() == ExportKind.DOCX.label), None
        )
        if action is None:
            raise _Failed(f"the Export menu has no {ExportKind.DOCX.label!r} entry")
        proposal = self._output() / (Path(PNG_NAME).stem + DOCX_SUFFIX)
        self._answers.expect(
            "save_file",
            title=dlg.save_export(
                ExportKind.DOCX.label, ExportKind.DOCX.file_filter, str(proposal)
            ).title,
            start=proposal,
            reply=target,
        )
        action.trigger()
        self._settle(lambda: target.is_file(), "the exported Word file", UI_S)
        self._assert_docx(target, (TYPED_WORD,))
        run_file = self._run_docx(PNG_NAME)
        if TYPED_WORD in docx_text(run_file):
            raise _Failed(f"the export changed the run's own {run_file.name}")
        self._screenshot("export")

    def _step_run_again(self) -> None:
        """Run again: every result is stored, so nothing is read a second time."""
        self._tabs().setCurrentIndex(0)
        self._table().selectAll()
        self._recorder.begin()
        self._click_run()
        summary = self._wait_run("the second run")
        if summary.status is not RunStatus.DONE or len(summary.succeeded) != 3:
            raise _Failed(f"the second run ended {summary.status}: {list(summary.succeeded)}")
        if self._recorder.stages:
            raise _Failed(f"a step ran again: {self._recorder.stages}")
        self._assert_docx(self._run_docx(PNG_NAME), (TYPED_WORD,))

    def _step_stop_after_file(self) -> None:
        """Re-read everything, press Stop once, and check the run stopped after one file."""
        table = self._table()
        order = table.queued_source_ids()
        if len(order) != 3:
            raise _Failed(f"the queue holds {len(order)} rows")
        table.selectAll()
        self._answers.expect(
            "choose",
            title=dlg.read_again([]).title,
            mention=(PNG_NAME,),
            choice=dlg.READ_AGAIN,
        )
        self._recorder.begin()
        self._recorder.arm_stop_point(
            "file_started", when=lambda args, second=order[1]: args[0] == second, clicks=1
        )
        table.stage_run_requested.emit(order, Stage.OCR)
        summary = self._wait_run("the stopped run")
        if summary.status is not RunStatus.STOPPED:
            raise _Failed(f"the run ended {summary.status}")
        if self._names(summary.succeeded) != self._names(order[:2]):
            raise _Failed(f"succeeded is {list(summary.succeeded)}")
        if self._names(summary.not_started) != self._names(order[2:]):
            raise _Failed(f"not_started is {list(summary.not_started)}")
        if summary.failed or summary.stopped:
            raise _Failed(f"failed {list(summary.failed)}, stopped {list(summary.stopped)}")
        row = table.row_for(order[2])
        if row is None or row.ocr is not StageState.NONE:
            raise _Failed(f"{Path(order[2]).name} was read although the run stopped before it")

    def _step_stop_now(self) -> None:
        """Stop a run at once, and check that the file it cut short left nothing behind."""
        table = self._table()
        pdf = self._source(PDF_NAME)
        table.clearSelection()
        table.select_source(pdf)
        self._recorder.begin()
        self._recorder.arm_stop_point(
            "stage_started",
            when=lambda args: args[0] == pdf and str(args[2]) == ProgressStage.PREPROCESSING,
            clicks=2,
        )
        table.stage_run_requested.emit([pdf], Stage.OCR)
        summary = self._wait_run("the second stopped run")
        if summary.error or summary.failed:
            raise _Failed(f"the run failed: {summary.error or list(summary.failed)}")
        if self._names(summary.stopped) != [PDF_NAME]:
            raise _Failed(f"stopped is {list(summary.stopped)} (expected ['{PDF_NAME}'])")
        stopped = [event for event in self._recorder.outcomes if event.status is FileStatus.STOPPED]
        if len(stopped) != 1:
            raise _Failed(f"the run recorded {len(stopped)} stopped files")
        row = table.row_for(pdf)
        if row is None or row.ocr is not StageState.NONE:
            raise _Failed(f"{PDF_NAME} has a reading although the stop came first")
        document = self._service.documents.check_document(self._config(), Path(pdf))
        if document is not None and document.raw_text.strip():
            raise _Failed(f"the stopped file left {document.raw_text[:80]!r} stored")

    def _step_finish(self) -> None:
        """Run the whole queue once more, so the walk ends on a complete result."""
        table = self._table()
        table.selectAll()
        self._recorder.begin()
        self._click_run()
        summary = self._wait_run("the run to finish with")
        if summary.status is not RunStatus.DONE or len(summary.succeeded) != 3:
            raise _Failed(f"the run ended {summary.status}: {list(summary.succeeded)}")
        for source_id in table.queued_source_ids():
            if not table.has_text(source_id):
                raise _Failed(f"{Path(source_id).name} has no text after the run")
        self._screenshot("documents")

    def _step_restart(self) -> None:
        """Close the window, open a new one on the same folder, and check what it shows."""
        self._window.close()
        self._settle(lambda: not self._window.isVisible(), "the window to close", UI_S)
        if not wait_for_background_tasks():
            raise _Failed("a background task is still running after the window closed")
        if detached_workers_running():
            raise _Failed("a worker is still running after the window closed")
        if not (self._root / "gui-settings.ini").is_file():
            raise _Failed("the window wrote no gui-settings.ini under the root")
        self._service = build_app_services()
        self._recorder = Recorder(on_stop=self._click_stop)
        self._window = build_main_window(
            app_service=self._service, worker_factory=self._recorder.build
        )
        self._window.show()
        self._settle(lambda: self._window.isVisible(), "the window to be shown again", UI_S)
        self._settle(
            lambda: Path(self._field(_handles.INPUT_PATH).text()) == self._inputs(),
            "the documents folder to be shown again",
            UI_S,
        )
        self._settle(
            lambda: Path(self._field(_handles.OUTPUT_PATH).text()) == self._output(),
            "the results folder to be shown again",
            UI_S,
        )
        table = self._table()
        self._settle(lambda: len(table.queued_source_ids()) == 3, "the restored queue", UI_S)
        for source_id in table.queued_source_ids():
            if not table.has_text(source_id):
                raise _Failed(f"{Path(source_id).name} lost its text over the restart")
        if not self._button(_handles.EXPORT_BUTTON).isEnabled():
            raise _Failed("Export stays disabled after the restart")
        same = [
            (row.name, row.ocr, row.has_text)
            for row in self._service.documents.queue_rows(
                self._config(), [Path(one) for one in table.queued_source_ids()]
            )
        ]
        shown = [
            (table.row_for(one).name, table.row_for(one).ocr, table.has_text(one))
            for one in table.queued_source_ids()
        ]
        if same != shown:
            raise _Failed(f"the saved settings name other documents: {same} against {shown}")

    def _step_other_windows(self) -> None:
        """Open every other window and close it again; the settings must be unchanged."""
        settings = self._root / "ocr.toml"
        before = settings.read_bytes()

        def in_settings(modal: QDialog) -> None:
            if not isinstance(modal, SettingsWindow):
                raise _Failed(f"All settings opened a {type(modal).__name__}")
            for page in SettingsPage:
                modal.show_page(page)
                if modal.page(page) is None:
                    raise _Failed(f"the Settings window built no {page} page")
            self._save_picture(modal.grab(), "settings")

        self._in_modal(self._action(_handles.ALL_SETTINGS).trigger, in_settings)
        for action_name, window_name in (
            (_handles.BENCHMARK, _handles.BENCHMARK_WINDOW),
            (_handles.SYSTEM_MONITOR, _handles.MONITOR_WINDOW),
        ):
            self._action(action_name).trigger()
            self._settle(
                lambda name=window_name: self._shown(name) is not None,
                f"the window {window_name}",
                UI_S * 3,  # the Benchmark window builds a whole app of its own
            )
            window = self._shown(window_name)
            if window is not None:
                window.close()
                self._settle(
                    lambda name=window_name: self._shown(name) is None,
                    f"{window_name} to close",
                    UI_S,
                )
        for action_name in (
            _handles.USER_GUIDE,
            _handles.LICENSES,
            _handles.ABOUT,
            _handles.SETUP_ASSISTANT,
        ):
            self._in_modal(self._action(action_name).trigger, lambda _modal: None)
        if settings.read_bytes() != before:
            raise _Failed("opening the other windows changed the settings file")

    def _step_shut_down(self) -> None:
        """Close the window and check that nothing of the app is left running."""
        self._window.close()
        self._settle(lambda: not self._window.isVisible(), "the window to close", UI_S)
        if not wait_for_background_tasks():
            raise _Failed("a background task is still running after the window closed")
        if detached_workers_running():
            raise _Failed("a worker is still running after the window closed")
        self._check_guards()


# ── the entry point ──────────────────────────────────────────────────────────


def walk_main_window(
    root: Path,
    service: AppServices,
    *,
    screenshots: Path | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """Play teacher in a real main window on *root* and return the result."""
    return Walk(root=root, service=service, screenshots=screenshots, out=out).run()


def write_result(path: Path, result: dict[str, Any]) -> None:
    """Write *result* to *path* as JSON, creating the folder if it is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    """Walk the app in this process and write the result; 0 when every step passed."""
    parser = argparse.ArgumentParser(
        prog="walkthrough", description="Play teacher in the opened app and report what it did."
    )
    parser.add_argument("out", help="the file the result goes into")
    parser.add_argument(
        "--screenshots", default="", help="a folder for one picture per window state"
    )
    options = parser.parse_args(argv)
    out = Path(options.out).expanduser().resolve()
    screenshots = Path(options.screenshots).expanduser().resolve() if options.screenshots else None
    # The pictures are written while the walk runs, so their folder must not be the one the
    # walk's isolation removes at the end.
    with isolated() as root:
        write_settings(root)
        result = walk_main_window(root, build_app_services(), screenshots=screenshots, out=out)
    write_result(out, result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
