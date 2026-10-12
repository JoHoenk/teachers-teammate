"""GUI tests for the shared HardwareProfileSelector widget."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import pytest

from teachers_teammate.domain.model_catalog import (
    DetectionReason,
    HardwareDetection,
)
from teachers_teammate.gui._hardware_profile_selector import HardwareProfileSelector
from teachers_teammate.gui._model_wording import detection_status_text


class _StubService:
    """Stub application service with a fixed detection result."""

    def __init__(self, detection: HardwareDetection) -> None:
        self.detection = detection
        self.detect_calls: list[tuple[str, bool]] = []

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        self.detect_calls.append((ollama_url, refresh))
        return self.detection


_GPU_DETECTED = HardwareDetection(
    profile="8gb",
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
                profile="8gb",
                reason=DetectionReason.GPU_DETECTED,
                gpu_name="NVIDIA GeForce RTX 4060",
                memory_gb=8.0,
            ),
            "Found: NVIDIA GeForce RTX 4060 with 8 GB graphics memory.",
        ),
        (
            HardwareDetection(
                profile="8gb",
                reason=DetectionReason.APPLE_UNIFIED_MEMORY,
                memory_gb=16.0,
            ),
            "Found: Apple computer with 16 GB shared memory.",
        ),
        (
            HardwareDetection(profile="cpu", reason=DetectionReason.NO_GPU_FOUND),
            "No graphics card found.",
        ),
        (
            HardwareDetection(
                profile="8gb",
                reason=DetectionReason.GPU_MEMORY_UNKNOWN,
                gpu_name="AMD GPU",
            ),
            "Found: AMD GPU. Its memory size could not be read",
        ),
        (
            HardwareDetection(
                profile="8gb",
                reason=DetectionReason.REMOTE_SERVER,
                host="192.168.1.50",
            ),
            "Ollama runs on another computer (192.168.1.50)",
        ),
        (HardwareDetection(profile="16gb", reason=DetectionReason.MANUAL), "Chosen by you."),
    ],
)
def test_detection_status_text_per_reason(
    detection: HardwareDetection, expected_fragment: str
) -> None:
    """
    Given  a hardware detection with a given reason
    When   detection_status_text is called
    Then   the §2 wording for that situation is produced
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
    widget = HardwareProfileSelector(app_service=stub_service)
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    assert "NVIDIA GeForce RTX 4060 with 8 GB graphics memory." in widget._status_lbl.text()


@pytest.mark.use_case("Hardware_Profile_Override")
def test_combo_change_emits_setting_changed_with_internal_value(qtbot, stub_service) -> None:
    """
    Given  an editable selector
    When   the user picks "Graphics card with 16 GB memory or more"
    Then   setting_changed emits the internal value "16gb"
    """
    widget = HardwareProfileSelector(app_service=stub_service)
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
    widget = HardwareProfileSelector(app_service=stub_service)
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
    widget = HardwareProfileSelector(app_service=stub_service, setting="cpu")
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000) as blocker:
        pass
    detection = blocker.args[0]
    assert detection.profile == "cpu"
    assert detection.reason is DetectionReason.MANUAL
    assert widget.resolved().profile == "cpu"
    assert stub_service.detect_calls == []


# ── read-only variant ────────────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_read_only_variant_shows_suggestions_label(qtbot, stub_service) -> None:
    """
    Given  a read-only selector
    When   it is created
    Then   it has no combo box and shows "Suggestions are for: …"
    """
    widget = HardwareProfileSelector(app_service=stub_service, read_only=True)
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    assert widget._combo is None
    assert widget._status_lbl.text().startswith("Suggestions are for: ")
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

    widget = HardwareProfileSelector(app_service=_SlowService(_GPU_DETECTED))
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
    widget = HardwareProfileSelector(app_service=stub_service)
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
    widget = HardwareProfileSelector(app_service=stub_service, setting="cpu")
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
    When   refresh_status() is called (the probe was repeated elsewhere, e.g. "Detect GPU")
    Then   the profile is resolved again from the shared probe — refresh stays False
    """
    widget = HardwareProfileSelector(app_service=stub_service)
    qtbot.addWidget(widget)
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        pass
    with qtbot.waitSignal(widget.resolved_changed, timeout=5000):
        widget.refresh_status()
    assert stub_service.detect_calls == [
        ("http://127.0.0.1:11434", False),
        ("http://127.0.0.1:11434", False),
    ]
