"""The repository explains itself: no source, test, document or record points into working notes.

``plans/`` is git-ignored, so a reference to a file in it, or to a decision number from it
(``DW6b-2``, ``F-W6b-13``, ``CW7``, ``§4.4``, ``W8``, ``(R4)``, ``A1-A5``, ``S0b``, a
``spec-cw-followup.md``), points nowhere for anyone else.  The reason for a rule is written where
the rule is.  This test fails on the first such reference, so it cannot grow back.
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

REPOSITORY_FILES = (
    ".bazelignore",
    ".bazelrc",
    ".gitignore",
    "BUILD",
    "MODULE.bazel",
    "pyproject.toml",
)
"""The build's own configuration, which a reader of the repository sees as much as a source file."""

DOCUMENT_GLOBS = (
    "docs/*.md",
    "evaluations/**/*.md",
    "evaluations/**/*.txt",
    "AGENTS.md",
    ".github/**/*.yml",
)

REFERENCES: dict[str, re.Pattern[str]] = {
    "a path into plans/": re.compile(r"\bplans/"),
    "a spec file": re.compile(r"\bspec-[a-z0-9-]+"),
    "a finding or decision id": re.compile(
        r"\b(?:F-W\d+b?-\d+|DW-?c?\d+[ab]?(?:-\d+)?|DC\d+[ab]?(?:-\d+)?|DF-\d+|CW\d+[a-z]?)\b"
    ),
    "a section sign": re.compile(r"§"),
    "a work-package number": re.compile(r"\bW\d{1,2}[abc]?\b"),
    "a work-package step": re.compile(r"\bWP\d{1,2}(?:\.\d+)?\b"),
    "the plan": re.compile(r"\b(?:the plan's|the plan\b|model plan|writing-model plan)"),
    "a parenthesised id": re.compile(
        r"\((?:[ADFGHPRV]\d{1,2}[a-z]?)(?:\s*[,;]\s*[ADFGHPRV]\d{1,2}[a-z]?)*\)"
    ),
    "an id with a note": re.compile(r"\([ADFGHPRV]\d{1,2}[a-z]?:\s"),
    "an id range": re.compile(r"\b([ADFRV])\d{1,2}\s*[-\u2013]\s*\1?\d{1,2}\b"),
    "a step id": re.compile(r"\bS0b\b"),
    "a section comment with an id": re.compile(r"^# ── [ADFHPRV]\d{1,2}[a-z]?:"),
    "a test named after an acceptance id": re.compile(r"\btest_a\d+_"),
    "an acceptance id in prose": re.compile(r"\bacceptance tests? [Aa]\d{1,2}\b"),
}
"""What counts as a pointer into the working notes, and what to call it.

The last eight catch the shapes a citation takes in prose: ``(R4)``, ``(R9, V8)``, ``(R1: a run
keeps every fact)``, ``A1-A5``, ``S0b``, ``WP2.2``, a ``# ── R11: … ──`` section comment, a test
named ``test_a1_…`` after an acceptance id, and ``acceptance test A3``.  ``S`` is left out of every
rule that would take a bare ``S<n>``, because the setup walkthrough numbers its own scenarios
``S1`` to ``S9``; the plan's step id ``S0b`` is caught by its own rule.  A bare ``A1`` is left out
because it is also a CEFR level, and a bare id is caught by its prefix (``F-W``, ``DW``, ``DF``,
``CW``) rather than by its shape, because ``MD5``, ``A4`` and ``W3C`` have the same shape.
"""

ALLOWED: dict[str, str] = {
    ".gitignore: plans/": "the ignore rule for the folder itself, which is what keeps it out of the tree",
}
"""``"path: line text"`` of a line that matches by accident, with the reason."""


def _files() -> list[Path]:
    """Return every file the rule applies to that exists in this tree."""
    found: list[Path] = []
    for tree in SOURCE_TREES:
        base = ROOT / tree
        if base.is_dir():
            found += [*base.rglob("*.py"), *base.rglob("BUILD")]
    found += [ROOT / name for name in REPOSITORY_FILES if (ROOT / name).is_file()]
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
                if f"{path.relative_to(ROOT)}: {line.strip()}" not in ALLOWED:
                    hits.append(f"{where}: {kind}: {line.strip()[:90]}")
    return hits


def test_no_file_points_into_the_working_notes():
    """
    Given  every source, test, build, document and record file of the repository
    When   each line is searched for a path into plans/, a spec file, a finding or decision id, a
           section sign, a work-package number, a parenthesised id, an id range, a step id or a
           mention of "the plan"
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
        "(spec-cw-followup.md)",
        "per F-W6b-13",
        "the verdict of DW6a-12",
        "the DF-2 rule",
        "the cap (CW7)",
        "as §4.4 says",
        "after W8",
        "as the plan's first idea",
        "the model plan wants it",
        "the rule (R4)",
        "the two of them (R9, V8)",
        "the pair (D16, R12)",
        "the rule (R1: a run keeps every fact)",
        "the acceptance tests A1-A5",
        "the step S0b",
        "a step of WP2.2",
        "# ── R11: an unknown field survives a read and a write ──",
        "def test_a1_every_old_row_becomes_one_answer():",
        "the acceptance test A3",
        "the acceptance tests a10",
    ]
    ordinary = [
        "W&I is a corpus",
        "the CEFR level C1",
        "a plan for the run",
        "MD5 and W3C",
        "the corpora CC0 and SHA1",
        "a tuple of (str, ...)",
        "the run of 2026-11-16",
        "F0.5 and GLEU",
        "the levels A1\u2013C2",
        "# ── S1: the whole way through ──",
        "# ── The proofreading page (S5, S6) ──",
        "the scenarios S1\u2013S9",
        "def test_a_changed_source_moves_the_question():",
        "the acceptance tests run the converter over the real folders",
    ]

    for line in cited:
        assert any(pattern.search(line) for pattern in REFERENCES.values()), line
    for line in ordinary:
        assert not any(pattern.search(line) for pattern in REFERENCES.values()), line
