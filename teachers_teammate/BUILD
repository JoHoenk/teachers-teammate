load("@rules_python//python:defs.bzl", "py_library")
load("@rules_python//python:packaging.bzl", "py_package", "py_wheel")

# Exposed so //:requirements_{dev,test} can stage it next to pyproject.toml when
# pip-compile expands the dynamic version (attr = teachers_teammate.__version__).
exports_files(["__init__.py"])

# The package root and the exception types: what every layer below imports.
_PKG_SRCS = [
    "__init__.py",
    "exceptions.py",
]

# domain/: pure business concepts, no import from any other layer.
_DOMAIN_SRCS = [
    "domain/__init__.py",
    "domain/anonymization.py",
    "domain/benchmark.py",
    "domain/correction_policy.py",
    "domain/context_budget.py",
    "domain/correction_prompts.py",
    "domain/data_location.py",
    "domain/formatted_text.py",
    "domain/freshness.py",
    "domain/language.py",
    "domain/local_models.py",
    "domain/model_catalog.py",
    "domain/model_listing.py",
    "domain/notation.py",
    "domain/offset_map.py",
    "domain/ollama_discovery.py",
    "domain/output_files.py",
    "domain/pdf_text_layer.py",
    "domain/performance_estimate.py",
    "domain/prompt_text.py",
    "domain/proofreading/__init__.py",
    "domain/proofreading/edits.py",
    "domain/proofreading/markdown_shield.py",
    "domain/proofreading/quotes.py",
    "domain/proofreading/result.py",
    "domain/proofreading/review.py",
    "domain/proofreading/sentences.py",
    "domain/proofreading/text_loss.py",
    "domain/proofreading/word_diff.py",
    "domain/run_summary.py",
    "domain/stages.py",
    "domain/teacher_work.py",
    "domain/text_chunks.py",
]

# The settings (config.py) and the stage interfaces: depend on domain/ only.
_CORE_SRCS = [
    "_model_defaults.py",
    "config.py",
    "interfaces.py",
]

# texts/: the sentences a teacher reads; depend on domain/ only.
_TEXTS_SRCS = [
    "texts/__init__.py",
    "texts/_types.py",
    "texts/check.py",
    "texts/dialogs.py",
    "texts/privacy.py",
    "texts/servers.py",
    "texts/settings.py",
    "texts/setup.py",
]

# infrastructure/: concrete implementations; imports domain/ and the core modules.
_INFRASTRUCTURE_SRCS = [
    "infrastructure/__init__.py",
    "infrastructure/_llm_invoke.py",
    "infrastructure/addon_licenses.py",
    "infrastructure/addon_manager.py",
    "infrastructure/diagnostics.py",
    "infrastructure/benchmark/__init__.py",
    "infrastructure/benchmark/run_store.py",
    "infrastructure/benchmark/runner.py",
    "infrastructure/anonymizer.py",
    "infrastructure/atomic_file.py",
    "infrastructure/docx_builder.py",
    "infrastructure/docx_comments.py",
    "infrastructure/evaluation.py",
    "infrastructure/file_discovery.py",
    "infrastructure/gpu_detector.py",
    "infrastructure/image_io.py",
    "infrastructure/input_provider_factory.py",
    "infrastructure/input_providers.py",
    "infrastructure/llm_factory.py",
    "infrastructure/ocr_formats/__init__.py",
    "infrastructure/ocr_formats/chandra_html.py",
    "infrastructure/ocr_formats/filters.py",
    "infrastructure/ocr_formats/markdown.py",
    "infrastructure/ocr_formats/plain.py",
    "infrastructure/ocr_formats/renderers.py",
    "infrastructure/ocr_processor.py",
    "infrastructure/ocr_text_cleaner.py",
    "infrastructure/ollama_discovery.py",
    "infrastructure/ollama_install.py",
    "infrastructure/ollama_utils.py",
    "infrastructure/onnx/__init__.py",
    "infrastructure/onnx/runner.py",
    "infrastructure/onnx/store.py",
    "infrastructure/performance_probe.py",
    "infrastructure/pipeline.py",
    "infrastructure/power.py",
    "infrastructure/ppocr/__init__.py",
    "infrastructure/ppocr/detect.py",
    "infrastructure/ppocr/engine.py",
    "infrastructure/ppocr/reading_order.py",
    "infrastructure/ppocr/recognise.py",
    "infrastructure/preprocessing/__init__.py",
    "infrastructure/preprocessing/auto.py",
    "infrastructure/preprocessing/identity.py",
    "infrastructure/preprocessing/page_models.py",
    "infrastructure/preprocessing/recipes.py",
    "infrastructure/preprocessing/steps.py",
    "infrastructure/preview_image_store.py",
    "infrastructure/reporting.py",
    "infrastructure/proofreading/__init__.py",
    "infrastructure/proofreading/chunking.py",
    "infrastructure/proofreading/corrector.py",
    "infrastructure/proofreading/exchanges/__init__.py",
    "infrastructure/proofreading/exchanges/base.py",
    "infrastructure/proofreading/exchanges/fallback.py",
    "infrastructure/proofreading/exchanges/t.py",
    "infrastructure/proofreading/port.py",
    "infrastructure/proofreading/runner.py",
    "infrastructure/proofreading/schemas.py",
    "infrastructure/providers/__init__.py",
    "infrastructure/providers/_helpers.py",
    "infrastructure/providers/anthropic.py",
    "infrastructure/providers/cohere.py",
    "infrastructure/providers/google.py",
    "infrastructure/providers/_mistral_common.py",
    "infrastructure/providers/mistral.py",
    "infrastructure/providers/mistral_eu.py",
    "infrastructure/providers/ollama.py",
    "infrastructure/providers/openai.py",
    "infrastructure/run_stats.py",
    "infrastructure/spacy_models.py",
    "infrastructure/speed_store.py",
    "infrastructure/stage_builder.py",
    "infrastructure/state_repository.py",
    "infrastructure/storage_root.py",
    "infrastructure/subprocess_utils.py",
    "infrastructure/system_locale.py",
    "infrastructure/teacher_work_store.py",
    "infrastructure/wsl.py",
    "infrastructure/workflow/__init__.py",
    "infrastructure/workflow/cache_service.py",
    "infrastructure/workflow/coordinator.py",
    "infrastructure/workflow/file_processor.py",
    "infrastructure/workflow/ocr_service.py",
    "infrastructure/workflow/preprocess_service.py",
]

