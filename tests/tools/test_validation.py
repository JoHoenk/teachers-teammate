"""Tests of the validator: format mode without the corpora, strict mode with them."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.domain.correction_review import ChangeNote
from tests.tools.store_workspace import (
    COMMITTED,
    export,
    one,
    review,
    rewrite,
    workspace,
)
from tools.model_eval import export as export_module
from tools.model_eval import layout, protocol, run_format, testset, validation


# ── format mode ─────────────────────────────────────────────────────────────


def test_format_mode_refuses_a_field_that_looks_like_a_sentence(tmp_path):
    """
    Given  a committed run with an unknown field that holds a whole sentence
    When   it is validated in format mode
    Then   the field is refused as sentence-like
    """
    written = one(workspace(tmp_path))

    def change(header, items):
        items[0]["unexpected"] = "Hier steht ein ganzer Satz, der aus dem Korpus stammt"

    rewrite(written.path, change)
    problems = validation.validate_run(written.path)
    assert any("sentence-like" in problem for problem in problems)


def test_format_mode_refuses_a_header_that_disagrees_with_the_file_name(tmp_path):
    """
    Given  a run file whose header names another run
    When   it is validated in format mode
    Then   the mismatch is reported
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(run_id="another-run"))
    problems = validation.validate_run(written.path)
    assert any("the header names the run" in problem for problem in problems)


