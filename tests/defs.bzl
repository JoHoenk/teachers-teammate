"""The macro a test target of this package is written with.

``app_test`` takes the file to run. Everything that follows from the file is derived: the target's
name, the folder tag that its folder's suite selects on, and ``run_test.py`` as the entry point.
What a test needs beyond that — its layers, its pip packages, its data files, its extra tags — is
passed in.

``tests/unit/test_test_targets.py`` holds these targets to the test files of the package.
"""

load("@rules_python//python:defs.bzl", "py_test")

#: The files every test target carries: the pytest entry point, the shared fixtures, and the
#: helpers more than one test imports.
COMMON_SRCS = [
    "run_test.py",
    "conftest.py",
    "__init__.py",
    "unit/__init__.py",
    "unit/_service_helpers.py",
    "integration/__init__.py",
    "gui/__init__.py",
    "gui/_service_stubs.py",
    "tools/__init__.py",
    "fakes/__init__.py",
    "fakes/fake_port.py",
]

#: What every test needs: the proofreading steps' schemas are pydantic models, and the app reads
#: its folders from platformdirs, so any import of the app brings both in.
COMMON_DEPS = [
    "@ocr_test_dependencies//pydantic:pkg",
    "@ocr_test_dependencies//platformdirs:pkg",
    "@ocr_test_dependencies//pytest:pkg",
    "@ocr_test_dependencies//pytest_mock:pkg",
]

def app_test(
    src, size = "small", deps = [], data = [], env = {}, tags = [], extra_srcs = [], **kwargs
):
    """Add the test target of one test file.

    Args:
        src: the test file, ``<folder>/test_<topic>.py``. The target is named after it and
            carries ``<folder>`` as its first tag.
        size: the timeout class, when the test needs more than ``"small"``.
        deps: the layers and pip packages the file imports, on top of ``COMMON_DEPS``.
        data: the files it reads at run time.
        env: where the test finds those files.
        tags: further tags, such as ``walkthrough``. Never a folder tag.
        extra_srcs: helpers it shares with no other test, or another test file it imports.
        **kwargs: a further attribute of the rule, such as ``visibility``.
    """
    py_test(
        name = src.split("/")[-1][:-3],
        size = size,
        srcs = COMMON_SRCS + extra_srcs + [src],
        args = [src],
        main = "run_test.py",
        deps = COMMON_DEPS + deps,
        data = data,
        env = env,
        tags = [src.split("/")[0]] + tags,
        **kwargs
    )
