"""Bazel build targets for the test suite.

Running tests
─────────────
  * Lint targets (ruff / pylint / ty):
        bazel test //tests:py_ruff //tests:py_pylint //tests:py_ty

  * Run a single test file:
        bazel test //tests:test_config

  * Run the full unit-test suite:
        bazel test //tests:unit_tests

  * Everything at once:
        bazel test //tests/...
"""

load("@rules_python//python:defs.bzl", "py_library", "py_test")
load("//third_party/lint:linters.bzl", "pylint_test", "ruff_test", "ty_test")

# ── Test files tagged with @pytest.mark.use_case (for doc extraction) ────

filegroup(
    name = "use_case_test_files",
    srcs = [
        "gui/test_benchmark_integration.py",
        "gui/test_benchmark_widgets.py",
        "gui/test_benchmark_window.py",
        "gui/test_benchmark_worker.py",
        "gui/test_gui_addon_installer_dialog.py",
        "gui/test_gui_addons_page.py",
        "gui/test_gui_anonymizer_widget.py",
        "gui/test_gui_main_window.py",
        "gui/test_gui_privacy_settings.py",
        "gui/test_gui_settings_pages.py",
        "gui/test_gui_settings_window.py",
        "gui/test_gui_setup_assistant.py",
        "gui/test_gui_setup_flow.py",
        "gui/test_gui_setup_pages.py",
        "gui/test_gui_side_panel.py",
        "gui/test_gui_speed_panel.py",
        "gui/test_gui_text_views.py",
        "gui/test_gui_widgets.py",
        "gui/test_hardware_profile_selector.py",
        "gui/test_model_wording.py",
        "gui/test_ocr_config_selector.py",
        "integration/test_pipeline_preview.py",
        "integration/test_pipeline_run.py",
        "integration/test_pipeline_scenarios.py",
        "integration/test_pipeline_teacher_work.py",
        "integration/test_pipeline_txt.py",
        "unit/test_addon_manager.py",
        "unit/test_anonymizer.py",
        "unit/test_benchmark_domain.py",
        "unit/test_benchmark_run_store.py",
        "unit/test_benchmark_runner.py",
        "unit/test_benchmark_service.py",
        "unit/test_cache_service.py",
        "unit/test_cli.py",
        "unit/test_config.py",
        "unit/test_coordinator.py",
        "unit/test_docx_builder.py",
        "unit/test_docx_comments.py",
        "unit/test_file_discovery.py",
        "unit/test_file_processor.py",
        "unit/test_gpu_detector.py",
        "unit/test_model_catalog.py",
        "unit/test_model_listing.py",
        "unit/test_ollama_discovery.py",
        "unit/test_ollama_utils.py",
        "unit/test_preprocessor.py",
        "unit/test_service.py",
        "unit/test_setup_plan.py",
        "unit/test_stage_builder.py",
        "unit/test_state_repository.py",
    ],
    visibility = ["//docs:__pkg__"],
)

# ── Shared library used by lint targets ───────────────────────────────────

# Every test source, for //:py_pylint (one action over the whole first-party tree). Not a test.
filegroup(
    name = "all_sources",
    srcs = glob(["**/*.py"]),
    visibility = ["//visibility:public"],
)

# Full pip dep set for the lint aspects (ty/pylint) so test sources resolve
# their third-party imports. tests_lib is consumed only by lint targets, never
# the wheel, so depending on pip packages here is safe.
_TESTS_TYPED_DEPS = [
    "@ocr_test_dependencies//langchain_core:pkg",
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//markdown_it_py:pkg",
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//opencv_python_headless:pkg",
    "@ocr_test_dependencies//pillow:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pypdfium2:pkg",
    "@ocr_test_dependencies//pyside6_essentials:pkg",
    "@ocr_test_dependencies//pytest:pkg",
    "@ocr_test_dependencies//pytest_mock:pkg",
    "@ocr_test_dependencies//pytest_qt:pkg",
    "@ocr_test_dependencies//python_docx:pkg",
    "@ocr_test_dependencies//pyyaml:pkg",
    "@ocr_test_dependencies//qdarkstyle:pkg",
    "@ocr_test_dependencies//requests:pkg",
    "@ocr_test_dependencies//shiboken6:pkg",
    "@ocr_test_dependencies//spacy:pkg",
]

py_library(
    name = "tests_lib",
    srcs = glob(
        [
            "*.py",
            "unit/*.py",
            "integration/*.py",
            "gui/*.py",
            "tools/*.py",
            "fakes/*.py",
        ],
    ),
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:gui_lib_typed",
        "//teachers_teammate:ocr_lib_typed",
    ] + _TESTS_TYPED_DEPS,
)

# ── Shared dep groups ─────────────────────────────────────────────────────
#
# Each py_test declares only what it actually needs so that Bazel can give
# precise cache keys and run targets in parallel with minimal overhead.

_COMMON_SRCS = [
    "run_test.py",
    "conftest.py",
    "__init__.py",
    "unit/__init__.py",
    "integration/__init__.py",
    "gui/__init__.py",
    "gui/_service_stubs.py",
    "tools/__init__.py",
    "fakes/__init__.py",
    "fakes/fake_port.py",
]

# The layers of //teachers_teammate (see the comment there). A test lists the layer it imports
# and nothing above it, so an edit elsewhere in the package does not rerun it: gui_layer_lib for
# the windows, infrastructure_lib for the pipeline stages, domain_lib for the pure rules, ...
# Each one brings the layers below it. Never put //teachers_teammate:ocr_lib or :gui_lib here
# again: they hold the whole package and every test would rerun on every edit.
_PKG_LIB = ["//teachers_teammate:pkg_lib"]

_DOMAIN_LIB = ["//teachers_teammate:domain_lib"]

_CORE_LIB = ["//teachers_teammate:core_lib"]

_TEXTS_LIB = ["//teachers_teammate:texts_lib"]

_INFRASTRUCTURE_LIB = ["//teachers_teammate:infrastructure_lib"]

_APPLICATION_LIB = ["//teachers_teammate:application_lib"]

_CLI_LIB = ["//teachers_teammate:cli_lib"]

# The proofreading steps' schemas (infrastructure/proofreading/schemas.py) are pydantic models,
# which every stage that reads a proofreading result imports (directly or through cache_service).
_COMMON_DEPS = [
    "@ocr_test_dependencies//pydantic:pkg",
    "@ocr_test_dependencies//pytest:pkg",
    "@ocr_test_dependencies//pytest_mock:pkg",
]

_LANGCHAIN_DEPS = ["@ocr_test_dependencies//langchain_core:pkg"]

_IMAGE_DEPS = ["@ocr_test_dependencies//pillow:pkg"]

_PDF_DEPS = ["@ocr_test_dependencies//pypdfium2:pkg"]

