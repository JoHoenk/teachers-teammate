"""Unit tests for teachers_teammate.infrastructure.workflow.cache_service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from teachers_teammate.domain.stages import Stage
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.config import Config, OcrConfig
from teachers_teammate.domain.context_budget import RunWindows
from teachers_teammate.infrastructure.preview_image_store import PreviewImageStore
from teachers_teammate.domain.document_state import DocumentState
from teachers_teammate.infrastructure.state_repository import (
    OcrResultRecord,
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.storage_root import document_stem
from teachers_teammate.domain.freshness import (
    ExpectedHashes,
    first_stale_stage_for_hashes,
    stage_hashes,
)
from teachers_teammate.domain.teacher_work import (
    CorrectionsWork,
    TeacherWork,
    TranscriptionWork,
    proofreading_id,
    text_identity,
)
from teachers_teammate.cache_keys import (
    correction_key,
    review_key,
    sent_record,
    with_task_text,
)
from teachers_teammate.infrastructure.workflow.cache_service import (
    CacheContext,
    CacheReconciliationService,
)
from tests.conftest import make_config, make_key_facts

_PDF = Path("worksheet.pdf")


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_state(source_path: str = "doc.png") -> DocumentState:
    return DocumentState(schema_version=2, source_path=source_path, source_hash="abc123")


def _make_ctx(state: DocumentState | None = None) -> CacheContext:
    return CacheContext(
        state=state or _make_state(),
        source_hash="abc123",
        ocr_prompt="Transcribe the text.",
        ocr_config_hash="ocr_h",
        correction_config_hash="corr_h",
        eval_config_hash="eval_h",
        preview_config_hash="prev_h",
    )


def _correction_key(config: Config, windows: RunWindows | None = None) -> str:
    """The proofreading key of *config*, made with the facts of this computer."""
    return correction_key(config, windows, make_key_facts())


def _make_svc(
    tmp_path: Path, **config_overrides: object
) -> tuple[CacheReconciliationService, MagicMock, MagicMock]:
    repo = MagicMock()
    artifacts = MagicMock()
    cfg = make_config(tmp_path, **config_overrides)
    svc = CacheReconciliationService(
        facts=make_key_facts(), state_repo=repo, artifact_store=artifacts, config=cfg
    )
    return svc, repo, artifacts


# ── prepare() ─────────────────────────────────────────────────────────────


def test_prepare_calls_load_or_create_and_reconcile(tmp_path: Path) -> None:
    """
    Given  a source file and a new cache service
    When   prepare() is called
    Then   repo.load_or_create and repo.reconcile are both called,
           and the returned CacheContext contains populated hash fields
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"fake image data")

    svc, repo, _ = _make_svc(tmp_path)
    reconciled_state = _make_state()
    repo.load_or_create.return_value = _make_state()
    repo.reconcile.return_value = reconciled_state

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="filehash",
    ):
        ctx = svc.prepare(src)

    repo.load_or_create.assert_called_once_with(src, "filehash")
    repo.reconcile.assert_called_once()
    assert ctx.source_hash == "filehash"
    assert ctx.state is reconciled_state
    assert ctx.ocr_prompt != ""
    assert ctx.ocr_config_hash != ""
    assert ctx.preview_config_hash != ""


def test_prepare_keys_with_the_facts_it_was_given(tmp_path: Path) -> None:
    """
    Given  a cache service handed the facts of a made-up computer with a page model downloaded
    When   prepare() computes the keys of an automatically prepared page
    Then   the OCR and preview keys are the ones those facts make, not the ones this computer makes
    """
    from teachers_teammate.cache_keys import ocr_key, preview_key  # noqa: PLC0415

    class _Facts:
        def page_steps(self) -> tuple[str, ...]:
            return ("turn", "flatten")

        def system_language(self) -> str:
            return "English"

        def exchange_identity(self, profile: object, *, corrections_only: bool) -> str:
            return "exchange"

    src = tmp_path / "doc.png"
    src.write_bytes(b"fake image data")
    cfg = make_config(tmp_path, preprocess_method="auto")
    repo = MagicMock()
    repo.load_or_create.return_value = _make_state()
    repo.reconcile.return_value = _make_state()
    svc = CacheReconciliationService(
        state_repo=repo, artifact_store=MagicMock(), config=cfg, facts=_Facts()
    )

    ctx = svc.prepare(src)

    assert ctx.ocr_config_hash == ocr_key(cfg, src, _Facts())
    assert ctx.preview_config_hash == preview_key(cfg, src, ctx.source_hash, _Facts())


def test_prepare_writes_the_state_file_once(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a stored document whose reading is stale and whose preview image is invalid
    When   prepare() reconciles it
    Then   the state file is written once, with both changes in it
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    page = tmp_path / "doc_preprocessed.png"
    page.write_bytes(b"png")
    repo = StateRepository(tmp_path / "state")
    repo.save(
        src,
        replace(
            repo.create(src, compute_file_hash(src)),
            ocr_done=True,
            raw_text="raw",
            ocr_config_hash="stale-ocr",
            preview_config_hash="stale-preview",
            page_images=[str(page)],
            source_image=str(page),
            preprocess_steps=["grayscale"],
        ),
    )
    svc = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=make_config(tmp_path),
    )
    writes: list[Path] = []
    save = repo.save

    def _counting_save(input_file: Path, state: DocumentState) -> None:
        writes.append(input_file)
        save(input_file, state)

    monkeypatch.setattr(repo, "save", _counting_save)

    ctx = svc.prepare(src)

    assert len(writes) == 1
    assert ctx.state.ocr_done is False
    assert ctx.state.page_images == []


def _prepared_hashes(tmp_path: Path, **config_overrides: object) -> CacheContext:
    """Return the context prepare() builds for a config with *config_overrides*."""
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    svc, repo, _ = _make_svc(tmp_path, **config_overrides)
    repo.load_or_create.return_value = _make_state()
    repo.reconcile.return_value = _make_state()
    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="h",
    ):
        return svc.prepare(src)


def test_the_correction_hash_describes_the_settings_whatever_the_switch(tmp_path: Path) -> None:
    """
    Given  two configs that differ only in correction_enabled
    When   prepare() is called for each
    Then   both carry the same, non-empty correction hash: a switched-off stage is "this run
           does not do it", not "throw the stored proofreading away"
    """
    on = _prepared_hashes(tmp_path, correction_enabled=True)
    off = _prepared_hashes(tmp_path, correction_enabled=False)

    assert on.correction_config_hash != ""
    assert off.correction_config_hash == on.correction_config_hash


def test_the_eval_hash_describes_the_settings_whatever_the_switch(tmp_path: Path) -> None:
    """
    Given  two configs that differ only in evaluation_enabled
    When   prepare() is called for each
    Then   both carry the same, non-empty evaluation hash
    """
    on = _prepared_hashes(tmp_path, evaluation_enabled=True)
    off = _prepared_hashes(tmp_path, evaluation_enabled=False)

    assert on.eval_config_hash != ""
    assert off.eval_config_hash == on.eval_config_hash


