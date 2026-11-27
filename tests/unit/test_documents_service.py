"""Unit tests for what the store says about a document."""

from __future__ import annotations

import dataclasses
from pathlib import Path
import shutil
from teachers_teammate.domain.model_catalog import HardwareProfileSetting
from teachers_teammate.domain.stages import Stage
from unittest.mock import MagicMock, patch
import pytest
from teachers_teammate.application.correction_options import proofreading_prompts
from teachers_teammate.application.queue import StageState
from teachers_teammate.application.run import windows_for
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.domain.teacher_work import (
    current_texts,
    kept_over,
    with_corrected,
    with_looked_at,
    with_transcription,
)
from teachers_teammate.infrastructure.preview_image_store import PreviewImageStore
from teachers_teammate.infrastructure.state_repository import StateRepository
from teachers_teammate.cache_keys import correction_key, sent_record
from teachers_teammate.infrastructure.workflow.cache_service import (
    CacheReconciliationService,
)
from tests.conftest import make_config, make_key_facts
from ._service_helpers import (
    _change_work,
    _machine_of,
    _make_svc,
    _ollama_config,
    _proofread,
    _svc_with_document,
    reader_models_in_place,
)


@pytest.fixture(autouse=True)
def _reader_models_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every service of this file a store that holds the built-in reader's models."""
    reader_models_in_place(tmp_path, monkeypatch)


@pytest.mark.unit
def test_a_privacy_setting_change_keeps_the_proofreading_and_says_so(tmp_path: Path) -> None:
    """
    Given  a document proofread with personal info removal off
    When   the setting is switched on and the views are reconciled
    Then   the proofreading stays done (so the next run asks no corrector and runs no
           anonymizer) and the view says it was made with other privacy settings
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    off = make_config(tmp_path, correction_enabled=True, anonymization_enabled=False)
    on = make_config(tmp_path, correction_enabled=True, anonymization_enabled=True)

    repo = StateRepository(tmp_path / "state")
    artifacts = PreviewImageStore(tmp_path / "art")
    recon = CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=off
    )
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected", source_text="hello")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(on, [source])[0]

    assert row.correction is StageState.DONE
    assert row.sent[Stage.CORRECTION].record == sent_record(
        off, Stage.CORRECTION, facts=make_key_facts()
    )
    assert row.sent[Stage.CORRECTION].other_privacy_settings is True

    # The next run with the new settings: nothing is invalidated, so the corrector is skipped
    # and the anonymizer is never asked for a text that is not sent again.
    after = CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=on
    ).prepare(source)

    assert after.state.correction_done is True
    assert after.state.correction_text == "hello corrected"
    assert (
        after.state.correction_sent
        == sent_record(off, Stage.CORRECTION, facts=make_key_facts()).to_dict()
    )


@pytest.mark.unit
def test_a_stale_stage_shows_no_line_about_how_it_was_sent(tmp_path: Path) -> None:
    """
    Given  a document proofread with one model
    When   the views are reconciled with another model, so the proofreading is stale
    Then   the view says it is not done and carries no record of how it was sent, since the
           result it describes will be made again
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    made = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:9b")
    now = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=made,
    )
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(now, [source])[0]

    assert row.correction is StageState.STALE
    assert row.sent == {}


@pytest.mark.unit
def test_a_switched_off_proofreading_stays_done_and_is_kept(tmp_path: Path) -> None:
    """
    Given  a document proofread with the stage on
    When   the queue is reconciled with the stage switched off, and a run with it off follows
    Then   the proofreading still shows as done and the run keeps it: off means "this run does
           not do it", not "throw it away"
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    on = make_config(tmp_path, correction_enabled=True)
    off = make_config(tmp_path, correction_enabled=False)
    repo = StateRepository(tmp_path / "state")
    artifacts = PreviewImageStore(tmp_path / "art")
    recon = CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=on
    )
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected", source_text="hello")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(off, [source])[0]

    assert row.correction is StageState.DONE

    after = CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=off
    ).prepare(source)

    assert after.state.correction_done is True
    assert after.state.correction_text == "hello corrected"


@pytest.mark.unit
def test_the_queue_and_the_check_view_agree_after_reconciliation(tmp_path: Path) -> None:
    """
    Given  a document read and proofread with one model, and a store reconciled with another
    When   the queue row and the Check view are read for it
    Then   both say the same thing: the reading is done and has text, the proofreading is gone
           (the reconciliation dropped it, so neither has a result to show)
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    made = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:9b")
    now = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    repo = StateRepository(tmp_path / "state")
    artifacts = PreviewImageStore(tmp_path / "art")
    recon = CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=made
    )
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected", source_text="hello")
    CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=now
    ).prepare(source)

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(now, [source])[0]
    view = svc.documents.load_view(now, source)

    assert view is not None
    assert row.has_text is True
    assert row.has_text == bool(view.raw_text.strip())
    assert row.ocr is StageState.DONE and view.ocr_done is True
    assert row.correction is StageState.NONE and view.correction_done is False
    assert view.correction_text == ""
    assert row.evaluation is StageState.NONE and view.evaluation_done is False


