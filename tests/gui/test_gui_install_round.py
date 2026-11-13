"""GUI tests for :class:`InstallRound`: the order of an install round and what it reports."""
# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

import threading
from typing import Any, ClassVar

import pytest
from PySide6.QtCore import QObject, QThread, Signal

from teachers_teammate.gui import _addon_installer_dialog, _install_round
from teachers_teammate.gui._install_round import InstallRound

from ._service_stubs import RecommendationServiceStub


class _Script:
    """What the fake threads do, and the order in which they were started."""

    order: ClassVar[list[str]] = []
    fail_with: ClassVar[str] = ""
    fail_item: ClassVar[str] = ""  # "" = every step fails


def _fails(item: str) -> bool:
    """True when the script says this item fails."""
    return bool(_Script.fail_with) and _Script.fail_item in ("", item)


class _Service(RecommendationServiceStub):
    """Stub application service: records what the round asks of it."""

    def __init__(self) -> None:
        super().__init__()
        self.installed_packages: list[list[str]] = []

    def install_packages(self, packages: list[str], progress_cb: Any) -> None:
        self.installed_packages.append(list(packages))
        progress_cb("installing…")


class _Manager(QObject):
    """Stand-in for the main window's download manager."""

    progress = Signal(str, int, int)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.is_active = False
        self.queued: list[str] = []
        self.current_model = ""
        self.calls: list[tuple[str, str, list[str]]] = []
        self.aborted = False

    def start(self, url: str, model: str, queue: Any = ()) -> None:
        self.calls.append((url, model, list(queue)))
        self.current_model = model
        self.queued = list(queue)
        self.is_active = True

    def abort(self) -> None:
        self.aborted = True


class _FakePipThread(QObject):
    """Reports the package list back at once, as the real pip thread does."""

    log_line = Signal(str)
    finished_ok = Signal(list)
    finished_err = Signal(str)

    def __init__(self, _service: Any, packages: list[str]) -> None:
        super().__init__()
        self._packages = packages

    def start(self) -> None:
        item = "pip:" + ",".join(self._packages)
        _Script.order.append(item)
        if _fails(item):
            self.finished_err.emit(_Script.fail_with)
            return
        self.finished_ok.emit(self._packages)


class _FakeSpacyThread(QObject):
    """Reports a downloaded spaCy model at once."""

    log_line = Signal(str)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, model: str, _target: Any) -> None:
        super().__init__()
        self._model = model

    def start(self) -> None:
        item = f"spacy:{self._model}"
        _Script.order.append(item)
        if _fails(item):
            self.finished_err.emit(_Script.fail_with)
            return
        self.finished_ok.emit(self._model)


class _FakePullThread(QObject):
    """Reports a pulled Ollama model at once."""

    progress = Signal(str, int, int)
    finished_ok = Signal(str)
    finished_err = Signal(str)

    def __init__(self, _url: str, model: str) -> None:
        super().__init__()
        self._model = model

    def start(self) -> None:
        _Script.order.append(self._model)
        if _fails(self._model):
            self.finished_err.emit(_Script.fail_with)
            return
        self.finished_ok.emit(self._model)


class _BlockingPipThread(QThread):
    """A pip install that keeps running until the test releases it."""

    log_line = Signal(str)
    finished_ok = Signal(list)
    finished_err = Signal(str)

    def __init__(self, _service: Any, _packages: list[str]) -> None:
        super().__init__()
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait(5)


@pytest.fixture
def fake_threads(monkeypatch) -> type[_Script]:
    """Replace the three real install threads with ones that report at once."""
    _Script.order = []
    _Script.fail_with = ""
    _Script.fail_item = ""
    monkeypatch.setattr(_install_round, "_PipInstallThread", _FakePipThread)
    monkeypatch.setattr(_addon_installer_dialog, "_ModelDownloadThread", _FakeSpacyThread)
    monkeypatch.setattr(_addon_installer_dialog, "_OllamaPullThread", _FakePullThread)
    return _Script


def _round(manager: Any = None) -> tuple[InstallRound, _Service]:
    """Build a round on a stub service, with the manager if the test wants one."""
    service = _Service()
    return InstallRound(service, manager), service