# ── Hash determinism ───────────────────────────────────────────────────────


def test_ocr_config_hash_is_deterministic(tmp_path: Path) -> None:
    """
    Given  the same config object
    When   _ocr_config_hash is called twice
    Then   both calls return the same string
    """
    svc, _, _ = _make_svc(tmp_path)
    h1 = svc._ocr_config_hash(_PDF)
    h2 = svc._ocr_config_hash(_PDF)
    assert h1 == h2
    assert h1 != ""


def test_ocr_config_hash_changes_with_pdf_text_layer(tmp_path: Path) -> None:
    """
    Given  two configs that differ only in the PDF text-layer mode
    When   _ocr_config_hash is called with the same prompt
    Then   the hashes differ so switching mode re-runs cached PDFs
    """
    svc_auto, _, _ = _make_svc(tmp_path, ocr=OcrConfig(engine="ppocr", pdf_text_layer="auto"))
    svc_never, _, _ = _make_svc(tmp_path, ocr=OcrConfig(engine="ppocr", pdf_text_layer="never"))
    assert svc_auto._ocr_config_hash(_PDF) != svc_never._ocr_config_hash(_PDF)


def test_ocr_config_hash_changes_with_engine(tmp_path: Path) -> None:
    """
    Given  two services with different ocr_engine values
    When   _ocr_config_hash is called with the same prompt
    Then   the two hashes are different
    """
    svc_a, _, _ = _make_svc(tmp_path, ocr_engine="ppocr")
    svc_b, _, _ = _make_svc(tmp_path, ocr_engine="ollama")
    assert svc_a._ocr_config_hash(_PDF) != svc_b._ocr_config_hash(_PDF)


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
def test_correction_config_hash_independent_of_ocr_engine(tmp_path: Path) -> None:
    """
    Given  two services differing only in ocr_engine (all correction params identical)
    When   _correction_config_hash() is called on each
    Then   the two hashes are equal (OCR engine does not affect correction hash)
    """
    svc_a, _, _ = _make_svc(
        tmp_path, ocr_engine="ppocr", correction_enabled=True, correction_provider="openai"
    )
    svc_b, _, _ = _make_svc(
        tmp_path, ocr_engine="ollama", correction_enabled=True, correction_provider="openai"
    )
    assert svc_a._correction_config_hash() == svc_b._correction_config_hash()


def test_correction_config_hash_ignores_anonymization(tmp_path: Path) -> None:
    """
    Given  two services differing only in whether personal info is removed
    When   _correction_config_hash() is called on each
    Then   the hashes are equal, so a privacy setting change does not send the text again
    """
    plain, _, _ = _make_svc(tmp_path, correction_enabled=True, anonymization_enabled=False)
    hidden, _, _ = _make_svc(tmp_path, correction_enabled=True, anonymization_enabled=True)

    assert plain._correction_config_hash() == hidden._correction_config_hash()


@pytest.mark.parametrize(
    "override",
    [
        {"correction_level": "C1"},
        {"explanation_language": "German"},
        {"correction_output": "text"},
        {"correction_notes": "Year 9, second foreign language."},
    ],
)
def test_correction_config_hash_changes_with_the_review_settings(
    tmp_path: Path, override: dict[str, str]
) -> None:
    """
    Given  two services differing only in the level, explanation language, output mode or notes
    When   _correction_config_hash() is called on each
    Then   the hashes differ, so a changed review setting re-runs the proofreading
    """
    base, _, _ = _make_svc(tmp_path, correction_enabled=True)
    changed, _, _ = _make_svc(tmp_path, correction_enabled=True, **override)

    assert base._correction_config_hash() != changed._correction_config_hash()


def test_correction_config_hash_ignores_surrounding_whitespace_of_the_notes(
    tmp_path: Path,
) -> None:
    """
    Given  two services whose notes differ only in surrounding whitespace
    When   _correction_config_hash() is called on each
    Then   the hashes are equal, because the request carries the stripped notes
    """
    plain, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_notes="Year 9")
    padded, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_notes="  Year 9\n")

    assert plain._correction_config_hash() == padded._correction_config_hash()


