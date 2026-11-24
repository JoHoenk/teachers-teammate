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
        "gui/test_gui_addons_tabs.py",
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
        "unit/test_addons_service.py",
        "unit/test_anonymization_service.py",
        "unit/test_anonymizer.py",
        "unit/test_api_keys.py",
        "unit/test_benchmark_domain.py",
        "unit/test_benchmark_run_store.py",
        "unit/test_benchmark_runner.py",
        "unit/test_benchmark_service.py",
        "unit/test_cache_service.py",
        "unit/test_cli.py",
        "unit/test_commands_service.py",
        "unit/test_config.py",
        "unit/test_coordinator.py",
        "unit/test_correction_options.py",
        "unit/test_documents_service.py",
        "unit/test_docx_builder.py",
        "unit/test_docx_comments.py",
        "unit/test_export_service.py",
        "unit/test_file_discovery.py",
        "unit/test_file_processor.py",
        "unit/test_gpu_detector.py",
        "unit/test_model_catalog.py",
        "unit/test_model_listing.py",
        "unit/test_ollama_address.py",
        "unit/test_ollama_discovery.py",
        "unit/test_ollama_utils.py",
        "unit/test_preprocessor.py",
        "unit/test_provider_service.py",
        "unit/test_recommendation_service.py",
        "unit/test_run_service.py",
        "unit/test_settings_schema.py",
        "unit/test_setup_plan.py",
        "unit/test_setup_service.py",
        "unit/test_stage_builder.py",
        "unit/test_state_repository.py",
    ],
    visibility = ["//docs:__pkg__"],
)

# The BUILD file itself, for //tests:test_test_targets: it holds the targets to the folders.
exports_files(["BUILD"])

# ── Shared library used by lint targets ───────────────────────────────────

# Every test source, for //:py_pylint (one action over the whole first-party tree). Not a test.
filegroup(
    name = "all_sources",
    srcs = glob(["**/*.py"]),
    visibility = ["//visibility:public"],
)