# application/: the service the adapters call; imports infrastructure/, domain/, core.
_APPLICATION_SRCS = [
    "application/__init__.py",
    "application/benchmark_service.py",
    "application/check_document.py",
    "application/check_items.py",
    "application/commands.py",
    "application/context_choice.py",
    "application/correction_options.py",
    "application/data_flow.py",
    "application/formatting.py",
    "application/merged_view.py",
    "application/model_recommendations.py",
    "application/sent_text.py",
    "application/speed_estimates.py",
    "application/service.py",
    "application/setup_plan.py",
]

# The command-line adapter and the module entry point.
_CLI_SRCS = [
    "__main__.py",
    "cli.py",
    "cli_composition.py",
]

# Every non-GUI source, for the lint-only *_typed mirrors below.
_OCR_SRCS = (
    _PKG_SRCS +
    _DOMAIN_SRCS +
    _CORE_SRCS +
    _TEXTS_SRCS +
    _INFRASTRUCTURE_SRCS +
    _APPLICATION_SRCS +
    _CLI_SRCS
)

_GUI_SRCS = [
    "gui/__init__.py",
    "gui/__main__.py",
    "gui/_addon_installer_dialog.py",
    "gui/_anonymizer_config_widget.py",
    "gui/_app_bootstrap.py",
    "gui/_background.py",
    "gui/_chart_widget.py",
    "gui/_collapsible_section.py",
    "gui/_config_panel.py",
    "gui/_credential_store.py",
    "gui/_constants.py",
    "gui/_data_flow_summary.py",
    "gui/_diff_widget.py",
    "gui/_dependency_guard_dialog.py",
    "gui/_dialogs.py",
    "gui/_export_menu.py",
    "gui/_fix_targets.py",
    "gui/_formatted_text.py",
    "gui/_help_dialog.py",
    "gui/_hardware_profile_selector.py",
    "gui/_image_utils.py",
    "gui/_image_view.py",
    "gui/_install_round.py",
    "gui/_log_widget.py",
    "gui/_main_window.py",
    "gui/_model_fetch.py",
    "gui/_model_guidance.py",
    "gui/_model_item_delegate.py",
    "gui/_model_wording.py",
    "gui/_ocr_config_selector.py",
    "gui/_ollama_status_line.py",
    "gui/_server_card.py",
    "gui/_package_hint.py",
    "gui/_path_row.py",
    "gui/_privacy_settings.py",
    "gui/_prompt_editor.py",
    "gui/_qt_stream.py",
    "gui/_readiness_line.py",
    "gui/_results_table.py",
    "gui/_run_status.py",
    "gui/_stats_widget.py",
    "gui/_system_monitor.py",
    "gui/_types.py",
    "gui/_update_check.py",
    "gui/_user_text.py",
    "gui/_view_settings.py",
    "gui/_what_is_sent_dialog.py",
    "gui/_window_geometry.py",
    "gui/_worker.py",
    "gui/benchmark/__init__.py",
    "gui/benchmark/__main__.py",
    "gui/benchmark/_benchmark_window.py",
    "gui/benchmark/_benchmark_worker.py",
    "gui/benchmark/_compare_view.py",
    "gui/benchmark/_runs_list.py",
    "gui/benchmark/composition.py",
    "gui/check/__init__.py",
    "gui/check/_page.py",
    "gui/check/_flow.py",
    "gui/check/_masking.py",
    "gui/check/_plain_map.py",
    "gui/check/_run_strip.py",
    "gui/check/_scan_view.py",
    "gui/check/_side_panel.py",
    "gui/check/_style.py",
    "gui/check/_text_views.py",
    "gui/composition.py",
    "gui/settings/__init__.py",
    "gui/settings/_addons_page.py",
    "gui/settings/_ai_models_page.py",
    "gui/settings/_ai_service_section.py",
    "gui/settings/_ai_setup_page.py",
    "gui/settings/_computer_box.py",
    "gui/settings/_credentials.py",
    "gui/settings/_general_page.py",
    "gui/settings/_online_services_page.py",
    "gui/settings/_page.py",
    "gui/settings/_privacy_page.py",
    "gui/settings/_scan_quality_page.py",
    "gui/settings/_server_search_dialog.py",
    "gui/settings/_speed_panel.py",
    "gui/settings/_window.py",
    "gui/setup/__init__.py",
    "gui/setup/_assistant.py",
    "gui/setup/_context.py",
    "gui/setup/_flow.py",
    "gui/setup/_pages.py",
    "gui/setup/_summary.py",
    # Imports GUI modules (the smoke test) and is only run by the installed app.
    "frozen_modes.py",
]

