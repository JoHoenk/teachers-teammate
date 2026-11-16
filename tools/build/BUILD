"""Bazel targets for the standalone-build tools.

The other scripts in this folder run as programs (build_standalone.py, check_pins.py,
run_gui.py); addon_constraints.py, addon_lock.py, check_addon_resolution.py, fetch_models.py,
make_constraints.py, check_pins.py, check_linux_bundle.py and check_macos_bundle.py are also
libraries, so a unit test can drive them.  The license inventory lives in //tools/licenses.
"""

load("@rules_python//python:defs.bzl", "py_library")

# The platform pins: the packages the Linux lock cannot hold, or that only the build needs.
# Read by the license inventory (a row per pin) and by the store test.  The spec is read by the
# smoke test's unit test, which keeps its GPL-only Qt list equal to the one in frozen_modes.py.
exports_files(
    [
        "requirements_platform.txt",
        "teachers-teammate.spec",
    ],
    visibility = ["//visibility:public"],
)

py_library(
    name = "build_tools_lib",
    srcs = [
        "__init__.py",
        "addon_constraints.py",
        "addon_lock.py",
        "check_addon_resolution.py",
        "check_linux_bundle.py",
        "check_macos_bundle.py",
        "check_pins.py",
        "fetch_models.py",
        "make_constraints.py",
    ],
    visibility = ["//visibility:public"],
    deps = ["@ocr_test_dependencies//packaging:pkg"],
)

# The scripts here run as programs, so nothing imports them; this keeps them in the lint set.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)
