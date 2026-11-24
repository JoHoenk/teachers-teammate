load("@rules_python//python:defs.bzl", "py_library")

py_library(
    name = "lint_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
)

py_library(
    name = "ci_tools_lib",
    srcs = [
        "__init__.py",
        "check_lint_coverage.py",
        "target_groups.py",
    ],
    visibility = ["//visibility:public"],
)
