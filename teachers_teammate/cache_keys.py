"""The keys of the stored results: what a result was made from.

A key says what a stored result was made with, so a run reads, proofreads or reviews a document
again exactly when its key differs from the stored one.  Every key is a pure function of the
settings, the windows a run resolved and the facts of the computer
(:class:`~teachers_teammate.domain.key_facts.KeyFacts`): no file system, no server.

A key is also the identity of what was already sent to a provider, and that cannot be sent back.
So a key changes only with an intended bump of ``AUTO_VERSION``, ``PROMPT_VERSION``,
``READER_VERSION`` or the reader's fingerprint — never with a move or a refactoring of this code.
``tests/unit/test_cache_keys_golden.py`` holds the digests that must not move.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .config import Config, OcrConfig
from .domain.anonymization import resolve_anonymizer_config
from .domain.context_budget import RunWindows, window_from_setting
from .domain.correction_policy import CorrectionOutput
from .domain.data_location import ENGINE_PPOCR, SentRecord, is_ollama
from .domain.freshness import ExpectedHashes, compute_config_hash
from .domain.key_facts import KeyFacts
from .domain.local_models import auto_identity, reader_fingerprint
from .domain.ocr_prompt import get_ocr_prompt
from .domain.ollama_address import ollama_host
from .domain.pdf_text_layer import input_fingerprint
from .domain.review_prompts import review_wording
from .domain.stages import Stage


def preprocess_fingerprint(ocr: OcrConfig, source: Path, facts: KeyFacts) -> tuple[str, ...]:
    """Return the preprocessing settings that change the image fed to OCR.

    Every entry is included in both the OCR key and the preview key, so a changed setting (the
    method, the PDF DPI) invalidates the cached result instead of returning stale OCR text or a
    stale preview image.

    The automatic preparation adds what decides its pixels (``auto_identity``): its version,
    whether the engine gets gray or colour, and which page models are downloaded — so a new
    version of the steps, or downloading the page models, reads pages again once.  "Original
    page" is just ``none``.

    *source* is the file being processed: format-specific input settings (the PDF text-layer
    mode) are only included for the formats they affect, so changing one does not invalidate
    results of other files.
    """
    text_layer = input_fingerprint(source.suffix, ocr.pdf_text_layer)
    identity = (
        auto_identity(ocr.engine, facts.page_steps()) if ocr.preprocess_method == "auto" else ()
    )
    return (ocr.preprocess_method, *identity, str(ocr.pdf_render_dpi), *text_layer)


def ocr_key(config: Config, source: Path, facts: KeyFacts) -> str:
    """Return the OCR stage key for *config* when it reads *source*."""
    ocr = config.ocr
    if ocr.engine == ENGINE_PPOCR:
        # The reader has no model tag, language or prompt of its own: a new model revision or
        # reader version re-reads the pages, a changed language or Ollama model does not.
        return compute_config_hash(
            Stage.OCR,
            ocr.engine,
            ocr.engine,
            reader_fingerprint(),
            *preprocess_fingerprint(ocr, source, facts),
            "-",
            "reader",
            "-",
        )
    return compute_config_hash(
        Stage.OCR,
        ocr.engine,
        # Two slots for one value: the second is the provider slot the key has room for, and
        # dropping it would change every OCR cache key.
        ocr.engine,
        ocr.model,
        *preprocess_fingerprint(ocr, source, facts),
        config.language,
        get_ocr_prompt(config.language),
        format(ocr.temperature, ".1f"),
    )


def preview_key(config: Config, file: Path, source_hash: str, facts: KeyFacts) -> str:
    """Return the key of the prepared page image of *file*, whose content hash is *source_hash*.

    The hash of the content says which pixels were read; the file says which input settings
    apply, because only the formats they affect are part of the key.
    """
    return compute_config_hash(
        "preview", source_hash, *preprocess_fingerprint(config.ocr, file, facts)
    )


def anonymization_fingerprint(config: Config, facts: KeyFacts) -> tuple[str, ...]:
    """Return the hash parts of the anonymizer settings: what a text is made with.

    Empty when off.  The resolved models and patterns are included: they decide which spans
    become placeholders, so two texts made with different rules are different texts.

    Deliberately *not* part of a stage key: a stored result is valid for the text it was made
    from, not for the setting that produced that text.  Hashing the setting would send every
    text again whenever a teacher turns names-hiding on or improves a pattern — and what was
    already sent cannot be unsent.  The window compares the settings instead
    (:func:`privacy_key`).
    """
    if not config.anonymization_enabled:
        return ()
    # Deliberately without the installed models: hashing must not import spaCy.  So the key
    # holds the model the settings ask for, whether or not it is downloaded; downloading a
    # missing automatic model later does not change it.
    resolved, _notice = resolve_anonymizer_config(
        config.anonymizer_config,
        document_language=config.language,
        system_language=facts.system_language(),
    )
    return (
        "anonymized",
        resolved.primary_model or "",
        resolved.secondary_model or "",
        *(f"{tag}={pattern}" for tag, pattern in resolved.patterns),
        # Only when there is one: a result stored before these lists existed recorded a hash
        # without them, and must still match.
        *(f"hide={literal}" for literal in resolved.hide),
        *(f"keep={literal}" for literal in resolved.keep),
    )


def privacy_key(config: Config, facts: KeyFacts) -> str:
    """Return the hash of the anonymizer settings a text is made with.

    Compared with the fingerprint a stored result recorded, so the window can say that a result
    was made with other privacy settings.  Computed from the settings alone (no spaCy), so the
    comparison needs no run.
    """
    return compute_config_hash(*anonymization_fingerprint(config, facts))


def sent_record(
    config: Config, stage: Stage, *, names_hidden: int = 0, facts: KeyFacts
) -> SentRecord:
    """Return how a result of *stage* is sent with *config*.

    *names_hidden* is the number of placeholders the text was sent with (0 when names went as
    written).  The endpoint is what decides the location besides provider and model: the Ollama
    host.  A provider whose region depends on a setting (a custom base URL, a regional endpoint)
    adds that setting here; none does today, so an online provider has no endpoint.
    """
    if stage == Stage.EVALUATION:
        provider, model = config.evaluate_provider, config.effective_evaluate_model
    else:
        provider, model = config.correction_provider, config.effective_correction_model
    return SentRecord(
        provider=provider,
        model=model,
        endpoint=ollama_host(config.ollama_url) if is_ollama(provider) else "",
        names_hidden=names_hidden,
        hiding_on=config.anonymization_enabled,
        privacy=privacy_key(config, facts),
    )


def window_fingerprint(config: Config, windows: RunWindows | None = None) -> tuple[str, ...]:
    """Return the key part for the context window of the proofreading requests.

    *windows* are the windows the run really uses, resolved from the settings and the computer.
    Without them only the settings are known, which gives the pinned window and the base step
    for ``"auto"`` — the same key a run at the base window produces.
    """
    if not is_ollama(config.correction_provider):
        return ()
    window = (
        windows.proofreading
        if windows is not None
        else window_from_setting(config.proofreading_context)
    )
    return (f"num-ctx-{window}",)


def evaluation_window_fingerprint(
    config: Config, windows: RunWindows | None = None
) -> tuple[str, ...]:
    """Return the key part for the context window of the content review.

    A larger window can make a review succeed that failed with "too long", so the cached failure
    of a smaller window must not be reused.  An online provider brings its own window, which the
    app does not set.
    """
    if not is_ollama(config.evaluate_provider) or windows is None:
        return ()
    return (f"num-ctx-{windows.evaluation}",)


def correction_key(config: Config, windows: RunWindows | None, facts: KeyFacts) -> str:
    """Return the proofreading stage key for *config*.

    The key describes the settings, whatever the switch: a switched-off stage is "this run does
    not do it", not "throw the stored result away".  Only a stage that is enabled can make a
    stored result stale (see :func:`~teachers_teammate.domain.freshness.first_stale_stage_for_hashes`).
    """
    # What is sent, not the setting: the exchange's fixed prompts and the model's request
    # profile are what decide the requests, so both key the cache.  The profile carries every
    # temperature, the thinking and the sampling values of the steps, so a change of any of them
    # invalidates the cached corrections of that model and no other.
    profile = config.correction_profile
    return compute_config_hash(
        Stage.CORRECTION,
        config.correction_provider,
        config.effective_correction_model,
        config.language,
        facts.exchange_identity(
            profile, corrections_only=config.correction_output == CorrectionOutput.TEXT
        ),
        # What the teacher set changes the answer itself, so it belongs in the key.
        config.correction_level,
        config.explanation_language,
        config.correction_output,
        config.correction_notes.strip(),
        # The window decides how a long text is split into requests, so a changed window must
        # proofread again.  Only an Ollama corrector uses it (an online provider has its own),
        # and only the window it really gets counts: a setting above the cap changes nothing.
        # *windows* is the window the run resolved from the settings and the computer; without it
        # the settings alone decide, which is the base step for "auto".
        *window_fingerprint(config, windows),
    )


def with_task_text(config: Config, key: str, task_text: Sequence[str]) -> str:
    """Return the proofreading key of one document: *key* plus what it leaves alone.

    *task_text* is what the document's marks really cover
    (:func:`~teachers_teammate.domain.document_state.task_text_of`), so marking a
    passage, or undoing that, makes the stored answer stale for the documents it touches and for
    no other.  A document that leaves nothing alone, and every document while
    ``skip_task_text`` is off, keeps the settings key itself.
    """
    if not task_text or not config.skip_task_text:
        return key
    return compute_config_hash(key, *task_text)


def review_key(config: Config, windows: RunWindows | None = None) -> str:
    """Return the evaluation stage key for *config*.

    Like the proofreading's, it describes the settings whatever the switch.
    """
    return compute_config_hash(
        Stage.EVALUATION,
        config.evaluate_provider,
        config.effective_evaluate_model,
        config.language,
        config.evaluate_prompt,
        review_wording(),
        format(config.evaluate_temperature, "g"),
        str(config.evaluate_think),
        *evaluation_window_fingerprint(config, windows),
    )


def expected_hashes(
    config: Config,
    file: Path,
    windows: RunWindows | None,
    facts: KeyFacts,
    *,
    task_text: Sequence[str],
) -> ExpectedHashes:
    """Return the stage hashes the settings of *config* ask for, for the document *file*.

    *windows* is ``None`` while this computer has not been probed; the hashes then key the
    settings alone, as the run's own base step does.  *task_text* is what the document leaves
    alone, which belongs to its proofreading and to no other stage; it has no default, so no
    caller keys a marked document as if it had no marks.
    """
    return ExpectedHashes(
        ocr_config_hash=ocr_key(config, file, facts),
        correction_config_hash=with_task_text(
            config, correction_key(config, windows, facts), task_text
        ),
        eval_config_hash=review_key(config, windows),
        correction_enabled=config.correction_enabled,
        evaluation_enabled=config.evaluation_enabled,
    )
