"""Bazel targets for developer tools."""

py_binary(
    name = "extract_test_specs",
    srcs = ["extract_test_specs.py"],
    main = "extract_test_specs.py",
    visibility = ["//docs:__pkg__"],
)

# Developer tool (needs network; not run in CI) that verifies the Ollama model
# catalog against the real registry. See docs/development.md → "Ollama model catalog".
py_binary(
    name = "verify_model_catalog",
    srcs = ["verify_model_catalog.py"],
    main = "verify_model_catalog.py",
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:ocr_lib"],
)
