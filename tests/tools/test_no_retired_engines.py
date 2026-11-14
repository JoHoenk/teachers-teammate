"""The retired engines stay gone: nothing in the app imports Tesseract or Paddle.

The built-in reader replaced both.  What may still name them is the migration of old settings
(``RETIRED_OCR_ENGINES``) and the clean-up of the old add-on's files, neither of which imports
anything; this test fails on the first ``import`` of one of their packages.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:paddle\w*|pytesseract)\b|importlib\.import_module\(\s*[\"'](?:paddle|pytesseract)"
)


def test_no_module_of_the_app_imports_paddle_or_pytesseract() -> None:
    """
    Given  every Python file of the application package
    When   its import lines are read
    Then   none imports paddle, paddleocr, paddlex or pytesseract
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
