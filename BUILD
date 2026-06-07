load("@rules_python//python:pip.bzl", "compile_pip_requirements")
load("//third_party/lint:linters.bzl", "pylint_test", "ruff_test", "ty_test")

# pyproject.toml is needed by pylint/ty via rules_lint.
exports_files(
    [
        "pyproject.toml",
        "README.md",
    ],
    visibility = ["//visibility:public"],
)

# pylint resolves imports by importing modules in its own (dev-hub) environment;
# the rules_lint pylint aspect passes only source files, not target deps. So unlike
# py_ty there's no benefit to the *_typed libs here — PySide6/qdarkstyle simply
# aren't importable by pylint and are carved out via [tool.pylint.typecheck]
# ignored-modules in pyproject.toml.
pylint_test(
    name = "py_pylint",
    srcs = [
        "//teachers_teammate:gui_lib",
        "//teachers_teammate:ocr_lib",
        "//tests:tests_lib",
    ],
)

ruff_test(
    name = "py_ruff",
    srcs = [
        "//teachers_teammate:gui_lib",
        "//teachers_teammate:ocr_lib",
        "//tests:tests_lib",
    ],
)

ty_test(
    name = "py_ty",
    srcs = [
        "//teachers_teammate:gui_lib_typed",
        "//teachers_teammate:ocr_lib_typed",
        "//tests:tests_lib",
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
