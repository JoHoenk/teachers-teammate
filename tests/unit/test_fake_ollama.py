"""The fake Ollama server, checked against the ``ollama`` client the app is locked to.

The walkthrough tests drive the real client, the real threads and the real pull stream against
:class:`~tests.fakes.fake_ollama.FakeOllama`.  This file is what keeps that fake honest: a bump
of ``ollama`` that changes a response model fails *here*, with a readable message, instead of
hanging in the walkthrough.

It cannot say anything about what a real server answers — only that the fake and the client
still agree.
"""

from __future__ import annotations

import ollama
from collections.abc import Iterator

import pytest

from teachers_teammate.infrastructure.ollama_utils import OllamaClient

from ..fakes.fake_ollama import PULL_FAILED, PULL_SUCCESS, FakeOllama


@pytest.fixture(name="fake")
def _fake() -> Iterator[FakeOllama]:
    """A fake server that stops answering when the test is over."""
    with FakeOllama() as server:
        yield server


def test_list_parses_the_installed_models(fake: FakeOllama) -> None:
    """
    Given  a server holding two models, one of them able to read images
    When   the app's client lists them
    Then   both tags come back, so the setup plan can tell what is already downloaded
    """
    fake.install("qwen3:8b")
    fake.install("qwen3-vl:8b", ("completion", "vision"))

    listed = ollama.Client(host=fake.url).list()

    assert [model.model for model in listed.models] == ["qwen3:8b", "qwen3-vl:8b"]
    assert all(model.size for model in listed.models)
    assert listed.models[1].details.families == ["clip"]


def test_show_gives_the_capabilities(fake: FakeOllama) -> None:
    """
    Given  an installed model that can read images
    When   the app asks the server to describe it
    Then   its capabilities say so, which is how a model outside the catalog is classified
    """
    fake.install("some-vision-model:latest", ("completion", "vision"))

    shown = ollama.Client(host=fake.url).show("some-vision-model:latest")

    assert shown.capabilities == ["completion", "vision"]


def test_show_of_an_unknown_model_is_a_404(fake: FakeOllama) -> None:
    """
    Given  a server that does not hold a tag
    When   the app asks the server to describe it
    Then   the client raises, which the app turns into "no information about this model"
    """
    with pytest.raises(ollama.ResponseError) as raised:
        ollama.Client(host=fake.url).show("not-installed:latest")

    assert raised.value.status_code == 404


def test_pull_streams_to_success_and_installs_the_tag(fake: FakeOllama) -> None:
    """
    Given  a server that does not hold a tag
    When   the app pulls it with the real client
    Then   the stream ends in "success" and the tag is listed afterwards
    """
    statuses = [part.status for part in ollama.Client(host=fake.url).pull("qwen3:8b", stream=True)]

    assert statuses[-1] == PULL_SUCCESS
    assert fake.pulled == ["qwen3:8b"]
    assert [model.model for model in ollama.Client(host=fake.url).list().models] == ["qwen3:8b"]


def test_the_apps_own_pull_reports_progress_and_installs_the_tag(fake: FakeOllama) -> None:
    """
    Given  a server that does not hold a tag
    When   the app's own pull runs, as the download thread runs it
    Then   it reports the bytes as they come and the tag is there afterwards
    """
    reported: list[tuple[str, float, float]] = []

    OllamaClient(fake.url).pull(
        "qwen3:8b", lambda status, done, total: reported.append((status, done, total))
    )

    assert fake.pulled == ["qwen3:8b"]
    assert reported[-1][0] == PULL_SUCCESS
    assert reported[-2][1:] == (1_000_000.0, 1_000_000.0)


def test_a_failing_tag_raises(fake: FakeOllama) -> None:
    """
    Given  a server that refuses to pull a tag
    When   the app pulls it
    Then   the client raises, so the Done page can say why the row failed
    """
    fake.fail_pull.add("broken:latest")

    with pytest.raises(ollama.ResponseError) as raised:
        list(ollama.Client(host=fake.url).pull("broken:latest", stream=True))

    assert PULL_FAILED in str(raised.value)
    assert fake.pulled == []


def test_the_apps_own_pull_turns_a_refusal_into_its_own_error(fake: FakeOllama) -> None:
    """
    Given  a server that refuses to pull a tag
    When   the app's own pull runs
    Then   it raises the app's error, which is what the download thread reports
    """
    from teachers_teammate.exceptions import OCRError  # noqa: PLC0415

    fake.fail_pull.add("broken:latest")

    with pytest.raises(OCRError) as raised:
        OllamaClient(fake.url).pull("broken:latest")

    assert PULL_FAILED in str(raised.value)


def test_the_fake_records_every_request_in_order(fake: FakeOllama) -> None:
    """
    Given  a server the app has asked for its models and for a description of one
    When   the requests are read back
    Then   they are there in order, with the JSON body the app sent
    """
    client = ollama.Client(host=fake.url)
    fake.install("qwen3:8b")
    client.list()
    client.show("qwen3:8b")

    assert fake.paths() == ["/api/tags", "/api/show"]
    assert fake.requests[1].method == "POST"
    assert fake.requests[1].body == {"model": "qwen3:8b"}
