"""Unit tests for teachers_teammate.domain.run_summary and the wording of a finished run."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.run_summary import RunStatus, RunSummary


@pytest.mark.parametrize(
    ("summary", "status", "exit_code"),
    [
        (RunSummary(), RunStatus.DONE, 0),
        (RunSummary(succeeded=("a",), skipped=("b",)), RunStatus.DONE, 0),
        (RunSummary(succeeded=("a",), stopped=("b",)), RunStatus.STOPPED, 0),
        (RunSummary(not_started=("a",)), RunStatus.STOPPED, 0),
        (RunSummary(failed=("a",)), RunStatus.FAILED, 1),
        (RunSummary(failed=("a",), stopped=("b",), not_started=("c",)), RunStatus.FAILED, 1),
        (RunSummary(error="folder refused"), RunStatus.FAILED, 1),
        (RunSummary(error="boom", not_started=("a",)), RunStatus.FAILED, 1),
    ],
)
def test_status_and_exit_code(summary: RunSummary, status: RunStatus, exit_code: int) -> None:
    """
    Given  a run summary
    When   its status and exit code are read
    Then   failed beats stopped beats done, and only a failure gives exit code 1
    """
    assert summary.status is status
    assert summary.exit_code == exit_code