@pytest.mark.unit
def test_the_queue_and_the_check_view_agree_about_a_file_without_a_result(tmp_path: Path) -> None:
    """
    Given  a file the store knows nothing about
    When   the queue row and the Check view are read for it
    Then   both say it has no text and no stage is done
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path)
    repo = StateRepository(tmp_path / "state")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(cfg, [source])[0]

    assert svc.documents.load_view(cfg, source) is None
    assert row.has_text is False
    assert row.ocr is StageState.NONE
    assert row.correction is StageState.NONE
    assert row.evaluation is StageState.NONE


@pytest.mark.unit
def test_views_without_a_stored_result_cost_no_privacy_fingerprint(tmp_path: Path) -> None:
    """
    Given  a document that has recognised text but no proofreading or review
    When   its view is loaded
    Then   the privacy fingerprint is never computed, because nothing is there to compare
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
    )
    ctx = recon.prepare(source)
    recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    with patch("teachers_teammate.application.documents.privacy_key", side_effect=AssertionError):
        assert svc.documents.load_view(cfg, source) is not None
        assert svc.documents.queue_rows(cfg, [source])


@pytest.mark.unit
def test_check_document_answers_for_a_stored_document_and_none_without_one(
    tmp_path: Path,
) -> None:
    """
    Given  a service over a state store holding one recognized document
    When   check_document() is asked for that file and for a file without state
    Then   the first gives the Check tab's data and the second gives None
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
    )
    ctx = recon.prepare(source)
    recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    doc = svc.documents.check_document(cfg, source)

    assert doc is not None
    assert doc.source_id == str(source.resolve())
    assert doc.raw_text == "hello"
    assert doc.has_proofreading is False
    assert svc.documents.check_document(cfg, tmp_path / "other.txt") is None


@pytest.mark.parametrize(("scan_marks", "served"), [(True, 1), (False, 0)])
def test_check_document_serves_the_places_only_with_scan_marks_on(
    tmp_path: Path, scan_marks: bool, served: int
) -> None:
    """
    Given  a stored reading with the place of its one line
    When   check_document() is asked with the scan's marks switched on or off
    Then   the places are served when they are on, and none when they are off
    """
    from teachers_teammate.domain.page_layout import ScanBox  # noqa: PLC0415

    source = tmp_path / "note.png"
    source.write_text("not an image")
    cfg = make_config(tmp_path, scan_marks=scan_marks)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
    )
    place = ScanBox(page=0, start=0, end=5, confidence=0.9, boxes=())
    recon.record_ocr(
        source,
        recon.prepare(source),
        raw_text="hello",
        preview_img="",
        source_image="",
        layout=(place,),
    )
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    doc = svc.documents.check_document(cfg, source)

    assert doc is not None
    assert len(doc.scan_boxes) == served


# ── _invalidate_from_stage ValueError ────────────────────────────────────


@pytest.mark.unit
def test_expected_cache_keys_never_probe_this_computer(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a service that has not probed this computer, and a local Ollama with "auto" hardware
    When   the GUI asks for the context windows and the reconciled views
    Then   no graphics tool is run (that would freeze the window), and the base windows are used
    """
    from teachers_teammate.domain.context_budget import BASE_NUM_CTX  # noqa: PLC0415
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    probes: list[str] = []
    monkeypatch.setattr(gpu_detector, "detect_gpus", lambda: probes.append("gpu") or [])
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr", hardware_profile="auto")

    windows = windows_for(cfg, recommendations=svc.recommendations)
    svc.documents.queue_rows(cfg, [])

    assert probes == []
    assert (windows.proofreading, windows.evaluation) == (BASE_NUM_CTX, BASE_NUM_CTX)
    assert (
        svc.recommendations.cached_hardware_profile(HardwareProfileSetting.AUTO, cfg.ollama_url)
        is None
    )


