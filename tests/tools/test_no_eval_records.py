"""The evaluation records live in a repository of their own, never in this one.

A record holds the corpora's text: a run file carries every answer, a test set carries the sources
and the references, a marks file carries a teacher's verdicts.  The corpora are licensed for use and
not for redistribution, and a slip in this repository's history cannot be taken back.  The records
are therefore kept in the private repository ``teachers-teammate-evals``, and this test fails on the
first one that appears here, so it cannot grow back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

RECORDS: dict[str, str] = {
    "*.run.jsonl": "a run file: every answer of one model",
    "manifest.json": "a test set's manifest: its hashes and one entry per text",
    "sizes.jsonl": "a model-fit record: what a model needs on a machine",
    "accepted.jsonl": "a test set's accepted alternatives",
    "*__*.jsonl": "a marks file: one marker's verdicts on one test set",
}
"""What a record is called, and what it holds."""

RECORD_DIRS: dict[str, str] = {
    "machines": "the registry of the machines the evaluations ran on",
    "model-fit": "the size measurements",
    "gates": "the gate records",
    "testsets": "the test sets",
    "runs": "the runs",
    "marks": "the teachers' marks",
}
"""The folders a store keeps its records in, and what each holds."""

STORE_PLACES = (ROOT, ROOT / "evaluations")
"""Where a store would be copied in: the top of the repository, or beside the written method."""

SKIPPED = (".git", "bazel-out", "bazel-bin", "bazel-testlogs", "node_modules", ".venv")
"""Folders that are not part of the repository, or are generated from it."""


def _walk() -> list[Path]:
    """Return every file and folder of the repository, skipping the generated ones."""
    found: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIPPED for part in path.relative_to(ROOT).parts):
            continue
        found.append(path)
    return found


@pytest.mark.parametrize("name", sorted(RECORDS))
def test_no_record_file_is_in_the_repository(name: str) -> None:
    """
    Given  the app repository
    When   it is searched for a file a record is called
    Then   there is none, because a record holds the corpora's text and belongs in the private
           repository
    """
    found = [path for path in _walk() if path.is_file() and path.match(name)]

    assert found == [], f"{RECORDS[name]} in the app repository: {found}"


@pytest.mark.parametrize("name", sorted(RECORD_DIRS))
def test_no_record_folder_is_in_the_repository(name: str) -> None:
    """
    Given  the app repository
    When   it is searched for a folder a store keeps its records in
    Then   there is none where a store would be copied in, so no part of one was
    """
    found = [place / name for place in STORE_PLACES if (place / name).is_dir()]

    assert found == [], f"{RECORD_DIRS[name]} in the app repository: {found}"
