"""Unit tests for teachers_teammate.infrastructure.stage_builder wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.domain.hardware_profile import (
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
)
from teachers_teammate.domain.providers import ProviderInfo
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.exceptions import OnlineOcrNotAllowedError
from teachers_teammate.infrastructure.preprocessing.steps import StepPipeline
from teachers_teammate.infrastructure.stage_builder import (
    PipelineComponentFactory,
    StageBuilder,
)
from tests.conftest import fake_reader_store, make_config


def _stage_builder(tmp_path: Path, *args: Any, **kwargs: Any) -> StageBuilder:
    """A stage builder whose model folder stays inside the test, unless the test names one."""
    kwargs.setdefault("model_store", ModelStore(tmp_path / "models"))
    return StageBuilder(*args, **kwargs)


@pytest.mark.use_case("Batch_OCR_Processing")
@pytest.mark.parametrize(
    ("method", "first_step"),
    [(PreprocessMethod.AUTO, "measure"), (PreprocessMethod.NONE, None)],
)
def test_build_preprocessor_builds_the_steps_of_the_chosen_method(
    tmp_path: Path, method: PreprocessMethod, first_step: str | None
) -> None:
    """
    Given  a Config whose OcrConfig chooses the automatic preparation, or "Original page"
    When   StageBuilder.build_preprocessor() constructs the preprocessor
    Then   the preprocessor is a step pipeline of the automatic steps, or of none at all
    """
    config = make_config(tmp_path, ocr=OcrConfig(engine="ppocr", preprocess_method=method))
    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    pre = builder.build_preprocessor(tmp_path)

    assert isinstance(pre, StepPipeline)
    assert [step.name for step in pre._steps][:1] == ([first_step] if first_step else [])


@pytest.mark.use_case("Batch_OCR_Processing")
def test_build_preprocessor_gives_the_run_s_model_store_to_the_factory(tmp_path: Path) -> None:
    """
    Given  a StageBuilder with a model store and a factory with its own preprocessor builder
    When   build_preprocessor() is called
    Then   the builder receives the output folder, the debug flag, the OcrConfig and the store
    """
    store = ModelStore(tmp_path / "models")
    build = MagicMock()
    config = make_config(tmp_path)
    builder = _stage_builder(
        tmp_path,
        config=config,
        factory=PipelineComponentFactory(build_preprocessor=build),
        model_store=store,
    )

    builder.build_preprocessor(tmp_path)

    build.assert_called_once_with(
        tmp_dir=tmp_path, save_steps=config.debug, ocr=config.ocr, store=store
    )


@pytest.mark.use_case("Batch_OCR_Processing")
def test_build_preprocessor_service_uses_configured_pdf_dpi(tmp_path: Path) -> None:
    """
    Given  a Config with a non-default OCR pdf_render_dpi
    When   the preprocessor service builds a PDF input provider
    Then   that provider is constructed with the configured DPI
    """
    from teachers_teammate.infrastructure.input_providers import (  # noqa: PLC0415
        PdfInputProvider,
    )

    config = make_config(tmp_path, ocr=OcrConfig(engine="ppocr", pdf_render_dpi=150))
    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    service = builder.build_preprocessor_service(tmp_path)
    provider = service._provider_factory(".pdf", tmp_path)

    assert isinstance(provider, PdfInputProvider)
    assert provider._pdf_dpi == 150


@pytest.mark.use_case("Batch_OCR_Processing")
def test_build_preprocessor_service_passes_configured_pdf_text_layer(
    tmp_path: Path,
) -> None:
    """
    Given  a Config with pdf_text_layer="always"
    When   the preprocessor service is built
    Then   the PDF provider uses the configured mode
    """
    config = make_config(tmp_path, ocr=OcrConfig(engine="ppocr", pdf_text_layer="always"))
    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    provider = builder.build_preprocessor_service(tmp_path)._provider_factory(".pdf", tmp_path)

    assert provider._text_layer == "always"


def test_build_ocr_postprocessor_uses_factory_callable(tmp_path: Path) -> None:
    """
    Given  a PipelineComponentFactory whose build_ocr_postprocessor is overridden
    When   StageBuilder.build_ocr_postprocessor() is called
    Then   the injected instance is returned (so adapters/tests can swap the notation stage)
    """
    import dataclasses  # noqa: PLC0415

    sentinel = object()
    factory = dataclasses.replace(
        PipelineComponentFactory(), build_ocr_postprocessor=lambda: sentinel
    )
    builder = _stage_builder(tmp_path, make_config(tmp_path), factory)

    assert builder.build_ocr_postprocessor() is sentinel


def test_default_ocr_postprocessor_understands_layout_html(tmp_path: Path) -> None:
    """
    Given  the default factory
    When   its postprocessor processes Chandra-style HTML
    Then   Markdown is produced
    """
    builder = _stage_builder(tmp_path, make_config(tmp_path), PipelineComponentFactory())

    result = builder.build_ocr_postprocessor().process_pages(
        ['<div data-label="Text"><p><b>x</b></p></div>']
    )

    assert (result.text, result.text_format) == ("**x**", "markdown")


@pytest.mark.parametrize("engine", ["langchain", "openai"])
def test_build_ocr_refuses_online_engines(tmp_path: Path, engine: str) -> None:
    """
    Given  a Config whose OCR engine is an online vision model
    When   StageBuilder.build_ocr() is called
    Then   it raises OnlineOcrNotAllowedError before any LLM is built
    """
    build_llm = MagicMock()
    factory = PipelineComponentFactory(build_llm=build_llm)
    config = make_config(tmp_path, ocr=OcrConfig(engine=engine, model="gpt-4o"))
    builder = _stage_builder(tmp_path, config=config, factory=factory)

    with pytest.raises(OnlineOcrNotAllowedError, match="whole page"):
        builder.build_ocr()

    build_llm.assert_not_called()


@pytest.mark.parametrize(
    ("model", "url"),
    [
        ("qwen3-vl:235b-cloud", "http://127.0.0.1:11434"),
        ("deepseek-ocr:latest", "https://ollama.com"),
    ],
)
def test_build_ocr_refuses_ollama_cloud(tmp_path: Path, model: str, url: str) -> None:
    """
    Given  Ollama OCR with a cloud model, or pointed at Ollama's hosted API
    When   StageBuilder.build_ocr() is called
    Then   it raises OnlineOcrNotAllowedError and builds no OCR processor
    """
    build_ollama_ocr = MagicMock()
    factory = PipelineComponentFactory(build_ollama_ocr=build_ollama_ocr)
    config = make_config(tmp_path, ocr=OcrConfig(engine="ollama", model=model), ollama_url=url)
    builder = _stage_builder(tmp_path, config=config, factory=factory)

    with pytest.raises(OnlineOcrNotAllowedError, match="Ollama's servers"):
        builder.build_ocr(MagicMock())

    build_ollama_ocr.assert_not_called()


@pytest.mark.parametrize(
    "url", ["http://203.0.113.7:11434", "https://ollama.example.com", "ollama.example.com:11434"]
)
def test_build_ocr_refuses_ollama_on_the_internet(tmp_path: Path, url: str) -> None:
    """
    Given  Ollama OCR with a downloaded model, pointed at a server on the internet
    When   StageBuilder.build_ocr() is called
    Then   it raises OnlineOcrNotAllowedError naming the internet and builds no OCR processor
    """
    build_ollama_ocr = MagicMock()
    factory = PipelineComponentFactory(build_ollama_ocr=build_ollama_ocr)
    config = make_config(
        tmp_path, ocr=OcrConfig(engine="ollama", model="deepseek-ocr:latest"), ollama_url=url
    )

    with pytest.raises(OnlineOcrNotAllowedError, match="over the internet"):
        _stage_builder(tmp_path, config=config, factory=factory).build_ocr(MagicMock())

    build_ollama_ocr.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://school-server:11434",
        "http://nas.fritz.box:11434",
        "http://192.168.1.5:11434",
        "http://[fd00::1]:11434",
    ],
)
def test_build_ocr_allows_downloaded_ollama_model_on_another_computer(
    tmp_path: Path, url: str
) -> None:
    """
    Given  Ollama OCR with a downloaded model on a server in the school's own network
    When   StageBuilder.build_ocr() is called
    Then   the Ollama OCR processor is built (a server you run is allowed)
    """
    build_ollama_ocr = MagicMock()
    factory = PipelineComponentFactory(build_ollama_ocr=build_ollama_ocr)
    config = make_config(
        tmp_path, ocr=OcrConfig(engine="ollama", model="deepseek-ocr:latest"), ollama_url=url
    )

    _stage_builder(tmp_path, config=config, factory=factory).build_ocr(MagicMock())

    build_ollama_ocr.assert_called_once()


@pytest.mark.parametrize(
    ("provider", "url", "online"),
    [
        ("ollama", "http://127.0.0.1:11434", False),
        ("ollama", "http://192.168.1.5:11434", False),
        ("ollama", "http://nas.fritz.box:11434", False),
        ("ollama", "http://203.0.113.7:11434", True),
        ("ollama", "https://ollama.example.com", True),
        ("openai", "http://127.0.0.1:11434", True),
    ],
)
def test_proofreading_is_online_by_where_its_server_is(
    tmp_path: Path, provider: str, url: str, online: bool
) -> None:
    """
    Given  proofreading with a provider and an Ollama address
    When   the builder asks whether proofreading is online (it decides the tries of a cut-off
           answer)
    Then   an Ollama server on this computer or the school's network is not, one on the
           internet is, and a vendor's service always is, as the privacy badge says
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider=provider,
        correction_model="m",
        ollama_url=url,
    )

    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    assert builder._correction_is_online() is online


