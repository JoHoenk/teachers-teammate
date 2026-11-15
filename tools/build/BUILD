"""Bazel targets for the standalone-build tools.

The other scripts in this folder run as programs (build_standalone.py, check_pins.py,
run_gui.py); addon_constraints.py, check_addon_resolution.py, make_constraints.py, check_pins.py
and check_linux_bundle.py are also libraries, so a unit test can drive them.
"""

py_library(
    name = "build_tools_lib",
    srcs = [
        "__init__.py",
        "addon_constraints.py",
        "check_addon_resolution.py",
        "check_linux_bundle.py",
        "check_pins.py",
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
