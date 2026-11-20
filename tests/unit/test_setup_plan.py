"""Unit tests for teachers_teammate.application.setup_plan."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from teachers_teammate.application.setup_plan import (
    DownloadKind,
    HandIn,
    PlannedDownload,
    PrivacyCase,
    ProofreadingPlace,
    SetupAnswers,
    SetupFacts,
    build_model_choices,
    build_setup_plan,
)
from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.local_models import (
    PAGE_MODELS_ADDON_ID,
    PAGE_MODELS_SIZE_GB,
    PAGE_MODELS_SIZE_LABEL,
)
from teachers_teammate.domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
)
from teachers_teammate.domain.model_catalog import (
    DiskVerdict,
    ModelCategory,
    ModelRating,
    Suitability,
    find_spec,
)

#: The recommendation the fake ``best`` names per task.
_BEST: dict[str, str] = {"ocr": "qwen2.5vl:7b", "general": "gemma4:e4b"}


def _rating(tag: str, task: ModelCategory, suitability: Suitability) -> ModelRating:
    """Return a rating of *tag* for *task* with *suitability* and nothing else filled in."""
    return ModelRating(
        tag=tag, task=task, model_uses=None, suitability=suitability, memory_gb=None, note=""
    )


def _rater(ratings: dict[str, Suitability]):
    """Return a ``rate`` that answers from *ratings* and rates everything else ``WORKS``."""

    def rate(tag: str, task: ModelCategory) -> ModelRating:
        return _rating(tag, task, ratings.get(tag, Suitability.WORKS))

    return rate


def _best(task: ModelCategory) -> str:
    """Return the fake recommendation for *task*."""
    return _BEST[task]


def _default_model(provider: str, task: str) -> str:
    """Return a recognisable default model name for *provider* and *task*."""
    return f"{provider}-default-{task}"


def _answers(**overrides: Any) -> SetupAnswers:
    """Return answers for a first start with handwritten pages and local proofreading."""
    base = SetupAnswers(
        language="German",
        hand_in=HandIn.HANDWRITTEN,
        proofreading=ProofreadingPlace.LOCAL,
        ollama_url="http://127.0.0.1:11434",
        hardware_profile="auto",
        local_ai_shown=True,
    )
    return replace(base, **overrides)


def _facts(**overrides: Any) -> SetupFacts:
    """Return the facts of a computer with a running Ollama and plenty of disk space."""
    base = SetupFacts(
        profile="8gb",
        system_memory_gb=32.0,
        card_gb=8.0,
        ollama_running=True,
        installed_tags=frozenset(),
        free_disk_gb=200.0,
        removal_on=False,
        name_detection=False,
        spacy_installed=True,
        spacy_model="de_core_news_sm",
        missing_package="",
    )
    return replace(base, **overrides)


def _current(**overrides: Any) -> dict[str, Any]:
    """Return the settings as they are on a first start, with *overrides* applied."""
    values: dict[str, Any] = dict(DEFAULTS)
    values["anonymization_enabled"] = False
    values.update(overrides)
    return values


def _plan(
    answers: SetupAnswers | None = None,
    current: dict[str, Any] | None = None,
    facts: SetupFacts | None = None,
    ratings: dict[str, Suitability] | None = None,
):
    """Return the setup plan for *answers*, *current* and *facts* with fake ratings."""
    return build_setup_plan(
        answers or _answers(),
        current if current is not None else _current(),
        facts or _facts(),
        rate=_rater(ratings or {}),
        best=_best,
        default_model=_default_model,
    )


def _downloads(plan, kind: DownloadKind) -> list[PlannedDownload]:
    """Return the setup plan's downloads of *kind*."""
    return [download for download in plan.downloads if download.kind is kind]


def _step(plan, stage: str):
    """Return the setup plan's row for *stage*."""
    return next(step for step in plan.steps if step.stage == stage)


# ── reading ─────────────────────────────────────────────────────────────────


