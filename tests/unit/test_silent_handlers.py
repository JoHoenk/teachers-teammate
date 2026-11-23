"""No handler swallows a failure without a trace.

An ``except`` that only passes, continues or returns a constant says one of two things — "this
cannot happen here" or "this does not matter" — and leaves the reader guessing which.  Each one
either writes a line (``teachers_teammate/infrastructure/logging_setup.py`` says where it lands)
or carries a comment that says why nothing happens.  A comment above the handler counts too.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: The package whose handlers are checked.
SCANNED = "teachers_teammate"

#: The calls that write a line into the log, as the level names of the logging module.
LEVELS = frozenset({"debug", "info", "warning", "error", "exception", "critical", "log"})


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _is_value(node: ast.expr | None) -> bool:
    """Return whether *node* is a value written down, not a computation."""
    return isinstance(node, (ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict))


def _says_nothing(handler: ast.ExceptHandler) -> bool:
    """Return whether every statement of *handler* is a pass, a continue or a constant return."""
    for statement in handler.body:
        if isinstance(statement, (ast.Pass, ast.Continue)):
            continue
        if isinstance(statement, ast.Return) and (
            statement.value is None or _is_value(statement.value)
        ):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # a bare string is a note, not a line of code
        return False
    return bool(handler.body)


def _writes_a_line(node: ast.AST) -> bool:
    """Return whether *node* holds a logging call."""
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in LEVELS:
                return True
    return False


def _explains(lines: list[str], handler: ast.ExceptHandler) -> bool:
    """Return whether the handler's own lines, or the line above it, carry a comment."""
    start = max(handler.lineno - 1, 1)
    end = handler.end_lineno or handler.lineno
    return any("#" in line for line in lines[start - 1 : end])


def findings(source: str, path: str) -> list[str]:
    """Return ``path:line`` for every handler of *source* that neither logs nor explains."""
    lines = source.splitlines()
    found = [
        handler.lineno
        for handler in ast.walk(ast.parse(source))
        if isinstance(handler, ast.ExceptHandler)
        and _says_nothing(handler)
        and not _writes_a_line(handler)
        and not _explains(lines, handler)
    ]
    return [f"{path}:{line}" for line in sorted(found)]


def test_a_handler_that_says_nothing_is_a_finding() -> None:
    """
    Given  handlers that pass, continue, return a constant, and one with a note
    When   the source is scanned
    Then   the first three are findings and the note is not
    """
    source = (
        "def f(rows, x):\n"
        "    for row in rows:\n"
        "        try:\n"
        "            read(row)\n"
        "        except OSError:\n"
        "            pass\n"
        "    try:\n"
        "        read(x)\n"
        "    except ValueError:\n"
        "        continue\n"
        "    try:\n"
        "        read(x)\n"
        "    except LookupError:\n"
        "        return False\n"
        "    try:\n"
        "        read(x)\n"
        "    except KeyError:\n"
        "        return\n"
        "    try:\n"
        "        read(x)\n"
        "    except TypeError:\n"
        "        pass  # no key was given, so there is nothing to look up\n"
    )

    assert findings(source, "x.py") == ["x.py:5", "x.py:9", "x.py:13", "x.py:17"]


def test_a_handler_that_logs_does_its_job() -> None:
    """
    Given  handlers that write a line and that do real work
    When   the source is scanned
    Then   none of them is a finding
    """
    source = (
        "def f(x):\n"
        "    try:\n"
        "        read(x)\n"
        "    except OSError:\n"
        "        _logger.warning('cannot read %s', x)\n"
        "    try:\n"
        "        read(x)\n"
        "    except ValueError:\n"
        "        # The caller's problem, not ours.\n"
        "        pass\n"
        "    try:\n"
        "        read(x)\n"
        "    except LookupError:\n"
        "        return read_fallback(x)\n"
    )

    assert findings(source, "x.py") == []


def test_no_handler_in_the_app_says_nothing() -> None:
    """
    Given  every module of the app
    When   its handlers are scanned
    Then   each writes a line or says in a comment why nothing happens
    """
    root = _root()
    found = [
        finding
        for path in sorted((root / SCANNED).rglob("*.py"))
        for finding in findings(path.read_text(encoding="utf-8"), str(path.relative_to(root)))
    ]

    assert not found, (
        "A handler that says nothing either writes the line the log needs or explains itself:\n"
        + "\n".join(f"  {finding}" for finding in found)
    )
