"""Bazel targets for developer tools."""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# The invented French set is read by //tools/evaluation:input, so it is exported rather than only
# named as data of one target.
exports_files(
    ["gec_fr_invented.jsonl"],
    visibility = ["//visibility:public"],
)

# What a tool that reaches the app's proofreading infrastructure needs at start-up: the corrector
# pulls in pydantic and the ocr_formats parsers (lxml, markdown_it), and the client needs ollama.
# One list, so a new tool cannot forget one of them and fail only when it is run.
_APP_RUNTIME = [
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//markdown_it_py:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//pydantic:pkg",
]

py_binary(
    name = "extract_test_specs",
    srcs = ["extract_test_specs.py"],
    main = "extract_test_specs.py",
    visibility = [
        "//docs:__pkg__",
        "//tests:__pkg__",
    ],
)

# Every developer-tool source, for //:py_pylint (one action over the whole first-party tree, so
# pylint can resolve the tools' first-party imports). Not added to any wheel.
filegroup(
    name = "all_sources",
    srcs = glob(["**/*.py"]),
    visibility = ["//visibility:public"],
)

# Every script of this folder as one library, for //:py_ruff and //:py_ty. The binaries below
# each hold one script with its own dependencies; this one lets the linters see all of them, so a
# new script is linted without anybody remembering to add it to the root BUILD.
py_library(
    name = "scripts_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:report",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
        "//tools/evaluation:viewer",
        "@ocr_test_dependencies//numpy:pkg",
        "@ocr_test_dependencies//opencv_python_headless:pkg",
        "@ocr_test_dependencies//pillow:pkg",
    ] + _APP_RUNTIME,
)

# Developer tool (needs network; not run in CI) that verifies the Ollama model catalog against
# the real registry. Six subcommands: sizes, capabilities, structured-output, smoke, fit, recheck.
# pydantic is needed by tools.evaluation.run.one_request.correction_schema, which the
# structured-output check uses to build and validate the review schema; ollama is the client
# the check sends its requests with; lxml and markdown_it are imported by ocr_formats, which
# TextFormatter.plain_text() reaches for the Markdown fixture.
py_binary(
    name = "verify_model_catalog",
    srcs = ["verify_model_catalog.py"],
    main = "verify_model_catalog.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
    ] + _APP_RUNTIME,
)

# Developer tool (needs a running Ollama server; not run in CI) that runs writing models over a
# test set through the app's own corrector.  See docs/development.md -> "Ollama model catalog".
# The checks it screens a setup with sit above `run`, so they are named here and not pulled in by
# it.
py_binary(
    name = "evaluate",
    srcs = ["evaluate.py"],
    main = "evaluate.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:checks",
        "//tools/evaluation:core",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ] + _APP_RUNTIME,
)

# Developer tool (needs a running Ollama server and the developer's own benchmark pages; not run
# in CI) that measures what a reading model costs on one page: the answer tokens per page and the
# words per page, which are the catalog's answer_length and TYPICAL_PAGE_WORDS.
py_binary(
    name = "ocr_page_tokens",
    srcs = ["ocr_page_tokens.py"],
    main = "ocr_page_tokens.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:core",
        "@ocr_test_dependencies//numpy:pkg",
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//opencv_python_headless:pkg",
        "@ocr_test_dependencies//pillow:pkg",
    ],
)

# Developer tool (standard library only, writes the test set for `evaluate`).
py_binary(
    name = "prepare_gec_testset",
    srcs = ["prepare_gec_testset.py"],
    main = "prepare_gec_testset.py",
    visibility = ["//visibility:public"],
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:store",
    ],
)

# The same module as a library, so a py_test can import it: a py_binary is not a dependency a test
# can import from. The test drives `_check` on the invented corpus, without the selection.
py_library(
    name = "prepare_gec_testset_lib",
    srcs = ["prepare_gec_testset.py"],
    visibility = ["//visibility:public"],
    deps = [
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:store",
    ],
)

# Developer tool that reads a store of runs: the tables, the comparisons, the gates, the sheets and
# the static page.  It imports the viewer, which reads run.protocol (it hashes the prompts the app
# sends), so it needs the app's runtime packages.
py_binary(
    name = "report",
    srcs = ["report.py"],
    main = "report.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:report",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
        "//tools/evaluation:viewer",
    ] + _APP_RUNTIME,
)

# Developer tool that keeps the store and the human data beside it: the one-off conversion of the
# old folders, the check of the store, the marks and the accepted alternatives, and what a manifest
# pins.
py_binary(
    name = "store",
    srcs = ["store.py"],
    main = "store.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:convert",
        "//tools/evaluation:core",
        "//tools/evaluation:input",
        "//tools/evaluation:report",
        "//tools/evaluation:run",
        "//tools/evaluation:store",
    ] + _APP_RUNTIME,
)
