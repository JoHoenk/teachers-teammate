"""Bazel targets for the teacher-facing text inventory.

``inventory.py`` is both a program (a maintainer writes the migration CSV with it) and a
library, so the ratchet test can scan the sources without a second copy of the sink tables.
Standard library only: the test that uses it needs nothing but this library.
"""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# The tool runs as a program, so nothing imports it from the app; this keeps it in the lint set.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)

py_library(
    name = "user_text_lib",
    srcs = [
        "__init__.py",
        "allowlist.py",
        "inventory.py",
    ],
    visibility = ["//visibility:public"],
)

py_binary(
    name = "inventory",
    srcs = ["inventory.py"],
    main = "inventory.py",
    visibility = ["//visibility:public"],
    deps = [":user_text_lib"],
)
