"""Tests of the export: a local run cache becomes a committed run file."""

from __future__ import annotations

import json

import pytest

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.domain.correction_review import ChangeNote
from teachers_teammate.infrastructure import correction
from tests.tools import invented_corpus
from tests.tools.store_workspace import (
    DIGEST,
    MACHINE,
    MODEL,
    TESTSET,
    append_session,
    entry,
    export,
    first_item_as,
    one,
    review,
    session,
    source_of,
    workspace,
)
from tools.model_eval import export as export_module
from tools.model_eval import layout, protocol, redaction, run_format, testset, validation
from tools.model_eval.redaction import ELLIPSIS


# ── what an export writes ────────────────────────────────────────────────────


def test_an_exported_run_passes_format_mode_and_strict_mode(tmp_path):
    """
    Given  a local run of the invented test set
    When   it is exported
    Then   the run file passes the format-mode and the strict-mode validation and covers all seven
           items
    """
    space = workspace(tmp_path)
    written = one(space)
    assert validation.validate_run(written.path) == []
    assert validation.validate_run(written.path, space.corpora) == []
    assert written.items == 7
    assert written.run_id == written.path.stem


def test_the_run_id_follows_the_scheme(tmp_path):
    """
    Given  a local run made on one machine with one model, mode and temperature
    When   it is exported
    Then   the run id is date, machine, model slug, digest, mode, temperature and repeat
    """
    written = one(workspace(tmp_path))
    assert written.run_id == f"2026-11-09_{MACHINE}_gemma4-e2b_01234567_review_T0.7_r1"


def test_the_header_records_what_the_run_was_made_with(tmp_path):
    """
    Given  a local run with its header of model, seed, context and temperature
    When   it is exported
    Then   the run header records them, the machine, the digest, the manifest hash, the protocol
           the run measured and the instructions hash, but not the texts
    """
    written = one(workspace(tmp_path))
    header = run_format.read_run(written.path).header
    assert header["schema"] == run_format.RUN_SCHEMA
    assert header["type"] == "run"
    assert header["testset"] == TESTSET
    assert header["manifest_sha256"] == testset.manifest_sha256(
        written.path.parents[2] / "testsets" / TESTSET / "manifest.json"
    )
    assert header["machine"] == MACHINE
    assert header["model"] == MODEL
    assert header["model_info"]["digest"] == DIGEST
    assert header["seed"] == 111
    assert header["num_ctx"] == 4096
    assert header["temperature"] == 0.7
    assert header["kind"] == "quality"
    assert header["texts"] == 7
    assert "output_sha256" not in header
    assert header["eval_version"] == protocol.current_stamp().eval_version
    assert header["fingerprint"] == protocol.fingerprint_sha256()
    assert header["answer_format_version"] == ANSWER_FORMAT_VERSION
    assert header["instructions_sha256"] == protocol.instructions_sha256(["de", "en", "fr"])
    assert "tool_dirty" not in header


def test_the_manifest_is_copied_next_to_the_runs_it_pins(tmp_path):
    """
    Given  a run exported into an empty repository directory
    When   the export has finished
    Then   the manifest is stored next to the runs and is the one the run pins
    """
    space = workspace(tmp_path)
    export(space)
    copied = space.out / "testsets" / TESTSET / "manifest.json"
    assert testset.manifest_sha256(copied) == testset.manifest_sha256(space.manifest)


def test_a_second_export_of_a_changed_manifest_is_refused(tmp_path):
    """
    Given  a test set that is pinned in the repository already
    When   another manifest under the same test-set id is exported
    Then   it is refused, because another selection is another test-set id
    """
    space = workspace(tmp_path)
    export(space)
    other = invented_corpus.manifest(space.root, space.corpora, testset_id=TESTSET)
    other["built"] = "2026-12-01"
    testset.write_manifest(space.manifest, other)
    with pytest.raises(ValueError, match="another selection is another test-set id"):
        export(space, label="second")


def test_the_items_carry_the_stored_numbers_and_the_models_edits(tmp_path):
    """
    Given  a model that corrects the invented texts perfectly
    When   the run is exported
    Then   the items carry the scores, the model's edits as positions and the reference outcomes,
           and a correct text has no edits
    """
    written = one(workspace(tmp_path))
    items = {item["item"]: item for item in run_format.read_run(written.path).records}
    de01 = items["de-01"]
    assert de01["scores"]["tp"] == 1
    assert de01["scores"]["fp"] == 0
    assert de01["edits"] == [[4, 5, {"r": "läuft"}]]
    assert de01["reference"] == [[True, True, None]]
    assert items["de-correct"]["edits"] == []
    assert items["de-correct"]["reference"] == []


