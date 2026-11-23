"""Bazel targets for the standalone-build tools.

The other scripts in this folder run as programs (build_standalone.py, check_pins.py,
run_gui.py); addon_constraints.py, addon_lock.py, check_addon_resolution.py, fetch_models.py,
make_constraints.py, check_pins.py, check_linux_bundle.py and check_macos_bundle.py are also
libraries, so a unit test can drive them.  The license inventory lives in //tools/licenses.
"""

load("@rules_python//python:defs.bzl", "py_library")
load("@rules_shell//shell:sh_library.bzl", "sh_library")

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
        "addon_pins.py",
        "check_addon_resolution.py",
        "check_linux_bundle.py",
        "check_macos_bundle.py",
        "check_pins.py",
        "check_smoke.py",
        "fetch_models.py",
        "make_constraints.py",
        "make_version_resource.py",
        "platform_lock.py",
    ],
    visibility = ["//visibility:public"],
    deps = ["@ocr_test_dependencies//packaging:pkg"],
)

# Every script of this folder, for //:py_ruff and //:py_ty: build_tools_lib above holds only the
# ones a unit test imports, and the rest (build_standalone.py, run_gui.py, ...) is what ships.
py_library(
    name = "all_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = ["@ocr_test_dependencies//packaging:pkg"],
)

# The scripts here run as programs, so nothing imports them; this keeps them in the lint set.
filegroup(
    name = "all_sources",
    srcs = glob(["*.py"]),
    visibility = ["//visibility:public"],
)

# The shell scripts, for //:sh_shellcheck (rules_lint's shellcheck visits sh_library targets).
sh_library(
    name = "scripts",
    srcs = glob(["*.sh"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
)
