load("@rules_python//python:pip.bzl", "compile_pip_requirements")
load("//third_party/lint:linters.bzl", "pylint_test", "ruff_test", "ty_test")

# pyproject.toml is needed by pylint/ty via rules_lint.
exports_files(
    [
        "CLAUDE.md",
        "pyproject.toml",
        "README.md",
    ],
    visibility = ["//visibility:public"],
)

# pylint resolves imports by importing modules from the action's sandbox; the rules_lint pylint
# aspect passes only the visited target's own source files, not its deps. So unlike py_ty there's
# no benefit to the *_typed libs here — PySide6/qdarkstyle simply aren't importable by pylint and
# are carved out via [tool.pylint.typecheck] ignored-modules in pyproject.toml.
# Because only its own sources are in the sandbox, a per-package pylint target would report
# import-error (E0401) for every correct first-party import from //tools and //tests. One target
# holding the whole first-party tree puts it all in a single action's sandbox, which keeps E0401
# meaningful everywhere (a typo still fails) — and the developer tools get the same gate as the
# app, which they need: they decide product facts (the catalog's `explanations ✓` flag, the gate-1
# pass rule).
py_library(
    name = "first_party_sources",
    srcs = [
        "//teachers_teammate:all_sources",
        "//tests:all_sources",
        "//tools:all_sources",
    ],
    tags = ["manual"],
)

pylint_test(
    name = "py_pylint",
    srcs = [":first_party_sources"],
)

ruff_test(
    name = "py_ruff",
    srcs = [
        "//teachers_teammate:gui_lib",
        "//teachers_teammate:ocr_lib",
        "//tests:tests_lib",
        "//tools:compare_writing_models",
        "//tools:model_eval",
        "//tools:prepare_gec_testset",
        "//tools:proofreading_store",
        "//tools:verify_model_catalog",
    ],
)

ty_test(
    name = "py_ty",
    srcs = [
        "//teachers_teammate:gui_lib_typed",
        "//teachers_teammate:ocr_lib_typed",
        "//tests:tests_lib",
        "//tools:compare_writing_models",
        "//tools:model_eval",
        "//tools:prepare_gec_testset",
        "//tools:proofreading_store",
        "//tools:verify_model_catalog",
    ],
)

test_suite(
    name = "lint",
    tests = [
        ":py_pylint",
        ":py_ruff",
        ":py_ty",
    ],
)

# Lock files are compiled from the [project.optional-dependencies] extras in
# pyproject.toml — the single source of truth for every pip dependency. Each
# `--extra` also pulls the base runtime deps, so the locks are self-contained.
#
# `data` stages teachers_teammate/__init__.py beside pyproject.toml so pip-compile
# can expand the dynamic version (attr = teachers_teammate.__version__).
#
# Run: bazel run //:requirements_dev.update
compile_pip_requirements(
    name = "requirements_dev",
    src = "pyproject.toml",
    data = ["//teachers_teammate:__init__.py"],
    extra_args = ["--extra=dev"],
    requirements_txt = "requirements_dev_lock.txt",
)

# Run: bazel run //:requirements_test.update
# --extra=privacy pulls spaCy so the ty/pylint typecheck targets can resolve the
# anonymizer's NER imports (see //teachers_teammate:gui_lib_typed / ocr_lib_typed).
compile_pip_requirements(
    name = "requirements_test",
    src = "pyproject.toml",
    data = ["//teachers_teammate:__init__.py"],
    extra_args = [
        "--extra=test",
        "--extra=privacy",
    ],
    requirements_txt = "requirements_test_lock.txt",
)

alias(
    name = "format.check",
    actual = "//third_party/format:format.check",
)

alias(
    name = "format.fix",
    actual = "//third_party/format:format",
)

alias(
    name = "buildifier.fix",
    actual = "@buildifier_prebuilt//:buildifier",
)