def test_build_ocr_builds_the_built_in_reader_with_the_stop_event(tmp_path: Path) -> None:
    """
    Given  the built-in reader chosen and a run's stop event
    When   StageBuilder.build_ocr() is called
    Then   the factory builds the reader with a model store and that event, and no Ollama
           processor is built
    """
    import threading  # noqa: PLC0415

    reader = MagicMock()
    build_ppocr = MagicMock(return_value=reader)
    build_ollama_ocr = MagicMock()
    factory = PipelineComponentFactory(
        build_ppocr_ocr=build_ppocr, build_ollama_ocr=build_ollama_ocr
    )
    store = ModelStore(tmp_path / "models")
    builder = _stage_builder(
        tmp_path,
        config=make_config(tmp_path, ocr_engine="ppocr"),
        factory=factory,
        model_store=store,
    )
    stop = threading.Event()

    assert builder.build_ocr(None, stop) is reader

    assert build_ppocr.call_args.kwargs["store"] is store
    assert build_ppocr.call_args.kwargs["stop_event"] is stop
    build_ollama_ocr.assert_not_called()


def test_the_default_reader_builder_constructs_the_processor(tmp_path: Path) -> None:
    """
    Given  the default factory and the built-in reader chosen
    When   StageBuilder.build_ocr() is called (the processor class patched at the import site)
    Then   PpOcrProcessor is constructed with the store and the stop event
    """
    with patch("teachers_teammate.infrastructure.stage_builder.PpOcrProcessor") as processor:
        _stage_builder(
            tmp_path,
            config=make_config(tmp_path, ocr_engine="ppocr"),
            factory=PipelineComponentFactory(),
        ).build_ocr()

    processor.assert_called_once()
    assert processor.call_args.kwargs == {"stop_event": None}


