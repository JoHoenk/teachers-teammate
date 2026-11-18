load("@rules_python//python:defs.bzl", "py_binary", "py_library")

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
    name = "release_tools_lib",
    srcs = [
        "__init__.py",
        "release_tag.py",
        "stamp_versions.py",
    ],
    visibility = ["//visibility:public"],
)

# Extracts [project.dependencies] from pyproject.toml so the wheel's
# Requires-Dist is single-sourced from pyproject (see //teachers_teammate:BUILD).
py_binary(
    name = "extract_wheel_requires",
    srcs = ["extract_wheel_requires.py"],
    visibility = ["//visibility:public"],
)
