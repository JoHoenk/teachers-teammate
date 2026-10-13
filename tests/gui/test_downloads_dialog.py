"""GUI tests for the reworked Downloads dialog (Ollama model groups & suitability)."""
# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    OLLAMA_CATALOG,
    DetectionReason,
    HardwareDetection,
    ModelUses,
)
from teachers_teammate.gui._downloads_dialog import DownloadsDialog

from ._service_stubs import RecommendationServiceStub

_USER_ROLE = 0x0100  # Qt.ItemDataRole.UserRole


class _StubService(RecommendationServiceStub):
    """Stub application service for DownloadsDialog tests."""

    def __init__(self, installed: set[str] | None = None) -> None:
        super().__init__()
        self.installed = installed or set()
        # Tests that need to control *when* hardware detection finishes set this.
        self.detection_gate: threading.Event | None = None
        # Likewise for the installed-model check.
        self.status_gate: threading.Event | None = None
        self.status_calls = 0
        # When set, the installed-model check fails with this error.
        self.status_error: Exception | None = None
        # Uses the (fake) server reports for models that are not in the catalog.
        self.custom_uses: dict[str, ModelUses] = {}

    def classify_ollama_models(self, tags, ollama_url, *, cached_only=False):
        result = super().classify_ollama_models(tags, ollama_url, cached_only=cached_only)
        for tag in tags:
            if result.get(tag) is None:
                result[tag] = self.custom_uses.get(tag)
        return result

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        if self.detection_gate is not None:
            assert self.detection_gate.wait(timeout=5), "test never released the detection"
        return super().detect_hardware(ollama_url, refresh=refresh)

    # ── installation probing ──────────────────────────────────────────────
    def get_installation_status(self, *, ollama_models=None, **_kwargs) -> dict[str, bool]:
        self.status_calls += 1
        if self.status_error is not None:
            raise self.status_error
        if self.status_gate is not None and self.status_calls == 1:
            assert self.status_gate.wait(timeout=5), "test never released the check"
        return {f"ollama:{m}": (m in self.installed) for m in (ollama_models or [])}

    def is_module_importable(self, module: str) -> bool:
        _ = module
        return False

    def list_ollama_model_sizes(self, url: str = "") -> dict[str, int]:
        _ = url
        return {}

    # ── unused in these tests ─────────────────────────────────────────────
    def pull_ollama_model(
        self, url: str, model_name: str, progress_cb=None, abort_event=None
    ) -> None:
        _ = url, model_name, progress_cb, abort_event


def _make_dialog(
    qtbot,
    installed: set[str] | None = None,
    hardware_profile: str = "8gb",
    preselect=(),
    service: Any = None,
    wait_for_profile: bool = True,
) -> DownloadsDialog:
    dlg = DownloadsDialog(
        ollama_url="http://127.0.0.1:11434",
        hardware_profile=hardware_profile,
        preselect=preselect,
        app_service=service or _StubService(installed),  # ty: ignore[invalid-argument-type]  # test stub for the concrete service
    )
    qtbot.addWidget(dlg)
    if wait_for_profile:
        with qtbot.waitSignal(dlg._hardware_selector.resolved_changed, timeout=5000):
            pass
    return dlg


def _detection_results(dlg: DownloadsDialog, installed: set[str]) -> dict:
    """Build the results dict ``_detect_installed`` would produce."""
    tags = [tag for tag, _item in dlg._iter_model_items()]
    return {f"ollama:{t}": (t in installed) for t in tags}


def _group_tags(dlg: DownloadsDialog, key: str) -> list[str]:
    group = dlg._group_items[key]
    return [
        str(group.child(i).data(0, _USER_ROLE) or group.child(i).text(0))
        for i in range(group.childCount())
    ]


