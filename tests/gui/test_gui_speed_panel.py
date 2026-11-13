"""GUI tests for the speed panel on the AI models page, and for its duration wording."""
# pylint: disable=W0212  # protected-access — the tests drive the panel's widgets directly

from __future__ import annotations

from collections.abc import Mapping
import threading
from typing import cast

from PySide6.QtCore import QObject, Signal
import pytest

from teachers_teammate.application.speed_estimates import (
    SpeedChoices,
    SpeedSource,
    SpeedSummary,
    StepEstimate,
)
from teachers_teammate.domain.model_catalog import DetectionReason, HardwareDetection
from teachers_teammate.domain.performance_estimate import (
    SpeedBand,
    SpeedStep,
    StepOutcome,
    TimeRange,
)
from teachers_teammate.exceptions import RunStoppedError
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._hardware_profile_selector import HardwareProfileSelector
from teachers_teammate.gui.settings._speed_panel import SpeedPanel
from teachers_teammate.infrastructure.performance_probe import ProbeResult

_URL = "http://localhost:11434"

_GPU = HardwareDetection(
    profile="8gb",
    reason=DetectionReason.GPU_DETECTED,
    gpu_name="RTX 3070",
    memory_gb=8.0,
)


class _FakeSelector(QObject):
    """Stands in for the page's ``HardwareProfileSelector``: one resolved detection."""

    resolved_changed = Signal(object)

    def __init__(self, detection: HardwareDetection | None) -> None:
        super().__init__()
        self._detection = detection

    def resolved(self) -> HardwareDetection | None:
        return self._detection