# pyyaml: the built-in reader reads its recogniser's character list with it.
_CV_DEPS = [
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//opencv_python_headless:pkg",
    "@ocr_test_dependencies//pyyaml:pkg",
]

# infrastructure/ocr_formats parses OCR output with markdown-it-py (Markdown) and lxml (HTML).
# lxml also arrives with python-docx, but is listed so format tests do not rely on that.
_FORMAT_DEPS = [
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//markdown_it_py:pkg",
]

# The developer tools' shared library. Its own suite, because `//tools:model_eval` pulls in the
# whole OCR library: a tools test never runs quicker than the app tests, and it is not part of
# the unit suite.
# lxml and markdown_it: text mode tidies the answer through ocr_formats, and the protocol fingerprint
# asks the app's corrector in both modes.
_TOOL_DEPS = _COMMON_DEPS + _FORMAT_DEPS + ["//tools:model_eval"]

# The built-in reader's pinned model files (MODULE.bazel), for the tests that run the real
# models. conftest.reader_store() links them into a ModelStore; without them those tests skip.
_READER_MODEL_DATA = [
    "@ppocr_small_det_onnx//file",
    "@ppocr_small_det_yml//file",
    "@ppocr_small_rec_onnx//file",
    "@ppocr_small_rec_yml//file",
]

# The page models' pinned files (turn, flatten), for the tests that run the real models.
# conftest.page_models_store() links them into a ModelStore; without them those tests skip.
_PAGE_MODEL_DATA = [
    "@doc_ori_onnx//file",
    "@doc_ori_yml//file",
    "@uvdoc_onnx//file",
    "@uvdoc_yml//file",
]

_PAGE_MODEL_ENV = {
    "DOC_ORI_ONNX": "$(rlocationpath @doc_ori_onnx//file)",
    "DOC_ORI_YML": "$(rlocationpath @doc_ori_yml//file)",
    "UVDOC_ONNX": "$(rlocationpath @uvdoc_onnx//file)",
    "UVDOC_YML": "$(rlocationpath @uvdoc_yml//file)",
}

_READER_MODEL_ENV = {
    "PPOCR_SMALL_DET_ONNX": "$(rlocationpath @ppocr_small_det_onnx//file)",
    "PPOCR_SMALL_DET_YML": "$(rlocationpath @ppocr_small_det_yml//file)",
    "PPOCR_SMALL_REC_ONNX": "$(rlocationpath @ppocr_small_rec_onnx//file)",
    "PPOCR_SMALL_REC_YML": "$(rlocationpath @ppocr_small_rec_yml//file)",
}

# ── Individual py_test targets ────────────────────────────────────────────

_REQUESTS_DEPS = [
    "@ocr_test_dependencies//httpx:pkg",
    "@ocr_test_dependencies//requests:pkg",
]

_GUI_DEPS = [
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pyside6_essentials:pkg",
    "@ocr_test_dependencies//shiboken6:pkg",
    "@ocr_test_dependencies//pytest_qt:pkg",
    "@ocr_test_dependencies//qdarkstyle:pkg",
]

# infrastructure/pipeline.py has module-level imports of docx_builder (→ python-docx),
# preprocessing (→ opencv-python-headless), correction (→ langchain-core),
# ocr_processor + ollama_utils (→ requests), and input_providers (→ pypdfium2).
# Any test that imports from pipeline needs all of these.
_PIPELINE_DEPS = (
    _FORMAT_DEPS +
    _LANGCHAIN_DEPS +
    _IMAGE_DEPS +
    _PDF_DEPS +
    _CV_DEPS +
    _REQUESTS_DEPS + [
        "@ocr_test_dependencies//python_docx:pkg",
    ]
)

