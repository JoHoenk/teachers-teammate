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

Given a :class:`ProofreadingSetup`, the run proofreads too, and the walk reads what that left: the
items of the Check tab, a ticked item, the model's word in the Word file, and a review the app has
to drop because it no longer fits its text.  The bundle walk never does: the stand-in server is
test code (:class:`~tests.fakes.fake_ollama.ProofreadingAnswers`) and stays outside this file.

A few steps belong to one system and run only there (``PLATFORM_STEPS``); docs/testing.md →
"On each system" says which ones and what they check.

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
import faulthandler
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
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    Qt,
    QtMsgType,
    QTimer,
    qInstallMessageHandler,
    qVersion,
)
from PySide6.QtGui import (
    QAction,
    QContextMenuEvent,
    QKeyEvent,
    QPixmap,
    QTextCursor,
    QTextDocumentFragment,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QTabWidget,
    QTextEdit,
    QWidget,
)

from teachers_teammate import __version__
from teachers_teammate.application.check_items import ItemKind
from teachers_teammate.application.composition import AppServices, build_app_services
from teachers_teammate.application.queue import StageState
from teachers_teammate.application.run import state_store_for
from teachers_teammate.config import Config, load_config_file
from teachers_teammate.domain.output_files import DOCX_SUFFIX, output_path
from teachers_teammate.domain.proofreading.review import CorrectionReview
from teachers_teammate.domain.run_summary import FileStatus, RunStatus, RunSummary
from teachers_teammate.domain.stages import ProgressStage
from teachers_teammate.gui import _handles
from teachers_teammate.gui._app_bootstrap import create_app
from teachers_teammate.gui._background import background_activity, wait_for_background_tasks
from teachers_teammate.gui._dialogs import Asked, answering
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui._main_window import MainWindow, _app_icon
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._worker import OCRWorker, detached_workers_running
from teachers_teammate.gui.composition import build_main_window
from teachers_teammate.gui.settings import SettingsPage, SettingsWindow
from teachers_teammate.infrastructure.proofreading.schemas import Category
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts import main_window as win
from teachers_teammate.texts._types import Answer, StandardButton

# ── what the walk waits for ──────────────────────────────────────────────────
#: A step that opens a window or waits for a redraw.
UI_S = 10.0
#: One run over the three documents.
RUN_S = 180.0
#: The whole walk; the watchdog ends the process at this point, so a deadlock cannot run into
#: the CI job's own limit.  Well under the Bazel target's timeout, so the result is written first.
WALK_S = 600.0
#: How long the walk waits for the app's background tasks after a window closed.  A hardware probe
#: is a subprocess with a 5 s limit of its own, and a cold machine is slower than that.
BACKGROUND_MS = 30_000
#: How many Qt warnings the result keeps.
QT_MESSAGES_KEPT = 200
#: How often the modal guard looks for a window the step did not open.
MODAL_POLL_MS = 200
#: ``EDIT_COMMIT_DELAY_MS``: the Check tab stores 400 ms after the last key.
EDIT_COMMIT_S = 0.4

# ── the inputs ───────────────────────────────────────────────────────────────
#: The folder's name carries a space, and two of the three names carry a non-ASCII character:
#: a path the app mishandles shows here.  The words drawn on the pages are ASCII, and what the
#: reader made of them is compared with a slack of a few characters (``_reads``): which CPU
#: instructions the reader's runtime picks can differ from one machine to the next.  What the walk
#: types and the text file hold are compared exactly.
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
#: Where the export of the Windows step writes, and the file it holds open while it does.
LOCKED_FOLDER = "Gesperrt"
#: A results folder as long as a long path needs: over 260 characters, where Windows needs its
#: long-path support.  One name stays short enough for any file system's limit on a name.
LONG_PATH = 300
LONG_PATH_NAME = "Ein langer Ordnername der Ausgabe"

# ── the proofreading ─────────────────────────────────────────────────────────
#: What the model writes in place of the last word of every text, and what it answers when the app
#: asks why.  The last word, whatever the reader made of it: the stand-in cannot know what a page
#: was read as, and one change per document is what the walk needs, the same on every machine.
PROOFREADING_WORD = "Zielwort"
PROOFREADING_EXPLANATION = "The student chose the wrong word."
#: The category the stand-in files its change under; ``word_choice`` is not a minor one, so the
#: list counts the item on its own.
PROOFREADING_CATEGORY: Category = "word_choice"

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

#: The steps only a walk with a proofreading runs, each right after the step it names.
PROOFREADING_STEPS: tuple[tuple[str, str, str], ...] = (
    ("proofreading-items", "open", "_step_proofreading_items"),
    ("proofreading-checked", "correct", "_step_proofreading_checked"),
    ("review-dropped", "restart", "_step_review_dropped"),
)


#: The steps that need a system of their own: ``(name, after, method, platforms)``.  Each runs
#: right after the first step of *after* that this walk runs, and only where ``sys.platform`` is
#: one of *platforms*, so a bundle walk picks them up without CI wiring of its own.
PLATFORM_STEPS: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    ("export-held-open", ("export",), "_step_export_held_open", ("win32",)),
    # Right after the first run, before the teacher works on anything: the results folder holds
    # the stored results, so a folder of its own is one the run fills from the start.
    # Linux has no 260-character limit and its folder is only long; the run into it is the same
    # work, which is how a Bazel walk exercises this step rather than a bundle build.
    ("long-output-path", ("run",), "_step_long_output_path", ("linux", "win32")),
    ("case-spelling", ("restart",), "_step_case_spelling", ("darwin", "win32")),
)


def _insert_after(
    steps: list[tuple[str, str]], after: Sequence[str], step: tuple[str, str]
) -> None:
    """Insert *step* into *steps* after the first of *after* that *steps* holds."""
    anchor = next(name for name in after if any(step_name == name for step_name, _m in steps))
    steps.insert(next(i for i, (name, _m) in enumerate(steps) if name == anchor) + 1, step)


def walk_steps(*, proofreading: bool, platform: str = sys.platform) -> tuple[tuple[str, str], ...]:
    """Return the steps a walk runs here: its own, the platform's, and the proofreading ones."""
    steps = list(STEPS)
    for name, after, method, platforms in PLATFORM_STEPS:
        if platform in platforms:
            _insert_after(steps, after, (name, method))
    if not proofreading:
        return tuple(steps)
    for name, after, method in PROOFREADING_STEPS:
        _insert_after(steps, (after,), (name, method))
    return tuple(steps)


