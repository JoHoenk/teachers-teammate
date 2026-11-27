"""Unit tests for teachers_teammate.application.ollama_server — status, start-up and memory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import threading
import time
from unittest.mock import patch

import pytest

from teachers_teammate.application.ollama_server import (
    OllamaServerService,
    OllamaStatus,
    ollama_client,
)
from teachers_teammate.domain.ollama_discovery import OllamaState
from teachers_teammate.domain.ollama_snapshot import SNAPSHOT_TTL_S
from teachers_teammate.exceptions import OllamaUnreachableError
from teachers_teammate.infrastructure.ollama_utils import OllamaClient

from ..fakes.fake_ollama import PULL_DIGEST, FakeOllama

_INSTALL = "teachers_teammate.infrastructure.ollama_install"

#: A digest a tag answers after it was pulled again; the one the fake reports otherwise is
#: :data:`~tests.fakes.fake_ollama.PULL_DIGEST`.
_REPULLED_DIGEST = "sha256:" + "2" * 64


class _Clock:
    """A clock a test moves by hand: the service measures the age of an answer with it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the time the test last set."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Let *seconds* pass."""
        self.now += seconds


class _CountingFactory:
    """The app's client factory, remembering the timeout of every client it built."""

    def __init__(self) -> None:
        self.timeouts: list[float | None] = []

    def __call__(
        self, url: str, *, cancel_event: threading.Event | None = None, timeout: float | None = None
    ) -> OllamaClient:
        """Build the real client of *url* and note its timeout."""
        self.timeouts.append(timeout)
        return ollama_client(url, cancel_event=cancel_event, timeout=timeout)


@pytest.fixture(name="fake")
def _fake() -> Iterator[FakeOllama]:
    """The shared fake Ollama server, stopped when the test is over."""
    with FakeOllama() as server:
        yield server


@pytest.fixture(name="clock")
def _clock() -> _Clock:
    """The clock of one test, which only the test moves."""
    return _Clock()


@pytest.fixture(name="svc")
def _svc(clock: _Clock) -> OllamaServerService:
    """A service that talks to the fake through the real client, on the clock of the test."""
    return OllamaServerService(clock=clock)


@pytest.mark.unit
def test_ollama_status_reports_the_version_and_the_models_it_has() -> None:
    """
    Given  a server that answers with a version and two models
    When   ollama_status is asked
    Then   it says running, with the version and the count, and offers no button
    """
    from teachers_teammate.infrastructure.ollama_discovery import ServerInfo  # noqa: PLC0415

    svc = OllamaServerService()
    info = ServerInfo(version="0.12.3", model_count=2)

    with patch("teachers_teammate.infrastructure.ollama_discovery.probe_server", return_value=info):
        status = svc.ollama_status("http://127.0.0.1:11434")

    assert status.state is OllamaState.RUNNING
    assert (status.version, status.model_count, status.can_start) == ("0.12.3", 2, False)


@pytest.mark.unit
def test_ollama_status_looks_for_the_program_only_when_nothing_answered() -> None:
    """
    Given  a local address that does not answer, and an installed program
    When   ollama_status is asked
    Then   it says not running and offers to start it, and the version stays empty
    """
    svc = OllamaServerService()

    with (
        patch("teachers_teammate.infrastructure.ollama_discovery.probe_server", return_value=None),
        patch(f"{_INSTALL}.find_ollama_program", return_value=Path("/usr/bin/ollama")),
        patch(f"{_INSTALL}.can_start_ollama", return_value=True),
    ):
        status = svc.ollama_status("http://127.0.0.1:11434")

    assert status.state is OllamaState.NOT_RUNNING
    assert (status.version, status.model_count, status.can_start) == ("", None, True)


@pytest.mark.unit
def test_start_ollama_does_nothing_without_a_program() -> None:
    """
    Given  a computer where no Ollama program was found
    When   start_ollama is called
    Then   nothing is started, so no error is raised for a teacher who has none
    """
    svc = OllamaServerService()

    with (
        patch(f"{_INSTALL}.find_ollama_program", return_value=None),
        patch(f"{_INSTALL}.start_ollama") as start,
    ):
        svc.start_ollama()

    start.assert_not_called()


@pytest.mark.unit
def test_start_ollama_starts_the_program_where_the_platform_allows_it() -> None:
    """
    Given  an installed Ollama program on a platform where the app may start it
    When   start_ollama is called
    Then   the program is started
    """
    svc = OllamaServerService()
    program = Path("/Applications/Ollama.app")

    with (
        patch(f"{_INSTALL}.find_ollama_program", return_value=program),
        patch(f"{_INSTALL}.can_start_ollama", return_value=True),
        patch(f"{_INSTALL}.start_ollama") as start,
    ):
        svc.start_ollama()

    start.assert_called_once_with(program)