def test_the_built_in_reader_is_not_built_alongside_itself(tmp_path: Path) -> None:
    """
    Given  the built-in reader chosen, and a store that holds its models
    When   StageBuilder.build_scan_places() is called
    Then   the places are kept with no reader alongside: the run's own lines are the places
    """
    build_ppocr = MagicMock()
    builder = _stage_builder(
        tmp_path,
        config=make_config(tmp_path, ocr_engine="ppocr"),
        factory=PipelineComponentFactory(build_ppocr_ocr=build_ppocr),
        model_store=fake_reader_store(tmp_path),
    )

    places = builder.build_scan_places()

    assert places is not None
    assert places._alongside is None
    build_ppocr.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        {"ocr_engine": "ppocr", "scan_marks": False},
        {"ocr_engine": "ollama", "scan_marks": False},
        {"ocr_engine": "ppocr", "recognition_enabled": False},
    ],
)
def test_no_places_when_the_run_keeps_none(tmp_path: Path, overrides: dict) -> None:
    """
    Given  the scan's marks switched off, or a run that reads no page
    When   StageBuilder.build_scan_places() is called
    Then   it returns None and builds no reader
    """
    build_ppocr = MagicMock()
    builder = _stage_builder(
        tmp_path,
        config=make_config(tmp_path, **overrides),
        factory=PipelineComponentFactory(build_ppocr_ocr=build_ppocr),
        model_store=fake_reader_store(tmp_path),
    )

    assert builder.build_scan_places() is None
    build_ppocr.assert_not_called()


