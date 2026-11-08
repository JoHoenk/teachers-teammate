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

# The writing-model tools' shared work: pure metrics come from the shipped domain
# (teachers_teammate/domain/proofreading_quality.py), everything that needs a server sits
# behind the ChatPort seam (ports.py), so the rules are tested without Ollama.
# See docs/development.md → "Ollama model catalog".
py_library(
    name = "model_eval",
    srcs = glob(["model_eval/*.py"]),
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:ocr_lib"],
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
    data = ["gec_fr_invented.jsonl"],
    main = "prepare_gec_testset.py",
    visibility = ["//visibility:public"],
    deps = [":model_eval"],
)