# Third-party deps used ONLY to give the ty/pylint lint aspects real type
# resolution (PySide6, numpy, spaCy, …). They must NOT be added to ocr_lib/
# gui_lib: those feed py_package/py_wheel, which would then bundle the packages
# into the wheel. The *_typed targets below exist purely for the lint targets.
_TYPED_DEPS_OCR = [
    "@ocr_test_dependencies//httpx:pkg",
    "@ocr_test_dependencies//langchain_core:pkg",
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//markdown_it_py:pkg",
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//opencv_python_headless:pkg",
    "@ocr_test_dependencies//pillow:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pydantic:pkg",
    "@ocr_test_dependencies//pypdfium2:pkg",
    "@ocr_test_dependencies//python_docx:pkg",
    "@ocr_test_dependencies//pyyaml:pkg",
    "@ocr_test_dependencies//requests:pkg",
    "@ocr_test_dependencies//spacy:pkg",
]

_TYPED_DEPS_GUI = [
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pyside6_essentials:pkg",
    "@ocr_test_dependencies//qdarkstyle:pkg",
    "@ocr_test_dependencies//shiboken6:pkg",
]

# One library per layer, with the real import direction as `deps`, so a test lists the layer it
# needs and Bazel reruns it only when that layer (or one below it) changes -- not on every edit
# anywhere in the package.  The layers form a DAG (domain imports nothing above it, application
# never imports gui, ...): a new import that points upwards is an ImportError in the tests of the
# lower layer, which only depend on what is listed here.
py_library(
    name = "pkg_lib",
    srcs = _PKG_SRCS,
    visibility = ["//visibility:public"],
)

py_library(
    name = "domain_lib",
    srcs = _DOMAIN_SRCS,
    visibility = ["//visibility:public"],
    deps = [":pkg_lib"],
)

py_library(
    name = "core_lib",
    srcs = _CORE_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        ":domain_lib",
        ":pkg_lib",
    ],
)

py_library(
    name = "texts_lib",
    srcs = _TEXTS_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        ":domain_lib",
        ":pkg_lib",
    ],
)

py_library(
    name = "infrastructure_lib",
    srcs = _INFRASTRUCTURE_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        ":core_lib",
        ":domain_lib",
        ":pkg_lib",
    ],
)

py_library(
    name = "application_lib",
    srcs = _APPLICATION_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        ":core_lib",
        ":domain_lib",
        ":infrastructure_lib",
        ":pkg_lib",
    ],
)

py_library(
    name = "cli_lib",
    srcs = _CLI_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        ":application_lib",
        ":core_lib",
        ":domain_lib",
        ":infrastructure_lib",
        ":pkg_lib",
    ],
)

py_library(
    name = "gui_layer_lib",
    srcs = _GUI_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        ":application_lib",
        ":core_lib",
        ":domain_lib",
        ":infrastructure_lib",
        ":pkg_lib",
        ":texts_lib",
    ],
)

