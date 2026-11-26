"""Running the frozen app: where it is, how Linux gets a display, what a failure prints."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

import pytest

from tools.build import run_bundle


class _Run:
    """A stand-in for ``subprocess.run``: records the call and answers with an exit code."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.argv: list[str] = []
        self.env: dict[str, str] = {}

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.argv = argv
        self.env = kwargs["env"]
        return subprocess.CompletedProcess(argv, self.code)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux", "dist/teachers-teammate/teachers-teammate"),
        ("win32", "dist/teachers-teammate/teachers-teammate.exe"),
        ("darwin", "dist/teachers-teammate.app/Contents/MacOS/teachers-teammate"),
    ],
)
def test_the_executable_is_where_each_system_builds_it(platform: str, expected: str) -> None:
    """
    Given  a system
    When   the executable is asked for
    Then   it is the path the build leaves on that system
    """
    assert run_bundle.executable(platform, Path("root")) == Path("root") / expected


def test_linux_without_a_display_runs_offscreen() -> None:
    """
    Given  Linux and the offscreen display
    When   the command is built
    Then   the app is started directly with the offscreen platform
    """
    argv, env = run_bundle.command("linux", Path("app"), ["--_smoke_test", "s.json"], "offscreen")

    assert argv == ["app", "--_smoke_test", "s.json"]
    assert env == {"QT_QPA_PLATFORM": "offscreen"}


def test_linux_with_xvfb_runs_on_a_virtual_display() -> None:
    """
    Given  Linux and the xvfb display
    When   the command is built
    Then   xvfb-run wraps the app and Qt is not forced offscreen
    """
    argv, env = run_bundle.command("linux", Path("app"), ["--x"], "xvfb")

    assert argv == ["xvfb-run", "-a", "app", "--x"]
    assert env == {}


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_other_systems_ignore_the_display(platform: str) -> None:
    """
    Given  Windows or macOS and a display setting
    When   the command is built
    Then   the app is started as it is, with no extra environment
    """
    assert run_bundle.command(platform, Path("app"), ["--x"], "xvfb") == (["app", "--x"], {})


def test_success_prints_nothing_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """
    Given  an app that exits 0
    When   it is run
    Then   the exit code is 0 and the result file is not printed
    """
    run = _Run(0)

    code = run_bundle.run_bundle(
        Path("r.json"), ["--x"], platform="linux", environ={"A": "1"}, run=run
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    assert run.env == {"A": "1", "QT_QPA_PLATFORM": "offscreen"}


def test_a_failure_prints_the_result_and_keeps_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  an app that exits 3 and left a result file
    When   it is run
    Then   the file is printed and the exit code is 3
    """
    result = tmp_path / "e2e.json"
    result.write_text('{"failed": "step 4"}', encoding="utf-8")

    code = run_bundle.run_bundle(result, [], platform="linux", environ={}, run=_Run(3))

    assert code == 3
    assert "step 4" in capsys.readouterr().out


def test_a_failure_without_a_result_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  an app that exits 1 and wrote no result (Qt aborted early)
    When   it is run
    Then   the output names the missing file
    """
    code = run_bundle.run_bundle(
        tmp_path / "none.json", [], platform="win32", environ={}, run=_Run(1)
    )

    assert code == 1
    assert "wrote no result" in capsys.readouterr().out


def test_the_arguments_after_the_separator_go_to_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a command line with our options and, after --, the app's own
    When   main runs
    Then   the app gets exactly those arguments, whatever they look like
    """
    seen: dict[str, Any] = {}

    def fake(result: Path, arguments: list[str], **kwargs: Any) -> int:
        seen.update(result=result, arguments=arguments, **kwargs)
        return 0

    monkeypatch.setattr(run_bundle, "run_bundle", fake)

    code = run_bundle.main(
        ["--result", "e.json", "--display", "xvfb", "--", "--_run_script", "w.py", "--tag", "v1"]
    )

    assert code == 0
    assert seen["arguments"] == ["--_run_script", "w.py", "--tag", "v1"]
    assert seen["display"] == "xvfb"
    assert seen["result"] == Path("e.json")
