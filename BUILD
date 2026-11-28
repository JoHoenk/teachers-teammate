load("@rules_python//python:defs.bzl", "py_library")
load("@rules_python//python:pip.bzl", "compile_pip_requirements")
load("//third_party/lint:linters.bzl", "ps_script_analyzer_test", "ruff_test", "shellcheck_test", "ty_test")

# pyproject.toml is needed by ruff/ty via rules_lint.  `.python-version` is the one place the
# Python version is written down; //tests:test_python_version holds the interpreter to it.
exports_files(
    [
        ".python-version",
        "AGENTS.md",
        "LICENSE",
        "NOTICE",
        "pyproject.toml",
        "README.md",
        "requirements_addons_lock.txt",
        "requirements_dev_lock.txt",
        "requirements_lock.txt",
    ],
    visibility = ["//visibility:public"],
)

# The repository's own configuration, for //tests:test_no_plan_references: a comment in these
# files is as visible to a reader as one in a source file.
filegroup(
    name = "repo_config",
    srcs = [
        ".bazelignore",
        ".bazelrc",
        ".gitignore",
        "BUILD",
        "MODULE.bazel",
        "pyproject.toml",
    ] + glob([".github/**/*.yml"]),
    visibility = ["//visibility:public"],
)

# The license store: the inventory, the texts the tool collects, the curated folders a person
# owns, and the compiled outputs.  One filegroup so the offline store test can read all of it.
filegroup(
    name = "license_store",
    srcs = glob(["third_party_licenses/**"]),
    visibility = ["//visibility:public"],
)

# The libraries ruff and ty visit: one per package of Python, each holding every file of its
# package that the narrow libraries below it do not. A new package adds its library here, and
# tools/ci/check_lint_coverage.py fails the lint job when one is missing.
_LINTED = [
    "//teachers_teammate:gui_lib_typed",
    "//teachers_teammate:ocr_lib_typed",
    "//tests:tests_lib",
    "//tools:evaluate",
    "//tools:prepare_gec_testset",
    "//tools:report",
    "//tools:scripts_lib",
    "//tools:store",
    "//tools:verify_model_catalog",
    "//tools/build:lint_lib",
    "//tools/dev:lint_lib",
    "//tools/ci:lint_lib",
    "//tools/evaluation:checks",
    "//tools/evaluation:convert",
    "//tools/evaluation:core",
    "//tools/evaluation:input",
    "//tools/evaluation:metrics",
    "//tools/evaluation:report",
    "//tools/evaluation:run",
    "//tools/evaluation:store",
    "//tools/evaluation:viewer",
    "//tools/licenses:lint_lib",
    "//tools/ocr_eval:lib",
    "//tools/ocr_eval:pages",
    "//tools/ocr_eval:records",
    "//tools/packaging:lint_lib",
    "//tools/release:lint_lib",
    "//tools/user_text:user_text_lib",
]

ruff_test(
    name = "py_ruff",
    srcs = _LINTED,
)

ty_test(
    name = "py_ty",
    srcs = _LINTED,
)

# Every shell script of the repository (the packaging and build scripts the workflows run).
shellcheck_test(
    name = "sh_shellcheck",
    srcs = [
        "//tools/build:scripts",
        "//tools/packaging:scripts",
    ],
)

# The PowerShell scripts (the Windows installer build and its test). The PowerShell binary comes
# from a Linux archive (MODULE.bazel), so the test is skipped on other systems.
ps_script_analyzer_test(
    name = "ps_script_analyzer",
    srcs = ["//tools/packaging:powershell_scripts"],
    target_compatible_with = ["@platforms//os:linux"],
)

test_suite(
    name = "lint",
    tests = [
        ":ps_script_analyzer",
        ":py_ruff",
        ":py_ty",
        ":sh_shellcheck",
    ],
)

# Lock files are compiled from the [project.optional-dependencies] extras in
# pyproject.toml — the single source of truth for every pip dependency. Each
# `--extra` also pulls the base runtime deps, so the locks are self-contained.
#
# `data` stages teachers_teammate/__init__.py beside pyproject.toml so pip-compile
# can expand the dynamic version (attr = teachers_teammate.__version__).
#
# Run: bazel run //:requirements_dev.update
compile_pip_requirements(
    name = "requirements_dev",
    src = "pyproject.toml",
    data = ["//teachers_teammate:__init__.py"],
    extra_args = ["--extra=dev"],
    requirements_txt = "requirements_dev_lock.txt",
)

# The lock the bundle's runtime and the tests install from: a bump here changes what teachers
# receive, not only what CI runs.
#
# Run: bazel run //:requirements.update
# --extra=privacy pulls spaCy so the ty typecheck targets can resolve the
# anonymizer's NER imports (see //teachers_teammate:gui_lib_typed / ocr_lib_typed).
compile_pip_requirements(
    name = "requirements",
    src = "pyproject.toml",
    data = ["//teachers_teammate:__init__.py"],
    extra_args = [
        "--extra=test",
        "--extra=privacy",
    ],
    requirements_txt = "requirements_lock.txt",
)

# Run: bazel run //:requirements_addons.update
# The pip add-ons the app installs at runtime (Settings → Add-ons), with their whole closure and
# hashes.  The build turns it into the flat ``addon_lock.txt`` the installed app gives to pip
# (tools/build/addon_lock.py), so a release installs the add-on versions it was tested with, and
# tools/licenses/inventory.py reads the same lock for the license inventory.
compile_pip_requirements(
    name = "requirements_addons",
    src = "pyproject.toml",
    data = ["//teachers_teammate:__init__.py"],
    extra_args = [
        "--extra=llm",
        "--extra=privacy",
    ],
    requirements_txt = "requirements_addons_lock.txt",
)

# The lock files checked against pyproject.toml and against one another, as CI runs them: one
# label to hold a dependency change against before pushing it.
test_suite(
    name = "lock_tests",
    tests = [
        ":requirements.test",
        ":requirements_addons.test",
        ":requirements_dev.test",
        "//tests:test_lock_agreement",
    ],
)

alias(
    name = "format.check",
    actual = "//third_party/format:format.check",
)

alias(
    name = "format.fix",
    actual = "//third_party/format:format",
)

alias(
    name = "buildifier.fix",
    actual = "@buildifier_prebuilt//:buildifier",
)
