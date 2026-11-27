"""The teacher's verdicts on disk: one file per text, in the data root.

A mark is the teacher's work and can never be made again, so it lives beside ``TeacherWorkStore``
and not in the cache the answers sit in.  What the store must do is give back exactly what went in,
hold no text, ignore a file of another shape, and leave the file alone when it cannot be read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate.domain.proofreading.option_marks import OptionMark, Verdict, identity
from teachers_teammate.exceptions import MarksKeptError
from teachers_teammate.infrastructure.option_mark_store import OptionMarkStore
from teachers_teammate.messages import marks_not_replaced, message_of

TEXT_SHA = "a1b2c3"


def _mark(*, replacement: str = "Hund", verdict: Verdict = Verdict.RIGHT) -> OptionMark:
    """Return one verdict on the words ``[4, 6)`` as the comparison records it."""
    return OptionMark(
        text_sha=TEXT_SHA,
        start=4,
        end=6,
        replacement_sha256=identity(TEXT_SHA, 4, 6, replacement)[3],
        verdict=verdict,
    )


def test_a_stored_mark_comes_back_as_it_went_in(tmp_path: Path) -> None:
    """
    Given  a right and a wrong verdict about one text
    When   they are saved and read back
    Then   they equal what went in, in one file named after the text
    """
    store = OptionMarkStore(tmp_path)
    right = _mark()
    wrong = _mark(replacement="Hund bellt", verdict=Verdict.WRONG)

    store.save(TEXT_SHA, (right, wrong))

    assert (tmp_path / f"{TEXT_SHA}.json").is_file()
    assert store.marks(TEXT_SHA) == (right, wrong)


def test_a_mark_holds_no_text_of_the_document(tmp_path: Path) -> None:
    """
    Given  a verdict on a long replacement
    When   the file is read as it lies on disk
    Then   it holds a span, a hash and the verdict, and none of the words
    """
    store = OptionMarkStore(tmp_path)

    store.save(TEXT_SHA, (_mark(replacement="die ganze Nacht hindurch"),))

    text = (tmp_path / f"{TEXT_SHA}.json").read_text(encoding="utf-8")
    assert "Nacht" not in text
    assert identity(TEXT_SHA, 4, 6, "die ganze Nacht hindurch")[3] in text


def test_a_file_of_another_shape_is_no_mark(tmp_path: Path) -> None:
    """
    Given  a file of another format, one of another text and one that is no JSON at all
    When   the verdicts of that text are read
    Then   none of them is a verdict, and nothing raises
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{TEXT_SHA}.json").write_text(
        json.dumps({"format": "compare-marks/2", "text_sha": TEXT_SHA, "marks": []}),
        encoding="utf-8",
    )
    (tmp_path / "other.json").write_text("{not json", encoding="utf-8")

    assert store.marks(TEXT_SHA) == ()
    assert store.marks("other") == ()


def test_a_file_that_belongs_to_another_text_is_ignored(tmp_path: Path) -> None:
    """
    Given  a marks file whose own name says it is about a different text
    When   it is read under the name of the text's file
    Then   nothing is read, so no verdict lands on the wrong text
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{TEXT_SHA}.json").write_text(
        json.dumps(
            {
                "format": "compare-marks/1",
                "text_sha": "another-text",
                "marks": [_mark().to_dict()],
            }
        ),
        encoding="utf-8",
    )

    assert store.marks(TEXT_SHA) == ()


def test_a_verdict_of_a_newer_app_is_not_counted(tmp_path: Path) -> None:
    """
    Given  a marks file whose verdicts this app does not know
    When   it is read
    Then   the marks are left out, and nothing raises
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{TEXT_SHA}.json").write_text(
        json.dumps(
            {
                "format": "compare-marks/1",
                "text_sha": TEXT_SHA,
                "marks": [{"start": 4, "end": 6, "replacement_sha256": "a" * 64, "verdict": "?"}],
            }
        ),
        encoding="utf-8",
    )

    assert store.marks(TEXT_SHA) == ()


def test_saving_no_mark_leaves_no_file(tmp_path: Path) -> None:
    """
    Given  a text the teacher marked and then took every verdict back from
    When   the empty list is saved
    Then   the file is gone, so the folder holds only what the teacher really did
    """
    store = OptionMarkStore(tmp_path)
    store.save(TEXT_SHA, (_mark(),))

    store.save(TEXT_SHA, ())

    assert not (tmp_path / f"{TEXT_SHA}.json").exists()
    assert store.marks(TEXT_SHA) == ()


