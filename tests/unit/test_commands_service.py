"""Unit tests for the writes: the teacher's texts and decisions."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import dataclasses
from pathlib import Path
import shutil
from unittest.mock import MagicMock
import pytest
from teachers_teammate.application.commands import DocumentCommands, WorkNotSavedError
from teachers_teammate.application.run import windows_for
from teachers_teammate.domain.teacher_work import CorrectionsWork, current_texts, with_corrected
from teachers_teammate.infrastructure.preview_image_store import PreviewImageStore
from teachers_teammate.infrastructure.state_repository import (
    OcrResultRecord,
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore, WorkTooNewError
from teachers_teammate.infrastructure.workflow.cache_service import (
    CacheReconciliationService,
    compute_correction_config_hash,
)
from tests.conftest import make_config
from ._service_helpers import (
    _change_work,
    _commands_for,
    _machine_of,
    _make_svc,
    _proofread,
    _repo_with_document,
    reader_models_in_place,
)


@pytest.fixture(autouse=True)
def _reader_models_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every service of this file a store that holds the built-in reader's models."""
    reader_models_in_place(tmp_path, monkeypatch)


@pytest.mark.unit
def test_invalidate_from_stage_static_resets_exactly_the_files_it_is_given(tmp_path: Path) -> None:
    """
    Given  one file and a store
    When   _invalidate_from_stage() is called
    Then   that file is invalidated in the store and nothing failed
    """
    source = tmp_path / "doc.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.return_value = MagicMock()

    failures = DocumentCommands._invalidate_from_stage(
        stage="ocr",
        files=[source],
        store=mock_store,
    )

    mock_store.invalidate_from_stage.assert_called_once_with(source, "ocr")
    assert failures == ()


@pytest.mark.unit
def test_invalidate_from_stage_static_accumulates_oserror_failures(tmp_path: Path) -> None:
    """
    Given  a store whose invalidate_from_stage raises OSError
    When   _invalidate_from_stage() is called
    Then   the failure names the file and keeps the reason
    """
    source = tmp_path / "bad.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.side_effect = OSError("disk full")

    failures = DocumentCommands._invalidate_from_stage(
        stage="ocr",
        files=[source],
        store=mock_store,
    )
    assert failures == (("bad.png", "disk full"),)


# ── The teacher's texts and decisions ──────────────────────────────────────


@pytest.mark.unit
def test_edit_transcription_stores_the_teachers_text(tmp_path: Path) -> None:
    """
    Given  a document whose transcription the machine read
    When   edit_transcription() is called with the teacher's text
    Then   the view carries that text and the cache keeps the machine's own
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)

    view = commands.edit_transcription(
        make_config(tmp_path), source=source, text="hello, world", text_format="plain"
    )

    assert view is not None
    assert view.raw_text == "hello, world"
    assert view.work.transcription_changed is True
    assert repo.load_valid(source).raw_text == "hello world"


@pytest.mark.unit
def test_edit_transcription_resets_the_proofreading_and_drops_the_part(tmp_path: Path) -> None:
    """
    Given  a document with a proofreading the teacher checked
    When   edit_transcription() is called
    Then   the cache's correction stage is reset and the corrections part is gone
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    commands = _commands_for(repo)
    config = make_config(tmp_path)
    assert commands.set_checked(config, source=source, checked=True) is not None

    view = commands.edit_transcription(config, source=source, text="hello, world")

    assert view is not None
    assert view.correction_text == ""
    assert view.work.corrections_checked is False
    assert repo.load_valid(source).correction_done is False


@pytest.mark.unit
def test_edit_transcription_keeps_the_transcription_check(tmp_path: Path) -> None:
    """
    Given  a transcription the teacher checked
    When   edit_transcription() is called
    Then   the check stays: it names the text they were given, and they changed it
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)
    config = make_config(tmp_path)
    assert commands.set_checked(config, source=source, checked=True) is not None

    view = commands.edit_transcription(config, source=source, text="hello, world")

    assert view is not None
    assert view.work.transcription_checked is True


@pytest.mark.unit
def test_edit_corrected_text_without_a_proofreading_returns_none(tmp_path: Path) -> None:
    """
    Given  a document the machine read but did not proofread
    When   edit_corrected_text() is called
    Then   None is returned: there is no answer to bind the corrections to
    """
    repo, source = _repo_with_document(tmp_path)

    view = _commands_for(repo).edit_corrected_text(
        make_config(tmp_path), source=source, text="Hello world."
    )

    assert view is None


@pytest.mark.unit
def test_edit_corrected_text_stores_the_teachers_corrections(tmp_path: Path) -> None:
    """
    Given  a document with a stored proofreading
    When   edit_corrected_text() is called with the teacher's text
    Then   the view carries it as the corrected text and the machine's own next to it
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    commands = _commands_for(repo)

    view = commands.edit_corrected_text(make_config(tmp_path), source=source, text="Hello, world!")

    assert view is not None
    assert view.correction_text == "Hello, world!"
    assert view.machine_correction_text == "Hello world."
    assert view.work.corrections_changed is True
    assert repo.load_valid(source).correction_text == "Hello world."