# ── grouping ────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_groups_contain_catalog_models_and_other_is_hidden(qtbot) -> None:
    """
    Given  the reworked Downloads dialog
    When   the Ollama tab is built
    Then   reading/writing groups hold exactly their catalog models and
           "Other models" is hidden while empty
    """
    dlg = _make_dialog(qtbot)
    reading = {spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"}
    writing = {spec.tag for spec in OLLAMA_CATALOG if spec.category == "general"}
    assert set(_group_tags(dlg, "ocr")) == reading
    assert set(_group_tags(dlg, "general")) == writing
    assert dlg._group_items["other"].isHidden() is True
    assert dlg._group_items["other"].childCount() == 0


# ── suitability column ──────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_suitability_column_and_sort_order_follow_profile(qtbot) -> None:
    """
    Given  the dialog resolved to the 8gb profile
    When   the suitability column is inspected
    Then   the BEST models come first with the "★ Best choice" label
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    reading = dlg._group_items["ocr"]
    assert reading.child(0).text(0) == DEFAULT_OCR_MODEL
    assert reading.child(0).text(1) == "★ Best choice"
    writing = dlg._group_items["general"]
    assert writing.child(0).text(0) == "qwen3.5:9b"
    gpt_oss = dlg._model_items["gpt-oss:20b"]
    assert gpt_oss.text(1) == "Too large for this computer"


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_switching_profile_updates_suitability(qtbot) -> None:
    """
    Given  the dialog resolved to 8gb
    When   the user switches to "16 GB or more"
    Then   suitability labels and sorting update for the new profile
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    with qtbot.waitSignal(dlg._hardware_selector.resolved_changed, timeout=5000):
        dlg._hardware_selector._combo.setCurrentText("Graphics card with 16 GB memory or more")
    writing = dlg._group_items["general"]
    assert writing.child(0).text(0) == "gpt-oss:20b"
    assert writing.child(0).text(1) == "★ Best choice"


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_rows_are_grey_but_checkable(qtbot) -> None:
    """
    Given  a TOO_LARGE model row on the 8gb profile
    When   the row is inspected and ticked
    Then   it is drawn grey but stays checkable (AC4)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    gpt_oss = dlg._model_items["gpt-oss:20b"]
    assert gpt_oss.foreground(0).color().name().lower() == "#888888"
    assert bool(gpt_oss.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    gpt_oss.setCheckState(0, Qt.CheckState.Checked)
    assert gpt_oss.checkState(0) is Qt.CheckState.Checked


# ── select best & pre-ticking ───────────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_select_best_ticks_undownloaded_best_models(qtbot) -> None:
    """
    Given  the dialog with nothing installed and deepseek-ocr already ticked
    When   "Select the best models for my computer" is clicked
    Then   the BEST reading and writing models are ticked, other ticks stay,
           and downloaded models are skipped
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    deepseek = dlg._model_items["deepseek-ocr:latest"]
    deepseek.setCheckState(0, Qt.CheckState.Checked)
    dlg._on_select_best_models()
    assert dlg._model_items[DEFAULT_OCR_MODEL].checkState(0) is Qt.CheckState.Checked
    assert dlg._model_items["qwen3.5:9b"].checkState(0) is Qt.CheckState.Checked
    assert deepseek.checkState(0) is Qt.CheckState.Checked


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_select_best_reports_when_already_downloaded(qtbot) -> None:
    """
    Given  the dialog with both BEST models already installed
    When   "Select the best models for my computer" is clicked
    Then   the status line says the best models are already downloaded
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, {DEFAULT_OCR_MODEL, "qwen3.5:9b"}))
    dlg._on_select_best_models()
    assert dlg._ollama_hint_lbl.text() == (
        "The best models for your computer are already downloaded."
    )


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_pre_ticking_when_no_model_of_category_downloaded(qtbot) -> None:
    """
    Given  no catalogued reading model installed
    When   install detection finishes
    Then   the BEST reading model is pre-ticked (AC14)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert dlg._model_items[DEFAULT_OCR_MODEL].checkState(0) is Qt.CheckState.Checked
    assert dlg._model_items["qwen3.5:9b"].checkState(0) is Qt.CheckState.Checked


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_no_pre_ticking_when_category_already_downloaded(qtbot) -> None:
    """
    Given  glm-ocr (a reading model) already installed
    When   install detection finishes
    Then   the BEST reading model is not pre-ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, {"glm-ocr:q8_0", "qwen3.5:9b"}))
    assert dlg._model_items[DEFAULT_OCR_MODEL].checkState(0) is Qt.CheckState.Unchecked


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_preselect_ticks_given_tags(qtbot) -> None:
    """
    Given  the dialog opened with preselect=["glm-ocr:q8_0"]
    When   install detection finishes
    Then   the given tag is ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot, preselect=["glm-ocr:q8_0"])
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert dlg._model_items["glm-ocr:q8_0"].checkState(0) is Qt.CheckState.Checked


# ── detection regression ────────────────────────────────────────────────────


