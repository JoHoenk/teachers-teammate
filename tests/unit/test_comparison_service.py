"""Comparing writing models on one text: the models to choose from, and their answers.

The service and the list of models are given everything they touch: the store, the unit that
proofreads a text, the document, the AI server, the model lists and the measurements.  So these tests hand it fakes and a
:class:`~tests.fakes.fake_ollama.FakeOllama` for the one thing that must stay real — a model that
was loaded has to be unloaded again.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import logging
from pathlib import Path
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application.compare_candidates import ComparisonCandidates
from teachers_teammate.application.document_view import DocumentStateView
from teachers_teammate.application.model_comparison import (
    Candidate,
    CompareSource,
    ComparedRun,
    ComparisonParts,
    JobHooks,
    ModelComparisonService,
)
from teachers_teammate.application.ollama_server import OllamaServerService
from teachers_teammate.application.stored_results import RunInProgressError
from teachers_teammate.cache_keys import correction_key
from teachers_teammate.messages import proofreading_not_compared
from teachers_teammate.domain.context_budget import BASE_NUM_CTX, RunWindows
from teachers_teammate.domain.data_location import DataLocation, StageLocation
from teachers_teammate.domain.model_fit import ModelRating, Suitability
from teachers_teammate.domain.performance_estimate import TimeRange
from teachers_teammate.domain.proofreading.compared_result import ComparedResult, stem_of
from teachers_teammate.domain.proofreading.option_marks import OptionMark, Verdict
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.teacher_work import text_identity
from teachers_teammate.exceptions import RunStoppedError, StageError, StageErrorKind
from teachers_teammate.infrastructure.speed_store import SampleStore
from teachers_teammate.infrastructure.stage_builder import PipelineComponentFactory
from teachers_teammate.infrastructure.workflow.proofread_text import ProofreadOutcome
from teachers_teammate.infrastructure.workflow.proofread_unit import proofread_unit_for
from teachers_teammate.messages import MessageKey
from tests.conftest import make_config, make_key_facts
from tests.fakes.fake_ollama import FakeOllama

TEXT = "Der Hund ist laut. Er bellt die ganze Nacht."


# ── what the service is given ──────────────────────────────────────────────


@dataclass
class _Results:
    """The compared results a test keeps in memory instead of in the cache."""

    saved: dict[tuple[str, str], ComparedResult] = field(default_factory=dict)

    def results(self, text_sha: str) -> dict[str, ComparedResult]:
        return {result.stem: result for (sha, _), result in self.saved.items() if sha == text_sha}

    def load(self, text_sha: str, key: str, repeat: int = 0) -> ComparedResult | None:
        return self.saved.get((text_sha, stem_of(key, repeat)))

    def save(self, result: ComparedResult) -> None:
        self.saved[(result.text_sha, result.stem)] = result


@dataclass
class _Marks:
    """The teacher's verdicts a test keeps in memory instead of in the data root."""

    saved: dict[str, tuple[OptionMark, ...]] = field(default_factory=dict)

    def marks(self, text_sha: str) -> tuple[OptionMark, ...]:
        return self.saved.get(text_sha, ())

    def save(self, text_sha: str, marks: tuple[OptionMark, ...]) -> None:
        self.saved[text_sha] = marks


class _Runs:
    """A run in progress as the comparison sees it: it counts, and it refuses."""

    def __init__(self) -> None:
        self.going = 0

    def busy(self) -> bool:
        return self.going > 0

    @contextmanager
    def running(self):
        self.going += 1
        try:
            yield
        finally:
            self.going -= 1


class _Providers:
    """The model lists: the ones each provider offers, the ones the app holds, and where a model
    runs.  A provider in *held* has its list held already; asking for a list holds it."""

    def __init__(
        self,
        lists: dict[str, list[str]],
        *,
        held: set[str] | None = None,
        keyless: set[str] | None = None,
    ) -> None:
        self._lists = lists
        self._held = set(lists) if held is None else held
        self._keyless = keyless or set()
        #: The providers whose list was asked for, in order.
        self.asked: list[str] = []

    def list_providers(self) -> list[str]:
        return list(self._lists)

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        self.asked.append(provider)
        self._held.add(provider)
        return list(self._lists.get(provider, ()))

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return self._lists.get(provider) if provider in self._held else None

    def provider_key_missing(self, provider: str) -> bool:
        return provider in self._keyless

    def missing_provider_package(self, provider: str) -> str | None:
        return None

    def describe_llm_location(self, provider: str, model: str, ollama_url: str) -> StageLocation:
        if provider == "ollama":
            return StageLocation(DataLocation.THIS_COMPUTER, "Ollama", host=ollama_url)
        return StageLocation(DataLocation.ONLINE, provider.title())


class _Advice:
    """The ratings of this computer's models, and no detection at all."""

    def __init__(self, ratings: list[ModelRating]) -> None:
        self._ratings = ratings
        #: The tags the last classification was asked for.
        self.classified: list[str] = []

    def cached_hardware_profile(self, setting: Any, ollama_url: str) -> None:
        return None

    def rate_for(self, tags, task, detection, setting, ollama_url, **_kwargs) -> list[ModelRating]:
        return [rating for rating in self._ratings if rating.tag in tags]

    def classify_ollama_models(self, tags, ollama_url, *, cached_only: bool = False) -> dict:
        self.classified = list(tags)
        return dict.fromkeys(tags)