def test_correction_config_hash_changes_with_the_prompt_version(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a service and a bumped version of the exchange's fixed prompts
    When   _correction_config_hash() is called before and after the bump
    Then   the hashes differ, so results made with the old prompts are not served as fresh
    """
    from teachers_teammate.infrastructure.proofreading import corrector  # noqa: PLC0415

    svc, _, _ = _make_svc(tmp_path, correction_enabled=True)
    before = svc._correction_config_hash()

    monkeypatch.setattr(corrector, "PROMPT_VERSION", int(corrector.PROMPT_VERSION) + 1)

    assert svc._correction_config_hash() != before


def test_correction_config_hash_follows_the_text_of_a_step_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a service and an edited system prompt of step ②
    When   _correction_config_hash() is called before and after the edit
    Then   the hashes differ, so an edited prompt invalidates the stored proofreadings
    """
    from teachers_teammate.infrastructure.proofreading.exchanges import text_first as t  # noqa: PLC0415

    svc, _, _ = _make_svc(tmp_path, correction_enabled=True)
    before = svc._correction_config_hash()

    monkeypatch.setattr(t, "EXPLAIN_SYSTEM", t.EXPLAIN_SYSTEM + "\nBe brief.")

    assert svc._correction_config_hash() != before


def test_correction_config_hash_changes_with_a_step_temperature(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a service and a model whose step ② runs at another temperature
    When   _correction_config_hash() is called before and after the change
    Then   the hashes differ, because the request profile carries every step's temperature
    """
    from teachers_teammate import config as config_module  # noqa: PLC0415

    svc, _, _ = _make_svc(tmp_path, correction_enabled=True)
    before = svc._correction_config_hash()

    original = config_module.request_profile

    def warmer(model: str, provider: str):
        profile = original(model, provider)
        return replace(profile, explain=replace(profile.explain, temperature=0.9))

    monkeypatch.setattr(config_module, "request_profile", warmer)

    assert svc._correction_config_hash() != before


def test_eval_config_hash_ignores_anonymization(tmp_path: Path) -> None:
    """
    Given  two services differing only in whether personal info is removed
    When   _eval_config_hash() is called on each
    Then   the hashes are equal, so a privacy setting change does not send the text again
    """
    plain, _, _ = _make_svc(tmp_path, evaluation_enabled=True, anonymization_enabled=False)
    hidden, _, _ = _make_svc(tmp_path, evaluation_enabled=True, anonymization_enabled=True)

    assert plain._eval_config_hash() == hidden._eval_config_hash()


def test_ocr_config_hash_independent_of_correction_provider(tmp_path: Path) -> None:
    """
    Given  two services differing only in correction_provider (all OCR params identical)
    When   _ocr_config_hash() is called on each
    Then   the two hashes are equal (correction provider does not affect OCR hash)
    """
    svc_a, _, _ = _make_svc(tmp_path, ocr_engine="ppocr", correction_provider="openai")
    svc_b, _, _ = _make_svc(tmp_path, ocr_engine="ppocr", correction_provider="anthropic")
    assert svc_a._ocr_config_hash(_PDF) == svc_b._ocr_config_hash(_PDF)


@pytest.mark.use_case("Multi_Provider_Stage_Configuration")
def test_three_stage_hashes_are_all_different_when_providers_differ(tmp_path: Path) -> None:
    """
    Given  ocr_engine='ppocr', correction_provider='openai', evaluate_provider='anthropic'
    When   each stage hash method is called
    Then   all three hashes are non-empty and mutually distinct
    """
    svc, _, _ = _make_svc(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=True,
        correction_provider="openai",
        evaluation_enabled=True,
        evaluate_provider="anthropic",
    )
    ocr_h = svc._ocr_config_hash(_PDF)
    corr_h = svc._correction_config_hash()
    eval_h = svc._eval_config_hash()
    assert ocr_h != ""
    assert corr_h != ""
    assert eval_h != ""
    assert ocr_h != corr_h
    assert corr_h != eval_h
    assert ocr_h != eval_h


def test_ocr_config_hash_changes_with_temperature(tmp_path: Path) -> None:
    """
    Given  two services differing only in ocr_temperature
    When   _ocr_config_hash() is called on each
    Then   the two hashes differ (temperature affects OCR cache invalidation)
    """
    svc_a, _, _ = _make_svc(tmp_path, ocr_engine="ollama", ocr_temperature=0.0)
    svc_b, _, _ = _make_svc(tmp_path, ocr_engine="ollama", ocr_temperature=0.7)
    assert svc_a._ocr_config_hash(_PDF) != svc_b._ocr_config_hash(_PDF)


@pytest.mark.use_case("Config_Aware_Cache_Reuse")
@pytest.mark.parametrize(
    "mutate",
    [
        lambda o: replace(o, pdf_render_dpi=600),
        lambda o: replace(o, preprocess_method="none"),
        lambda o: replace(o, engine="ollama"),
    ],
    ids=["pdf_render_dpi", "method", "engine_output"],
)
def test_ocr_config_hash_changes_with_each_preprocessing_field(
    tmp_path: Path, mutate: Callable[[OcrConfig], OcrConfig]
) -> None:
    """
    Given  two configs differing only in one preprocessing field (PDF DPI, the method, or the
           engine, which decides whether the automatic preparation hands over gray or colour)
    When   _ocr_config_hash() is called on each
    Then   the two hashes differ, so changing the field invalidates the OCR cache

    Without this, the cache (which has no expiry) returns stale OCR text after a change.
    """
    base = OcrConfig(engine="ppocr", preprocess_method=PreprocessMethod.AUTO)
    svc_a, _, _ = _make_svc(tmp_path, ocr=base)
    svc_b, _, _ = _make_svc(tmp_path, ocr=mutate(base))
    assert svc_a._ocr_config_hash(_PDF) != svc_b._ocr_config_hash(_PDF)


def test_correction_config_hash_changes_with_the_step_one_temperature(tmp_path: Path) -> None:
    """
    Given  a model whose measured step-① temperature changes between two app versions
    When   _correction_config_hash() is called before and after
    Then   the two hashes differ (the temperature is part of what is sent)
    """
    from teachers_teammate.domain.request_profile import STEP_ONE_TEMPERATURES  # noqa: PLC0415

    svc, _, _ = _make_svc(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gemma4:e4b",
    )
    before = svc._correction_config_hash()
    with patch.dict(STEP_ONE_TEMPERATURES, {"gemma4:e4b": 0.5}):
        assert svc._correction_config_hash() != before


def test_eval_config_hash_changes_with_the_recommended_temperature(tmp_path: Path) -> None:
    """
    Given  a review model whose recommended temperature changes between two app versions
    When   _eval_config_hash() is called before and after
    Then   the two hashes differ (the temperature affects evaluation cache invalidation)
    """
    from teachers_teammate.domain.request_profile import MAKER_TEMPERATURES  # noqa: PLC0415

    svc, _, _ = _make_svc(
        tmp_path,
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="gemma4:e4b",
    )
    before = svc._eval_config_hash()
    with patch.dict(MAKER_TEMPERATURES, {"gemma4:e4b": 0.5}):
        assert svc._eval_config_hash() != before


def test_the_hashes_change_with_the_reasoning_level_a_model_is_asked_for(tmp_path: Path) -> None:
    """
    Given  gpt-oss, which the app asks for a reasoning level, and a catalogue that stops doing so
    When   the correction and the evaluation hash are computed before and after
    Then   both differ: pages proofread at another reasoning level are not served again
    """
    from teachers_teammate.domain.request_profile import THINK_LEVELS  # noqa: PLC0415

    svc, _, _ = _make_svc(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gpt-oss:20b",
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="gpt-oss:20b",
    )
    correction, review = svc._correction_config_hash(), svc._eval_config_hash()
    with patch.dict(THINK_LEVELS, {"gpt-oss": "medium"}):
        assert svc._correction_config_hash() != correction
        assert svc._eval_config_hash() != review


def test_correction_config_hash_changes_with_model(tmp_path: Path) -> None:
    """
    Given  two services differing only in correction_model (regression guard)
    When   _correction_config_hash() is called on each
    Then   the two hashes differ — confirms a correction-model change invalidates the cache
    """
    svc_a, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_model="gpt-oss:20b")
    svc_b, _, _ = _make_svc(tmp_path, correction_enabled=True, correction_model="llama3.1:8b")
    assert svc_a._correction_config_hash() != svc_b._correction_config_hash()


# ── record_ocr() ──────────────────────────────────────────────────────────


def test_record_ocr_delegates_to_repo_and_preserves_hashes(tmp_path: Path) -> None:
    """
    Given  a CacheContext and OCR text
    When   record_ocr() is called
    Then   repo.record_ocr_result is called with the correct kwargs and the returned
           CacheContext preserves all hash fields from the original context
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    new_state = _make_state()
    repo.record_ocr_result.return_value = new_state

    ctx = _make_ctx()
    result = svc.record_ocr(
        file, ctx, raw_text="text", preview_img="prev.png", source_image="src.png"
    )

    repo.record_ocr_result.assert_called_once_with(
        file,
        ctx.state,
        OcrResultRecord(
            source_hash=ctx.source_hash,
            raw_text="text",
            preview_img="prev.png",
            source_image="src.png",
            ocr_config_hash=ctx.ocr_config_hash,
            preview_config_hash=ctx.preview_config_hash,
        ),
    )
    assert result.state is new_state
    assert result.ocr_config_hash == ctx.ocr_config_hash
    assert result.correction_config_hash == ctx.correction_config_hash
    assert result.eval_config_hash == ctx.eval_config_hash
    assert result.preview_config_hash == ctx.preview_config_hash


# ── record_correction() ───────────────────────────────────────────────────


def test_record_correction_delegates_to_repo(tmp_path: Path) -> None:
    """
    Given  a CacheContext and correction text
    When   record_correction() is called
    Then   repo.record_correction_result is called, and correction_config_hash is forwarded
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    new_state = _make_state()
    repo.record_correction_result.return_value = new_state

    ctx = _make_ctx()
    result = svc.record_correction(file, ctx, correction_text="corrected")

    repo.record_correction_result.assert_called_once_with(
        file,
        ctx.state,
        correction_text="corrected",
        correction_config_hash=ctx.correction_config_hash,
        correction_review=None,
        correction_warning="",
        sent_text="",
        sent_map=None,
        sent=sent_record(svc._config, Stage.CORRECTION, facts=make_key_facts()),
        source_hash=text_identity(""),
    )
    assert result.state is new_state
    assert result.correction_config_hash == ctx.correction_config_hash


def test_record_ocr_forwards_text_format(tmp_path: Path) -> None:
    """
    Given  OCR output that was parsed from layout HTML into Markdown
    When   record_ocr() is called with the format
    Then   it lands in the OcrResultRecord handed to the repository
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    repo.record_ocr_result.return_value = _make_state()

    svc.record_ocr(
        file,
        _make_ctx(),
        raw_text="**x**",
        preview_img="p.png",
        source_image="s.png",
        raw_text_format="markdown",
    )

    record = repo.record_ocr_result.call_args.args[2]
    assert record.raw_text_format == "markdown"


# ── record_evaluation() ───────────────────────────────────────────────────


def test_record_evaluation_delegates_to_repo(tmp_path: Path) -> None:
    """
    Given  a CacheContext and evaluation text
    When   record_evaluation() is called
    Then   repo.record_evaluation_result is called with correct kwargs
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    new_state = _make_state()
    repo.record_evaluation_result.return_value = new_state

    ctx = _make_ctx()
    result = svc.record_evaluation(file, ctx, evaluation_text="quality: good")

    repo.record_evaluation_result.assert_called_once_with(
        file,
        ctx.state,
        evaluation_text="quality: good",
        eval_config_hash=ctx.eval_config_hash,
        evaluation_warning="",
        sent=sent_record(svc._config, Stage.EVALUATION, facts=make_key_facts()),
    )
    assert result.state is new_state


# ── How a result was sent ─────────────────────────────────────────────────


def test_record_correction_forwards_the_text_that_was_sent(tmp_path: Path) -> None:
    """
    Given  the text a proofreading was made from and its placeholder map
    When   record_correction() is called with them
    Then   both reach the repository, so the window can show what really went out
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    repo.record_correction_result.return_value = _make_state()

    svc.record_correction(
        file,
        _make_ctx(),
        correction_text="corrected",
        sent_text="raw with <PERSON_1>",
        sent_map={"<PERSON_1>": "Anna"},
    )

    kwargs = repo.record_correction_result.call_args.kwargs
    assert kwargs["sent_text"] == "raw with <PERSON_1>"
    assert kwargs["sent_map"] == {"<PERSON_1>": "Anna"}


def test_record_evaluation_forwards_the_number_of_hidden_names(tmp_path: Path) -> None:
    """
    Given  a review of a text that was sent with three placeholders
    When   record_evaluation() is called with that count
    Then   the record stored with the review says three names were hidden
    """
    file = tmp_path / "doc.png"
    svc, repo, _ = _make_svc(tmp_path)
    repo.record_evaluation_result.return_value = _make_state()

    svc.record_evaluation(file, _make_ctx(), evaluation_text="good", names_hidden=3)

    sent = repo.record_evaluation_result.call_args.kwargs["sent"]
    assert sent.names_hidden == 3


# ── persist_preview_artifact() ────────────────────────────────────────────


def test_persist_preview_artifact_copies_and_records(tmp_path: Path) -> None:
    """
    Given  a source image path and a CacheContext
    When   persist_preview_artifact() is called
    Then   artifact_store.persist_preview_image is called with stem and source_path,
           repo.record_preview_artifact is called, and the return value contains the stable path string
    """
    file = tmp_path / "doc.png"
    source_path = tmp_path / "tmp_preview.png"
    stable_path = tmp_path / "artifacts" / "doc_preprocessed.png"

    svc, repo, artifacts = _make_svc(tmp_path)
    new_state = _make_state()
    artifacts.persist_preview_image.return_value = stable_path
    repo.record_preview_artifact.return_value = new_state

    ctx = _make_ctx()
    preview_img, new_ctx = svc.persist_preview_artifact(file, ctx, source_path)

    artifacts.persist_preview_image.assert_called_once_with(
        stem=document_stem(file), source_path=source_path
    )
    repo.record_preview_artifact.assert_called_once_with(
        file,
        ctx.state,
        preview_img=str(stable_path),
        source_image=str(stable_path),
        preview_config_hash=ctx.preview_config_hash,
        preprocess_steps=None,
        page_images=[],
        original_images=[],
    )
    assert preview_img == str(stable_path)
    assert new_ctx.state is new_state
    assert new_ctx.preview_config_hash == ctx.preview_config_hash


def test_persist_preview_artifact_stores_original_and_steps(tmp_path: Path) -> None:
    """
    Given  an original page image and the preprocessing steps that produced the preview
    When   persist_preview_artifact() is called with original_paths and preprocess_steps
    Then   the original is persisted via the artifact store and both its path and the
           steps are recorded on the state
    """
    file = tmp_path / "doc.png"
    source_path = tmp_path / "tmp_preview.png"
    original_path = tmp_path / "page1.png"
    stable_path = tmp_path / "artifacts" / "doc_preprocessed.png"
    stable_original = tmp_path / "artifacts" / "doc_original.png"

    svc, repo, artifacts = _make_svc(tmp_path)
    artifacts.persist_preview_image.return_value = stable_path
    artifacts.persist_original_images.return_value = [stable_original]
    repo.record_preview_artifact.return_value = _make_state()

    ctx = _make_ctx()
    svc.persist_preview_artifact(
        file,
        ctx,
        source_path,
        original_paths=[original_path],
        preprocess_steps=["grayscale", "deskew"],
    )

    artifacts.persist_original_images.assert_called_once_with(
        stem=document_stem(file), sources=[original_path]
    )
    repo.record_preview_artifact.assert_called_once_with(
        file,
        ctx.state,
        preview_img=str(stable_path),
        source_image=str(stable_path),
        preview_config_hash=ctx.preview_config_hash,
        preprocess_steps=["grayscale", "deskew"],
        page_images=[],
        original_images=[str(stable_original)],
    )


def test_persist_preview_artifact_original_copy_failure_is_not_fatal(tmp_path: Path) -> None:
    """
    Given  an artifact store whose persist_original_images raises OSError
    When   persist_preview_artifact() is called with original_paths
    Then   the preview is still recorded, with no original and the steps preserved
    """
    file = tmp_path / "doc.png"
    stable_path = tmp_path / "artifacts" / "doc_preprocessed.png"
    svc, repo, artifacts = _make_svc(tmp_path)
    artifacts.persist_preview_image.return_value = stable_path
    artifacts.persist_original_images.side_effect = OSError("disk full")
    repo.record_preview_artifact.return_value = _make_state()

    ctx = _make_ctx()
    preview_img, _ = svc.persist_preview_artifact(
        file,
        ctx,
        tmp_path / "tmp_preview.png",
        original_paths=[tmp_path / "page1.png"],
        preprocess_steps=["grayscale"],
    )

    assert preview_img == str(stable_path)
    repo.record_preview_artifact.assert_called_once_with(
        file,
        ctx.state,
        preview_img=str(stable_path),
        source_image=str(stable_path),
        preview_config_hash=ctx.preview_config_hash,
        preprocess_steps=["grayscale"],
        page_images=[],
        original_images=[],
    )


def test_persist_preview_artifact_stores_every_page(tmp_path: Path) -> None:
    """
    Given  three prepared pages
    When   persist_preview_artifact() is called with them
    Then   the store keeps all three, the first is the preview image, and all are recorded
    """
    file = tmp_path / "doc.png"
    pages = [tmp_path / f"page{number}.png" for number in (1, 2, 3)]
    stored = [
        tmp_path / "artifacts" / name
        for name in ("doc_preprocessed.png", "doc_p2.png", "doc_p3.png")
    ]

    svc, repo, artifacts = _make_svc(tmp_path)
    artifacts.persist_page_images.return_value = stored
    repo.record_preview_artifact.return_value = _make_state()

    ctx = _make_ctx()
    preview_img, _ = svc.persist_preview_artifact(file, ctx, pages[0], pages=pages)

    artifacts.persist_page_images.assert_called_once_with(stem=document_stem(file), sources=pages)
    artifacts.persist_preview_image.assert_not_called()
    assert preview_img == str(stored[0])
    repo.record_preview_artifact.assert_called_once_with(
        file,
        ctx.state,
        preview_img=str(stored[0]),
        source_image=str(stored[0]),
        preview_config_hash=ctx.preview_config_hash,
        preprocess_steps=None,
        page_images=[str(page) for page in stored],
        original_images=[],
    )


def test_persist_preview_artifact_prunes_stale_originals_when_none_recorded(
    tmp_path: Path,
) -> None:
    """
    Given  a re-run that produces no original page
    When   persist_preview_artifact() is called without original_paths
    Then   leftover <stem>_original.* files from earlier runs are pruned
    """
    file = tmp_path / "doc.png"
    svc, repo, artifacts = _make_svc(tmp_path)
    artifacts.persist_preview_image.return_value = tmp_path / "artifacts" / "doc_preprocessed.png"
    repo.record_preview_artifact.return_value = _make_state()

    svc.persist_preview_artifact(file, _make_ctx(), tmp_path / "tmp_preview.png")

    artifacts.prune_original_images.assert_called_once_with(document_stem(file))


def test_persist_preview_artifact_preserves_all_hash_fields(tmp_path: Path) -> None:
    """
    Given  a CacheContext with specific hash values
    When   persist_preview_artifact() is called
    Then   all hash fields are forwarded unchanged to the new CacheContext
    """
    file = tmp_path / "doc.png"
    svc, repo, artifacts = _make_svc(tmp_path)
    artifacts.persist_preview_image.return_value = tmp_path / "stable.png"
    repo.record_preview_artifact.return_value = _make_state()

    ctx = _make_ctx()
    _, new_ctx = svc.persist_preview_artifact(file, ctx, tmp_path / "src.png")

    assert new_ctx.source_hash == ctx.source_hash
    assert new_ctx.ocr_prompt == ctx.ocr_prompt
    assert new_ctx.ocr_config_hash == ctx.ocr_config_hash
    assert new_ctx.correction_config_hash == ctx.correction_config_hash
    assert new_ctx.eval_config_hash == ctx.eval_config_hash
    assert new_ctx.preview_config_hash == ctx.preview_config_hash


@pytest.mark.parametrize("mode", ["always", "never"])
def test_pdf_text_layer_mode_changes_hash_only_for_pdfs(tmp_path: Path, mode: str) -> None:
    """
    Given  an OCR config switched away from the default "auto" PDF text-layer mode
    When   the OCR config hash is computed for a PDF and for a JPG
    Then   the PDF hash changes while the JPG hash stays valid (its OCR input is unchanged)
    """
    base = OcrConfig(engine="ppocr")
    svc_auto, _, _ = _make_svc(tmp_path, ocr=base)
    svc_mode, _, _ = _make_svc(tmp_path, ocr=replace(base, pdf_text_layer=mode))

    jpg, txt = Path("scan.jpg"), Path("notes.TXT")
    assert svc_auto._ocr_config_hash(_PDF) != svc_mode._ocr_config_hash(_PDF)
    assert svc_auto._ocr_config_hash(jpg) == svc_mode._ocr_config_hash(jpg)
    assert svc_auto._ocr_config_hash(txt) == svc_mode._ocr_config_hash(txt)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ([], [("ID", r"\d+")]),
        ([("ID", r"\d+")], [("ID", r"\d{4,}")]),
        ([("ID", r"\d+")], [("ID", r"\d+"), ("CODE", r"[A-Z]{3}")]),
    ],
)
def test_correction_config_hash_ignores_the_anonymizer_patterns(
    tmp_path: Path, first: list, second: list
) -> None:
    """
    Given  two services with anonymization on and different regex patterns
    When   _correction_config_hash() is called on each
    Then   the hashes are equal, so a changed pattern keeps the stored proofreading
    """
    left, _, _ = _make_svc(
        tmp_path, correction_enabled=True, anonymization_enabled=True, anonymizer_patterns=first
    )
    right, _, _ = _make_svc(
        tmp_path, correction_enabled=True, anonymization_enabled=True, anonymizer_patterns=second
    )

    assert left._correction_config_hash() == right._correction_config_hash()


