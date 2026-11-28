"""Bazel targets for the OCR evaluation tooling: reading models compared on one page set.

Three libraries, innermost first, each importing only the ones before it:

* `records`: the record format and what reads it back (`presets`, `records`, `runs`, `report`),
  no page and no engine; `validate` and `report` need nothing more.
* `pages`: the page set, which `tools/dev`'s line probes read too.
* `lib`: the rest — the preparation, the engines, the scores, the cache, the run, the screen.

`cli` is the command line a developer starts.  The tool is never part of a wheel, a build or a
gate.
"""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# The app below the GUI: the preparation, the reader, the Ollama client, the answer handling and
# the model catalog.  `infrastructure_lib` brings the layers below it.
_APP = ["//teachers_teammate:infrastructure_lib"]

_RECORDS_SRCS = [
    "__init__.py",
    "presets.py",
    "records.py",
    "report.py",
    "runs.py",
]

_PAGES_SRCS = ["pages.py"]

py_library(
    name = "records",
    srcs = _RECORDS_SRCS,
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:domain_lib",
        "//tools/evaluation:core",
    ],
)

py_library(
    name = "pages",
    srcs = _PAGES_SRCS,
    visibility = ["//visibility:public"],
    deps = _APP + [
        ":records",
        "//tools/evaluation:core",
        "@ocr_test_dependencies//pillow:pkg",
        "@ocr_test_dependencies//pypdfium2:pkg",
    ],
)

# What the rest needs at start-up: the ocr_formats parsers (lxml, markdown_it), the server client
# (ollama), the preparation and the readings (numpy, opencv, pillow), where the readings sit
# (platformdirs), and the character distance a page is scored with (rapidfuzz).
_RUNTIME = [
    "@ocr_test_dependencies//lxml:pkg",
    "@ocr_test_dependencies//markdown_it_py:pkg",
    "@ocr_test_dependencies//numpy:pkg",
    "@ocr_test_dependencies//ollama:pkg",
    "@ocr_test_dependencies//opencv_python_headless:pkg",
    "@ocr_test_dependencies//pillow:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
    "@ocr_test_dependencies//pydantic:pkg",
    "@ocr_test_dependencies//pypdfium2:pkg",
    "@ocr_test_dependencies//psutil:pkg",
    "@ocr_test_dependencies//pyyaml:pkg",
    "@ocr_test_dependencies//rapidfuzz:pkg",
]

py_library(
    name = "lib",
    srcs = glob(
        ["*.py"],
        exclude = _RECORDS_SRCS + _PAGES_SRCS,
    ),
    visibility = ["//visibility:public"],
    deps = _APP + [
        ":pages",
        ":records",
        "//tools/evaluation:core",
    ] + _RUNTIME,
)

# Developer tool (needs a running Ollama server and the developer's own page set; not run in CI)
# that reads a page set with one engine and writes the numbers to the store.  See
# evaluations/TOOLS.md.
py_binary(
    name = "cli",
    srcs = ["cli.py"],
    main = "cli.py",
    visibility = ["//visibility:public"],
    deps = [":lib"],
)

# Every module of the package, for the tests that scan the tools' sources.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)
