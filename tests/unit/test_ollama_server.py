"""Unit tests for teachers_teammate.application.ollama_server — status and start-up."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from teachers_teammate.application.ollama_server import OllamaServerService, OllamaStatus
from teachers_teammate.domain.ollama_discovery import OllamaState

_INSTALL = "teachers_teammate.infrastructure.ollama_install"


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
