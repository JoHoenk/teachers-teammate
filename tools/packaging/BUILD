"""Bazel targets for the packaging tools.

The installers themselves run as programs (make_deb.sh, make_dmg.sh, windows.nsi);
latest_release.py and check_desktop_entry.py are also libraries, so a unit test can drive them
without a network or an installed package.
"""

load("@rules_python//python:defs.bzl", "py_library")
load("@rules_shell//shell:sh_library.bzl", "sh_library")

# windows.nsi for the static tests of the installer; the three install tests for the test that
# they walk through the installed app after its smoke test.
exports_files(
    [
        "test_deb_install.sh",
        "test_macos_install.sh",
        "test_windows_installer.ps1",
        "windows.nsi",
    ],
    visibility = ["//tests:__pkg__"],
)

py_library(
    name = "lint_lib",
    srcs = glob(["*.py"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
    deps = ["@ocr_test_dependencies//pillow:pkg"],  # make_ico.py, so ty knows its types
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

# The PowerShell scripts, for //:ps_script_analyzer (rules_lint's PSScriptAnalyzer aspect visits
# filegroups with this tag).
filegroup(
    name = "powershell_scripts",
    srcs = glob(["*.ps1"]),
    tags = [
        "lint-with-psscriptanalyzer",
        "manual",
    ],
    visibility = ["//visibility:public"],
)

# The shell scripts, for //:sh_shellcheck (rules_lint's shellcheck visits sh_library targets).
sh_library(
    name = "scripts",
    srcs = glob(["*.sh"]),
    tags = ["manual"],
    visibility = ["//visibility:public"],
)
