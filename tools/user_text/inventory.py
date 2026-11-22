"""Find the string literals that reach a teacher's eyes.

``teachers_teammate/texts`` is the one home for the sentences a teacher reads.  This
module is the checklist for getting there and the source of the ratchet test's
allowlist: it parses a module with ``ast`` and reports every literal handed to a sink
that shows text — a message box, a widget's label, a menu entry, a CLI line, an
exception message, a field of a result or a request, a module constant a teacher is
shown.

Run it as a program to write the CSV::

    bazel run //tools/user_text:inventory -- --out user_text.csv
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Iterable, Iterator
import csv
from dataclasses import dataclass
import os
from pathlib import Path
import sys

#: The modules whose literals a teacher can read, relative to the repository root.
SCANNED: tuple[str, ...] = (
    "teachers_teammate/gui",
    "teachers_teammate/cli.py",
    "teachers_teammate/application",
    "teachers_teammate/infrastructure",
    "teachers_teammate/domain",
)

DIALOG = "dialog"
WIDGET = "widget"
CLI = "cli"
ERROR = "error"
TEXT_CALL = "text-call"
FIELD = "field"
CONSTANT = "constant"

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

#: Keyword arguments that carry prose wherever they appear: a result a teacher reads, the reason
#: a step refused, the line a run prints.  Below the adapters a field like this holds data, so a
#: literal in it is a sentence written at the call site.
_FIELD_KEYWORDS: tuple[str, ...] = ("message", "notice", "reason")

#: Suffixes of a module constant that holds a sentence a teacher reads (``BATTERY_WARNING``).
_CONSTANT_SUFFIXES: tuple[str, ...] = ("_MESSAGE", "_NOTICE", "_WARNING")

#: Where a field holds a note for the debug log, never for a teacher: the reason of a step of the
#: automatic image preparation.
_DEBUG_ONLY_FIELDS: tuple[str, ...] = ("teachers_teammate/infrastructure/preprocessing/",)

#: Where a raised exception states an invariant of the code, never something a teacher reads.
_INVARIANT_ERRORS: tuple[str, ...] = ("teachers_teammate/domain/",)

#: The names the adapters give the modules of ``teachers_teammate/texts``.  A sentence is
#: *defined* there, so a literal handed to one of their functions is a sentence written at the
#: call site after all (``dlg.copy_failed("Could not copy")``).
TEXT_MODULES: frozenset[str] = frozenset({"addon_text", "check_text", "dlg", "privacy", "txt"})

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


def _constant_target(node: ast.Assign | ast.AnnAssign) -> str | None:
    """Return the name of a constant whose name says a teacher reads its sentence."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name) and target.id.endswith(_CONSTANT_SUFFIXES):
            return target.id
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


def _finding(path: str, node: ast.expr, sink: str, kind: str) -> Finding | None:
    """Return the finding for *node* when it holds a literal, else ``None``."""
    text = _literal(node)
    return None if text is None else Finding(path, node.lineno, sink, kind, text)


def _text_call_findings(call: ast.Call, path: str, name: str) -> list[Finding]:
    """Return the sentences *call* hands to a function of ``texts``; an empty one says nothing."""
    found = [_finding(path, argument, name, TEXT_CALL) for argument in call.args]
    found += [_finding(path, keyword.value, name, TEXT_CALL) for keyword in call.keywords]
    return [finding for finding in found if finding is not None and finding.text]


def _call_findings(call: ast.Call, path: str) -> list[Finding]:
    """Return the sentences *call* shows, by its sink arguments or by a prose keyword."""
    findings: list[Finding] = []
    last = _call_name(call.func)[1]
    sink = _sink(call.func)
    if sink is not None:
        name, kind, positions = sink
        findings.extend(
            finding
            for argument in _shown_arguments(call, positions)
            if (finding := _finding(path, argument, name, kind)) is not None
        )
    text_call = _text_module_call(call.func)
    if text_call is not None:
        findings.extend(_text_call_findings(call, path, text_call))
    # A function of ``texts`` already reported its keywords above: once is enough.
    fields = () if text_call is not None else ((FIELD, _FIELD_KEYWORDS),)
    for kind, names in (*fields, _KEYWORDS.get(last, ("", ()))):
        findings.extend(
            finding
            for argument in _keyword_arguments(call, names)
            if (finding := _finding(path, argument, last, kind)) is not None
        )
    return findings


def _constant_findings(node: ast.Assign | ast.AnnAssign, path: str) -> list[Finding]:
    """Return the sentence a module constant holds when its name says a teacher reads it."""
    name = _constant_target(node)
    value = node.value
    if name is None or value is None:
        return []
    finding = _finding(path, value, name, CONSTANT)
    if finding is None or " " not in finding.text:
        return []  # a colour or an id named ``*_WARNING`` is no sentence
    return [finding]


def _raise_findings(node: ast.Raise, path: str) -> list[Finding]:
    """Return the message of a raised exception a teacher may read through ``str(exc)``."""
    if not isinstance(node.exc, ast.Call) or not node.exc.args:
        return []
    last = _call_name(node.exc.func)[1]
    if not last.endswith(_ERROR_SUFFIXES):
        return []
    finding = _finding(path, node.exc.args[0], last, ERROR)
    return [] if finding is None else [finding]


def scan_source(source: str, *, path: str) -> list[Finding]:
    """Return every literal in *source* that reaches a teacher, in line order."""
    tree = ast.parse(source, filename=path)
    findings: list[Finding] = []
    for node in tree.body:  # a constant is a module's own: a local one is not a sink
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            findings.extend(_constant_findings(node, path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            findings.extend(_call_findings(node, path))
        elif isinstance(node, ast.Raise):
            findings.extend(_raise_findings(node, path))
    return sorted(f for f in findings if not _is_exempt(f))


def _is_exempt(finding: Finding) -> bool:
    """Return whether *finding* sits where its kind is no teacher's text (see the tuples above)."""
    if finding.kind == FIELD:
        return finding.path.startswith(_DEBUG_ONLY_FIELDS) and finding.sink == "StepResult"
    if finding.kind == ERROR:
        return finding.path.startswith(_INVARIANT_ERRORS)
    return False


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


def output_path(out: Path) -> Path:
    """Return where to write *out*: a relative path is the folder ``bazel run`` was started in.

    ``bazel run`` changes into the runfiles, which are no place for a file anyone looks for.
    """
    if out.is_absolute():
        return out
    return Path(os.environ.get("BUILD_WORKING_DIRECTORY", Path.cwd())) / out


def main(argv: list[str] | None = None) -> int:
    """Write the inventory CSV; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="the repository root")
    parser.add_argument("--out", type=Path, default=Path("user_text.csv"), help="the CSV to write")
    arguments = parser.parse_args(argv)
    findings = scan(arguments.root)
    out = output_path(arguments.out)
    write_csv(findings, out)
    duplicates = len(findings) - len({(f.path, f.text) for f in findings})
    print(
        f"{len(findings)} literals in {len({f.path for f in findings})} files "
        f"({duplicates} repeats within a file) → {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