def _record(round_: InstallRound) -> dict[str, list[Any]]:
    """Collect everything the round reports."""
    seen: dict[str, list[Any]] = {
        "log": [],
        "started": [],
        "done": [],
        "progress": [],
        "finished": [],
        "failed": [],
    }
    round_.log_line.connect(seen["log"].append)
    round_.item_started.connect(seen["started"].append)
    round_.item_done.connect(seen["done"].append)
    round_.pull_progress.connect(lambda s, d, t: seen["progress"].append((s, d, t)))
    round_.finished.connect(seen["finished"].append)
    round_.failed.connect(lambda item, msg: seen["failed"].append((item, msg)))
    return seen


# ── the order ────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_round_installs_pip_then_spacy_then_pulls(qtbot, fake_threads) -> None:
    """
    Given  a round with a package, a spaCy model and two Ollama models
    When   it starts
    Then   pip runs first, then spaCy, then the pulls — in that order
    """
    round_, _service = _round()
    seen = _record(round_)

    assert round_.start(
        packages=["spacy"],
        spacy_models=["en_core_web_sm"],
        ollama_models=["a:1b", "b:1b"],
        ollama_url="http://127.0.0.1:11434",
    )

    assert fake_threads.order == ["pip:spacy", "spacy:en_core_web_sm", "a:1b", "b:1b"]
    assert seen["started"] == ["package:spacy", "spacy:en_core_web_sm", "a:1b", "b:1b"]
    assert seen["done"] == ["package:spacy", "spacy:en_core_web_sm", "a:1b", "b:1b"]
    assert seen["finished"] == [True]
    assert seen["failed"] == []


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_the_packages_dir_is_injected_after_pip_and_after_spacy(qtbot, fake_threads) -> None:
    """
    Given  a round that installs a package and then a spaCy model
    When   each step finishes
    Then   the add-on packages directory is put on sys.path both times, so the new
           package is importable before the next step uses it
    """
    round_, service = _round()
    injected: list[bool] = []
    original = service.inject_addon_packages_dir

    def _count() -> None:
        injected.append(True)
        original()

    service.inject_addon_packages_dir = _count  # ty: ignore[invalid-assignment]

    round_.start(packages=["spacy"], spacy_models=["en_core_web_sm"])

    assert injected == [True, True]


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_nothing_to_do_starts_nothing(qtbot, fake_threads) -> None:
    """
    Given  a round with no packages, no spaCy models and no Ollama models
    When   it is started
    Then   it says so and reports nothing
    """
    round_, _service = _round()
    seen = _record(round_)

    assert round_.start() is False
    assert round_.running() is False
    assert seen["finished"] == []
    assert fake_threads.order == []


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_second_start_while_running_is_refused(qtbot, fake_threads) -> None:
    """
    Given  a round that is already running
    When   it is started again
    Then   the second call is refused, so one round never overlaps another
    """
    round_, _service = _round(_Manager())
    round_.start(ollama_models=["a:1b"])

    assert round_.running() is True
    assert round_.start(ollama_models=["b:1b"]) is False


# ── busy(): only pip and spaCy keep a window open ────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_busy_only_while_pip_or_spacy_runs(qtbot, monkeypatch) -> None:
    """
    Given  a round whose pip install is still running
    When   busy() is asked, and again once the install has finished
    Then   it is True while pip runs and False afterwards — a pull never blocks closing
    """
    monkeypatch.setattr(_install_round, "_PipInstallThread", _BlockingPipThread)
    round_, _service = _round()
    assert round_.busy() is False

    round_.start(packages=["spacy"])
    thread = round_._thread
    assert isinstance(thread, _BlockingPipThread)
    try:
        assert round_.busy() is True
    finally:
        thread.release.set()
        thread.wait(5000)

    assert round_.busy() is False


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_pull_does_not_keep_the_window_open(qtbot) -> None:
    """
    Given  a round that only pulls Ollama models through the manager
    When   busy() is asked while the pull runs
    Then   it is False: the pull goes on in the main window after the window closed
    """
    manager = _Manager()
    round_, _service = _round(manager)

    round_.start(ollama_models=["a:1b"], ollama_url="http://127.0.0.1:11434")

    assert round_.running() is True
    assert round_.busy() is False