def test_format_mode_refuses_a_run_of_another_schema(tmp_path):
    """
    Given  a run file of an unknown schema version
    When   it is validated in format mode
    Then   the schema is refused
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(schema=99))
    assert any("unsupported run schema" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_a_run_without_an_eval_version(tmp_path):
    """
    Given  a run file that does not say which protocol it measured
    When   it is validated in format mode
    Then   the missing version is refused, because runs are grouped by it
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.pop("eval_version"))
    assert any("the header has no eval_version" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_a_version_pair_the_protocol_does_not_record(tmp_path):
    """
    Given  a run file whose fingerprint is not the one its version records
    When   it is validated in format mode
    Then   the pair is refused, because the run measured an unrecorded protocol
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(fingerprint="0" * 64))
    assert any("is not in protocol.json" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_a_mode_that_is_not_one_of_the_modes(tmp_path):
    """
    Given  a run file whose header names an answer mode the tools do not know
    When   it is validated in format mode
    Then   the mode is refused, naming the allowed values
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(mode="chat"))
    assert any("mode is 'chat'" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_an_explainer_in_a_mode_that_has_none(tmp_path):
    """
    Given  a run file that names an explaining model although it was asked in review mode
    When   it is validated in format mode
    Then   the explainer is refused, because only two-step has one
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(explainer="gemma4:e4b"))
    assert any("explainer is set" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_a_header_with_the_legacy_key(tmp_path):
    """
    Given  a run file whose header still carries the removed `legacy` key
    When   it is validated in format mode
    Then   the key is refused as an unknown field, because the conversion was dropped
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(legacy=False))
    assert any("unknown fields legacy" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_a_manifest_that_differs(tmp_path):
    """
    Given  a run that pins another manifest hash than the manifest file has
    When   it is validated in format mode
    Then   the difference is reported
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(manifest_sha256="0" * 64))
    assert any("pins the manifest" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_a_replacement_of_nine_words(tmp_path):
    """
    Given  a stored replacement of nine words
    When   the run is validated in format mode
    Then   it is refused because at most eight may be stored
    """
    written = one(workspace(tmp_path))

    def change(header, items):
        items[0]["edits"] = [[4, 5, {"r": " ".join(["wort"] * 9)}]]

    rewrite(written.path, change)
    assert any("at most 8 may be stored" in p for p in validation.validate_run(written.path))


def test_format_mode_refuses_words_in_a_restricted_item(tmp_path):
    """
    Given  a restricted item that stores a replacement and an explanation as text
    When   the run is validated in format mode
    Then   both are refused
    """
    written = one(workspace(tmp_path))

    def change(header, items):
        item = next(i for i in items if i["item"] == "en-correct")
        item["edits"] = [[0, 1, {"r": "The"}]]
        item["notes"] = [
            {
                "edit": 0,
                "category": "other",
                "explanation": "Klar.",
                "explanation_sha256": None,
                "orphan": False,
            }
        ]

    rewrite(written.path, change)
    problems = validation.validate_run(written.path)
    assert any("a restricted item stores no words" in problem for problem in problems)
    assert any("a restricted item stores no explanation text" in problem for problem in problems)


def test_output_is_refused_as_a_field_name(tmp_path):
    """
    Given  an item with a field called output that holds a text
    When   the run is validated in format mode
    Then   the field is refused
    """
    written = one(workspace(tmp_path))

    def change(header, items):
        items[0]["output"] = "Der Hund läuft schnell."

    rewrite(written.path, change)
    assert any("'output' holds text" in p for p in validation.validate_run(written.path))


def test_strict_mode_refuses_a_copied_run_of_source_words(tmp_path):
    """
    Given  a stored replacement that copies a run of source words
    When   the run is validated without and with the corpora
    Then   format mode passes it and strict mode refuses it
    """
    space = workspace(tmp_path)
    written = one(space)

    def change(header, items):
        items[0]["edits"] = [[4, 5, {"r": "Der Hund laufen schnell ."}]]

    rewrite(written.path, change)
    assert validation.validate_run(written.path) == []
    problems = validation.validate_run(written.path, space.corpora)
    assert any("copies a run of corpus words" in problem for problem in problems)


def test_a_category_is_only_given_for_an_error_the_model_fixed_exactly(tmp_path):
    """
    Given  a model that fixes the German error with the wrong word and names a category for it
    When   the run is exported
    Then   the reference outcome is not exact and has no category judgement, like the scores
    """
    asked = review(
        ChangeNote(
            original="laufen",
            corrected="laufe",
            category="grammar",
            explanation="Falsche Form.",
        ),
        original="Der Hund laufen schnell.",
        corrected="Der Hund laufe schnell.",
    )
    space = workspace(
        tmp_path,
        overrides={"de-01": "Der Hund laufe schnell."},
        reviews={"de-01": asked},
    )
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")

    assert item["reference"] == [[False, True, None]]
    assert item["scores"]["category_checked"] == 0


def test_an_export_checks_the_run_in_strict_mode(tmp_path, monkeypatch):
    """
    Given  an export with the corpora at hand
    When   the run file has been written
    Then   it is validated against the corpora, not in format mode alone
    """
    space = workspace(tmp_path)
    corpora_seen = []
    real = validation.validate_run

    def spy(path, corpora=None, **kwargs):
        corpora_seen.append(corpora)
        return real(path, corpora, **kwargs)

    monkeypatch.setattr(export_module, "validate_run", spy)

    one(space)

    assert corpora_seen == [space.corpora]


def test_an_export_whose_rebuild_does_not_match_is_refused_and_removed(tmp_path, monkeypatch):
    """
    Given  an item whose stored edits do not rebuild to the hash of the model's text
    When   the run is exported
    Then   strict validation refuses it and no run file is left behind
    """
    space = workspace(tmp_path)
    real = export_module.item_record

    def tampered(*args, **kwargs):
        record = real(*args, **kwargs)
        if record["item"] == "de-01":
            record["output_sha256"] = "0" * 64
        return record

    monkeypatch.setattr(export_module, "item_record", tampered)

    with pytest.raises(ValueError, match="strict validation refused"):
        export(space)
    assert list((space.out / layout.RUNS_DIR).rglob("*.jsonl")) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"warning_code": "review_partial", "has_review": False}, "review_partial needs"),
        ({"warning_code": "fallback", "has_review": True}, "warning_code fallback needs"),
        ({"warning_code": "", "fallback": True}, "outcome.fallback needs"),
    ],
)
def test_format_mode_refuses_a_fallback_flag_that_contradicts_the_code(tmp_path, change, expected):
    """
    Given  a run file whose fallback flag, review flag and warning code contradict each other
    When   it is validated in format mode
    Then   the contradiction is named
    """
    written = one(workspace(tmp_path))
    rewrite(
        written.path,
        lambda header, items: items[0]["outcome"].update(
            {"fallback": change.get("fallback", True), **change}
        ),
    )

    assert any(expected in problem for problem in validation.validate_run(written.path))


def test_a_further_numeric_score_is_allowed_and_typed(tmp_path):
    """
    Given  a run whose items carry two numbers the schema does not know yet
    When   it is validated and its items are typed
    Then   it passes, and the typed item holds both numbers, so a new metric needs no schema change
    """
    space = workspace(tmp_path)
    written = one(space)
    rewrite(
        written.path,
        lambda header, items: items[0]["scores"].update({"gleu_matches": 12, "coverage": 0.75}),
    )

    assert validation.validate_run(written.path) == []
    run = run_format.read_run(written.path)
    item = run.items(testset.read_manifest(space.manifest))[0]
    assert item.scores["gleu_matches"] == 12
    assert item.scores["coverage"] == 0.75
    assert item.scores["notes_linked"] in (0, 1)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a sentence from the corpus", "scores.extra is not a number"),
        (True, "scores.extra is not a number"),
        (float("nan"), "scores.extra is not a number"),
        ([1], "scores.extra is not a number"),
    ],
)
def test_a_score_that_is_not_a_number_is_refused_with_its_key_named(tmp_path, value, expected):
    """
    Given  a run whose item carries a further score that is not a number
    When   it is validated in format mode
    Then   it is refused, naming the key, because a number is what keeps corpus text out
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: items[0]["scores"].update({"extra": value}))

    assert any(expected in problem for problem in validation.validate_run(written.path))