def test_detection_marks_rows_downloaded_and_disables_them(qtbot) -> None:
    """
    Given  a detection result with glm-ocr installed
    When   _on_detection_done runs
    Then   the row shows a downloaded mark and is disabled (regression)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, {"glm-ocr:q8_0"}))
    row = dlg._model_items["glm-ocr:q8_0"]
    assert row.text(3) == "✓"
    assert not bool(row.flags() & Qt.ItemFlag.ItemIsEnabled)
    assert row.checkState(0) is Qt.CheckState.Checked


def test_download_install_collects_ticked_child_rows(qtbot) -> None:
    """
    Given  two ticked model rows
    When   _on_download_install runs with _run_ollama_pulls patched
    Then   exactly those two tags are pulled (regression: flat-list loop)
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    # Undo the pre-ticking so the test controls exactly what is ticked.
    for _tag, row in dlg._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg._model_items[DEFAULT_OCR_MODEL].setCheckState(0, Qt.CheckState.Checked)
    dlg._model_items["deepseek-ocr:latest"].setCheckState(0, Qt.CheckState.Checked)
    pulls = MagicMock()
    dlg._run_ollama_pulls = pulls  # ty: ignore[invalid-assignment]  # test patches the pull pipeline
    dlg._on_download_install()
    pulls.assert_called_once_with([DEFAULT_OCR_MODEL, "deepseek-ocr:latest"])


# ── too-large confirmation ──────────────────────────────────────────────────


class _FakeMessageBox:
    """Stand-in for QMessageBox returning a chosen button."""

    class Icon:
        Warning = None

    class ButtonRole:
        AcceptRole = None
        RejectRole = None

    clicked: object = "Cancel"
    last_text: str = ""

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def setWindowTitle(self, _title: str) -> None:
        pass

    def setIcon(self, _icon: object) -> None:
        pass

    def setText(self, text: str) -> None:
        _FakeMessageBox.last_text = text

    def addButton(self, text: str, _role: object) -> object:
        return text

    def setDefaultButton(self, _button: object) -> None:
        pass

    def exec(self) -> int:
        return 0

    def clickedButton(self) -> object:
        return _FakeMessageBox.clicked


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_selection_cancel_aborts(qtbot, monkeypatch) -> None:
    """
    Given  a ticked TOO_LARGE model and a confirmation answered with Cancel
    When   _on_download_install runs
    Then   no pull happens
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    # Undo the pre-ticking so only the TOO_LARGE model is ticked.
    for _tag, row in dlg._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg._model_items["gpt-oss:20b"].setCheckState(0, Qt.CheckState.Checked)
    pulls = MagicMock()
    dlg._run_ollama_pulls = pulls  # ty: ignore[invalid-assignment]  # test patches the pull pipeline
    _FakeMessageBox.clicked = "Cancel"
    import teachers_teammate.gui._downloads_dialog as dd  # noqa: PLC0415

    monkeypatch.setattr(dd, "QMessageBox", _FakeMessageBox)
    dlg._on_download_install()
    pulls.assert_not_called()
    assert "needs about 14 GB of graphics memory" in _FakeMessageBox.last_text
    assert "computers of this type have about 8 GB" in _FakeMessageBox.last_text


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_selection_download_anyway_proceeds(qtbot, monkeypatch) -> None:
    """
    Given  a ticked TOO_LARGE model and a confirmation answered with Download anyway
    When   _on_download_install runs
    Then   the pull happens
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    # Undo the pre-ticking so only the TOO_LARGE model is ticked.
    for _tag, row in dlg._iter_model_items():
        row.setCheckState(0, Qt.CheckState.Unchecked)
    dlg._model_items["gpt-oss:20b"].setCheckState(0, Qt.CheckState.Checked)
    pulls = MagicMock()
    dlg._run_ollama_pulls = pulls  # ty: ignore[invalid-assignment]  # test patches the pull pipeline
    _FakeMessageBox.clicked = "Download anyway"
    import teachers_teammate.gui._downloads_dialog as dd  # noqa: PLC0415

    monkeypatch.setattr(dd, "QMessageBox", _FakeMessageBox)
    dlg._on_download_install()
    pulls.assert_called_once_with(["gpt-oss:20b"])