def test_the_scores_of_an_item_with_added_marks_agree_with_its_reference_outcomes(tmp_path):
    """
    Given  a model that wraps its correct fix of one text in bold marks
    When   the run is exported
    Then   the item's true positives are the exact matches of its reference outcomes, nothing is
           counted as a false positive, and `marks_added` says the marks were there
    """
    space = workspace(tmp_path, overrides={"de-01": lambda perfect: f"**{perfect}**"})
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")
    exact = sum(1 for outcome in item["reference"] if outcome[0])
    assert item["scores"]["marks_added"] == 1
    assert (item["scores"]["tp"], item["scores"]["fp"], item["scores"]["fn"]) == (1, 0, 0)
    assert item["scores"]["tp"] == exact
    assert len(item["edits"]) == item["scores"]["tp"] + item["scores"]["fp"]


def test_a_note_is_linked_to_the_edit_it_explains(tmp_path):
    """
    Given  a model that explains its one change with a note
    When   the run is exported
    Then   the note names the index of the edit it explains and is not an orphan
    """
    space = workspace(
        tmp_path,
        reviews={
            "de-01": review(
                ChangeNote(
                    original="laufen",
                    corrected="läuft",
                    category="grammar",
                    explanation="Das Verb muss im Singular stehen.",
                ),
                original="Der Hund laufen schnell.",
                corrected="Der Hund läuft schnell.",
            )
        },
    )
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")
    (note,) = item["notes"]
    assert note["edit"] == 0
    assert note["category"] == "grammar"
    assert note["explanation"] == "Das Verb muss im Singular stehen."
    assert note["orphan"] is False
    assert item["scores"]["notes_linked"] is True


def test_the_output_hash_is_the_source_with_the_edits_applied(tmp_path):
    """
    Given  an exported item and the source it was made from
    When   the stored edits are applied to the source
    Then   the result hashes to the output_sha256 of the item, which is what a reader of the run
           checks
    """
    space = workspace(tmp_path)
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")
    resolved = testset.resolve(entry(space, "de-01"), space.corpora)
    applied = run_format.apply_stored_edits(
        resolved.source, [(edit[0], edit[1], edit[2]["r"]) for edit in item["edits"]]
    )
    assert applied == resolved.reference
    assert item["output_sha256"] == testset.sha256_text(applied)


# ── the rule against corpus text ─────────────────────────────────────────────


def test_an_explanation_that_copies_the_source_is_cut(tmp_path):
    """
    Given  a model whose explanation copies five words of the source
    When   the run is exported
    Then   the explanation is cut to an ellipsis, its hash is kept and one field is counted as cut
    """
    space = workspace(
        tmp_path,
        reviews={
            "de-01": review(
                ChangeNote(
                    original="laufen",
                    corrected="läuft",
                    category="grammar",
                    explanation="Hier steht Der Hund laufen schnell . und das ist falsch.",
                ),
                original="Der Hund laufen schnell.",
                corrected="Der Hund läuft schnell.",
            )
        },
    )
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")
    (note,) = item["notes"]
    assert note["explanation"] == ELLIPSIS
    assert len(note["explanation_sha256"]) == 64
    assert written.cut_fields == 1


def test_a_long_replacement_is_stored_as_a_hash(tmp_path):
    """
    Given  a model that inserts nine new words
    When   the run is exported
    Then   the long replacement is stored as a hash and word count while the short one keeps its
           text, and the file still validates
    """
    nine = "heute sehr weit durch den tiefen Wald und zurück"
    space = workspace(tmp_path, overrides={"de-01": f"Der Hund läuft {nine}."})
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")
    assert [4, 5, {"r": "läuft"}] in item["edits"]
    assert [6, 7, {"h": redaction.sha256_text(nine), "n": 9}] in item["edits"]
    assert written.cut_fields == 1
    assert validation.validate_run(written.path) == []


