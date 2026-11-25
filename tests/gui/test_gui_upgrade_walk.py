"""The upgrade walk on a data root written by hand: the seven steps the installer jobs run.

Phase A of the installer jobs lets the newest published release leave its data — its ``ocr.toml``
and a folder of add-ons that version's own pip filled — updates the app over it, and runs
``walkthrough.py --after-upgrade`` on what is left (``docs/testing.md`` → "In CI").  That job
needs the network and a download of the release; what it checks is the steps of the walk, and
those are checked here on a settings file written by hand.

The add-on folder is the one thing that cannot be written by hand: an outdated folder is what an
app *update* over that folder makes.  So ``add-ons`` passes here as "current", and the update it
would click otherwise is the installer job's part.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from teachers_teammate.infrastructure.onnx.store import MODELS_DIR_ENV
from tests.conftest import models_store, skip_no_page_models, skip_no_reader_models
from tests.e2e import walkthrough

pytestmark = [pytest.mark.gui]

#: Longer than the child's own watchdog, so the child writes its result before this gives up.
CHILD_S = walkthrough.UPGRADE_WALK_S + 120.0

#: The settings an earlier version writes: the keys this one knows too, one it has dropped
#: (``correction_prompt``), and no ``check_for_updates``, which that version did not have.
OLD_SETTINGS = """\
ollama_url = "http://127.0.0.1:9"
ocr_engine = "ppocr"
language = "English"
correction_enabled = false
evaluation_enabled = false
anonymization_enabled = false
docx_enabled = true
text_enabled = false
text_anonymized_enabled = false
correction_prompt = ""
output = "{output}"
"""


def _old_root(tmp_path: Path) -> Path:
    """Write what an earlier version leaves: its settings file and its output folder."""
    root = tmp_path / "old-data"
    output = root / walkthrough.OUTPUT_FOLDER
    output.mkdir(parents=True)
    (root / "ocr.toml").write_text(OLD_SETTINGS.format(output=output.as_posix()), encoding="utf-8")
    return root


def _upgrade_walk(
    tmp_path: Path, root: Path, store: str = "store"
) -> tuple[int, dict[str, Any], str]:
    """Walk in a process of its own; return its exit code, what it wrote and what it printed.

    *store* names the folder the model store is linked into: one store per folder, since the
    links cannot be made twice.
    """
    out = tmp_path / "e2e.json"
    env = dict(os.environ)
    env[MODELS_DIR_ENV] = str(models_store(tmp_path / store).root)
    # What Bazel puts on this process's path is what the child imports the app and the tests from.
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    runner = "import sys; from tests.e2e import walkthrough; raise SystemExit(walkthrough.main(sys.argv[1:]))"
    try:
        done = subprocess.run(
            [sys.executable, "-c", runner, str(out), "--after-upgrade", str(root)],
            env=env,
            capture_output=True,
            text=True,
            timeout=CHILD_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"the upgrade walk ran past {CHILD_S:g} s: {exc.stdout!r} {exc.stderr!r}")
    printed = f"{done.stdout}\n{done.stderr}"
    assert out.is_file(), f"the upgrade walk wrote no result (exit {done.returncode}): {printed}"
    return done.returncode, json.loads(out.read_text(encoding="utf-8")), printed


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_the_upgrade_walk_passes_on_the_data_of_an_earlier_version(tmp_path: Path) -> None:
    """
    Given  the settings file an earlier version left, with its output folder
    When   the upgrade walk opens it with this app and runs the queue
    Then   every step passes, the old values are in the settings file afterwards, and the keys
           this version dropped are reported rather than failing the walk
    """
    root = _old_root(tmp_path)

    code, result, printed = _upgrade_walk(tmp_path, root)

    assert result["failed"] == [], printed
    assert [step["name"] for step in result["steps"]] == [
        name for name, _method in walkthrough.UPGRADE_STEPS
    ]
    assert all(step["ok"] for step in result["steps"]), [
        step for step in result["steps"] if not step["ok"]
    ]
    assert result["ok"] is True
    assert code == 0, printed
    assert result["modals"] == []
    assert result["exceptions"] == []
    # The old file had no update check; the walk switched it off and says so, so nothing was
    # asked of GitHub.
    assert "update_check" in result["readings"]
    # One key of the old file is one this version does not have: reported, not a failure.
    assert "correction_prompt" in result["readings"]["settings"]
    # The old run's folder held no document, so the walk wrote its three and the app listed them.
    assert result["readings"]["queue"].startswith("3 documents")
    # The old settings named no documents and there were no stored results, so the run read the
    # three documents the walk wrote, and nothing was there to keep.
    assert "3 had none" in result["readings"]["results"]
    assert result["readings"]["results"].endswith("read again: none")


def test_a_second_upgrade_walk_keeps_the_results_of_the_first(tmp_path: Path) -> None:
    """
    Given  the folder a first upgrade walk left: settings that name its documents, and the
    stored results of the run it did
    When   an upgrade walk runs on it again, as an update over an updated app would
    Then   it lists the same documents, the run hands back the stored texts unchanged, and the
           settings keep every value they had, which is what a second update has to survive
    """
    root = _old_root(tmp_path)
    first_code, _first, printed = _upgrade_walk(tmp_path, root, "store-first")
    assert first_code == 0, printed

    code, result, printed = _upgrade_walk(tmp_path, root, "store-second")

    assert result["failed"] == [], printed
    assert result["ok"] is True
    assert code == 0, printed
    assert result["readings"]["queue"].startswith("3 documents")
    assert result["readings"]["stored"].startswith("3 of 3 documents")
    # Nothing had to be read again, so every text, review and teacher's work is compared.
    assert result["readings"]["results"] == (
        "3 kept their stored text, 0 had none, read again: none"
    )
    # The file the first walk wrote already had the update check off, so the second changed none.
    assert "update_check" not in result["readings"]


def test_a_value_this_version_refuses_fails_before_the_window_is_built(tmp_path: Path) -> None:
    """
    Given  a settings file with a value this version does not accept
    When   the upgrade walk opens it
    Then   the first step fails and names what the app would have replaced, before a window
           would show the notice to a teacher
    """
    root = _old_root(tmp_path)
    (root / "ocr.toml").write_text('ocr_timeout = "soon"\n', encoding="utf-8")

    code, result, _printed = _upgrade_walk(tmp_path, root)

    assert code == 1
    assert result["ok"] is False
    assert result["failed"][0].startswith("old-data: "), result["failed"]
    assert "refuses 1" in result["failed"][0]
