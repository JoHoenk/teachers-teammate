"""GUI tests for the shared HardwareProfileSelector widget."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.model_catalog import (
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    HardwareProfileSetting,
    concrete_profile,
)
from teachers_teammate.gui._hardware_profile_selector import HardwareProfileSelector
from teachers_teammate.texts.models import detection_status_text
from ._service_stubs import services_of


class _StubService:
    """Stub application service with a fixed detection result."""

    def __init__(self, detection: HardwareDetection) -> None:
        self.detection = detection
        self.detect_calls: list[tuple[str, bool]] = []
        self.manual_memory_gb: float | None = None  # what a chosen profile reports as memory
        self.free_memory_gb: float | None = None  # what is free right now
        self.memory_calls = 0

    def available_memory_gb(self) -> float | None:
        self.memory_calls += 1
        return self.free_memory_gb

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        self.detect_calls.append((ollama_url, refresh))
        return self.detection

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        _ = ollama_url
        profile = concrete_profile(setting)
        if profile is not None:
            return HardwareDetection(
                profile=profile,
                reason=DetectionReason.MANUAL,
                system_memory_gb=self.manual_memory_gb,
            )
        return self.detect_hardware(ollama_url)


_GPU_DETECTED = HardwareDetection(
    profile=HardwareProfile.CARD_8GB,
    reason=DetectionReason.GPU_DETECTED,
    gpu_name="NVIDIA GeForce RTX 4060",
    memory_gb=8.0,
)


@pytest.fixture
def stub_service() -> _StubService:
    return _StubService(_GPU_DETECTED)


# ── detection status texts ──────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
@pytest.mark.parametrize(
    ("detection", "expected_fragment"),
    [
        (
            HardwareDetection(
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.GPU_DETECTED,
                gpu_name="NVIDIA GeForce RTX 4060",
                memory_gb=8.0,
            ),
            "Graphics card: NVIDIA GeForce RTX 4060 with 8 GB graphics memory",
        ),
        (
            HardwareDetection(
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.APPLE_UNIFIED_MEMORY,
                memory_gb=16.0,
            ),
            "Apple computer with 16 GB shared memory",
        ),
        (
            HardwareDetection(profile=HardwareProfile.CPU, reason=DetectionReason.NO_GPU_FOUND),
            "No graphics card found.",
        ),
        (
            HardwareDetection(
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.GPU_MEMORY_UNKNOWN,
                gpu_name="AMD GPU",
            ),
            "Graphics card: AMD GPU. Its memory size could not be read",
        ),
        (
            HardwareDetection(
                profile=HardwareProfile.CARD_8GB,
                reason=DetectionReason.REMOTE_SERVER,
                host="192.168.1.50",
            ),
            "Ollama runs on another computer (192.168.1.50)",
        ),
        (
            HardwareDetection(profile=HardwareProfile.CARD_16GB, reason=DetectionReason.MANUAL),
            "Chosen by you.",
        ),
    ],
)
def test_detection_status_text_per_reason(
    detection: HardwareDetection, expected_fragment: str
) -> None:
    """
    Given  a hardware detection with a given reason
    When   detection_status_text is called
    Then   the wording for that situation is produced
    """
    assert expected_fragment in detection_status_text(detection)


# ── editable variant ────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_status_label_shows_gpu_detected_text_after_detection(qtbot, stub_service) -> None:
    """
    Given  an editable selector with a patched service
    When   detection finishes
    Then   the status label shows the GPU_DETECTED text
    """
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    assert "NVIDIA GeForce RTX 4060 with 8 GB graphics memory" in widget._status_lbl.text()


@pytest.mark.use_case("Hardware_Profile_Override")
def test_combo_change_emits_setting_changed_with_internal_value(qtbot, stub_service) -> None:
    """
    Given  an editable selector
    When   the user picks "Graphics card with 16 GB memory or more"
    Then   setting_changed emits the internal value "16gb"
    """
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.setting_changed, timeout=5000) as blocker:
        widget._combo.setCurrentText("Graphics card with 16 GB memory or more")
    assert blocker.args == ["16gb"]
    assert widget.setting() == "16gb"


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_again_calls_detect_hardware_with_refresh(qtbot, stub_service) -> None:
    """
    Given  an editable selector in auto mode
    When   the "Detect again" link is activated
    Then   detect_hardware runs with refresh=True
    """
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        widget._on_detect_again()
    assert stub_service.detect_calls == [
        ("http://127.0.0.1:11434", False),
        ("http://127.0.0.1:11434", True),
    ]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_manual_choice_resolves_without_detection(qtbot, stub_service) -> None:
    """
    Given  an editable selector set to a concrete profile
    When   resolution finishes
    Then   the detection is MANUAL with the chosen profile and no detection ran
    """
    widget = HardwareProfileSelector(
        app_service=services_of(stub_service), setting=HardwareProfileSetting.CPU
    )
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000) as blocker:
        pass
    detection = blocker.args[0]
    assert detection.profile == "cpu"
    assert detection.reason is DetectionReason.MANUAL
    assert widget.resolved().profile == "cpu"
    assert stub_service.detect_calls == []


# ── the memory free right now ───────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_status_line_names_the_memory_free_right_now(qtbot, stub_service) -> None:
    """
    Given  a computer with 16 GB of memory, 9 GB of it free right now
    When   the selector is shown
    Then   the status line names both figures, and the free one is read again every 10 s while
           the page stays visible (it changes all the time)
    """
    stub_service.detection = HardwareDetection(
        profile=HardwareProfile.CPU, reason=DetectionReason.NO_GPU_FOUND, system_memory_gb=16.0
    )
    stub_service.free_memory_gb = 9.0
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)

    widget.show()

    qtbot.waitUntil(lambda: "9 GB free right now" in widget._status_lbl.text(), timeout=5000)
    assert "Computer memory: 16 GB, 9 GB free right now" in widget._status_lbl.text()
    assert widget._memory_timer.isActive()
    assert widget._memory_timer.interval() == 10_000
    calls = stub_service.memory_calls
    widget._read_available_memory()
    qtbot.waitUntil(lambda: stub_service.memory_calls > calls, timeout=5000)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_remote_server_shows_no_free_memory(qtbot, stub_service) -> None:
    """
    Given  Ollama on another computer
    When   the selector is shown
    Then   no free figure is read: it would be this computer's, not the server's
    """
    stub_service.detection = HardwareDetection(
        profile=HardwareProfile.CARD_8GB, reason=DetectionReason.REMOTE_SERVER, host="gpu-box.lan"
    )
    stub_service.free_memory_gb = 9.0
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)

    widget.show()
    qtbot.waitUntil(lambda: "another computer" in widget._status_lbl.text(), timeout=5000)

    assert stub_service.memory_calls == 0
    assert "free right now" not in widget._status_lbl.text()


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_the_free_memory_is_not_part_of_the_detection(qtbot, stub_service) -> None:
    """
    Given  a computer whose free memory was read
    When   the detection is asked for again
    Then   the detection carries the total memory only, so the suggestions stay stable
    """
    stub_service.detection = HardwareDetection(
        profile=HardwareProfile.CPU, reason=DetectionReason.NO_GPU_FOUND, system_memory_gb=16.0
    )
    stub_service.free_memory_gb = 9.0
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: "free right now" in widget._status_lbl.text(), timeout=5000)

    detection = widget.resolved()

    assert detection is not None
    assert detection.system_memory_gb == 16.0
    assert not hasattr(detection, "available_memory_gb")


# ── read-only variant ────────────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_read_only_variant_shows_suggestions_label(qtbot, stub_service) -> None:
    """
    Given  a read-only selector
    When   it is created
    Then   it has no combo box and shows "Models are suggested for: …"
    """
    widget = HardwareProfileSelector(app_service=services_of(stub_service), read_only=True)
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    assert widget._combo is None
    assert widget._status_lbl.text().startswith("Models are suggested for: ")
    assert "Detect again" not in widget._status_lbl.text()
    assert widget._status_lbl.toolTip()


# ── lifetime ────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_closing_while_detection_runs_is_harmless(qtbot) -> None:
    """
    Given  a selector whose hardware detection is still running
    When   the widget is destroyed and the detection finishes afterwards
    Then   nothing crashes and the late result is dropped (review finding #2)
    """
    import threading  # noqa: PLC0415

    from teachers_teammate.gui._background import wait_for_background_tasks  # noqa: PLC0415

    gate = threading.Event()
    resolved: list[object] = []

    class _SlowService(_StubService):
        def detect_hardware(self, ollama_url: str, *, refresh: bool = False):
            assert gate.wait(timeout=5)
            return super().detect_hardware(ollama_url, refresh=refresh)

    widget = HardwareProfileSelector(app_service=services_of(_SlowService(_GPU_DETECTED)))
    widget.resolved_changed.connect(resolved.append)
    widget.deleteLater()
    qtbot.wait(50)  # processes the deferred delete

    gate.set()
    assert wait_for_background_tasks(5000)
    qtbot.wait(50)
    assert resolved == []


@pytest.mark.use_case("Hardware_Profile_Override")
def test_same_url_does_not_trigger_a_new_resolution(qtbot, stub_service) -> None:
    """
    Given  a resolved selector in automatic mode
    When   set_ollama_url is called with the URL it already has
    Then   no further detection is requested
    """
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    calls = len(stub_service.detect_calls)
    widget.set_ollama_url("http://127.0.0.1:11434")
    qtbot.wait(50)
    assert len(stub_service.detect_calls) == calls


@pytest.mark.use_case("Hardware_Profile_Override")
def test_manual_choice_ignores_url_changes(qtbot, stub_service) -> None:
    """
    Given  a selector set to a concrete profile
    When   the Ollama URL changes
    Then   no detection runs — a concrete choice does not depend on the server
    """
    widget = HardwareProfileSelector(
        app_service=services_of(stub_service), setting=HardwareProfileSetting.CPU
    )
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    widget.set_ollama_url("http://192.168.1.50:11434")
    qtbot.wait(50)
    assert stub_service.detect_calls == []


@pytest.mark.use_case("Hardware_Profile_Override")
def test_refresh_status_resolves_again_without_probing_again(qtbot, stub_service) -> None:
    """
    Given  a selector in auto mode that resolved once
    When   refresh_status() is called (the probe was repeated elsewhere)
    Then   the profile is resolved again from the shared probe — refresh stays False
    """
    widget = HardwareProfileSelector(app_service=services_of(stub_service))
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        widget.refresh_status()
    assert stub_service.detect_calls == [
        ("http://127.0.0.1:11434", False),
        ("http://127.0.0.1:11434", False),
    ]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_a_failed_check_is_said_and_logged_not_shown_as_no_suggestion(
    qtbot, stub_service, caplog
) -> None:
    """
    Given  a computer check that raises
    When   the selector resolves
    Then   the status line says the computer could not be checked (and that suggestions assume
           8 GB), the error is logged with its traceback, and the result is None --
           instead of the silent "no suggestion" the same line showed for a failure and for
           "nothing to suggest"
    """
    import logging  # noqa: PLC0415

    class _Failing(_StubService):
        def detect_hardware(self, ollama_url: str, *, refresh: bool = False):
            raise RuntimeError("nvidia-smi exploded")

    widget = HardwareProfileSelector(app_service=services_of(_Failing(_GPU_DETECTED)))
    qtbot.addWidget(widget)
    with (
        caplog.at_level(logging.WARNING),
        qtbot.waitSignal(widget.resolved_changed, timeout=5000) as blocker,
    ):
        pass

    assert blocker.args[0] is None
    status = widget._status_lbl.text()
    assert "could not be checked" in status and "8 GB" in status
    assert status != "No suggestion available for this computer."
    record = next(r for r in caplog.records if "Checking this computer failed" in r.message)
    assert record.exc_info is not None
    assert "nvidia-smi exploded" in str(record.exc_info[1])


@pytest.mark.use_case("Hardware_Profile_Override")
def test_a_chosen_profile_is_resolved_through_the_service_and_keeps_the_memory(
    qtbot, stub_service
) -> None:
    """
    Given  a chosen profile on a computer with 32 GB of memory
    When   the selector resolves
    Then   the detection is MANUAL with that profile and carries the memory (a chosen profile
           still rates models too big for the card by it), and no graphics probe ran
    """
    stub_service.manual_memory_gb = 32.0
    widget = HardwareProfileSelector(
        app_service=services_of(stub_service), setting=HardwareProfileSetting.CARD_8GB
    )
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000) as blocker:
        pass

    detection = blocker.args[0]
    assert (detection.profile, detection.system_memory_gb) == ("8gb", 32.0)
    assert detection.reason is DetectionReason.MANUAL
    assert stub_service.detect_calls == []
