"""The log of one run: one JSON line per event, appended as the run goes."""

from __future__ import annotations

import json
from pathlib import Path

from tools.evaluation.run import run_log


def test_every_event_is_one_json_line_with_its_time(tmp_path: Path) -> None:
    """
    Given  a log with a fixed clock
    When   two events are written, the second with fields
    Then   the file holds one JSON line per event, each with the time it was written, and the
           fields of the event beside them
    """
    log = run_log.RunLog(tmp_path / "run.log", now=lambda: "2026-11-09T18:00:00+00:00")

    log.write("session", model="gemma4:e2b", repeat=1)
    log.write("finished", sessions=1, rows=2)

    lines = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    assert lines == [
        {
            "time": "2026-11-09T18:00:00+00:00",
            "event": "session",
            "model": "gemma4:e2b",
            "repeat": 1,
        },
        {"time": "2026-11-09T18:00:00+00:00", "event": "finished", "sessions": 1, "rows": 2},
    ]


def test_the_directory_of_the_log_is_created(tmp_path: Path) -> None:
    """
    Given  a log path in a directory that does not exist yet
    When   the log is opened and one event is written
    Then   the directory is created and the line is there, so a run does not fail on its log
    """
    path = tmp_path / "logs" / "night" / "run.log"

    run_log.RunLog(path).write("finished")

    assert [
        json.loads(line)["event"] for line in path.read_text(encoding="utf-8").splitlines()
    ] == ["finished"]


def test_the_default_clock_is_utc_to_the_second() -> None:
    """
    Given  the default clock
    When   it is read
    Then   the time is ISO 8601 in UTC to the second, so two machines' logs line up
    """
    stamp = run_log.utc_now()

    assert stamp.endswith("+00:00")
    assert len(stamp) == len("2026-11-09T18:00:00+00:00")
