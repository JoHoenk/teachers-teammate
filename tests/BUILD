"""Bazel build targets for the test suite.

A test target is one ``app_test()`` call that names the test file: the macro in ``defs.bzl``
derives the target's name, its folder tag and ``run_test.py`` from it.

Running tests
─────────────
  * Lint targets (ruff / ty / shellcheck):
        bazel test //:lint

  * Run a single test file:
        bazel test //tests:test_config

  * Run the full unit-test suite:
        bazel test //tests:unit_tests

  * Everything at once:
        bazel test //tests/...
"""

load("@rules_python//python:defs.bzl", "py_library")
load(":defs.bzl", "app_test")

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

# Every test source, for the tests that scan the tree they run in. Not a test.
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

# Full pip dep set for the lint aspects (ruff/ty) so test sources resolve
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

# The developer tools the tests drive: a test that runs `tools/packaging/latest_release.py` imports
# it, and the linter only resolves an import whose sources are those of a declared dep.
# `//tools:scripts_lib` holds every script of `tools/` and the evaluation layers below them.
_TESTS_DEVELOPER_TOOLS = [
    "//tools:scripts_lib",
    "//tools/build:lint_lib",
    "//tools/ci:lint_lib",
    "//tools/dev:lint_lib",
    "//tools/licenses:lint_lib",
    "//tools/packaging:lint_lib",
    "//tools/release:lint_lib",
    "//tools/user_text:user_text_lib",
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
    ] + _TESTS_DEVELOPER_TOOLS + _TESTS_TYPED_DEPS,
)

# ── Shared dep groups ─────────────────────────────────────────────────────
#
# Each app_test() call declares only what its test actually needs so that Bazel can give
# precise cache keys and run targets in parallel with minimal overhead. What every test needs
# (``COMMON_SRCS`` and ``COMMON_DEPS``) is in defs.bzl, where the macro adds it.

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
_TOOL_DEPS = _FORMAT_DEPS + _INFRASTRUCTURE_LIB

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

app_test(
    "unit/test_file_discovery.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_config.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_settings_schema.py",
    size = "medium",
    deps = _GUI_DEPS +
           _PIPELINE_DEPS +
           _CLI_LIB + [
        "//teachers_teammate:gui_layer_lib",
    ],
)

app_test(
    "unit/test_language.py",
    data = ["//:requirements_addons_lock.txt"],
    env = {"ADDON_LOCK": "$(rlocationpath //:requirements_addons_lock.txt)"},
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_benchmark_domain.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_benchmark_run_store.py",
    deps = _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_benchmark_runner.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_benchmark_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_cli.py",
    deps = _PIPELINE_DEPS + _CLI_LIB,
)

app_test(
    "unit/test_cli_composition.py",
    deps = _PIPELINE_DEPS + _CLI_LIB,
)

app_test(
    "unit/test_step_runner.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_word_diff.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_sentences.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_text_loss.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_edits.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_review_v2.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_carry_reading_fix.py",
    deps = _DOMAIN_LIB,
)

# The catalogue of messages: one factory per key in ``messages.py``, one renderer per key
# in ``texts/messages.py``, and a golden list of the key values a stored result keeps.
app_test(
    "unit/test_messages.py",
    data = ["//teachers_teammate:all_sources"],
    deps = _DOMAIN_LIB + _TEXTS_LIB,
)

app_test(
    "unit/test_markdown_shield.py",
    deps = _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_offset_map.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_check_items.py",
    deps = _APPLICATION_LIB,
)

app_test(
    "unit/test_merged_view.py",
    deps = _FORMAT_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_check_document.py",
    deps = _FORMAT_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_teacher_work.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_atomic_file.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_teacher_work_store.py",
    deps = _INFRASTRUCTURE_LIB,
)

# A service is built in one place, so every adapter shares its caches.
app_test(
    "unit/test_service_construction.py",
    data = ["//teachers_teammate:all_sources"],
)

# Every reader shows the merged view, never a DocumentState text of its own.
app_test(
    "unit/test_teacher_work_readers.py",
    data = ["//teachers_teammate:all_sources"],
)

