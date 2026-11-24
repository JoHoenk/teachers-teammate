"""Bazel targets for the license inventory.

``inventory.py`` is both a program (a maintainer refreshes the store with it) and a library, so
the offline tests can drive the closure walk, the policy and the compile without the network.
"""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# The modules a build or release check imports: they read NOTICE and the inventory and compare
# them with what a download carries, so they take standard library imports only and run in a bare
# CI job.
py_library(
    name = "artifact_checks_lib",
    srcs = [
        "__init__.py",
        "artifacts.py",
        "notice.py",
    ],
    visibility = ["//visibility:public"],
)

# Every script of this folder, for the lint set in the root BUILD: licenses_lib below holds the
# one a unit test imports, and the rest are the checks the build and release jobs run.
py_library(
    name = "lint_lib",
    srcs = glob(["*.py"]),
    # inventory.py reads the app's model catalog and add-on lists, so the linter has to resolve
    # the package's layers.
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:domain_lib",
        "//teachers_teammate:infrastructure_lib",
    ],
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
