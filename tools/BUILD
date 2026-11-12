"""Bazel targets for developer tools."""

py_binary(
    name = "extract_test_specs",
    srcs = ["extract_test_specs.py"],
    main = "extract_test_specs.py",
    visibility = ["//docs:__pkg__"],
)

# Every developer-tool source, for //:py_pylint (one action over the whole first-party tree, so
# pylint can resolve the tools' first-party imports). Not added to any wheel.
filegroup(
    name = "all_sources",
    srcs = glob(["**/*.py"]),
    visibility = ["//visibility:public"],
)

# The writing-model tools' shared work: the pure metrics (model_eval/quality.py) and everything
# that needs a server, which sits behind the ChatPort seam (ports.py), so the rules are tested
# without Ollama.
# protocol.json is the version file of the request side (eval_version and its fingerprint); it is
# data, so a run and the export read the same file.  See docs/development.md → "Ollama model
# catalog".
py_library(
    name = "model_eval",
    srcs = glob(["model_eval/**/*.py"]),
    data = [
        "gec_fr_invented.jsonl",
        "model_eval/protocol.json",
    ],
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:ocr_lib"],
)

# The memory agent: one file, standard library only, no import from this repository, so it can be
# copied alone to the machine with the graphics card. It is also part of :model_eval (the glob
# above), which is harmless; the AST test in tests/tools/test_gpu_agent.py enforces the rule.
py_binary(
    name = "gpu_agent",
    srcs = ["model_eval/gpu_agent.py"],
    main = "model_eval/gpu_agent.py",
    visibility = ["//visibility:public"],
)

# Developer tool (needs network; not run in CI) that verifies the Ollama model catalog against
# the real registry. Three subcommands: sizes, capabilities, structured-output.
# pydantic is needed by teachers_teammate.infrastructure.correction_schema, which the
# structured-output check uses to build and validate the review schema; ollama is the client
# the check sends its requests with; lxml and markdown_it are imported by ocr_formats, which
# TextFormatter.plain_text() reaches for the Markdown fixture.
py_binary(
    name = "verify_model_catalog",
    srcs = ["verify_model_catalog.py"],
    main = "verify_model_catalog.py",
    visibility = ["//visibility:public"],
    deps = [
        ":model_eval",
        "//teachers_teammate:ocr_lib",
        "@ocr_test_dependencies//lxml:pkg",
        "@ocr_test_dependencies//markdown_it_py:pkg",
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//pydantic:pkg",
    ],
)

# Developer tool (needs a running Ollama server; not run in CI) that compares writing models on
# learner texts through the app's own corrector. See docs/development.md → "Ollama model
# catalog". Dependencies as for verify_model_catalog: the corrector pulls in pydantic and the
# ocr_formats parsers (lxml, markdown_it), the client needs ollama.
py_binary(
    name = "compare_writing_models",
    srcs = ["compare_writing_models.py"],
    main = "compare_writing_models.py",
    visibility = ["//visibility:public"],
    deps = [
        ":model_eval",
        "//teachers_teammate:ocr_lib",
        "@ocr_test_dependencies//lxml:pkg",
        "@ocr_test_dependencies//markdown_it_py:pkg",
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//pydantic:pkg",
    ],
)

# Developer tool (standard library only, writes the test set for compare_writing_models).
py_binary(
    name = "prepare_gec_testset",
    srcs = ["prepare_gec_testset.py"],
    main = "prepare_gec_testset.py",
    visibility = ["//visibility:public"],
    deps = [":model_eval"],
)

# The same module as a library, so a py_test can import it: a py_binary is not a dependency a test
# can import from. The test drives `_write_manifest` on the invented corpus, without the selection.
py_library(
    name = "prepare_gec_testset_lib",
    srcs = ["prepare_gec_testset.py"],
    visibility = ["//visibility:public"],
    deps = [":model_eval"],
)

# Developer tool that turns a local run cache into the committed run files below
# evaluations/proofreading and checks them again: export, validate, index, manifest.
# run_format.py and protocol.py import teachers_teammate.infrastructure.correction (they hash the
# prompts the app sends), which needs pydantic (correction_schema) and, through ocr_formats, lxml
# and markdown_it.
py_binary(
    name = "proofreading_store",
    srcs = ["proofreading_store.py"],
    main = "proofreading_store.py",
    visibility = ["//visibility:public"],
    deps = [
        ":model_eval",
        "//teachers_teammate:ocr_lib",
        "@ocr_test_dependencies//lxml:pkg",
        "@ocr_test_dependencies//markdown_it_py:pkg",
        "@ocr_test_dependencies//ollama:pkg",
        "@ocr_test_dependencies//pydantic:pkg",
    ],
)