def test_a_short_replacement_of_the_models_own_words_keeps_its_text(tmp_path):
    """
    Given  a model that replaces one word with another of its own
    When   the run is exported
    Then   the replacement is stored as text, rebuilds the model's text and no field is cut
    """
    space = workspace(tmp_path, overrides={"de-01": "Der Hund rennt schnell."})
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "de-01")
    assert item["edits"] == [[4, 5, {"r": "rennt"}]]
    assert (
        run_format.apply_stored_edits(
            source_of(space, "de-01"), [(edit[0], edit[1], edit[2]["r"]) for edit in item["edits"]]
        )
        == "Der Hund rennt schnell."
    )
    assert written.cut_fields == 0


def test_a_restricted_item_stores_no_words(tmp_path):
    """
    Given  a model that changes a text of a restricted corpus and explains it
    When   the run is exported
    Then   no replacement or explanation text is stored, only hashes, and the file still validates
    """
    space = workspace(
        tmp_path,
        overrides={"en-correct": lambda text: text.replace("quiet", "calm")},
        reviews={
            "en-correct": review(
                ChangeNote(
                    original="quiet",
                    corrected="calm",
                    category="word_choice",
                    explanation="The river runs through the town, so calm fits better.",
                ),
                original="It is quiet there.",
                corrected="It is calm there.",
            )
        },
    )
    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "en-correct")
    assert item["edits"], "the model did change the text"
    for edit in item["edits"]:
        assert set(edit[2]) == {"h", "n"}, edit
    (note,) = item["notes"]
    assert note["explanation"] == ""
    assert len(note["explanation_sha256"]) == 64
    assert validation.validate_run(written.path) == []


# ── what the export refuses ──────────────────────────────────────────────────


def test_a_run_of_a_dirty_tree_exports_normally(tmp_path):
    """
    Given  a run made with uncommitted changes in the tool
    When   it is exported
    Then   the run is written and keeps the revision as information, because nothing depends on
           the state of the tree any more
    """
    written = one(workspace(tmp_path, tool_revision="cfdc0ea+dirty"))
    header = run_format.read_run(written.path).header
    assert header["tool_revision"] == "cfdc0ea+dirty"
    assert "tool_dirty" not in header
    assert validation.validate_run(written.path) == []


def test_the_export_copies_the_instructions_hash_of_the_run_not_of_today(tmp_path, monkeypatch):
    """
    Given  a run made with today's prompts
    When   a prompt changes and the run is exported afterwards
    Then   the header carries the hash the run recorded, not the one the changed prompt would give
    """
    space = workspace(tmp_path)
    recorded = protocol.instructions_sha256(["de", "en", "fr"])
    monkeypatch.setitem(correction.PROMPTS, "german", "Ein anderer Prompt für {level}.")
    assert protocol.instructions_sha256(["de", "en", "fr"]) != recorded

    header = run_format.read_run(one(space).path).header

    assert header["instructions_sha256"] == recorded


def test_a_session_header_without_the_protocol_fields_is_refused(tmp_path):
    """
    Given  a local run made before the protocol was recorded
    When   it is exported
    Then   it is refused, because nobody can say which prompts and window it measured
    """
    space = workspace(tmp_path)
    path = space.cache / "run.header.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    for key in export_module.PROTOCOL_FIELDS:
        del entries[0][key]
    path.write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(ValueError, match="before the protocol was recorded — run it again"):
        export(space)


def test_a_version_pair_the_protocol_does_not_record_is_refused(tmp_path):
    """
    Given  a local run whose version and fingerprint are not an entry of the version file
    When   it is exported
    Then   it is refused, because a run made with an unrecorded protocol is evidence of nothing
    """
    space = workspace(tmp_path)
    path = space.cache / "run.header.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries[0]["fingerprint"] = "0" * 64
    path.write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(ValueError, match=r"not in protocol\.json"):
        export(space)


def test_two_sessions_that_disagree_on_the_digest_are_refused(tmp_path):
    """
    Given  a run resumed after the model was pulled again, so the digest changed
    When   it is exported
    Then   it is refused, because the two sessions are not one run
    """
    space = workspace(tmp_path)
    append_session(space, digest="sha256:fedcba9876543210fedcba9876543210")

    with pytest.raises(ValueError, match="the sessions of this run disagree"):
        export(space)


def test_two_sessions_that_agree_export_with_the_date_of_the_first(tmp_path):
    """
    Given  a run resumed the next day, with the same protocol, model and settings
    When   it is exported
    Then   it is one run, and its id carries the date the run started
    """
    space = workspace(tmp_path)
    append_session(space, date="2026-11-10T09:00:00", tool_revision="cfdc0ea+dirty")

    written = one(space)

    assert written.run_id.startswith("2026-11-09_")
    assert run_format.read_run(written.path).header["date"] == "2026-11-09T18:00:00"