# ── the pull manager ─────────────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_the_whole_queue_goes_to_the_manager(qtbot) -> None:
    """
    Given  a round of two models and the main window's manager
    When   the round starts
    Then   the manager gets the whole queue, so the models after the first are still
           pulled when the window is closed
    """
    manager = _Manager()
    round_, _service = _round(manager)
    seen = _record(round_)

    round_.start(ollama_models=["a:1b", "b:1b"], ollama_url="http://127.0.0.1:11434")

    assert manager.calls == [("http://127.0.0.1:11434", "a:1b", ["b:1b"])]
    assert seen["started"] == ["a:1b"]

    manager.finished.emit("a:1b")  # the manager goes on with b:1b by itself
    assert seen["done"] == ["a:1b"]
    assert seen["started"] == ["a:1b", "b:1b"]
    assert seen["finished"] == []

    manager.queued = []
    manager.finished.emit("b:1b")
    assert seen["done"] == ["a:1b", "b:1b"]
    assert seen["finished"] == [True]


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_pull_survives_detach(qtbot) -> None:
    """
    Given  a round following a pull through the manager
    When   the window closes (detach) and the pull finishes afterwards
    Then   the round no longer reports it, and the manager is not asked to abort
    """
    manager = _Manager()
    round_, _service = _round(manager)
    seen = _record(round_)
    round_.start(ollama_models=["a:1b"], ollama_url="http://127.0.0.1:11434")

    round_.detach()
    manager.finished.emit("a:1b")

    assert manager.aborted is False
    assert seen["done"] == []
    assert seen["finished"] == []


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_abort_stops_the_pull(qtbot) -> None:
    """
    Given  a round pulling through the manager
    When   abort() is called
    Then   the manager is asked to abort the pull
    """
    manager = _Manager()
    round_, _service = _round(manager)
    round_.start(ollama_models=["a:1b"], ollama_url="http://127.0.0.1:11434")

    round_.abort()

    assert manager.aborted is True


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_running_pull_can_be_followed(qtbot) -> None:
    """
    Given  a manager that is already pulling a model
    When   the round is asked to follow it
    Then   it names the model and reports the pull's progress and end
    """
    manager = _Manager()
    manager.is_active = True
    manager.current_model = "a:1b"
    round_, _service = _round(manager)
    seen = _record(round_)

    assert round_.follow_running_pull() == "a:1b"
    assert round_.follow_running_pull() == ""  # already following

    manager.progress.emit("pulling", 5, 10)
    manager.finished.emit("a:1b")

    assert seen["progress"] == [("pulling", 5, 10)]
    assert seen["done"] == ["a:1b"]
    assert seen["finished"] == [True]


# ── failures ─────────────────────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_failure_stops_the_round_and_names_the_item(qtbot, fake_threads) -> None:
    """
    Given  a round of a package, a spaCy model and a pull, and the spaCy download fails
    When   the round runs
    Then   it stops there, names the spaCy model as the failed item, and reports that
           the pip install did arrive
    """
    fake_threads.fail_with = "no network"
    fake_threads.fail_item = "spacy:en_core_web_sm"
    round_, _service = _round()
    seen = _record(round_)

    round_.start(
        packages=["spacy"],
        spacy_models=["en_core_web_sm"],
        ollama_models=["a:1b"],
        ollama_url="http://127.0.0.1:11434",
    )

    assert seen["failed"] == [("spacy:en_core_web_sm", "no network")]
    assert seen["finished"] == [True]  # the pip install did arrive
    assert fake_threads.order == ["pip:spacy", "spacy:en_core_web_sm"]
    assert round_.running() is False


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_a_failed_pip_install_names_the_first_package(qtbot, fake_threads) -> None:
    """
    Given  a round whose pip install fails
    When   it runs
    Then   the failure names the first package of the batch (pip reports one error for
           the whole command) and the round reports that nothing arrived
    """
    fake_threads.fail_with = "pip is not available"
    round_, _service = _round()
    seen = _record(round_)

    round_.start(packages=["spacy", "pytesseract"])

    assert seen["failed"] == [("package:spacy", "pip is not available")]
    assert seen["finished"] == [False]


# ── without a manager ────────────────────────────────────────────────────────


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_without_a_manager_the_round_pulls_by_itself(qtbot, fake_threads) -> None:
    """
    Given  a round with no pull manager (tests, Benchmark)
    When   it pulls two models
    Then   it runs the pulls itself, one after the other, and reports each arrival
    """
    round_, service = _round()
    seen = _record(round_)

    round_.start(ollama_models=["a:1b", "b:1b"], ollama_url="http://127.0.0.1:11434")

    assert fake_threads.order == ["a:1b", "b:1b"]
    assert seen["done"] == ["a:1b", "b:1b"]
    assert seen["finished"] == [True]
    assert service.changed_urls == ["http://127.0.0.1:11434", "http://127.0.0.1:11434"]