@pytest.mark.unit
def test_describe_sent_texts_shows_the_cached_text_that_a_run_sends(tmp_path: Path) -> None:
    """
    Given  a run that anonymized a file's text through the shared cache
    When   describe_sent_texts() is asked what that file's proofreading receives
    Then   it reports the same anonymized text, with one spaCy pass in total
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    svc, source = _svc_with_document(tmp_path, raw_text="Anna wrote")
    config = dataclasses.replace(
        make_config(tmp_path),
        correction_enabled=True,
        anonymization_enabled=True,
    )
    anon_config = AnonymizerConfig()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.anonymization.build_cached_anonymizer("English", anon_config).anonymize("Anna wrote")
        view = svc.documents.load_view(config, source)
        assert view is not None
        report = svc.documents.describe_sent_texts(config, view)

    sent = [section for section in report.sections if section.stage.stage == "correction"]
    assert sent, "a correction step that hides names is part of the report"
    assert sent[0].text == "[PERSON_1] wrote"
    assert sent[0].placeholders == ("[PERSON_1]",)
    anonymizer.anonymize.assert_called_once_with("Anna wrote")


@pytest.mark.unit
def test_describe_sent_texts_without_anonymization_sends_names_as_written(
    tmp_path: Path,
) -> None:
    """
    Given  a run with personal-info removal switched off
    When   describe_sent_texts() is asked what is sent
    Then   the text is the extracted text, and no spaCy model is loaded
    """
    svc, source = _svc_with_document(tmp_path, raw_text="Anna wrote")
    config = dataclasses.replace(
        make_config(tmp_path), correction_enabled=True, anonymization_enabled=False
    )

    with patch("teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer") as ctor:
        view = svc.documents.load_view(config, source)
        assert view is not None
        report = svc.documents.describe_sent_texts(config, view)

    ctor.assert_not_called()
    sent = [section for section in report.sections if section.stage.stage == "correction"]
    assert sent and sent[0].text == "Anna wrote"
    assert sent[0].names_hidden is False


@pytest.mark.unit
def test_describe_sent_texts_carries_the_prompts_the_steps_send(tmp_path: Path) -> None:
    """
    Given  a proofreading step with the fixed prompts of the exchange
    When   describe_sent_texts() builds the report
    Then   the section carries every step the exchange sends, with its own prompt
    """
    svc, source = _svc_with_document(tmp_path, raw_text="Anna schrieb das.")
    config = dataclasses.replace(make_config(tmp_path, language="German"), correction_enabled=True)

    view = svc.documents.load_view(config, source)
    assert view is not None
    report = svc.documents.describe_sent_texts(config, view)

    sent = next(s for s in report.sections if s.stage.stage == "correction")
    assert sent.instructions == tuple(proofreading_prompts(config))


@pytest.mark.unit
def test_describe_sent_texts_carries_the_notes_for_the_ai(tmp_path: Path) -> None:
    """
    Given  a proofreading step with the teacher's notes
    When   describe_sent_texts() builds the report
    Then   the languages, the level and the notes travel with the section, apart from the prompts
    """
    svc, source = _svc_with_document(tmp_path, raw_text="Anna wrote this.")
    config = dataclasses.replace(
        make_config(tmp_path),
        correction_enabled=True,
        correction_level="B1",
        language="German",
        correction_notes="Year 9, second foreign language.",
    )

    view = svc.documents.load_view(config, source)
    assert view is not None
    report = svc.documents.describe_sent_texts(config, view)

    sent = next(s for s in report.sections if s.stage.stage == "correction")
    assert sent.notes.startswith("Text language: German\n")
    assert "CEFR level of the student: B1" in sent.notes
    assert sent.notes.endswith("Year 9, second foreign language.")
    assert all("Year 9" not in prompt for _step, prompt in sent.instructions)


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_lists_a_stale_proofreading_the_teacher_worked_on(
    tmp_path: Path,
) -> None:
    """
    Given  a proofread document whose corrections the teacher changed, and one they only
           looked at
    When   the documents of a run with other proofreading settings are looked at
    Then   the changed one is stale-but-kept and unanswered, and the other is not listed
    """
    _made, repo, [worked, looked] = _proofread(tmp_path, "worked.txt", "looked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    _change_work(
        looked,
        lambda work: with_looked_at(
            work, ["edit:1"], current_texts(_machine_of(repo, looked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(other, [worked, looked], run_proofreads=True)

    assert result.kept_stale == (worked,)
    assert result.unanswered == (worked,)
    assert result.unbound == ()
    assert result.transcribed == ()
    assert result.empty is False


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_asks_nothing_about_a_proofreading_a_run_does_not_replace(
    tmp_path: Path,
) -> None:
    """
    Given  a proofread document whose corrections the teacher changed
    When   the documents of a run that does not proofread are looked at, with other settings
    Then   nothing is stale-but-kept or unanswered: the run replaces no proofreading
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=False, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(other, [worked], run_proofreads=False)

    assert result.kept_stale == ()
    assert result.unanswered == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_does_not_ask_again_after_keep_my_changes(tmp_path: Path) -> None:
    """
    Given  a document whose corrections the teacher changed and then kept over these settings
    When   the documents of a run with the same settings are looked at
    Then   it is still stale-but-kept, but no longer unanswered
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
    expected = correction_key(
        other, windows_for(other, recommendations=svc.recommendations), make_key_facts()
    )

    _change_work(
        worked,
        lambda work: kept_over(work, expected, current_texts(_machine_of(repo, worked), work)),
    )
    result = svc.documents.worked_on_documents(other, [worked], run_proofreads=True)

    assert result.kept_stale == (worked,)
    assert result.unanswered == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_names_corrections_that_belong_to_no_stored_answer(
    tmp_path: Path,
) -> None:
    """
    Given  a document whose corrections the teacher changed, and then the results cache is
           emptied
    When   the documents of a run that proofreads are looked at
    Then   the corrections are named as belonging to no stored answer — and not at all when
           the run does not proofread
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

    proofreading = svc.documents.worked_on_documents(made, [worked], run_proofreads=True)
    reading_only = svc.documents.worked_on_documents(made, [worked], run_proofreads=False)

    assert proofreading.unbound == (worked,)
    assert proofreading.kept_stale == ()
    assert reading_only.unbound == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_names_a_transcription_the_teacher_fixed(tmp_path: Path) -> None:
    """
    Given  a document whose transcription the teacher fixed
    When   the documents of a run are looked at
    Then   it is named as one whose reading a re-read would replace
    """
    made, repo, [source] = _proofread(tmp_path, "essay.txt")
    machine = _machine_of(repo, source)
    _change_work(
        source, lambda work: with_transcription(work, "the essay", machine.raw_text_format, machine)
    )
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(made, [source], run_proofreads=True)

    assert result.transcribed == (source,)


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_is_empty_without_work(tmp_path: Path) -> None:
    """
    Given  a proofread document the teacher did nothing to
    When   the documents of a run are looked at
    Then   nothing is listed and the result says so
    """
    made, repo, [source] = _proofread(tmp_path, "essay.txt")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(made, [source], run_proofreads=True)

    assert result.empty is True


@pytest.mark.unit
def test_a_reading_made_with_other_settings_is_not_a_recognised_file(tmp_path: Path) -> None:
    """
    Given  a scan read with one image preparation and a transcription the teacher did not touch
    When   the files whose stored text a run would use are asked for, with the same settings
           and with another image preparation
    Then   the scan counts with the same settings and does not with the other, because the run
           would read it again
    """
    source = tmp_path / "scan.png"
    source.write_bytes(b"page")
    made = make_config(tmp_path, preprocess_method="none")
    now = make_config(tmp_path, preprocess_method="auto")

    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=made,
    )
    ctx = recon.prepare(source)
    recon.record_ocr(source, ctx, raw_text="page text", preview_img="", source_image="")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    assert svc.documents.recognised_files(made, [source]) == {source}
    assert svc.documents.recognised_files(now, [source]) == set()