@dataclasses.dataclass(frozen=True)
class ProofreadingSetup:
    """A proofreading server and the model the walk asks: the stand-in's address, tag and words.

    The walk knows nothing else about the stand-in: it names what the run is pointed at and what
    the model answers — the word it writes in place of the last word of every text, and the
    sentence and category it explains each change with.
    """

    url: str
    model: str
    word: str = PROOFREADING_WORD
    explanation: str = PROOFREADING_EXPLANATION
    category: Category = PROOFREADING_CATEGORY


class _Failed(Exception):
    """One condition of a step did not hold; the message says which."""


# ── the settings file and the inputs ─────────────────────────────────────────


def write_settings(root: Path, *, proofreading: ProofreadingSetup | None = None) -> Path:
    """Write the settings the walk runs with, and return the file's path.

    ``input`` is left out on purpose: step 2 chooses the folder the teacher's way.  Preprocessing
    stays at its default (automatic), so the walk takes the same path through the page models as
    a teacher would.  Nothing here may be a key the schema drops or corrects — a notice at load
    is a box, and step 1 fails on it.

    With *proofreading* the run proofreads against that server.  Its address is on this computer,
    so no run asks whether the documents may leave it.
    """
    path = root / "ocr.toml"
    lines = [
        "check_for_updates = false",
        # Nothing listens here without a proofreading: the requirements check finds a stopped
        # server and says so instead of contacting whatever runs on this computer.
        f'ollama_url = "{proofreading.url if proofreading is not None else "http://127.0.0.1:9"}"',
        'ocr_engine = "ppocr"',
        'language = "English"',
    ]
    if proofreading is not None:
        lines.append(f'correction_model = "{proofreading.model}"')
    lines += [
        f"correction_enabled = {str(proofreading is not None).lower()}",
        "evaluation_enabled = false",
        "anonymization_enabled = false",
        "docx_enabled = true",
        # A text file would be refused in an input folder, which is where the walk's
        # output folder would end up if it were the same one.
        "text_enabled = false",
        "text_anonymized_enabled = false",
        f'output = "{(root / OUTPUT_FOLDER).as_posix()}"',
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
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


def _shifted(review: CorrectionReview) -> CorrectionReview:
    """Return *review* with every offset moved by one, as a review carried over wrongly is."""
    return dataclasses.replace(
        review,
        edits=tuple(
            dataclasses.replace(
                note,
                parts=tuple(
                    dataclasses.replace(
                        part, raw_start=part.raw_start - 1, raw_end=part.raw_end - 1
                    )
                    for part in note.parts
                ),
            )
            for note in review.edits
        ),
        unclear=tuple(
            dataclasses.replace(note, raw_start=note.raw_start - 1, raw_end=note.raw_end - 1)
            for note in review.unclear
        ),
    )


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


def _squeezed(text: str) -> str:
    """Return *text* in lower case with every run of white space as one space."""
    return " ".join(text.casefold().split())


def _reads(text: str, words: str) -> bool:
    """Whether *text* holds *words*, give or take a character or two (case and spacing aside).

    The reader's runtime picks its kernels by CPU, so the same page can read a letter differently
    on two machines; what is checked is that the page was read, not that it was read to the letter.
    The slack is one edit per eight characters, at least one.  What the walk typed itself or took
    from a text file is not read by anything and is compared exactly, not with this.
    """
    needle, hay = _squeezed(words), _squeezed(text)
    if needle in hay:
        return True
    slack = max(1, len(needle) // 8)
    # Sellers' variant of the edit distance: a match may start anywhere in *hay*.
    previous = list(range(len(needle) + 1))
    for character in hay:
        current = [0]
        for index, expected in enumerate(needle, start=1):
            current.append(
                min(
                    previous[index] + 1,
                    current[index - 1] + 1,
                    previous[index - 1] + (expected != character),
                )
            )
        if current[-1] <= slack:
            return True
        previous = current
    return False


@contextmanager
def isolated(root: Path | None = None, *, keep: bool = False) -> Iterator[Path]:
    """Run with one folder for the cache, the config root and the data root.

    ``TEACHERS_TEAMMATE_TMPDIR`` is what the app reads (``config.override_root``), and under it
    the app also keeps its GUI settings in a file instead of the platform's registry or plist.
    ``QT_QPA_PLATFORM`` is left alone — native in a bundle, offscreen under Bazel — and
    ``QT_PLUGIN_PATH`` is never set: a bundle also carries OpenCV's Qt 5 platform plugin, which
    a set path would find before Qt's own.

    *root* runs in a folder of the caller's, which *keep* leaves as the walk left it — what an
    upgrade walk needs, and what a walk being looked into afterwards needs.  Without *root* a
    temporary folder is made.  At the end the walk removes only a folder it made itself: one
    that was there before (an old version's data root) is never removed, *keep* or not.
    """
    previous = os.environ.get("TEACHERS_TEAMMATE_TMPDIR")
    # The real path: macOS keeps its temporary folder behind a link (/var -> /private/var) and
    # Windows may hand out an 8.3 short name, and the app resolves every source id it is given.
    made_here = root is None or not Path(root).exists()
    folder = Path(root or tempfile.mkdtemp(prefix="teachers-teammate-walk-")).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.environ["TEACHERS_TEAMMATE_TMPDIR"] = str(folder)
    try:
        yield folder
    finally:
        if previous is None:
            os.environ.pop("TEACHERS_TEAMMATE_TMPDIR", None)
        else:
            os.environ["TEACHERS_TEAMMATE_TMPDIR"] = previous
        if made_here and not keep:
            shutil.rmtree(folder, onexc=lambda *_exc: None)


# ── what only one system has ─────────────────────────────────────────────────


@contextmanager
def _held_open(path: Path) -> Iterator[None]:
    """Hold *path* open so that nothing else may write it.

    A word processor keeps the document it has open to itself, so a second writer gets a
    permission error; this asks for no sharing at all, which is stricter than the read sharing
    Word allows and is what the step needs.  Windows only: elsewhere a second writer is allowed,
    which is why the step that uses this runs there and nowhere else.
    """
    import ctypes  # noqa: PLC0415  # Windows only: no such call elsewhere
    from ctypes import wintypes  # noqa: PLC0415

    # ``use_last_error`` keeps the error of this call apart: one the interpreter raises later
    # would otherwise be what ``WinError()`` reports.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = wintypes.HANDLE
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    handle = create_file(
        str(path),
        0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE: what a word processor asks
        0,  # no sharing at all
        None,
        2,  # CREATE_ALWAYS: the file is the walk's, whether it exists or not
        0x80,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    if handle == wintypes.HANDLE(-1).value or handle is None:
        raise _Failed(
            f"could not hold {path.name} open: {ctypes.WinError(ctypes.get_last_error())}"
        )
    try:
        yield
    finally:
        kernel32.CloseHandle(handle)


def _long_paths_enabled() -> bool:
    """Whether this Windows has long paths switched on, which a deeper folder needs.

    The app adds no long-path prefix of its own, so a path over 260 characters reaches the file
    systems below it only where the machine's own switch is on — and a default Windows has it
    off.  The step records what it found, so the result says what a passing walk rests on.
    """
    try:
        import winreg  # noqa: PLC0415  # Windows only: no such module elsewhere
    except ImportError:  # not Windows, where the step that asks does not run
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except OSError:  # no such value: Windows' own default, which is off
        return False


def _deep_folder(base: Path) -> Path:
    """Return a folder below *base* whose path is longer than :data:`LONG_PATH` characters."""
    folder = base
    while len(str(folder)) <= LONG_PATH:
        folder = folder / LONG_PATH_NAME
    return folder


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
            chosen = next(
                (one for one in choices if expected.choice in (one.id, one.standard)), None
            )
            if chosen is None:
                raise _Failed(
                    f"the question has no button {expected.choice!r}: "
                    f"{[one.id or one.standard or one.label for one in choices]}"
                )
            return Answer(chosen, checked=False)
        if asked.call == "pick_files":
            return [] if expected.reply is None else [expected.reply]
        return expected.reply


class Modals:
    """A window the step did not open: recorded, then closed, so the walk cannot hang in it.

    A line carries what the window says, its title and the sentences it lists: a bundle that
    lost a model file opens a window saying so, and the result must name it.
    """

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
        title = window.windowTitle()
        said = [text for text in (title, *self._said(window, title)) if text]
        if said:
            line = f"{line}: {' / '.join(said)}"
        self.record.append(line)
        self.unexpected.append(line)

    @classmethod
    def _said(cls, window: QWidget, title: str) -> list[str]:
        """Return what *window* tells a teacher: its labels' sentences, without the title again."""
        lines = []
        for label in window.findChildren(QLabel):
            if not label.isVisibleTo(window):
                continue
            text = cls._plain(label.text())
            if text and text not in (title, f"{title}:"):
                lines.append(text)
        return lines

    @staticmethod
    def _tagged(text: str) -> bool:
        """Whether *text* holds a tag (``<b>``, ``</b>``, ``<br>``), not just a ``<`` sign.

        ``Qt.mightBeRichText`` is not always there: PySide6 attaches the QtGui half of the ``Qt``
        namespace only after something has used it.
        """
        start = text.find("<")
        while start != -1:
            end = text.find(">", start)
            if end == -1:
                return False
            first = text[start + 1 : start + 2]
            if first.isalpha() or first in ("/", "!"):
                return True
            start = text.find("<", start + 1)
        return False

    @classmethod
    def _plain(cls, text: str) -> str:
        """Return *text* without Qt's markup: a dialog bolds its title and the walk keeps words."""
        if cls._tagged(text):
            text = QTextDocumentFragment.fromHtml(text).toPlainText()
        return " ".join(text.split())

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


class Watchdog:
    """Writes the result so far and ends the process when the walk stops making progress.

    ``main`` arms it before the app services are built, because building them can hang too: a
    deadlock must end with a file and an exit code, never with the CI job's own limit.
    """

    def __init__(self, out: Path | None) -> None:
        self._out = out
        self._walk: Walk | None = None
        self._started = False
        self._timer = threading.Timer(WALK_S, self._fire)
        self._timer.daemon = True

    def start(self) -> None:
        """Arm the timer; ``main`` arms it, and the walk arms it again when it runs alone."""
        if not self._started:
            self._started = True
            self._timer.start()

    def stop(self) -> None:
        """Disarm it and wait for a fire that is already running; unarmed is nothing to do."""
        if not self._started:
            return
        self._timer.cancel()
        self._timer.join()

    def follow(self, walk: Walk) -> None:
        """Take the current step and the result from *walk*."""
        self._walk = walk

    def _fire(self) -> None:
        walk = self._walk
        result = walk.result() if walk is not None else empty_result()
        step = walk.current if walk is not None else "before the first step"
        result["ok"] = False
        result["failed"] = [*result["failed"], f"timed out in step {step}"]
        result["hang"] = hang_report(walk)
        if self._out is not None:
            write_result(self._out, result)
        os._exit(1)


def hang_report(walk: Walk | None) -> dict[str, Any]:
    """Return what the process was doing when the watchdog fired.

    Every thread's stack, then the app's own counters: a walk that stops making progress says where
    it stands, not only which step it was in.  Nothing here may raise, or the result is never
    written.
    """
    report: dict[str, Any] = {}
    try:
        with tempfile.TemporaryFile("w+", encoding="utf-8") as sink:
            faulthandler.dump_traceback(file=sink, all_threads=True)
            sink.seek(0)
            report["stacks"] = sink.read()
    except Exception as exc:  # noqa: BLE001 - a report that cannot be made is said, not raised
        report["stacks"] = f"unavailable: {type(exc).__name__}: {exc}"
    if walk is not None:
        try:
            report.update(walk.snapshot())
        except Exception as exc:  # noqa: BLE001
            report["snapshot"] = f"unavailable: {type(exc).__name__}: {exc}"
    return report


# ── the walk ─────────────────────────────────────────────────────────────────


class Walk:
    """One walk through the app: the window, the steps and what they found.

    ``root`` is the isolated folder the settings, the documents and the results live in;
    ``screenshots`` a folder for one picture per window state (left empty under Bazel);
    ``out`` where the watchdog writes the result if the walk hangs (``None``: it only exits);
    ``watchdog`` the one ``main`` armed before the app services, or ``None`` for one of its own;
    ``proofreading`` the server the run proofreads with, or ``None`` for a walk without one.
    """

    def __init__(
        self,
        *,
        root: Path,
        service: AppServices,
        screenshots: Path | None = None,
        out: Path | None = None,
        watchdog: Watchdog | None = None,
        proofreading: ProofreadingSetup | None = None,
    ) -> None:
        self._root = root
        self._service = service
        self._screenshots = screenshots
        self._proofreading = proofreading
        self._watchdog = watchdog if watchdog is not None else Watchdog(out)
        self._current = "before the first step"
        self._app: QApplication | None = None
        self._window: MainWindow | None = None
        self._recorder = Recorder(on_stop=self._click_stop)
        self._answers = Answers(step=lambda: self._current)
        self._modals = Modals()
        self._exceptions: list[str] = []
        #: How many of ``_exceptions`` a guard has already reported.
        self._reported = 0
        self._warnings: list[str] = []
        #: What Qt itself said at warning level and above.
        self._qt_messages: list[str] = []
        #: What the reader made of the pages, by Word file: the walk compares it with a slack.
        #: Also what a step had to read off the machine, like Windows' long-path switch.
        self._readings: dict[str, str] = {}
        self._pictures: list[str] = []
        self._failed: list[str] = []
        self._steps: list[dict[str, Any]] = []
        self.platform = ""
        self.screen = ""
        self.versions: dict[str, str] = {}

    # ── running it ────────────────────────────────────────────────────────

    @property
    def current(self) -> str:
        """The step the walk is in, which the watchdog names when it fires."""
        return self._current

    def snapshot(self) -> dict[str, Any]:
        """Return what is running now, for the watchdog's report."""
        worker = self._recorder.worker
        return {
            "step": self._current,
            "worker_running": bool(worker is not None and worker.isRunning()),
            "run_summary_seen": self._recorder.summary is not None,
            "detached_workers_running": detached_workers_running(),
            "background_tasks": background_activity(),
            "python_threads": sorted(thread.name for thread in threading.enumerate()),
        }

    def run(self) -> dict[str, Any]:
        """Walk every step and return the result; the first failure ends the walk."""
        self._watchdog.follow(self)
        self._watchdog.start()
        try:
            return self._walk()
        finally:
            self._watchdog.stop()

    def _walk(self) -> dict[str, Any]:
        """The steps, with the watchdog armed around them."""
        try:
            # Before the guards: the modal poll's timer needs it.  The icon is the app's own.
            self._app = create_app(icon_factory=_app_icon)
        except Exception as exc:  # noqa: BLE001 - the result says what happened
            self._failed.append(f"no QApplication: {type(exc).__name__}: {exc}")
            return self.result()
        with self._guards():
            try:
                for name, method in walk_steps(proofreading=self._proofreading is not None):
                    if not self._run_step(name, getattr(self, method)):
                        break
            finally:
                try:
                    self._shut_down()
                except Exception as exc:  # noqa: BLE001 - the result is written all the same
                    self._failed.append(f"shut down: {type(exc).__name__}: {exc}")
        return self.result()

    def result(self) -> dict[str, Any]:
        """Return what the walk found, in the shape the CI job uploads."""
        return {
            **empty_result(),
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
            "qt_messages": list(self._qt_messages),
            "readings": dict(self._readings),
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

    @contextmanager
    def _guards(self) -> Iterator[None]:
        """Install the guards for the whole walk and put every one of them back."""
        previous_hook = sys.excepthook
        handler = Warnings(self._warnings)
        logging.getLogger().addHandler(handler)
        previous_qt = qInstallMessageHandler(self._on_qt_message)
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
            qInstallMessageHandler(previous_qt)
            logging.getLogger().removeHandler(handler)

    def _on_qt_message(self, kind: QtMsgType, _context: Any, message: str) -> None:
        """Keep what Qt itself complains about (a plugin, a font, a surface); it is reported only.

        Qt calls this from any thread, and a handler that raises would be a slot's exception, so
        nothing here may.
        """
        try:
            if kind not in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
                if len(self._qt_messages) < QT_MESSAGES_KEPT:
                    self._qt_messages.append(f"{kind.name}: {message}")
            if sys.stderr is not None:  # a windowed build on Windows has none
                print(message, file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass

    def _on_exception(self, kind: type[BaseException], exc: BaseException, tb: Any) -> None:
        """A slot raised: PySide6 hands it here and carries on, so the walk records it."""
        self._exceptions.append(f"{kind.__name__}: {exc}")
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

    def _unexpected(self) -> list[str]:
        """Take what no step expected: a call, a window opened, an exception in a slot."""
        new_exceptions = self._exceptions[self._reported :]
        self._reported = len(self._exceptions)
        return [
            *self._answers.take_unexpected(),
            *(f"a window opened: {name}" for name in self._modals.take_unexpected()),
            *(f"an exception in a slot: {line}" for line in new_exceptions),
        ]

    def _check_guards(self) -> None:
        """Fail the step on what no step expected: a call, a window, a slot's exception."""
        found = [
            *self._unexpected(),
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
        wait_for_background_tasks(BACKGROUND_MS)

    # ── what every step uses ──────────────────────────────────────────────

    @property
    def _app_or_fail(self) -> QApplication:
        """Return the application object; a step before the first one has none."""
        app = self._app
        if app is None:
            raise _Failed("no QApplication was built")
        return app

    @staticmethod
    def _pump() -> None:
        """Run the app's events for a moment, deferred deletes included.

        ``deleteLater`` is how the app lets go of a dialog or a round of downloads, and
        ``processEvents`` alone never delivers it: the app's own event loop does, so this does too.
        """
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.01)

    def _settle(self, condition: Callable[[], bool], what: str, deadline_s: float) -> None:
        """Pump the event loop until *condition* holds; raise :class:`_Failed` when it does not."""
        deadline = time.monotonic() + deadline_s
        raised: RuntimeError | None = None
        while True:
            self._pump()
            try:
                if condition():
                    return
                raised = None
            except RuntimeError as exc:
                raised = exc  # a widget that went away: the condition does not hold yet
            if time.monotonic() > deadline:
                # What no step expected is why the wait never ends, and a slot that raised says
                # it too: the message carries them, not only the wait.
                notes = [
                    *self._unexpected(),
                    *(
                        [f"the last check raised {type(raised).__name__}: {raised}"]
                        if raised
                        else []
                    ),
                ]
                raise _Failed(
                    f"{what} did not happen within {deadline_s:g} s"
                    + (f" ({'; '.join(notes)})" if notes else "")
                )

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

    def _check_tab(self) -> int:
        """Return the index of the Check tab, found by its page and not by its place."""
        index = self._tabs().indexOf(self._child(QWidget, _handles.CHECK_PAGE))
        if index < 0:
            raise _Failed("the Check page is not one of the window's tabs")
        return index

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

    def _choose_folder(self, button: str, field: str, folder: Path, *, title: str = "") -> None:
        """Choose *folder* with the picker behind *button*, and wait for *field* to show it."""
        self._answers.expect("pick_folder", title=title, reply=folder)
        self._button(button).click()
        self._settle(
            lambda: Path(self._field(field).text()) == folder,
            f"{folder} to be shown in {field}",
            UI_S,
        )

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

    def _run_idle(self) -> bool:
        """Whether the window is ready for the next run: Run enabled and no worker running.

        The summary reaches the recorder and the window as two queued events, so a wait that
        stopped at the summary could click Run, or start a stage, before the window had finished
        the last run.
        """
        worker = self._recorder.worker
        try:
            running = worker is not None and worker.isRunning()
        except RuntimeError:  # the worker is gone: nothing runs
            running = False
        return not running and self._button(_handles.RUN_BUTTON).isEnabled()

    def _wait_run(self, what: str) -> RunSummary:
        """Wait for the run to end and the window to take it in; return the run's summary."""
        self._settle(lambda: self._recorder.summary is not None, f"{what} to end", RUN_S)
        self._settle(self._run_idle, f"the window to be ready again after {what}", UI_S)
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

    def _in_modal(
        self, trigger: Callable[[], object], inside: Callable[[Any], None], *, popup: bool = False
    ) -> None:
        """Run *inside* on the window (or, with *popup*, the menu) *trigger* opens, then close it.

        The callback runs inside ``exec()``; PySide6 would swallow what it raises, so the
        exception is carried out and raised after *trigger* returned.  The guard is told the
        window is expected before *trigger* runs, not when the callback does: its timer may be due
        the moment the window opens.
        """
        problems: list[BaseException] = []
        opened: list[bool] = []

        def handle() -> None:
            opened.append(True)
            modal = QApplication.activePopupWidget() if popup else QApplication.activeModalWidget()
            if modal is None:
                problems.append(_Failed("no window opened"))
                return
            try:
                if not popup and not isinstance(modal, QDialog):  # pragma: no cover - app type
                    problems.append(_Failed(f"not a dialog but {type(modal).__name__}"))
                    return
                inside(modal)
            except BaseException as exc:  # noqa: BLE001 - carried out of the slot
                problems.append(exc)
            finally:
                modal.close()

        starter = QTimer()
        starter.setSingleShot(True)
        starter.timeout.connect(handle)
        starter.start(0)
        self._modals.expected += 1
        try:
            trigger()
        finally:
            self._modals.expected -= 1
            starter.stop()  # a trigger that opened nothing must not leave *handle* for later
        if problems:
            raise problems[0]
        if not opened:
            raise _Failed("no window opened")

    def _rerun_recognition(self, only: str | None = None) -> None:
        """Choose "Re-run Text Recognition" in the queue's right-click menu.

        With *only* that document's row is selected, otherwise every row; the menu opens on a
        context-menu event sent to the table's viewport, and the entry is chosen with the keys.
        """
        table = self._table()
        if only is None:
            table.selectAll()
        else:
            table.select_source(only)
        wanted = only if only is not None else table.source_id_for_row(0)
        row = next(
            (
                index
                for index in range(table.rowCount())
                if table.source_id_for_row(index) == wanted
            ),
            None,
        )
        if row is None:
            raise _Failed(f"the queue has no row for {Path(wanted).name}")
        viewport = table.viewport()
        position = table.visualRect(table.model().index(row, 0)).center()
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, position, viewport.mapToGlobal(position)
        )

        def choose(menu: Any) -> None:
            if not isinstance(menu, QMenu):
                raise _Failed(f"the right-click opened a {type(menu).__name__}, not a menu")
            entry = next(
                (one for one in menu.actions() if one.text() == win.MENU_RERUN_RECOGNITION), None
            )
            if entry is None:
                raise _Failed(
                    f"the queue's menu has no {win.MENU_RERUN_RECOGNITION!r}: "
                    f"{[one.text() for one in menu.actions()]}"
                )
            menu.setActiveAction(entry)
            QApplication.sendEvent(
                menu,
                QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
            )

        self._in_modal(lambda: QApplication.sendEvent(viewport, event), choose, popup=True)

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

    def _assert_docx(self, path: Path, words: Sequence[str], *, read: bool = False) -> None:
        """Fail the step unless the Word file at *path* holds every one of *words*.

        *read* says the words were on a page the reader had to read: they are compared with the
        reader's slack (:func:`_reads`) and what it made of them goes into the result.
        """
        if not path.is_file():
            raise _Failed(f"no Word file at {path}")
        found = docx_text(path)
        if read:
            self._readings[path.name] = found
            missing = [word for word in words if not _reads(found, word)]
        else:
            missing = [word for word in words if word not in found]
        if missing:
            raise _Failed(f"{path.name} does not hold {', '.join(missing)}: {found[:200]!r}")

    def _typed_view(self) -> QTextEdit:
        """Return the text the teacher types into, and fail the step while it is read-only.

        Once a text is proofread the read view is the student's text and stays as it is: the
        teacher types in the corrected one.
        """
        name = _handles.FINAL_VIEW if self._proofreading is not None else _handles.READ_VIEW
        view = self._child(QTextEdit, name)
        if self._proofreading is not None:
            # A proofread document opens on its marked text: the teacher switches first.
            self._button(_handles.FINAL_VIEW_BUTTON).click()
            self._settle(view.isVisible, "the corrected text to be in front", UI_S)
        if view.isReadOnly():
            raise _Failed(f"{name} is read-only, so nothing can be typed into it")
        return view

    def _stored(self, source_id: str) -> Any:
        """Return what the store holds about *source_id*, or fail the step naming it."""
        document = self._service.documents.check_document(self._config(), Path(source_id))
        if document is None:
            raise _Failed(f"the store has no document for {Path(source_id).name}")
        return document

    def _checked_text(self, source_id: str) -> str:
        """Return the stored text the walk's typed word goes into: the corrected one if proofread."""
        document = self._stored(source_id)
        return document.corrected_text if self._proofreading is not None else document.raw_text

    def _run_words(self, *words: str) -> tuple[str, ...]:
        """Return *words*, the pieces of one document's text, as a run's Word file holds them.

        With a proofreading the model's word stands in place of the document's last word.
        """
        if self._proofreading is None:
            return words
        *kept, last = words
        head = " ".join(last.split()[:-1])
        return (*kept, *([head] if head else []), self._proofreading.word)

    def _assert_proofread(self) -> None:
        """Check what a proofreading run left: every document sent, the model's word stored."""
        setup = self._proofreading
        if setup is None:
            return
        for source_id in self._table().queued_source_ids():
            stages = self._recorder.stages.get(source_id, [])
            if ProgressStage.CORRECTION not in stages:
                raise _Failed(f"{Path(source_id).name} was not proofread: {stages}")
        stored = self._stored(self._source(PNG_NAME)).corrected_plain
        if setup.word not in stored:
            raise _Failed(f"the stored text of {PNG_NAME} has no {setup.word!r}: {stored[:200]!r}")

    def _stored_transcription(self, source_id: str) -> bool:
        """Whether the store has the walk's typed word in the text the teacher typed into."""
        return TYPED_WORD in self._checked_text(source_id)

    def _transcription_worked_on(self, source_id: str) -> bool:
        """Whether the store says the teacher worked on *source_id*'s text."""
        worked = self._service.documents.worked_on_documents(
            self._config(), [Path(source_id)], run_proofreads=self._proofreading is not None
        )
        names = worked.worked_on if self._proofreading is not None else worked.transcribed
        return Path(source_id) in names

    def _items(self) -> QListWidget:
        """Return the list of the proofreading's items."""
        return self._child(QListWidget, _handles.ITEM_LIST)

    # ── the steps ─────────────────────────────────────────────────────────

    def _step_start(self) -> None:
        """Build the real window and show it; nothing is checked yet, nothing is queued."""
        self._window = build_main_window(
            app_service=self._service, worker_factory=self._recorder.build
        )
        self._window.show()
        self._settle(lambda: self._window.isVisible(), "the window to be shown", UI_S)
        if self._tabs().isTabEnabled(self._check_tab()):
            raise _Failed("the Check tab is enabled before a document was read")
        if self._button(_handles.EXPORT_BUTTON).isEnabled():
            raise _Failed("Export is enabled before there is a result")
        # Under Bazel the assets folder is not next to the code; a bundle carries it.
        if getattr(sys, "frozen", False) and QApplication.windowIcon().isNull():
            raise _Failed("the app has no window icon: the bundle lacks its assets")
        screen = QApplication.primaryScreen()
        self.platform = QApplication.platformName()
        self.screen = "" if screen is None else f"{screen.size().width()}x{screen.size().height()}"
        self.versions = {"app": __version__, "qt": qVersion()}

    def _step_choose_folder(self) -> None:
        """Write the documents, then choose their folder the way a teacher does."""
        write_inputs(self._inputs())
        self._choose_folder(
            _handles.INPUT_FOLDER_BUTTON,
            _handles.INPUT_PATH,
            self._inputs(),
            title=dlg.select_input_folder().title,
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
        if ProgressStage.OCR in self._recorder.stages.get(text, []):
            raise _Failed(f"the text file was read: {self._recorder.stages[text]}")
        for source_id in table.queued_source_ids():
            if not table.has_text(source_id):
                raise _Failed(f"{Path(source_id).name} has no text after the run")
        self._assert_docx(self._run_docx(PNG_NAME), self._run_words(PNG_WORDS), read=True)
        self._assert_docx(self._run_docx(PDF_NAME), self._run_words(*PDF_PAGES), read=True)
        self._assert_docx(self._run_docx(TXT_NAME), self._run_words(TXT_TEXT))
        self._assert_proofread()
        self._settle(
            lambda: self._button(_handles.EXPORT_BUTTON).isEnabled(), "Export to be enabled", UI_S
        )
        if not self._tabs().isTabEnabled(self._check_tab()):
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
        self._settle(
            lambda: self._tabs().currentIndex() == self._check_tab(), "the Check tab", UI_S
        )
        document = self._service.documents.check_document(self._config(), Path(image))
        if document is None:
            raise _Failed("the store has no document for the image")
        view = self._child(QTextEdit, _handles.READ_VIEW)
        self._settle(
            lambda: view.toPlainText().rstrip("\n") == document.raw_plain.rstrip("\n"),
            "the transcription in the Check tab",
            UI_S,
        )
        if not _reads(view.toPlainText(), PNG_WORDS):
            raise _Failed(f"the Check tab does not show {PNG_WORDS!r}")
        if self._table().selected_source_ids() != [image]:
            raise _Failed(f"the selection is {self._table().selected_source_ids()}")
        self._screenshot("check")

    def _step_correct(self) -> None:
        """Type into the transcription and wait for the store to carry it."""
        image = self._source(PNG_NAME)
        view = self._typed_view()
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

    def _step_proofreading_items(self) -> None:
        """Read the Check tab's list: one item per thing the model changed, with its reason."""
        setup = self._proofreading
        if setup is None:  # pragma: no cover - the step only runs with one
            raise _Failed("the walk runs no proofreading")
        image = self._source(PNG_NAME)
        document = self._stored(image)
        finished = self._child(QTextEdit, _handles.FINAL_VIEW)
        self._settle(
            lambda: finished.toPlainText().rstrip("\n") == document.corrected_plain.rstrip("\n"),
            "the corrected text in the Check tab",
            UI_S,
        )
        if setup.word not in finished.toPlainText():
            raise _Failed(f"the Check tab does not show {setup.word!r}")
        explained = [item for item in document.items if item.kind is ItemKind.CORRECTION]
        if len(explained) != 1:
            raise _Failed(
                f"the store has {len(explained)} explained corrections for {PNG_NAME}: "
                f"{[(item.kind.value, item.key) for item in document.items]}"
            )
        item = explained[0]
        if (item.category, item.explanation) != (setup.category, setup.explanation):
            raise _Failed(f"the item says {item.category}/{item.explanation!r}")
        listed = self._items()
        self._settle(
            lambda: listed.count() == len(document.items),
            f"the {len(document.items)} items of {PNG_NAME} in the list",
            UI_S,
        )

    def _step_proofreading_checked(self) -> None:
        """Look at the first item and tick the corrections as checked; the store must say both."""
        image = self._source(PNG_NAME)
        document = self._stored(image)
        if not document.items:
            raise _Failed(f"the store has no item for {PNG_NAME}")
        key = document.items[0].key
        listed = self._items()
        self._settle(
            lambda: listed.count() == len(document.items),
            f"the {len(document.items)} items of {PNG_NAME} in the list",
            UI_S,
        )
        listed.setCurrentRow(0)
        self._settle(
            lambda: key in self._stored(image).work.looked_at,
            f"the store to know item {key} was looked at",
            UI_S,
        )
        self._button(_handles.CHECKED_BUTTON).click()
        self._settle(
            lambda: self._stored(image).work.corrections_checked,
            "the store to know the corrections were checked",
            UI_S,
        )

    def _step_review_dropped(self) -> None:
        """Spoil a stored review, open the document again, and watch the app drop the review.

        A review whose offsets no longer point at the words it quotes must never be shown: the
        explanations would sit on other words than the ones the model changed.  The app drops it
        and says so in the log, and the changes stay as corrections without an explanation.
        """
        pdf = self._source(PDF_NAME)
        config = self._config()
        store = state_store_for(config, repository=StateRepository)
        state = store.load(Path(pdf))
        review = None if state is None else CorrectionReview.from_dict(state.correction_review)
        if state is None or review is None or not review.edits:
            raise _Failed(f"the store holds no review for {PDF_NAME}")
        if any(not note.parts for note in review.edits):
            raise _Failed(f"the review of {PDF_NAME} quotes no words to move")
        # The app says so once per document, on the first read: the mark comes before it.
        before = len(self._warnings)
        store.save(
            Path(pdf),
            dataclasses.replace(state, correction_review=_shifted(review).to_dict()),
        )
        if self._stored(pdf).review is not None:
            raise _Failed("the app kept a review that no longer fits its text")
        buttons = self._window.findChildren(QAbstractButton, _handles.OPEN_DOCUMENT)
        opener = next(
            (button for button in buttons if button.property(_handles.SOURCE_ID_PROPERTY) == pdf),
            None,
        )
        if opener is None:
            raise _Failed(f"the row of {PDF_NAME} has no Open button")
        opener.click()
        self._settle(
            lambda: self._items().count() == len(self._stored(pdf).items),
            f"the items of {PDF_NAME} in the list",
            UI_S,
        )
        kinds = {item.kind for item in self._stored(pdf).items}
        if kinds != {ItemKind.UNEXPLAINED}:
            raise _Failed(f"the changes of {PDF_NAME} are {sorted(kind.value for kind in kinds)}")
        said = [line for line in self._warnings[before:] if "Dropped the review" in line]
        if not any(PDF_NAME in line for line in said):
            raise _Failed(f"no warning about the dropped review of {PDF_NAME}: {said}")
        self._screenshot("dropped-review")

    def _docx_export_action(self) -> QAction:
        """Return the Word entry of the Export button's menu, or fail the step naming it."""
        menu = self._button(_handles.EXPORT_BUTTON).menu()
        if menu is None:
            raise _Failed("the Export button has no menu")
        action = next(
            (entry for entry in menu.actions() if entry.text() == ExportKind.DOCX.label), None
        )
        if action is None:
            raise _Failed(f"the Export menu has no {ExportKind.DOCX.label!r} entry")
        return action

    def _step_export(self) -> None:
        """Export the open document as a Word file, the way a teacher saves one."""
        self._tabs().setCurrentIndex(0)
        image = self._source(PNG_NAME)
        self._table().select_source(image)
        target = self._root / EXPORT_FOLDER / (Path(PNG_NAME).stem + DOCX_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        # The app proposes the folder of the last run, which a step before this one may have
        # left somewhere other than the results folder.
        last_run = self._table().get_output_dir() or self._output()
        proposal = last_run / (Path(PNG_NAME).stem + DOCX_SUFFIX)
        self._answers.expect(
            "save_file",
            title=dlg.save_export(
                ExportKind.DOCX.label, ExportKind.DOCX.file_filter, str(proposal)
            ).title,
            start=proposal,
            reply=target,
        )
        self._docx_export_action().trigger()
        self._settle(lambda: target.is_file(), "the exported Word file", UI_S)
        self._assert_docx(target, (TYPED_WORD,))
        run_file = self._run_docx(PNG_NAME)
        if TYPED_WORD in docx_text(run_file):
            raise _Failed(f"the export changed the run's own {run_file.name}")
        self._screenshot("export")

    def _step_export_held_open(self) -> None:
        """Export a whole queue over a file Word holds open: one notice, and the rest written.

        A word processor keeps the document it has open to itself, so the app cannot replace it.
        What a teacher gets is one notice naming that file; the other documents are written, the
        run does not end, and the file that could not be replaced is left as it was.
        """
        self._tabs().setCurrentIndex(0)
        table = self._table()
        table.selectAll()
        folder = self._root / EXPORT_FOLDER / LOCKED_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        locked = folder / (Path(PNG_NAME).stem + DOCX_SUFFIX)
        others = [folder / (Path(name).stem + DOCX_SUFFIX) for name in (PDF_NAME, TXT_NAME)]
        self._answers.expect(
            "pick_folder",
            title=dlg.save_export_to_folder(ExportKind.DOCX.label).title,
            reply=folder,
        )
        # The file is there already — Word has it open — so the teacher is asked to replace it.
        self._answers.expect(
            "choose",
            title=dlg.replace_existing_files(1, locked.name).title,
            mention=(locked.name,),
            choice=StandardButton.YES,
        )
        self._answers.expect("notify", mention=(PNG_NAME,))
        with _held_open(locked):
            self._docx_export_action().trigger()
            self._settle(
                lambda: all(path.is_file() for path in others),
                f"the Word files of {', '.join(path.name for path in others)}",
                UI_S,
            )
            if len(docx_text(others[0])) == 0:
                raise _Failed(f"the exported {others[0].name} is empty")
        if locked.stat().st_size:
            raise _Failed(f"the export wrote {locked.name} although Word had it open")
        self._screenshot("export-held-open")

    def _step_long_output_path(self) -> None:
        """Run into a results folder whose path is over 260 characters: the Word file still lands.

        A results folder holds the stored results, so this folder is a new one and the run fills
        it from the start — which is why the step comes before the teacher works on anything.

        Windows is where the limit is, and where the machine's own switch decides whether a path
        this long reaches the file system at all: the app adds no prefix of its own, so the step
        records what the switch says and fails here rather than report a run the machine made
        possible.  Linux has no such limit and the folder is only long, which is why the step runs
        there too: the same work catches a mistake in the step itself.
        """
        if sys.platform == "win32":
            enabled = _long_paths_enabled()
            self._readings["windows_long_paths"] = "on" if enabled else "off"
            if not enabled:
                raise _Failed(
                    "this Windows has long paths off (LongPathsEnabled under "
                    r"SYSTEM\CurrentControlSet\Control\FileSystem), so a folder this deep "
                    "cannot say whether the app handles one"
                )
        folder = _deep_folder(self._output())
        self._choose_folder(
            _handles.OUTPUT_FOLDER_BUTTON,
            _handles.OUTPUT_PATH,
            folder,
            title=dlg.select_output_folder().title,
        )
        try:
            image = self._source(PNG_NAME)
            self._table().select_source(image)
            self._recorder.begin()
            self._click_run()
            summary = self._wait_run("the run into the long folder")
            if summary.status is not RunStatus.DONE or self._names(summary.succeeded) != [PNG_NAME]:
                raise _Failed(f"the run ended {summary.status}: {list(summary.succeeded)}")
            written = output_path(self._inputs() / PNG_NAME, self._inputs(), folder, DOCX_SUFFIX)
            self._assert_docx(written, self._run_words(PNG_WORDS), read=True)
            self._screenshot("long-output-path")
        finally:
            # Whatever happened, the steps after this one run into the folder they expect.
            self._choose_folder(
                _handles.OUTPUT_FOLDER_BUTTON,
                _handles.OUTPUT_PATH,
                self._output(),
                title=dlg.select_output_folder().title,
            )

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
        self._rerun_recognition()
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
            raise _Failed(
                f"{Path(order[2]).name} was read although the run stopped before it "
                f"(its row says {row.ocr if row is not None else 'no row'})"
            )

    def _step_stop_now(self) -> None:
        """Stop a run at once, and check that the file it cut short left nothing behind."""
        table = self._table()
        pdf = self._source(PDF_NAME)
        self._recorder.begin()
        self._recorder.arm_stop_point(
            "stage_started",
            when=lambda args: args[0] == pdf and str(args[2]) == ProgressStage.PREPROCESSING,
            clicks=2,
        )
        self._rerun_recognition(pdf)
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
        if not wait_for_background_tasks(BACKGROUND_MS):
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

    def _step_case_spelling(self) -> None:
        """Reach the same documents through another spelling of their folder: one cache entry each.

        A file system that ignores case hands out one file however its folder is spelled; the app
        must see one document too, or a second run would read every page again and keep a second
        answer beside the first.  Only such a file system makes this worth walking, which is why
        the step runs on macOS and Windows.
        """
        original = self._inputs()
        other = self._root / INPUT_FOLDER.upper()
        store = state_store_for(self._config(), repository=StateRepository)
        for name in (PNG_NAME, PDF_NAME, TXT_NAME):
            if store.state_path_for_input(other / name) != store.state_path_for_input(
                original / name
            ):
                raise _Failed(f"the two spellings of the folder give two cache entries for {name}")
        self._choose_folder(
            _handles.INPUT_FOLDER_BUTTON,
            _handles.INPUT_PATH,
            other,
            title=dlg.select_input_folder().title,
        )
        try:
            table = self._table()
            self._settle(
                lambda: len(table.queued_source_ids()) == 3,
                "the queue of the other spelling",
                UI_S,
            )
            for source_id in table.queued_source_ids():
                if not table.has_text(source_id):
                    raise _Failed(
                        f"{Path(source_id).name} lost its text through the other spelling"
                    )
            table.selectAll()
            self._recorder.begin()
            self._click_run()
            summary = self._wait_run("the run through the other spelling")
            if summary.status is not RunStatus.DONE or len(summary.succeeded) != 3:
                raise _Failed(f"the run ended {summary.status}: {list(summary.succeeded)}")
            if self._recorder.stages:
                raise _Failed(f"a step ran again: {self._recorder.stages}")
        finally:
            # Whatever happened, the steps after this one read the folder they expect.
            self._choose_folder(
                _handles.INPUT_FOLDER_BUTTON,
                _handles.INPUT_PATH,
                original,
                title=dlg.select_input_folder().title,
            )

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
        if not wait_for_background_tasks(BACKGROUND_MS):
            raise _Failed("a background task is still running after the window closed")
        if detached_workers_running():
            raise _Failed("a worker is still running after the window closed")
        self._check_guards()


# ── the entry point ──────────────────────────────────────────────────────────


def empty_result() -> dict[str, Any]:
    """A result with no step in it yet: the file's shape, and ``"ok": false``."""
    return {
        "ok": False,
        "platform": "",
        "screen": "",
        "versions": {},
        "failed": [],
        "steps": [],
        "dialogs": [],
        "modals": [],
        "exceptions": [],
        "warnings": [],
        "qt_messages": [],
        "readings": {},
        "hang": {},
        "screenshots": [],
    }


def walk_main_window(
    root: Path,
    service: AppServices,
    *,
    screenshots: Path | None = None,
    out: Path | None = None,
    watchdog: Watchdog | None = None,
    proofreading: ProofreadingSetup | None = None,
) -> dict[str, Any]:
    """Play teacher in a real main window on *root* and return the result.

    With *proofreading* the run proofreads against that server, and the walk reads the items it
    left, ticks one as checked, and spoils a stored review to see it dropped.
    """
    return Walk(
        root=root,
        service=service,
        screenshots=screenshots,
        out=out,
        watchdog=watchdog,
        proofreading=proofreading,
    ).run()


def write_result(path: Path, result: dict[str, Any]) -> None:
    """Write *result* to *path* as JSON, creating the folder if it is new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str], *, proofreading: ProofreadingSetup | None = None) -> int:
    """Walk the app in this process and write the result; 0 when every step passed."""
    parser = argparse.ArgumentParser(
        prog="walkthrough", description="Play teacher in the opened app and report what it did."
    )
    parser.add_argument("out", help="the file the result goes into")
    parser.add_argument(
        "--screenshots", default="", help="a folder for one picture per window state"
    )
    parser.add_argument(
        "--root", default="", help="the folder to run in, instead of a temporary one"
    )
    parser.add_argument("--keep", action="store_true", help="leave the folder as the walk left it")
    options = parser.parse_args(argv)
    out = Path(options.out).expanduser().resolve()
    screenshots = Path(options.screenshots).expanduser().resolve() if options.screenshots else None
    root_given = Path(options.root).expanduser().resolve() if options.root else None
    # The pictures are written while the walk runs, so their folder must not be the one the
    # walk's isolation removes at the end.
    with isolated(root_given, keep=options.keep) as root:
        write_settings(root, proofreading=proofreading)
        # Armed before the services are built: a hang there leaves a file to read instead of a
        # job that runs to its limit.
        watchdog = Watchdog(out)
        watchdog.start()
        try:
            result = walk_main_window(
                root,
                build_app_services(),
                screenshots=screenshots,
                out=out,
                watchdog=watchdog,
                proofreading=proofreading,
            )
        finally:
            watchdog.stop()
    write_result(out, result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
