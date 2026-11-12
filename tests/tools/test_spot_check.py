"""Tests of the explanation check: the sample, the marks and the sheet."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import export, results, spot_check, validation

SOURCE = "Der Hund laufen schnell ."
OUTPUT = "Ein Hund läuft schnell ."
EDITS = ((0, 1, "Ein"), (4, 5, "läuft"))


def _page(language: str, count: int = 2) -> list[fixtures.Text]:
    """Return page texts of one language whose notes explain the edits of :data:`EDITS`."""
    return [
        fixtures.Text(
            id=f"{language}-{number:02d}",
            language=language,
            band="page",
            notes=(fixtures.note(edit=0), fixtures.note(edit=1)),
            edits=EDITS,
        )
        for number in range(1, count + 1)
    ]


def _texts() -> list[fixtures.Text]:
    """Return two page texts of each deciding language, each with two linked notes."""
    return _page("de") + _page("en")


def _group(texts=None):
    """Return a group of the texts, three repeats."""
    return fixtures.group(*(texts if texts is not None else _texts()))


def _sample(group=None) -> spot_check.Sample:
    """Return the explanation sample of the group's run of repeat 1."""
    run, items = (group or _group()).first()
    return spot_check.sample(run, items)


def _row(item: str, *, source: str = SOURCE, output: str = OUTPUT) -> results.ResultRow:
    """Return a cache row of one text, with the changes the sheet shows."""
    return results.ResultRow(
        id=f"de/{item}",
        model=fixtures.MODEL,
        temperature=0.2,
        language="de",
        kind="errors",
        origin="falko",
        seconds=1.0,
        failed=False,
        fallback=False,
        has_review=True,
        source=source,
        output=output,
        changes=(
            {"edit": [0, 1, "Ein"], "category": "grammar", "explanation": fixtures.EXPLANATION},
            {"edit": [4, 5, "läuft"], "category": "grammar", "explanation": fixtures.EXPLANATION},
        ),
    )


def _sheet_inputs(texts=None):
    """Return the texts, the cache rows, the run items and the manifest entries of a sheet."""
    digest = export.output_sha256(_row("de-01"))
    texts = [replace(text, output_sha256=digest) for text in (texts or _texts())]
    _run, items = _group(texts).first()
    rows = {item.item: _row(item.item) for item in items}
    return (
        texts,
        rows,
        {item.item: item for item in items},
        {text.id: text.entry() for text in texts},
    )


def _repository(root, texts):
    """Write the run of *texts* and its manifest below *root*; return the repository directory."""
    return fixtures.repository(root, *texts, run_id=_group(texts).first()[0].run_id)


# ── the sample ────────────────────────────────────────────────────────────────


def test_the_sample_takes_two_linked_notes_per_page_text():
    """
    Given  a run of two page texts per language, each with two notes linked to an edit
    When   the sample is drawn
    Then   it holds two notes per text and language, and every note explains an edit
    """
    sample = _sample()

    assert {code: len(notes) for code, notes in sample.per_language().items()} == {
        "de": 4,
        "en": 4,
    }
    assert all(note.edit is not None for note in sample.notes)
    assert {note.item for note in sample.notes} == {"de-01", "de-02", "en-01", "en-02"}


def test_the_sample_is_the_same_on_every_machine():
    """
    Given  one committed run
    When   the sample is drawn twice
    Then   both draws are the same, because the run's id seeds it
    """
    group = _group()

    assert _sample(group) == _sample(group)
    assert _sample(group).seed == f"{group.first()[0].run_id}/"


def test_orphans_and_blank_explanations_are_not_sampled():
    """
    Given  a page text whose notes are an orphan, a blank explanation and a linked one
    When   the sample is drawn
    Then   only the linked note with an explanation is in it, because the others explain nothing
    """
    text = fixtures.Text(
        id="de-01",
        language="de",
        band="page",
        notes=(
            fixtures.note(edit=0, orphan=True),
            fixtures.note(edit=1, explanation="   "),
            fixtures.note(edit=2),
        ),
        edits=((0, 1, "Ein"), (1, 2, "Hund"), (2, 3, "läuft")),
    )

    sample = _sample(_group([text]))

    assert [(note.item, note.note) for note in sample.notes] == [("de-01", 2)]


def test_a_short_sample_scales_the_threshold():
    """
    Given  a language whose sample holds three notes, because its texts have fewer
    When   the threshold of that language is asked for
    Then   it is three, because 80 % of three rounds up to three
    """
    first = fixtures.Text(
        id="de-01",
        language="de",
        band="page",
        notes=(fixtures.note(edit=0), fixtures.note(edit=1)),
        edits=((0, 1, "Ein"), (1, 2, "Hund")),
    )
    second = fixtures.Text(
        id="de-02",
        language="de",
        band="page",
        notes=(fixtures.note(edit=0),),
        edits=((0, 1, "Ein"),),
    )

    sample = _sample(_group([first, second]))

    assert len(sample.per_language()["de"]) == 3
    assert sample.threshold("de") == 3


# ── the marks ─────────────────────────────────────────────────────────────────


def test_marks_hold_no_text():
    """
    Given  a sample and the marks of it
    When   the marks are written as JSON
    Then   they hold the item, the note, the hash and the mark, and no explanation or text
    """
    marks = fixtures.marks_for(_sample())

    written = marks.to_json()

    assert set(written) == {"schema", "type", "run_id", "date", "marker", "sample_seed", "marks"}
    assert set(written["marks"][0]) == {"item", "note", "explanation_sha256", "mark"}
    assert fixtures.EXPLANATION not in str(written)
    assert SOURCE not in str(written)