# ── custom models ────────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_custom_model_goes_into_other_group(qtbot) -> None:
    """
    Given  a custom model tag added by name
    When   the row is placed
    Then   it lives in the visible "Other models" group and is ticked
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._ollama_custom_input.setText("llama3.2:latest")
    dlg._on_add_custom_ollama_model()
    other = dlg._group_items["other"]
    assert other.childCount() == 1
    assert other.isHidden() is False
    row = dlg._model_items["llama3.2:latest"]
    assert row.checkState(0) is Qt.CheckState.Checked


@pytest.mark.use_case("Model_Category_Guidance")
def test_custom_model_duplicates_are_ignored(qtbot) -> None:
    """
    Given  a custom model already in the list
    When   the same tag is added again
    Then   no duplicate row is created
    """
    dlg = _make_dialog(qtbot)
    dlg._ollama_custom_input.setText("llama3.2:latest")
    dlg._on_add_custom_ollama_model()
    dlg._ollama_custom_input.setText("llama3.2:latest")
    dlg._on_add_custom_ollama_model()
    assert dlg._group_items["other"].childCount() == 1


@pytest.mark.use_case("Model_Category_Guidance")
def test_pulled_custom_model_stays_in_other_and_shows_abilities(qtbot) -> None:
    """
    Given  a custom model that was pulled and that the server reports as able to read images
    When   detection finishes
    Then   its row stays in "Other models" and says what it can do ("Reads scans and writes")
    """
    service = _StubService()
    service.custom_uses = {"my-vision-model:latest": frozenset({"ocr", "general"})}
    dlg = _make_dialog(qtbot, service=service)
    dlg._ollama_custom_input.setText("my-vision-model:latest")
    dlg._on_add_custom_ollama_model()
    dlg._on_detection_done(_detection_results(dlg, {"my-vision-model:latest"}))
    assert "my-vision-model:latest" in _group_tags(dlg, "other")
    assert "my-vision-model:latest" not in _group_tags(dlg, "ocr")
    assert dlg._model_items["my-vision-model:latest"].text(1) == "Reads scans and writes"


@pytest.mark.use_case("Model_Category_Guidance")
def test_failed_installed_check_still_ticks_best_models_and_preselect(qtbot) -> None:
    """
    Given  an installed-model check that fails (for example the server is unreachable)
    When   the dialog opens with a preselected model
    Then   the failure is shown, and the best models and the preselected model are
           still ticked — nothing is downloaded until the user clicks Install
    """
    service = _StubService()
    service.status_error = RuntimeError("Ollama is unreachable")
    dlg = _make_dialog(qtbot, service=service, preselect=["llama3.2:latest"])
    dlg.show()
    qtbot.waitUntil(lambda: "Detection failed" in dlg._dl_detect_lbl.text(), timeout=5000)
    assert _ticked(dlg) == {DEFAULT_OCR_MODEL, "qwen3.5:9b", "llama3.2:latest"}


# ── profile change signal ────────────────────────────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_changing_my_computer_emits_hardware_profile_changed(qtbot) -> None:
    """
    Given  the Downloads dialog
    When   the "My computer" combo is changed
    Then   hardware_profile_changed emits the internal setting value
    """
    dlg = _make_dialog(qtbot)
    with qtbot.waitSignal(dlg.hardware_profile_changed, timeout=5000) as blocker:
        dlg._hardware_selector._combo.setCurrentText(
            "Basic computer or small graphics card (under 8 GB)"
        )
    assert blocker.args == ["cpu"]


# ── pre-ticking waits for BOTH detections (review finding #4) ───────────────


def _ticked(dlg: DownloadsDialog) -> set[str]:
    """Return the tags of all enabled rows that are currently ticked."""
    from PySide6.QtCore import Qt  # noqa: PLC0415

    return {
        tag
        for tag, row in dlg._iter_model_items()
        if row.checkState(0) is Qt.CheckState.Checked
        and bool(row.flags() & Qt.ItemFlag.ItemIsEnabled)
    }


def _choose_computer(qtbot, dlg: DownloadsDialog, text: str) -> None:
    with qtbot.waitSignal(dlg._hardware_selector.resolved_changed, timeout=5000):
        dlg._hardware_selector._combo.setCurrentText(text)


_CPU_TEXT = "Basic computer or small graphics card (under 8 GB)"
_16GB_TEXT = "Graphics card with 16 GB memory or more"


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_pre_ticking_waits_for_hardware_detection(qtbot) -> None:
    """
    Given  the installed-model check finishes BEFORE the hardware detection and
           the computer turns out to be a basic one
    When   the hardware detection finishes later
    Then   nothing was ticked in between, and afterwards the basic-computer
           models are pre-ticked — not the 8 GB ones assumed while unknown
    """
    service = _StubService()
    service.detection_gate = threading.Event()
    service.detection = HardwareDetection(profile="cpu", reason=DetectionReason.NO_GPU_FOUND)
    dlg = _make_dialog(qtbot, hardware_profile="auto", service=service, wait_for_profile=False)

    dlg._on_detection_done(_detection_results(dlg, set()))
    assert _ticked(dlg) == set()

    with qtbot.waitSignal(dlg._hardware_selector.resolved_changed, timeout=5000):
        service.detection_gate.set()
    assert _ticked(dlg) == {"glm-ocr:q8_0", "qwen3:4b"}


@pytest.mark.use_case("Hardware_Profile_Override")
def test_profile_change_replaces_untouched_auto_ticks_and_keeps_user_ticks(qtbot) -> None:
    """
    Given  automatic ticks for 8 GB and one model the user ticked by hand
    When   the user switches "My computer" to a basic computer
    Then   the automatic ticks move to the basic-computer models and the
           user's own tick stays
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    assert _ticked(dlg) == {DEFAULT_OCR_MODEL, "qwen3.5:9b"}
    dlg._model_items["mistral:latest"].setCheckState(0, Qt.CheckState.Checked)

    _choose_computer(qtbot, dlg, _CPU_TEXT)

    assert _ticked(dlg) == {"glm-ocr:q8_0", "qwen3:4b", "mistral:latest"}