app_test(
    "unit/test_quotes.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_correction_prompts_steps.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_exchange_schemas.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_prompt_pin.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_exchange_corrector.py",
    deps = _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_exchange_text_first.py",
    deps = _FORMAT_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_request_profile.py",
    deps = _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_evaluation.py",
    deps = _FORMAT_DEPS + _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_input_providers.py",
    deps = _IMAGE_DEPS + _PDF_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_llm_factory.py",
    deps = _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

# The LangChain invocation helpers: which errors count as the provider rejecting a request.
app_test(
    "unit/test_llm_invoke.py",
    deps = _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ocr_processor.py",
    deps = _LANGCHAIN_DEPS + _IMAGE_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ocr_formats.py",
    deps = _FORMAT_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_ocr_text_cleaner.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_preprocessor.py",
    deps = _IMAGE_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ollama_server.py",
    deps = _APPLICATION_LIB,
)

app_test(
    "unit/test_ollama_address.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_ollama_discovery.py",
    extra_srcs = ["fakes/fake_ollama.py"],
    deps = _REQUESTS_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_ollama_install.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ollama_utils.py",
    deps = _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

# The fake Ollama server, checked against the ``ollama`` client the app is locked to: a bump
# that changes a response model fails here, not as a hang in the walkthrough.
app_test(
    "unit/test_fake_ollama.py",
    extra_srcs = ["fakes/fake_ollama.py"],
    deps = _REQUESTS_DEPS + ["@ocr_test_dependencies//ollama:pkg"] + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_providers.py",
    deps = _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

# This project's releases: which one is newer than this build, and the page a banner may open.
app_test(
    "unit/test_releases_domain.py",
    deps = _DOMAIN_LIB,
)

# The one HTTPS request the update check makes: GitHub's feed, and the failure it reports.
app_test(
    "unit/test_release_feed.py",
    deps = _INFRASTRUCTURE_LIB,
)

# The update check as a service: the feed, the version rule and the one line a failure writes.
app_test(
    "unit/test_release_service.py",
    deps = _APPLICATION_LIB,
)

# The engines of text recognition, their models, and the reader's model files.
app_test(
    "unit/test_reader_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# Personal-info removal: the anonymizer cache, the anonymized texts and the spaCy models.
app_test(
    "unit/test_anonymization_service.py",
    deps = _APPLICATION_LIB,
)

# The keys of the online services: what the app holds, and what the environment has.
app_test(
    "unit/test_api_keys.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# This computer's hardware, the model catalog, the classifications and the ratings.
app_test(
    "unit/test_recommendation_service.py",
    deps = _APPLICATION_LIB,
)

# What each provider is, needs and offers, cached per provider and address.
app_test(
    "unit/test_provider_service.py",
    # It builds the whole set of services to check a provider, so the pipeline comes with it.
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_providers_mistral_curation.py",
    data = ["//tests/fixtures:mistral_models.json"],
    deps = _LANGCHAIN_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "integration/test_pipeline_txt.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "integration/test_pipeline_preview.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_docx_builder.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_docx_comments.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

# The library internals the app reaches into, against the versions it is locked to.
app_test(
    "unit/test_library_internals.py",
    deps = [
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//python_docx:pkg",
    ],
)

app_test(
    "integration/test_pipeline_run.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_storage_policy.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_stored_results.py",
    deps = _APPLICATION_LIB,
)

app_test(
    "unit/test_data_location.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB + _TEXTS_LIB,
)

app_test(
    "unit/test_sent_text.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_domain_policy.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_check_linux_bundle.py",
    deps = ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

app_test(
    "unit/test_check_macos_bundle.py",
    deps = ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

app_test(
    "unit/test_install_nsis.py",
    deps = ["//tools/build:build_tools_lib"],
)

app_test(
    "unit/test_run_bundle.py",
    deps = ["//tools/build:build_tools_lib"],
)

app_test(
    "unit/test_check_desktop_entry.py",
    deps = ["//tools/packaging:packaging_tools_lib"],
)

# The groups CI runs on separate machines (tools/ci/target_groups.py): every test in exactly one.
app_test(
    "unit/test_target_groups.py",
    deps = ["//tools/ci:ci_tools_lib"],
)

app_test(
    "unit/test_check_lint_coverage.py",
    deps = ["//tools/ci:ci_tools_lib"],
)

app_test(
    "unit/test_check_setuptools_wheel.py",
    deps = ["//tools/ci:ci_tools_lib"],
)

app_test(
    "unit/test_latest_release.py",
    deps = ["//tools/packaging:packaging_tools_lib"],
)

app_test(
    "unit/test_windows_installer_floor.py",
    data = ["//tools/packaging:windows.nsi"],
    env = {"WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)"},
)

app_test(
    "unit/test_frozen_entry.py",
    deps = _INFRASTRUCTURE_LIB + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "unit/test_windows_installer_script.py",
    data = [
        "//docs:code_signing_policy.md",
        "//tools/packaging:windows.nsi",
    ],
    env = {
        "CODE_SIGNING_POLICY": "$(rlocationpath //docs:code_signing_policy.md)",
        "WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)",
    },
)

app_test(
    "unit/test_packaging_walkthrough.py",
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
)

app_test(
    "unit/test_walkthrough_folder.py",
    extra_srcs = [
        "e2e/__init__.py",
        "e2e/walkthrough.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "unit/test_walkthrough_upgrade.py",
    extra_srcs = [
        "e2e/__init__.py",
        "e2e/walkthrough.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "unit/test_update_banner_script.py",
    extra_srcs = [
        "e2e/__init__.py",
        "e2e/update_banner.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "unit/test_addon_constraints.py",
    deps = ["//tools/build:build_tools_lib"] + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_texts_types.py",
    deps = _TEXTS_LIB,
)

# The ratchet on class and function size over every layer: the sources come in as data because
# the test parses them with ``ast``.
app_test(
    "unit/test_class_size.py",
    data = ["//teachers_teammate:all_sources"],
)

# No default stands in for a forgotten argument.
app_test(
    "unit/test_no_hidden_defaults.py",
    data = ["//teachers_teammate:all_sources"],
)

# The window keeps no log pane: the log file holds the record, the status bar the last line.
app_test(
    "unit/test_no_log_pane.py",
    data = ["//teachers_teammate:all_sources"],
)

# A start-up failure is worded for a teacher: each entry point installs the wording first.
app_test(
    "unit/test_wording_is_installed.py",
    data = ["//teachers_teammate:all_sources"],
    deps = _PIPELINE_DEPS + _CLI_LIB,
)

app_test(
    "unit/test_layer_imports.py",
    data = ["//teachers_teammate:all_sources"],
)

app_test(
    "unit/test_subprocess_env.py",
    data = ["//teachers_teammate:all_sources"],
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_thread_kinds.py",
    data = ["//teachers_teammate:all_sources"],
)

# The layer's lazy imports only go down: the count of `# noqa: PLC0415` is the ratchet.
app_test(
    "unit/test_lazy_import_budget.py",
    data = ["//teachers_teammate:all_sources"],
)

# The application layer reaches into infrastructure only where the pinned table says it does.
app_test(
    "unit/test_application_infrastructure_budget.py",
    data = ["//teachers_teammate:all_sources"],
)

# The log file leaves the app: a line names a file and a stage, never a document's text.
app_test(
    "unit/test_log_lines_carry_no_text.py",
    data = ["//teachers_teammate:all_sources"],
)

# An `except` that only passes says nothing to whoever reads the log.
app_test(
    "unit/test_silent_handlers.py",
    data = ["//teachers_teammate:all_sources"],
)

app_test(
    "unit/test_logging_setup.py",
    deps = _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_qt_log.py",
    deps = _INFRASTRUCTURE_LIB,
)

# A stage word and the Ollama address are spelled in one module each.
app_test(
    "unit/test_names_have_one_home.py",
    data = ["//teachers_teammate:all_sources"],
)

app_test(
    "unit/test_user_text_centralised.py",
    data = ["//teachers_teammate:user_text_sources"],
    deps = ["//tools/user_text:user_text_lib"] + _PKG_LIB,
)

app_test(
    "unit/test_inventory_tool.py",
    deps = ["//tools/licenses:licenses_lib"] + _INFRASTRUCTURE_LIB,
)

# The committed store, checked offline: the rows are the closures of the locks, every license
# passes the deny rules, every bundle row has a text, and the compiled outputs are fresh.  A
# Dependabot bump of either lock is red here until tools/licenses/inventory.py runs.
app_test(
    "unit/test_license_inventory.py",
    data = [
        "//:.python-version",
        "//:LICENSE",
        "//:NOTICE",
        "//:license_store",
        "//:pyproject.toml",
        "//:requirements_addons_lock.txt",
        "//:requirements_lock.txt",
        "//teachers_teammate:assets",
        "//teachers_teammate:wheel.bzl",
        "//tools/build:requirements_addons_platform.txt",
        "//tools/build:requirements_platform.txt",
        "//tools/packaging:make_deb.sh",
        "//tools/packaging:windows.nsi",
    ],
    env = {
        "LICENSE_DEB": "$(rlocationpath //tools/packaging:make_deb.sh)",
        "LICENSE_NSI": "$(rlocationpath //tools/packaging:windows.nsi)",
        "LICENSE_PYPROJECT": "$(rlocationpath //:pyproject.toml)",
        "LICENSE_WHEEL_BZL": "$(rlocationpath //teachers_teammate:wheel.bzl)",
    },
    deps = [
        "//tools/build:build_tools_lib",
        "//tools/licenses:artifact_checks_lib",
        "//tools/licenses:licenses_lib",
        "//tools/release:release_tools_lib",
    ] + _INFRASTRUCTURE_LIB,
)

# The targets of this file: one per test file, each tagged by its folder, which is what the
# suites below take.
app_test(
    "unit/test_test_targets.py",
    data = [
        "BUILD",
        ":test_sources",
    ],
    env = {"TESTS_BUILD": "$(rlocationpath BUILD)"},
)

# The three locks against one another: a package two of them pin differently is a conflict no
# single pip-compile run can see.
app_test(
    "unit/test_lock_agreement.py",
    data = [
        "//:requirements_addons_lock.txt",
        "//:requirements_dev_lock.txt",
        "//:requirements_lock.txt",
    ],
    env = {"LOCKS_ROOT": "$(rlocationpath //:requirements_lock.txt)"},
    visibility = ["//:__pkg__"],
)

app_test(
    "unit/test_fetch_models.py",
    deps = _REQUESTS_DEPS + ["//tools/build:build_tools_lib"] + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_addon_lock.py",
    deps = ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

app_test(
    "unit/test_check_pins.py",
    deps = ["//tools/build:build_tools_lib"],
)

app_test(
    "unit/test_make_constraints.py",
    deps = ["//tools/build:build_tools_lib"],
)

app_test(
    "unit/test_make_venv.py",
    deps = ["//tools/build:build_tools_lib"],
)

app_test(
    "unit/test_make_version_resource.py",
    data = ["//tools/packaging:windows.nsi"],
    env = {"WINDOWS_NSI": "$(rlocationpath //tools/packaging:windows.nsi)"},
    deps = ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

# The wheel Bazel builds must hold every module, the assets and the help pages, and say what
# pyproject.toml says. Builds the wheel, so it is the slowest of the release tests; the wheel's
# install is checked on the release dry run (build_wheel.yml).
app_test(
    "unit/test_wheel.py",
    data = [
        "//:LICENSE",
        "//:NOTICE",
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
    deps = ["@ocr_test_dependencies//packaging:pkg"],
)

app_test(
    "unit/test_check_smoke.py",
    deps = ["//tools/build:build_tools_lib"] + _PKG_LIB,
)

# What a finished download carries, held against the tree: the test drives the comparison with a
# fixture folder, so the rule the build checks is the rule the test pins.
app_test(
    "unit/test_artifact_licenses.py",
    deps = ["//tools/licenses:artifact_checks_lib"],
)

app_test(
    "unit/test_release_tag.py",
    deps = ["//tools/release:release_tools_lib"] + _PKG_LIB,
)

app_test(
    "unit/test_release_stamp_versions.py",
    deps = ["//tools/release:release_tools_lib"] + _PKG_LIB,
)

# `.python-version` is the one place the Python version is written down (rules_python reads it for
# the toolchain, setup-python in CI, build.sh/build.bat name the same minor).  This target holds
# the interpreter the tests run on to it.
app_test(
    "unit/test_python_version.py",
    data = ["//:.python-version"],
    env = {"PYTHON_VERSION_FILE": "$(rlocationpath //:.python-version)"},
)

app_test(
    "unit/test_interfaces.py",
    deps = _CORE_LIB,
)

app_test(
    "unit/test_run_summary.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_power.py",
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_state_repository.py",
    deps = _FORMAT_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_export_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The run: its files, its requirements and its summary.
app_test(
    "unit/test_run_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB + _TEXTS_LIB,
)

app_test(
    "unit/test_run_rules.py",
    deps = _CORE_LIB,
)

# What the store says about a document.
app_test(
    "unit/test_documents_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The writes: the teacher's texts and decisions.
app_test(
    "unit/test_commands_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# Where each step of a run takes place.
app_test(
    "unit/test_data_flow_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_setup_plan.py",
    # ``application.setup_plan`` also holds ``SetupService``, which asks the reader service and
    # so the pipeline (the docx builder included).
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_addon_pins.py",
    deps = ["//tools/build:build_tools_lib"],
)

app_test(
    "unit/test_addon_requirements.py",
    data = [
        "//:pyproject.toml",
        "//:requirements_addons_lock.txt",
    ],
    env = {
        "ADDON_LOCK": "$(rlocationpath //:requirements_addons_lock.txt)",
        "PYPROJECT": "$(rlocationpath //:pyproject.toml)",
    },
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_addon_manager.py",
    deps = _INFRASTRUCTURE_LIB,
)

# The proofreading options the GUI shows: the steps that run and the notation note.
app_test(
    "unit/test_correction_options.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The Setup Assistant's facts and plan: what it asks the computer, and which settings file a
# first start reads.
app_test(
    "unit/test_setup_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

# The add-ons: what of them is installed, and the installs themselves.
app_test(
    "unit/test_addons_service.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_spacy_models.py",
    deps = _INFRASTRUCTURE_LIB,
)

# The smoke test imports every lazily loaded module of the app, so it needs the whole runtime
# set — including the packages no other unit test imports (ollama, keyring).
# ``medium``: the smoke test now also builds the Setup Assistant and waits for its plan, which
# has a 30 s timeout of its own, so the 60 s of ``small`` is too tight on a loaded runner.
app_test(
    "unit/test_frozen_modes.py",
    size = "medium",
    data = ["//tools/build:teachers-teammate.spec"],
    env = {"BUILD_SPEC": "$(rlocationpath //tools/build:teachers-teammate.spec)"},
    extra_srcs = ["gui/_settings_stubs.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//keyring:pkg",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

app_test(
    "unit/test_gpu_detector.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_diagnostics.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_speed_estimates.py",
    deps = _FORMAT_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_speed_store.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_run_stats.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_performance_probe.py",
    deps = _IMAGE_DEPS + _CV_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_performance_estimate.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_model_catalog.py",
    deps = _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_chunking_corrector.py",
    deps = _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_text_chunks.py",
    deps = _DOMAIN_LIB,
)

# The core layer of the new evaluation tooling: the formats round-trip, an unknown field survives a
# read and a write, a changed reference moves the key hash and not the question hash, character
# offsets round-trip through the tokenizer, and the axes build the compare key and the file name.
app_test(
    "tools/test_evaluation_core.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
    ],
)

# The one scoring path: its numbers are pinned as golden values, and the fallback rule, the marks a
# model added and an accepted alternative of the key are counted as the report counts them.
app_test(
    "tools/test_evaluation_score.py",
    deps = _DOMAIN_LIB + [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
    ],
)

# The input layer: the corpora, the labels of their M2 files, and the manifest.  The M2 files are
# written in the test, so no learner corpus has to be present; the one corpus that is in the
# repository (invented-fr) is read as it is.
app_test(
    "tools/test_evaluation_input.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:metrics",
    ],
)

# The test-set builder: a committed set is never edited, and importing the builder does not load
# the app.  The recipe of the fake set is added to the registry, so no learner corpus is needed.
app_test(
    "tools/test_evaluation_prepare_testset.py",
    deps = [
        "//tools:prepare_gec_testset_lib",
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:store",
    ],
)

# The store layer: the layout, the reader and the writer, every rule of the format, and the marks.
# It reads and writes files and nothing above it.
app_test(
    "tools/test_evaluation_store.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:metrics",
        "//tools/evaluation:store",
    ],
)

# The converter: the old folders read as they are.  The unit tests build a small old store; the
# acceptance tests run over the real folders and are skipped unless GEC_RESULTS names them and
# GEC_EVALS the private store that holds the manifests.
app_test(
    "tools/test_evaluation_convert_v1.py",
    deps = _DOMAIN_LIB + _FORMAT_DEPS + [
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
app_test(
    "tools/test_evaluation_report.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The report layer's tables and `compare --along`: the by-error view, the sweep, the passes, the
# false alarms, the performance, and the axis selection a comparison reads.  The store is built in
# the test, so the old package is not a dependency here.
app_test(
    "tools/test_evaluation_report_tables.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The report's views: the model and band tables, the selection of the runs a command reads, and the
# spot-check sheet a teacher marks.  The store is built in the test, so the old package is not a
# dependency here.
app_test(
    "tools/test_evaluation_report_views.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The fit and page time table: the verdict of every tier from the size records and the full-exchange
# runs, and the markdown a report prints.
app_test(
    "tools/test_evaluation_fit_table.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:report",
    ],
)

# The marks workflow end to end: a sheet printed from a stored run, filled in, read back as marks,
# checked, and then counted by the gate or turned into the key's accepted alternatives.  The old
# blind sheet's key is imported the same way.  The store is built in the test.
app_test(
    "tools/test_evaluation_marks_workflow.py",
    deps = [
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
app_test(
    "tools/test_evaluation_cli.py",
    deps = [
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
app_test(
    "tools/test_evaluation_viewer.py",
    deps = [
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
app_test(
    "tools/test_evaluation_gates.py",
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:metrics",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The run layer's log: one line per thing that happened, so a long run can be read afterwards.
app_test(
    "tools/test_evaluation_run_log.py",
    deps = ["//tools/evaluation:run"],
)

# The client of the graphics-memory agent: the wire format, the windows and the errors, against a
# server the test starts itself.
app_test(
    "tools/test_evaluation_agent_client.py",
    deps = ["//tools/evaluation:run"],
)

# The graphics-memory agent itself: the script it writes, the interpreter it picks and the samples
# it reads back.  Nothing here starts a real agent.
app_test(
    "tools/test_evaluation_gpu_agent.py",
    deps = ["//tools/evaluation:run"],
)

# The protocol table and the stamp a run is made with: the fingerprints, the versions and what a
# changed table refuses.
app_test(
    "tools/test_evaluation_protocol.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# What a run measures: the header, the session line, the sampler record and the performance of an
# answer.
app_test(
    "tools/test_evaluation_measure.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The commit a run records: read from the checkout, from the workspace Bazel names, and out of the
# repository's own files when git refuses.
app_test(
    "tools/test_evaluation_revision.py",
    deps = [
        "//tools/evaluation:core",
    ],
)

# The sessions of one run (the header file and the resume check) and the layering of the offline
# modules.
app_test(
    "tools/test_evaluation_session.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The run loop: the planning, the resume check, the seed per repeat, the warm-up, the header and the
# sampler, with every seam replaced.
app_test(
    "tools/test_evaluation_runner.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The path a run walks, end to end: a scripted corrector writes a run file, the report scores it,
# ``compare`` and ``gate`` read it and the check passes it.  The store is built in the test and no
# request leaves it.
app_test(
    "tools/test_evaluation_workflow.py",
    deps = _FORMAT_DEPS + [
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
app_test(
    "tools/test_evaluation_reference.py",
    deps = _FORMAT_DEPS + [
        "//tools:report",
        "//tools/evaluation:core",
        "//tools/evaluation:report",
        "//tools/evaluation:store",
    ],
)

# The comparison that writes an answer as it arrives: the chunking, the fallback and the failure.
app_test(
    "tools/test_evaluation_comparison.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The progress display: the line, the estimate, the waiting thread and the wiring into the loop.
# The runner's own helpers are shared, so its test file is a source here too.
app_test(
    "tools/test_evaluation_progress.py",
    extra_srcs = ["tools/test_evaluation_runner.py"],
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ],
)

# The app's own corrector as the run layer builds it, and the guard that it stays the app's: the
# test also builds the app's corrector through the stage builder, which needs the pipeline
# libraries.
app_test(
    "tools/test_evaluation_app_corrector.py",
    deps = _FORMAT_DEPS + _LANGCHAIN_DEPS + _IMAGE_DEPS + _PDF_DEPS + _CV_DEPS +
           _REQUESTS_DEPS + [
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "@ocr_test_dependencies//python_docx:pkg",
    ],
)

# The pass/fail rule a check is judged by: the verdicts, the thresholds and the retry ladder.
app_test(
    "tools/test_evaluation_check_rule.py",
    deps = ["//tools/evaluation:checks"],
)

# Gate 1: the saved answers, the smoke gate that opens them and the first try judged from them.
app_test(
    "tools/test_evaluation_gate_one.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The machines the evaluations ran on: the registry, its ids, and what it refuses.
app_test(
    "tools/test_evaluation_machines.py",
    data = ["//evaluations:records"],
    deps = ["//tools/evaluation:core"],
)

# The model fit: whether a model's window, its memory and its answer length suit the app.
app_test(
    "tools/test_evaluation_model_fit.py",
    deps = [
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The smoke check: the fixtures, the steps it walks, the decision it reaches, and the command
# line that runs it (`verify_model_catalog smoke`).  Medium: the replay of the saved gate-1
# answers alone takes half a minute.
app_test(
    "tools/test_evaluation_smoke.py",
    size = "medium",
    deps = _FORMAT_DEPS + [
        "//tools:verify_model_catalog",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The structured output check: the fixtures, the review budget and the margin it warns about.
app_test(
    "tools/test_evaluation_structured_check.py",
    deps = _FORMAT_DEPS + [
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The command line of ``verify_model_catalog structured-output``: the header, the default window,
# the saved answers and the re-run that judges them again.
app_test(
    "tools/test_evaluation_verify_model_catalog.py",
    data = ["//evaluations:records"],
    deps = _FORMAT_DEPS + [
        "//tools:verify_model_catalog",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ],
)

# The developer tool that asks the global and the EU Mistral endpoint which models they serve: the
# comparison and the command line, with the endpoints' answers faked.
app_test(
    "tools/test_compare_mistral_regions.py",
    deps = _INFRASTRUCTURE_LIB + ["//tools/dev:compare_mistral_regions_lib"],
)

# The developer tool that measures what a reading model costs on one page: the pure parts, so no
# server and no page image.  The tool itself imports the image preprocessor, hence the CV deps.
app_test(
    "tools/test_ocr_page_tokens.py",
    deps = _TOOL_DEPS + _CV_DEPS + _IMAGE_DEPS + ["//tools/dev:ocr_page_tokens"],
)

# One spelling per fact: the text id, the shared words (``ollama``, ``errors``, ``correct``) and the
# host of a run each live once, in ``core``.  The search reads the syntax tree, so a docstring that
# explains a rule is not a second copy of it.
app_test(
    "tools/test_evaluation_shared_names.py",
    data = [
        "//tools:all_sources",
        "//tools/dev:all_sources",
    ],
)

# The layers of the evaluation tooling: an import that reaches above its own layer fails here, with
# the file and the line, instead of failing the build with the name of a target.
app_test(
    "tools/test_evaluation_layers.py",
    data = [
        "//tools:all_sources",
        "//tools/dev:all_sources",
    ],
)

# Every tool binary starts: `--help` exits 0, and so does each subcommand's `--help`.  The binaries
# are data, so the test starts them the way a person does, with the runfiles Bazel gives them.
app_test(
    "tools/test_tool_binaries.py",
    size = "medium",
    data = [
        "//tools:evaluate",
        "//tools:extract_test_specs",
        "//tools:prepare_gec_testset",
        "//tools:report",
        "//tools:store",
        "//tools:verify_model_catalog",
        "//tools/dev:compare_mistral_regions",
        "//tools/dev:ocr_page_tokens",
        "//tools/evaluation:gpu_agent",
    ],
)

# The sources mirror, without the network: the listings, the digests, the set and the gh commands.
app_test(
    "tools/test_mirror_sources.py",
    deps = ["//tools/release:release_tools_lib"],
)

# The records live in a repository of their own: no run file, manifest, size record, machine or
# marks file is in this one, because they hold the corpora's text.
app_test(
    "tools/test_no_eval_records.py",
    data = [
        ":all_sources",
        "//:AGENTS.md",
        "//docs:markdown",
        "//evaluations:records",
        "//teachers_teammate:all_sources",
        "//tools:all_sources",
        "//tools/dev:all_sources",
    ],
)

# The diagrams name what the code has: the exception tree, and every diagram a page shows.
app_test(
    "tools/test_diagrams.py",
    data = [
        "//docs:diagrams",
        "//docs:markdown",
        "//teachers_teammate:all_sources",
    ],
    deps = _PKG_LIB,
)

# The repository explains itself: no source, document or record points into git-ignored notes.
app_test(
    "tools/test_no_plan_references.py",
    data = [
        ":all_sources",
        "//:AGENTS.md",
        "//:repo_config",
        "//docs:markdown",
        "//evaluations:records",
        "//teachers_teammate:all_sources",
        "//tools:all_sources",
        "//tools/dev:all_sources",
    ],
    deps = _PKG_LIB,
)

app_test(
    "unit/test_context_budget.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_model_listing.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_cache_service.py",
    deps = _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_cache_keys.py",
    deps = _CORE_LIB,
)

app_test(
    "unit/test_cache_keys_golden.py",
    deps = _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_file_processor.py",
    deps = _REQUESTS_DEPS + _FORMAT_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_preview_image_store.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_input_provider_factory.py",
    deps = _PDF_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ocr_stage_service.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_preprocess_service.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_stage_builder.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "unit/test_coordinator.py",
    deps = _FORMAT_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

# A GUI test builds real widgets and a real window, and a loaded runner runs a dozen of them at
# once: the 60 s a ``small`` test gets is not enough headroom, so every ``test_gui_*`` is
# ``medium``.  A timeout is a flake, and a flake hides the failure it should report.
app_test(
    "gui/test_gui_config_panel.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_readiness.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_check_page.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_results_table.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_what_is_sent.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_check_controller.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_main_window.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_long_job.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_ollama_status_line.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_worker.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_error_hooks.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_errors.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_prompt_dialog.py",
    size = "medium",
    extra_srcs = ["gui/_settings_stubs.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

# Where the API keys are kept: the system keychain, else the app's settings file.  No window:
# the store reads QSettings without one.
app_test(
    "unit/test_credential_store.py",
    deps = _PIPELINE_DEPS + _APPLICATION_LIB,
)

app_test(
    "gui/test_gui_general_page.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_run_session.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_run_start.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + _CLI_LIB + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_run_status.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_settings_pages.py",
    size = "medium",
    extra_srcs = [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_settings_window.py",
    size = "medium",
    extra_srcs = [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_setup_assistant.py",
    size = "medium",
    extra_srcs = [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_setup_flow.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

# The whole wizard, end to end: the real pages, the real service, real threads, a real HTTP
# conversation with a fake Ollama and the real MainWindow writing a real ocr.toml.
app_test(
    "gui/test_gui_setup_walkthrough.py",
    size = "medium",
    extra_srcs = [
        "fakes/fake_ollama.py",
        "gui/_settings_stubs.py",
    ],
    tags = ["walkthrough"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

app_test(
    "gui/test_gui_setup_pages.py",
    size = "medium",
    extra_srcs = [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_plain_map.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_check_style.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_text_views.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_check_body.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_side_panel.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_window_geometry.py",
    size = "medium",
    extra_srcs = [
        "gui/_gui_harness.py",
        "gui/_settings_stubs.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_scan_view.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_formatted_text.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_chart_widget.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_widgets.py",
    size = "medium",
    data = ["//teachers_teammate:assets"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "unit/test_anonymizer.py",
    deps = _INFRASTRUCTURE_LIB,
)

app_test(
    "gui/test_gui_anonymizer_widget.py",
    size = "medium",
    extra_srcs = ["gui/_settings_stubs.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_privacy_settings.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "unit/test_anonymization_mask.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "gui/test_gui_privacy_view.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_dialogs.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_qsettings.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_addon_installer_dialog.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_update_check.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_ocr_config_selector.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_speed_panel.py",
    size = "medium",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_hardware_profile_selector.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_background.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_model_item_delegate.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_model_wording.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_install_round.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_addons_tabs.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_addons_page.py",
    size = "medium",
    extra_srcs = [
        "fakes/fake_ollama.py",
        "gui/_gui_harness.py",
    ],
    deps = _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

app_test(
    "gui/test_benchmark_window.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_lifecycle.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_gui_bootstrap.py",
    size = "medium",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_benchmark_widgets.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_benchmark_worker.py",
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "gui/test_benchmark_integration.py",
    extra_srcs = ["gui/_gui_harness.py"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + ["//teachers_teammate:gui_layer_lib"],
)

app_test(
    "integration/test_pipeline_scenarios.py",
    data = _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "integration/test_pipeline_teacher_work.py",
    data = _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_install_item.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_local_models.py",
    deps = _DOMAIN_LIB,
)

app_test(
    "unit/test_model_store.py",
    deps = _REQUESTS_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ppocr_detect.py",
    deps = _CV_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ppocr_reading_order.py",
    deps = _CV_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ppocr_recognise.py",
    deps = _CV_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_ppocr_engine.py",
    deps = _CV_DEPS + _REQUESTS_DEPS + _INFRASTRUCTURE_LIB + _TEXTS_LIB,
)

app_test(
    "integration/test_ppocr_models.py",
    size = "medium",
    data = ["//tests/fixtures:umlaut_line.png"] + _READER_MODEL_DATA,
    env = _READER_MODEL_ENV,
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "tools/test_no_retired_engines.py",
    data = ["//teachers_teammate:all_sources"],
    deps = _PKG_LIB,
)

app_test(
    "tools/test_no_sentence_review_in_the_app.py",
    data = ["//teachers_teammate:all_sources"],
    deps = _PKG_LIB,
)

app_test(
    "unit/test_preprocessing_auto.py",
    extra_srcs = ["unit/preprocessing_auto_golden_data.py"],
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "unit/test_preprocessing_auto_golden.py",
    extra_srcs = ["unit/preprocessing_auto_golden_data.py"],
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

app_test(
    "integration/test_page_models.py",
    size = "medium",
    data = _PAGE_MODEL_DATA,
    env = _PAGE_MODEL_ENV,
    extra_srcs = ["unit/preprocessing_auto_golden_data.py"],
    deps = _PIPELINE_DEPS + _INFRASTRUCTURE_LIB,
)

# The whole app as a teacher uses it: the walkthrough script builds the real main window, runs
# the real reader and page models over three documents, and checks what the app did.  Large
# because the run over the documents is the slow part.
app_test(
    "gui/test_gui_main_walkthrough.py",
    size = "large",
    data = _READER_MODEL_DATA + _PAGE_MODEL_DATA,
    env = _MODEL_ENV,
    extra_srcs = [
        "e2e/__init__.py",
        "e2e/walk_checked.py",
        "e2e/walkthrough.py",
        "fakes/fake_ollama.py",
    ],
    tags = ["walkthrough"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        # The proofreading walk talks to the real ``ollama`` client.
        "@ocr_test_dependencies//ollama:pkg",
    ],
)

# The walk over the data an earlier version left: the same window, the same reader, and a
# settings file written by hand where a bundle walk would find the old version's.
app_test(
    "gui/test_gui_upgrade_walk.py",
    size = "large",
    data = _READER_MODEL_DATA + _PAGE_MODEL_DATA,
    env = _MODEL_ENV,
    extra_srcs = [
        "e2e/__init__.py",
        "e2e/walkthrough.py",
    ],
    tags = ["walkthrough"],
    deps = _GUI_DEPS + _PIPELINE_DEPS + [
        "//teachers_teammate:gui_layer_lib",
        "//teachers_teammate:infrastructure_lib",
    ],
)

# What those scripts may import: they also run inside the shipped bundle, which carries no tests.
app_test(
    "unit/test_walkthrough_imports.py",
    extra_srcs = [
        "e2e/__init__.py",
        "e2e/update_banner.py",
        "e2e/walkthrough.py",
    ],
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