class _FakeService:
    """Answers the panel from a fixed summary and records what it was asked.

    ``test_speed`` blocks until the test releases it, so a test can look at the panel while a
    measurement is "running".
    """

    def __init__(
        self,
        summary: SpeedSummary,
        choices: SpeedChoices,
        *,
        fail_with: BaseException | None = None,
        result: ProbeResult | None = None,
    ) -> None:
        self.result = result if result is not None else ProbeResult()
        self.summary = summary
        self.choices = choices
        self.fail_with = fail_with
        self.settings: dict[str, object] = {}
        self.tested: list[SpeedChoices] = []
        self.facts_urls: list[str] = []
        self.cancel_event: threading.Event | None = None
        self.started = threading.Event()
        self.release = threading.Event()

    def speed_choices(self, settings: Mapping[str, object]) -> SpeedChoices:
        self.settings = dict(settings)
        return self.choices

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cached_only: bool = True,
    ) -> SpeedSummary:
        _ = (choices, hardware, cached_only)
        return self.summary

    def refresh_speed_server_facts(self, ollama_url: str) -> None:
        self.facts_urls.append(ollama_url)

    def test_speed(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProbeResult:
        _ = hardware
        self.tested.append(choices)
        self.cancel_event = cancel_event
        self.started.set()
        self.release.wait(5)
        if self.fail_with is not None:
            raise self.fail_with
        return self.result


def _choices(**overrides: object) -> SpeedChoices:
    """Return the choices of a page that proofreads with one local model."""
    values: dict[str, object] = {
        "ollama_url": _URL,
        "reading_model": "",
        "writing_model": "gemma4:e4b",
        "review_model": "",
        "language": "English",
    }
    values.update(overrides)
    return SpeedChoices(**values)  # ty: ignore[invalid-argument-type]  # built from the fields above


def _step(**overrides: object) -> StepEstimate:
    """Return the proofreading row of a measurement taken on the card."""
    values: dict[str, object] = {
        "step": SpeedStep.PROOFREADING,
        "model": "gemma4:e4b",
        "per_page": TimeRange(25.0, 70.0),
        "band": SpeedBand.OK,
        "source": SpeedSource.MEASURED,
        "outcome": StepOutcome.MEASURED,
        "on_card": 1.0,
    }
    values.update(overrides)
    return StepEstimate(**values)  # ty: ignore[invalid-argument-type]  # built from the fields above


def _summary(**overrides: object) -> SpeedSummary:
    """Return a summary of one measured proofreading step and its class set."""
    values: dict[str, object] = {
        "steps": (_step(),),
        "class_set": TimeRange(5400.0, 10800.0),
        "measured_at": "2026-11-13 10:00",
        "on_battery": False,
    }
    values.update(overrides)
    return SpeedSummary(**values)  # ty: ignore[invalid-argument-type]  # built from the fields above


def _panel(
    qtbot: object,
    service: _FakeService,
    detection: HardwareDetection | None = _GPU,
) -> SpeedPanel:
    """Build the panel as the AI models page does, show it and draw it once."""
    selector = _FakeSelector(detection)
    panel = SpeedPanel(
        app_service=service,
        settings_provider=lambda: {"ollama_url": _URL},
        profile_selector=cast(HardwareProfileSelector, selector),
        ollama_url=_URL,
    )
    qtbot.addWidget(panel)
    panel.show()
    panel.refresh()
    return panel


# ── the rows ────────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_panel_shows_one_row_per_measured_step(qtbot) -> None:
    """
    Given  a stored measurement of the proofreading step
    When   the panel is drawn
    Then   its row names the model, the time per page and where the model ran, and the steps
           that were not measured stay hidden
    """
    panel = _panel(qtbot, _FakeService(_summary(), _choices()))

    row = panel._rows[SpeedStep.PROOFREADING]
    assert not row.isHidden()
    assert row.model.text() == "gemma4:e4b"
    assert row.per_page.text() == "about 25-70 s per page"
    assert row.note.text() == txt.SPEED_ON_CARD
    assert panel._rows[SpeedStep.READING].isHidden()
    assert panel._rows[SpeedStep.REVIEW].isHidden()


@pytest.mark.gui
def test_the_panel_shows_the_class_set_and_when_it_was_measured(qtbot) -> None:
    """
    Given  a measurement taken on mains power
    When   the panel is drawn
    Then   the example class set and the date line are shown
    """
    panel = _panel(qtbot, _FakeService(_summary(), _choices()))

    assert panel._class_set.text() == "30 two-page essays: about 1 h 30 min - 3 h"
    assert not panel._class_set.isHidden()
    assert panel._date.text() == "Measured 2026-11-13, on mains power."


@pytest.mark.gui
def test_the_date_line_says_the_figures_come_from_real_runs(qtbot) -> None:
    """
    Given  a summary whose figures rest on the last 12 documents of real runs
    When   the panel is drawn
    Then   the date line says so instead of naming the date of a test
    """
    summary = _summary(sample_count=12, sample_pages=False)
    panel = _panel(qtbot, _FakeService(summary, _choices()))

    assert panel._date.text() == "From your last 12 documents."


@pytest.mark.gui
def test_the_date_line_counts_pages_when_only_text_recognition_ran(qtbot) -> None:
    """
    Given  a summary whose figures rest on 12 pages and no document
    When   the panel is drawn
    Then   the date line counts pages, because no document went through the writing model
    """
    summary = _summary(sample_count=12, sample_pages=True)
    panel = _panel(qtbot, _FakeService(summary, _choices()))

    assert panel._date.text() == "From your last 12 pages."


@pytest.mark.gui
def test_a_step_that_hit_the_time_limit_says_so_instead_of_a_range(qtbot) -> None:
    """
    Given  a measurement whose proofreading step hit the test's time limit
    When   the panel is drawn
    Then   the row says the step is very slow instead of naming a time per page
    """
    summary = _summary(
        steps=(_step(outcome=StepOutcome.TOO_SLOW, message="took too long"),), too_slow=True
    )
    panel = _panel(qtbot, _FakeService(summary, _choices()))

    row = panel._rows[SpeedStep.PROOFREADING]
    assert row.per_page.text() == txt.SPEED_TOO_SLOW
    assert row.per_page.toolTip() == "took too long"
    assert not panel._too_slow.isHidden()


@pytest.mark.gui
def test_the_panel_says_so_while_no_step_runs_on_ollama(qtbot) -> None:
    """
    Given  a page whose text recognition is Tesseract and whose proofreading is off
    When   the panel is drawn
    Then   the button and the figures are hidden and a line says there is nothing to measure
    """
    panel = _panel(qtbot, _FakeService(_summary(), _choices(writing_model="")))

    assert panel._body.isHidden()
    assert not panel._nothing.isHidden()


# ── the warning lines ───────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_panel_says_when_the_card_was_not_used(qtbot) -> None:
    """
    Given  a measurement in which Ollama ran the model on the processor although a card was found
    When   the panel is drawn
    Then   the warning names the model, the card and where to choose a smaller model
    """
    summary = _summary(steps=(_step(on_card=0.0),))
    panel = _panel(qtbot, _FakeService(summary, _choices()))

    assert not panel._card_line.isHidden()
    assert "gemma4:e4b" in panel._card_line.text()
    assert "RTX 3070 (8 GB)" in panel._card_line.text()
    assert txt.WHERE_AI_MODELS in panel._card_line.text()
    assert panel._rows[SpeedStep.PROOFREADING].note.text() == txt.SPEED_ON_PROCESSOR


@pytest.mark.gui
def test_the_panel_says_when_the_measurement_is_out_of_date(qtbot) -> None:
    """
    Given  a measurement the server's version or a model digest has moved past
    When   the panel is drawn
    Then   the warning names the date of the measurement
    """
    panel = _panel(qtbot, _FakeService(_summary(out_of_date=True), _choices()))

    assert not panel._out_of_date.isHidden()
    assert "2026-11-13" in panel._out_of_date.text()


@pytest.mark.gui
def test_the_panel_says_when_the_server_was_busy(qtbot) -> None:
    """
    Given  a measurement taken while another model was loaded
    When   the panel is drawn
    Then   the line says the times may be too long
    """
    panel = _panel(qtbot, _FakeService(_summary(shared=True), _choices()))

    assert not panel._busy.isHidden()
    assert panel._busy.text() == txt.SPEED_BUSY


@pytest.mark.gui
def test_the_panel_says_what_the_swapping_models_cost_per_file(qtbot) -> None:
    """
    Given  a measurement whose models evict each other, with 15 s of loading each
    When   the panel is drawn
    Then   the line names the loading time every file pays
    """
    summary = _summary(swaps=True, swap_load_s=15.0)
    panel = _panel(qtbot, _FakeService(summary, _choices()))

    assert not panel._swaps.isHidden()
    assert "15 s" in panel._swaps.text()


@pytest.mark.gui
def test_the_panel_says_when_the_test_ran_on_another_computer(qtbot) -> None:
    """
    Given  a page whose AI server is another computer
    When   the panel is drawn
    Then   the note names that server, because other people may be using it
    """
    remote = HardwareDetection(
        profile="8gb", reason=DetectionReason.REMOTE_SERVER, host="gpu-server"
    )
    panel = _panel(qtbot, _FakeService(_summary(), _choices()), detection=remote)

    assert not panel._remote.isHidden()
    assert "gpu-server" in panel._remote.text()


@pytest.mark.gui
def test_the_panel_says_when_nothing_was_measured_yet(qtbot) -> None:
    """
    Given  a page whose models were never measured here
    When   the panel is drawn
    Then   the date line says so instead of naming a date
    """
    summary = _summary(steps=(), class_set=None, measured_at="")
    panel = _panel(qtbot, _FakeService(summary, _choices()))

    assert panel._date.text() == txt.SPEED_NEVER_MEASURED
    assert panel._rows[SpeedStep.PROOFREADING].isHidden()


# ── the test itself ─────────────────────────────────────────────────────────


@pytest.mark.gui
def test_the_button_starts_the_test_and_turns_into_stop(qtbot) -> None:
    """
    Given  the panel with a measurement to show
    When   the button is clicked
    Then   the chosen models are measured on a background thread, the button becomes Stop and
           the panel says the test is running; when it finishes the button is Test speed again
    """
    service = _FakeService(_summary(), _choices())
    panel = _panel(qtbot, service)
    assert panel._button.text() == txt.SPEED_TEST

    panel._button.click()

    assert service.started.wait(5)
    assert panel._button.text() == txt.SPEED_STOP
    assert panel.is_running()
    assert not panel._status.isHidden()
    assert service.tested == [service.choices]

    service.release.set()
    qtbot.waitUntil(lambda: panel._button.text() == txt.SPEED_TEST, timeout=5000)
    assert not panel.is_running()
    assert panel._status.isHidden()


@pytest.mark.gui
def test_the_test_measures_the_models_the_page_shows_now(qtbot) -> None:
    """
    Given  a page whose settings provider answers with the choices as they are on screen
    When   the test is started
    Then   the service is asked for the choices of exactly those settings, so unsaved choices
           are measured too
    """
    service = _FakeService(_summary(), _choices())
    panel = _panel(qtbot, service)

    panel._button.click()

    assert service.started.wait(5)
    assert service.settings == {"ollama_url": _URL}
    service.release.set()
    qtbot.waitUntil(lambda: not panel.is_running(), timeout=5000)


@pytest.mark.gui
def test_closing_the_page_stops_the_test(qtbot) -> None:
    """
    Given  a running test
    When   the page is closed
    Then   the test is told to stop and the panel stops claiming to be running, without waiting
           for the measurement to finish
    """
    service = _FakeService(_summary(), _choices())
    panel = _panel(qtbot, service)
    panel._button.click()
    assert service.started.wait(5)

    panel.shutdown()

    assert service.cancel_event is not None
    assert service.cancel_event.is_set()
    assert not panel.is_running()
    assert panel._button.text() == txt.SPEED_TEST
    service.release.set()


@pytest.mark.gui
def test_stop_waits_for_the_test_to_end_before_a_new_one_can_start(qtbot) -> None:
    """
    Given  a running test
    When   Stop is clicked
    Then   the test is told to stop, but the panel keeps running (button disabled, "Stopping…")
           until the test's thread has ended; only then is the button Test speed again
    """
    service = _FakeService(_summary(), _choices(), fail_with=RunStoppedError())
    panel = _panel(qtbot, service)
    panel._button.click()
    assert service.started.wait(5)

    panel._button.click()

    assert service.cancel_event is not None
    assert service.cancel_event.is_set()
    assert panel.is_running()
    assert not panel._button.isEnabled()
    assert panel._button.text() == txt.SPEED_STOPPING

    service.release.set()
    qtbot.waitUntil(lambda: not panel.is_running(), timeout=5000)
    assert panel._button.isEnabled()
    assert panel._button.text() == txt.SPEED_TEST
    assert panel._error.isHidden()


@pytest.mark.gui
def test_a_server_that_could_not_be_reached_shows_the_error(qtbot) -> None:
    """
    Given  a test that ends with a result carrying an error line (the server is not running)
    When   the test finishes
    Then   the panel shows the reason instead of nothing
    """
    service = _FakeService(
        _summary(), _choices(), result=ProbeResult(error="Cannot connect to Ollama")
    )
    panel = _panel(qtbot, service)

    panel._button.click()
    assert service.started.wait(5)
    service.release.set()

    qtbot.waitUntil(lambda: not panel._error.isHidden(), timeout=5000)
    assert "Cannot connect to Ollama" in panel._error.text()


@pytest.mark.gui
def test_a_test_that_could_not_run_says_why(qtbot) -> None:
    """
    Given  a server that cannot be reached
    When   the test finishes with an error
    Then   the panel shows the reason instead of a figure
    """
    service = _FakeService(_summary(), _choices(), fail_with=RuntimeError("server is down"))
    panel = _panel(qtbot, service)

    panel._button.click()
    assert service.started.wait(5)
    service.release.set()

    qtbot.waitUntil(lambda: not panel._error.isHidden(), timeout=5000)
    assert "server is down" in panel._error.text()
    assert panel._button.text() == txt.SPEED_TEST


@pytest.mark.gui
def test_the_panel_asks_for_the_server_facts_once_per_address(qtbot) -> None:
    """
    Given  the panel on a page with one server address
    When   it is drawn twice and then pointed at another server
    Then   the facts are asked for once per address, because they decide whether a stored
           measurement is still current
    """
    service = _FakeService(_summary(), _choices())
    panel = _panel(qtbot, service)
    panel.refresh()

    qtbot.waitUntil(lambda: service.facts_urls == [_URL], timeout=5000)
    panel.set_ollama_url("http://gpu-server:11434")

    qtbot.waitUntil(lambda: len(service.facts_urls) == 2, timeout=5000)
    assert service.facts_urls[-1] == "http://gpu-server:11434"


# ── the wording ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 s"), (40, "40 s"), (59, "59 s"), (90, "2 min"), (3540, "59 min"), (7200, "2 h")],
)
def test_a_duration_is_rounded_to_what_a_teacher_reads(seconds, expected) -> None:
    """
    Given  a number of seconds
    When   it is written out
    Then   it is rounded to whole seconds below a minute and to whole minutes above it
    """
    assert txt.format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (20, 30, "20-30 s"),
        (25, 25, "25 s"),
        (420, 720, "7-12 min"),
        (5400, 10800, "1 h 30 min - 3 h"),
    ],
)
def test_a_range_shares_its_unit_when_both_ends_use_it(low, high, expected) -> None:
    """
    Given  the low and the high end of an estimate
    When   the range is written out
    Then   the unit is named once when both ends use it, and twice when they do not
    """
    assert txt.format_span(low, high) == expected


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        ("fast", "fast (expected)"),
        ("ok", "ok (expected)"),
        ("very_slow", "very slow (expected)"),
    ],
)
def test_the_expected_speed_label_names_the_band_and_says_it_is_expected(band, expected) -> None:
    """
    Given  a speed band a model is expected to reach here
    When   its compact label is written
    Then   it names the band in words and says the figure is expected, never seconds
    """
    assert txt.speed_expected_label(band) == expected


def test_the_expected_speed_note_says_where_the_figure_comes_from() -> None:
    """
    Given  a speed band a model is expected to reach here
    When   its tooltip line is written
    Then   it says the figure was worked out from the models measured here
    """
    assert txt.speed_expected_note("fast") == (
        "Expected speed on this computer: fast. Worked out from the models measured here, "
        "not measured for this one."
    )
