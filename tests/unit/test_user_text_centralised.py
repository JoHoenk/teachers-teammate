"""The ratchet: a sentence a teacher reads is defined in ``teachers_teammate/texts``.

``texts`` is the one home for teacher-facing wording; every other module refers to a sentence
there instead of writing its own.  This test is what keeps that true: it parses the sources with
``ast`` and fails on a literal handed to a sink that shows text, unless the literal is still on
the allowlist.  The allowlist may only shrink — an entry that no longer matches a finding fails
too, so a phase that moves a sentence deletes its entry instead of leaving it behind.

The inventory itself is ``tools/user_text/inventory.py``; run it as a program to write the CSV
the migration works from.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from tools.user_text import inventory
from tools.user_text.allowlist import ALLOWED

#: The package that owns every teacher-facing sentence.
TEXTS = "teachers_teammate/texts"

#: The types only ``texts`` may build: a sentence is defined there, not at the call site.
_OWNED = ("Notice", "Question", "FilePrompt", "Choice")


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _findings() -> list[inventory.Finding]:
    """Return every literal that reaches a teacher in the scanned modules."""
    return inventory.scan(_root())


def _called(node: ast.expr) -> str:
    """Return the last component of the callee of *node*, or an empty string."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def test_no_new_literal_reaches_a_teacher() -> None:
    """
    Given  the GUI and the CLI
    When   every literal handed to a sink that shows text is collected
    Then   each one is already on the allowlist, so no new sentence sits at its call site
    """
    unexpected = [
        finding
        for finding in _findings()
        if finding.text not in ALLOWED.get(finding.path, frozenset())
    ]

    assert not unexpected, (
        "Move these into teachers_teammate/texts and call them from there:\n"
        + "\n".join(f"  {f.path}:{f.line}  {f.sink}  {f.text!r}" for f in unexpected)
    )


def test_the_allowlist_has_no_stale_entries() -> None:
    """
    Given  the allowlist of literals still at their call site
    When   it is compared with what the sources actually contain
    Then   no entry is left over, so a moved sentence deletes its entry
    """
    present = {(finding.path, finding.text) for finding in _findings()}
    stale = [
        (path, text)
        for path, texts in ALLOWED.items()
        for text in texts
        if (path, text) not in present
    ]

    assert not stale, "Delete these allowlist entries; the literals are gone:\n" + "\n".join(
        f"  {path}  {text!r}" for path, text in sorted(stale)
    )


def test_only_texts_builds_a_notice() -> None:
    """
    Given  every module of the app outside ``teachers_teammate/texts``
    When   its calls are searched for the types that carry a sentence
    Then   none of them builds one, so a notice is defined in one place only
    """
    root = _root()
    offenders: list[str] = []
    for path in sorted((root / "teachers_teammate").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(TEXTS):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called(node.func) in _OWNED:
                offenders.append(f"  {relative}:{node.lineno}  {_called(node.func)}(…)")

    assert not offenders, "Build the sentence in teachers_teammate/texts instead:\n" + "\n".join(
        offenders
    )


def test_a_literal_handed_to_a_text_function_is_a_finding() -> None:
    """
    Given  a window that passes a sentence to a function of ``texts``, as an argument or a keyword
    When   the source is scanned
    Then   each sentence is a finding, so the new route is no way around the ratchet
    """
    source = (
        "def f(self, name):\n"
        '    _dialogs.notify(self, dlg.copy_failed("Could not copy"))\n'
        '    _dialogs.notify(self, privacy.what_is_sent_failed(reason=f"No {name}"))\n'
    )

    found = [(f.sink, f.kind) for f in inventory.scan_source(source, path="x.py")]

    assert found == [
        ("dlg.copy_failed", inventory.TEXT_CALL),
        ("privacy.what_is_sent_failed", inventory.TEXT_CALL),
    ]


def test_names_and_values_handed_to_a_text_function_are_not_findings() -> None:
    """
    Given  calls of ``texts`` functions with variables, calls, numbers and an empty start folder
    When   the source is scanned
    Then   nothing is found: only a sentence is a finding
    """
    source = (
        "def f(self, folder):\n"
        "    dlg.no_files_found(str(folder))\n"
        "    dlg.restore_settings(applies_immediately=True)\n"
        '    dlg.select_input_folder("")\n'
        '    other.helper("a sentence")\n'
    )

    assert inventory.scan_source(source, path="x.py") == []
