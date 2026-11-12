"""The repository explains itself: no source, test, document or record points into working notes.

``plans/`` is git-ignored, so a reference to a file in it, or to a decision number from it
(``DW6b-2``, ``F-W6b-13``, ``§4.4``, ``W8``), points nowhere for anyone else.  The reason for a
rule is written where the rule is.  This test fails on the first such reference, so it cannot grow
back.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

THIS_FILE = Path(__file__).resolve()

SOURCE_TREES = ("teachers_teammate", "tools", "tests")
"""Python and BUILD files below these."""

DOCUMENT_GLOBS = ("docs/*.md", "evaluations/**/*.md", "evaluations/**/*.txt", "CLAUDE.md")

REFERENCES: dict[str, re.Pattern[str]] = {
    "a path into plans/": re.compile(r"\bplans/"),
    "a spec file": re.compile(r"\bspec-w[a-z0-9-]+"),
    "a finding or decision id": re.compile(r"\b(?:F-W\d+b?-\d+|DW-?c?\d+[ab]?(?:-\d+)?|DF-\d+)\b"),
    "a section sign": re.compile(r"§"),
    "a work-package number": re.compile(r"\bW\d{1,2}[abc]?\b"),
    "the plan": re.compile(r"\b(?:the plan's|the plan\b|model plan|writing-model plan)"),
}
"""What counts as a pointer into the working notes, and what to call it."""

ALLOWED: dict[str, str] = {}
"""``"path:line"`` of a line that matches by accident, with the reason."""


def _files() -> list[Path]:
    """Return every file the rule applies to that exists in this tree."""
    found: list[Path] = []
    for tree in SOURCE_TREES:
        base = ROOT / tree
        if base.is_dir():
            found += [*base.rglob("*.py"), *base.rglob("BUILD")]
    for pattern in DOCUMENT_GLOBS:
        found += ROOT.glob(pattern)
    return sorted({path.resolve() for path in found if path.resolve() != THIS_FILE})


def _hits(path: Path) -> list[str]:
    """Return ``path:line: kind`` for every pointer into the working notes in *path*."""
    hits: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in REFERENCES.items():
            if pattern.search(line):
                where = f"{path.relative_to(ROOT)}:{number}"
                if where not in ALLOWED:
                    hits.append(f"{where}: {kind}: {line.strip()[:90]}")
    return hits


def test_no_file_points_into_the_working_notes():
    """
    Given  every source, test, build, document and record file of the repository
    When   each line is searched for a path into plans/, a spec file, a finding or decision id, a
           section sign, a work-package number or a mention of "the plan"
    Then   there is none, because those notes are not in the repository
    """
    files = _files()
    if not files:
        pytest.skip("the repository tree is not available")

    problems = [hit for path in files for hit in _hits(path)]

    assert problems == [], "\n".join(problems)


def test_the_rule_recognises_what_it_forbids(tmp_path):
    """
    Given  lines that cite the working notes in each of the ways the rule names
    When   they are searched
    Then   every one is found, and ordinary text is not
    """
    cited = [
        "see plans/open-work.md",
        "(spec-w6b-gate2.md)",
        "per F-W6b-13",
        "the verdict of DW6a-12",
        "the DF-2 rule",
        "as §4.4 says",
        "after W8",
        "as the plan's first idea",
        "the model plan wants it",
    ]
    ordinary = ["W&I is a corpus", "the CEFR level C1", "a plan for the run", "MD5 and W3C"]

    for line in cited:
        assert any(pattern.search(line) for pattern in REFERENCES.values()), line
    for line in ordinary:
        assert not any(pattern.search(line) for pattern in REFERENCES.values()), line