@pytest.mark.unit
@pytest.mark.use_case("Mark_Document_Checked")
def test_set_checked_on_a_transcription_stores_a_copy(tmp_path: Path) -> None:
    """
    Given  a document the machine read but did not proofread
    When   set_checked() is called
    Then   the transcription is checked and stored as a copy of the text it was given
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)

    view = commands.set_checked(make_config(tmp_path), source=source, checked=True)

    assert view is not None
    assert view.work.transcription_checked is True
    assert view.work.checked_label == "text"
    assert view.raw_text == "hello world"


@pytest.mark.unit
def test_a_refused_write_is_reported_and_resets_nothing(tmp_path: Path) -> None:
    """
    Given  a work store that refuses to write (a file saved by a newer version)
    When   edit_transcription() is called for a document that has a proofreading
    Then   WorkNotSavedError says so (too new), and the stored proofreading is untouched
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = MagicMock()
    work_store.update.side_effect = WorkTooNewError("newer")
    commands = _commands_for(repo, work_store=work_store)

    with pytest.raises(WorkNotSavedError) as raised:
        commands.edit_transcription(make_config(tmp_path), source=source, text="mine")

    assert raised.value.too_new is True
    assert raised.value.name == source.name
    stored = repo.load_valid(source)
    assert stored is not None
    assert stored.correction_done
    assert stored.correction_text == "Hello world."


@pytest.mark.unit
def test_a_write_that_fails_is_reported_not_too_new(tmp_path: Path) -> None:
    """
    Given  a work store that cannot write (a folder that is not writable)
    When   the teacher marks the document as checked
    Then   WorkNotSavedError says so, and says it is not a newer version's file
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = MagicMock()
    work_store.update.side_effect = PermissionError("denied")
    commands = _commands_for(repo, work_store=work_store)

    with pytest.raises(WorkNotSavedError) as raised:
        commands.set_checked(make_config(tmp_path), source=source, checked=True)

    assert raised.value.too_new is False


@pytest.mark.unit
def test_invalidate_from_stage_drops_the_parts_the_stage_takes(tmp_path: Path) -> None:
    """
    Given  a document whose transcription and corrections the teacher worked on
    When   the proofreading is re-run, then the reading
    Then   the corrections part goes first, and the transcription part with the reading
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = TeacherWorkStore(tmp_path / "work")
    commands = _commands_for(repo, work_store=work_store)
    config = make_config(tmp_path)
    commands.edit_transcription(config, source=source, text="hello, world")
    commands.edit_corrected_text(config, source=source, text="Hello, world!")
    source_hash = compute_file_hash(source)

    commands.invalidate_from_stage(config, files=[source], stage="correction")

    work = work_store.load(source_hash)
    assert work is not None
    assert work.corrections is None
    assert work.transcription is not None

    commands.invalidate_from_stage(config, files=[source], stage="ocr")

    assert work_store.load(source_hash) is None


@pytest.mark.unit
def test_a_teachers_transcription_survives_reconcile(tmp_path: Path) -> None:
    """
    Given  a transcription the teacher fixed
    When   the cache is reconciled against the same (unchanged) runtime config
    Then   the reading is not redone and the teacher's text is still the one the view shows
    """
    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path, correction_enabled=False)

    repo = StateRepository(tmp_path / "state")
    work_store = TeacherWorkStore(tmp_path / "work")
    commands = DocumentCommands(
        state_store_factory=lambda _c: repo,
        work_store=work_store,
    )
    repo.record_ocr_result(
        source,
        repo.load_or_create(source, compute_file_hash(source)),
        OcrResultRecord(
            source_hash=compute_file_hash(source),
            raw_text="hello",
            preview_img="",
            source_image="",
            ocr_config_hash="ocr",
            preview_config_hash="",
        ),
    )
    commands.edit_transcription(cfg, source=source, text="MANUAL")

    recon = CacheReconciliationService(
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
        work_store=work_store,
    )
    ctx = recon.prepare(source)

    assert ctx.state.ocr_done is True
    assert ctx.state.raw_text == "hello"
    assert ctx.work is not None
    assert ctx.work.transcription is not None
    assert ctx.work.transcription.text == "MANUAL"


# ── What was sent ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_invalidate_from_stage_static_invalid_stage_propagates_value_error(tmp_path: Path) -> None:
    """
    Given  a non-empty files list and an invalid stage name
    When   _invalidate_from_stage() is called
    Then   ValueError is raised (normalize_stage rejects the unknown stage name)
    """
    source = tmp_path / "doc.png"
    source.touch()

    with pytest.raises(ValueError, match="Invalid stage"):
        DocumentCommands._invalidate_from_stage(
            stage="nonexistent",
            files=[source],
            store=MagicMock(),
        )


