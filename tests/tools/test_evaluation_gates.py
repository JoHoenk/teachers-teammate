"""Unit tests for gate 2's floors: what one model has to meet on its own numbers.

A group of three repeats is judged per language, a group of two is not judged at all, a failed text
fails the model, a language that does not decide is shown and not counted, and the explanations of a
review run decide only once a teacher has marked them.

Nothing here reads the real store: the records and the runs are built in the test, so a failure
names the rule that broke and not a file on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from tools.evaluation.core import protocol_table
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.core.modes import Exchange, Step, default_steps
from tools.evaluation.core.performance import RequestStats
from tools.evaluation.core.records import (
    Answer,
    Environment,
    Input,
    Mark,
    Protocol,
    RunFile,
    RunHeader,
    RunSettings,
    Session,
    TestRecord,
    key_sha256,
    question_sha256,
    text_id,
)
from teachers_teammate.domain.proofreading.word_diff import text_offsets
from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits
from tools.evaluation.report import gates, groups, load, score, spot_check
from tools.evaluation.store import layout, writer

DE_SOURCE = "Er geht heute nach Hause. Das Wetter ist schön."
DE_REFERENCE = "Er geht heute heim. Das Wetter ist schön."
EN_SOURCE = "He go to school. The weather is nice."
EN_REFERENCE = "He goes to school. The weather is nice."

FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]
"""A fingerprint the table records, so a run of it is not refused for an unknown protocol."""


def _spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return the character span of every sentence of *text*, the full stop included."""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == ".":
            spans.append((start, index + 1))
            start = index + 2
    return tuple(spans)


def _label(source: str, reference: str, code: str = "R:VERB:FORM") -> ReferenceLabel:
    """Return the first edit of *reference* against *source* as a label, in characters."""
    offsets = text_offsets(source)
    start, end, replacement = ordered_edits(source, reference)[0]
    return ReferenceLabel(
        start=offsets[start],
        end=offsets[end],
        replacement="".join(replacement),
        code=code,
    )


def _record(
    language: str, source: str, reference: str, *, band: str = "page", **changes
) -> TestRecord:
    """Return one test record of *language*, with *changes* applied."""
    record = TestRecord(
        id=f"{language}-01",
        language=language,
        source=source,
        kind="errors",
        reference=reference,
        labels=(_label(source, reference),),
        sentence_spans=_spans(source),
        unchanged_sentences=(1,),
        origin="a test",
        band=band,
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def _records() -> tuple[TestRecord, ...]:
    """Return one German and one English text, both of the ``page`` band."""
    return (
        _record("de", DE_SOURCE, DE_REFERENCE),
        _record("en", EN_SOURCE, EN_REFERENCE),
    )


def _review(record: TestRecord) -> CorrectionReview:
    """Return the review of *record*: its one edit, explained."""
    start, end, replacement = ordered_edits(record.source, record.reference or record.source)[0]
    offsets = text_offsets(record.source)
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(
                        raw_start=offsets[start],
                        raw_end=offsets[end],
                        original=record.source[offsets[start] : offsets[end]],
                        corrected="".join(replacement),
                    ),
                ),
                sentences=(0,),
                category="R:VERB:FORM",
                explanation="The verb agrees with the subject.",
            ),
        )
    )


def _header(
    records: tuple[TestRecord, ...],
    *,
    exchange: Exchange,
    steps: tuple[Step, ...] | None = None,
    repeat: int,
) -> RunHeader:
    """Return the header of a run of *records*, of one exchange, its steps and repeat."""
    return RunHeader(
        input=Input(
            testset="gec-2026-12",
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=tuple(sorted({record.language for record in records})),
        ),
        protocol=Protocol(
            model="gemma4:12b",
            model_digest="a" * 64,
            fingerprint=FINGERPRINT,
            temperature=0.0,
            exchange=exchange,
            steps=default_steps(exchange) if steps is None else steps,
        ),
        run=RunSettings(seed=20261109, repeat=repeat),
        environment=Environment(machine="rx9070xt-server", ollama_version="0.40.16"),
    )


