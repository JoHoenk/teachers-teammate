"""Find the string literals that reach a teacher's eyes.

``teachers_teammate/texts`` is the one home for the sentences a teacher reads.  This
module is the checklist for getting there and the source of the ratchet test's
allowlist: it parses a module with ``ast`` and reports every literal handed to a sink
that shows text — a message box, a widget's label, a menu entry, a CLI line, an
exception message.

Run it as a program to write the CSV::

    bazel run //tools/user_text:inventory -- --out user_text.csv
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Iterator
import csv
from dataclasses import dataclass
from pathlib import Path
import sys

#: The modules whose literals a teacher can read, relative to the repository root.
SCANNED: tuple[str, ...] = ("teachers_teammate/gui", "teachers_teammate/cli.py")

DIALOG = "dialog"
WIDGET = "widget"
CLI = "cli"
ERROR = "error"
TEXT_CALL = "text-call"

#: ``positions`` is the tuple of positional arguments that show text; ``None`` means all of
#: them.  A sink is named by its full dotted path when the last component alone is too
#: common to be sure of it (``information``), and by that last component otherwise.
_QUALIFIED: dict[str, tuple[str, tuple[int, ...] | None]] = {
    "QMessageBox.information": (DIALOG, (1, 2)),
    "QMessageBox.warning": (DIALOG, (1, 2)),
    "QMessageBox.critical": (DIALOG, (1, 2)),
    "QMessageBox.question": (DIALOG, (1, 2)),
    "QMessageBox.about": (DIALOG, (1, 2)),
    "QFileDialog.getExistingDirectory": (DIALOG, (1,)),
    "QFileDialog.getOpenFileName": (DIALOG, (1, 3)),
    "QFileDialog.getOpenFileNames": (DIALOG, (1, 3)),
    "QFileDialog.getSaveFileName": (DIALOG, (1, 3)),
}

_SIMPLE: dict[str, tuple[str, tuple[int, ...] | None]] = {
    # Widget constructors: the first argument is the text the teacher reads.
    "QLabel": (WIDGET, (0,)),
    "QPushButton": (WIDGET, (0,)),
    "QCheckBox": (WIDGET, (0,)),
    "QRadioButton": (WIDGET, (0,)),
    "QGroupBox": (WIDGET, (0,)),
    "QAction": (WIDGET, (0,)),
    "QMenu": (WIDGET, (0,)),
    "QToolButton": (WIDGET, (0,)),
    "QListWidgetItem": (WIDGET, (0,)),
    "QTreeWidgetItem": (WIDGET, (0,)),
    "QTableWidgetItem": (WIDGET, (0,)),
    # Setters and the adders that carry a label.
    "setText": (WIDGET, (0,)),
    "setToolTip": (WIDGET, (0,)),
    "setPlaceholderText": (WIDGET, (0,)),
    "setWindowTitle": (WIDGET, (0,)),
    "setTitle": (WIDGET, (0,)),
    "setInformativeText": (WIDGET, (0,)),
    "addItem": (WIDGET, (0,)),
    "addTab": (WIDGET, (1,)),
    "addMenu": (WIDGET, (0,)),
    "addAction": (WIDGET, (0,)),
    # A CLI line: every positional argument is read.
    "print": (CLI, None),
}

#: Keyword arguments that carry prose, by the call they belong to.
_KEYWORDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "add_argument": (CLI, ("help",)),
    "ArgumentParser": (CLI, ("description", "epilog")),
    "add_parser": (CLI, ("help", "description")),
    "add_mutually_exclusive_group": (CLI, ()),
}

#: The names the GUI gives the modules of ``teachers_teammate/texts``.  A sentence is *defined*
#: there, so a literal handed to one of their functions is a sentence written at the call site
#: after all (``dlg.copy_failed("Could not copy")``).
TEXT_MODULES: frozenset[str] = frozenset({"dlg", "privacy"})

#: Suffixes of an exception class whose message a teacher may read through ``str(exc)``.
_ERROR_SUFFIXES = ("Error", "Exception")


@dataclass(frozen=True, order=True)
class Finding:
    """One literal that reaches a teacher, as the inventory and the ratchet test see it."""

    path: str
    line: int
    sink: str
    kind: str
    text: str


def _literal(node: ast.expr) -> str | None:
    """Return the text of *node* when it is a literal or an f-string, else ``None``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ast.unparse(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None or right is not None:
            return ast.unparse(node)
    return None


def _call_name(func: ast.expr) -> tuple[str, str]:
    """Return ``(dotted, last)`` for the callee of a call."""
    if isinstance(func, ast.Attribute):
        return ast.unparse(func), func.attr
    if isinstance(func, ast.Name):
        return func.id, func.id
    return ast.unparse(func), ""


def _sink(func: ast.expr) -> tuple[str, str, tuple[int, ...] | None] | None:
    """Return ``(name, kind, positions)`` when calling *func* shows text."""
    dotted, last = _call_name(func)
    if dotted in _QUALIFIED:
        kind, positions = _QUALIFIED[dotted]
        return dotted, kind, positions
    if last in _SIMPLE:
        kind, positions = _SIMPLE[last]
        return last, kind, positions
    return None


def _text_module_call(func: ast.expr) -> str | None:
    """Return ``dlg.name`` when *func* is a function of a texts module, else ``None``."""
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in TEXT_MODULES
    ):
        return ast.unparse(func)
    return None


