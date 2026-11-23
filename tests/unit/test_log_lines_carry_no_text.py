"""A log line holds where and what went wrong, never what a teacher's document says.

The log file leaves the app — the teacher sends it to whoever helps.  A line may name a file, a
stage and the kind of an exception; the text of a document, a prompt, a model's answer or an
access key must never reach one.  This test parses the sources and fails on a logging call that
passes such a value by name, in any argument or inside a format string, and on ``.exception``,
which writes the exception's message (it can quote the text it choked on): log the kind and the
frames instead.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: The package whose log lines are checked.
SCANNED = "teachers_teammate"

#: The calls that write a line into the log, as the level names of the logging module.
LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})

#: The names a log line never passes: a document's text, a prompt, a model's answer, a key.
FORBIDDEN = frozenset(
    {
        "text",
        "raw_text",
        "correction_text",
        "prompt",
        "answer",
        "response",
        "content",
        "body",
        "api_key",
    }
)


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _is_logger_call(node: ast.Call) -> bool:
    """Return whether *node* writes a line, for example ``_logger.warning(…)``."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in LEVELS:
        return False
    owner = func.value
    name = owner.attr if isinstance(owner, ast.Attribute) else getattr(owner, "id", "")
    return "log" in name.lower()


def _names_in(node: ast.AST) -> set[str]:
    """Return the variable and attribute names *node* reads, however deep inside it."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _findings(source: str) -> list[str]:
    """Return ``what@line`` for every forbidden value or ``.exception`` of a log line in *source*."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_logger_call(node):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "exception":
            found.append(f"exception@{node.lineno}")
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        for name in sorted(FORBIDDEN & set().union(*map(_names_in, arguments))):
            found.append(f"{name}@{node.lineno}")
    return found


def test_a_log_line_that_passes_the_text_of_a_document_is_a_finding() -> None:
    """
    Given  log lines that pass a document's text, a prompt, an answer and an access key, by
           keyword, by position, inside an f-string and in a format call, and one ``.exception``
    When   the source is scanned
    Then   each of them is a finding
    """
    source = (
        "def f(self, text, prompt, api_key, answer):\n"
        '    _logger.warning("%s", text=text)\n'
        '    _logger.debug("prompt", prompt=prompt)\n'
        '    LOG.error("key", api_key=api_key)\n'
        '    _logger.debug("rejected %s", answer)\n'
        '    _logger.info(f"read {self.raw_text}")\n'
        '    _logger.info("{}".format(text))\n'
        '    _logger.exception("failed")\n'
    )

    assert sorted(_findings(source)) == [
        "answer@5",
        "api_key@4",
        "exception@8",
        "prompt@3",
        "raw_text@6",
        "text@2",
        "text@7",
    ]


def test_other_arguments_a_log_line_takes_are_no_finding() -> None:
    """
    Given  log lines that pass a file name, a stage name and the kind of an exception
    When   the source is scanned
    Then   nothing is found
    """
    source = (
        "def f(self, name, stage, exc):\n"
        '    _logger.warning("%s failed in %s", name, stage)\n'
        '    _logger.error("%s: %s", name, type(exc).__name__)\n'
        "    logging.basicConfig(level=logging.WARNING)\n"
        "    other.status(name)\n"
    )

    assert _findings(source) == []


def test_no_log_line_in_the_app_passes_text_a_prompt_or_a_key() -> None:
    """
    Given  every module of the app
    When   its log lines are scanned for the forbidden arguments
    Then   there are none
    """
    found = [
        f"{path.relative_to(_root())}:{line}"
        for path in sorted((_root() / SCANNED).rglob("*.py"))
        for line in _findings(path.read_text(encoding="utf-8"))
    ]

    assert not found, "A log line leaves the app; keep the text out of it:\n" + "\n".join(found)