def test_a_first_start_with_nothing_installed_proposes_the_best_models_with_their_sizes() -> None:
    """
    Given  a computer with no Ollama model installed and a running server
    When   the setup plan is built for handwritten pages and local proofreading
    Then   the recommendation is proposed for both steps, with its download size
    """
    plan = _plan()

    assert plan.settings["ocr_model"] == "qwen2.5vl:7b"
    assert plan.settings["correction_provider"] == "ollama"
    assert plan.settings["correction_model"] == "gemma4:e4b"
    assert plan.panel["recognition_enabled"] is True
    assert plan.panel["correction_enabled"] is True
    assert plan.panel["language"] == "German"

    models = _downloads(plan, DownloadKind.OLLAMA_MODEL)
    assert [(d.name, d.stage, d.size_gb, d.possible) for d in models] == [
        ("qwen2.5vl:7b", STAGE_OCR, find_spec("qwen2.5vl:7b").download_gb, True),
        ("gemma4:e4b", STAGE_CORRECTION, find_spec("gemma4:e4b").download_gb, True),
    ]
    assert plan.disk is DiskVerdict.ENOUGH


def test_an_installed_model_that_fits_is_kept_without_a_download() -> None:
    """
    Given  the reading and proofreading models are installed and rated GOOD and WORKS
    When   the setup plan is built
    Then   both are kept and nothing is downloaded for them
    """
    plan = _plan(
        current=_current(ocr_model="qwen3-vl:8b", correction_model="mistral:latest"),
        facts=_facts(installed_tags=frozenset({"qwen3-vl:8b", "mistral:latest"})),
        ratings={"qwen3-vl:8b": Suitability.GOOD, "mistral:latest": Suitability.WORKS},
    )

    assert plan.settings["ocr_model"] == "qwen3-vl:8b"
    assert plan.settings["correction_model"] == "mistral:latest"
    assert _step(plan, STAGE_OCR).installed is True
    assert _step(plan, STAGE_CORRECTION).installed is True
    assert _downloads(plan, DownloadKind.OLLAMA_MODEL) == []


def test_an_installed_model_that_is_too_slow_is_replaced_by_the_recommendation() -> None:
    """
    Given  the saved reading model is installed but rated SLOW
    When   the setup plan is built
    Then   the recommendation replaces it and is downloaded
    """
    plan = _plan(
        current=_current(ocr_model="deepseek-ocr:latest"),
        facts=_facts(installed_tags=frozenset({"deepseek-ocr:latest"})),
        ratings={"deepseek-ocr:latest": Suitability.SLOW},
    )

    assert plan.settings["ocr_model"] == "qwen2.5vl:7b"
    assert [
        d.name for d in _downloads(plan, DownloadKind.OLLAMA_MODEL) if d.stage == STAGE_OCR
    ] == ["qwen2.5vl:7b"]


def test_another_installed_catalog_model_that_fits_is_used() -> None:
    """
    Given  the saved reading model is too large and another catalogued reading model is installed
    When   the setup plan is built
    Then   the installed model that fits is used instead of the recommendation
    """
    plan = _plan(
        current=_current(ocr_model="deepseek-ocr:latest"),
        facts=_facts(installed_tags=frozenset({"deepseek-ocr:latest", "glm-ocr:q8_0"})),
        ratings={
            "deepseek-ocr:latest": Suitability.TOO_LARGE,
            "glm-ocr:q8_0": Suitability.GOOD,
        },
    )

    assert plan.settings["ocr_model"] == "glm-ocr:q8_0"
    assert all(d.stage != STAGE_OCR for d in _downloads(plan, DownloadKind.OLLAMA_MODEL))


def test_typed_pages_turn_recognition_off_and_leave_the_reading_model_alone() -> None:
    """
    Given  the teacher hands in typed pages
    When   the setup plan is built
    Then   recognition is off, no reading row appears and the reading model is untouched
    """
    plan = _plan(answers=_answers(hand_in=HandIn.TYPED))

    assert plan.panel["recognition_enabled"] is False
    assert "ocr_model" not in plan.settings
    assert all(step.stage != STAGE_OCR for step in plan.steps)
    assert all(download.stage != STAGE_OCR for download in plan.downloads)