def test_no_reader_alongside_an_online_reader_when_its_models_are_absent(
    tmp_path: Path,
) -> None:
    """
    Given  an Ollama reader and a store without the built-in reader's models
    When   StageBuilder.build_scan_places() is called
    Then   it returns None and builds nothing: no mark, no message, and nothing downloaded
    """
    build_ppocr = MagicMock()
    builder = _stage_builder(
        tmp_path,
        config=make_config(tmp_path, ocr_engine="ollama"),
        factory=PipelineComponentFactory(build_ppocr_ocr=build_ppocr),
        model_store=fake_reader_store(tmp_path, installed=False),
    )

    assert builder.build_scan_places() is None

    build_ppocr.assert_not_called()


def test_the_built_in_reader_reads_alongside_an_online_reader(tmp_path: Path) -> None:
    """
    Given  an Ollama reader and a store that holds the built-in reader's models
    When   StageBuilder.build_scan_places() is called with a stop event
    Then   the factory builds the built-in reader with the store and that event, to read alongside
    """
    import threading  # noqa: PLC0415

    reader = MagicMock()
    build_ppocr = MagicMock(return_value=reader)
    store = fake_reader_store(tmp_path)
    builder = _stage_builder(
        tmp_path,
        config=make_config(tmp_path, ocr_engine="ollama"),
        factory=PipelineComponentFactory(build_ppocr_ocr=build_ppocr),
        model_store=store,
    )
    stop = threading.Event()

    places = builder.build_scan_places(stop)

    assert places is not None
    assert places._alongside is reader
    assert build_ppocr.call_args.kwargs["store"] is store
    assert build_ppocr.call_args.kwargs["stop_event"] is stop


def test_build_text_anonymizer_only_when_enabled(tmp_path: Path) -> None:
    """
    Given  anonymized text files off, then on
    When   build_text_anonymizer() is called
    Then   it returns None when off, and builds an anonymizer from the factory when on
    """
    anonymizer = MagicMock()
    build_anonymizer = MagicMock(return_value=anonymizer)
    factory = PipelineComponentFactory(build_anonymizer=build_anonymizer)

    off = _stage_builder(tmp_path, config=make_config(tmp_path), factory=factory)
    on = _stage_builder(
        tmp_path, config=make_config(tmp_path, text_anonymized_enabled=True), factory=factory
    )

    assert off.build_text_anonymizer() is None
    assert on.build_text_anonymizer() is anonymizer
    build_anonymizer.assert_called_once()


def test_build_text_anonymizer_reuses_shared_anonymizer(tmp_path: Path) -> None:
    """
    Given  anonymized text files on and an anonymizer already built for proofreading
    When   build_text_anonymizer(shared) is called
    Then   the shared instance is returned and no second one is built
    """
    build_anonymizer = MagicMock()
    factory = PipelineComponentFactory(build_anonymizer=build_anonymizer)
    shared = MagicMock()
    builder = _stage_builder(
        tmp_path, config=make_config(tmp_path, text_anonymized_enabled=True), factory=factory
    )

    assert builder.build_text_anonymizer(shared) is shared
    build_anonymizer.assert_not_called()


def test_anonymizer_receives_models_and_current_default_patterns(tmp_path: Path) -> None:
    """
    Given  a config with primary and secondary models and the earlier IBAN / EMAIL / PHONE list
    When   the stage anonymizer and the anonymized-text anonymizer are built
    Then   both get the models and the current default patterns instead of the old list
    """
    from teachers_teammate.domain.anonymization import (  # noqa: PLC0415
        _LEGACY_DEFAULT_PATTERNS,
        DEFAULT_PATTERNS,
    )

    build_anonymizer = MagicMock()
    factory = PipelineComponentFactory(build_anonymizer=build_anonymizer)
    config = make_config(
        tmp_path,
        text_anonymized_enabled=True,
        anonymizer_primary_model="de_core_news_sm",
        anonymizer_secondary_model="xx_ent_wiki_sm",
        anonymizer_patterns=list(_LEGACY_DEFAULT_PATTERNS),
    )

    _stage_builder(tmp_path, config=config, factory=factory).build_text_anonymizer()

    anon_config = build_anonymizer.call_args.kwargs["config"]
    assert build_anonymizer.call_args.kwargs["language"] == config.language
    assert anon_config.primary_model == "de_core_news_sm"
    assert anon_config.secondary_model == "xx_ent_wiki_sm"
    assert anon_config.patterns == tuple(DEFAULT_PATTERNS)