def test_eval_config_hash_ignores_the_anonymizer_patterns(tmp_path: Path) -> None:
    """
    Given  two services with anonymization on and different regex patterns
    When   _eval_config_hash() is called on each
    Then   the hashes are equal, so a changed pattern keeps the stored content review
    """
    left, _, _ = _make_svc(
        tmp_path,
        correction_enabled=True,
        evaluation_enabled=True,
        anonymization_enabled=True,
        anonymizer_patterns=[],
    )
    right, _, _ = _make_svc(
        tmp_path,
        correction_enabled=True,
        evaluation_enabled=True,
        anonymization_enabled=True,
        anonymizer_patterns=[("ID", r"\d+")],
    )

    assert left._eval_config_hash() == right._eval_config_hash()


def test_the_fingerprint_changes_when_the_context_window_is_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  two Ollama configs that differ only in the proofreading window setting
    When   the correction fingerprint is computed
    Then   a pinned window gives another key, so the files are proofread again

    The proofreading cap is raised to 16384 for this test: the shipped one is the base window,
    so every setting gives the same window, and this test is about the key.
    """
    from teachers_teammate.domain import context_budget  # noqa: PLC0415

    monkeypatch.setattr(context_budget, "PROOFREADING_NUM_CTX_CAP", 16384)
    auto = make_config(tmp_path, correction_enabled=True, correction_provider="ollama")
    pinned = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        proofreading_context="16384",
    )

    assert _correction_key(auto) != _correction_key(pinned)


def test_settings_that_give_the_same_window_give_the_same_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  two Ollama configs whose pinned windows are both above the proofreading cap
    When   the correction fingerprint is computed
    Then   the keys are equal, because the run uses the same window for both

    The proofreading cap is raised to 16384 for this test: the shipped one is the base window,
    so every setting gives the same window, and this test is about the key.
    """
    from teachers_teammate.domain import context_budget  # noqa: PLC0415

    monkeypatch.setattr(context_budget, "PROOFREADING_NUM_CTX_CAP", 16384)
    first = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        proofreading_context="16384",
    )
    second = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        proofreading_context="32768",
    )

    assert _correction_key(first) == _correction_key(second)


