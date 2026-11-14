"""The speed test and the time estimates, on the *This computer* page.

A button, one row per step that runs on Ollama, the example of a class set, and the lines that
qualify the figures.  The speed of a computer is a fact about the computer, so the test lives
with the other facts about it (its power, its graphics card, its server); the *AI models* page,
where the models are chosen, shows the result read-only (:class:`SpeedHint`).

Three rules shape it:

* **The test runs only when the teacher asks for it.**  It occupies the graphics card for up to
  a minute and a half per step, possibly on a server other people use, so it never starts by
  itself — not at start, not when a model is chosen.
* **It tests the models chosen on AI models right now**, unsaved choices included (the window
  hands the panel those settings), so a teacher can compare two models before pressing OK.
* **Nothing here waits for the network.**  The figures come from the stored measurements
  (``cached_only``); the server's version and model digests, which decide whether a measurement
  is still current, are fetched in the background and only then redraw the block.

While no step uses Ollama the block says so instead: text recognition with the built-in
reader has no AI model to measure, and a step on an online service is not measured at all.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...application.speed_estimates import CLASS_SET_FILES, SpeedChoices, SpeedSummary
from ...domain.model_catalog import HardwareDetection
from ...domain.performance_estimate import SpeedStep, StepOutcome
from ...exceptions import RunStoppedError
from ...infrastructure.performance_probe import ProbeResult
from .. import _user_text as txt
from .._background import LatestTask
from .._hardware_profile_selector import HardwareProfileSelector

#: Left margin of the step rows, so they read as part of the heading above them.
_ROW_INDENT = 12
#: Width of the step-name column, so the model names line up.
_NAME_WIDTH = 110
#: Width of the model column, so the figures line up.
_MODEL_WIDTH = 190
_HINT_STYLE = "color: #888; font-size: 11px;"
_WARNING_STYLE = "color: #e67e22; font-size: 11px;"


class _SpeedService(Protocol):
    """What the panel asks the application service; the facade methods are the only ones."""

    def speed_choices(self, settings: Mapping[str, object]) -> SpeedChoices: ...

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cached_only: bool = True,
    ) -> SpeedSummary: ...

    def test_speed(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProbeResult: ...

    def refresh_speed_server_facts(self, ollama_url: str) -> None: ...


class _StepRow(QWidget):
    """One line of the block: the step, its model, the time per page and where it ran."""

    def __init__(self, step: SpeedStep, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(_ROW_INDENT, 0, 0, 0)
        self.name = QLabel(txt.speed_step_name(step))
        self.name.setMinimumWidth(_NAME_WIDTH)
        self.model = QLabel("")
        self.model.setMinimumWidth(_MODEL_WIDTH)
        self.per_page = QLabel("")
        self.note = QLabel("")
        self.note.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self.name)
        layout.addWidget(self.model)
        layout.addWidget(self.per_page, stretch=1)
        layout.addWidget(self.note)


class SpeedPanel(QWidget):
    """The speed test and the time estimates for the models chosen on *AI models*."""

    #: A test finished and stored its measurement (the figures elsewhere are out of date).
    measured = Signal()

    def __init__(
        self,
        *,
        app_service: _SpeedService,
        settings_provider: Callable[[], Mapping[str, object]],
        profile_selector: HardwareProfileSelector,
        ollama_url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        """Create the block.

        Args:
            app_service: The application service; only its speed facade methods are used.
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
        self._facts_task = LatestTask(self)
        #: The address whose server facts were asked for, so they are fetched once per address.
        self._facts_url = ""

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
        """Fill the body: the button, the step rows, the class set and the lines of text."""
        header = QHBoxLayout()
        header.addStretch(1)
        self._button = QPushButton(txt.SPEED_TEST)
        self._button.setToolTip(txt.SPEED_TEST_TOOLTIP)
        self._button.clicked.connect(self._on_button)
        header.addWidget(self._button)
        layout.addLayout(header)

        self._status = QLabel(txt.SPEED_TESTING)
        self._status.setStyleSheet(_HINT_STYLE)
        self._status.setVisible(False)
        layout.addWidget(self._status)

        self._rows: dict[SpeedStep, _StepRow] = {}
        for step in SpeedStep:
            row = _StepRow(step, self)
            row.setVisible(False)
            self._rows[step] = row
            layout.addWidget(row)

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
        self._facts_url = ""  # another server has other facts
        self.refresh()

    def refresh(self) -> None:
        """Redraw from the stored measurements; never contacts the server.

        Says "nothing to measure" while no step runs on Ollama.  The server facts are asked for in
        the background the first time an address is seen, and the block is redrawn when they
        arrive.
        """
        choices = self._choices()
        has_models = bool(choices.models())
        self._body.setVisible(has_models)
        self._nothing.setVisible(not has_models)
        if not has_models:
            return
        self._fetch_facts()
        self._draw(self._service.speed_summary(choices, hardware=self._hardware()))

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
            lambda: self._service.test_speed(choices, hardware=hardware, cancel_event=cancel),
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
        self._status.setVisible(running)

    # ── server facts ───────────────────────────────────────────────────────

    def _fetch_facts(self) -> None:
        """Ask for the server's version and digests once per address, in the background.

        They are what makes a stored measurement out of date, and fetching them needs the
        network, so the block is drawn from the cache first and redrawn when they arrive.
        """
        url = self._ollama_url
        if not url or url == self._facts_url:
            return
        self._facts_url = url
        self._facts_task.submit(
            lambda: self._service.refresh_speed_server_facts(url),
            lambda _result, _error: self.refresh(),
        )

    # ── drawing ────────────────────────────────────────────────────────────

    def _draw(self, summary: SpeedSummary) -> None:
        for step, row in self._rows.items():
            estimate = summary.step(step)
            row.setVisible(estimate is not None)
            if estimate is None:
                continue
            row.model.setText(estimate.model)
            if estimate.outcome is StepOutcome.TOO_SLOW:
                row.per_page.setText(txt.SPEED_TOO_SLOW)
                row.per_page.setToolTip(estimate.message)
            else:
                row.per_page.setText(
                    txt.speed_per_page(estimate.per_page.low_s, estimate.per_page.high_s)
                )
                row.per_page.setToolTip("")
            row.note.setText(self._card_note(estimate.on_card))

        if summary.class_set is None:
            self._class_set.setVisible(False)
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
        return self._service.speed_choices(self._settings_provider())

    def _hardware(self) -> HardwareDetection | None:
        """Return this computer as the page resolved it, or ``None`` while it is unknown."""
        return self._selector.resolved()


def _card_text(detection: HardwareDetection | None) -> str:
    """Return the card as a teacher would name it: ``"RTX 3070 (8 GB)"``.

    The size is what the card *has*, not the share models may use: the warning is about a card
    that is there and was not used, so the figure has to match what the teacher was told.
    """
    if detection is None or not detection.gpu_name:
        return ""
    memory = detection.memory_gb
    return f"{detection.gpu_name} ({memory:.0f} GB)" if memory else detection.gpu_name