def _answer(record: TestRecord, **changes) -> Answer:
    """Return one answer of *record* that fixes it as the reference does, with *changes* applied."""
    answer = Answer(
        text=f"{record.language}/{record.id}",
        text_sha256=record.text_sha256,
        output=record.reference or record.source,
    )
    return replace(answer, **changes) if changes else answer


def _run(
    records: tuple[TestRecord, ...],
    *,
    exchange: Exchange = Exchange.TEXT_FIRST,
    steps: tuple[Step, ...] | None = None,
    repeat: int = 1,
    answers: tuple[Answer, ...] | None = None,
) -> RunFile:
    """Return a run of *records* with one answer each, of one exchange, its steps and repeat."""
    return RunFile(
        header=_header(records, exchange=exchange, steps=steps, repeat=repeat),
        sessions=(Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
        answers=answers if answers is not None else tuple(_answer(record) for record in records),
    )


def _manifest(records: tuple[TestRecord, ...]) -> Manifest:
    """Return a manifest that pins *records*, as the store writes it."""
    return Manifest(
        testset="gec-2026-12",
        built="2026-11-14T23:22:00+00:00",
        selection_seed=20261109,
        builder_revision="ffa440f",
        question_sha256=question_sha256(list(records)),
        key_sha256=key_sha256(list(records)),
        items=tuple(
            ManifestItem(
                id=record.id,
                language=record.language,
                kind=record.kind,
                corpus=record.corpus,
                band=record.band,
                pointer={},
                source={"sha256": "0" * 64, "words": 1, "chars": len(record.source)},
                reference={"sha256": "0" * 64},
            )
            for record in records
        ),
    )


def _store(root: Path, records: tuple[TestRecord, ...], *runs: RunFile) -> Path:
    """Write *records* and *runs* into a store at *root* and return the root."""
    writer.write_testset(root, "gec-2026-12", records, _manifest(records))
    for number, run in enumerate(runs):
        writer.write_run(layout.run_path(root, f"run-{number}.run.jsonl"), run)
    return root


def _group(
    root: Path,
    records: tuple[TestRecord, ...],
    *,
    repeats: int = 3,
    exchange: Exchange = Exchange.TEXT_FIRST,
    steps: tuple[Step, ...] | None = None,
    answers: dict[int, tuple[Answer, ...]] | None = None,
) -> groups.RunGroup:
    """Return the one group of *repeats* runs of *records*, each repeat answered as *answers* says."""
    runs = tuple(
        _run(
            records,
            exchange=exchange,
            steps=steps,
            repeat=number,
            answers=(answers or {}).get(number),
        )
        for number in range(1, repeats + 1)
    )
    return groups.groups(load.load(_store(root, records, *runs)).runs)[0]


def _check(judgement: gates.Judgement, name: str) -> gates.Check:
    """Return the check of *judgement* named *name*.

    Raises:
        AssertionError: if the judgement has no such check.
    """
    found = next((check for check in judgement.checks if check.name == name), None)
    assert found is not None, f"no check {name!r} in {[check.name for check in judgement.checks]}"
    return found


# ── the outcome ─────────────────────────────────────────────────────────────


def test_a_group_of_three_repeats_is_judged_per_language(tmp_path):
    """
    Given  a store of one German and one English text, run three times
    When   the group is judged
    Then   every floor is measured for both languages and the model passes
    """
    group = _group(tmp_path / "store", _records(), steps=(Step.CORRECT,))

    judgement = gates.GateTwoRule().judge(group)

    assert judgement.outcome is gates.Outcome.PASS
    assert judgement.languages() == ("de", "en")
    assert _check(judgement, "failed texts, de").passed is True
    assert _check(judgement, "coverage, en").passed is True


def test_a_group_of_two_repeats_is_not_judged(tmp_path):
    """
    Given  a store whose model was run twice
    When   the group is judged
    Then   nothing is judged, and the check says how many repeats there are
    """
    group = _group(tmp_path / "store", _records(), repeats=2)

    judgement = gates.GateTwoRule().judge(group)

    assert judgement.outcome is gates.Outcome.INCOMPLETE
    assert _check(judgement, "repeats").value == "2 (1, 2)"


def test_a_group_without_a_deciding_language_is_not_judged(tmp_path):
    """
    Given  a store of German texts only
    When   the group is judged with German and English deciding
    Then   nothing is judged, because a language nobody measured cannot pass
    """
    records = (_record("de", DE_SOURCE, DE_REFERENCE),)
    group = _group(tmp_path / "store", records)

    judgement = gates.GateTwoRule().judge(group)

    assert judgement.outcome is gates.Outcome.INCOMPLETE
    assert "no text in en" in _check(judgement, "languages").value


def test_a_failed_text_fails_the_model(tmp_path):
    """
    Given  a store whose first repeat could not proofread one text
    When   the group is judged
    Then   the model fails, and the failed text is counted for its language
    """
    records = _records()
    answers = tuple(
        _answer(record, failed=True) if record.language == "de" else _answer(record)
        for record in records
    )
    group = _group(tmp_path / "store", records, answers={1: answers})

    judgement = gates.GateTwoRule().judge(group)

    assert judgement.outcome is gates.Outcome.FAIL
    assert _check(judgement, "failed texts, de").value == "1"
    assert _check(judgement, "failed texts, en").passed is True


def test_a_language_that_does_not_decide_is_shown_and_not_counted(tmp_path):
    """
    Given  a store whose English answers all failed
    When   the group is judged with German alone deciding
    Then   the model passes, and the English checks say they do not decide
    """
    records = _records()
    answers = tuple(
        _answer(record, failed=True) if record.language == "en" else _answer(record)
        for record in records
    )
    group = _group(
        tmp_path / "store",
        records,
        steps=(Step.CORRECT,),
        answers={1: answers, 2: answers, 3: answers},
    )

    judgement = gates.GateTwoRule(deciding=("de",)).judge(group)

    assert judgement.outcome is gates.Outcome.PASS
    assert _check(judgement, "failed texts, en").passed is False
    assert _check(judgement, "failed texts, en").deciding is False
    assert "(not deciding)" in _check(judgement, "failed texts, en").line()


def test_a_text_that_lost_text_in_two_repeats_fails(tmp_path):
    """
    Given  a store whose model dropped the text in two of three repeats
    When   the group is judged
    Then   the lost-text floor fails, because one repeat is shown and two are a configuration
    """
    records = _records()
    dropped = tuple(_answer(record, output=record.source.split()[0]) for record in records)
    group = _group(tmp_path / "store", records, answers={1: dropped, 2: dropped})

    judgement = gates.GateTwoRule().judge(group)

    assert _check(judgement, "texts that lost text in 2 or more repeats, de").passed is False


def test_a_text_that_lost_text_once_is_shown_and_not_marked(tmp_path):
    """
    Given  a store whose model dropped the text in one of three repeats
    When   the group is judged
    Then   the lost-text floor holds, because a single drop can be the sampler's doing
    """
    records = _records()
    dropped = tuple(_answer(record, output=record.source.split()[0]) for record in records)
    group = _group(tmp_path / "store", records, answers={1: dropped})

    judgement = gates.GateTwoRule().judge(group)

    assert _check(judgement, "texts that lost text in 2 or more repeats, de").passed is True


# ── the explanations ────────────────────────────────────────────────────────


def _review_group(root: Path, *, marks: tuple[Mark, ...] | None = None) -> gates.Judgement:
    """Return the judgement of a review run of two explained texts, marked as *marks* says."""
    records = _records()
    answers = tuple(_answer(record, review=_review(record)) for record in records)
    group = _group(
        root,
        records,
        exchange=Exchange.ONE_REQUEST_REVIEW,
        answers={1: answers, 2: answers, 3: answers},
    )
    return gates.GateTwoRule().judge(group, marks)


def _marks(value: str = "correct") -> tuple[Mark, ...]:
    """Return one mark per explained text of :func:`_records`, all of *value*.

    A mark of an explanation carries the explanation's hash, which is part of its identity: the
    verdict is about that text and not about the edit alone.
    """
    marks = []
    for record in _records():
        offsets = text_offsets(record.source)
        start, end, replacement = ordered_edits(record.source, record.reference)[0]
        note = _review(record).edits[0]
        marks.append(
            Mark(
                kind="explanation",
                text=text_id(record),
                span=(offsets[start], offsets[end]),
                replacement="".join(replacement),
                value=value,
                explanation_sha256=spot_check.explanation_hash(note),
            )
        )
    return tuple(marks)


def test_an_unmarked_review_passes_pending(tmp_path):
    """
    Given  a review run whose explanations nobody has marked
    When   the group is judged without marks
    Then   the floors hold but the outcome is pending, because no star is given without marks
    """
    judgement = _review_group(tmp_path / "store")

    assert judgement.outcome is gates.Outcome.PASS_PENDING
    assert _check(judgement, "explanations, de").value == "1 to mark"


def test_marked_explanations_decide_the_outcome(tmp_path):
    """
    Given  a review run whose explanations a teacher marked
    When   the group is judged with those marks
    Then   the correct ones pass the model and the wrong ones fail it
    """
    passed = _review_group(tmp_path / "correct", marks=_marks("correct"))
    failed = _review_group(tmp_path / "wrong", marks=_marks("wrong"))

    assert passed.outcome is gates.Outcome.PASS
    assert _check(passed, "explanations, de").value == "1/1 correct"
    assert failed.outcome is gates.Outcome.FAIL
    assert _check(failed, "explanations, de").passed is False


def test_a_review_with_nothing_to_mark_is_pending(tmp_path):
    """
    Given  a review run whose answers carry no explanations
    When   the group is judged
    Then   the explanation check is neither passed nor failed, so the outcome is pending
    """
    records = tuple(
        _record(record.language, record.source, record.reference, band="short")
        for record in _records()
    )
    answers = tuple(_answer(record, review=_review(record)) for record in records)
    group = _group(
        tmp_path / "store",
        records,
        exchange=Exchange.ONE_REQUEST_REVIEW,
        answers={1: answers, 2: answers, 3: answers},
    )

    judgement = gates.GateTwoRule().judge(group)

    assert judgement.outcome is gates.Outcome.PASS_PENDING
    assert _check(judgement, "explanations, de").value == "nothing to mark"


def test_a_text_first_group_gets_the_explanation_floors(tmp_path):
    """
    Given  a text-first run, which asks the explanations
    When   the group is judged
    Then   the explained share, the other fallbacks and the spot check are measured
    """
    group = _group(tmp_path / "store", _records())

    judgement = gates.GateTwoRule().judge(group)

    assert _check(judgement, "explained share, de").passed is False
    assert _check(judgement, "other fallbacks, de").passed is True
    assert [check for check in judgement.checks if check.kind is gates.CheckKind.EXPLANATION]


def test_a_correct_only_group_has_no_explanation_floor(tmp_path):
    """
    Given  a text-first run that asked step ① alone
    When   the group is judged
    Then   no explanation floor is measured, and the model passes on its floors alone
    """
    group = _group(tmp_path / "store", _records(), steps=(Step.CORRECT,))

    judgement = gates.GateTwoRule().judge(group)

    assert judgement.outcome is gates.Outcome.PASS
    assert not [check for check in judgement.checks if "explained" in check.name]
    assert not [check for check in judgement.checks if "fallbacks" in check.name]
    assert not [check for check in judgement.checks if check.kind is gates.CheckKind.EXPLANATION]


# ── the diagnostics ─────────────────────────────────────────────────────────


def test_the_diagnostics_never_decide(tmp_path):
    """
    Given  a group of three repeats
    When   the group is judged
    Then   the figures are printed with the floors, and none of them decides
    """
    group = _group(tmp_path / "store", _records())

    judgement = gates.GateTwoRule().judge(group)

    names = [check.name for check in judgement.diagnostics]
    assert names == [
        "recall, 1st / 2nd half",
        "pages band, F0.5",
        "cut-offs the second try absorbed",
        "seconds per 100 words",
        "explanations of an unreadable review",
    ]
    assert all(check.passed is None for check in judgement.diagnostics)
    assert all(check.kind is gates.CheckKind.DIAGNOSTIC for check in judgement.diagnostics)


def test_a_cut_off_that_the_second_try_absorbed_is_counted():
    """
    Given  one text whose first request was cut off and whose second answered
    When   the absorbed cut-offs are counted
    Then   the text is counted, and a text that fell back is not
    """
    records = _records()
    cut_off = _request("length")
    absorbed = _answer(records[0], requests=(cut_off, _request("stop")))
    fell_back = _answer(records[1], fallback=True, requests=(cut_off,))

    counted = gates.absorbed_cut_offs((_item(records[0], absorbed), _item(records[1], fell_back)))

    assert counted == {"de": 1}


def _request(done_reason: str) -> RequestStats:
    """Return one finished request that ended for *done_reason*."""
    return RequestStats(
        model="gemma4:12b",
        prompt_tokens=10,
        answer_tokens=10,
        load_s=0.0,
        prompt_eval_s=0.1,
        generation_s=0.2,
        total_s=0.3,
        wall_s=0.4,
        done_reason=done_reason,
    )


def _item(record: TestRecord, answer: Answer) -> load.LoadedItem:
    """Return the scored item of *answer*, as the loader would hold it."""
    header = _header((record,), exchange=Exchange.TEXT_FIRST, steps=(Step.CORRECT,), repeat=1)
    return load.LoadedItem(
        answer=answer,
        record=record,
        scored=score.score_row(answer, record, header),
        header=header,
    )


# ── the lines ───────────────────────────────────────────────────────────────


def test_a_check_line_names_the_bar_and_the_mark():
    """
    Given  a passed, a failed, an unmeasured and a diagnostic check
    When   each is written as a line
    Then   the mark says which it is, and the bar is named where there is one
    """
    passed = gates.Check("coverage, de", True, "0.95", "≥ 0.90")
    failed = gates.Check("coverage, de", False, "0.80", "≥ 0.90")
    unmeasured = gates.Check("coverage, de", None, "no text", "≥ 0.90")
    diagnostic = gates.Check(
        "seconds per 100 words", None, "3.1", "", kind=gates.CheckKind.DIAGNOSTIC
    )

    assert passed.line() == "✓ coverage, de: 0.95 (needs ≥ 0.90)"
    assert failed.line() == "✗ coverage, de: 0.80 (needs ≥ 0.90)"
    assert unmeasured.line() == "? coverage, de: no text (needs ≥ 0.90)"
    assert diagnostic.line() == "· seconds per 100 words: 3.1"


def test_the_judgement_lists_the_languages_and_the_outcome(tmp_path):
    """
    Given  a group of three repeats
    When   the judgement is written as lines
    Then   each language has its block, the diagnostics follow, and the outcome closes it
    """
    group = _group(tmp_path / "store", _records(), steps=(Step.CORRECT,))

    lines = gates.GateTwoRule().judge(group).lines()

    assert "## de" in lines
    assert "## en" in lines
    assert "## diagnostics" in lines
    assert lines[-1] == "→ pass"
