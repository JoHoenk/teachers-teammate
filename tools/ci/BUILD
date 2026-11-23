load("@rules_python//python:defs.bzl", "py_library")

# Every script of this folder, for //:py_pylint, //:py_ruff and //:py_ty.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)

py_library(
    name = "all_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
)

py_library(
    name = "ci_tools_lib",
    srcs = [
        "__init__.py",
        "target_groups.py",
    ],
    visibility = ["//visibility:public"],
)