class _Speeds:
    """What this computer measured: one figure per model and text length."""

    def __init__(self, times: dict[str, TimeRange] | None = None) -> None:
        self._times = times or {}
        self.asked: list[int] = []

    def page_times(
        self, tags, category, ollama_url, *, hardware=None, words=0
    ) -> dict[str, TimeRange]:
        self.asked.append(words)
        return {tag: self._times[tag] for tag in tags if tag in self._times}


def _source() -> CompareSource:
    """Return the one text these tests ask the models about."""
    return CompareSource(
        text=TEXT,
        plain=TEXT,
        text_format="plain",
        language="German",
        text_sha=text_identity(TEXT),
        words=9,
    )


def _candidate(model: str = "qwen3:8b", provider: str = "ollama") -> Candidate:
    """Return one model of the list, as the window hands it back to the service."""
    return Candidate(
        provider=provider,
        model=model,
        key=f"{model}-key",
        location=StageLocation(
            DataLocation.THIS_COMPUTER if provider == "ollama" else DataLocation.ONLINE,
            "Ollama" if provider == "ollama" else provider.title(),
        ),
        suitability=Suitability.GOOD,
    )


def _answer(text: str, *, names_hidden: int = 0, failed: bool = False) -> ProofreadOutcome:
    """Return a proofread answer as the unit gives one back."""
    return ProofreadOutcome(
        outcome=CorrectionOutcome(
            text=text,
            review=None,
            warning="Die Antwort ist unbrauchbar." if failed else None,
            failed=failed,
        ),
        text=text,
        review=None,
        warnings=(),
        stored_warning="",
        pieces=1,
        names_hidden=names_hidden,
        sent_text=text,
        sent_map={},
    )


class _Unit:
    """One proofreading: it records the settings it was built for and answers as told."""

    def __init__(self, owner: _UnitFor, config: Any) -> None:
        self._owner = owner
        self._config = config

    def __call__(self, text, text_format="plain", *, name="", stop_requested=None):
        self._owner.asked.append(self._config)
        self._owner.texts.append(text)
        if self._owner.ollama is not None:
            self._owner.ollama.loaded(self._config.correction_model)
        if self._owner.stop_before_answering:
            raise RunStoppedError()
        if self._owner.error is not None:
            raise self._owner.error
        return self._owner.answer


class _UnitFor:
    """The builder of the unit, and what the test wants that unit to do."""

    def __init__(
        self,
        *,
        answer: ProofreadOutcome | None = None,
        error: Exception | None = None,
        stop_before_answering: bool = False,
        ollama: FakeOllama | None = None,
    ) -> None:
        self.answer = answer if answer is not None else _answer(TEXT)
        self.error = error
        self.stop_before_answering = stop_before_answering
        self.ollama = ollama
        #: The settings every unit was built for, in order.
        self.asked: list[Any] = []
        #: The texts the units were asked about, in order.
        self.texts: list[str] = []
        #: The stop event every unit was built with, in order.
        self.stop_events: list[Any] = []

    def __call__(self, config, *, reporter, stop_event=None) -> _Unit:
        self.stop_events.append(stop_event)
        return _Unit(self, config)


def _view(
    *,
    raw_text: str = TEXT,
    corrected: str = "",
    config_hash: str = "",
    raw_text_format: str = "plain",
) -> DocumentStateView:
    """Return the document a comparison starts from, as the window reads it."""
    return DocumentStateView(
        source_id="/docs/Lena.txt",
        name="Lena.txt",
        preview_img="",
        raw_text=raw_text,
        correction_text=corrected,
        evaluation_text="",
        ocr_done=True,
        correction_done=bool(corrected),
        evaluation_done=False,
        raw_text_format=raw_text_format,
        machine_correction_text=corrected,
        correction_config_hash=config_hash,
    )


_RATINGS = [
    ModelRating(
        tag="gemma3:12b",
        task="general",
        model_uses=None,
        suitability=Suitability.BEST,
        memory_gb=7.5,
        note="",
    ),
    ModelRating(
        tag="qwen3:8b",
        task="general",
        model_uses=None,
        suitability=Suitability.GOOD,
        memory_gb=5.0,
        note="",
    ),
]


def _key(config) -> str:
    """Return the proofreading key a run with *config* would write on this computer."""
    return correction_key(config, _windows(config), make_key_facts())


def _parts(
    *,
    unit: Any,
    results: _Results | None = None,
    marks: _Marks | None = None,
    runs: _Runs | None = None,
    view: DocumentStateView | None = None,
    ollama: Any | None = None,
) -> ComparisonParts:
    """Return what the comparison is built from: the fakes, and one document the machine read."""
    return ComparisonParts(
        store=results if results is not None else _Results(),
        option_marks=marks if marks is not None else _Marks(),
        unit_for=unit,
        documents=lambda config, path: view if view is not None else _view(),
        key_of=_key,
        facts=make_key_facts(),
        run=runs if runs is not None else _Runs(),
        ollama=ollama if ollama is not None else MagicMock(),
    )