def test_the_built_in_reader_is_kept_and_nothing_is_downloaded_when_it_is_there() -> None:
    """
    Given  the teacher reads pages with the built-in reader, whose model files are downloaded
    When   the setup plan is built for handwritten pages
    Then   the engine stays, the row says so, and nothing is downloaded for it
    """
    plan = _plan(
        current=_current(ocr_engine="ppocr", ocr_model="deu"),
        facts=_facts(reader_installed=True, page_models_installed=True),
    )

    assert "ocr_model" not in plan.settings
    row = _step(plan, STAGE_OCR)
    assert (row.provider, row.kept, row.installed) == ("ppocr", True, True)
    assert all(download.stage != STAGE_OCR for download in plan.downloads)


def test_the_built_in_reader_without_its_models_adds_a_download_and_no_proposal() -> None:
    """
    Given  the teacher reads pages with the built-in reader, which is not downloaded
    When   the setup plan is built for handwritten pages
    Then   one reader download is planned, the reader is kept, and no Ollama model is proposed
    """
    plan = _plan(
        current=_current(ocr_engine="ppocr"),
        facts=_facts(reader_installed=False),
    )

    (download,) = _downloads(plan, DownloadKind.READER_MODEL)
    assert (download.name, download.stage) == ("ppocr", STAGE_OCR)
    assert _step(plan, STAGE_OCR).kept is True
    assert "ocr_model" not in plan.settings
    assert not [d for d in _downloads(plan, DownloadKind.OLLAMA_MODEL) if d.stage == STAGE_OCR]


def test_handwritten_pages_with_automatic_preparation_plan_the_page_models() -> None:
    """
    Given  handwritten pages, the automatic image preparation and no page models downloaded
    When   the setup plan is built
    Then   one page-models download is planned for the reading step, sized like the label
    """
    plan = _plan(
        current=_current(preprocess_method="auto"), facts=_facts(page_models_installed=False)
    )

    (download,) = _downloads(plan, DownloadKind.PAGE_MODELS)
    assert (download.name, download.stage) == (PAGE_MODELS_ADDON_ID, STAGE_OCR)
    assert download.size_gb == PAGE_MODELS_SIZE_GB
    assert f"{PAGE_MODELS_SIZE_GB * 1000:.0f} MB" == PAGE_MODELS_SIZE_LABEL


def test_page_models_that_are_downloaded_are_not_planned_again() -> None:
    """
    Given  handwritten pages with the automatic preparation and the page models downloaded
    When   the setup plan is built
    Then   no page-models download is planned
    """
    plan = _plan(facts=_facts(page_models_installed=True))

    assert not _downloads(plan, DownloadKind.PAGE_MODELS)


def test_the_original_page_needs_no_page_models() -> None:
    """
    Given  handwritten pages and the teacher chose "Original page" for the image preparation
    When   the setup plan is built
    Then   no page-models download is planned, and the choice is not changed
    """
    plan = _plan(current=_current(preprocess_method="none"))

    assert not _downloads(plan, DownloadKind.PAGE_MODELS)
    assert "preprocess_method" not in plan.settings


def test_typed_pages_need_no_page_models() -> None:
    """
    Given  a class that hands in typed texts, so no page is read or prepared
    When   the setup plan is built
    Then   no page-models download is planned
    """
    plan = _plan(answers=_answers(hand_in=HandIn.TYPED, proofreading=ProofreadingPlace.OFF))

    assert not _downloads(plan, DownloadKind.PAGE_MODELS)


def test_the_page_models_are_planned_for_the_built_in_reader_too() -> None:
    """
    Given  handwritten pages read by the built-in reader, which is downloaded, and no page models
    When   the setup plan is built
    Then   only the page models are planned (they serve every reading engine)
    """
    plan = _plan(
        current=_current(ocr_engine="ppocr"),
        facts=_facts(reader_installed=True, page_models_installed=False),
    )

    assert [d.kind for d in plan.downloads if d.stage == STAGE_OCR] == [DownloadKind.PAGE_MODELS]


def test_typed_pages_need_no_reader_download() -> None:
    """
    Given  the built-in reader chosen but not downloaded, and a class that hands in typed texts
    When   the setup plan is built
    Then   no reader download is planned (no page is read)
    """
    plan = _plan(
        answers=_answers(hand_in=HandIn.TYPED),
        current=_current(ocr_engine="ppocr"),
        facts=_facts(reader_installed=False),
    )

    assert not _downloads(plan, DownloadKind.READER_MODEL)


