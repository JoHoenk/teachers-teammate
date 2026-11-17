"""Bazel targets for the packaging tools.

The installers themselves run as programs (make_deb.sh, make_dmg.sh, windows.nsi);
latest_release.py and check_desktop_entry.py are also libraries, so a unit test can drive them
without a network or an installed package.
"""

load("@rules_python//python:defs.bzl", "py_library")

exports_files(
    ["windows.nsi"],
    visibility = ["//tests:__pkg__"],
)

py_library(
    name = "packaging_tools_lib",
    srcs = [
        "__init__.py",
        "check_desktop_entry.py",
        "latest_release.py",
    ],
    visibility = ["//visibility:public"],
)