def _service(tmp_path: Path, **parts: Any) -> ModelComparisonService:
    """Return the comparison over the fakes."""
    return ModelComparisonService(_parts(**parts))


def _lister(
    results: _Results | None = None,
    *,
    ratings: list[ModelRating] | None = None,
    times: dict[str, TimeRange] | None = None,
    providers: _Providers | None = None,
    advice: _Advice | None = None,
    speeds: _Speeds | None = None,
) -> ComparisonCandidates:
    """Return the models to choose from: two installed ones and one online one."""
    return ComparisonCandidates(
        store=results if results is not None else _Results(),
        key_of=_key,
        advice=advice if advice is not None else _Advice(_RATINGS if ratings is None else ratings),
        providers=providers
        if providers is not None
        else _Providers(
            {"ollama": ["gemma3:12b", "qwen3:8b"], "mistral": ["mistral-large-latest"]}
        ),
        speeds=speeds if speeds is not None else _Speeds(times),
    )


def _config(tmp_path: Path):
    """Return the teacher's settings: an Ollama proofreading they already ran."""
    return make_config(
        tmp_path,
        language="German",
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gemma3:12b",
    )


def _windows(config) -> RunWindows:
    """Return the context windows of a run on this computer: the base ones, unprobed."""
    return RunWindows(proofreading=BASE_NUM_CTX, evaluation=BASE_NUM_CTX)


def _key_of(config, model: str, facts) -> str:
    """Return the proofreading key a run with *model* would write for these settings."""
    variant = replace(config, correction_provider="ollama", correction_model=model)
    return correction_key(variant, _windows(variant), facts)


def _keys(config) -> frozenset[str]:
    """Return the proofreading keys of the two models the window lists for these settings."""
    facts = make_key_facts()
    return frozenset(_key_of(config, model, facts) for model in ("gemma3:12b", "qwen3:8b"))


# ── the document ───────────────────────────────────────────────────────────


def test_the_source_is_the_transcription_and_its_own_proofreading(tmp_path: Path) -> None:
    """
    Given  a document the machine read and already proofread with one model
    When   the comparison reads its source
    Then   it holds the text a run would send, its words and identity, and that answer as a
           version of its own
    """
    service = _service(
        tmp_path,
        unit=_UnitFor(),
        view=_view(corrected="Der Hund ist laut! Er bellt die ganze Nacht.", config_hash="own"),
    )

    source = service.source(_config(tmp_path), Path("/docs/Lena.txt"))

    assert source is not None
    assert source.text == TEXT
    assert source.plain == TEXT
    assert source.language == "German"
    assert source.words == 9
    assert source.text_sha == text_identity(TEXT)
    assert source.stored is not None
    assert source.stored.key == "own"
    assert source.stored.text == "Der Hund ist laut! Er bellt die ganze Nacht."


def test_a_document_that_was_not_read_cannot_be_compared(tmp_path: Path) -> None:
    """
    Given  a document without a transcription
    When   the comparison reads its source
    Then   there is none, and nothing is asked of a model
    """
    service = _service(tmp_path, unit=_UnitFor(), view=_view(raw_text="   "))

    assert service.source(_config(tmp_path), Path("/docs/Lena.txt")) is None


# ── the models ─────────────────────────────────────────────────────────────


def test_the_models_are_the_installed_and_the_online_ones_with_their_times(
    tmp_path: Path,
) -> None:
    """
    Given  two installed writing models, one measured, and one online model whose key is held
    When   the models to choose from are asked for
    Then   each row carries its place, its rating, its estimate — the text's words, not a page —
           and the online one carries no time and no rating of this computer
    """
    config = _config(tmp_path)
    source = CompareSource(
        text=TEXT,
        plain=TEXT,
        text_format="plain",
        language="German",
        text_sha=text_identity(TEXT),
        words=500,
    )
    speeds = _Speeds({"gemma3:12b": TimeRange(60.0, 90.0)})
    found = _lister(speeds=speeds).candidates(config, source)
    rows = {candidate.model: candidate for candidate in found}

    assert set(rows) == {"gemma3:12b", "qwen3:8b", "mistral-large-latest"}
    assert rows["gemma3:12b"].estimated == TimeRange(60.0, 90.0)
    assert speeds.asked == [500]
    assert rows["qwen3:8b"].estimated is None
    assert rows["gemma3:12b"].location.location is DataLocation.THIS_COMPUTER
    assert rows["mistral-large-latest"].location.location is DataLocation.ONLINE
    assert rows["mistral-large-latest"].estimated is None
    assert rows["mistral-large-latest"].suitability is Suitability.UNKNOWN
    assert rows["gemma3:12b"].suitability is Suitability.BEST