def test_the_window_setting_does_not_change_the_fingerprint_of_an_online_provider(
    tmp_path: Path,
) -> None:
    """
    Given  two configs with an online proofreading provider that differ only in the window
    When   the correction fingerprint is computed
    Then   the keys are equal, because an online provider never receives the window
    """
    auto = make_config(tmp_path, correction_enabled=True, correction_provider="openai")
    pinned = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        proofreading_context="16384",
    )

    assert _correction_key(auto) == _correction_key(pinned)


def test_a_profile_change_changes_the_cache_key_of_that_model_only(tmp_path: Path) -> None:
    """
    Given  two catalog models, one of which gets a smaller explanation batch
    When   the correction fingerprint is computed for each model, before and after
    Then   only the changed model's key moves, because the profile is resolved per model
    """
    from teachers_teammate.domain import model_catalog  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG  # noqa: PLC0415

    changed = "gemma4:e4b"
    untouched = "qwen3:4b"
    catalog = tuple(
        replace(spec, request_profile=replace(spec.request_profile, explain_batch=5))
        if spec.tag == changed
        else spec
        for spec in OLLAMA_CATALOG
    )
    configs = {
        tag: make_config(
            tmp_path, correction_enabled=True, correction_provider="ollama", correction_model=tag
        )
        for tag in (changed, untouched)
    }
    before = {tag: _correction_key(cfg) for tag, cfg in configs.items()}

    with patch.object(model_catalog, "OLLAMA_CATALOG", catalog):
        after = {tag: _correction_key(cfg) for tag, cfg in configs.items()}

    assert after[changed] != before[changed]
    assert after[untouched] == before[untouched]


