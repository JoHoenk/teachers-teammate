"""Bazel targets for the standalone-build tools.

The other scripts in this folder run as programs (build_standalone.py);
addon_constraints.py, addon_lock.py, check_addon_resolution.py, fetch_models.py,
make_constraints.py, make_venv.py, check_pins.py, check_linux_bundle.py and check_macos_bundle.py
are also libraries, so a unit test can drive them.  The license inventory lives in
//tools/licenses.
"""

load("@rules_python//python:defs.bzl", "py_library")
load("@rules_shell//shell:sh_library.bzl", "sh_library")

# The platform pins: the packages the Linux lock cannot hold, or that only the build needs.
# Read by the license inventory (a row per pin) and by the store test.  The spec is read by the
# smoke test's unit test, which keeps its GPL-only Qt list equal to the one in frozen_modes.py.
exports_files(
    [
        "requirements_addons_platform.txt",
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
        "make_venv.py",
        "make_version_resource.py",
        "platform_lock.py",
    ],
    visibility = ["//visibility:public"],
    deps = [
        "//tools/licenses:artifact_checks_lib",
        "@ocr_test_dependencies//packaging:pkg",
    ],
)

# Every script of this folder, for the lint set in the root BUILD: build_tools_lib above holds
# only the ones a unit test imports, and the rest (build_standalone.py, ...) is what ships.
py_library(
    name = "lint_lib",
    srcs = glob(["*.py"]),
    # The checks read the app's model catalog and its add-on resolution, so the linter has to
    # resolve the package's layers.
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = [
        "//teachers_teammate:domain_lib",
        "//teachers_teammate:infrastructure_lib",
        "//teachers_teammate:pkg_lib",
        "//tools/licenses:artifact_checks_lib",
        "@ocr_test_dependencies//packaging:pkg",
    ],
)

# The shell scripts, for //:sh_shellcheck (rules_lint's shellcheck visits sh_library targets).
sh_library(
    name = "scripts",
    srcs = glob(["*.sh"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
)