def test_a_text_without_a_file_has_no_marks(tmp_path: Path) -> None:
    """
    Given  a data folder the teacher never marked anything in
    When   the verdicts of a text are asked for
    Then   there are none, and nothing raises
    """
    store = OptionMarkStore(tmp_path / "compare_marks")

    assert store.marks(TEXT_SHA) == ()


def test_a_save_never_replaces_a_file_of_a_newer_app(tmp_path: Path) -> None:
    """
    Given  a marks file whose format this app does not know, with the teacher's verdicts in it
    When   the teacher marks one more change, or takes every mark back
    Then   the save fails with MarksKeptError and the file is left exactly as it was
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{TEXT_SHA}.json"
    original = json.dumps({"format": "compare-marks/2", "text_sha": TEXT_SHA, "marks": [1]})
    path.write_text(original, encoding="utf-8")

    with pytest.raises(MarksKeptError) as first:
        store.save(TEXT_SHA, (_mark(),))
    with pytest.raises(MarksKeptError):
        store.save(TEXT_SHA, ())

    assert message_of(first.value) == marks_not_replaced(locked=False)

    assert path.read_text(encoding="utf-8") == original


def test_a_save_never_replaces_a_file_that_cannot_be_read_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a marks file that another program holds, so reading it fails with OSError
    When   the teacher marks a change
    Then   the save fails with MarksKeptError and the file is not written
    """
    store = OptionMarkStore(tmp_path)
    store.save(TEXT_SHA, (_mark(),))
    path = tmp_path / f"{TEXT_SHA}.json"
    before = path.read_text(encoding="utf-8")
    real_read = Path.read_text

    def locked(self: Path, *args: object, **kwargs: object) -> str:
        if self == path:
            raise PermissionError("locked")
        return real_read(self, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(Path, "read_text", locked)

    with pytest.raises(MarksKeptError) as refused:
        store.save(TEXT_SHA, (_mark(replacement="other"),))
    assert message_of(refused.value) == marks_not_replaced(locked=True)

    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == before


def test_a_file_that_is_no_json_is_replaced_by_a_save(tmp_path: Path) -> None:
    """
    Given  a marks file that is no JSON at all, so it holds no verdict to keep
    When   the teacher marks a change
    Then   the file is replaced and the mark reads back
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"{TEXT_SHA}.json").write_text("{not json", encoding="utf-8")

    store.save(TEXT_SHA, (_mark(),))

    assert store.marks(TEXT_SHA) == (_mark(),)


def test_a_verdict_of_a_newer_app_is_written_back_with_the_new_marks(tmp_path: Path) -> None:
    """
    Given  a marks file in this format that holds a verdict this app does not know
    When   the teacher marks another change, and then takes that mark back
    Then   the newer app's verdict is in the file each time, as it was, and the file stays
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{TEXT_SHA}.json"
    foreign = {"start": 9, "end": 12, "replacement_sha256": "b" * 64, "verdict": "unsure"}
    path.write_text(
        json.dumps({"format": "compare-marks/1", "text_sha": TEXT_SHA, "marks": [foreign]}),
        encoding="utf-8",
    )

    store.save(TEXT_SHA, (_mark(),))
    assert foreign in json.loads(path.read_text(encoding="utf-8"))["marks"]
    assert store.marks(TEXT_SHA) == (_mark(),)

    store.save(TEXT_SHA, ())
    assert json.loads(path.read_text(encoding="utf-8"))["marks"] == [foreign]
    assert store.marks(TEXT_SHA) == ()


def test_a_mark_on_an_option_replaces_a_verdict_of_a_newer_app_there(tmp_path: Path) -> None:
    """
    Given  a verdict of a newer app on one option
    When   the teacher gives that same option a verdict of this app
    Then   the option has one record, the teacher's
    """
    store = OptionMarkStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{TEXT_SHA}.json"
    mark = _mark()
    foreign = {
        "start": mark.start,
        "end": mark.end,
        "replacement_sha256": mark.replacement_sha256,
        "verdict": "unsure",
    }
    path.write_text(
        json.dumps({"format": "compare-marks/1", "text_sha": TEXT_SHA, "marks": [foreign]}),
        encoding="utf-8",
    )

    store.save(TEXT_SHA, (mark,))

    assert json.loads(path.read_text(encoding="utf-8"))["marks"] == [mark.to_dict()]