@pytest.mark.unit
def test_edit_transcription_stores_the_notation_it_was_given(tmp_path: Path) -> None:
    """
    Given  a Markdown transcription shown in the GUI
    When   edit_transcription() is called with text_format="markdown"
    Then   the view keeps the Markdown notation and the teacher's text
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)

    view = commands.edit_transcription(
        make_config(tmp_path), source=source, text="~~a~~ b!", text_format="markdown"
    )

    assert view is not None
    assert (view.raw_text, view.raw_text_format) == ("~~a~~ b!", "markdown")


# ── Run preflight & model-list caching ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_checking_a_document_whose_proofreading_was_kept_leaves_it_kept(tmp_path: Path) -> None:
    """
    Given  a proofreading the teacher worked on, made with other settings than today's
    When   the teacher marks the document as checked
    Then   the view that comes back still has the proofreading done, and says it was kept
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    view = svc.commands.set_checked(other, source=worked, checked=True)

    assert view is not None
    assert view.correction_done is True
    assert view.work.kept_over_settings is True
    assert view.work.corrections_checked is True


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_a_stored_mark_shows_a_stage_stale_by_its_settings_as_not_done(tmp_path: Path) -> None:
    """
    Given  a proofread document the teacher did nothing to, and other proofreading settings
    When   a mark that is not work (an unchecked toggle) is stored
    Then   the view says the proofreading is not done, as the queue and the run would
    """
    _made, repo, [source] = _proofread(tmp_path, "essay.txt")
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    view = svc.commands.set_checked(other, source=source, checked=False)

    assert view is not None
    assert view.correction_done is False
    assert view.work.kept_over_settings is False


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_corrections_of_no_stored_answer_are_dropped_and_not_asked_about_again(
    tmp_path: Path,
) -> None:
    """
    Given  corrections the teacher worked on whose proofreading is gone from the cache
    When   the teacher went on with the run and the corrections are dropped
    Then   the next look at the run no longer names them
    """
    made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    shutil.rmtree(tmp_path / "state")
    svc = _make_svc(state_repository_factory=lambda _config: repo)
    assert svc.documents.worked_on_documents(made, [worked], run_proofreads=True).unbound == (
        worked,
    )

    svc.commands.drop_unbound_corrections(made, files=[worked])

    assert svc.documents.worked_on_documents(made, [worked], run_proofreads=True).unbound == ()
    assert TeacherWorkStore().load(compute_file_hash(worked)) is None


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_corrections_that_apply_are_not_dropped_as_unbound(tmp_path: Path) -> None:
    """
    Given  corrections the teacher worked on whose proofreading is still stored
    When   the unbound corrections are dropped
    Then   these are left alone: they belong to a stored answer
    """
    made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    svc.commands.drop_unbound_corrections(made, files=[worked])

    work = TeacherWorkStore().load(compute_file_hash(worked))
    assert work is not None and work.corrections is not None
    assert work.corrections.text == "corrected by the teacher"


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_keeping_the_changes_stamps_the_settings_on_corrections_that_apply(tmp_path: Path) -> None:
    """
    Given  a document whose corrections apply to the stored proofreading
    When   *Keep my changes* is recorded for it, with the files themselves
    Then   the corrections remember the settings they were kept over
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    svc.commands.keep_teacher_work(other, files=[worked])

    stamped = TeacherWorkStore().load(compute_file_hash(worked))
    assert stamped is not None and stamped.corrections is not None
    assert stamped.corrections.kept_under == compute_correction_config_hash(
        other, windows_for(other, recommendations=svc.recommendations)
    )


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_keeping_the_changes_does_not_stamp_corrections_of_another_answer(tmp_path: Path) -> None:
    """
    Given  corrections the teacher worked on, written on an answer that is not the stored one
    When   *Keep my changes* is recorded for the document
    Then   they are not stamped: they are not what the teacher kept
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: dataclasses.replace(
            work, corrections=CorrectionsWork(bound_to="another-answer", text="Anders")
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    svc.commands.keep_teacher_work(other, files=[worked])

    stamped = TeacherWorkStore().load(compute_file_hash(worked))
    assert stamped is not None and stamped.corrections is not None
    assert stamped.corrections.kept_under == ""


@pytest.mark.unit
def test_a_rerun_that_cannot_drop_the_work_resets_nothing(tmp_path: Path) -> None:
    """
    Given  a document the teacher worked on, whose work cannot be written
    When   the proofreading is invalidated for a re-run
    Then   the failure is reported, the cache is left as it was, and the document is not run
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = MagicMock()
    work_store.update.side_effect = PermissionError("denied")

    failures = DocumentCommands._invalidate_from_stage(
        stage="correction",
        files=[source],
        store=repo,
        work_store=work_store,
    )

    assert len(failures) == 1 and source.name in failures[0]
    stored = repo.load_valid(source)
    assert stored is not None and stored.correction_done