def test_an_exported_run_carries_the_numbers_of_every_error_item(tmp_path):
    """
    Given  an exported run of the scripted runner at the current eval version
    When   it is validated in format mode and its items are read
    Then   it passes, and every error item carries the GLEU and half counts
    """
    space = workspace(tmp_path)
    written = one(space)
    kinds = {entry["id"]: entry["kind"] for entry in testset.read_manifest(space.manifest)["items"]}

    assert validation.validate_run(written.path) == []
    for item in run_format.read_run(written.path).records:
        if kinds[item["item"]] == "errors":
            assert {"gleu_c", "gleu_src_c", "tp_h1", "fn_h2"} <= set(item["scores"])


def test_a_text_with_spans_without_coverage_is_refused(tmp_path):
    """
    Given  an exported run whose first item has sentence spans but no coverage
    When   it is validated in format mode
    Then   the missing keys are named, because a text with spans must say what was covered
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: items[0]["scores"].pop("cover"))

    assert any(
        "scores has no cover" in problem for problem in validation.validate_run(written.path)
    )


def test_halves_that_do_not_add_up_to_the_whole_text_are_refused(tmp_path):
    """
    Given  an exported run whose first item counts one match more in the first half than it has
    When   it is validated in format mode
    Then   the halves are refused, because they must add up to the counts of the whole text
    """
    written = one(workspace(tmp_path))

    def change(header, items):
        items[0]["scores"]["tp_h1"] += 1

    rewrite(written.path, change)

    assert any(
        "tp_h1 + tp_h2 is not tp" in problem for problem in validation.validate_run(written.path)
    )


def test_a_run_without_the_gleu_and_half_numbers_is_refused(tmp_path):
    """
    Given  an exported run whose items carry none of the GLEU, half and span numbers
    When   it is validated in format mode
    Then   it is refused, because every run of the one protocol version stores them
    """
    written = one(workspace(tmp_path))

    def change(header, items):
        for item in items:
            for key in (*validation.GLEU_KEYS, *validation.HALF_KEYS, *validation.SPAN_KEYS):
                item["scores"].pop(key, None)

    rewrite(written.path, change)

    assert validation.validate_run(written.path) != []


def test_the_committed_runs_pass_format_mode():
    """
    Given  the run files committed below evaluations/proofreading
    When   each is validated in format mode
    Then   none has a problem
    """
    if not COMMITTED.is_dir():
        pytest.skip("the repository has no evaluations/proofreading directory")
    runs = layout.run_files(COMMITTED)
    if not runs:
        pytest.skip("no committed run yet")
    assert {path: validation.validate_run(path) for path in runs} == {path: [] for path in runs}


@pytest.mark.parametrize("version", ["4", None, 4.5])
def test_an_eval_version_that_is_not_an_integer_is_reported_not_raised(tmp_path, version):
    """
    Given  a run file whose header has an eval_version that is not an integer
    When   it is validated in format mode
    Then   the version is reported as a problem and the validator does not raise
    """
    written = one(workspace(tmp_path))
    rewrite(written.path, lambda header, items: header.update(eval_version=version))

    problems = validation.validate_run(written.path)

    assert any("eval_version is not a positive integer" in problem for problem in problems)