def test_an_existing_run_id_is_refused(tmp_path):
    """
    Given  a run exported once already
    When   the same cache is exported again
    Then   it is refused and the first file stays valid
    """
    space = workspace(tmp_path)
    first = one(space)
    with pytest.raises(ValueError, match="exists already; use --label"):
        export(space)
    assert validation.validate_run(first.path) == []


def test_a_label_makes_a_second_run_of_one_configuration(tmp_path):
    """
    Given  a run exported once already
    When   the same cache is exported again with a label
    Then   a second run file ends in the label
    """
    space = workspace(tmp_path)
    export(space)
    second = one(space, label="nocache")
    assert second.run_id.endswith("_nocache")
    assert len(layout.run_files(space.out)) == 2


def test_a_test_set_that_is_not_the_manifests_is_refused(tmp_path):
    """
    Given  a manifest of one test set
    When   an export names another test set
    Then   it is refused
    """
    space = workspace(tmp_path)
    with pytest.raises(ValueError, match="not 'gec-1999-01'"):
        export(space, testset="gec-1999-01")


def test_a_row_of_an_item_the_manifest_does_not_have_is_refused(tmp_path):
    """
    Given  a local run with a row for an item the manifest does not have
    When   it is exported
    Then   it is refused because the row belongs to another test set
    """
    space = workspace(tmp_path)
    rows = space.cache / "run.jsonl"
    lines = rows.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace('"de/de-01"', '"de/de-99"')
    rows.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="belong to another test set"):
        export(space)


def test_a_row_for_a_text_of_the_set_is_missing_is_refused(tmp_path):
    """
    Given  a local run that lacks the row of one text of the set
    When   it is exported
    Then   it is refused, because a committed run covers the whole test set
    """
    space = workspace(tmp_path)
    rows = space.cache / "run.jsonl"
    lines = rows.read_text(encoding="utf-8").splitlines()
    rows.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="a committed run covers the whole test set"):
        export(space)


def test_a_cache_without_headers_is_not_exported(tmp_path):
    """
    Given  a local cache whose header file is gone
    When   it is exported
    Then   nothing is written
    """
    space = workspace(tmp_path)
    (space.cache / "run.header.json").unlink()
    assert export(space) == []


def test_a_machine_file_that_is_missing_is_refused(tmp_path):
    """
    Given  an export for a machine that has no file in the repository
    When   it is exported
    Then   it is refused with the missing machine file named
    """
    space = workspace(tmp_path)
    (space.out / "machines" / f"{MACHINE}.json").unlink()
    with pytest.raises(ValueError, match="no such machine file"):
        export(space)


def test_corpora_that_are_not_the_pinned_ones_are_refused(tmp_path):
    """
    Given  corpora one of whose files has changed since the manifest was built
    When   a run is exported
    Then   it is refused because they are not the ones the manifest pinned
    """
    space = workspace(tmp_path)
    path = space.corpora.root("falko-merlin") / "fm-test.m2"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not the ones the manifest pinned"):
        export(space)


def test_a_text_whose_first_piece_fell_back_exports_as_a_partial_review(tmp_path):
    """
    Given  a text of several pieces, one of which fell back, so the others kept their review
    When   it is exported and the run file is validated
    Then   the outcome says review_partial with a fallback and a review, and it validates
    """
    path = first_item_as(
        workspace(tmp_path),
        fallback=True,
        has_review=True,
        warning=f"{correction.FALLBACK_WARNING} {correction.MISSING_REVIEW_WARNING}",
        pieces=3,
    )

    outcome = run_format.read_run(path).records[0]["outcome"]

    assert (outcome["warning_code"], outcome["fallback"], outcome["has_review"]) == (
        "review_partial",
        True,
        True,
    )
    assert outcome["pieces"] == 3
    assert validation.validate_run(path) == []


def test_a_text_that_fell_back_as_a_whole_exports_as_a_fallback(tmp_path):
    """
    Given  a text that fell back to corrections only and has no review
    When   it is exported and the run file is validated
    Then   the outcome says fallback, and it validates
    """
    path = first_item_as(
        workspace(tmp_path),
        fallback=True,
        has_review=False,
        warning=correction.FALLBACK_WARNING,
    )

    assert run_format.read_run(path).records[0]["outcome"]["warning_code"] == "fallback"
    assert validation.validate_run(path) == []
