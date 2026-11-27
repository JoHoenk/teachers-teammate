"""A failure at the very start of an entry point is worded for a teacher, not printed as a key.

``str(message)`` gives the sentence once the wording is installed, and ``"key: args"`` until
then.  Each entry point installs it before its first statement that can fail, so a start-up
failure (an add-on folder that cannot be repaired, a settings file that cannot be read) reaches
the teacher as a sentence.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

from teachers_teammate.texts import addons as addon_text

_PACKAGE = "teachers_teammate"

#: The sentence a rendered message gives, and its unwritten form, for one key.
_SENTENCE = addon_text.download_damaged("reader.onnx")
_UNWORDED = "download_damaged: name=reader.onnx"

#: The modules the app is started from.
ENTRY_POINTS = (
    "__main__.py",
    "gui/__main__.py",
)


def _package_dir() -> Path:
    """Return the package folder, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / _PACKAGE
    return Path(__file__).resolve().parents[2] / _PACKAGE


def _render_in_a_fresh_interpreter(imports: str) -> str:
    """Return what a message says in a new interpreter that runs *imports* first."""
    code = (
        f"{imports}\n"
        "from teachers_teammate.messages import Message, MessageKey\n"
        'print(str(Message(MessageKey.DOWNLOAD_DAMAGED, {"name": "reader.onnx"})))\n'
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_a_message_without_the_wording_module_prints_its_key_and_data() -> None:
    """
    Given  a new interpreter that imports the message keys but no wording
    When   a message is shown
    Then   it gives its key and its data, so a traceback or a log line stays readable
    """
    assert _render_in_a_fresh_interpreter("import teachers_teammate.messages") == _UNWORDED


def test_the_command_line_entry_point_words_a_message_by_itself() -> None:
    """
    Given  a new interpreter that imports the message keys and the command line entry point
    When   a message is shown
    Then   it gives the sentence, so nothing has to import the wording separately
    """
    shown = _render_in_a_fresh_interpreter(
        "import teachers_teammate.messages\nimport teachers_teammate.cli"
    )

    assert shown == _SENTENCE


def test_every_entry_point_installs_the_wording_first() -> None:
    """
    Given  the modules the app is started from
    When   the first statements of each are read
    Then   the wording is imported and installed before anything that can fail
    """
    package = _package_dir()
    for module in ENTRY_POINTS:
        body = ast.parse((package / module).read_text(encoding="utf-8")).body
        statements = [
            node
            for node in body
            if not (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
        ]
        first, second = statements[0], statements[1]
        assert isinstance(first, ast.ImportFrom), module
        assert first.names[0].name == "install", module
        assert isinstance(second, ast.Expr) and isinstance(second.value, ast.Call), module
        called = second.value.func
        assert isinstance(called, ast.Name) and called.id == "install", module