py_test(
    name = "test_collect_files",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_file_discovery.py"],
    args = ["unit/test_file_discovery.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_config",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_config.py"],
    args = ["unit/test_config.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_language",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_language.py"],
    args = ["unit/test_language.py"],
    data = ["//:requirements_addons_lock.txt"],
    env = {"ADDON_LOCK": "$(rlocationpath //:requirements_addons_lock.txt)"},
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_benchmark_domain",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_domain.py"],
    args = ["unit/test_benchmark_domain.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_benchmark_run_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_run_store.py"],
    args = ["unit/test_benchmark_run_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_benchmark_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_runner.py"],
    args = ["unit/test_benchmark_runner.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_benchmark_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_service.py"],
    args = ["unit/test_benchmark_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_cli",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cli.py"],
    args = ["unit/test_cli.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _CLI_LIB,
)

py_test(
    name = "test_cli_composition",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cli_composition.py"],
    args = ["unit/test_cli_composition.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _CLI_LIB,
)

# The one-request correctors of the evaluation baseline: the review and text modes, the
# fallback, the cut-off retries and the request the tool measures.
py_test(
    name = "test_correction",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_correction.py"],
    args = ["tools/test_correction.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + _LANGCHAIN_DEPS,
)

py_test(
    name = "test_sentence_review",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_sentence_review.py"],
    args = ["tools/test_sentence_review.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_clean_answer",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_clean_answer.py"],
    args = ["tools/test_clean_answer.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_fallback_exchange",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_fallback_exchange.py"],
    args = ["unit/test_fallback_exchange.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_step_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_step_runner.py"],
    args = ["unit/test_step_runner.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_word_diff",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_word_diff.py"],
    args = ["unit/test_word_diff.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_sentences",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_sentences.py"],
    args = ["unit/test_sentences.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_text_loss",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_text_loss.py"],
    args = ["unit/test_text_loss.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_edits",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_edits.py"],
    args = ["unit/test_edits.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_review_v2",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_review_v2.py"],
    args = ["unit/test_review_v2.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_markdown_shield",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_markdown_shield.py"],
    args = ["unit/test_markdown_shield.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_offset_map",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_offset_map.py"],
    args = ["unit/test_offset_map.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_check_items",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_items.py"],
    args = ["unit/test_check_items.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_check_document",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_document.py"],
    args = ["unit/test_check_document.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_teacher_work",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_teacher_work.py"],
    args = ["unit/test_teacher_work.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_atomic_file",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_atomic_file.py"],
    args = ["unit/test_atomic_file.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_teacher_work_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_teacher_work_store.py"],
    args = ["unit/test_teacher_work_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

# Every reader shows the merged view, never a DocumentState text of its own.
py_test(
    name = "test_teacher_work_readers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_teacher_work_readers.py"],
    args = ["unit/test_teacher_work_readers.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_quotes",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_quotes.py"],
    args = ["unit/test_quotes.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_correction_prompts_steps",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_correction_prompts_steps.py"],
    args = ["unit/test_correction_prompts_steps.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_exchange_schemas",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_exchange_schemas.py"],
    args = ["unit/test_exchange_schemas.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_exchange_corrector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_exchange_corrector.py"],
    args = ["unit/test_exchange_corrector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_exchange_t",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_exchange_t.py"],
    args = ["unit/test_exchange_t.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_request_profile",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_request_profile.py"],
    args = ["unit/test_request_profile.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_correction_presets",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_correction_presets.py"],
    args = ["tools/test_correction_presets.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + ["//tools:model_eval"],
)

py_test(
    name = "test_evaluation",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_evaluation.py"],
    args = ["unit/test_evaluation.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_input_providers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_input_providers.py"],
    args = ["unit/test_input_providers.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _IMAGE_DEPS + _PDF_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_llm_factory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_llm_factory.py"],
    args = ["unit/test_llm_factory.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ocr_processor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_processor.py"],
    args = ["unit/test_ocr_processor.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _IMAGE_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ocr_formats",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_formats.py"],
    args = ["unit/test_ocr_formats.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_ocr_text_cleaner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_text_cleaner.py"],
    args = ["unit/test_ocr_text_cleaner.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preprocessor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preprocessor.py"],
    args = ["unit/test_preprocessor.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _IMAGE_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ollama_discovery",
    size = "small",
    srcs = _COMMON_SRCS + [
        "fakes/fake_ollama.py",
        "unit/test_ollama_discovery.py",
    ],
    args = ["unit/test_ollama_discovery.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ollama_install",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_install.py"],
    args = ["unit/test_ollama_install.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ollama_utils",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_utils.py"],
    args = ["unit/test_ollama_utils.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

# The fake Ollama server, checked against the ``ollama`` client the app is locked to: a bump
# that changes a response model fails here, not as a hang in the walkthrough.
py_test(
    name = "test_fake_ollama",
    size = "small",
    srcs = _COMMON_SRCS + [
        "fakes/fake_ollama.py",
        "unit/test_fake_ollama.py",
    ],
    args = ["unit/test_fake_ollama.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + ["@ocr_test_dependencies//ollama:pkg"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_providers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_providers.py"],
    args = ["unit/test_providers.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_providers_mistral_curation",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_providers_mistral_curation.py"],
    args = ["unit/test_providers_mistral_curation.py"],
    data = ["//tests/fixtures:mistral_models.json"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_pipeline_txt_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_txt.py"],
    args = ["integration/test_pipeline_txt.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_pipeline_preview_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_preview.py"],
    args = ["integration/test_pipeline_preview.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_docx_builder",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_docx_builder.py"],
    args = ["unit/test_docx_builder.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_docx_comments",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_docx_comments.py"],
    args = ["unit/test_docx_comments.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_pipeline_run_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_run.py"],
    args = ["integration/test_pipeline_run.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_storage_policy",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_storage_policy.py"],
    args = ["unit/test_storage_policy.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_data_location",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_data_location.py"],
    args = ["unit/test_data_location.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_sent_text",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_sent_text.py"],
    args = ["unit/test_sent_text.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_domain_policy",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_domain_policy.py"],
    args = ["unit/test_domain_policy.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_check_linux_bundle",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_linux_bundle.py"],
    args = ["unit/test_check_linux_bundle.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_check_macos_bundle",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_macos_bundle.py"],
    args = ["unit/test_check_macos_bundle.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_check_desktop_entry",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_desktop_entry.py"],
    args = ["unit/test_check_desktop_entry.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/packaging:packaging_tools_lib"],
)

py_test(
    name = "test_latest_release",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_latest_release.py"],
    args = ["unit/test_latest_release.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/packaging:packaging_tools_lib"],
)

py_test(
    name = "test_windows_installer_floor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_windows_installer_floor.py"],
    args = ["unit/test_windows_installer_floor.py"],
    data = ["//tools/packaging:windows.nsi"],
    env = {"WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)"},
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_windows_installer_script",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_windows_installer_script.py"],
    args = ["unit/test_windows_installer_script.py"],
    data = ["//tools/packaging:windows.nsi"],
    env = {"WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)"},
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_addon_constraints",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_constraints.py"],
    args = ["unit/test_addon_constraints.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_texts_types",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_texts_types.py"],
    args = ["unit/test_texts_types.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _TEXTS_LIB,
)

# The ratchet that keeps teacher-facing text in ``teachers_teammate/texts``: no literal at a
# call site that is not on the allowlist, no stale allowlist entry, and no ``Notice`` built
# outside the package.  The sources come in as data because the test parses them with ``ast``.
# The import direction between the layers of //teachers_teammate (see the libraries there).
py_test(
    name = "test_layer_imports",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_layer_imports.py"],
    args = ["unit/test_layer_imports.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_user_text_centralised",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_user_text_centralised.py"],
    args = ["unit/test_user_text_centralised.py"],
    data = ["//teachers_teammate:user_text_sources"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/user_text:user_text_lib"] + _PKG_LIB,
)

py_test(
    name = "test_inventory_tool",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_inventory_tool.py"],
    args = ["unit/test_inventory_tool.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/licenses:licenses_lib"] + _INFRASTRUCTURE_LIB,
)

# The committed store, checked offline: the rows are the closures of the locks, every license
# passes the deny rules, every bundle row has a text, and the compiled outputs are fresh.  A
# Dependabot bump of either lock is red here until tools/licenses/inventory.py runs.
py_test(
    name = "test_license_inventory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_license_inventory.py"],
    args = ["unit/test_license_inventory.py"],
    data = [
        "//:.python-version",
        "//:NOTICE",
        "//:license_store",
        "//:pyproject.toml",
        "//:requirements_addons_lock.txt",
        "//:requirements_test_lock.txt",
        "//teachers_teammate:assets",
        "//tools/build:requirements_platform.txt",
    ],
    env = {"LICENSE_PYPROJECT": "$(rlocationpath //:pyproject.toml)"},
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/licenses:licenses_lib"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_fetch_models",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_fetch_models.py"],
    args = ["unit/test_fetch_models.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + ["//tools/build:build_tools_lib"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_addon_lock",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_lock.py"],
    args = ["unit/test_addon_lock.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_check_pins",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_pins.py"],
    args = ["unit/test_check_pins.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"],
)

py_test(
    name = "test_make_constraints",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_make_constraints.py"],
    args = ["unit/test_make_constraints.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"],
)

py_test(
    name = "test_make_version_resource",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_make_version_resource.py"],
    args = ["unit/test_make_version_resource.py"],
    data = ["//tools/packaging:windows.nsi"],
    env = {"WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)"},
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

# The wheel Bazel builds must hold every module, the assets and the help pages, and say what
# pyproject.toml says. Builds the wheel, so it is the slowest of the release tests; the wheel's
# install is checked on the release dry run (build_wheel.yml).
py_test(
    name = "test_wheel",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_wheel.py"],
    args = ["unit/test_wheel.py"],
    data = [
        "//:pyproject.toml",
        "//docs:advanced_user_guide.md",
        "//docs:index.md",
        "//docs:using_the_app.md",
        "//teachers_teammate:__init__.py",
        "//teachers_teammate:all_sources",
        "//teachers_teammate:teachers_teammate_wheel",
    ],
    env = {
        "DOCS_INDEX": "$(rlocationpath //docs:index.md)",
        "PACKAGE_INIT": "$(rlocationpath //teachers_teammate:__init__.py)",
        "PYPROJECT": "$(rlocationpath //:pyproject.toml)",
        "WHEEL": "$(rlocationpath //teachers_teammate:teachers_teammate_wheel)",
    },
    main = "run_test.py",
    deps = _COMMON_DEPS + ["@ocr_test_dependencies//packaging:pkg"],
)

py_test(
    name = "test_check_smoke",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_smoke.py"],
    args = ["unit/test_check_smoke.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_release_tag",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_release_tag.py"],
    args = ["unit/test_release_tag.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/release:release_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_release_stamp_versions",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_release_stamp_versions.py"],
    args = ["unit/test_release_stamp_versions.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/release:release_tools_lib"] + _PKG_LIB,
)

# `.python-version` is the one place the Python version is written down (rules_python reads it for
# the toolchain, setup-python in CI, build.sh/build.bat name the same minor).  This target holds
# the interpreter the tests run on to it.
py_test(
    name = "test_python_version",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_python_version.py"],
    args = ["unit/test_python_version.py"],
    data = ["//:.python-version"],
    env = {"PYTHON_VERSION_FILE": "$(rlocationpath //:.python-version)"},
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_interfaces",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_interfaces.py"],
    args = ["unit/test_interfaces.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _CORE_LIB,
)

py_test(
    name = "test_run_summary",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_summary.py"],
    args = ["unit/test_run_summary.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_power",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_power.py"],
    args = ["unit/test_power.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_state_repository",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_state_repository.py"],
    args = ["unit/test_state_repository.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_service.py"],
    args = ["unit/test_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_setup_plan",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_setup_plan.py"],
    args = ["unit/test_setup_plan.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_addon_manager",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_manager.py"],
    args = ["unit/test_addon_manager.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

# The smoke test imports every lazily loaded module of the app, so it needs the whole runtime
# set — including the packages no other unit test imports (ollama, platformdirs, keyring).
# ``medium``: the smoke test now also builds the Setup Assistant and waits for its plan, which
# has a 30 s timeout of its own, so the 60 s of ``small`` is too tight on a loaded runner.
py_test(
    name = "test_frozen_modes",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_settings_stubs.py",
        "unit/test_frozen_modes.py",
    ],
    args = ["unit/test_frozen_modes.py"],
    data = ["//tools/build:teachers-teammate.spec"],
    env = {"BUILD_SPEC": "$(rlocationpath //tools/build:teachers-teammate.spec)"},
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//keyring:pkg",
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//platformdirs:pkg",
    ],
)

py_test(
    name = "test_gpu_detector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_gpu_detector.py"],
    args = ["unit/test_gpu_detector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_diagnostics",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_diagnostics.py"],
    args = ["unit/test_diagnostics.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_speed_estimates",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_speed_estimates.py"],
    args = ["unit/test_speed_estimates.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_speed_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_speed_store.py"],
    args = ["unit/test_speed_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_run_stats",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_stats.py"],
    args = ["unit/test_run_stats.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_performance_probe",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_performance_probe.py"],
    args = ["unit/test_performance_probe.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _IMAGE_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_performance_estimate",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_performance_estimate.py"],
    args = ["unit/test_performance_estimate.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_model_catalog",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_model_catalog.py"],
    args = ["unit/test_model_catalog.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_chunking_corrector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_chunking_corrector.py"],
    args = ["unit/test_chunking_corrector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_text_chunks",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_text_chunks.py"],
    args = ["unit/test_text_chunks.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

# The metrics of a proofreading answer against a reference correction: edit agreement,
# explanations, sentences left alone.  Pure, so no server and no corpus.
py_test(
    name = "test_model_eval_quality",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_quality.py"],
    args = ["tools/test_quality.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_check_rule",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_check_rule.py"],
    args = ["tools/test_check_rule.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The structured-output check itself: the fixtures as data, the size the app really sends, the
# request it really builds, and how one answer is judged.  The server is scripted.
py_test(
    name = "test_model_eval_structured_check",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_structured_check.py"],
    args = ["tools/test_structured_check.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The memory agent that runs on the machine with the graphics card: the readers, the ring buffer,
# the windows and the HTTP API, all offline.
py_test(
    name = "test_gpu_agent",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_gpu_agent.py"],
    args = ["tools/test_gpu_agent.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The tool's side of that agent: the four requests, the answers as types, and the wait for the
# card to settle.
py_test(
    name = "test_agent_client",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_agent_client.py"],
    args = ["tools/test_agent_client.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# What a model really needs: the stored records, the need, the size limit and the growth with the
# window, plus the flags that say when a figure cannot be entered as it is.
py_test(
    name = "test_model_eval_model_fit",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_model_fit.py"],
    args = ["tools/test_model_fit.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The command line of the structured-output check: the header, the default window, the saved
# answers and the re-run that judges them again, mixed --tag lists, the margin warning.
py_test(
    name = "test_verify_model_catalog_cli",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_verify_model_catalog.py"],
    args = ["tools/test_verify_model_catalog.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:verify_model_catalog"],
)

# The per-setup smoke check: the two-in-a-row rule, the retry, the collapse signature, the
# text-mode rule, and the two commands that run it (``smoke`` and ``--continue``).
py_test(
    name = "test_model_eval_smoke",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_smoke.py"],
    args = ["tools/test_smoke.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:verify_model_catalog"],
)

# The answer modes: the values, the parser and the row that carries one.
py_test(
    name = "test_model_eval_answer_mode",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_answer_mode.py"],
    args = ["tools/test_answer_mode.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# GLEU: the counts of one text, their sum over texts, and the score, cross-checked against the
# official script of gec-ranking.
py_test(
    name = "test_model_eval_gleu",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_gleu.py"],
    args = ["tools/test_gleu.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The numbers of one item: GLEU, recall by half, left alone and coverage.
py_test(
    name = "test_model_eval_item_scores",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_item_scores.py"],
    args = ["tools/test_item_scores.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The arithmetic the reports share: pooling, the F-beta and its mean.
py_test(
    name = "test_model_eval_scoring",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_scoring.py"],
    args = ["tools/test_scoring.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_results",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_results.py"],
    args = ["tools/test_results.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_error_labels",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_error_labels.py"],
    args = ["tools/test_error_labels.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_measure",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_measure.py"],
    args = ["tools/test_measure.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The version file of the request side: the fingerprint, the moved instructions hash and the
# validity of protocol.json itself.
py_test(
    name = "test_model_eval_protocol",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_protocol.py"],
    args = ["tools/test_protocol.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The sessions of one run (the header file and the resume check) and the layering of the offline
# modules.
py_test(
    name = "test_model_eval_session",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_session.py"],
    args = ["tools/test_session.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The app's own corrector as the tools build it, and the guard that it stays the app's: the test
# also builds the app's corrector through the stage builder, which needs the pipeline libraries.
py_test(
    name = "test_model_eval_app_corrector",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_app_corrector.py"],
    args = ["tools/test_app_corrector.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + _LANGCHAIN_DEPS + _IMAGE_DEPS + _PDF_DEPS + _CV_DEPS + _REQUESTS_DEPS + [
        "@ocr_test_dependencies//python_docx:pkg",
    ],
)

py_test(
    name = "test_model_eval_comparison",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_comparison.py"],
    args = ["tools/test_comparison.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_edit_review",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_edit_review.py"],
    args = ["tools/test_edit_review.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The developer tool that measures what a reading model costs on one page: the pure parts, so no
# server and no page image.  The tool itself imports the image preprocessor, hence the CV deps.
py_test(
    name = "test_ocr_page_tokens",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_ocr_page_tokens.py"],
    args = ["tools/test_ocr_page_tokens.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + _CV_DEPS + _IMAGE_DEPS + ["//tools:ocr_page_tokens"],
)

py_test(
    name = "test_model_eval_redaction",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_redaction.py"],
    args = ["tools/test_redaction.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The words an answer keeps and adds against the reference, and the floor that reads them.
py_test(
    name = "test_model_eval_text_loss",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_text_loss.py",
    ],
    args = ["tools/test_text_loss.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# test_testset and test_store build their corpus from tests/tools/invented_corpus.py, which is a
# source of those two targets only.
py_test(
    name = "test_model_eval_testset",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/test_testset.py",
    ],
    args = ["tools/test_testset.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The corpus registry, the four adapters and the recipes: what a manifest needs from a corpus, how
# a pointer becomes a text, and which texts a test set chooses.
py_test(
    name = "test_model_eval_corpora",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/test_corpora.py",
    ],
    args = ["tools/test_corpora.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The recipe of gec-2026-12: the bands, the slots and the filters, on a corpus built for the
# bands (tests/tools/band_corpus.py), and the committed manifest of the set. The builder is a
# dependency so that the test runs the very module `bazel run //tools:prepare_gec_testset` runs.
py_test(
    name = "test_model_eval_recipe_gec_2026_12",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "tools/band_corpus.py",
        "tools/test_recipe_gec_2026_12.py",
    ],
    args = ["tools/test_recipe_gec_2026_12.py"],
    data = ["//evaluations/proofreading:all"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:prepare_gec_testset_lib"],
)

# The recipe of gec-2026-12-heldout: the same bands and filters on the splits the first set never
# reads, the CEFR level of an English item, and the committed manifest of the set.
py_test(
    name = "test_model_eval_recipe_gec_2026_12_heldout",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "tools/band_corpus.py",
        "tools/test_recipe_gec_2026_12_heldout.py",
    ],
    args = ["tools/test_recipe_gec_2026_12_heldout.py"],
    data = ["//evaluations/proofreading:all"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:prepare_gec_testset_lib"],
)

# A corpus whose adapter is defined in the test file itself goes through build,
# check, export and both validation modes, and is named on the command line. Nothing else changes.
py_test(
    name = "test_model_eval_corpus_extension",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_corpus_extension.py",
    ],
    args = ["tools/test_corpus_extension.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + [
        "//tools:prepare_gec_testset_lib",
        "//tools:proofreading_store",
    ],
)

# The committed set still rebuilds from the local corpora. Skipped unless
# GEC_CORPORA names a {corpus: path} file, which is never the case in CI: the corpora are licensed
# and stay outside the repository.
py_test(
    name = "test_model_eval_corpus_regression",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_corpus_regression.py",
    ],
    args = ["tools/test_corpus_regression.py"],
    data = ["//evaluations/proofreading:all"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The export, the validator, the index and the layout of a run file, each with its own target.
# The tests that read the committed tree below evaluations/proofreading take the filegroup as data,
# so that the check of the committed runs is not skipped under Bazel.
py_test(
    name = "test_model_eval_export",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_export.py",
    ],
    args = ["tools/test_export.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_validation",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_validation.py",
    ],
    args = ["tools/test_validation.py"],
    data = ["//evaluations/proofreading:all"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_index",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_index.py",
    ],
    args = ["tools/test_index.py"],
    data = ["//evaluations/proofreading:all"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_layout",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_layout.py",
    ],
    args = ["tools/test_layout.py"],
    data = ["//evaluations/proofreading:all"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_report_sources",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_report_sources.py",
    ],
    args = ["tools/test_report_sources.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The log of one run: one JSON line per event, so a night that was paused can be read afterwards.
py_test(
    name = "test_model_eval_run_log",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_run_log.py",
    ],
    args = ["tools/test_run_log.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

py_test(
    name = "test_model_eval_run_format",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_run_format.py",
    ],
    args = ["tools/test_run_format.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The command line of the proofreading store; the binary is a dependency so that the test imports
# the very module `bazel run //tools:proofreading_store` runs, with the dependencies it declares.
py_test(
    name = "test_model_eval_store_cli",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/invented_corpus.py",
        "tools/store_workspace.py",
        "tools/test_proofreading_store_cli.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_proofreading_store_cli.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:proofreading_store"],
)

# The repository explains itself: no source, document or record points into git-ignored notes.
py_test(
    name = "test_no_plan_references",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_no_plan_references.py"],
    args = ["tools/test_no_plan_references.py"],
    data = [
        ":all_sources",
        "//:AGENTS.md",
        "//docs:markdown",
        "//evaluations:records",
        "//evaluations/proofreading:all",
        "//teachers_teammate:all_sources",
        "//tools:all_sources",
    ],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PKG_LIB,
)

# Gate 1 beyond the single request: the saved-answers file and continuing a run.
py_test(
    name = "test_model_eval_gate_one",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_gate_one.py"],
    args = ["tools/test_gate_one.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# Gate 2's groups: the key that separates runs, the ranges of the repeats and the order of a
# group.  The fixtures build the runs in memory, so no corpus and no export is needed.
py_test(
    name = "test_model_eval_grouping",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_grouping.py",
    ],
    args = ["tools/test_grouping.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The groups a local result folder makes, so gate 2 reads one like a committed run.  The fixtures
# write a real test set and real caches to a temporary directory, so the format is a run's.
py_test(
    name = "test_model_eval_cache_groups",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_cache_groups.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_cache_groups.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# Gate 2's floors and its comparison: one number moved per test, so each check is seen alone.
py_test(
    name = "test_model_eval_gate_two",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_gate_two.py",
    ],
    args = ["tools/test_gate_two.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The figures a report is made of: per page of 300 words, found the spot, and the mean over texts.
# The fixtures build the runs in memory, so one number can be moved and one figure seen.
py_test(
    name = "test_model_eval_summary",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_summary.py",
    ],
    args = ["tools/test_summary.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The sweep: one row per temperature, the difference against a reference row, and the best row with
# the rows that are a tie with it.
py_test(
    name = "test_model_eval_sweep",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_sweep.py",
    ],
    args = ["tools/test_sweep.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The passes: what a vote over the repeats of one configuration would keep, and what the passes
# would cost.  The rows are hand-made, so every count is arithmetic a reader can follow.
py_test(
    name = "test_model_eval_passes",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_passes.py",
    ],
    args = ["tools/test_passes.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The explanation check: the sample of a run, the marks without text, and the sheet whose source
# sentences come from a local cache.
py_test(
    name = "test_model_eval_spot_check",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/gate_fixtures.py",
        "tools/test_spot_check.py",
    ],
    args = ["tools/test_spot_check.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The progress display: the line, the estimate, the waiting thread, and the two commands that
# report every request through it.
py_test(
    name = "test_model_eval_progress",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_progress.py",
        "tools/test_runner.py",
    ],
    args = ["tools/test_progress.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:verify_model_catalog"],
)

# The run loop of `compare_writing_models run`, driven without a server: the planning, the resume
# check, the seed per repeat, the warm-up, the header and the sampler.
py_test(
    name = "test_model_eval_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_runner.py"],
    args = ["tools/test_runner.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The test-set builder: the day it records, the checks it runs on its own manifest, and the
# warning a dirty tree gets. The library is a dependency so that the test imports the very module
# `bazel run //tools:prepare_gec_testset` runs, with the dependencies it declares.
py_test(
    name = "test_prepare_gec_testset",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/invented_corpus.py",
        "tools/test_prepare_gec_testset.py",
    ],
    args = ["tools/test_prepare_gec_testset.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:prepare_gec_testset_lib"],
)

# The command line of the comparison; the binary is a dependency so that the test imports the very
# module `bazel run //tools:compare_writing_models` runs, with the dependencies it declares.
py_test(
    name = "test_model_eval_compare_cli",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_compare_cli.py",
    ],
    args = ["tools/test_compare_cli.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:compare_writing_models"],
)

# The evaluation report's reading: the test set, the local caches, what was skipped and why, and
# the variants the runs make.  The fixtures write a real test set and real caches to a temporary
# directory, so the format is the one a run writes.
py_test(
    name = "test_model_eval_viewer_load",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_viewer_load.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_viewer_load.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# What each edit of a version came to: exact, located or extra for the model's, fixed, located or
# missed for the reference's, and the sentence rows both are shown in.
py_test(
    name = "test_model_eval_viewer_outcomes",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_viewer_outcomes.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_viewer_outcomes.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The scorecard: one row per variant, language and band, per repeat, with the mean and the range
# from the same functions the report and gate 2 use.
py_test(
    name = "test_model_eval_viewer_numbers",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_viewer_numbers.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_viewer_numbers.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The report as one model: the rows of a text, the complete sentence of every version with its
# changes marked, the counts of each version and the scorecard of each variant.
py_test(
    name = "test_model_eval_viewer_model",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_viewer_model.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_viewer_model.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

# The report folder: the page, the script and style, one data file per text, the zip, and the
# refusal to write inside a git work tree.
py_test(
    name = "test_model_eval_viewer_page",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_viewer_page.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_viewer_page.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS + ["//tools:eval_report"],
)

py_test(
    name = "test_model_eval_viewer_schema",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_viewer_schema.py",
        "tools/viewer_fixtures.py",
    ],
    args = ["tools/test_viewer_schema.py"],
    main = "run_test.py",
    deps = _TOOL_DEPS,
)

test_suite(
    name = "tools_tests",
    tests = [
        ":test_edit_review",
        ":test_model_eval_answer_mode",
        ":test_model_eval_app_corrector",
        ":test_model_eval_cache_groups",
        ":test_model_eval_check_rule",
        ":test_model_eval_compare_cli",
        ":test_model_eval_comparison",
        ":test_model_eval_corpora",
        ":test_model_eval_corpus_extension",
        ":test_model_eval_corpus_regression",
        ":test_model_eval_error_labels",
        ":test_model_eval_export",
        ":test_model_eval_gate_one",
        ":test_model_eval_gate_two",
        ":test_model_eval_gleu",
        ":test_model_eval_grouping",
        ":test_model_eval_index",
        ":test_model_eval_item_scores",
        ":test_model_eval_layout",
        ":test_model_eval_measure",
        ":test_model_eval_model_fit",
        ":test_model_eval_passes",
        ":test_model_eval_progress",
        ":test_model_eval_protocol",
        ":test_model_eval_quality",
        ":test_model_eval_recipe_gec_2026_12",
        ":test_model_eval_recipe_gec_2026_12_heldout",
        ":test_model_eval_redaction",
        ":test_model_eval_report_sources",
        ":test_model_eval_results",
        ":test_model_eval_run_format",
        ":test_model_eval_run_log",
        ":test_model_eval_runner",
        ":test_model_eval_scoring",
        ":test_model_eval_session",
        ":test_model_eval_smoke",
        ":test_model_eval_spot_check",
        ":test_model_eval_store_cli",
        ":test_model_eval_structured_check",
        ":test_model_eval_summary",
        ":test_model_eval_sweep",
        ":test_model_eval_testset",
        ":test_model_eval_text_loss",
        ":test_model_eval_validation",
        ":test_model_eval_viewer_load",
        ":test_model_eval_viewer_model",
        ":test_model_eval_viewer_numbers",
        ":test_model_eval_viewer_outcomes",
        ":test_model_eval_viewer_page",
        ":test_model_eval_viewer_schema",
        ":test_no_plan_references",
        ":test_no_retired_engines",
        ":test_no_sentence_review_in_the_app",
        ":test_ocr_page_tokens",
        ":test_prepare_gec_testset",
        ":test_verify_model_catalog_cli",
    ],
)

py_test(
    name = "test_context_budget",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_context_budget.py"],
    args = ["unit/test_context_budget.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_model_listing",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_model_listing.py"],
    args = ["unit/test_model_listing.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_cache_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cache_service.py"],
    args = ["unit/test_cache_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_file_processor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_file_processor.py"],
    args = ["unit/test_file_processor.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preview_image_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preview_image_store.py"],
    args = ["unit/test_preview_image_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_input_provider_factory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_input_provider_factory.py"],
    args = ["unit/test_input_provider_factory.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PDF_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ocr_stage_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_stage_service.py"],
    args = ["unit/test_ocr_stage_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preprocess_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preprocess_service.py"],
    args = ["unit/test_preprocess_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_stage_builder",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_stage_builder.py"],
    args = ["unit/test_stage_builder.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_coordinator",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_coordinator.py"],
    args = ["unit/test_coordinator.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _FORMAT_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_gui_config_panel",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/test_gui_config_panel.py",
    ],
    args = ["gui/test_gui_config_panel.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_readiness",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_readiness.py"],
    args = ["gui/test_gui_readiness.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_page",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_check_page.py"],
    args = ["gui/test_gui_check_page.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_results_table",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_results_table.py"],
    args = ["gui/test_gui_results_table.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_what_is_sent",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_what_is_sent.py"],
    args = ["gui/test_gui_what_is_sent.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_main_window",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_main_window.py",
    ],
    args = ["gui/test_gui_main_window.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_ollama_status_line",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_ollama_status_line.py"],
    args = ["gui/test_gui_ollama_status_line.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_worker",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_worker.py"],
    args = ["gui/test_gui_worker.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_prompt_dialog",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_settings_stubs.py",
        "gui/test_gui_prompt_dialog.py",
    ],
    args = ["gui/test_gui_prompt_dialog.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_credential_store",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_settings_stubs.py",
        "gui/test_credential_store.py",
    ],
    args = ["gui/test_credential_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_general_page",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_general_page.py",
    ],
    args = ["gui/test_gui_general_page.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_run_status",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_run_status.py"],
    args = ["gui/test_gui_run_status.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_settings_pages",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_settings_pages.py",
    ],
    args = ["gui/test_gui_settings_pages.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_settings_window",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_settings_window.py",
    ],
    args = ["gui/test_gui_settings_window.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_setup_assistant",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_setup_assistant.py",
    ],
    args = ["gui/test_gui_setup_assistant.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_setup_flow",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_setup_flow.py"],
    args = ["gui/test_gui_setup_flow.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

# The whole wizard, end to end: the real pages, the real service, real threads, a real HTTP
# conversation with a fake Ollama and the real MainWindow writing a real ocr.toml.  ``medium``
# for the headroom: the default 60 s of ``small`` is tight for a wizard plus real threads.
py_test(
    name = "test_gui_setup_walkthrough",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "fakes/fake_ollama.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_setup_walkthrough.py",
    ],
    args = ["gui/test_gui_setup_walkthrough.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

py_test(
    name = "test_gui_setup_pages",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_setup_pages.py",
    ],
    args = ["gui/test_gui_setup_pages.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_plain_map",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_plain_map.py"],
    args = ["gui/test_gui_plain_map.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_style",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_check_style.py"],
    args = ["gui/test_gui_check_style.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_text_views",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_text_views.py"],
    args = ["gui/test_gui_text_views.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_body",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_check_body.py"],
    args = ["gui/test_gui_check_body.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_side_panel",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_side_panel.py"],
    args = ["gui/test_gui_side_panel.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_scan_view",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_scan_view.py"],
    args = ["gui/test_gui_scan_view.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_formatted_text",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_formatted_text.py"],
    args = ["gui/test_gui_formatted_text.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_chart_widget",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_chart_widget.py"],
    args = ["gui/test_gui_chart_widget.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_widgets",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_widgets.py"],
    args = ["gui/test_gui_widgets.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_anonymizer",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_anonymizer.py"],
    args = ["unit/test_anonymizer.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_gui_anonymizer_widget",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/test_gui_anonymizer_widget.py",
    ],
    args = ["gui/test_gui_anonymizer_widget.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_privacy_settings",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_privacy_settings.py",
    ],
    args = ["gui/test_gui_privacy_settings.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_anonymization_mask",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_anonymization_mask.py"],
    args = ["unit/test_anonymization_mask.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_gui_privacy_view",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_privacy_view.py",
    ],
    args = ["gui/test_gui_privacy_view.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_dialogs",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_dialogs.py"],
    args = ["gui/test_gui_dialogs.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_addon_installer_dialog",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_addon_installer_dialog.py"],
    args = ["gui/test_gui_addon_installer_dialog.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_update_check",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_update_check.py"],
    args = ["gui/test_gui_update_check.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_ocr_config_selector",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_ocr_config_selector.py"],
    args = ["gui/test_ocr_config_selector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_speed_panel",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_speed_panel.py"],
    args = ["gui/test_gui_speed_panel.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_hardware_profile_selector",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_hardware_profile_selector.py"],
    args = ["gui/test_hardware_profile_selector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_background",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_background.py"],
    args = ["gui/test_background.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_model_item_delegate",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_model_item_delegate.py"],
    args = ["gui/test_model_item_delegate.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_model_wording",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_model_wording.py"],
    args = ["gui/test_model_wording.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_install_round",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_install_round.py"],
    args = ["gui/test_gui_install_round.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_addons_page",
    size = "small",
    srcs = _COMMON_SRCS + [
        "fakes/fake_ollama.py",
        "gui/_gui_harness.py",
        "gui/test_gui_addons_page.py",
    ],
    args = ["gui/test_gui_addons_page.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

py_test(
    name = "test_benchmark_window",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_window.py"],
    args = ["gui/test_benchmark_window.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_lifecycle",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_lifecycle.py",
    ],
    args = ["gui/test_gui_lifecycle.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_bootstrap",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_bootstrap.py",
    ],
    args = ["gui/test_gui_bootstrap.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_benchmark_widgets",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_widgets.py"],
    args = ["gui/test_benchmark_widgets.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_benchmark_worker",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_worker.py"],
    args = ["gui/test_benchmark_worker.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_benchmark_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_integration.py"],
    args = ["gui/test_benchmark_integration.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_pipeline_scenarios_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_scenarios.py"],
    args = ["integration/test_pipeline_scenarios.py"],
    data = _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_pipeline_teacher_work_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_teacher_work.py"],
    args = ["integration/test_pipeline_teacher_work.py"],
    data = _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_local_models",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_local_models.py"],
    args = ["unit/test_local_models.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_model_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_model_store.py"],
    args = ["unit/test_model_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_detect",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_detect.py"],
    args = ["unit/test_ppocr_detect.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_reading_order",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_reading_order.py"],
    args = ["unit/test_ppocr_reading_order.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_recognise",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_recognise.py"],
    args = ["unit/test_ppocr_recognise.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_engine",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_engine.py"],
    args = ["unit/test_ppocr_engine.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _CV_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_models",
    size = "medium",
    srcs = _COMMON_SRCS + ["integration/test_ppocr_models.py"],
    args = ["integration/test_ppocr_models.py"],
    data = ["//tests/fixtures:umlaut_line.png"] + _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_no_retired_engines",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_no_retired_engines.py"],
    args = ["tools/test_no_retired_engines.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PKG_LIB,
)

py_test(
    name = "test_no_sentence_review_in_the_app",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_no_sentence_review_in_the_app.py"],
    args = ["tools/test_no_sentence_review_in_the_app.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PKG_LIB,
)

py_test(
    name = "test_preprocessing_auto",
    size = "small",
    srcs = _COMMON_SRCS + [
        "unit/preprocessing_auto_golden_data.py",
        "unit/test_preprocessing_auto.py",
    ],
    args = ["unit/test_preprocessing_auto.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preprocessing_auto_golden",
    size = "small",
    srcs = _COMMON_SRCS + [
        "unit/preprocessing_auto_golden_data.py",
        "unit/test_preprocessing_auto_golden.py",
    ],
    args = ["unit/test_preprocessing_auto_golden.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_page_models",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "integration/test_page_models.py",
        "unit/preprocessing_auto_golden_data.py",
    ],
    args = ["integration/test_page_models.py"],
    data = _PAGE_MODEL_DATA,
    env = _PAGE_MODEL_ENV,
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

# ── Test suite ────────────────────────────────────────────────────────────

test_suite(
    name = "unit_tests",
    tests = [
        ":test_addon_constraints",
        ":test_addon_lock",
        ":test_addon_manager",
        ":test_anonymization_mask",
        ":test_anonymizer",
        ":test_benchmark_domain",
        ":test_benchmark_run_store",
        ":test_benchmark_runner",
        ":test_benchmark_service",
        ":test_cache_service",
        ":test_check_desktop_entry",
        ":test_check_document",
        ":test_check_items",
        ":test_check_linux_bundle",
        ":test_check_macos_bundle",
        ":test_check_pins",
        ":test_check_smoke",
        ":test_chunking_corrector",
        ":test_clean_answer",
        ":test_cli",
        ":test_cli_composition",
        ":test_collect_files",
        ":test_config",
        ":test_context_budget",
        ":test_coordinator",
        ":test_correction",
        ":test_correction_presets",
        ":test_correction_prompts_steps",
        ":test_data_location",
        ":test_diagnostics",
        ":test_docx_builder",
        ":test_docx_comments",
        ":test_domain_policy",
        ":test_edits",
        ":test_evaluation",
        ":test_exchange_corrector",
        ":test_exchange_schemas",
        ":test_exchange_t",
        ":test_fake_ollama",
        ":test_fallback_exchange",
        ":test_fetch_models",
        ":test_file_processor",
        ":test_frozen_modes",
        ":test_gpu_detector",
        ":test_input_provider_factory",
        ":test_input_providers",
        ":test_interfaces",
        ":test_inventory_tool",
        ":test_language",
        ":test_latest_release",
        ":test_layer_imports",
        ":test_license_inventory",
        ":test_llm_factory",
        ":test_local_models",
        ":test_make_constraints",
        ":test_make_version_resource",
        ":test_markdown_shield",
        ":test_model_catalog",
        ":test_model_listing",
        ":test_model_store",
        ":test_ocr_formats",
        ":test_ocr_processor",
        ":test_ocr_stage_service",
        ":test_ocr_text_cleaner",
        ":test_offset_map",
        ":test_ollama_discovery",
        ":test_ollama_install",
        ":test_ollama_utils",
        ":test_performance_estimate",
        ":test_performance_probe",
        ":test_pipeline_txt_integration",
        ":test_power",
        ":test_ppocr_detect",
        ":test_ppocr_engine",
        ":test_ppocr_reading_order",
        ":test_ppocr_recognise",
        ":test_preprocess_service",
        ":test_preprocessing_auto",
        ":test_preprocessing_auto_golden",
        ":test_preprocessor",
        ":test_preview_image_store",
        ":test_providers",
        ":test_providers_mistral_curation",
        ":test_python_version",
        ":test_quotes",
        ":test_release_stamp_versions",
        ":test_release_tag",
        ":test_request_profile",
        ":test_review_v2",
        ":test_run_stats",
        ":test_run_summary",
        ":test_sent_text",
        ":test_sentence_review",
        ":test_sentences",
        ":test_service",
        ":test_setup_plan",
        ":test_speed_estimates",
        ":test_speed_store",
        ":test_stage_builder",
        ":test_state_repository",
        ":test_step_runner",
        ":test_storage_policy",
        ":test_teacher_work",
        ":test_teacher_work_readers",
        ":test_text_chunks",
        ":test_text_loss",
        ":test_texts_types",
        ":test_user_text_centralised",
        ":test_wheel",
        ":test_windows_installer_floor",
        ":test_windows_installer_script",
        ":test_word_diff",
    ],
)

test_suite(
    name = "gui_tests",
    tests = [
        ":test_background",
        ":test_benchmark_integration",
        ":test_benchmark_widgets",
        ":test_benchmark_window",
        ":test_benchmark_worker",
        ":test_credential_store",
        ":test_gui_addon_installer_dialog",
        ":test_gui_addons_page",
        ":test_gui_anonymizer_widget",
        ":test_gui_bootstrap",
        ":test_gui_chart_widget",
        ":test_gui_check_body",
        ":test_gui_check_page",
        ":test_gui_check_style",
        ":test_gui_config_panel",
        ":test_gui_dialogs",
        ":test_gui_formatted_text",
        ":test_gui_general_page",
        ":test_gui_install_round",
        ":test_gui_lifecycle",
        ":test_gui_main_window",
        ":test_gui_plain_map",
        ":test_gui_privacy_settings",
        ":test_gui_privacy_view",
        ":test_gui_prompt_dialog",
        ":test_gui_readiness",
        ":test_gui_results_table",
        ":test_gui_run_status",
        ":test_gui_scan_view",
        ":test_gui_settings_pages",
        ":test_gui_settings_window",
        ":test_gui_setup_assistant",
        ":test_gui_setup_flow",
        ":test_gui_setup_pages",
        ":test_gui_setup_walkthrough",
        ":test_gui_side_panel",
        ":test_gui_speed_panel",
        ":test_gui_text_views",
        ":test_gui_update_check",
        ":test_gui_what_is_sent",
        ":test_gui_widgets",
        ":test_gui_worker",
        ":test_hardware_profile_selector",
        ":test_model_item_delegate",
        ":test_model_wording",
        ":test_ocr_config_selector",
    ],
)

test_suite(
    name = "integration_tests",
    tests = [
        ":test_page_models",
        ":test_pipeline_preview_integration",
        ":test_pipeline_run_integration",
        ":test_pipeline_scenarios_integration",
        ":test_pipeline_teacher_work_integration",
        ":test_pipeline_txt_integration",
        ":test_ppocr_models",
    ],
)

test_suite(
    name = "coverage_tests",
    tests = [
        ":gui_tests",
        ":unit_tests",
    ],
)

# ── Lint targets ──────────────────────────────────────────────────────────

pylint_test(
    name = "py_pylint",
    srcs = [":tests_lib"],
)

ruff_test(
    name = "py_ruff",
    srcs = [":tests_lib"],
)

ty_test(
    name = "py_ty",
    srcs = [":tests_lib"],
)