# ── The window the run really uses ─────────────────────────────────────────


def test_the_resolved_window_changes_the_correction_key(tmp_path: Path) -> None:
    """
    Given  one Ollama proofreading config and two windows the run could resolve
    When   the correction fingerprint is computed for each window
    Then   the keys differ, so results made at 8192 are not reused at 16384
    """
    config = make_config(tmp_path, correction_enabled=True, correction_provider="ollama")

    base = _correction_key(config, RunWindows(proofreading=8192, evaluation=8192))
    larger = _correction_key(config, RunWindows(proofreading=16384, evaluation=16384))

    assert base != larger


def test_a_pinned_window_without_resolved_windows_keeps_todays_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a config that pins 16384 and no resolved windows
    When   the correction fingerprint is computed
    Then   it equals the key of the same window passed in, so nothing is invalidated

    The proofreading cap is raised to 16384 for this test: the shipped one is the base window,
    so the setting alone gives 8192, and this test is about the key.
    """
    from teachers_teammate.domain import context_budget  # noqa: PLC0415

    monkeypatch.setattr(context_budget, "PROOFREADING_NUM_CTX_CAP", 16384)
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        proofreading_context="16384",
    )

    assert _correction_key(config) == _correction_key(
        config, RunWindows(proofreading=16384, evaluation=16384)
    )


def test_an_online_provider_has_no_window_part_even_with_resolved_windows(
    tmp_path: Path,
) -> None:
    """
    Given  an online proofreading provider and two different resolved windows
    When   the correction fingerprint is computed for each
    Then   the keys are equal, because an online provider never receives the window
    """
    config = make_config(tmp_path, correction_enabled=True, correction_provider="openai")

    base = _correction_key(config, RunWindows(proofreading=8192, evaluation=8192))
    larger = _correction_key(config, RunWindows(proofreading=16384, evaluation=16384))

    assert base == larger


def test_the_evaluation_key_follows_the_review_window(tmp_path: Path) -> None:
    """
    Given  an Ollama content review and two windows the run could resolve
    When   the evaluation fingerprint is computed for each window
    Then   the keys differ, so a review that failed as "too long" is tried again
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        evaluation_enabled=True,
        evaluate_provider="ollama",
    )

    base = review_key(config, RunWindows(proofreading=8192, evaluation=8192))
    larger = review_key(config, RunWindows(proofreading=8192, evaluation=32768))

    assert base != larger


def test_an_online_content_review_has_no_window_part(tmp_path: Path) -> None:
    """
    Given  an online content review and two different resolved windows
    When   the evaluation fingerprint is computed for each
    Then   the keys are equal, because the app does not set an online provider's window
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        evaluation_enabled=True,
        evaluate_provider="openai",
    )

    base = review_key(config, RunWindows(proofreading=8192, evaluation=8192))
    larger = review_key(config, RunWindows(proofreading=8192, evaluation=32768))

    assert base == larger


def test_todays_catalog_keeps_the_cache_key_of_a_pinned_base_window(tmp_path: Path) -> None:
    """
    Given  today's catalog (no measured context growth) and a 16 GB card
    When   the automatic window is resolved and the correction key is computed
    Then   the key equals the key of a pinned "8192", so an upgrade invalidates nothing
    """
    from teachers_teammate.domain.context_budget import run_windows  # noqa: PLC0415
    from teachers_teammate.domain.hardware_profile import (  # noqa: PLC0415
        FULL_SPEED_SHARE,
        DetectionReason,
        HardwareDetection,
        HardwareProfile,
    )
    from teachers_teammate.domain.model_catalog import find_spec  # noqa: PLC0415

    tag = "gemma4:e4b"
    auto = make_config(
        tmp_path, correction_enabled=True, correction_provider="ollama", correction_model=tag
    )
    pinned = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model=tag,
        proofreading_context="8192",
    )
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_16GB, reason=DetectionReason.GPU_DETECTED, memory_gb=16.0
    )
    windows = run_windows(
        auto.proofreading_context,
        proofreading_spec=find_spec(tag),
        evaluation_spec=None,
        card_gb=detection.card_gb,
        same_model=False,
        full_speed_share=FULL_SPEED_SHARE,
    )

    assert windows.proofreading == 8192
    assert _correction_key(auto, windows) == _correction_key(pinned)


def test_the_cache_service_keys_the_window_it_was_given(tmp_path: Path) -> None:
    """
    Given  two cache services over the same config but different resolved windows
    When   each computes its correction hash
    Then   the hashes differ, because the service keys the window it uses
    """
    cfg = make_config(tmp_path, correction_enabled=True, correction_provider="ollama")
    base = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=MagicMock(),
        artifact_store=MagicMock(),
        config=cfg,
        windows=RunWindows(proofreading=8192, evaluation=8192),
    )
    larger = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=MagicMock(),
        artifact_store=MagicMock(),
        config=cfg,
        windows=RunWindows(proofreading=16384, evaluation=16384),
    )

    assert base._correction_config_hash() != larger._correction_config_hash()


def test_a_changed_temperature_of_steps_two_and_three_changes_the_correction_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an Ollama config and a release that changes the maker's temperature of its model
    When   the correction fingerprint is computed before and after
    Then   the key differs, though step ①'s measured temperature is the same
    """
    from teachers_teammate.domain.request_profile import MAKER_TEMPERATURES  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3.5:9b",
    )
    before = _correction_key(cfg)

    monkeypatch.setitem(MAKER_TEMPERATURES, "qwen3.5:9b", 0.5)

    assert _correction_key(cfg) != before