def test_a_model_keeps_the_catalogs_rating_and_a_reading_model_is_left_out(
    tmp_path: Path,
) -> None:
    """
    Given  one installed model the catalog rates slow, very slow even, and one that only reads
    When   the models to choose from are asked for
    Then   the first row carries that rating as it is, and the reading model is no row at all
    """
    ratings = [
        ModelRating(
            tag="gemma3:12b",
            task="general",
            model_uses=None,
            suitability=Suitability.SLOW,
            memory_gb=7.5,
            note="",
            very_slow=True,
        ),
        ModelRating(
            tag="qwen3:8b",
            task="general",
            model_uses=None,
            suitability=Suitability.WRONG_KIND,
            memory_gb=5.0,
            note="",
        ),
    ]

    rows = {
        candidate.model: candidate
        for candidate in _lister(ratings=ratings).candidates(_config(tmp_path), _source())
    }

    assert set(rows) == {"gemma3:12b", "mistral-large-latest"}
    assert rows["gemma3:12b"].suitability is Suitability.SLOW
    assert rows["gemma3:12b"].very_slow is True


def test_expired_model_lists_are_asked_for_again_and_then_listed(tmp_path: Path) -> None:
    """
    Given  an app whose model lists have expired, an online provider with a key and one without
    When   the models are listed, then the lists are refreshed and the models listed again
    Then   at first there are none; the refresh asks the server, classifies its models and asks
           the provider with a key, and afterwards every model of them is listed
    """
    providers = _Providers(
        {
            "ollama": ["gemma3:12b", "qwen3:8b"],
            "mistral": ["mistral-large-latest"],
            "anthropic": ["claude-sonnet"],
        },
        held=set(),
        keyless={"anthropic"},
    )
    advice = _Advice(_RATINGS)
    lister = _lister(providers=providers, advice=advice)
    config = _config(tmp_path)

    assert lister.candidates(config, _source()) == ()

    lister.refresh(config)

    assert providers.asked == ["ollama", "mistral"]
    assert advice.classified == ["gemma3:12b", "qwen3:8b"]
    assert {candidate.model for candidate in lister.candidates(config, _source())} == {
        "gemma3:12b",
        "qwen3:8b",
        "mistral-large-latest",
    }


def test_the_documents_own_proofreading_counts_as_one_models_answer(tmp_path: Path) -> None:
    """
    Given  a document whose stored proofreading carries the key of the model the teacher uses
    When   the models to choose from are asked for
    Then   that model is done, so running it costs no request, and the other one is not
    """
    config = _config(tmp_path)
    facts = make_key_facts()
    own = _key_of(config, "qwen3:8b", facts)
    service = _service(
        tmp_path, unit=_UnitFor(), view=_view(corrected="Der Hund bellt.", config_hash=own)
    )
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None

    rows = {candidate.model: candidate for candidate in _lister().candidates(config, source)}

    assert rows["qwen3:8b"].done is True
    assert rows["gemma3:12b"].done is False


def test_a_stored_answer_of_those_settings_counts_as_done(tmp_path: Path) -> None:
    """
    Given  an answer an earlier comparison stored for one model's key
    When   the models to choose from are asked for
    Then   that model is done, and the answer needs no request
    """
    config = _config(tmp_path)
    key = _key_of(config, "gemma3:12b", make_key_facts())
    results = _Results()
    results.save(
        ComparedResult(
            text_sha=text_identity(TEXT),
            key=key,
            provider="ollama",
            model="gemma3:12b",
            language="German",
            text=TEXT,
            review=None,
            seconds=12.0,
            fell_back=False,
            left_out=0,
            warning="",
            sent=None,
            digest="",
            exchange="",
            app_version="0.0.0.dev0",
            created=0.0,
        )
    )
    service = _service(tmp_path, unit=_UnitFor(), results=results, view=_view())
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None

    rows = {candidate.model: candidate for candidate in _lister(results).candidates(config, source)}

    assert rows["gemma3:12b"].done is True
    assert rows["gemma3:12b"].own is False


def test_only_the_documents_own_proofreading_is_own(tmp_path: Path) -> None:
    """
    Given  a document proofread with one model, and a stored answer of another model
    When   the models to choose from are asked for
    Then   the model of the document's proofreading is own and done, the stored one is done only
    """
    config = _config(tmp_path)
    facts = make_key_facts()
    results = _Results()
    results.save(
        ComparedResult(
            text_sha=text_identity(TEXT),
            key=_key_of(config, "gemma3:12b", facts),
            provider="ollama",
            model="gemma3:12b",
            language="German",
            text=TEXT,
            review=None,
            seconds=1.0,
            fell_back=False,
            left_out=0,
            warning="",
            sent=None,
            digest="",
            exchange="",
            app_version="0.0.0.dev0",
            created=0.0,
        )
    )
    service = _service(
        tmp_path,
        unit=_UnitFor(),
        results=results,
        view=_view(corrected="Der Hund bellt.", config_hash=_key_of(config, "qwen3:8b", facts)),
    )
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None

    rows = {candidate.model: candidate for candidate in _lister(results).candidates(config, source)}

    assert (rows["qwen3:8b"].own, rows["qwen3:8b"].done) == (True, True)
    assert (rows["gemma3:12b"].own, rows["gemma3:12b"].done) == (False, True)


