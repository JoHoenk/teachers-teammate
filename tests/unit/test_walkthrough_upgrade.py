"""The walkthrough's upgrade mode: which steps it runs, and what it says without a folder.

``tests/e2e/walkthrough.py --after-upgrade <root>`` is what the installer tests run on the data an
earlier version left.  Checked here is the entry only — the steps themselves need the app, and run
in the three installer jobs.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.e2e import walkthrough


def test_the_upgrade_walk_runs_the_upgrade_steps() -> None:
    """
    Given  a walk with --after-upgrade, and one without it
    When   each names the steps it runs
    Then   the first runs the upgrade steps and the second plays teacher, so one file holds both
    """
    upgrade = walkthrough.Walk(
        root=Path(),
        service=None,  # ty: ignore[invalid-argument-type]
        after_upgrade=True,
    )
    teacher = walkthrough.Walk(
        root=Path(),
        service=None,  # ty: ignore[invalid-argument-type]
        after_upgrade=False,
    )

    assert upgrade._steps_to_run() == walkthrough.UPGRADE_STEPS
    assert teacher._steps_to_run() == walkthrough.walk_steps(proofreading=False)


def test_every_upgrade_step_names_a_method_the_walk_has() -> None:
    """
    Given  the upgrade steps, one name and one method each
    When   the methods are looked up on the walk
    Then   every one is there and every name is used once, so a rename fails here and not in CI
    """
    names = [name for name, _method in walkthrough.UPGRADE_STEPS]
    walk = walkthrough.Walk(
        root=Path(),
        service=None,  # ty: ignore[invalid-argument-type]
        after_upgrade=True,
    )

    assert len(set(names)) == len(names)
    for _name, method in walkthrough.UPGRADE_STEPS:
        assert callable(getattr(walk, method))


def test_a_folder_that_is_not_there_is_a_failed_result(tmp_path: Path) -> None:
    """
    Given  an --after-upgrade folder that does not exist
    When   the walk's entry runs on it
    Then   it writes a result naming the folder and ends with 1, before the app is built
    """
    out = tmp_path / "result.json"
    missing = tmp_path / "gone"

    code = walkthrough.main([str(out), "--after-upgrade", str(missing)])

    result = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert result["ok"] is False
    assert "gone" in result["failed"][0]


def test_a_root_and_an_old_folder_together_are_refused(tmp_path: Path) -> None:
    """
    Given  --root and --after-upgrade in one call, which name two different folders
    When   the walk's entry runs
    Then   it writes why it walked nothing and ends with 1, instead of picking one of them
    """
    out = tmp_path / "result.json"
    old = tmp_path / "old"
    old.mkdir()

    code = walkthrough.main(
        [str(out), "--root", str(tmp_path / "new"), "--after-upgrade", str(old)]
    )

    result = json.loads(out.read_text(encoding="utf-8"))
    assert code == 1
    assert "after-upgrade" in result["failed"][0]