def test_correction_config_hash_changes_with_a_prompt_variant_and_only_then(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a service, then the same service for a model that is sent a step-one prompt variant
    When   the correction hashes are compared
    Then   they differ, so results made with another prompt are not served as fresh, while a model
           without a variant keeps the hash it always had
    """
    from teachers_teammate.domain.request_profile import STEP_ONE_PROMPTS  # noqa: PLC0415

    svc, _, _ = _make_svc(
        tmp_path, correction_enabled=True, correction_provider="ollama", correction_model="x:1"
    )
    plain = svc._correction_config_hash()

    monkeypatch.setitem(STEP_ONE_PROMPTS, "x:1", "keep")

    assert svc._correction_config_hash() != plain
    monkeypatch.delitem(STEP_ONE_PROMPTS, "x:1")
    assert svc._correction_config_hash() == plain


def test_correction_config_hash_follows_the_text_of_a_variant_not_only_its_id(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a model that is sent the variant "keep"
    When   the text of that variant changes under the same id
    Then   the correction hash changes, so results made with the old text are not served as fresh
    """
    from teachers_teammate.domain import correction_prompts  # noqa: PLC0415
    from teachers_teammate.domain.request_profile import STEP_ONE_PROMPTS  # noqa: PLC0415

    svc, _, _ = _make_svc(
        tmp_path, correction_enabled=True, correction_provider="ollama", correction_model="x:1"
    )
    monkeypatch.setitem(STEP_ONE_PROMPTS, "x:1", "keep")
    before = svc._correction_config_hash()

    monkeypatch.setitem(
        correction_prompts.CORRECT_VARIANTS, "keep", correction_prompts.KEEP_SYSTEM + " More."
    )

    assert svc._correction_config_hash() != before


# ── stage_hashes: what the teacher's work says about the stored stages ─────


def _proofread_state() -> DocumentState:
    """Return a state whose reading and proofreading are done."""
    return replace(
        _make_state(),
        ocr_done=True,
        raw_text="Der bunte Hund",
        ocr_config_hash="ocr_h",
        correction_done=True,
        correction_text="Der braune Hund",
        correction_config_hash="corr_h",
        correction_source_hash=text_identity("Der bunte Hund"),
    )


def _expected() -> ExpectedHashes:
    """Return the hashes the settings of :func:`_make_svc` ask for."""
    return ExpectedHashes(
        ocr_config_hash="ocr_h", correction_config_hash="corr_h", eval_config_hash="eval_h"
    )


def _answer_of(state: DocumentState) -> str:
    """Return the identity of the answer *state* holds."""
    return proofreading_id(
        text_identity(state.raw_text), state.correction_text, state.correction_review
    )


def test_stage_hashes_without_work_freezes_and_keeps_nothing() -> None:
    """
    Given  a document with a reading and a proofreading, and no work
    When   its freshness snapshot is built
    Then   nothing is frozen or kept, and the expected source is the reading itself
    """
    state = _proofread_state()

    hashes = stage_hashes(state, None, _expected())

    assert hashes.ocr_frozen is False
    assert hashes.correction_kept is False
    assert hashes.expected_correction_source == text_identity("Der bunte Hund")
    assert hashes.correction_source == text_identity("Der bunte Hund")


def test_stage_hashes_freezes_the_reading_when_the_teacher_fixed_it() -> None:
    """
    Given  a document whose transcription the teacher fixed
    When   its freshness snapshot is built
    Then   the reading is frozen and the expected source is the teacher's text
    """
    state = _proofread_state()
    work = TeacherWork(
        source_hash=state.source_hash,
        name="doc.png",
        transcription=TranscriptionWork(text="Der bunte Hunt", text_format="plain"),
    )

    hashes = stage_hashes(state, work, _expected())

    assert hashes.ocr_frozen is True
    assert hashes.expected_correction_source == text_identity("Der bunte Hunt")


def test_stage_hashes_keeps_a_proofreading_the_teacher_worked_on() -> None:
    """
    Given  a document whose corrections the teacher changed, bound to the stored answer
    When   its freshness snapshot is built
    Then   the proofreading counts as kept
    """
    state = _proofread_state()
    work = TeacherWork(
        source_hash=state.source_hash,
        name="doc.png",
        corrections=CorrectionsWork(bound_to=_answer_of(state), text="Der braune Hund!"),
    )

    hashes = stage_hashes(state, work, _expected())

    assert hashes.correction_kept is True


def test_stage_hashes_freezes_the_reading_for_corrections_the_teacher_worked_on() -> None:
    """
    Given  a document whose corrections the teacher changed, and no fixed transcription
    When   its freshness snapshot is built
    Then   the reading is frozen too: reading the pages again could change the text the
           proofreading was made from, and the corrections would stop applying
    """
    state = _proofread_state()
    work = TeacherWork(
        source_hash=state.source_hash,
        name="doc.png",
        corrections=CorrectionsWork(bound_to=_answer_of(state), text="Der braune Hund!"),
    )

    hashes = stage_hashes(state, work, _expected())

    assert hashes.ocr_frozen is True
    assert first_stale_stage_for_hashes(replace(hashes, expected_ocr_config_hash="other")) is None


def test_stage_hashes_does_not_freeze_the_reading_for_items_only_looked_at() -> None:
    """
    Given  a corrections part whose items the teacher only looked at
    When   its freshness snapshot is built
    Then   the reading is not frozen: looking is cheap to lose
    """
    state = _proofread_state()
    work = TeacherWork(
        source_hash=state.source_hash,
        name="doc.png",
        corrections=CorrectionsWork(bound_to=_answer_of(state), looked_at=frozenset({"edit:1"})),
    )

    assert stage_hashes(state, work, _expected()).ocr_frozen is False


def test_stage_hashes_does_not_keep_a_part_bound_to_another_answer() -> None:
    """
    Given  a corrections part bound to an answer the cache does not hold
    When   its freshness snapshot is built
    Then   nothing is kept: the part applies to nothing
    """
    state = _proofread_state()
    work = TeacherWork(
        source_hash=state.source_hash,
        name="doc.png",
        corrections=CorrectionsWork(bound_to="another-answer", text="Der braune Hund!"),
    )

    hashes = stage_hashes(state, work, _expected())

    assert hashes.correction_kept is False


def test_stage_hashes_does_not_keep_a_part_the_teacher_only_looked_at() -> None:
    """
    Given  a corrections part whose items the teacher only looked at
    When   its freshness snapshot is built
    Then   nothing is kept: looking is not work
    """
    state = _proofread_state()
    work = TeacherWork(
        source_hash=state.source_hash,
        name="doc.png",
        corrections=CorrectionsWork(bound_to=_answer_of(state), looked_at=frozenset({"edit:1"})),
    )

    hashes = stage_hashes(state, work, _expected())

    assert hashes.correction_kept is False


# ── prepare(): the work the run reads ─────────────────────────────────────


def test_prepare_loads_the_work_of_the_documents_content(tmp_path: Path) -> None:
    """
    Given  a store that holds the work of the document's content
    When   prepare() is called
    Then   the work is in the context, loaded by the file's hash
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    work = TeacherWork(source_hash="filehash", name="doc.png")
    work_store = MagicMock()
    work_store.load.return_value = work
    repo = MagicMock()
    repo.load_or_create.return_value = _make_state()
    repo.reconcile.return_value = _make_state()
    svc = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=MagicMock(),
        config=make_config(tmp_path),
        work_store=work_store,
    )

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="filehash",
    ):
        ctx = svc.prepare(src)

    work_store.load.assert_called_once_with("filehash")
    assert ctx.work is work


def test_prepare_keys_the_task_text_of_the_document_it_reads(tmp_path: Path) -> None:
    """
    Given  a document whose text carries a marked passage of printed text, and the same document
           marked nowhere
    When   prepare() computes the proofreading key of each
    Then   the marked one is keyed by what the passage covers, the other by the settings alone
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    cfg = make_config(tmp_path)
    plain = "Aufgabe 1 Lies den Text!\nAnna schreibt ihrer Freundin."
    marked = replace(
        _make_state(),
        raw_text=plain,
        ocr_done=True,
        task_passages=["Aufgabe 1 Lies den Text!"],
    )

    keys = {}
    for name, state in (("marked", marked), ("plain", replace(marked, task_passages=[]))):
        svc, repo, _ = _make_svc(tmp_path)
        repo.load_or_create.return_value = state
        repo.reconcile.return_value = state
        with patch(
            "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
            return_value="filehash",
        ):
            keys[name] = svc.prepare(src).correction_config_hash

    settings_key = correction_key(cfg, None, make_key_facts())
    assert keys["plain"] == settings_key
    assert keys["marked"] == with_task_text(cfg, settings_key, ("Aufgabe 1 Lies den Text!",))


def test_prepare_with_the_switch_off_leaves_no_text_alone(tmp_path: Path) -> None:
    """
    Given  a document whose text carries a marked passage, and a run that proofreads task text
    When   prepare() computes its context
    Then   the key is the settings key and the context applies no mark
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    state = replace(
        _make_state(),
        raw_text="Aufgabe 1 Lies den Text!\nAnna schreibt ihrer Freundin.",
        ocr_done=True,
        task_passages=["Aufgabe 1 Lies den Text!"],
    )
    cfg = make_config(tmp_path, skip_task_text=False)
    svc, repo, _ = _make_svc(tmp_path, skip_task_text=False)
    repo.load_or_create.return_value = state
    repo.reconcile.return_value = state
    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="filehash",
    ):
        ctx = svc.prepare(src)

    assert ctx.correction_config_hash == correction_key(cfg, None, make_key_facts())
    assert ctx.task_marks == ()
    assert ctx.not_task == ()


def test_prepare_takes_the_not_task_entries_from_the_teachers_work(tmp_path: Path) -> None:
    """
    Given  a marked document, and the teacher's work that proofreads its printed line after all
    When   prepare() computes its context
    Then   the context applies the teacher's entry, and the key is the settings key: the entry
           takes the only mark back
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    task = "Aufgabe 1 Lies den Text!"
    state = replace(
        _make_state(),
        raw_text=f"{task}\nAnna schreibt ihrer Freundin.",
        ocr_done=True,
        task_passages=[task],
    )
    work_store = MagicMock()
    work_store.load.return_value = TeacherWork(
        source_hash="filehash", name="doc.png", not_task=(task,)
    )
    repo = MagicMock()
    repo.load_or_create.return_value = state
    repo.reconcile.return_value = state
    cfg = make_config(tmp_path)
    svc = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=MagicMock(),
        config=cfg,
        work_store=work_store,
    )

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="filehash",
    ):
        ctx = svc.prepare(src)

    assert ctx.task_marks == (task,)
    assert ctx.not_task == (task,)
    assert ctx.correction_config_hash == correction_key(cfg, None, make_key_facts())