@pytest.mark.use_case("Hardware_Profile_Override")
def test_user_unticking_an_auto_tick_is_respected_after_profile_change(qtbot) -> None:
    """
    Given  an automatic tick the user removed again
    When   the profile changes to one that recommends the same model
    Then   the model is not ticked again
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    dlg._model_items[DEFAULT_OCR_MODEL].setCheckState(0, Qt.CheckState.Unchecked)

    _choose_computer(qtbot, dlg, _16GB_TEXT)

    assert DEFAULT_OCR_MODEL not in _ticked(dlg)  # 16 GB recommends it as well
    assert "gpt-oss:20b" in _ticked(dlg)


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_too_large_tooltip_is_cleared_when_model_fits_again(qtbot) -> None:
    """
    Given  gpt-oss shown as too large on 8 GB (with an explanatory tooltip)
    When   the profile changes to 16 GB, where it is the best choice
    Then   the tooltip of the suitability column is gone
    """
    dlg = _make_dialog(qtbot)
    dlg._on_detection_done(_detection_results(dlg, set()))
    row = dlg._model_items["gpt-oss:20b"]
    assert "14 GB" in row.toolTip(1)

    _choose_computer(qtbot, dlg, _16GB_TEXT)

    assert row.toolTip(1) == ""
    assert row.text(1) == "★ Best choice"


@pytest.mark.use_case("Model_Category_Guidance")
def test_custom_tag_without_suffix_maps_onto_the_catalog_row(qtbot) -> None:
    """
    Given  the catalog row "deepseek-ocr:latest"
    When   the user adds "deepseek-ocr" by name
    Then   no duplicate appears in "Other models"
    """
    dlg = _make_dialog(qtbot)
    before = len(dlg._model_items)
    dlg._ollama_custom_input.setText("deepseek-ocr")
    dlg._on_add_custom_ollama_model()
    assert len(dlg._model_items) == before
    assert dlg._group_items["other"].childCount() == 0


@pytest.mark.use_case("Model_Category_Guidance")
def test_detection_runs_again_when_requested_during_a_running_check(qtbot) -> None:
    """
    Given  an installed-model check that is still running
    When   another check is requested (a pull just finished)
    Then   a second check runs afterwards instead of being dropped
    """
    service = _StubService()
    service.status_gate = threading.Event()
    dlg = _make_dialog(qtbot, service=service)

    dlg._start_detection()
    qtbot.waitUntil(lambda: service.status_calls == 1, timeout=5000)
    dlg._start_detection()  # requested while the first check is blocked
    assert dlg._detect_again is True

    service.status_gate.set()
    qtbot.waitUntil(lambda: service.status_calls == 2, timeout=5000)
    qtbot.waitUntil(lambda: not dlg._detect_running, timeout=5000)


# ── real service + fake Ollama server: the download → re-check → show flow ──


class _FakeOllamaServer:
    """In-memory Ollama server: installed models and per-model capabilities."""

    def __init__(self) -> None:
        self.installed: dict[str, int] = {}
        self.capabilities: dict[str, set[str]] = {}


@pytest.fixture
def fake_ollama(monkeypatch):
    """Patch the infrastructure seams of the *real* application service."""
    import teachers_teammate.application.service as service_module  # noqa: PLC0415
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    server = _FakeOllamaServer()

    class _Client:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def list_models_with_size(self) -> list[tuple[str, int]]:
            return list(server.installed.items())

        def model_capabilities(self, tag: str):
            caps = server.capabilities.get(tag)
            return frozenset(caps) if caps is not None else None

    monkeypatch.setattr(
        service_module, "list_provider_models", lambda _provider, **_kw: list(server.installed)
    )
    monkeypatch.setattr(ollama_utils, "OllamaClient", _Client)
    return server


def _real_service():
    from teachers_teammate.application.service import ProcessingApplicationService  # noqa: PLC0415

    return ProcessingApplicationService()


@pytest.mark.use_case("Model_Category_Guidance")
def test_finished_pull_shows_model_as_downloaded_without_waiting_for_cache(
    qtbot, fake_ollama
) -> None:
    """
    Given  the real service that cached an empty installed-model list
    When   a pull finishes and the dialog checks again
    Then   the model shows as downloaded at once (review finding #1: the stale
           5-minute cache used to hide it, so it was offered for download again)
    """
    dlg = _make_dialog(qtbot, service=_real_service())
    dlg._start_detection()
    qtbot.waitUntil(lambda: dlg._installed_ready, timeout=5000)
    assert dlg._model_items[DEFAULT_OCR_MODEL].text(3) == "—"

    fake_ollama.installed[DEFAULT_OCR_MODEL] = 6_000_000_000  # the pull arrived
    dlg._on_ollama_pull_done([])

    qtbot.waitUntil(lambda: dlg._model_items[DEFAULT_OCR_MODEL].text(3) == "✓", timeout=5000)
    # Decimal GB, like the catalog sizes shown before the download.
    assert dlg._model_items[DEFAULT_OCR_MODEL].text(4) == "6.0 GB"


@pytest.mark.use_case("Model_Category_Guidance")
def test_pulled_custom_models_stay_in_other_with_their_abilities(
    qtbot, monkeypatch, fake_ollama
) -> None:
    """
    Given  three custom models ticked for download (two that read images, one text-only)
    When   the pulls run one after the other through the real pull pipeline
    Then   all three stay in "Other models" and each row says what the model can do
    """
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    import teachers_teammate.gui._addon_installer_dialog as installer  # noqa: PLC0415

    class _FakePullThread(QObject):
        progress = Signal(str, int, int)
        finished_ok = Signal(str)
        finished_err = Signal(str)

        def __init__(self, _url: str, model: str) -> None:
            super().__init__()
            self._model = model

        def start(self) -> None:
            fake_ollama.installed[self._model] = 1_000_000
            self.finished_ok.emit(self._model)

    monkeypatch.setattr(installer, "_OllamaPullThread", _FakePullThread)
    fake_ollama.capabilities = {
        "reader-a:latest": {"vision", "completion"},
        "writer-b:latest": {"completion"},
        "reader-c:latest": {"vision"},
    }
    dlg = _make_dialog(qtbot, service=_real_service())
    for tag in fake_ollama.capabilities:
        dlg._ollama_custom_input.setText(tag)
        dlg._on_add_custom_ollama_model()

    dlg._on_download_install()

    qtbot.waitUntil(
        lambda: (
            dlg._model_items["reader-a:latest"].text(1) == "Reads scans and writes"
            and dlg._model_items["reader-c:latest"].text(1) == "Reads scans and writes"
            and dlg._model_items["writer-b:latest"].text(1) == "Writes only"
        ),
        timeout=5000,
    )
    assert dlg._group_items["other"].childCount() == 3


# ── tab constants ───────────────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_tab_constants_point_at_the_matching_tabs(qtbot) -> None:
    """
    Given  the Downloads dialog (the Ollama tab was moved to the front)
    When   the tab constants are used to look up tab titles
    Then   each constant names the tab it claims (callers open tabs by constant)
    """
    from PySide6.QtWidgets import QTabWidget  # noqa: PLC0415

    dlg = _make_dialog(qtbot)
    tabs = dlg.findChild(QTabWidget)
    assert tabs is not None
    assert tabs.tabText(DownloadsDialog.TAB_OLLAMA) == "Ollama Models"
    assert tabs.tabText(DownloadsDialog.TAB_OCR_ENGINES) == "OCR Engines"
    assert tabs.tabText(DownloadsDialog.TAB_PROVIDERS) == "LLM Providers"
    assert tabs.tabText(DownloadsDialog.TAB_SPACY) == "spaCy"
    assert tabs.tabText(DownloadsDialog.TAB_GPU) == "GPU Add-ons"