def test_anonymizer_keeps_custom_and_empty_pattern_lists(tmp_path: Path) -> None:
    """
    Given  a config with a custom pattern list, and one with an empty list
    When   the anonymizer is built for each
    Then   the custom list is kept and the empty list stays empty (no regex anonymization)
    """
    build_anonymizer = MagicMock()
    factory = PipelineComponentFactory(build_anonymizer=build_anonymizer)

    for patterns in ([("ID", r"\d+")], []):
        config = make_config(tmp_path, text_anonymized_enabled=True, anonymizer_patterns=patterns)
        _stage_builder(tmp_path, config=config, factory=factory).build_text_anonymizer()
        assert build_anonymizer.call_args.kwargs["config"].patterns == tuple(patterns)


def test_correction_settings_carry_what_the_teacher_set(tmp_path: Path) -> None:
    """
    Given  a configuration with a level, an explanation language, notes and corrections only
    When   the stage builder collects the corrector settings
    Then   all four reach the settings the exchange is built with
    """
    config = make_config(
        tmp_path,
        correction_level="B2",
        explanation_language="German",
        correction_notes="Year 9, second foreign language.",
        correction_output="text",
    )
    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    settings = builder._correction_settings()

    assert settings.level == "B2"
    assert settings.explanation_language == "German"
    assert settings.teacher_notes == "Year 9, second foreign language."
    assert settings.corrections_only is True


def test_correction_settings_ask_for_the_review_by_default(tmp_path: Path) -> None:
    """
    Given  a configuration that does not ask for corrections only
    When   the stage builder collects the corrector settings
    Then   the exchange runs all three steps
    """
    config = make_config(tmp_path, correction_output="review")
    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    assert builder._correction_settings().corrections_only is False


def test_anonymizer_is_built_when_only_anonymization_is_on(tmp_path: Path) -> None:
    """
    Given  a config with "Remove personal info" on and proofreading off
    When   build_correction_and_anonymizer() is called
    Then   the anonymizer is built anyway and no warning says it needs proofreading
    """
    anonymizer = MagicMock()
    factory = PipelineComponentFactory(build_anonymizer=MagicMock(return_value=anonymizer))
    config = make_config(
        tmp_path,
        correction_enabled=False,
        anonymization_enabled=True,
        text_anonymized_enabled=False,
    )

    correction, built = _stage_builder(
        tmp_path, config=config, factory=factory
    ).build_correction_and_anonymizer()

    assert correction is None
    assert built is anonymizer


def test_anonymizer_is_not_built_without_anonymization(tmp_path: Path) -> None:
    """
    Given  a config with anonymization off
    When   build_correction_and_anonymizer() is called
    Then   no anonymizer is built at all
    """
    build_anonymizer = MagicMock()
    factory = PipelineComponentFactory(build_anonymizer=build_anonymizer)
    config = make_config(tmp_path, correction_enabled=False, anonymization_enabled=False)

    _, built = _stage_builder(
        tmp_path, config=config, factory=factory
    ).build_correction_and_anonymizer()

    assert built is None
    build_anonymizer.assert_not_called()


def _failing_anonymizer_factory() -> PipelineComponentFactory:
    """A factory whose anonymizer cannot start (spaCy or its model is missing)."""
    return PipelineComponentFactory(
        build_anonymizer=MagicMock(side_effect=ImportError("No module named 'spacy'")),
        build_llm=MagicMock(),
        build_corrector=MagicMock(),
    )


def test_missing_spacy_without_proofreading_warns_and_continues(tmp_path: Path) -> None:
    """
    Given  "Remove personal info" on, proofreading off and spaCy missing
    When   build_correction_and_anonymizer() is called
    Then   no error is raised (nothing is sent anywhere) and a warning says names stay
    """
    reporter = MagicMock()
    config = make_config(tmp_path, correction_enabled=False, anonymization_enabled=True)

    correction, built = _stage_builder(
        tmp_path, config=config, factory=_failing_anonymizer_factory(), reporter=reporter
    ).build_correction_and_anonymizer()

    assert correction is None
    assert built is None
    assert "No module named 'spacy'" in reporter.warn.call_args.args[0]