# ── proofreading and review ─────────────────────────────────────────────────


def test_online_proofreading_takes_the_chosen_pair_and_the_review_follows_it() -> None:
    """
    Given  the teacher picked an online service for the proofreading
    When   the setup plan is built
    Then   the correction and review keys carry that pair
    """
    plan = _plan(
        answers=_answers(
            proofreading=ProofreadingPlace.ONLINE,
            online_choice=("mistral", "mistral-small-latest"),
        )
    )

    assert plan.settings["correction_provider"] == "mistral"
    assert plan.settings["correction_model"] == "mistral-small-latest"
    assert plan.settings["evaluate_provider"] == "mistral"
    assert plan.settings["evaluate_model"] == "mistral-small-latest"
    assert plan.review_kept is False
    assert all(d.stage != STAGE_CORRECTION for d in _downloads(plan, DownloadKind.OLLAMA_MODEL))


def test_an_online_service_without_a_model_gets_its_default_for_the_review() -> None:
    """
    Given  the teacher picked an online service and left the model to the app
    When   the setup plan is built
    Then   the review gets the provider's default model for the correction task
    """
    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, online_choice=("mistral", ""))
    )

    assert plan.settings["correction_model"] == ""
    assert plan.settings["evaluate_model"] == "mistral-default-correction"


def test_a_review_with_its_own_model_is_kept() -> None:
    """
    Given  the content review uses another service than the proofreading
    When   the setup plan is built
    Then   the review keys are left alone and the setup plan says the review was kept
    """
    plan = _plan(
        current=_current(evaluate_provider="openai", evaluate_model="gpt-4o"),
        answers=_answers(
            proofreading=ProofreadingPlace.ONLINE,
            online_choice=("mistral", "mistral-small-latest"),
        ),
    )

    assert plan.review_kept is True
    assert "evaluate_provider" not in plan.settings
    assert "evaluate_model" not in plan.settings
    row = _step(plan, STAGE_EVALUATION)
    assert (row.provider, row.model, row.kept) == ("openai", "gpt-4o", True)


def test_proofreading_off_leaves_the_correction_and_review_keys_alone() -> None:
    """
    Given  the teacher said "not now" to proofreading
    When   the setup plan is built
    Then   proofreading is off and neither the correction nor the review keys change
    """
    plan = _plan(answers=_answers(proofreading=ProofreadingPlace.OFF))

    assert plan.panel["correction_enabled"] is False
    assert "correction_provider" not in plan.settings
    assert "correction_model" not in plan.settings
    assert "evaluate_provider" not in plan.settings
    assert "evaluate_model" not in plan.settings
    assert [d.stage for d in _downloads(plan, DownloadKind.OLLAMA_MODEL)] == [STAGE_OCR]


# ── names ───────────────────────────────────────────────────────────────────


def test_online_proofreading_with_names_turns_removal_on_and_adds_name_detection() -> None:
    """
    Given  online proofreading, names to remove and no name detection installed
    When   the setup plan is built
    Then   removal is turned on and the spaCy model is downloaded
    """
    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, remove_names=True),
        facts=_facts(name_detection=False, spacy_installed=True),
    )

    assert plan.panel["anonymization_enabled"] is True
    assert [
        (d.kind, d.name, d.stage) for d in plan.downloads if d.stage == STAGE_ANONYMIZATION
    ] == [(DownloadKind.SPACY_MODEL, "de_core_news_sm", STAGE_ANONYMIZATION)]
    assert _step(plan, STAGE_ANONYMIZATION).installed is False


def test_name_detection_downloads_the_spacy_package_first_when_it_is_missing() -> None:
    """
    Given  online proofreading with names to remove and no spaCy at all
    When   the setup plan is built
    Then   the package comes before the model, in the order the install round runs them
    """
    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, remove_names=True),
        facts=_facts(name_detection=False, spacy_installed=False),
    )

    assert [(d.kind, d.name) for d in plan.downloads if d.stage == STAGE_ANONYMIZATION] == [
        (DownloadKind.PACKAGE, "spacy"),
        (DownloadKind.SPACY_MODEL, "de_core_news_sm"),
    ]


