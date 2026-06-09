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

load("//third_party/lint:linters.bzl", "pylint_test", "ruff_test", "ty_test")

# ── Test files tagged with @pytest.mark.use_case (for doc extraction) ────

filegroup(
    name = "use_case_test_files",
    srcs = [
        "gui/test_benchmark_integration.py",
        "gui/test_benchmark_widgets.py",
        "gui/test_benchmark_window.py",
        "gui/test_benchmark_worker.py",
        "integration/test_pipeline_preview.py",
        "integration/test_pipeline_run.py",
        "integration/test_pipeline_scenarios.py",
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
        "unit/test_docx_builder.py",
        "unit/test_gpu_detector.py",
        "unit/test_service.py",
        "unit/test_state_repository.py",
    ],
    visibility = ["//docs:__pkg__"],
)

# ── Shared library used by lint targets ───────────────────────────────────

# Full pip dep set for the lint aspects (ty/pylint) so test sources resolve
# their third-party imports. tests_lib is consumed only by lint targets, never
# the wheel, so depending on pip packages here is safe.
_TESTS_TYPED_DEPS = [
    "@ocr_test_dependencies//langchain_core:pkg",
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//opencv_python:pkg",
    "@ocr_test_dependencies//pillow:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pypdfium2:pkg",
    "@ocr_test_dependencies//pyside6:pkg",
    "@ocr_test_dependencies//pyside6_addons:pkg",
    "@ocr_test_dependencies//pyside6_essentials:pkg",
    "@ocr_test_dependencies//pytesseract:pkg",
    "@ocr_test_dependencies//pytest:pkg",
    "@ocr_test_dependencies//pytest_mock:pkg",
    "@ocr_test_dependencies//pytest_qt:pkg",
    "@ocr_test_dependencies//python_docx:pkg",
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
]

_COMMON_DEPS = [
    "//teachers_teammate:ocr_lib",
    "@ocr_test_dependencies//pytest:pkg",
    "@ocr_test_dependencies//pytest_mock:pkg",
]

_LANGCHAIN_DEPS = ["@ocr_test_dependencies//langchain_core:pkg"]

_IMAGE_DEPS = ["@ocr_test_dependencies//pillow:pkg"]

_PDF_DEPS = ["@ocr_test_dependencies//pypdfium2:pkg"]

_CV_DEPS = [
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//opencv_python:pkg",
]

# ── Individual py_test targets ────────────────────────────────────────────

_REQUESTS_DEPS = ["@ocr_test_dependencies//requests:pkg"]

_GUI_DEPS = [
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pyside6:pkg",
    "@ocr_test_dependencies//pyside6_addons:pkg",
    "@ocr_test_dependencies//pyside6_essentials:pkg",
    "@ocr_test_dependencies//shiboken6:pkg",
    "@ocr_test_dependencies//pytest_qt:pkg",
    "@ocr_test_dependencies//qdarkstyle:pkg",
]

