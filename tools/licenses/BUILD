"""Bazel targets for the license inventory.

``inventory.py`` is both a program (a maintainer refreshes the store with it) and a library, so
the offline tests can drive the closure walk, the policy and the compile without the network.
"""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# The tool runs as a program, so nothing imports it from the app; this keeps it in the lint set.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)

py_library(
    name = "licenses_lib",
    srcs = [
        "__init__.py",
        "inventory.py",
    ],
    data = ["policy.toml"],
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:ocr_lib"],
)

py_binary(
    name = "inventory",
    srcs = ["inventory.py"],
    data = ["policy.toml"],
    main = "inventory.py",
    visibility = ["//visibility:public"],
    deps = ["//teachers_teammate:ocr_lib"],
)