def test_missing_spacy_with_proofreading_stops_the_run(tmp_path: Path) -> None:
    """
    Given  "Remove personal info" on, proofreading on and spaCy missing
    When   build_correction_and_anonymizer() is called
    Then   it raises, because the text would be sent with its names
    """
    from teachers_teammate.exceptions import ProviderNotAvailableError  # noqa: PLC0415

    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        anonymization_enabled=True,
    )

    with pytest.raises(ProviderNotAvailableError):
        _stage_builder(
            tmp_path, config=config, factory=_failing_anonymizer_factory()
        ).build_correction_and_anonymizer()


def _hardware() -> HardwareDetection:
    """A computer with a 16 GB graphics card (13.6 GB usable) and 32 GB of main memory."""
    return HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        memory_gb=12.0,
        system_memory_gb=32.0,
    )


def test_the_resolved_window_reaches_the_correction_request(tmp_path: Path) -> None:
    """
    Given  a computer whose graphics card is known and an Ollama writing model
    When   the correction stage is built
    Then   the port the exchange asks carries the resolved proofreading window
    """
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )
    from teachers_teammate.infrastructure.proofreading.port import OllamaPort  # noqa: PLC0415

    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3:4b",
    )
    builder = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory(), hardware=_hardware()
    )

    windows = builder.resolved_windows()
    correction = builder.build_corrector(MagicMock())

    assert isinstance(correction, ChunkingCorrector)
    port = correction.inner.exchange._port
    assert isinstance(port, OllamaPort)
    budget = port.window()
    assert budget is not None
    assert budget.num_ctx == windows.proofreading
    assert windows.proofreading >= 8192


def test_the_resolved_window_reaches_the_evaluator(tmp_path: Path) -> None:
    """
    Given  the same computer and an enabled content review that uses another model
    When   the evaluation stage is built
    Then   the port the review asks carries the resolved evaluation window
    """
    from teachers_teammate.infrastructure.proofreading.port import OllamaPort  # noqa: PLC0415

    config = make_config(
        tmp_path,
        correction_provider="ollama",
        correction_model="qwen3:4b",
        correction_enabled=True,
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="gemma4:12b",
    )
    builder = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory(), hardware=_hardware()
    )

    evaluator = builder.build_evaluation(MagicMock(), MagicMock())

    windows = builder.resolved_windows()
    port = evaluator._port
    assert isinstance(port, OllamaPort)
    budget = port.window()
    assert budget is not None
    assert budget.num_ctx == windows.evaluation


def test_without_hardware_the_window_stays_at_the_base_step(tmp_path: Path) -> None:
    """
    Given  a builder that was given no hardware (a preview run, or a test double)
    When   the run windows are resolved
    Then   both stages keep the base window instead of guessing one
    """
    config = make_config(tmp_path, correction_provider="ollama")

    windows = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory()
    ).resolved_windows()

    assert windows.proofreading == windows.evaluation == 8192


def test_an_online_provider_keeps_the_base_window(tmp_path: Path) -> None:
    """
    Given  a correction provider and a content-review provider that bring their own window
    When   the run windows are resolved with hardware at hand
    Then   the app still reports the base window and never pretends to set one
    """
    config = make_config(
        tmp_path,
        correction_provider="openai",
        correction_model="gpt-4o",
        evaluate_provider="openai",
        evaluate_model="gpt-4o",
    )

    windows = _stage_builder(
        tmp_path,
        config=config,
        factory=PipelineComponentFactory(),
        hardware=_hardware(),
    ).resolved_windows()

    assert windows.proofreading == windows.evaluation == 8192


def _measured_specs(monkeypatch) -> None:
    """Make the catalog know a small unmeasured and a larger measured model."""
    from teachers_teammate.domain.model_catalog import OllamaModelSpec  # noqa: PLC0415

    specs = {
        "small:1b": OllamaModelSpec(
            tag="small:1b", category="general", memory_gb=1.0, download_gb=1.0, note=""
        ),
        "large:9b": OllamaModelSpec(
            tag="large:9b",
            category="general",
            memory_gb=2.0,
            download_gb=2.0,
            note="",
            context_gb_per_8k=0.1,
        ),
    }
    monkeypatch.setattr(
        "teachers_teammate.domain.context_budget.find_spec", lambda tag: specs.get(tag)
    )