def test_an_extra_language_is_saved_and_downloaded_when_it_is_missing() -> None:
    """
    Given  names to remove, German texts and an extra English one that is not downloaded
    When   the setup plan is built
    Then   the English model is the saved extra detection model and is downloaded after the main
           one, marked as an extra; an extra model that is installed is saved but not downloaded
    """
    answers = _answers(
        proofreading=ProofreadingPlace.ONLINE, remove_names=True, extra_names_model="en_core_web_sm"
    )

    plan = _plan(answers=answers, facts=_facts(name_detection=False))

    assert plan.settings["anonymizer_secondary_model"] == "en_core_web_sm"
    assert [(d.name, d.extra) for d in _downloads(plan, DownloadKind.SPACY_MODEL)] == [
        ("de_core_news_sm", False),
        ("en_core_web_sm", True),
    ]

    installed = _plan(
        answers=answers,
        facts=_facts(name_detection=True, installed_spacy_models=frozenset({"en_core_web_sm"})),
    )

    assert installed.settings["anonymizer_secondary_model"] == "en_core_web_sm"
    assert not _downloads(installed, DownloadKind.SPACY_MODEL)


def test_no_extra_language_leaves_the_saved_extra_model_alone() -> None:
    """
    Given  names to remove, no extra language, and the main language's own model as "extra"
    When   the setup plan is built
    Then   no extra detection model is written either way: the assistant changes nothing it was
           not asked to
    """
    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, remove_names=True),
        facts=_facts(name_detection=True),
    )
    same = _plan(
        answers=_answers(
            proofreading=ProofreadingPlace.ONLINE,
            remove_names=True,
            extra_names_model="de_core_news_sm",
        ),
        facts=_facts(name_detection=True),
    )

    assert "anonymizer_secondary_model" not in plan.settings
    assert "anonymizer_secondary_model" not in same.settings


def test_a_provider_package_is_only_planned_for_online_proofreading() -> None:
    """
    Given  an online service whose package is missing, while proofreading runs here or is off
    When   the setup plan is built
    Then   no package is downloaded; it is planned only once proofreading goes online
    """
    facts = _facts(missing_package="langchain-anthropic")
    online = ("anthropic", "claude-sonnet")

    for place in (ProofreadingPlace.LOCAL, ProofreadingPlace.OFF):
        plan = _plan(answers=_answers(proofreading=place, online_choice=online), facts=facts)
        assert not _downloads(plan, DownloadKind.PACKAGE)

    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, online_choice=online),
        facts=facts,
    )
    assert [d.name for d in _downloads(plan, DownloadKind.PACKAGE)] == ["langchain-anthropic"]


def test_removal_that_is_on_already_is_not_turned_on_again() -> None:
    """
    Given  "Remove personal info" is ticked and name detection is installed
    When   the setup plan is built for online proofreading with names to remove
    Then   the panel is left alone and nothing is downloaded for the names
    """
    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, remove_names=True),
        facts=_facts(removal_on=True, name_detection=True),
    )

    assert "anonymization_enabled" not in plan.panel
    assert all(download.stage != STAGE_ANONYMIZATION for download in plan.downloads)
    assert _step(plan, STAGE_ANONYMIZATION).installed is True


def test_a_teacher_who_keeps_removal_off_changes_nothing() -> None:
    """
    Given  online proofreading and the teacher unticked "Remove personal info"
    When   the setup plan is built
    Then   removal stays off and no name detection is downloaded
    """
    plan = _plan(
        answers=_answers(proofreading=ProofreadingPlace.ONLINE, remove_names=False),
        facts=_facts(name_detection=False, spacy_installed=False),
    )

    assert "anonymization_enabled" not in plan.panel
    assert all(download.stage != STAGE_ANONYMIZATION for download in plan.downloads)
    assert all(step.stage != STAGE_ANONYMIZATION for step in plan.steps)


# ── downloads, disk and the address ─────────────────────────────────────────