# infrastructure/pipeline.py has module-level imports of docx_builder (→ python-docx),
# image_preprocessor (→ opencv-python), correction (→ langchain-core),
# ocr_processor + ollama_utils (→ requests), and input_providers (→ pypdfium2).
# Any test that imports from pipeline needs all of these.
_PIPELINE_DEPS = (
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
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_config",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_config.py"],
    args = ["unit/test_config.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_benchmark_domain",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_domain.py"],
    args = ["unit/test_benchmark_domain.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_benchmark_run_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_run_store.py"],
    args = ["unit/test_benchmark_run_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_benchmark_runner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_runner.py"],
    args = ["unit/test_benchmark_runner.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_benchmark_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_benchmark_service.py"],
    args = ["unit/test_benchmark_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_cli",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cli.py"],
    args = ["unit/test_cli.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_cli_composition",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cli_composition.py"],
    args = ["unit/test_cli_composition.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_correction",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_correction.py"],
    args = ["unit/test_correction.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS,
)

py_test(
    name = "test_evaluation",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_evaluation.py"],
    args = ["unit/test_evaluation.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS,
)

py_test(
    name = "test_input_providers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_input_providers.py"],
    args = ["unit/test_input_providers.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _IMAGE_DEPS + _PDF_DEPS,
)

py_test(
    name = "test_llm_factory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_llm_factory.py"],
    args = ["unit/test_llm_factory.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS,
)

py_test(
    name = "test_ocr_processor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_processor.py"],
    args = ["unit/test_ocr_processor.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS + _IMAGE_DEPS + _REQUESTS_DEPS,
)

py_test(
    name = "test_ocr_text_cleaner",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_text_cleaner.py"],
    args = ["unit/test_ocr_text_cleaner.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_preprocessor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preprocessor.py"],
    args = ["unit/test_preprocessor.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _IMAGE_DEPS + _CV_DEPS,
)

py_test(
    name = "test_ollama_utils",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ollama_utils.py"],
    args = ["unit/test_ollama_utils.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS,
)

py_test(
    name = "test_providers",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_providers.py"],
    args = ["unit/test_providers.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _LANGCHAIN_DEPS,
)

py_test(
    name = "test_pipeline_txt_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_txt.py"],
    args = ["integration/test_pipeline_txt.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_pipeline_preview_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_preview.py"],
    args = ["integration/test_pipeline_preview.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_pipeline_tesseract_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_tesseract.py"],
    args = ["integration/test_pipeline_tesseract.py"],
    main = "run_test.py",
    tags = [
        "manual",
        "needs_tesseract",
    ],
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_docx_builder",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_docx_builder.py"],
    args = ["unit/test_docx_builder.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_pipeline_run_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_run.py"],
    args = ["integration/test_pipeline_run.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_storage_policy",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_storage_policy.py"],
    args = ["unit/test_storage_policy.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_domain_policy",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_domain_policy.py"],
    args = ["unit/test_domain_policy.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_release_stamp_versions",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_release_stamp_versions.py"],
    args = ["unit/test_release_stamp_versions.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + ["//tools/release:release_tools_lib"],
)

py_test(
    name = "test_interfaces",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_interfaces.py"],
    args = ["unit/test_interfaces.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_state_repository",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_state_repository.py"],
    args = ["unit/test_state_repository.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_service.py"],
    args = ["unit/test_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_addon_manager",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_addon_manager.py"],
    args = ["unit/test_addon_manager.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_gpu_detector",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_gpu_detector.py"],
    args = ["unit/test_gpu_detector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_cache_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_cache_service.py"],
    args = ["unit/test_cache_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS,
)

py_test(
    name = "test_file_processor",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_file_processor.py"],
    args = ["unit/test_file_processor.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS,
)

py_test(
    name = "test_preview_image_store",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preview_image_store.py"],
    args = ["unit/test_preview_image_store.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_input_provider_factory",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_input_provider_factory.py"],
    args = ["unit/test_input_provider_factory.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PDF_DEPS,
)

py_test(
    name = "test_ocr_stage_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_ocr_stage_service.py"],
    args = ["unit/test_ocr_stage_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_preprocess_service",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_preprocess_service.py"],
    args = ["unit/test_preprocess_service.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_stage_builder",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_stage_builder.py"],
    args = ["unit/test_stage_builder.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

py_test(
    name = "test_coordinator",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_coordinator.py"],
    args = ["unit/test_coordinator.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _REQUESTS_DEPS,
)

py_test(
    name = "test_gui_config_panel",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_config_panel.py"],
    args = ["gui/test_gui_config_panel.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_results_table",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_results_table.py"],
    args = ["gui/test_gui_results_table.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_main_window",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_main_window.py"],
    args = ["gui/test_gui_main_window.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_worker",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_worker.py"],
    args = ["gui/test_gui_worker.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_settings_dialog",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_settings_dialog.py"],
    args = ["gui/test_gui_settings_dialog.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_widgets",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_widgets.py"],
    args = ["gui/test_gui_widgets.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_anonymizer",
    size = "small",
    srcs = _COMMON_SRCS + ["unit/test_anonymizer.py"],
    args = ["unit/test_anonymizer.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS,
)

py_test(
    name = "test_gui_anonymizer_dialog",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_anonymizer_dialog.py"],
    args = ["gui/test_gui_anonymizer_dialog.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_addon_installer_dialog",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_addon_installer_dialog.py"],
    args = ["gui/test_gui_addon_installer_dialog.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_gui_update_check",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_gui_update_check.py"],
    args = ["gui/test_gui_update_check.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_ocr_config_selector",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_ocr_config_selector.py"],
    args = ["gui/test_ocr_config_selector.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_benchmark_window",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_window.py"],
    args = ["gui/test_benchmark_window.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
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
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
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
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_benchmark_widgets",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_widgets.py"],
    args = ["gui/test_benchmark_widgets.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_benchmark_worker",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_worker.py"],
    args = ["gui/test_benchmark_worker.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_benchmark_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["gui/test_benchmark_integration.py"],
    args = ["gui/test_benchmark_integration.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_lib"],
)

py_test(
    name = "test_pipeline_scenarios_integration",
    size = "small",
    srcs = _COMMON_SRCS + ["integration/test_pipeline_scenarios.py"],
    args = ["integration/test_pipeline_scenarios.py"],
    main = "run_test.py",
    deps = _COMMON_DEPS + _PIPELINE_DEPS,
)

# ── Test suite ────────────────────────────────────────────────────────────

test_suite(
    name = "unit_tests",
    tests = [
        ":test_addon_manager",
        ":test_anonymizer",
        ":test_benchmark_domain",
        ":test_benchmark_run_store",
        ":test_benchmark_runner",
        ":test_benchmark_service",
        ":test_cache_service",
        ":test_cli",
        ":test_cli_composition",
        ":test_collect_files",
        ":test_config",
        ":test_coordinator",
        ":test_correction",
        ":test_docx_builder",
        ":test_domain_policy",
        ":test_evaluation",
        ":test_file_processor",
        ":test_gpu_detector",
        ":test_input_provider_factory",
        ":test_input_providers",
        ":test_interfaces",
        ":test_llm_factory",
        ":test_ocr_processor",
        ":test_ocr_stage_service",
        ":test_ocr_text_cleaner",
        ":test_ollama_utils",
        ":test_pipeline_txt_integration",
        ":test_preprocess_service",
        ":test_preprocessor",
        ":test_preview_image_store",
        ":test_providers",
        ":test_release_stamp_versions",
        ":test_service",
        ":test_stage_builder",
        ":test_state_repository",
        ":test_storage_policy",
    ],
)

test_suite(
    name = "gui_tests",
    tests = [
        ":test_benchmark_integration",
        ":test_benchmark_widgets",
        ":test_benchmark_window",
        ":test_benchmark_worker",
        ":test_gui_addon_installer_dialog",
        ":test_gui_anonymizer_dialog",
        ":test_gui_bootstrap",
        ":test_gui_config_panel",
        ":test_gui_lifecycle",
        ":test_gui_main_window",
        ":test_gui_results_table",
        ":test_gui_settings_dialog",
        ":test_gui_update_check",
        ":test_gui_widgets",
        ":test_gui_worker",
        ":test_ocr_config_selector",
    ],
)

test_suite(
    name = "integration_tests",
    tests = [
        ":test_pipeline_preview_integration",
        ":test_pipeline_run_integration",
        ":test_pipeline_scenarios_integration",
        ":test_pipeline_tesseract_integration",
        ":test_pipeline_txt_integration",
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