def test_a_proofreading_made_from_another_text_is_not_an_answer_of_the_model(
    tmp_path: Path,
) -> None:
    """
    Given  a document whose stored proofreading was made from another transcription, so the
           machine's own text is gone and only the teacher's text is left
    When   the comparison reads its source
    Then   there is no version of the document's own proofreading: the teacher's text is theirs
    """
    view = _view(corrected="Vom Lehrer geändert.", config_hash="own")
    view = replace(view, machine_correction_text="")
    service = _service(tmp_path, unit=_UnitFor(), view=view)

    source = service.source(_config(tmp_path), Path("/docs/Lena.txt"))

    assert source is not None
    assert source.stored is None


# ── asking one model ───────────────────────────────────────────────────────


def test_a_model_is_asked_with_the_teachers_settings_and_its_own(tmp_path: Path) -> None:
    """
    Given  the teacher's settings on one model, and a text
    When   another model is asked about it
    Then   the unit sees those settings with only the provider and the model changed, and the
           answer is stored under that model's key
    """
    config = _config(tmp_path)
    unit = _UnitFor(answer=_answer("Der Hund ist laut!"))
    results = _Results()
    service = _service(tmp_path, unit=unit, results=results)
    source = _source()
    candidate = _candidate()

    (ran,) = service.run(config, source, [candidate])

    asked = unit.asked[0]
    assert (asked.correction_provider, asked.correction_model) == ("ollama", "qwen3:8b")
    assert asked.correction_level == config.correction_level
    assert asked.explanation_language == config.explanation_language
    assert unit.texts == [TEXT]
    assert ran.stopped is False and ran.failure is None and ran.own is False
    stored = results.load(text_identity(TEXT), _key_of(config, "qwen3:8b", make_key_facts()))
    assert stored is not None
    assert stored.text == "Der Hund ist laut!"
    assert stored.model == "qwen3:8b"
    assert stored.sent is not None and stored.sent.provider == "ollama"


def test_the_model_it_loaded_is_unloaded_and_the_one_that_was_there_stays(
    tmp_path: Path,
) -> None:
    """
    Given  a server that already holds the teacher's own proofreading model
    When   another model answers, and the server loaded it for that answer
    Then   only that one is dropped out of memory, so the next model starts cold and the one in
           use stays
    """
    with FakeOllama() as fake:
        config = replace(_config(tmp_path), ollama_url=fake.url)
        fake.install("gemma3:12b")
        fake.install("qwen3:8b")
        fake.loaded("gemma3:12b")
        unit = _UnitFor(answer=_answer("Der Hund ist laut!"), ollama=fake)
        service = _service(tmp_path, unit=unit, ollama=OllamaServerService())
        source = _source()
        candidate = _candidate()
        service.run(config, source, [candidate])

        assert fake.unloaded == ["qwen3:8b"]
        assert [entry["name"] for entry in fake.ps()] == ["gemma3:12b"]


def test_a_model_pulled_since_the_last_answer_is_stored_with_its_new_digest(
    tmp_path: Path,
) -> None:
    """
    Given  a server the app asked a moment ago, and a model of it that was pulled again since
    When   the model answers, and the comparison stores the answer
    Then   the stored answer carries the digest the server reports now, not the one it had
    """
    results = _Results()
    with FakeOllama() as fake:
        config = replace(_config(tmp_path), ollama_url=fake.url)
        fake.install("qwen3:8b", digest="sha256:before")
        server = OllamaServerService()
        # What the app holds from a moment ago: other weights than the ones installed since.
        server.snapshot(fake.url)
        fake.install("qwen3:8b", digest="sha256:after")
        unit = _UnitFor(answer=_answer("Der Hund ist laut!"), ollama=fake)
        service = _service(tmp_path, unit=unit, results=results, ollama=server)

        service.run(config, _source(), [_candidate()])

    stored = results.load(text_identity(TEXT), _key_of(config, "qwen3:8b", make_key_facts()))
    assert stored is not None
    assert stored.digest == "sha256:after"


def test_a_stopped_model_stores_nothing(tmp_path: Path) -> None:
    """
    Given  a teacher who pressed Stop while a model was answering
    When   the comparison comes back from that answer
    Then   the attempt says stopped, nothing is stored, and the model it loaded is dropped anyway
    """
    results = _Results()
    with FakeOllama() as fake:
        config = replace(_config(tmp_path), ollama_url=fake.url)
        fake.install("qwen3:8b")
        # A model the server loaded for other work: it stays, as a run's own model does.
        fake.loaded("qwen3:8b")
        unit = _UnitFor(stop_before_answering=True, ollama=fake)
        service = _service(tmp_path, unit=unit, results=results, ollama=OllamaServerService())
        source = _source()
        candidate = _candidate()
        (ran,) = service.run(config, source, [candidate])

    assert ran.stopped is True
    assert ran.result is None and ran.failure is None
    assert results.saved == {}
    # It was in memory before the answer, so the comparison leaves it alone.
    assert [entry["name"] for entry in fake.ps()] == ["qwen3:8b"]