def test_models_that_cannot_be_pulled_now_are_marked_impossible() -> None:
    """
    Given  Ollama is not running
    When   the setup plan is built
    Then   its model downloads are marked impossible and the disk verdict is unknown
    """
    plan = _plan(facts=_facts(ollama_running=False, installed_tags=frozenset()))

    models = _downloads(plan, DownloadKind.OLLAMA_MODEL)
    assert models
    assert all(download.possible is False for download in models)
    assert plan.disk is DiskVerdict.UNKNOWN


def test_the_disk_verdict_counts_only_the_downloads_that_can_run() -> None:
    """
    Given  7 GB free and 12.6 GB of models to pull
    When   the setup plan is built with Ollama running and again with it stopped
    Then   the running server gives "not enough" and the stopped one "unknown"
    """
    running = _plan(facts=_facts(free_disk_gb=7.0))
    stopped = _plan(facts=_facts(free_disk_gb=7.0, ollama_running=False))

    assert running.disk is DiskVerdict.NOT_ENOUGH
    assert stopped.disk is DiskVerdict.UNKNOWN


def test_the_address_and_the_profile_are_left_alone_when_the_local_page_was_skipped() -> None:
    """
    Given  the Local AI page was not part of the path
    When   the setup plan is built
    Then   the server address and the hardware profile stay as they are
    """
    plan = _plan(answers=_answers(local_ai_shown=False))

    assert "ollama_url" not in plan.settings
    assert "hardware_profile" not in plan.settings


def test_the_address_and_the_profile_are_written_when_the_local_page_was_shown() -> None:
    """
    Given  the teacher chose an address and a profile on the Local AI page
    When   the setup plan is built
    Then   both go into the settings
    """
    plan = _plan(answers=_answers(ollama_url="http://192.168.1.9:11434", hardware_profile="16gb"))

    assert plan.settings["ollama_url"] == "http://192.168.1.9:11434"
    assert plan.settings["hardware_profile"] == "16gb"


# ── a model the teacher chose ───────────────────────────────────────────────


def test_a_chosen_local_model_is_used_and_pulled_when_it_is_missing() -> None:
    """
    Given  local proofreading with a model the teacher chose that is not installed
    When   the setup plan is built
    Then   the proofreading uses it instead of the recommendation, and it is downloaded
    """
    plan = _plan(answers=_answers(local_model="qwen3:8b"))

    assert plan.settings["correction_model"] == "qwen3:8b"
    assert _step(plan, STAGE_CORRECTION).model == "qwen3:8b"
    assert "qwen3:8b" in [d.name for d in _downloads(plan, DownloadKind.OLLAMA_MODEL)]
    assert _BEST["general"] not in [d.name for d in _downloads(plan, DownloadKind.OLLAMA_MODEL)]


def test_a_chosen_local_model_that_is_installed_needs_no_download() -> None:
    """
    Given  a chosen local model that Ollama already has
    When   the setup plan is built
    Then   the row says it is installed and nothing is pulled for the proofreading
    """
    plan = _plan(
        answers=_answers(local_model="qwen3:8b"),
        facts=_facts(installed_tags=frozenset({"qwen3:8b"})),
    )

    assert _step(plan, STAGE_CORRECTION).installed is True
    assert "qwen3:8b" not in [d.name for d in _downloads(plan, DownloadKind.OLLAMA_MODEL)]


def test_the_chosen_model_is_ignored_for_online_proofreading() -> None:
    """
    Given  a model chosen for the local AI, and then online proofreading
    When   the setup plan is built
    Then   the online choice is what is used
    """
    plan = _plan(
        answers=_answers(
            local_model="qwen3:8b",
            proofreading=ProofreadingPlace.ONLINE,
            online_choice=("openai", "gpt-x"),
        )
    )

    assert plan.settings["correction_provider"] == "openai"
    assert plan.settings["correction_model"] == "gpt-x"


# ── who gets the text ───────────────────────────────────────────────────────


def test_the_privacy_case_follows_where_the_text_goes() -> None:
    """
    Given  proofreading online, on Ollama at another PC, on this computer and not at all
    When   the privacy case of each is asked
    Then   they are online, network PC, this computer and this computer
    """
    network = "http://192.168.1.20:11434"
    assert _answers(proofreading=ProofreadingPlace.ONLINE).privacy_case is PrivacyCase.ONLINE
    assert _answers(ollama_url=network).privacy_case is PrivacyCase.NETWORK_PC
    assert _answers().privacy_case is PrivacyCase.THIS_COMPUTER
    assert (
        _answers(proofreading=ProofreadingPlace.OFF, ollama_url=network).privacy_case
        is PrivacyCase.THIS_COMPUTER
    )