def test_content_review_is_sized_from_its_own_model(tmp_path: Path, monkeypatch) -> None:
    """
    Given  proofreading on a model with unmeasured context growth and a content review on a
           larger model with measured growth, both on Ollama, on a big card
    When   the run windows are resolved
    Then   proofreading keeps the base window and content review gets its own larger window
    """
    _measured_specs(monkeypatch)
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="small:1b",
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="large:9b",
    )

    windows = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory(), hardware=_hardware()
    ).resolved_windows()

    assert windows.proofreading == 8192
    assert windows.evaluation > windows.proofreading


def test_the_window_setting_reaches_a_local_review_behind_an_online_proofreader(
    tmp_path: Path,
) -> None:
    """
    Given  proofreading with an online provider, a content review on Ollama and a pinned window
    When   the run windows are resolved
    Then   the review gets the pinned window, and the online proofreader reports the base one
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        correction_model="gpt-4o",
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="qwen3:4b",
        proofreading_context="32768",
    )

    windows = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory()
    ).resolved_windows()

    assert windows.proofreading == 8192
    assert windows.evaluation == 32768


def test_a_local_corrector_runs_the_exchange_on_the_local_server(tmp_path: Path) -> None:
    """
    Given  a config with an Ollama proofreading provider
    When   the correction stage is built
    Then   the exchange runs on an Ollama port with the run's window and the OCR timeout, and
           the chunking decorator wraps it so a long text goes out in pieces
    """
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        ExchangeCorrector,
    )
    from teachers_teammate.infrastructure.proofreading.port import OllamaPort  # noqa: PLC0415

    config = make_config(
        tmp_path, correction_enabled=True, correction_provider="ollama", ocr_timeout=90
    )
    builder = _stage_builder(tmp_path, config=config, factory=PipelineComponentFactory())

    correction, _ = builder.build_correction_and_anonymizer(MagicMock())

    assert isinstance(correction, ChunkingCorrector)
    assert isinstance(correction.inner, ExchangeCorrector)
    port = correction.inner.exchange._port
    assert isinstance(port, OllamaPort)
    assert port.window().num_ctx == builder.resolved_windows().proofreading
    assert port._timeout == 90.0


def test_an_online_corrector_runs_the_exchange_on_the_provider(tmp_path: Path) -> None:
    """
    Given  a config with an online proofreading provider, whose window fits any text
    When   the correction stage is built
    Then   the exchange runs unwrapped on a LangChain port with the provider's strict schema
    """
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        ExchangeCorrector,
    )
    from teachers_teammate.infrastructure.proofreading.port import LangChainPort  # noqa: PLC0415

    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="openai",
        correction_model="gpt-4o-mini",
    )

    correction, _ = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory(build_llm=MagicMock())
    ).build_correction_and_anonymizer()

    assert isinstance(correction, ExchangeCorrector)
    assert not isinstance(correction, ChunkingCorrector)
    port = correction.exchange._port
    assert isinstance(port, LangChainPort)
    assert port._strict is True


def test_a_provider_without_a_strict_schema_is_asked_without_one(tmp_path: Path) -> None:
    """
    Given  a config with an online provider that has no strict structured output
    When   the correction stage is built
    Then   the LangChain port is not strict, so the provider's own mechanism is used
    """
    from teachers_teammate.infrastructure.proofreading.port import LangChainPort  # noqa: PLC0415

    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="anthropic",
        correction_model="claude-sonnet-4-5",
    )

    correction, _ = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory(build_llm=MagicMock())
    ).build_correction_and_anonymizer()

    port = correction.exchange._port
    assert isinstance(port, LangChainPort)
    assert port._strict is False


def test_no_corrector_is_built_when_proofreading_is_off(tmp_path: Path) -> None:
    """
    Given  a config with proofreading switched off
    When   the correction stage is built
    Then   there is no corrector at all
    """
    config = make_config(tmp_path, correction_enabled=False)

    correction, _ = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory()
    ).build_correction_and_anonymizer()

    assert correction is None


@pytest.mark.parametrize(
    ("provider", "model", "url", "tries"),
    [
        ("ollama", "gemma4:e4b", "http://127.0.0.1:11434", 2),
        ("ollama", "gemma4:e4b", "http://school-server:11434", 2),
        ("ollama", "gpt-oss:120b-cloud", "http://127.0.0.1:11434", 1),
        ("ollama", "gemma4:e4b", "https://ollama.com", 1),
        ("openai", "gpt-4o", "http://127.0.0.1:11434", 1),
    ],
)
def test_only_a_model_that_is_not_billed_is_asked_again_after_a_cut_off(
    tmp_path: Path, provider: str, model: str, url: str, tries: int
) -> None:
    """
    Given  proofreading on a local model, the school's server, an Ollama cloud model, Ollama's
           hosted API and an online provider
    When   the correction stage is built
    Then   only the first two are asked again after a cut-off answer; the others are billed
    """
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )

    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider=provider,
        correction_model=model,
        ollama_url=url,
    )

    correction, _ = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory(build_llm=MagicMock())
    ).build_correction_and_anonymizer(MagicMock())

    assert correction is not None
    corrector = correction.inner if isinstance(correction, ChunkingCorrector) else correction
    assert corrector.exchange._runner._cut_off_tries == tries


def test_the_request_profile_reaches_every_kind_of_corrector(tmp_path: Path) -> None:
    """
    Given  a catalog model proofread locally and an online model
    When   the correction stage is built for each path
    Then   both exchanges receive the model's profile, and the online model is built at the
           temperature of its first step
    """
    from teachers_teammate.domain.model_catalog import request_profile  # noqa: PLC0415

    llm = MagicMock()
    factory = PipelineComponentFactory(build_llm=llm)
    local = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gemma4:e4b",
    )
    online = make_config(
        tmp_path, correction_enabled=True, correction_provider="openai", correction_model="gpt-4o"
    )

    local_corrector, _ = _stage_builder(
        tmp_path, config=local, factory=factory
    ).build_correction_and_anonymizer(MagicMock())
    online_corrector, _ = _stage_builder(
        tmp_path, config=online, factory=factory
    ).build_correction_and_anonymizer()

    assert local_corrector.inner._profile == request_profile("gemma4:e4b")
    assert online_corrector._profile == request_profile("gpt-4o", "openai")
    assert (
        llm.call_args.kwargs["temperature"]
        == request_profile("gpt-4o", "openai").review_temperature
    )


def test_the_teacher_settings_reach_the_exchange(tmp_path: Path) -> None:
    """
    Given  a config with a level, an explanation language, notes and corrections only
    When   the correction stage is built and asked for its context
    Then   all four arrive in the context the exchange sends its requests with
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_level="B2",
        explanation_language="German",
        correction_notes="Year 9, second foreign language.",
        correction_output="text",
    )

    correction, _ = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory()
    ).build_correction_and_anonymizer(MagicMock())

    context = correction.inner._context("English", "plain")
    assert context.level == "B2"
    assert context.explanation_language == "German"
    assert context.teacher_notes == "Year 9, second foreign language."
    assert context.corrections_only is True


