"""The probes: tools a developer starts by hand, none of them part of a build or a gate."""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# What the type checker needs to resolve the app's proofreading imports (pydantic, the ocr_formats
# parsers, the ollama client). Only `lint_lib` takes it: neither probe imports those at start-up.
_APP_RUNTIME = [
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//markdown_it_py:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//pydantic:pkg",
]

# Developer probe (needs network and a Mistral key; not run in CI) that asks the global and the EU
# Mistral endpoint which models they serve.  The model list code it shares with the app imports
# nothing beyond the standard library at start-up.
py_binary(
    name = "compare_mistral_regions",
    srcs = ["compare_mistral_regions.py"],
    main = "compare_mistral_regions.py",
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:application_lib"],
)

# The same module as a library, so a py_test can import it.
py_library(
    name = "compare_mistral_regions_lib",
    srcs = ["compare_mistral_regions.py"],
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:application_lib"],
)

# Developer probe (needs a running Ollama server and the developer's own test pages; not run
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
        "@ocr_test_dependencies//platformdirs:pkg",
    ],
)

# Developer probe (needs the built-in reader's models and the developer's own pages; not run in
# CI) that asks whether the reader's rows line up with the written lines of a page, and how many
# rows a confidence threshold would have to mark to catch the reading errors.
py_binary(
    name = "page_lines",
    srcs = ["page_lines.py"],
    main = "page_lines.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "@ocr_test_dependencies//numpy:pkg",
        "@ocr_test_dependencies//opencv_python_headless:pkg",
        "@ocr_test_dependencies//pillow:pkg",
        "@ocr_test_dependencies//platformdirs:pkg",
        "@ocr_test_dependencies//pypdfium2:pkg",
        "@ocr_test_dependencies//pyyaml:pkg",
    ],
)

# Developer probe (not run in CI) that writes the reference transcriptions the probe above
# measures against, from a Chandra OCR run of the same pages: one line per written line, named and
# placed where the line measure looks for it.
py_binary(
    name = "chandra_gt",
    srcs = ["chandra_gt.py"],
    main = "chandra_gt.py",
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/dev:page_lines",
        "@ocr_test_dependencies//numpy:pkg",
        "@ocr_test_dependencies//opencv_python_headless:pkg",
        "@ocr_test_dependencies//pillow:pkg",
        "@ocr_test_dependencies//platformdirs:pkg",
        "@ocr_test_dependencies//pypdfium2:pkg",
        "@ocr_test_dependencies//pyyaml:pkg",
    ],
)

# Every probe's source, for the tests that scan the tools' sources (//tools:all_sources does not
# reach into this package). Not added to any wheel.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)

# Every probe of this package as one library, for //:py_ruff and //:py_ty. The binaries above each
# hold one script with its own dependencies; this one lets the linters see all of them.
py_library(
    name = "lint_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:application_lib",
        "//tools/evaluation:core",
        "@ocr_test_dependencies//numpy:pkg",
        "@ocr_test_dependencies//opencv_python_headless:pkg",
        "@ocr_test_dependencies//pillow:pkg",
        "@ocr_test_dependencies//platformdirs:pkg",
        "@ocr_test_dependencies//pypdfium2:pkg",
    ] + _APP_RUNTIME,
)