def test_proofreading_on_another_pc_can_remove_names_before_the_text_leaves() -> None:
    """
    Given  proofreading on Ollama at another PC, with the names to be removed
    When   the setup plan is built
    Then   removal is turned on and name detection is planned, as for an online service
    """
    plan = _plan(
        answers=_answers(ollama_url="http://192.168.1.20:11434", remove_names=True),
        facts=_facts(name_detection=False),
    )

    assert plan.panel["anonymization_enabled"] is True
    assert _step(plan, STAGE_ANONYMIZATION).view_only is False
    assert [d.kind for d in plan.downloads if d.stage == STAGE_ANONYMIZATION] == [
        DownloadKind.SPACY_MODEL
    ]


def test_with_everything_on_this_computer_removal_is_never_turned_on() -> None:
    """
    Given  everything on this computer and the "remove names" answer set (a stale answer)
    When   the setup plan is built
    Then   removal stays as it is: nothing is sent, so there is nothing to remove
    """
    plan = _plan(answers=_answers(remove_names=True))

    assert "anonymization_enabled" not in plan.panel
    assert not any(step.stage == STAGE_ANONYMIZATION for step in plan.steps)


def test_hiding_names_in_the_app_asks_for_name_detection_and_the_view_only() -> None:
    """
    Given  everything on this computer, and names to be hidden in the app's texts
    When   the setup plan is built
    Then   the view setting is turned on, the spaCy model is planned, removal is left alone, and
           the row says it serves the view only
    """
    plan = _plan(answers=_answers(hide_names=True), facts=_facts(name_detection=False))

    assert plan.display_anonymized is True
    assert "anonymization_enabled" not in plan.panel
    assert _step(plan, STAGE_ANONYMIZATION).view_only is True
    assert [d.kind for d in plan.downloads if d.stage == STAGE_ANONYMIZATION] == [
        DownloadKind.SPACY_MODEL
    ]


def test_not_hiding_names_leaves_the_view_setting_alone() -> None:
    """
    Given  the Privacy page's box left unticked
    When   the setup plan is built
    Then   the setup plan says nothing about the view setting, so the assistant never turns it off
    """
    assert _plan(answers=_answers(hide_names=False)).display_anonymized is None


# ── the models the proofreading page offers ─────────────────────────────────


def test_the_choices_hold_installed_models_and_catalog_models_that_can_work_here() -> None:
    """
    Given  one installed model outside the catalog, and a rating that rules a big catalog model
           out for this computer
    When   the choices are built
    Then   the installed model is offered, the ruled-out one is not, the best come first, and
           the suggestion is what the setup plan would use
    """
    ratings = {
        "my-own:7b": Suitability.WORKS,
        _BEST["general"]: Suitability.BEST,
        "gpt-oss:120b": Suitability.TOO_LARGE,
    }
    choices = build_model_choices(
        _facts(installed_tags=frozenset({"my-own:7b"})),
        suggested=_BEST["general"],
        rate=_rater(ratings),
    )

    tags = [choice.tag for choice in choices.choices]
    assert choices.suggested == _BEST["general"]
    assert tags[0] == _BEST["general"]
    assert "my-own:7b" in tags
    assert "gpt-oss:120b" not in tags
    assert next(c for c in choices.choices if c.tag == "my-own:7b").installed is True
    assert next(c for c in choices.choices if c.tag == _BEST["general"]).installed is False


def test_an_installed_model_is_offered_even_when_it_is_too_large() -> None:
    """
    Given  an installed model that is too large for this computer
    When   the choices are built
    Then   it is still listed (the teacher may know why it is there)
    """
    choices = build_model_choices(
        _facts(installed_tags=frozenset({"huge:70b"})),
        suggested="",
        rate=_rater({"huge:70b": Suitability.TOO_LARGE}),
    )

    assert "huge:70b" in [choice.tag for choice in choices.choices]
