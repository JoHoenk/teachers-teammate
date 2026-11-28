"""The speed test and the time estimates, on the *This computer* page.

The note and the button, one row per step that runs on Ollama, the example of a class set, and
the lines that qualify the figures.  The speed of a computer is a fact about the computer, so
the test lives with the other facts about it (its power, its graphics card, its server); the
*AI models* page, where the models are chosen, shows the class-set time read-only
(:class:`~._computer_box.SpeedLine`).

Three rules shape it:

* **The test runs only when the teacher asks for it.**  It occupies the graphics card for up to
  a minute and a half per step, possibly on a server other people use, so it never starts by
  itself — not at start, not when a model is chosen.
* **It tests the models chosen on AI models right now**, unsaved choices included (the window
  hands the panel those settings), so a teacher can compare two models before pressing OK.
* **Nothing here waits for the network.**  The figures come from the stored measurements and
  from what the server last answered; its version and model digests, which decide whether a
  measurement is still current, are asked for in the background and redraw the block when they
  arrive.

A step that runs here but was never measured keeps its row and says so: dropping it would make
the class set look shorter than it is.  While no step uses Ollama the block says so instead:
text recognition with the built-in reader has no AI model to measure, and a step on an online
service is not measured at all.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...application.composition import AppServices
from ...application.speed_estimates import (
    CLASS_SET_FILES,
    SpeedChoices,
    SpeedSummary,
    StepEstimate,
    UnmeasuredStep,
)
from ...domain.hardware_profile import HardwareDetection
from ...domain.model_catalog import find_spec
from ...domain.model_tags import short_model_name
from ...domain.performance_estimate import ProbeResult, SpeedStep, StepOutcome
from ...exceptions import RunStoppedError
from ...texts import review as txt
from .._background import LatestTask
from .._hardware_profile_selector import HardwareProfileSelector

#: Left margin of the step rows, so they read as part of the heading above them.
_ROW_INDENT = 12
#: Width of the model column, so the figures line up.
_MODEL_WIDTH = 190
#: How a model name that does not fit its column is shortened.
_ELIDE = Qt.TextElideMode.ElideRight
_HINT_STYLE = "color: #888; font-size: 11px;"
_WARNING_STYLE = "color: #e67e22; font-size: 11px;"


class _StepRow:
    """The labels of one step, placed in the panel's one grid so the columns line up.

    A row of its own layout would widen only itself: a long model tag would push its own
    figures to the right and leave the other rows behind.
    """

    def __init__(self, step: SpeedStep, grid: QGridLayout, line: int) -> None:
        self.name = QLabel(txt.speed_step_name(step))
        self.model = QLabel("")
        self.model.setFixedWidth(_MODEL_WIDTH)
        self.per_page = QLabel("")
        self.memory = QLabel("")
        self.memory.setStyleSheet(_HINT_STYLE)
        self.note = QLabel("")
        self.note.setStyleSheet(_HINT_STYLE)
        self._labels = (self.name, self.model, self.per_page, self.memory, self.note)
        for column, label in enumerate(self._labels):
            grid.addWidget(label, line, column)

    def set_visible(self, visible: bool) -> None:
        """Show or hide the whole row."""
        for label in self._labels:
            label.setVisible(visible)

    def is_hidden(self) -> bool:
        """Return whether the row is hidden."""
        return self.name.isHidden()

    def set_model(self, tag: str) -> None:
        """Show *tag* as a short name, elided to the column, with the full tag in the tooltip."""
        short = short_model_name(tag)
        self.model.setText(self.model.fontMetrics().elidedText(short, _ELIDE, _MODEL_WIDTH))
        self.model.setToolTip(tag)


class SpeedPanel(QWidget):
    """The speed test and the time estimates for the models chosen on *AI models*."""

    #: A test finished and stored its measurement (the figures elsewhere are out of date).
    measured = Signal()
    #: A step of the running test starts (emitted from the test's thread, so it is queued).
    _step_started = Signal(str, str, int, int)

    def __init__(
        self,
        *,
        app_service: AppServices,
        settings_provider: Callable[[], Mapping[str, object]],
        profile_selector: HardwareProfileSelector,
        ollama_url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """Create the block.

        Args:
            app_service: The application services: the speed estimates, and the Ollama server
                whose version and digests the estimates are checked against.
            settings_provider: Returns the settings as the AI models page shows them now, so the
                test covers unsaved choices too.
            profile_selector: The page's one resolution of this computer; it decides whether a
                card was found and which one.
            ollama_url: The local AI server address as the page shows it.
        """
        super().__init__(parent)
        self._service = app_service
        self._settings_provider = settings_provider
        self._selector = profile_selector
        self._ollama_url = ollama_url
        self._running = False
        self._cancel = threading.Event()
        self._test_task = LatestTask(self)
        # The server is a remote one: a fetch that waits does not hold up the local tasks.
        self._facts_task = LatestTask(self, network=True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        # Everything but the "nothing to measure" line is the body, so it hides in one call.
        self._body = QWidget(self)
        layout = QVBoxLayout(self._body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._layout = layout
        outer.addWidget(self._body)
        self._nothing = QLabel(txt.SPEED_NOTHING_TO_MEASURE)
        self._nothing.setWordWrap(True)
        self._nothing.setStyleSheet(_HINT_STYLE)
        outer.addWidget(self._nothing)

        self._build_body(layout)
        self._selector.resolved_changed.connect(lambda _detection: self.refresh())

    def _build_body(self, layout: QVBoxLayout) -> None:
        """Fill the body: the note and the button, the step rows, the class set and the lines."""
        header = QHBoxLayout()
        self._note = QLabel(txt.SPEED_NOTE)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(_HINT_STYLE)
        header.addWidget(self._note, stretch=1)
        self._button = QPushButton(txt.SPEED_TEST)
        self._button.setToolTip(txt.SPEED_TEST_TOOLTIP)
        self._button.clicked.connect(self._on_button)
        header.addWidget(self._button)
        layout.addLayout(header)

        # What runs now, in the normal text size: a test takes minutes, and a grey hint line
        # reads as "nothing happens".  The bar has no end because the test reports steps only.
        self._status = QLabel(txt.SPEED_TESTING)
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        layout.addWidget(self._status)
        self._busy_bar = QProgressBar()
        self._busy_bar.setRange(0, 0)
        self._busy_bar.setTextVisible(False)
        self._busy_bar.setFixedHeight(10)
        self._busy_bar.setVisible(False)
        layout.addWidget(self._busy_bar)
        self._step_started.connect(self._on_step_started)

        grid = QGridLayout()
        grid.setContentsMargins(_ROW_INDENT, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setColumnStretch(2, 1)  # the per-page figure takes the room the others leave
        layout.addLayout(grid)
        self._rows: dict[SpeedStep, _StepRow] = {}
        for line, step in enumerate(SpeedStep):
            row = _StepRow(step, grid, line)
            row.set_visible(False)
            self._rows[step] = row

        self._class_set = QLabel("")
        self._class_set.setVisible(False)
        layout.addWidget(self._class_set)

        self._error = self._add_line(_WARNING_STYLE)
        self._card_line = self._add_line(_WARNING_STYLE)
        self._too_slow = self._add_line(_WARNING_STYLE)
        self._busy = self._add_line(_WARNING_STYLE)
        self._swaps = self._add_line(_WARNING_STYLE)
        self._out_of_date = self._add_line(_WARNING_STYLE)
        self._remote = self._add_line(_HINT_STYLE)
        self._date = self._add_line(_HINT_STYLE)

    def _add_line(self, style: str) -> QLabel:
        """Add a hidden line of text and return it."""
        label = QLabel("")
        label.setWordWrap(True)
        label.setStyleSheet(style)
        label.setVisible(False)
        self._layout.addWidget(label)
        return label

    # ── public API ─────────────────────────────────────────────────────────

    def set_ollama_url(self, url: str) -> None:
        """Follow the local AI server address chosen on *This computer*."""
        self._ollama_url = url
        self.refresh()

    def refresh(self) -> None:
        """Redraw from the stored measurements and ask the server about itself.

        Says "nothing to measure" while no step runs on Ollama.  The block is drawn from what the
        server last answered — usually held already, so nothing is asked — and the answer that is
        asked for in the background redraws it when it arrives.
        """
        if not self._draw_block():
            return
        self._fetch_facts()

    def stop(self) -> None:
        """Ask a running test to stop; nothing is stored.

        The panel keeps claiming to be running until the test's thread has really ended: the
        request that is waiting is abandoned, but until then the card is still busy, and a
        second test started now would measure against the first.
        """
        if not self._running or self._cancel.is_set():
            return
        self._cancel.set()
        self._button.setEnabled(False)
        self._button.setText(txt.SPEED_STOPPING)

    def shutdown(self) -> None:
        """Stop the test and drop pending results: the page is closing."""
        if self._running:
            self._cancel.set()
            self._test_task.cancel()
            self._set_running(False)
        self._facts_task.cancel()

    def is_running(self) -> bool:
        """Return whether a test is running."""
        return self._running

    # ── the test ───────────────────────────────────────────────────────────

    def _on_button(self) -> None:
        if self._running:
            self.stop()
        else:
            self._start()

    def _start(self) -> None:
        """Measure the models the page shows now, on a background thread."""
        choices = self._choices()
        if not choices.models():
            return
        self._error.setVisible(False)
        self._cancel = threading.Event()
        cancel = self._cancel
        hardware = self._hardware()
        self._set_running(True)
        self._test_task.submit(
            lambda: self._service.speeds.test_speed(
                choices,
                hardware=hardware,
                cancel_event=cancel,
                on_progress=lambda step, model, number, total: self._step_started.emit(
                    step.value, model, number, total
                ),
            ),
            self._on_test_done,
        )

    def _on_test_done(self, result: object, error: BaseException | None) -> None:
        self._set_running(False)
        if isinstance(error, RunStoppedError):
            return  # the teacher stopped it: nothing was stored, nothing to explain
        reason = str(error) if error is not None else ""
        if not reason and isinstance(result, ProbeResult):
            reason = result.error  # a server that could not be reached
        if reason:
            self._error.setText(txt.speed_test_failed(reason))
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.refresh()
        self.measured.emit()

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._button.setEnabled(True)
        self._button.setText(txt.SPEED_STOP if running else txt.SPEED_TEST)
        self._button.setToolTip(txt.SPEED_STOP_TOOLTIP if running else txt.SPEED_TEST_TOOLTIP)
        self._status.setText(txt.SPEED_TESTING)
        self._status.setVisible(running)
        self._busy_bar.setVisible(running)

    def _on_step_started(self, step: str, model: str, number: int, total: int) -> None:
        """Say which step the running test is on."""
        if self._running:
            self._status.setText(txt.speed_testing_step(step, model, number, total))

    # ── server facts ───────────────────────────────────────────────────────

    def _fetch_facts(self) -> None:
        """Ask the server for its version and digests in the background, then redraw.

        They are what makes a stored measurement out of date.  Fetching them needs the network, so
        the block is drawn from what is held first; the answer redraws it, and asking again would
        be a fetch of its own.
        """
        url = self._ollama_url
        if not url:
            return
        self._facts_task.submit(lambda: self._service.ollama.snapshot(url), self._on_facts)

    def _on_facts(self, _result: object, _error: object) -> None:
        """Redraw the block with the answer that arrived; asking again would be a fetch."""
        self._draw_block()

    def _draw_block(self) -> bool:
        """Draw what the stored measurements say; ``False`` while no step runs on Ollama."""
        choices = self._choices()
        has_models = bool(choices.models())
        self._body.setVisible(has_models)
        self._nothing.setVisible(not has_models)
        if not has_models:
            return False
        self._draw(self._service.speeds.speed_summary(choices, hardware=self._hardware()))
        return True

    # ── drawing ────────────────────────────────────────────────────────────

    def _draw(self, summary: SpeedSummary) -> None:
        for step, row in self._rows.items():
            estimate = summary.step(step)
            missing = summary.unmeasured_step(step)
            row.set_visible(estimate is not None or missing is not None)
            if estimate is not None:
                self._draw_measured(row, estimate)
            elif missing is not None:
                self._draw_unmeasured(row, missing)

        if summary.class_set is None:
            self._class_set.setText(txt.SPEED_CLASS_SET_TEST)
            self._class_set.setVisible(bool(summary.steps or summary.unmeasured))
        else:
            self._class_set.setText(
                txt.speed_class_set(
                    CLASS_SET_FILES, summary.class_set.low_s, summary.class_set.high_s
                )
            )
            self._class_set.setVisible(True)

        self._draw_warnings(summary)
        self._date.setText(
            txt.speed_from_runs(summary.sample_count, pages=summary.sample_pages)
            or txt.speed_measured_at(summary.measured_at, on_battery=summary.on_battery)
            or txt.SPEED_NEVER_MEASURED
        )
        self._date.setVisible(True)

    def _draw_measured(self, row: _StepRow, estimate: StepEstimate) -> None:
        """Draw a step that was measured here."""
        row.set_model(estimate.model)
        if estimate.outcome is StepOutcome.TOO_SLOW:
            row.per_page.setText(txt.SPEED_TOO_SLOW)
            row.per_page.setToolTip(estimate.message)
        else:
            row.per_page.setText(
                txt.speed_per_page(estimate.per_page.low_s, estimate.per_page.high_s)
            )
            row.per_page.setToolTip("")
        row.memory.setText(_memory_text(estimate.model))
        row.note.setText(self._card_note(estimate.on_card))

    def _draw_unmeasured(self, row: _StepRow, missing: UnmeasuredStep) -> None:
        """Draw a step that runs here but was never measured: the row stays, and says so."""
        row.set_model(missing.model)
        row.per_page.setText(txt.speed_unmeasured_label(missing.band))
        row.per_page.setToolTip(
            "" if missing.band is None else txt.speed_expected_note(missing.band)
        )
        row.memory.setText(_memory_text(missing.model))
        row.note.setText("")

    def _draw_warnings(self, summary: SpeedSummary) -> None:
        detection = self._hardware()
        processor_model = self._processor_model(summary)
        self._card_line.setText(
            txt.speed_card_not_used(processor_model, _card_text(detection))
            if processor_model
            else ""
        )
        self._card_line.setVisible(bool(processor_model))
        self._too_slow.setVisible(summary.too_slow)
        self._busy.setText(txt.SPEED_BUSY)
        self._busy.setVisible(summary.shared)
        self._swaps.setText(txt.speed_swaps(summary.swap_load_s))
        self._swaps.setVisible(summary.swaps)
        self._out_of_date.setText(txt.speed_out_of_date(summary.measured_at))
        self._out_of_date.setVisible(summary.out_of_date and bool(summary.measured_at))
        host = detection.host if detection is not None else None
        self._remote.setText(txt.speed_remote(host) if host else "")
        self._remote.setVisible(bool(host))

    def _card_note(self, on_card: float | None) -> str:
        """Return where the model ran, or ``""`` when this computer has no card to compare with."""
        detection = self._hardware()
        if detection is None or detection.gpu_name is None or on_card is None:
            return ""
        return txt.SPEED_ON_CARD if on_card > 0 else txt.SPEED_ON_PROCESSOR

    def _processor_model(self, summary: SpeedSummary) -> str:
        """Return the model Ollama ran on the processor although a card was found, or ``""``."""
        detection = self._hardware()
        if detection is None or detection.gpu_name is None:
            return ""
        return next((item.model for item in summary.steps if item.on_card == 0), "")

    # ── what is measured ───────────────────────────────────────────────────

    def _choices(self) -> SpeedChoices:
        """Return the models the page shows now, as the test and the estimate need them."""
        return self._service.speeds.choices(self._settings_provider())

    def _hardware(self) -> HardwareDetection | None:
        """Return this computer as the page resolved it, or ``None`` while it is unknown."""
        return self._selector.resolved()


def _memory_text(tag: str) -> str:
    """Return the memory *tag* needs, or ``""`` when the catalog does not know the model.

    Memory rather than download size: it is what decides whether the model fits on the card,
    and so how fast it runs.  After the download, the download size says nothing.
    """
    spec = find_spec(tag)
    if spec is None or spec.memory_gb is None:
        return ""
    return txt.speed_memory(spec.memory_gb)


def _card_text(detection: HardwareDetection | None) -> str:
    """Return the card as a teacher would name it: ``"RTX 3070 (8 GB)"``.

    The size is what the card *has*, not the share models may use: the warning is about a card
    that is there and was not used, so the figure has to match what the teacher was told.
    """
    if detection is None or not detection.gpu_name:
        return ""
    memory = detection.memory_gb
    return f"{detection.gpu_name} ({memory:.0f} GB)" if memory else detection.gpu_name
