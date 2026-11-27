"""Unit tests for teachers_teammate.interfaces."""

from __future__ import annotations

import logging

import pytest

from teachers_teammate.interfaces import (
    INPUT_KINDS,
    RUN_LOGGER,
    SUPPORTED_SUFFIXES,
    LoggingReporter,
)


def test_supported_suffixes_is_the_table_of_what_reads_each_one() -> None:
    """
    Given  the table naming what reads each input suffix
    When   the offered suffixes are read
    Then   they are exactly its keys, so a new format is one line in one place
    """
    assert SUPPORTED_SUFFIXES == frozenset(INPUT_KINDS)


def test_supported_suffixes_contains_all_accepted_extensions() -> None:
    """
    Given  the SUPPORTED_SUFFIXES constant defined in interfaces (the canonical source)
    When   its membership is checked
    Then   all accepted input extensions (.pdf, .png, .jpg, .jpeg, .txt) are present
    """
    assert {".pdf", ".png", ".jpg", ".jpeg", ".txt"} <= SUPPORTED_SUFFIXES


def test_the_logging_reporter_writes_each_line_at_its_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Given  a reporter that writes the pipeline's narrative to the log
    When   it reports a status, a warning and an error
    Then   the run's logger holds them at INFO, WARNING and ERROR, without the newline a
           console line carries
    """
    caplog.set_level(logging.INFO, logger=RUN_LOGGER)
    reporter = LoggingReporter()

    reporter.status("reading anna.png\n")
    reporter.warn("the page models are missing")
    reporter.error("the reader found no text")

    assert [(record.levelno, record.getMessage()) for record in caplog.records] == [
        (logging.INFO, "reading anna.png"),
        (logging.WARNING, "the page models are missing"),
        (logging.ERROR, "the reader found no text"),
    ]
