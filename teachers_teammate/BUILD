load("@rules_python//python:packaging.bzl", "py_package", "py_wheel")

# Exposed so //:requirements_{dev,test} can stage it next to pyproject.toml when
# pip-compile expands the dynamic version (attr = teachers_teammate.__version__).
exports_files(["__init__.py"])

_OCR_SRCS = [
    "__init__.py",
    "__main__.py",
    "_model_defaults.py",
    "application/__init__.py",
    "application/benchmark_service.py",
    "application/commands.py",
    "application/service.py",
    "cli.py",
    "cli_composition.py",
    "config.py",
    "domain/__init__.py",
    "domain/benchmark.py",
    "domain/freshness.py",
    "domain/stages.py",
    "exceptions.py",
    "infrastructure/__init__.py",
    "infrastructure/_llm_invoke.py",
    "infrastructure/addon_manager.py",
    "infrastructure/benchmark/__init__.py",
    "infrastructure/benchmark/run_store.py",
    "infrastructure/benchmark/runner.py",
    "infrastructure/anonymizer.py",
    "infrastructure/correction.py",
    "infrastructure/docx_builder.py",
    "infrastructure/evaluation.py",
    "infrastructure/file_discovery.py",
    "infrastructure/gpu_detector.py",
    "infrastructure/image_preprocessor.py",
    "infrastructure/input_provider_factory.py",
    "infrastructure/input_providers.py",
    "infrastructure/llm_factory.py",
    "infrastructure/ocr_processor.py",
    "infrastructure/ocr_text_cleaner.py",
    "infrastructure/ollama_utils.py",
    "infrastructure/pipeline.py",
    "infrastructure/preview_image_store.py",
    "infrastructure/reporting.py",
    "infrastructure/providers/__init__.py",
    "infrastructure/providers/_helpers.py",
    "infrastructure/providers/anthropic.py",
    "infrastructure/providers/cohere.py",
    "infrastructure/providers/google.py",
    "infrastructure/providers/mistral.py",
    "infrastructure/providers/ollama.py",
    "infrastructure/providers/openai.py",
    "infrastructure/stage_builder.py",
    "infrastructure/state_repository.py",
    "infrastructure/storage_root.py",
    "infrastructure/workflow/__init__.py",
    "infrastructure/workflow/cache_service.py",
    "infrastructure/workflow/coordinator.py",
    "infrastructure/workflow/file_processor.py",
    "infrastructure/workflow/ocr_service.py",
    "infrastructure/workflow/preprocess_service.py",
    "interfaces.py",
]

_GUI_SRCS = [
    "gui/__init__.py",
    "gui/__main__.py",
    "gui/_addon_installer_dialog.py",
    "gui/_anonymizer_config_dialog.py",
    "gui/_app_bootstrap.py",
    "gui/_chart_widget.py",
    "gui/_collapsible_section.py",
    "gui/_config_panel.py",
    "gui/_constants.py",
    "gui/_dependency_guard_dialog.py",
    "gui/_downloads_dialog.py",
    "gui/_help_dialog.py",
    "gui/_image_utils.py",
    "gui/_log_widget.py",
    "gui/_main_window.py",
    "gui/_ocr_config_selector.py",
    "gui/_preview_panel.py",
    "gui/_qt_stream.py",
    "gui/_results_table.py",
    "gui/_settings_dialog.py",
    "gui/_stats_widget.py",
    "gui/_types.py",
    "gui/_update_check.py",
    "gui/_worker.py",
    "gui/benchmark/__init__.py",
    "gui/benchmark/__main__.py",
    "gui/benchmark/_benchmark_window.py",
    "gui/benchmark/_benchmark_worker.py",
    "gui/benchmark/_compare_view.py",
    "gui/benchmark/_runs_list.py",
    "gui/benchmark/composition.py",
    "gui/composition.py",
]

# Third-party deps used ONLY to give the ty/pylint lint aspects real type
# resolution (PySide6, numpy, spaCy, …). They must NOT be added to ocr_lib/
# gui_lib: those feed py_package/py_wheel, which would then bundle the packages
# into the wheel. The *_typed targets below exist purely for the lint targets.
_TYPED_DEPS_OCR = [
    "@ocr_test_dependencies//langchain_core:pkg",
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//opencv_python:pkg",
    "@ocr_test_dependencies//pillow:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
    "@ocr_test_dependencies//pypdfium2:pkg",
    "@ocr_test_dependencies//pytesseract:pkg",
    "@ocr_test_dependencies//python_docx:pkg",
    "@ocr_test_dependencies//requests:pkg",
    "@ocr_test_dependencies//spacy:pkg",
]

_TYPED_DEPS_GUI = [
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pyside6:pkg",
    "@ocr_test_dependencies//pyside6_addons:pkg",
    "@ocr_test_dependencies//pyside6_essentials:pkg",
    "@ocr_test_dependencies//qdarkstyle:pkg",
    "@ocr_test_dependencies//shiboken6:pkg",
]

py_library(
    name = "ocr_lib",
    srcs = _OCR_SRCS,
    visibility = ["//visibility:public"],
)

py_library(
    name = "gui_lib",
    srcs = _GUI_SRCS,
    visibility = ["//visibility:public"],
    deps = [":ocr_lib"],
)

# Lint-only mirrors of ocr_lib/gui_lib that additionally depend on the runtime
# pip packages, so the rules_lint ty/pylint aspects resolve real types. Tagged
# "manual" and never referenced by the wheel — keep them out of //:ocr_pkg.
py_library(
    name = "ocr_lib_typed",
    srcs = _OCR_SRCS,
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = _TYPED_DEPS_OCR,
)

py_library(
    name = "gui_lib_typed",
    srcs = _GUI_SRCS,
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = [":ocr_lib_typed"] + _TYPED_DEPS_GUI,
)

py_package(
    name = "ocr_pkg",
    packages = ["teachers_teammate"],
    deps = [":ocr_lib"],
)

# The wheel's Requires-Dist is generated from pyproject.toml's
# [project.dependencies] so there is a single source of truth for pip deps.
genrule(
    name = "wheel_requires",
    srcs = ["//:pyproject.toml"],
    outs = ["wheel_requires.txt"],
    cmd = "$(execpath //tools/release:extract_wheel_requires) $(execpath //:pyproject.toml) $@",
    tools = ["//tools/release:extract_wheel_requires"],
)

py_wheel(
    name = "teachers_teammate_wheel",
    abi = "none",
    description_content_type = "text/markdown",
    description_file = "//:README.md",
    distribution = "teachers-teammate",
    entry_points = {
        "console_scripts": [
            "teachers-teammate = teachers_teammate.cli:main",
            "teachers-teammate-gui = teachers_teammate.gui:main_gui",
            "teachers-teammate-benchmark = teachers_teammate.gui.benchmark:main_benchmark",
        ],
    },
    license = "Apache-2.0",
    platform = "any",
    python_requires = ">=3.12",
    python_tag = "py3",
    requires_file = ":wheel_requires",
    summary = "OCR pipeline for handwritten documents",
    version = "0.1.0",
    visibility = ["//visibility:public"],
    deps = [":ocr_pkg"],
)