# ── What the service remembers of a server ───────────────────────────────────


def _asked(fake: FakeOllama, path: str) -> int:
    """Return how often *path* was asked of *fake*."""
    return fake.paths().count(path)


@pytest.mark.unit
def test_a_second_look_within_the_lifetime_asks_nothing(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a server that answered what it has installed
    When   the same address is asked again, the answer being younger than the service allows
    Then   the server is not asked a second time, and both answers are the same one
    """
    fake.install("qwen3:8b")

    first = svc.snapshot(fake.url)
    second = svc.snapshot(fake.url)

    assert second is first
    assert _asked(fake, "/api/tags") == 1
    assert (first.tags, first.version) == (["qwen3:8b"], "0.12.3")


@pytest.mark.unit
def test_a_look_after_the_lifetime_asks_the_server_again(
    fake: FakeOllama, svc: OllamaServerService, clock: _Clock
) -> None:
    """
    Given  a held answer
    When   more time passed than the service keeps an answer for, and a model was pulled in a
           terminal meanwhile
    Then   the server is asked again and the new model is there
    """
    fake.install("qwen3:8b")
    svc.snapshot(fake.url)
    fake.install("gemma3:12b")

    clock.advance(SNAPSHOT_TTL_S + 1)

    assert svc.snapshot(fake.url).tags == ["qwen3:8b", "gemma3:12b"]
    assert _asked(fake, "/api/tags") == 2


@pytest.mark.unit
def test_asking_for_no_age_asks_the_server_every_time(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a held answer
    When   a snapshot is taken with ``max_age=0``, as a run's own check does
    Then   the server is asked again although the answer is fresh
    """
    fake.install("qwen3:8b")

    svc.snapshot(fake.url)
    svc.snapshot(fake.url, max_age=0)

    assert _asked(fake, "/api/tags") == 2


@pytest.mark.unit
def test_a_server_that_did_not_answer_replaces_what_was_held(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a server whose answer is held
    When   it is gone and the service asks it again
    Then   the held answer is the unreachable one, so a reader can tell "gone" from "not asked"
    """
    fake.install("qwen3:8b")
    svc.snapshot(fake.url)

    fake.close()
    gone = svc.snapshot(fake.url, max_age=0)

    assert gone.tags == []
    assert gone.unreachable
    assert svc.cached_snapshot(fake.url) is gone


@pytest.mark.unit
def test_an_answer_with_no_model_is_held_but_asked_again(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a server whose models were all removed since its answer was held
    When   it is asked again, and once more within the lifetime
    Then   the empty answer replaces the old list, and the next look asks again, so a model
           pulled in a terminal shows at once
    """
    fake.install("qwen3:8b")
    svc.snapshot(fake.url)
    fake.uninstall("qwen3:8b")

    empty = svc.snapshot(fake.url, max_age=0)
    svc.snapshot(fake.url)

    assert (empty.tags, empty.unreachable) == ([], "")
    assert svc.cached_snapshot(fake.url).tags == []
    assert _asked(fake, "/api/tags") == 3


@pytest.mark.unit
def test_a_held_answer_is_read_without_asking_the_server(
    fake: FakeOllama, svc: OllamaServerService, clock: _Clock
) -> None:
    """
    Given  a held answer
    When   it is read back, once as it is and once with an age limit it has outgrown
    Then   neither asks the server, and the limit hides the one that is too old to it
    """
    fake.install("qwen3:8b")
    svc.snapshot(fake.url)
    asked = fake.paths()

    clock.advance(60)

    assert svc.cached_snapshot(fake.url).tags == ["qwen3:8b"]
    assert svc.cached_snapshot(fake.url, max_age=30) is None
    assert fake.paths() == asked


@pytest.mark.unit
def test_a_pull_forgets_the_server(fake: FakeOllama, svc: OllamaServerService) -> None:
    """
    Given  a held answer
    When   a model is pulled from the server
    Then   nothing is held for it any more, so the next look lists what the pull installed
    """
    fake.install("qwen3:8b")
    svc.snapshot(fake.url)

    svc.pull_ollama_model(fake.url, "gemma3:12b")

    assert svc.cached_snapshot(fake.url) is None
    assert svc.snapshot(fake.url).tags == ["qwen3:8b", "gemma3:12b"]


@pytest.mark.unit
def test_a_pull_that_failed_forgets_the_server_too(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a held answer
    When   a pull fails (a stopped one ends the same way)
    Then   nothing is held for it any more, because a failed pull may have installed part of it
    """
    from teachers_teammate.exceptions import OCRError  # noqa: PLC0415

    fake.install("qwen3:8b")
    fake.fail_pull.add("broken:latest")
    svc.snapshot(fake.url)

    with pytest.raises(OCRError):
        svc.pull_ollama_model(fake.url, "broken:latest")

    assert svc.cached_snapshot(fake.url) is None


@pytest.mark.unit
def test_an_answer_asked_for_before_a_pull_is_not_kept(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a request for the server's models that is still waiting
    When   a pull forgets the server before the answer arrives
    Then   that answer is not kept: it may list what the server had before the pull
    """
    fake.install("qwen3:8b")
    fake.answers_tags_after(1.0)
    asking = threading.Thread(target=lambda: svc.snapshot(fake.url), daemon=True)

    asking.start()
    try:
        deadline = time.monotonic() + 5
        while _asked(fake, "/api/tags") < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        svc.forget(fake.url)
    finally:
        asking.join(timeout=10)

    assert not asking.is_alive()
    assert svc.cached_snapshot(fake.url) is None


@pytest.mark.unit
def test_an_unreachable_server_costs_one_request_for_its_capabilities(fake: FakeOllama) -> None:
    """
    Given  a server that is gone
    When   a model's capabilities are asked for with a short timeout
    Then   the service says it is unreachable after the one request for its models, which ran
           with that timeout
    """
    factory = _CountingFactory()
    svc = OllamaServerService(client_factory=factory)
    fake.close()

    with pytest.raises(OllamaUnreachableError):
        svc.capabilities(fake.url, "qwen3:8b", timeout=1.5)

    assert factory.timeouts == [1.5]


@pytest.mark.unit
def test_a_model_is_asked_what_it_can_do_once_per_digest(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a server holding a model that can read images
    When   its capabilities are asked for twice
    Then   the server is asked once and both answers say the same
    """
    fake.install("qwen3:8b", ("completion", "vision"))

    first = svc.capabilities(fake.url, "qwen3:8b", timeout=5)
    second = svc.capabilities(fake.url, "qwen3:8b", timeout=5)

    assert first == second == frozenset({"completion", "vision"})
    assert _asked(fake, "/api/show") == 1


@pytest.mark.unit
def test_a_tag_pulled_again_is_asked_again(fake: FakeOllama, svc: OllamaServerService) -> None:
    """
    Given  a tag whose capabilities are held
    When   the same tag is pulled again, so the server reports another digest
    Then   the model is described again, because a tag is a name that can be re-pointed
    """
    fake.install("qwen3:8b")
    svc.capabilities(fake.url, "qwen3:8b", timeout=5)

    fake.install("qwen3:8b", ("completion", "vision"), digest=_REPULLED_DIGEST)
    svc.forget(fake.url)  # what the pull itself does

    assert svc.capabilities(fake.url, "qwen3:8b", timeout=5) == frozenset({"completion", "vision"})
    assert _asked(fake, "/api/show") == 2


@pytest.mark.unit
def test_a_capability_answer_the_server_cannot_give_is_not_held(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a model the server reports neither capabilities nor families for
    When   its capabilities are asked for twice
    Then   the server is asked both times and says nothing each time
    """
    fake.install("qwen3:8b", (), families=())

    assert svc.capabilities(fake.url, "qwen3:8b", timeout=5) is None
    assert svc.capabilities(fake.url, "qwen3:8b", timeout=5) is None
    assert _asked(fake, "/api/show") == 2


@pytest.mark.unit
def test_a_held_answer_is_read_while_the_server_is_being_asked(
    fake: FakeOllama, svc: OllamaServerService
) -> None:
    """
    Given  a held answer, and a server that takes half a second to list its models
    When   a background thread asks it again and the GUI thread reads what is held meanwhile
    Then   the read returns while the request still waits: the lock guards the answers,
           never the request
    """
    fake.install("qwen3:8b")
    svc.snapshot(fake.url)
    fake.answers_tags_after(2.0)
    asking = threading.Thread(target=lambda: svc.snapshot(fake.url, max_age=0), daemon=True)

    asking.start()
    try:
        deadline = time.monotonic() + 5
        while _asked(fake, "/api/tags") < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        held = svc.cached_snapshot(fake.url)
        still_asking = asking.is_alive()
    finally:
        asking.join(timeout=10)

    assert held is not None and held.tags == ["qwen3:8b"]
    assert still_asking
    assert not asking.is_alive()
