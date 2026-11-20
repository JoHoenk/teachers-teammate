"""The baseline's sentence review stays out of the app: only the evaluation tools read it.

The app stores and shows the edit-based review of ``domain/proofreading/review.py``.  The
sentence-based review of the one-request baseline lives in ``tools/evaluation/run/one_request/`` and
is reached from there alone; this test fails on the first import of it from the application
package, so a second review model cannot creep back in.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:teachers_teammate\.)?(?:domain\.)?correction_review\b"
    r"|importlib\.import_module\(\s*[\"'](?:teachers_teammate\.)?(?:domain\.)?correction_review"
)


def test_no_module_of_the_app_imports_the_sentence_review() -> None:
    """
    Given  every Python file of the application package
    When   its import lines are read
    Then   none imports the baseline's sentence-based review
    """
    base = ROOT / "teachers_teammate"
    files = sorted(base.rglob("*.py"))
    assert files, "the application package was not found"

    offenders = [
        f"{path.relative_to(ROOT)}:{number}: {line.strip()}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if _IMPORT.search(line)
    ]

    assert offenders == []
