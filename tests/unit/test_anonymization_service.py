"""Tests for the personal-info removal service: its caches and the spaCy models it loads."""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application.anonymization import AnonymizationService
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.infrastructure.spacy_models import SpacyModels
from tests.conftest import make_config


def _service() -> AnonymizationService:
    """Return a service whose spaCy models are never asked for at construction."""
    return AnonymizationService(names=SpacyModels())


# ── The anonymizer and its caches ─────────────────────────────────────────────


@pytest.mark.unit
def test_clear_anonymizer_cache_empties_dict() -> None:
    """
    Given  a service with cached anonymizer entries
    When   clear_anonymizer_cache() is called
    Then   the internal cache is empty
    """
    svc = _service()
    svc._anonymizer_cache[("English", object())] = MagicMock()
    assert len(svc._anonymizer_cache) == 1

    svc.clear_anonymizer_cache()

    assert len(svc._anonymizer_cache) == 0


@pytest.mark.unit
def test_warm_up_anonymizer_builds_it_once_for_later_exports() -> None:
    """
    Given  a service whose anonymizer was warmed up
    When   anonymize_text() is called with the same language and config
    Then   the cached anonymizer is reused instead of being built again
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("x", {})
    svc = _service()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        svc.warm_up_anonymizer("English")
        svc.anonymize_text("text", "English")

    ctor.assert_called_once()


@pytest.mark.unit
def test_anonymizer_is_never_used_by_two_threads_at_once() -> None:
    """
    Given  a shared anonymizer and several threads anonymizing through the service
    When   the threads run at the same time
    Then   the anonymizer is never entered by more than one thread at a time
    """
    import time  # noqa: PLC0415

    lock = threading.Lock()
    state = {"inside": 0, "max": 0}

    def _slow_anonymize(text: str):
        with lock:
            state["inside"] += 1
            state["max"] = max(state["max"], state["inside"])
        time.sleep(0.02)
        with lock:
            state["inside"] -= 1
        return text, {}

    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = _slow_anonymize
    svc = _service()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        threads = [
            threading.Thread(target=svc.anonymize_text, args=("text", "English")) for _ in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert state["max"] == 1
    ctor.assert_called_once()


@pytest.mark.unit
def test_build_cached_anonymizer_shares_one_result_between_run_and_preview() -> None:
    """
    Given  a service whose anonymizer is built once
    When   the cached anonymizer and anonymize_text() are asked about the same text
    Then   spaCy runs once, so a run and the "What is sent" view agree
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    svc = _service()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        from_run = svc.build_cached_anonymizer("English", AnonymizerConfig())
        from_run.anonymize("Anna wrote")
        from_view = svc.anonymize_text("Anna wrote", "English")

    anonymizer.anonymize.assert_called_once_with("Anna wrote")
    assert from_view == ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})


@pytest.mark.unit
def test_changed_anonymizer_settings_analyse_the_text_again() -> None:
    """
    Given  a text already anonymized with one set of patterns
    When   the preview asks for it with different patterns
    Then   the text is analysed again, because the sent text would differ
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _service()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.anonymize_text("text", "English", AnonymizerConfig(patterns=()))
        svc.anonymize_text("text", "English", AnonymizerConfig(patterns=(("ID", r"\d+"),)))

    assert anonymizer.anonymize.call_count == 2


@pytest.mark.unit
def test_changed_language_analyses_the_text_again() -> None:
    """
    Given  a text already anonymized for English
    When   the same text is asked for in German
    Then   the text is analysed again with the German model
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _service()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        svc.anonymize_text("text", "English")
        svc.anonymize_text("text", "German")

    assert ctor.call_count == 2
    assert anonymizer.anonymize.call_count == 2