# ── one rule for "this step runs online" ──────────────────────────────────────


def _fake_provider(monkeypatch: pytest.MonkeyPatch, name: str, info: ProviderInfo) -> None:
    """Register a provider module that declares *info*."""
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    module_name = f"teachers_teammate.infrastructure.providers.{name}"
    module = types.ModuleType(module_name)
    module.PROVIDER = info
    monkeypatch.setitem(sys.modules, module_name, module)


@pytest.mark.parametrize(
    ("info", "expected_online"),
    [
        (ProviderInfo(name="ownserver", display_name="Own server", self_hosted=True), False),
        (ProviderInfo(name="ownserver", display_name="Vendor"), True),
    ],
)
def test_the_privacy_badge_and_the_number_of_tries_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, info: ProviderInfo, expected_online: bool
) -> None:
    """
    Given  a provider module that declares itself self-hosted, and one that does not, both
           pointed at a local Ollama URL
    When   the privacy badge and the number of tries are worked out
    Then   both say the same thing: a self-hosted provider is local and gets the local tries, a
           provider that does not declare it is online and gets one
    """
    from teachers_teammate.application.data_flow import llm_location  # noqa: PLC0415
    from teachers_teammate.domain.data_location import DataLocation  # noqa: PLC0415
    from teachers_teammate.infrastructure.llm_factory import get_provider_info  # noqa: PLC0415

    _fake_provider(monkeypatch, "ownserver", info)
    url = "http://127.0.0.1:11434"
    config = make_config(
        tmp_path, correction_provider="ownserver", correction_model="m", ollama_url=url
    )

    where = llm_location("ownserver", "m", url, get_provider_info)
    online = _stage_builder(
        tmp_path, config=config, factory=PipelineComponentFactory()
    )._correction_is_online()

    assert (where.location is DataLocation.ONLINE) is expected_online
    assert online is expected_online