# The whole non-GUI package and the whole package: what the wheel, the docs and the developer
# tools use.  A test should list the layer it needs instead (above), or it reruns on every edit.
py_library(
    name = "ocr_lib",
    visibility = ["//visibility:public"],
    deps = [
        ":application_lib",
        ":cli_lib",
        ":core_lib",
        ":domain_lib",
        ":infrastructure_lib",
        ":pkg_lib",
        ":texts_lib",
    ],
)

py_library(
    name = "gui_lib",
    visibility = ["//visibility:public"],
    deps = [
        ":gui_layer_lib",
        ":ocr_lib",
    ],
)

# Every Python file of the package, for //:py_pylint (which lints the whole first-party tree in
# one action so pylint can resolve cross-package imports). Never part of the wheel or of a test.
filegroup(
    name = "all_sources",
    srcs = glob(["**/*.py"]),
    visibility = ["//visibility:public"],
)

# The bundled assets: the icon, the user-facing license texts and the copy of the license
# inventory the Add-ons page reads.  The spec bundles the folder; the store test reads the copy.
filegroup(
    name = "assets",
    srcs = glob(["assets/**"]),
    visibility = ["//visibility:public"],
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

# The help pages the Help window reads. The bundle and a source checkout have them in docs/; a
# wheel has no docs/ folder, so they are copied into the package (gui/_help_dialog.py looks in
# `<package>/_docs` after `docs/`).
genrule(
    name = "wheel_docs",
    srcs = [
        "//docs:advanced_user_guide.md",
        "//docs:index.md",
        "//docs:using_the_app.md",
    ],
    outs = [
        "_docs/advanced_user_guide.md",
        "_docs/index.md",
        "_docs/using_the_app.md",
    ],
    cmd = " && ".join([
        "cp $(location //docs:advanced_user_guide.md) $(location _docs/advanced_user_guide.md)",
        "cp $(location //docs:index.md) $(location _docs/index.md)",
        "cp $(location //docs:using_the_app.md) $(location _docs/using_the_app.md)",
    ]),
)

# What the wheel carries besides Python files: the assets the GUI loads and the help pages. An
# explicit list, not a glob: assets/ also gets the generated .ico/.icns on a build machine.
# (The license inventory copy and the licenses page are read by the Add-ons page and the About
# dialog.)
filegroup(
    name = "wheel_data",
    srcs = [
        "assets/teachers_teammate.png",
        "assets/teachers_teammate_icon.png",
        "assets/third_party_inventory.csv",
        "assets/third_party_licenses.md",
        ":wheel_docs",
    ],
)

# The whole package, GUI included: the wheel installs `teachers-teammate-gui` and
# `teachers-teammate-benchmark` as well as the command line. tests/unit/test_wheel.py holds it to
# pyproject.toml and to the sources.
py_package(
    name = "ocr_pkg",
    packages = ["teachers_teammate"],
    deps = [
        ":gui_lib",
        ":wheel_data",
    ],
)

# The wheel's Requires-Dist and Provides-Extra are generated from pyproject.toml's
# [project.dependencies] and the user-facing [project.optional-dependencies] groups, so there is
# a single source of truth for pip deps. `test` and `dev` are tooling and stay out.
genrule(
    name = "wheel_requires",
    srcs = ["//:pyproject.toml"],
    outs = [
        "wheel_requires.txt",
        "wheel_requires_llm.txt",
        "wheel_requires_privacy.txt",
    ],
    cmd = " ".join([
        "$(execpath //tools/release:extract_wheel_requires)",
        "$(execpath //:pyproject.toml)",
        "$(execpath wheel_requires.txt)",
        "--extra llm=$(execpath wheel_requires_llm.txt)",
        "--extra privacy=$(execpath wheel_requires_privacy.txt)",
    ]),
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
    extra_requires_files = {
        "wheel_requires_llm.txt": "llm",
        "wheel_requires_privacy.txt": "privacy",
    },
    license = "Apache-2.0",
    platform = "any",
    python_requires = ">=3.14",
    python_tag = "py3",
    requires_file = "wheel_requires.txt",
    summary = "OCR pipeline for handwritten documents",
    version = "0.1.0",
    visibility = ["//visibility:public"],
    deps = [":ocr_pkg"],
)

# Every module of the app, for the ratchet test that keeps teacher-facing text in
# ``teachers_teammate/texts`` (tests/unit/test_user_text_centralised.py).  The test parses the
# sources with ``ast``, so it needs them as data rather than as imports.
filegroup(
    name = "user_text_sources",
    srcs = glob(["**/*.py"]),
    visibility = ["//visibility:public"],
)
