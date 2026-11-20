"""The committed runs of the repository: every one validates, and ``INDEX.md`` is the one they give.

The export is what a reader of the repository has, so it is checked without the store: the format,
the caps that need no corpus, and the two header fields that place the numbers.  ``INDEX.md`` is
generated from the same files, so a run that was added or replaced without regenerating it fails
here.

Nothing is committed yet, so the run loop is empty today; the test is what keeps the first export
honest.
"""

from __future__ import annotations

from pathlib import Path

from tools.evaluation.core.formats import read_jsonl
from tools.evaluation.publish.index import INDEX_NAME, index_markdown
from tools.evaluation.store import layout, validate
from tools.evaluation.store.redacted import EXPORT_NAME, read_redacted

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

OUT = ROOT / "evaluations" / "proofreading"
"""The committed runs, as ``publish export`` writes them."""


def test_every_committed_run_validates() -> None:
    """Given  the committed run files
    When   each is checked in format mode, without the store
    Then   none is refused, and each names the scorer and the key its numbers belong to
    """
    paths = layout.run_files(OUT)

    for path in paths:
        run = read_redacted(read_jsonl(path), str(path))
        assert validate.problems_of_run(run.header, run.answers, {}) == [], path.name
        assert run.scorer_version >= 1, path.name
        assert run.key_sha256, path.name


def test_the_index_is_the_one_the_runs_give() -> None:
    """Given  the committed export
    When   the index is generated from it
    Then   it is the file that is committed, or there is none because nothing is exported yet
    """
    target = OUT / INDEX_NAME

    if not (OUT / EXPORT_NAME).is_file():
        assert not layout.run_files(OUT), "a committed run needs a committed export.json"
        assert not target.is_file(), "an index needs the export it lists"
        return
    assert target.is_file(), "an export needs a committed INDEX.md"
    assert target.read_text(encoding="utf-8") == index_markdown(OUT)
