"""The macro the app's wheel targets are written with.

Everything a wheel of this package is — archive of the whole package, its extras, its entry
points — is the same for the development build and the release build. Only the version differs,
so ``app_wheel`` takes it as its argument.

``tests/unit/test_wheel.py`` holds the result to the source tree and to ``pyproject.toml``.
"""

load("@rules_python//python:packaging.bzl", "py_wheel")

def app_wheel(name, version, tags = []):
    """Write one wheel target of this package.

    Args:
        name: the target's name. Its ``.dist`` folder is where a stamped build writes the file.
        version: the wheel's version. ``"{BUILD_EMBED_LABEL}"`` is the label Bazel stamps with
            ``--embed_label``; unstamped, the braces stay literal, so the released wheel is built
            through ``<name>.dist``, which names the file after the stamped version.
        tags: tags such as ``manual`` for a target a wildcard build should skip.
    """
    py_wheel(
        name = name,
        abi = "none",
        description_content_type = "text/markdown",
        description_file = "//:README.md",
        distribution = "teachers-teammate",
        entry_points = {
            "console_scripts": [
                "teachers-teammate = teachers_teammate.cli:main",
                "teachers-teammate-gui = teachers_teammate.gui:main_gui",
            ],
        },
        extra_distinfo_files = {
            "//:LICENSE": "LICENSE",
            "//:NOTICE": "NOTICE",
        },
        extra_requires_files = {
            "wheel_requires_llm.txt": "llm",
            "wheel_requires_privacy.txt": "privacy",
        },
        license = "GPL-3.0-or-later",
        platform = "any",
        python_requires = ">=3.14",
        python_tag = "py3",
        requires_file = "wheel_requires.txt",
        summary = "OCR pipeline for handwritten documents",
        tags = tags,
        version = version,
        visibility = ["//visibility:public"],
        deps = [":ocr_pkg"],
    )