# Every test file of the package, so //tests:test_test_targets can hold them to the targets that
# run them (a target's own data names only what it runs).
filegroup(
    name = "test_sources",
    srcs = glob(["**/test_*.py"]),
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
            "e2e/*.py",
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
    "unit/_service_helpers.py",
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
# platformdirs: the app reads its folders from it (``infrastructure/storage_root.py``), so a
# test that imports the app has it.
_COMMON_DEPS = [
    "@ocr_test_dependencies//pydantic:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
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

# The developer tools' shared deps.  lxml and markdown_it: text mode tidies the answer through
# ocr_formats, and the protocol fingerprint asks the app's corrector in both modes.
_TOOL_DEPS = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB

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

# Both sets, for the test that walks the app with the real reader and the real page models.
_MODEL_ENV = dict(list(_READER_MODEL_ENV.items()) + list(_PAGE_MODEL_ENV.items()))

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
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_config",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_config.py"],
    args = ["unit/test_config.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_settings_schema",
    size = "medium",
    srcs = _COMMON_SRCS + ["unit/test_settings_schema.py"],
    args = ["unit/test_settings_schema.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS +
           _GUI_DEPS +
           _PIPELINE_DEPS +
           _CLI_LIB + [
        "//teachers_teammate:gui_layer_lib",
    ],
)

py_test(
    name = "test_language",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_language.py"],
    args = ["unit/test_language.py"],
    data = ["//:requirements_addons_lock.txt"],
    env = {"ADDON_LOCK": "$(rlocationpath //:requirements_addons_lock.txt)"},
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_benchmark_domain",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_domain.py"],
    args = ["unit/test_benchmark_domain.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_benchmark_run_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_run_store.py"],
    args = ["unit/test_benchmark_run_store.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_benchmark_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_runner.py"],
    args = ["unit/test_benchmark_runner.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_benchmark_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_service.py"],
    args = ["unit/test_benchmark_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_cli",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cli.py"],
    args = ["unit/test_cli.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _CLI_LIB,
)

py_test(
    name = "test_cli_composition",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cli_composition.py"],
    args = ["unit/test_cli_composition.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _CLI_LIB,
)

py_test(
    name = "test_step_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_step_runner.py"],
    args = ["unit/test_step_runner.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_word_diff",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_word_diff.py"],
    args = ["unit/test_word_diff.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_sentences",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_sentences.py"],
    args = ["unit/test_sentences.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_text_loss",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_text_loss.py"],
    args = ["unit/test_text_loss.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_edits",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_edits.py"],
    args = ["unit/test_edits.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_review_v2",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_review_v2.py"],
    args = ["unit/test_review_v2.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

# The catalogue of messages: one factory per key in ``messages.py``, one renderer per key
# in ``texts/messages.py``, and a golden list of the key values a stored result keeps.
py_test(
    name = "test_messages",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_messages.py"],
    args = ["unit/test_messages.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB + _TEXTS_LIB,
)

py_test(
    name = "test_markdown_shield",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_markdown_shield.py"],
    args = ["unit/test_markdown_shield.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_offset_map",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_offset_map.py"],
    args = ["unit/test_offset_map.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_check_items",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_items.py"],
    args = ["unit/test_check_items.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_merged_view",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_merged_view.py"],
    args = ["unit/test_merged_view.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_check_document",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_document.py"],
    args = ["unit/test_check_document.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_teacher_work",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_teacher_work.py"],
    args = ["unit/test_teacher_work.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_atomic_file",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_atomic_file.py"],
    args = ["unit/test_atomic_file.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_teacher_work_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_teacher_work_store.py"],
    args = ["unit/test_teacher_work_store.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

# A service is built in one place, so every adapter shares its caches.
py_test(
    name = "test_service_construction",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_service_construction.py"],
    args = ["unit/test_service_construction.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# Every reader shows the merged view, never a DocumentState text of its own.
py_test(
    name = "test_teacher_work_readers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_teacher_work_readers.py"],
    args = ["unit/test_teacher_work_readers.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_quotes",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_quotes.py"],
    args = ["unit/test_quotes.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_correction_prompts_steps",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_correction_prompts_steps.py"],
    args = ["unit/test_correction_prompts_steps.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_exchange_schemas",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_exchange_schemas.py"],
    args = ["unit/test_exchange_schemas.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_exchange_corrector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_exchange_corrector.py"],
    args = ["unit/test_exchange_corrector.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_exchange_text_first",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_exchange_text_first.py"],
    args = ["unit/test_exchange_text_first.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_request_profile",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_request_profile.py"],
    args = ["unit/test_request_profile.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_evaluation",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_evaluation.py"],
    args = ["unit/test_evaluation.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_input_providers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_input_providers.py"],
    args = ["unit/test_input_providers.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _IMAGE_DEPS + _PDF_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_llm_factory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_llm_factory.py"],
    args = ["unit/test_llm_factory.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

# The LangChain invocation helpers: which errors count as the provider rejecting a request.
py_test(
    name = "test_llm_invoke",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_llm_invoke.py"],
    args = ["unit/test_llm_invoke.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ocr_processor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_processor.py"],
    args = ["unit/test_ocr_processor.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _IMAGE_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ocr_formats",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_formats.py"],
    args = ["unit/test_ocr_formats.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_ocr_text_cleaner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_text_cleaner.py"],
    args = ["unit/test_ocr_text_cleaner.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preprocessor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preprocessor.py"],
    args = ["unit/test_preprocessor.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _IMAGE_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ollama_server",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_server.py"],
    args = ["unit/test_ollama_server.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_ollama_address",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_address.py"],
    args = ["unit/test_ollama_address.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
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
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_ollama_install",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_install.py"],
    args = ["unit/test_ollama_install.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ollama_utils",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_utils.py"],
    args = ["unit/test_ollama_utils.py"],
    main = "run_test.py",
    tags = ["unit"],
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
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + ["@ocr_test_dependencies//ollama:pkg"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_providers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_providers.py"],
    args = ["unit/test_providers.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

# The engines of text recognition, their models, and the reader's model files.
py_test(
    name = "test_reader_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_reader_service.py"],
    args = ["unit/test_reader_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# Personal-info removal: the anonymizer cache, the anonymized texts and the spaCy models.
py_test(
    name = "test_anonymization_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_anonymization_service.py"],
    args = ["unit/test_anonymization_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

# The keys of the online services: what the app holds, and what the environment has.
py_test(
    name = "test_api_keys",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_api_keys.py"],
    args = ["unit/test_api_keys.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# This computer's hardware, the model catalog, the classifications and the ratings.
py_test(
    name = "test_recommendation_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_recommendation_service.py"],
    args = ["unit/test_recommendation_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

# What each provider is, needs and offers, cached per provider and address.
py_test(
    name = "test_provider_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_provider_service.py"],
    args = ["unit/test_provider_service.py"],
    main = "run_test.py",
    # It builds the whole set of services to check a provider, so the pipeline comes with it.
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_providers_mistral_curation",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_providers_mistral_curation.py"],
    args = ["unit/test_providers_mistral_curation.py"],
    data = ["//tests/fixtures:mistral_models.json"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_pipeline_txt_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_txt.py"],
    args = ["integration/test_pipeline_txt.py"],
    main = "run_test.py",
    tags = ["integration"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_pipeline_preview_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_preview.py"],
    args = ["integration/test_pipeline_preview.py"],
    main = "run_test.py",
    tags = ["integration"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_docx_builder",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_docx_builder.py"],
    args = ["unit/test_docx_builder.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_docx_comments",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_docx_comments.py"],
    args = ["unit/test_docx_comments.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

# The library internals the app reaches into, against the versions it is locked to.
py_test(
    name = "test_library_internals",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_library_internals.py"],
    args = ["unit/test_library_internals.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + [
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//python_docx:pkg",
    ],
)

py_test(
    name = "test_pipeline_run_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_run.py"],
    args = ["integration/test_pipeline_run.py"],
    main = "run_test.py",
    tags = ["integration"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_storage_policy",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_storage_policy.py"],
    args = ["unit/test_storage_policy.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_stored_results",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_stored_results.py"],
    args = ["unit/test_stored_results.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_data_location",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_data_location.py"],
    args = ["unit/test_data_location.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB + _TEXTS_LIB,
)

py_test(
    name = "test_sent_text",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_sent_text.py"],
    args = ["unit/test_sent_text.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_domain_policy",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_domain_policy.py"],
    args = ["unit/test_domain_policy.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_check_linux_bundle",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_linux_bundle.py"],
    args = ["unit/test_check_linux_bundle.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_check_macos_bundle",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_macos_bundle.py"],
    args = ["unit/test_check_macos_bundle.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_check_desktop_entry",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_desktop_entry.py"],
    args = ["unit/test_check_desktop_entry.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/packaging:packaging_tools_lib"],
)

# The groups CI runs on separate machines (tools/ci/target_groups.py): every test in exactly one.
py_test(
    name = "test_target_groups",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_target_groups.py"],
    args = ["unit/test_target_groups.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/ci:ci_tools_lib"],
)

py_test(
    name = "test_latest_release",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_latest_release.py"],
    args = ["unit/test_latest_release.py"],
    main = "run_test.py",
    tags = ["unit"],
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
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_run_gui",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_gui.py"],
    args = ["unit/test_run_gui.py"],
    data = ["//tools/build:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_windows_installer_script",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_windows_installer_script.py"],
    args = ["unit/test_windows_installer_script.py"],
    data = [
        "//docs:code_signing_policy.md",
        "//tools/packaging:windows.nsi",
    ],
    env = {
        "CODE_SIGNING_POLICY": "$(rlocationpath //docs:code_signing_policy.md)",
        "WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)",
    },
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_packaging_walkthrough",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_packaging_walkthrough.py"],
    args = ["unit/test_packaging_walkthrough.py"],
    data = [
        "//tools/packaging:test_deb_install.sh",
        "//tools/packaging:test_macos_install.sh",
        "//tools/packaging:test_windows_installer.ps1",
    ],
    env = {
        "DEB_INSTALL_SCRIPT": "$(rlocationpath //tools/packaging:test_deb_install.sh)",
        "MACOS_INSTALL_SCRIPT": "$(rlocationpath //tools/packaging:test_macos_install.sh)",
        "WINDOWS_INSTALLER_SCRIPT": "$(rlocationpath //tools/packaging:test_windows_installer.ps1)",
    },
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_addon_constraints",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_constraints.py"],
    args = ["unit/test_addon_constraints.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_texts_types",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_texts_types.py"],
    args = ["unit/test_texts_types.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _TEXTS_LIB,
)

# The ratchet on class and function size over every layer: the sources come in as data because
# the test parses them with ``ast``.
py_test(
    name = "test_class_size",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_class_size.py"],
    args = ["unit/test_class_size.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_layer_imports",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_layer_imports.py"],
    args = ["unit/test_layer_imports.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_subprocess_env",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_subprocess_env.py"],
    args = ["unit/test_subprocess_env.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_thread_kinds",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_thread_kinds.py"],
    args = ["unit/test_thread_kinds.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# The application layer reaches into infrastructure only where the pinned table says it does.
py_test(
    name = "test_application_infrastructure_budget",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_application_infrastructure_budget.py"],
    args = ["unit/test_application_infrastructure_budget.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# The layer's lazy imports only go down: the count of `# noqa: PLC0415` is the ratchet.
py_test(
    name = "test_lazy_import_budget",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_lazy_import_budget.py"],
    args = ["unit/test_lazy_import_budget.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# The log file leaves the app: a line names a file and a stage, never a document's text.
py_test(
    name = "test_log_lines_carry_no_text",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_log_lines_carry_no_text.py"],
    args = ["unit/test_log_lines_carry_no_text.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# An `except` that only passes says nothing to whoever reads the log.
py_test(
    name = "test_silent_handlers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_silent_handlers.py"],
    args = ["unit/test_silent_handlers.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_logging_setup",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_logging_setup.py"],
    args = ["unit/test_logging_setup.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

# A stage word and the Ollama address are spelled in one module each.
py_test(
    name = "test_names_have_one_home",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_names_have_one_home.py"],
    args = ["unit/test_names_have_one_home.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_user_text_centralised",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_user_text_centralised.py"],
    args = ["unit/test_user_text_centralised.py"],
    data = ["//teachers_teammate:user_text_sources"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/user_text:user_text_lib"] + _PKG_LIB,
)

py_test(
    name = "test_inventory_tool",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_inventory_tool.py"],
    args = ["unit/test_inventory_tool.py"],
    main = "run_test.py",
    tags = ["unit"],
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
        "//:LICENSE",
        "//:NOTICE",
        "//:license_store",
        "//:pyproject.toml",
        "//:requirements_addons_lock.txt",
        "//:requirements_lock.txt",
        "//teachers_teammate:assets",
        "//tools/build:requirements_addons_platform.txt",
        "//tools/build:requirements_platform.txt",
    ],
    env = {"LICENSE_PYPROJECT": "$(rlocationpath //:pyproject.toml)"},
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + [
        "//tools/licenses:artifact_checks_lib",
        "//tools/licenses:licenses_lib",
        "//tools/release:release_tools_lib",
    ] + _INFRASTRUCTURE_LIB,
)

# The targets of this file: one per test file, each tagged by its folder, which is what the
# suites below take.
py_test(
    name = "test_test_targets",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_test_targets.py"],
    args = ["unit/test_test_targets.py"],
    data = [
        "BUILD",
        ":test_sources",
    ],
    env = {"TESTS_BUILD": "$(rlocationpath BUILD)"},
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# The three locks against one another: a package two of them pin differently is a conflict no
# single pip-compile run can see.
py_test(
    name = "test_lock_agreement",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_lock_agreement.py"],
    args = ["unit/test_lock_agreement.py"],
    data = [
        "//:requirements_addons_lock.txt",
        "//:requirements_dev_lock.txt",
        "//:requirements_lock.txt",
    ],
    env = {"LOCKS_ROOT": "$(rlocationpath //:requirements_lock.txt)"},
    main = "run_test.py",
    tags = ["unit"],
    visibility = ["//:__pkg__"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_fetch_models",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_fetch_models.py"],
    args = ["unit/test_fetch_models.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + ["//tools/build:build_tools_lib"] + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_addon_lock",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_lock.py"],
    args = ["unit/test_addon_lock.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_check_pins",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_pins.py"],
    args = ["unit/test_check_pins.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"],
)

py_test(
    name = "test_make_constraints",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_make_constraints.py"],
    args = ["unit/test_make_constraints.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"],
)

py_test(
    name = "test_make_venv",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_make_venv.py"],
    args = ["unit/test_make_venv.py"],
    main = "run_test.py",
    tags = ["unit"],
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
    tags = ["unit"],
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
    tags = ["unit"],
    deps = _COMMON_DEPS + ["@ocr_test_dependencies//packaging:pkg"],
)

py_test(
    name = "test_check_smoke",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_check_smoke.py"],
    args = ["unit/test_check_smoke.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

# What a finished download carries, held against the tree: the test drives the comparison with a
# fixture folder, so the rule the build checks is the rule the test pins.
py_test(
    name = "test_artifact_licenses",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_artifact_licenses.py"],
    args = ["unit/test_artifact_licenses.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/licenses:artifact_checks_lib"],
)

py_test(
    name = "test_release_tag",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_release_tag.py"],
    args = ["unit/test_release_tag.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/release:release_tools_lib"] + _PKG_LIB,
)

py_test(
    name = "test_release_stamp_versions",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_release_stamp_versions.py"],
    args = ["unit/test_release_stamp_versions.py"],
    main = "run_test.py",
    tags = ["unit"],
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
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_interfaces",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_interfaces.py"],
    args = ["unit/test_interfaces.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CORE_LIB,
)

py_test(
    name = "test_run_rules",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_rules.py"],
    args = ["unit/test_run_rules.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CORE_LIB,
)

py_test(
    name = "test_run_summary",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_summary.py"],
    args = ["unit/test_run_summary.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_power",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_power.py"],
    args = ["unit/test_power.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_state_repository",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_state_repository.py"],
    args = ["unit/test_state_repository.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_export_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_export_service.py"],
    args = ["unit/test_export_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The run: its files, its requirements and its summary.
py_test(
    name = "test_run_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_service.py"],
    args = ["unit/test_run_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB + _TEXTS_LIB,
)

# What the store says about a document.
py_test(
    name = "test_documents_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_documents_service.py"],
    args = ["unit/test_documents_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The writes: the teacher's texts and decisions.
py_test(
    name = "test_commands_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_commands_service.py"],
    args = ["unit/test_commands_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# Where each step of a run takes place.
py_test(
    name = "test_data_flow_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_data_flow_service.py"],
    args = ["unit/test_data_flow_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_setup_plan",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_setup_plan.py"],
    args = ["unit/test_setup_plan.py"],
    main = "run_test.py",
    # ``application.setup_plan`` also holds ``SetupService``, which asks the reader service and
    # so the pipeline (the docx builder included).
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_addon_pins",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_pins.py"],
    args = ["unit/test_addon_pins.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + ["//tools/build:build_tools_lib"],
)

py_test(
    name = "test_addon_requirements",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_requirements.py"],
    args = ["unit/test_addon_requirements.py"],
    data = [
        "//:pyproject.toml",
        "//:requirements_addons_lock.txt",
    ],
    env = {
        "ADDON_LOCK": "$(rlocationpath //:requirements_addons_lock.txt)",
        "PYPROJECT": "$(rlocationpath //:pyproject.toml)",
    },
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_addon_manager",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_manager.py"],
    args = ["unit/test_addon_manager.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

# The proofreading options the GUI shows: the steps that run and the notation note.
py_test(
    name = "test_correction_options",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_correction_options.py"],
    args = ["unit/test_correction_options.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The Setup Assistant's facts and plan: what it asks the computer, and which settings file a
# first start reads.
py_test(
    name = "test_setup_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_setup_service.py"],
    args = ["unit/test_setup_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The add-ons: what of them is installed, and the installs themselves.
py_test(
    name = "test_addons_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addons_service.py"],
    args = ["unit/test_addons_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_spacy_models",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_spacy_models.py"],
    args = ["unit/test_spacy_models.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

# The smoke test imports every lazily loaded module of the app, so it needs the whole runtime
# set — including the packages no other unit test imports (ollama, keyring).
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
    tags = ["unit"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//keyring:pkg",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

py_test(
    name = "test_gpu_detector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_gpu_detector.py"],
    args = ["unit/test_gpu_detector.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_diagnostics",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_diagnostics.py"],
    args = ["unit/test_diagnostics.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_speed_estimates",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_speed_estimates.py"],
    args = ["unit/test_speed_estimates.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_speed_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_speed_store.py"],
    args = ["unit/test_speed_store.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_run_stats",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_run_stats.py"],
    args = ["unit/test_run_stats.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_performance_probe",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_performance_probe.py"],
    args = ["unit/test_performance_probe.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _IMAGE_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_performance_estimate",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_performance_estimate.py"],
    args = ["unit/test_performance_estimate.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_model_catalog",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_model_catalog.py"],
    args = ["unit/test_model_catalog.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_chunking_corrector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_chunking_corrector.py"],
    args = ["unit/test_chunking_corrector.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_text_chunks",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_text_chunks.py"],
    args = ["unit/test_text_chunks.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

# The core layer of the new evaluation tooling: the formats round-trip, an unknown field survives a
# read and a write, a changed reference moves the key hash and not the question hash, character
# offsets round-trip through the tokenizer, and the axes build the compare key and the file name.
py_test(
    name = "test_evaluation_core",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_core.py"],
    args = ["tools/test_evaluation_core.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
    ],
)

# The one scoring path: its numbers are pinned as golden values, and the fallback rule, the marks a
# model added and an accepted alternative of the key are counted as the report counts them.
py_test(
    name = "test_evaluation_score",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_score.py"],
    args = ["tools/test_evaluation_score.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _DOMAIN_LIB + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
    ],
)

# The input layer: the corpora, the labels of their M2 files, and the manifest.  The M2 files are
# written in the test, so no learner corpus has to be present; the one corpus that is in the
# repository (invented-fr) is read as it is.
py_test(
    name = "test_evaluation_input",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_input.py"],
    args = ["tools/test_evaluation_input.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:metrics",
    ],
)

# The test-set builder: a committed set is never edited, and importing the builder does not load
# the app.  The recipe of the fake set is added to the registry, so no learner corpus is needed.
py_test(
    name = "test_evaluation_prepare_testset",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_prepare_testset.py"],
    args = ["tools/test_evaluation_prepare_testset.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools:prepare_gec_testset_lib",
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:store",
    ],
)

# The store layer: the layout, the reader and the writer, every rule of the format, and the marks.
# It reads and writes files and nothing above it.
py_test(
    name = "test_evaluation_store",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_store.py"],
    args = ["tools/test_evaluation_store.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:metrics",
        "//tools/evaluation:store",
    ],
)

# The converter: the old folders read as they are.  The unit tests build a small old store; the
# acceptance tests run over the real folders and are skipped unless GEC_RESULTS names them and
# GEC_EVALS the private store that holds the manifests.
py_test(
    name = "test_evaluation_convert_v1",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_convert_v1.py"],
    args = ["tools/test_evaluation_convert_v1.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _DOMAIN_LIB + _FORMAT_DEPS + [
        "//tools/evaluation:convert",
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The report layer's foundation: the loader and its score cache, the groups, the summaries and the
# paired bootstrap.  The store is built in the test, so the old package is not a dependency here.
py_test(
    name = "test_evaluation_report",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_report.py"],
    args = ["tools/test_evaluation_report.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The report layer's tables and `compare --along`: the by-error view, the sweep, the passes, the
# false alarms, the performance, and the axis selection a comparison reads.  The store is built in
# the test, so the old package is not a dependency here.
py_test(
    name = "test_evaluation_report_tables",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_report_tables.py"],
    args = ["tools/test_evaluation_report_tables.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The report's views: the model and band tables, the selection of the runs a command reads, and the
# spot-check sheet a teacher marks.  The store is built in the test, so the old package is not a
# dependency here.
py_test(
    name = "test_evaluation_report_views",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_report_views.py"],
    args = ["tools/test_evaluation_report_views.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The marks workflow end to end: a sheet printed from a stored run, filled in, read back as marks,
# checked, and then counted by the gate or turned into the key's accepted alternatives.  The old
# blind sheet's key is imported the same way.  The store is built in the test.
py_test(
    name = "test_evaluation_marks_workflow",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_marks_workflow.py"],
    args = ["tools/test_evaluation_marks_workflow.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:convert",
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The four command lines, one block per tool: the temperature a gate reads, the series a command
# chooses, the options a command refuses before printing, and what ``validate`` says with its exit
# code.  The store is built in the test, so no file on disk is read.
py_test(
    name = "test_evaluation_cli",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_cli.py"],
    args = ["tools/test_evaluation_cli.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools:evaluate",
        "//tools:prepare_gec_testset",
        "//tools:report",
        "//tools:store",
        "//tools:verify_model_catalog",
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The page's model: the texts of a store keyed by their test set, so two sets that both hold
# ``de-01`` are two texts, each diffed against its own source.  The store is built in the test.
py_test(
    name = "test_evaluation_viewer",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_viewer.py"],
    args = ["tools/test_evaluation_viewer.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
        "//tools/evaluation:viewer",
    ],
)

# Gate 2's floors: the outcome of one model on its own numbers, per language, and the explanation
# check a teacher's marks decide.  The store is built in the test, so the old package is not a
# dependency here.
py_test(
    name = "test_evaluation_gates",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_gates.py"],
    args = ["tools/test_evaluation_gates.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The run layer's log: one line per thing that happened, so a long run can be read afterwards.
py_test(
    name = "test_evaluation_run_log",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_run_log.py"],
    args = ["tools/test_evaluation_run_log.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + ["//tools/evaluation:run"],
)

# The client of the graphics-memory agent: the wire format, the windows and the errors, against a
# server the test starts itself.
py_test(
    name = "test_evaluation_agent_client",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_agent_client.py"],
    args = ["tools/test_evaluation_agent_client.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + ["//tools/evaluation:run"],
)

# The graphics-memory agent itself: the script it writes, the interpreter it picks and the samples
# it reads back.  Nothing here starts a real agent.
py_test(
    name = "test_evaluation_gpu_agent",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_gpu_agent.py"],
    args = ["tools/test_evaluation_gpu_agent.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + ["//tools/evaluation:run"],
)

# The protocol table and the stamp a run is made with: the fingerprints, the versions and what a
# changed table refuses.
py_test(
    name = "test_evaluation_protocol",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_protocol.py"],
    args = ["tools/test_evaluation_protocol.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# What a run measures: the header, the session line, the sampler record and the performance of an
# answer.
py_test(
    name = "test_evaluation_measure",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_measure.py"],
    args = ["tools/test_evaluation_measure.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The sessions of one run (the header file and the resume check) and the layering of the offline
# modules.
py_test(
    name = "test_evaluation_session",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_session.py"],
    args = ["tools/test_evaluation_session.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The run loop: the planning, the resume check, the seed per repeat, the warm-up, the header and the
# sampler, with every seam replaced.
py_test(
    name = "test_evaluation_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_runner.py"],
    args = ["tools/test_evaluation_runner.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The path a run walks, end to end: a scripted corrector writes a run file, the report scores it,
# ``compare`` and ``gate`` read it and the check passes it.  The store is built in the test and no
# request leaves it.
py_test(
    name = "test_evaluation_workflow",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_workflow.py"],
    args = ["tools/test_evaluation_workflow.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The new tools against the reports the old ones froze: the same numbers, or a listed difference.
# The comparison runs everywhere on tables written in the test; the frozen reports are read only
# when GEC_RESULTS and GEC_REFERENCE name them.
py_test(
    name = "test_evaluation_reference",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_reference.py"],
    args = ["tools/test_evaluation_reference.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools:report",
        "//tools/evaluation:core",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The comparison that writes an answer as it arrives: the chunking, the fallback and the failure.
py_test(
    name = "test_evaluation_comparison",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_comparison.py"],
    args = ["tools/test_evaluation_comparison.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The progress display: the line, the estimate, the waiting thread and the wiring into the loop.
# The runner's own helpers are shared, so its test file is a source here too.
py_test(
    name = "test_evaluation_progress",
    size = "small",
    srcs = _COMMON_SRCS + [
        "tools/test_evaluation_progress.py",
        "tools/test_evaluation_runner.py",
    ],
    args = ["tools/test_evaluation_progress.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The app's own corrector as the run layer builds it, and the guard that it stays the app's: the
# test also builds the app's corrector through the stage builder, which needs the pipeline
# libraries.
py_test(
    name = "test_evaluation_app_corrector",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_app_corrector.py"],
    args = ["tools/test_evaluation_app_corrector.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _LANGCHAIN_DEPS + _IMAGE_DEPS + _PDF_DEPS + _CV_DEPS +
           _REQUESTS_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "@ocr_test_dependencies//python_docx:pkg",
    ],
)

# The pass/fail rule a check is judged by: the verdicts, the thresholds and the retry ladder.
py_test(
    name = "test_evaluation_check_rule",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_check_rule.py"],
    args = ["tools/test_evaluation_check_rule.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + ["//tools/evaluation:checks"],
)

# Gate 1: the saved answers, the smoke gate that opens them and the first try judged from them.
py_test(
    name = "test_evaluation_gate_one",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_gate_one.py"],
    args = ["tools/test_evaluation_gate_one.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The machines the evaluations ran on: the registry, its ids, and what it refuses.
py_test(
    name = "test_evaluation_machines",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_machines.py"],
    args = ["tools/test_evaluation_machines.py"],
    data = ["//evaluations:records"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + ["//tools/evaluation:core"],
)

# The model fit: whether a model's window, its memory and its answer length suit the app.
py_test(
    name = "test_evaluation_model_fit",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_model_fit.py"],
    args = ["tools/test_evaluation_model_fit.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + [
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The smoke check: the fixtures, the steps it walks, the decision it reaches, and the command
# line that runs it (`verify_model_catalog smoke`).  Medium: the replay of the saved gate-1
# answers alone takes half a minute.
py_test(
    name = "test_evaluation_smoke",
    size = "medium",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_smoke.py"],
    args = ["tools/test_evaluation_smoke.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools:verify_model_catalog",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The structured output check: the fixtures, the review budget and the margin it warns about.
py_test(
    name = "test_evaluation_structured_check",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_structured_check.py"],
    args = ["tools/test_evaluation_structured_check.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The command line of ``verify_model_catalog structured-output``: the header, the default window,
# the saved answers and the re-run that judges them again.
py_test(
    name = "test_evaluation_verify_model_catalog",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_verify_model_catalog.py"],
    args = ["tools/test_evaluation_verify_model_catalog.py"],
    data = ["//evaluations:records"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + [
        "//tools:verify_model_catalog",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The developer tool that asks the global and the EU Mistral endpoint which models they serve: the
# comparison and the command line, with the endpoints' answers faked.
py_test(
    name = "test_compare_mistral_regions",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_compare_mistral_regions.py"],
    args = ["tools/test_compare_mistral_regions.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB + ["//tools:compare_mistral_regions_lib"],
)

# The developer tool that measures what a reading model costs on one page: the pure parts, so no
# server and no page image.  The tool itself imports the image preprocessor, hence the CV deps.
py_test(
    name = "test_ocr_page_tokens",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_ocr_page_tokens.py"],
    args = ["tools/test_ocr_page_tokens.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _TOOL_DEPS + _CV_DEPS + _IMAGE_DEPS + ["//tools:ocr_page_tokens"],
)

# One spelling per fact: the text id, the shared words (``ollama``, ``errors``, ``correct``) and the
# host of a run each live once, in ``core``.  The search reads the syntax tree, so a docstring that
# explains a rule is not a second copy of it.
py_test(
    name = "test_evaluation_shared_names",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_shared_names.py"],
    args = ["tools/test_evaluation_shared_names.py"],
    data = ["//tools:all_sources"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS,
)

# The layers of the evaluation tooling: an import that reaches above its own layer fails here, with
# the file and the line, instead of failing the build with the name of a target.
py_test(
    name = "test_evaluation_layers",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_evaluation_layers.py"],
    args = ["tools/test_evaluation_layers.py"],
    data = ["//tools:all_sources"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS,
)

# Every tool binary starts: `--help` exits 0, and so does each subcommand's `--help`.  The binaries
# are data, so the test starts them the way a person does, with the runfiles Bazel gives them.
py_test(
    name = "test_tool_binaries",
    size = "medium",
    srcs = _COMMON_SRCS + ["tools/test_tool_binaries.py"],
    args = ["tools/test_tool_binaries.py"],
    data = [
        "//tools:compare_mistral_regions",
        "//tools:evaluate",
        "//tools:extract_test_specs",
        "//tools:ocr_page_tokens",
        "//tools:prepare_gec_testset",
        "//tools:report",
        "//tools:store",
        "//tools:verify_model_catalog",
        "//tools/evaluation:gpu_agent",
    ],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS,
)

# The sources mirror, without the network: the listings, the digests, the set and the gh commands.
py_test(
    name = "test_mirror_sources",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_mirror_sources.py"],
    args = ["tools/test_mirror_sources.py"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + ["//tools/release:release_tools_lib"],
)

# The records live in a repository of their own: no run file, manifest, size record, machine or
# marks file is in this one, because they hold the corpora's text.
py_test(
    name = "test_no_eval_records",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_no_eval_records.py"],
    args = ["tools/test_no_eval_records.py"],
    data = [
        ":all_sources",
        "//:AGENTS.md",
        "//docs:markdown",
        "//evaluations:records",
        "//teachers_teammate:all_sources",
        "//tools:all_sources",
    ],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS,
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
        "//:repo_config",
        "//docs:markdown",
        "//evaluations:records",
        "//teachers_teammate:all_sources",
        "//tools:all_sources",
    ],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _PKG_LIB,
)

py_test(
    name = "test_context_budget",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_context_budget.py"],
    args = ["unit/test_context_budget.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_model_listing",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_model_listing.py"],
    args = ["unit/test_model_listing.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_cache_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cache_service.py"],
    args = ["unit/test_cache_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_cache_keys",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cache_keys.py"],
    args = ["unit/test_cache_keys.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CORE_LIB,
)

py_test(
    name = "test_cache_keys_golden",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cache_keys_golden.py"],
    args = ["unit/test_cache_keys_golden.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_file_processor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_file_processor.py"],
    args = ["unit/test_file_processor.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preview_image_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preview_image_store.py"],
    args = ["unit/test_preview_image_store.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_input_provider_factory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_input_provider_factory.py"],
    args = ["unit/test_input_provider_factory.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PDF_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ocr_stage_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_stage_service.py"],
    args = ["unit/test_ocr_stage_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_preprocess_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preprocess_service.py"],
    args = ["unit/test_preprocess_service.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_stage_builder",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_stage_builder.py"],
    args = ["unit/test_stage_builder.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _APPLICATION_LIB,
)

py_test(
    name = "test_coordinator",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_coordinator.py"],
    args = ["unit/test_coordinator.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _FORMAT_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

# A GUI test builds real widgets and a real window, and a loaded runner runs a dozen of them at
# once: the 60 s a ``small`` test gets is not enough headroom, so every ``test_gui_*`` is
# ``medium``.  A timeout is a flake, and a flake hides the failure it should report.
py_test(
    name = "test_gui_config_panel",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/test_gui_config_panel.py",
    ],
    args = ["gui/test_gui_config_panel.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_readiness",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_readiness.py"],
    args = ["gui/test_gui_readiness.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_page",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_check_page.py"],
    args = ["gui/test_gui_check_page.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_run_start",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_run_start.py",
    ],
    args = ["gui/test_gui_run_start.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS +
           _GUI_DEPS +
           _PIPELINE_DEPS +
           _CLI_LIB + [
        "//teachers_teammate:gui_layer_lib",
    ],
)

py_test(
    name = "test_gui_results_table",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_results_table.py"],
    args = ["gui/test_gui_results_table.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_what_is_sent",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_what_is_sent.py"],
    args = ["gui/test_gui_what_is_sent.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_controller",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_check_controller.py",
    ],
    args = ["gui/test_gui_check_controller.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_main_window",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_main_window.py",
    ],
    args = ["gui/test_gui_main_window.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_long_job",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_long_job.py"],
    args = ["gui/test_gui_long_job.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_ollama_status_line",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_ollama_status_line.py"],
    args = ["gui/test_gui_ollama_status_line.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_worker",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_worker.py",
    ],
    args = ["gui/test_gui_worker.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_prompt_dialog",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_settings_stubs.py",
        "gui/test_gui_prompt_dialog.py",
    ],
    args = ["gui/test_gui_prompt_dialog.py"],
    main = "run_test.py",
    tags = ["gui"],
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
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_general_page",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_general_page.py",
    ],
    args = ["gui/test_gui_general_page.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_run_session",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_run_session.py"],
    args = ["gui/test_gui_run_session.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_run_status",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_run_status.py"],
    args = ["gui/test_gui_run_status.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_settings_pages",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_settings_pages.py",
    ],
    args = ["gui/test_gui_settings_pages.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_settings_window",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_settings_window.py",
    ],
    args = ["gui/test_gui_settings_window.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_setup_assistant",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_setup_assistant.py",
    ],
    args = ["gui/test_gui_setup_assistant.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_setup_flow",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_setup_flow.py"],
    args = ["gui/test_gui_setup_flow.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

# The whole wizard, end to end: the real pages, the real service, real threads, a real HTTP
# conversation with a fake Ollama and the real MainWindow writing a real ocr.toml.
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
    tags = [
        "gui",
        "walkthrough",
    ],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

py_test(
    name = "test_gui_setup_pages",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_setup_pages.py",
    ],
    args = ["gui/test_gui_setup_pages.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_plain_map",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_plain_map.py"],
    args = ["gui/test_gui_plain_map.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_style",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_check_style.py"],
    args = ["gui/test_gui_check_style.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_text_views",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_text_views.py"],
    args = ["gui/test_gui_text_views.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_check_body",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_check_body.py"],
    args = ["gui/test_gui_check_body.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_side_panel",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_side_panel.py"],
    args = ["gui/test_gui_side_panel.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_window_geometry",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
        "gui/test_gui_window_geometry.py",
    ],
    args = ["gui/test_gui_window_geometry.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_scan_view",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_scan_view.py"],
    args = ["gui/test_gui_scan_view.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_formatted_text",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_formatted_text.py"],
    args = ["gui/test_gui_formatted_text.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_chart_widget",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_chart_widget.py"],
    args = ["gui/test_gui_chart_widget.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_widgets",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_widgets.py"],
    args = ["gui/test_gui_widgets.py"],
    data = ["//teachers_teammate:assets"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_anonymizer",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_anonymizer.py"],
    args = ["unit/test_anonymizer.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_gui_anonymizer_widget",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_settings_stubs.py",
        "gui/test_gui_anonymizer_widget.py",
    ],
    args = ["gui/test_gui_anonymizer_widget.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_privacy_settings",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_privacy_settings.py",
    ],
    args = ["gui/test_gui_privacy_settings.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_anonymization_mask",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_anonymization_mask.py"],
    args = ["unit/test_anonymization_mask.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_gui_privacy_view",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_privacy_view.py",
    ],
    args = ["gui/test_gui_privacy_view.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_dialogs",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_dialogs.py"],
    args = ["gui/test_gui_dialogs.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_qsettings",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_qsettings.py"],
    args = ["gui/test_qsettings.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_addon_installer_dialog",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_addon_installer_dialog.py",
    ],
    args = ["gui/test_gui_addon_installer_dialog.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_update_check",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_update_check.py"],
    args = ["gui/test_gui_update_check.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_ocr_config_selector",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_ocr_config_selector.py"],
    args = ["gui/test_ocr_config_selector.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_speed_panel",
    size = "medium",
    srcs = _COMMON_SRCS + ["gui/test_gui_speed_panel.py"],
    args = ["gui/test_gui_speed_panel.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_hardware_profile_selector",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_hardware_profile_selector.py"],
    args = ["gui/test_hardware_profile_selector.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_background",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_background.py"],
    args = ["gui/test_background.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_model_item_delegate",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_model_item_delegate.py"],
    args = ["gui/test_model_item_delegate.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_model_wording",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_model_wording.py"],
    args = ["gui/test_model_wording.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_install_round",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_install_round.py",
    ],
    args = ["gui/test_gui_install_round.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_addons_tabs",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_addons_tabs.py",
    ],
    args = ["gui/test_gui_addons_tabs.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_addons_page",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "fakes/fake_ollama.py",
        "gui/_gui_harness.py",
        "gui/test_gui_addons_page.py",
    ],
    args = ["gui/test_gui_addons_page.py"],
    main = "run_test.py",
    tags = ["gui"],
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
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_lifecycle",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_lifecycle.py",
    ],
    args = ["gui/test_gui_lifecycle.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_gui_bootstrap",
    size = "medium",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_gui_bootstrap.py",
    ],
    args = ["gui/test_gui_bootstrap.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_benchmark_widgets",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_widgets.py"],
    args = ["gui/test_benchmark_widgets.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_benchmark_worker",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_worker.py"],
    args = ["gui/test_benchmark_worker.py"],
    main = "run_test.py",
    tags = ["gui"],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

py_test(
    name = "test_benchmark_integration",
    size = "small",
    srcs = _COMMON_SRCS + [
        "gui/_gui_harness.py",
        "gui/test_benchmark_integration.py",
    ],
    args = ["gui/test_benchmark_integration.py"],
    main = "run_test.py",
    tags = ["gui"],
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
    tags = ["integration"],
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
    tags = ["integration"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_install_item",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_install_item.py"],
    args = ["unit/test_install_item.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_local_models",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_local_models.py"],
    args = ["unit/test_local_models.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _DOMAIN_LIB,
)

py_test(
    name = "test_model_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_model_store.py"],
    args = ["unit/test_model_store.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_detect",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_detect.py"],
    args = ["unit/test_ppocr_detect.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_reading_order",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_reading_order.py"],
    args = ["unit/test_ppocr_reading_order.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_recognise",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_recognise.py"],
    args = ["unit/test_ppocr_recognise.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_ppocr_engine",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ppocr_engine.py"],
    args = ["unit/test_ppocr_engine.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS + _CV_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB + _TEXTS_LIB,
)

py_test(
    name = "test_ppocr_models",
    size = "medium",
    srcs = _COMMON_SRCS + ["integration/test_ppocr_models.py"],
    args = ["integration/test_ppocr_models.py"],
    data = ["//tests/fixtures:umlaut_line.png"] + _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    main = "run_test.py",
    tags = ["integration"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

py_test(
    name = "test_no_retired_engines",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_no_retired_engines.py"],
    args = ["tools/test_no_retired_engines.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["tools"],
    deps = _COMMON_DEPS + _PKG_LIB,
)

py_test(
    name = "test_no_sentence_review_in_the_app",
    size = "small",
    srcs = _COMMON_SRCS + ["tools/test_no_sentence_review_in_the_app.py"],
    args = ["tools/test_no_sentence_review_in_the_app.py"],
    data = ["//teachers_teammate:all_sources"],
    main = "run_test.py",
    tags = ["tools"],
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
    tags = ["unit"],
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
    tags = ["unit"],
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
    tags = ["integration"],
    deps = _COMMON_DEPS + _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

# The whole app as a teacher uses it: the walkthrough script builds the real main window, runs
# the real reader and page models over three documents, and checks what the app did.  Large
# because the run over the documents is the slow part.
py_test(
    name = "test_gui_main_walkthrough",
    size = "large",
    srcs = _COMMON_SRCS + [
        "e2e/__init__.py",
        "e2e/walk_checked.py",
        "e2e/walkthrough.py",
        "fakes/fake_ollama.py",
        "gui/test_gui_main_walkthrough.py",
    ],
    args = ["gui/test_gui_main_walkthrough.py"],
    data = _READER_MODEL_DATA + _PAGE_MODEL_DATA,
    env = _MODEL_ENV,
    main = "run_test.py",
    tags = [
        "gui",
        "walkthrough",
    ],
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        # The proofreading walk talks to the real ``ollama`` client.
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

# What that script may import: it also runs inside the shipped bundle, which carries no tests.
py_test(
    name = "test_walkthrough_imports",
    size = "small",
    srcs = _COMMON_SRCS + [
        "e2e/__init__.py",
        "e2e/walkthrough.py",
        "unit/test_walkthrough_imports.py",
    ],
    args = ["unit/test_walkthrough_imports.py"],
    main = "run_test.py",
    tags = ["unit"],
    deps = _COMMON_DEPS,
)

# The tests that play the whole app (tag ``walkthrough``). CI runs them on a machine of their own
# (tools/ci/target_groups.py), so a growing walkthrough never slows the other groups.
test_suite(
    name = "walkthrough_tests",
    tags = ["walkthrough"],
)

# ── Test suite ────────────────────────────────────────────────────────────

# One suite per folder of ``tests/``, taken from the tag each ``py_test`` carries: a new test
# lands in its folder's suite by itself, and no list has to be kept in step.
# ``tests/unit/test_test_targets.py`` holds the tags to the folders.
test_suite(
    name = "unit_tests",
    tags = ["unit"],
)

test_suite(
    name = "gui_tests",
    tags = ["gui"],
)

test_suite(
    name = "integration_tests",
    tags = ["integration"],
)

test_suite(
    name = "tools_tests",
    tags = ["tools"],
)

test_suite(
    name = "coverage_tests",
    tests = [
        ":gui_tests",
        ":tools_tests",
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