@pytest.mark.unit
def test_clear_anonymizer_cache_forgets_the_anonymized_texts() -> None:
    """
    Given  a service with a cached anonymized text
    When   clear_anonymizer_cache() is called
    Then   the result cache is empty as well, so no hidden text outlives the reset
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _service()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.anonymize_text("text", "English")
        assert len(svc._anonymization_cache) == 1

        svc.clear_anonymizer_cache()

    assert len(svc._anonymization_cache) == 0


# ── The spaCy models ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_describe_anonymizer_models_is_empty_without_personal_info_removal(
    tmp_path: Path,
) -> None:
    """
    Given  a config with personal-info removal off
    When   the anonymizer models of the status box are asked for
    Then   there are none, so no model is loaded just to draw the box
    """
    assert _service().describe_anonymizer_models(make_config(tmp_path)) == []


@pytest.mark.unit
def test_describe_anonymizer_models_names_the_resolved_models(tmp_path: Path, mocker) -> None:
    """
    Given  personal-info removal on, a German document and a system language of English
    When   the anonymizer models of the status box are asked for
    Then   the document model and the system model are named, as a run would load them
    """
    mocker.patch(
        "teachers_teammate.infrastructure.spacy_models.installed_spacy_models",
        return_value=["de_core_news_sm", "en_core_web_sm"],
    )
    mocker.patch(
        "teachers_teammate.infrastructure.system_locale.system_language",
        return_value="English",
    )
    cfg = make_config(tmp_path, language="German", anonymization_enabled=True)

    assert _service().describe_anonymizer_models(cfg) == [
        "de_core_news_sm",
        "en_core_web_sm",
    ]


@pytest.mark.unit
def test_installed_models_are_listed_once_until_forgotten(caplog: pytest.LogCaptureFixture) -> None:
    """
    Given  a service that resolves anonymizer settings on every preview
    When   the settings are resolved several times, then models are forgotten
    Then   spaCy is asked for its models once per remembered state, and the missing-model
           warning is logged once
    """
    svc = AnonymizationService(names=SpacyModels())
    config = AnonymizerConfig(secondary_model="auto:system")
    with (
        patch.object(svc, "list_installed_spacy_models", return_value=[]) as listing,
        patch(
            "teachers_teammate.infrastructure.system_locale.system_language",
            return_value="German",
        ),
        caplog.at_level(logging.WARNING),
    ):
        for _ in range(3):
            svc.resolve_anonymizer_config("English", config)
        assert listing.call_count == 1
        assert caplog.text
        first = len(caplog.records)

        svc.resolve_anonymizer_config("English", config)
        assert len(caplog.records) == first  # same warning is not repeated

        svc.forget_installed_models()
        svc.resolve_anonymizer_config("English", config)

        assert listing.call_count == 2


@pytest.mark.unit
def test_rereading_the_installed_models_keeps_what_was_warned(monkeypatch) -> None:
    """
    Given  a service that printed a warning once and remembers the installed models
    When   it rereads the installed models, and later forgets them after an install
    Then   the reread asks spaCy again but keeps the warning quiet; only the install lets it
           be printed again
    """
    svc = _service()
    asked: list[int] = []
    monkeypatch.setattr(
        svc, "list_installed_spacy_models", lambda: asked.append(1) or ["de_core_news_sm"]
    )
    svc._remembered_installed_models()
    svc._warned.add("a warning")

    svc.reread_installed_models()
    svc._remembered_installed_models()

    assert asked == [1, 1]
    assert svc._warned == {"a warning"}

    svc.forget_installed_models()

    assert svc._warned == set()


@pytest.mark.unit
def test_a_chosen_primary_model_counts_for_name_detection(monkeypatch) -> None:
    """
    Given  a chosen primary model that is installed while the model of the document language
           is not
    When   the service is asked whether names can be hidden
    Then   it says yes, because that is the model a run would load
    """
    svc = _service()
    monkeypatch.setattr(svc, "list_installed_spacy_models", lambda: ["de_core_news_sm"])
    svc.forget_installed_models()

    # spaCy itself is not installed under Bazel; only its presence is asked for here.
    monkeypatch.setattr(
        "importlib.util.find_spec", lambda name: object() if name == "spacy" else None
    )
    assert not svc.anonymizer_available("English")
    assert svc.anonymizer_available("English", AnonymizerConfig(primary_model="de_core_news_sm"))