def _shown_arguments(call: ast.Call, positions: tuple[int, ...] | None) -> Iterator[ast.expr]:
    """Yield the arguments of *call* that a teacher reads."""
    if positions is None:
        yield from call.args
        return
    for index in positions:
        if index < len(call.args):
            yield call.args[index]


def _keyword_arguments(call: ast.Call, names: tuple[str, ...]) -> Iterator[ast.expr]:
    """Yield the keyword arguments of *call* named in *names*."""
    for keyword in call.keywords:
        if keyword.arg in names:
            yield keyword.value


def scan_source(source: str, *, path: str) -> list[Finding]:
    """Return every literal in *source* that reaches a teacher, in line order."""
    findings: list[Finding] = []
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, ast.Call):
            sink = _sink(node.func)
            if sink is not None:
                name, kind, positions = sink
                for argument in _shown_arguments(node, positions):
                    text = _literal(argument)
                    if text is not None:
                        findings.append(Finding(path, argument.lineno, name, kind, text))
            text_call = _text_module_call(node.func)
            if text_call is not None:
                for argument in [*node.args, *(k.value for k in node.keywords)]:
                    text = _literal(argument)
                    if text:
                        findings.append(Finding(path, argument.lineno, text_call, TEXT_CALL, text))
            keyword_sink = _KEYWORDS.get(_call_name(node.func)[1])
            if keyword_sink is not None:
                kind, names = keyword_sink
                for argument in _keyword_arguments(node, names):
                    text = _literal(argument)
                    if text is not None:
                        findings.append(
                            Finding(path, argument.lineno, _call_name(node.func)[1], kind, text)
                        )
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            _, last = _call_name(node.exc.func)
            if last.endswith(_ERROR_SUFFIXES) and node.exc.args:
                text = _literal(node.exc.args[0])
                if text is not None:
                    findings.append(Finding(path, node.exc.args[0].lineno, last, ERROR, text))
    return sorted(findings)


def scan_file(path: Path, *, root: Path) -> list[Finding]:
    """Return the findings of one module, with *path* made relative to *root*."""
    relative = path.relative_to(root).as_posix()
    return scan_source(path.read_text(encoding="utf-8"), path=relative)


def scanned_files(root: Path) -> list[Path]:
    """Return every module the inventory reads, in a stable order."""
    files: list[Path] = []
    for entry in SCANNED:
        target = root / entry
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        else:
            files.append(target)
    return files


def scan(root: Path) -> list[Finding]:
    """Return every finding under *root*."""
    findings: list[Finding] = []
    for path in scanned_files(root):
        findings.extend(scan_file(path, root=root))
    return findings


def write_csv(findings: Iterable[Finding], out: Path) -> None:
    """Write *findings* as the migration checklist."""
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["file", "line", "sink", "class", "text"])
        for finding in findings:
            writer.writerow([finding.path, finding.line, finding.sink, finding.kind, finding.text])


def main(argv: list[str] | None = None) -> int:
    """Write the inventory CSV; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="the repository root")
    parser.add_argument("--out", type=Path, default=Path("user_text.csv"), help="the CSV to write")
    arguments = parser.parse_args(argv)
    findings = scan(arguments.root)
    write_csv(findings, arguments.out)
    duplicates = len(findings) - len({(f.path, f.text) for f in findings})
    print(
        f"{len(findings)} literals in {len({f.path for f in findings})} files "
        f"({duplicates} repeats within a file) → {arguments.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