def test_a_model_that_failed_says_why_and_stores_nothing(tmp_path: Path) -> None:
    """
    Given  a model whose answer the app cannot use
    When   the comparison comes back from it
    Then   the row carries the reason, nothing is stored, and the log holds no text of the
           document
    """
    config = _config(tmp_path)
    results = _Results()
    unit = _UnitFor(answer=_answer(TEXT, failed=True))
    service = _service(tmp_path, unit=unit, results=results)
    source = _source()
    candidate = _candidate()

    (ran,) = service.run(config, source, [candidate])

    assert ran.result is None
    assert ran.failure is not None and ran.failure.key is MessageKey.MODEL_NOT_COMPARED
    assert results.saved == {}


def test_a_request_that_never_reached_the_model_is_a_rows_words(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """
    Given  a model whose request fails before the model answers
    When   the comparison comes back from it
    Then   the row says why, nothing is stored, and no log line holds the document's text
    """
    config = _config(tmp_path)
    results = _Results()
    failure = StageError("Der Server antwortet nicht.", kind=StageErrorKind.PROVIDER)
    service = _service(tmp_path, unit=_UnitFor(error=failure), results=results)
    source = _source()
    candidate = _candidate()

    with caplog.at_level(logging.DEBUG):
        (ran,) = service.run(config, source, [candidate])

    assert ran.failure is not None
    assert ran.failure.key is MessageKey.MODEL_NOT_COMPARED
    assert results.saved == {}
    assert "Der Hund" not in caplog.text


def test_a_comparison_refuses_while_a_run_is_going(tmp_path: Path) -> None:
    """
    Given  a run in progress in this process
    When   a model is asked about a text
    Then   the comparison raises, and no model is asked at all
    """
    config = _config(tmp_path)
    runs = _Runs()
    runs.going = 1
    unit = _UnitFor()
    service = _service(tmp_path, unit=unit, runs=runs)
    source = _source()
    candidate = _candidate()

    with pytest.raises(RunInProgressError):
        service.run(config, source, [candidate])

    assert unit.asked == []


def test_a_comparison_counts_as_one_run_from_its_first_model_to_its_last(tmp_path: Path) -> None:
    """
    Given  two models to ask
    When   the comparison asks them one after the other
    Then   it counts as a run between the two as well, so nothing deletes the stored results
           there, and no longer once it is over
    """
    runs = _Runs()
    service = _service(tmp_path, unit=_UnitFor(), runs=runs)
    between: list[int] = []

    attempts = service.run(
        _config(tmp_path),
        _source(),
        [_candidate("gemma3:12b"), _candidate("qwen3:8b")],
        hooks=JobHooks(on_answer=lambda _ran: between.append(runs.going)),
    )

    assert len(attempts) == 2
    assert between == [1, 1]
    assert runs.going == 0


def test_a_stop_between_two_models_asks_no_further_one(tmp_path: Path) -> None:
    """
    Given  two models to ask, and a teacher who presses Stop once the first one answered
    When   the comparison goes on
    Then   the second model is not asked
    """
    import threading  # noqa: PLC0415

    unit = _UnitFor()
    service = _service(tmp_path, unit=unit)
    event = threading.Event()

    attempts = service.run(
        _config(tmp_path),
        _source(),
        [_candidate("gemma3:12b"), _candidate("qwen3:8b")],
        hooks=JobHooks(stop_event=event, on_answer=lambda _ran: event.set()),
    )

    assert [ran.candidate.model for ran in attempts] == ["gemma3:12b"]
    assert len(unit.asked) == 1


def test_a_model_that_answered_already_is_not_asked_again(tmp_path: Path) -> None:
    """
    Given  a stored answer of one model about the text
    When   that model is asked again as a first answer, and then as a further run
    Then   the first time costs no request and gives the stored answer; the further run asks
    """
    config = _config(tmp_path)
    results = _Results()
    results.save(_result(config, "Der Hund bellt."))
    unit = _UnitFor(answer=_answer("Der Hund bellt laut."))
    service = _service(tmp_path, unit=unit, results=results)

    (first,) = service.run(config, _source(), [_candidate("gemma3:12b")])

    assert unit.asked == []
    assert first.result is not None and first.result.text == "Der Hund bellt."

    (again,) = service.run(config, _source(), [_candidate("gemma3:12b")], repeat=1)

    assert len(unit.asked) == 1
    assert again.result is not None and again.result.repeat == 1


def test_a_missing_provider_package_is_a_runtime_error(tmp_path: Path) -> None:
    """
    Given  the settings on a provider whose package is not installed
    When   the comparison asks that model, with the unit built as a run builds it
    Then   the missing package raises, as everywhere else in the app
    """
    config = replace(
        _config(tmp_path),
        correction_provider="anthropic",
        correction_model="claude-3-5-sonnet-latest",
        anonymization_enabled=False,
    )
    service = ModelComparisonService(
        ComparisonParts(
            store=_Results(),
            option_marks=_Marks(),
            unit_for=lambda config, *, reporter, stop_event=None: proofread_unit_for(
                config,
                # The factory a run gets: it builds the provider's own chat model, which is where
                # a package that is not installed shows up.
                factory=PipelineComponentFactory(),
                model_store=MagicMock(),
                reporter=reporter,
                samples=SampleStore(tmp_path / "samples.json"),
                stop_event=stop_event,
            ),
            documents=lambda config, path: _view(),
            key_of=_key,
            facts=make_key_facts(),
            run=_Runs(),
            ollama=MagicMock(),
        )
    )
    source = _source()
    candidate = _candidate("claude-3-5-sonnet-latest", "anthropic")

    with patch.dict(sys.modules, {"langchain_anthropic": None}):
        with pytest.raises(RuntimeError):
            service.run(config, source, [candidate])


def test_proofreading_switched_off_is_a_rows_words(tmp_path: Path) -> None:
    """
    Given  the teacher switched proofreading off, so there is no request to make
    When   a model is asked about a text
    Then   the row says there is nothing to compare, nothing is stored and nothing is asked
    """
    config = replace(_config(tmp_path), correction_enabled=False)
    results = _Results()
    service = ModelComparisonService(
        ComparisonParts(
            store=results,
            option_marks=_Marks(),
            # The unit as a run builds it, so the build itself is what refuses here.
            unit_for=lambda config, *, reporter, stop_event=None: proofread_unit_for(
                config,
                factory=PipelineComponentFactory(),
                model_store=MagicMock(),
                reporter=reporter,
                samples=SampleStore(tmp_path / "samples.json"),
                stop_event=stop_event,
            ),
            documents=lambda config, path: _view(),
            key_of=_key,
            facts=make_key_facts(),
            run=_Runs(),
            ollama=MagicMock(),
        )
    )

    (ran,) = service.run(config, _source(), [_candidate()])

    assert ran.result is None
    assert ran.failure is not None and ran.failure.key is MessageKey.MODEL_NOT_COMPARED
    assert ran.failure.args["reason"] == proofreading_not_compared()
    assert results.saved == {}


# ── the comparison itself ──────────────────────────────────────────────────


def test_the_comparison_shows_the_stored_answers_and_the_documents_own(tmp_path: Path) -> None:
    """
    Given  one stored answer about a text, and the document's own proofreading
    When   the comparison of that text is asked for
    Then   both are versions in it, the document's own being one of them
    """
    config = _config(tmp_path)
    results = _Results()
    results.save(
        ComparedResult(
            text_sha=text_identity(TEXT),
            key=_key_of(config, "gemma3:12b", make_key_facts()),
            provider="ollama",
            model="gemma3:12b",
            language="German",
            text="Der Hund ist laut!",
            review=None,
            seconds=20.0,
            fell_back=False,
            left_out=0,
            warning="",
            sent=None,
            digest="",
            exchange="",
            app_version="0.0.0.dev0",
            created=0.0,
        )
    )
    service = _service(
        tmp_path,
        unit=_UnitFor(),
        results=results,
        view=_view(corrected="Der Hund bellt.", config_hash="own-of-the-document"),
    )

    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None
    comparison = service.comparison(source, _keys(config))

    assert len(comparison.versions) == 2
    assert {version.key for version in comparison.versions} == {
        _key_of(config, "gemma3:12b", make_key_facts()),
        "own-of-the-document",
    }


def test_a_mark_is_written_and_the_figures_count_it(tmp_path: Path) -> None:
    """
    Given  a stored answer that fixes one word, and a teacher who calls that fix right
    When   they mark it
    Then   the verdict is stored for that text and the figures of that answer count it right
    """
    config = _config(tmp_path)
    marks = _Marks()
    results = _Results()
    results.save(_result(config, "Der Hund bellt."))
    service = _service(tmp_path, unit=_UnitFor(), results=results, marks=marks)
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None
    option = service.comparison(source, _keys(config)).sites[0].options[0]

    service.set_mark(source, option, Verdict.RIGHT)

    assert [mark.verdict for mark in service.marks(source)] == [Verdict.RIGHT]
    assert list(marks.saved) == [source.text_sha]
    figures = service.comparison(source, _keys(config)).versions[0]
    assert (figures.marked.right, figures.marked.wrong, figures.marked.unmarked) == (1, 0, 1)
    assert figures.marked.f_beta is None, "one change of that answer is still unmarked"


def test_a_mark_that_is_taken_back_leaves_nothing(tmp_path: Path) -> None:
    """
    Given  an option the teacher called right
    When   they take that verdict back
    Then   the text has no verdict left, and the figures count nothing as right
    """
    config = _config(tmp_path)
    marks = _Marks()
    results = _Results()
    results.save(_result(config, "Der Hund bellt."))
    service = _service(tmp_path, unit=_UnitFor(), results=results, marks=marks)
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None
    option = service.comparison(source, _keys(config)).sites[0].options[0]

    service.set_mark(source, option, Verdict.RIGHT)
    service.set_mark(source, option, None)

    assert service.marks(source) == ()
    assert service.comparison(source, _keys(config)).versions[0].marked.right == 0


def _result(config, text: str, *, repeat: int = 0) -> ComparedResult:
    """Return one stored answer of the model ``gemma3:12b`` about :data:`TEXT`."""
    return ComparedResult(
        text_sha=text_identity(TEXT),
        key=_key_of(config, "gemma3:12b", make_key_facts()),
        provider="ollama",
        model="gemma3:12b",
        language="German",
        text=text,
        review=None,
        seconds=20.0,
        fell_back=False,
        left_out=0,
        warning="",
        sent=None,
        digest="",
        exchange="",
        app_version="0.0.0.dev0",
        created=0.0,
        repeat=repeat,
    )


def test_two_answers_of_one_model_are_one_version_with_two_runs(tmp_path: Path) -> None:
    """
    Given  one model's answer, and the answer of the same model asked a second time
    When   the comparison is asked for
    Then   it is one column, with two runs, and the change both answers made is in both
    """
    config = _config(tmp_path)
    results = _Results()
    results.save(_result(config, "Der Hund bellt."))
    results.save(_result(config, "Der Hund bellt.", repeat=1))
    service = _service(tmp_path, unit=_UnitFor(), results=results)
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None

    comparison = service.comparison(source, _keys(config))

    assert len(comparison.versions) == 1
    figures = comparison.versions[0]
    assert (figures.runs, figures.changes, figures.in_every_run, figures.once) == (2, 2, 2, 0)
    assert service.next_repeat(source, _lister(results).candidates(config, source)[0]) == 2


def test_the_next_run_follows_the_highest_stored_one(tmp_path: Path) -> None:
    """
    Given  a model's first answer and its third, the second one gone
    When   the number of its next run is asked for
    Then   it follows the third, so no stored answer is replaced
    """
    config = _config(tmp_path)
    results = _Results()
    results.save(_result(config, "Der Hund bellt."))
    results.save(_result(config, "Der Hund bellt.", repeat=2))
    service = _service(tmp_path, unit=_UnitFor(), results=results)
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None

    assert service.next_repeat(source, _lister(results).candidates(config, source)[0]) == 3


def test_the_stop_event_reaches_the_unit_that_is_built(tmp_path: Path) -> None:
    """
    Given  a teacher's stop event
    When   a model is asked about a text with it
    Then   the unit is built with that event, so Stop can end the request that is waiting
    """
    import threading  # noqa: PLC0415

    unit = _UnitFor()
    service = _service(tmp_path, unit=unit)
    event = threading.Event()

    service.run(_config(tmp_path), _source(), [_candidate()], hooks=JobHooks(stop_event=event))

    assert unit.stop_events == [event]


def test_a_model_is_described_by_the_data_flow_of_its_proofreading_alone(tmp_path: Path) -> None:
    """
    Given  the teacher's settings, which read pages and review content too
    When   the settings that describe where one online model is asked are requested
    Then   they hold that provider and model with proofreading on, and recognition and the
           content review off: only the text of the proofreading goes there
    """
    config = replace(_config(tmp_path), recognition_enabled=True, evaluation_enabled=True)
    service = _service(tmp_path, unit=_UnitFor())

    described = service.data_flow_config(config, _candidate("mistral-large-latest", "mistral"))

    assert (described.correction_provider, described.correction_model) == (
        "mistral",
        "mistral-large-latest",
    )
    assert described.correction_enabled is True
    assert described.recognition_enabled is False
    assert described.evaluation_enabled is False


def test_a_stop_event_that_is_set_ends_the_request_of_a_local_model(tmp_path: Path) -> None:
    """
    Given  a unit built as a run builds it, with the teacher's stop event, which is set
    When   the unit is called without asking the event itself
    Then   the model's request is abandoned and raises RunStoppedError, as a run's does
    """
    import threading  # noqa: PLC0415

    event = threading.Event()
    event.set()
    with FakeOllama() as fake:
        fake.install("qwen3:8b")
        config = replace(
            _config(tmp_path),
            ollama_url=fake.url,
            correction_model="qwen3:8b",
            anonymization_enabled=False,
        )
        unit = proofread_unit_for(
            config,
            factory=PipelineComponentFactory(),
            model_store=MagicMock(),
            reporter=MagicMock(),
            samples=SampleStore(tmp_path / "samples.json"),
            stop_event=event,
        )

        with pytest.raises(RunStoppedError):
            unit(TEXT)


def test_an_answer_made_with_other_settings_is_not_a_column(tmp_path: Path) -> None:
    """
    Given  a stored answer of a model made with settings that are not the ones the window lists
           now, and one made with the listed ones
    When   the comparison is asked for with the listed keys
    Then   only the answer of the listed settings is a version; the other stays stored
    """
    config = _config(tmp_path)
    results = _Results()
    results.save(_result(config, "Der Hund bellt."))
    other = replace(_result(config, "Der Hund bellt laut."), key="made-with-other-settings")
    results.save(other)
    service = _service(tmp_path, unit=_UnitFor(), results=results)
    source = service.source(config, Path("/docs/Lena.txt"))
    assert source is not None

    comparison = service.comparison(source, _keys(config))

    assert [version.key for version in comparison.versions] == [
        _key_of(config, "gemma3:12b", make_key_facts())
    ]
    assert "made-with-other-settings" in {stem for _, stem in results.saved}