def test_marks_of_another_sample_are_refused(tmp_path):
    """
    Given  a marks file that marks a note the run's sample does not hold
    When   it is validated against its run
    Then   it is refused, because the sheet and the run disagree
    """
    texts = _texts()
    out = _repository(tmp_path, texts)
    sample = _sample(_group(texts))
    marks = fixtures.marks_for(sample)
    stranger = replace(marks.marks[0], note=99)
    path = spot_check.marks_path(out, sample.run_id)
    spot_check.write_marks(path, replace(marks, marks=(stranger, *marks.marks[1:])))

    problems = validation.validate_marks(path, out)

    assert any("note 99 is not in the sample" in problem for problem in problems)


def test_a_changed_explanation_is_refused(tmp_path):
    """
    Given  a marks file whose explanation hash is not the one the run stored
    When   it is validated against its run
    Then   it is refused, because the explanation changed since the run
    """
    texts = _texts()
    out = _repository(tmp_path, texts)
    sample = _sample(_group(texts))
    marks = fixtures.marks_for(sample)
    changed = replace(marks.marks[0], explanation_sha256="0" * 64)
    path = spot_check.marks_path(out, sample.run_id)
    spot_check.write_marks(path, replace(marks, marks=(changed, *marks.marks[1:])))

    problems = validation.validate_marks(path, out)

    assert any("changed since the run" in problem for problem in problems)


def test_marks_of_a_run_that_is_not_there_are_refused(tmp_path):
    """
    Given  a marks file whose run is not below the repository directory
    When   it is validated
    Then   it is refused, because marks can only be checked against the run they belong to
    """
    out = _repository(tmp_path, _texts())
    marks = fixtures.marks_for(_sample())
    path = spot_check.marks_path(out, "no-such-run")
    spot_check.write_marks(path, replace(marks, run_id="no-such-run"))

    problems = validation.validate_marks(path, out)

    assert any("no run called 'no-such-run'" in problem for problem in problems)


# ── the sheet ─────────────────────────────────────────────────────────────────


def test_the_sheet_picks_the_rows_of_repeat_one_from_a_shared_cache():
    """
    Given  a cache that holds the rows of three repeats and of another model under the same ids
    When   the rows of the run of repeat 1 are picked
    Then   only its own rows are kept, one per item, so another repeat's answer is never taken
    """
    _texts_, _rows, items, _entries = _sheet_inputs()
    stored = _row("de-01")
    cache = [
        replace(stored, output="Ein Hund läuft schnell!"),
        replace(stored, output="Der Hund läuft schnell."),
        replace(stored, model=fixtures.OTHER),
        stored,
    ]

    picked = spot_check.rows_of_run(cache, list(items.values()))

    assert picked == {"de-01": stored}


def test_the_sheet_shows_the_source_sentence_of_every_note():
    """
    Given  a sample and the cache rows of its texts
    When   the sheet is written
    Then   every row shows the source sentence, the change and the explanation, and no mark
    """
    texts, rows, items, entries = _sheet_inputs()

    written = spot_check.sheet(_sample(_group(texts)), rows, items, entries)

    assert f"Run: `{_sample(_group(texts)).run_id}`" in written
    assert (
        "| # | Item | Note | Source sentence | Change | Category | Explanation | Mark |" in written
    )
    assert "laufen → läuft" in written
    assert fixtures.EXPLANATION in written


def test_the_sheet_refuses_a_row_that_was_not_exported():
    """
    Given  a sample whose text has no row in the local cache
    When   the sheet is written
    Then   it is refused, because the sheet would show no source sentence for that note
    """
    texts, rows, items, entries = _sheet_inputs()
    del rows["de-01"]

    with pytest.raises(ValueError, match="the cache has no row"):
        spot_check.sheet(_sample(_group(texts)), rows, items, entries)


def test_the_sheet_refuses_a_changed_output():
    """
    Given  a cache row whose output is not the one the run stored
    When   the sheet is written
    Then   it is refused, because the sheet would show another text than the run
    """
    texts, rows, items, entries = _sheet_inputs()
    rows["de-01"] = replace(rows["de-01"], output="Ein Hund läuft schnell!")

    with pytest.raises(ValueError, match="the cache's output is not the one the run stored"):
        spot_check.sheet(_sample(_group(texts)), rows, items, entries)


def test_a_filled_in_sheet_becomes_marks():
    """
    Given  a sheet whose Mark column is filled in
    When   the marks are read from it
    Then   every mark carries the hash of the explanation the run stored, and a mark of another
           run or an unknown mark is refused
    """
    texts, rows, items, entries = _sheet_inputs()
    sample = _sample(_group(texts))
    written = spot_check.sheet(sample, rows, items, entries)
    filled = "\n".join(
        line + " correct |" if line.startswith("| 1 |") else line for line in written.splitlines()
    )

    marks = spot_check.marks_from_sheet(filled, sample, marker="joho", date="2026-11-12")

    assert marks.marker == "joho"
    assert marks.sample_seed == sample.seed
    assert {mark.explanation_sha256 for mark in marks.marks} == {
        note.explanation_sha256 for note in sample.notes if note.note == 0
    }
    with pytest.raises(ValueError, match="the sheet is of run"):
        spot_check.marks_from_sheet(filled, replace(sample, run_id="other-run"), marker="joho")
    with pytest.raises(ValueError, match="is not one of"):
        spot_check.marks_from_sheet(filled.replace("| correct |", "| good |"), sample, marker="x")
