load("@rules_python//python:defs.bzl", "py_binary", "py_library")

py_library(
    name = "lint_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = ["//tools/licenses:artifact_checks_lib"],
)

py_library(
    name = "release_tools_lib",
    srcs = [
        "__init__.py",
        "mirror_sources.py",
        "release_tag.py",
        "stamp_versions.py",
    ],
    visibility = ["//visibility:public"],
    deps = ["//tools/licenses:artifact_checks_lib"],
)

# The mirror runs as a program: it fetches the archives a sources release holds.
py_binary(
    name = "mirror_sources",
    srcs = ["mirror_sources.py"],
    main = "mirror_sources.py",
    visibility = ["//visibility:public"],
    deps = ["//tools/licenses:artifact_checks_lib"],
)

# Extracts [project.dependencies] from pyproject.toml so the wheel's
# Requires-Dist is single-sourced from pyproject (see //teachers_teammate:BUILD).
py_binary(
    name = "extract_wheel_requires",
    srcs = ["extract_wheel_requires.py"],
    visibility = ["//visibility:public"],
)