def test_prepare_without_a_store_reads_no_work(tmp_path: Path) -> None:
    """
    Given  a cache service without a work store
    When   prepare() is called
    Then   the context holds no work
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    svc, repo, _ = _make_svc(tmp_path)
    repo.load_or_create.return_value = _make_state()
    repo.reconcile.return_value = _make_state()

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="h",
    ):
        ctx = svc.prepare(src)

    assert ctx.work is None


def test_prepare_freezes_the_reading_of_a_document_the_teacher_fixed(tmp_path: Path) -> None:
    """
    Given  a document whose transcription the teacher fixed, and changed reading settings
    When   prepare() is called
    Then   the reading is not reset: the teacher's text is what the document says
    """
    src = tmp_path / "doc.png"
    src.write_bytes(b"data")
    state = replace(
        _make_state(), ocr_done=True, raw_text="Der bunte Hund", ocr_config_hash="old-ocr"
    )
    work = TeacherWork(
        source_hash="h",
        name="doc.png",
        transcription=TranscriptionWork(text="Der bunte Hunt", text_format="plain"),
    )
    work_store = MagicMock()
    work_store.load.return_value = work
    repo = MagicMock()
    repo.load_or_create.return_value = state
    repo.reconcile.return_value = state
    svc = CacheReconciliationService(
        facts=make_key_facts(),
        state_repo=repo,
        artifact_store=MagicMock(),
        config=make_config(tmp_path),
        work_store=work_store,
    )

    with patch(
        "teachers_teammate.infrastructure.workflow.cache_service.compute_file_hash",
        return_value="h",
    ):
        svc.prepare(src)

    assert repo.reconcile.call_args.kwargs["stale_stage"] is None


def test_the_correction_key_of_the_default_profile_is_pinned(tmp_path: Path) -> None:
    """
    Given  the default configuration
    When   the correction config hash is computed
    Then   it is the pinned value, so stored proofreadings stay fresh
    """
    assert (
        _correction_key(make_config(tmp_path))
        == "9c37ff17e56f8e8a7844b8119d6846f8ab51836aba2c1f4e43dfd8f88e302042"
    )
