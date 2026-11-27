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
import re
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
    # The few HTML fragments a widget puts around a label: every argument is a label, except
    # the target of a link.
    "_html.green": (WIDGET, None),
    "_html.red": (WIDGET, None),
    "_html.grey": (WIDGET, None),
    "_html.amber": (WIDGET, None),
    "_html.bold": (WIDGET, None),
    "_html.bold_with_note": (WIDGET, None),
    "_html.heading": (WIDGET, None),
    "_html.headline": (WIDGET, None),
    "_html.error_line": (WIDGET, None),
    "_html.with_path": (WIDGET, None),
    "_html.link": (WIDGET, (1,)),
}

#: A sink named by its last component, but only on a receiver whose dotted name holds the marker:
#: ``status`` and ``emit`` are too common alone.  A run's reporter prints a line, a worker's
#: ``log_line`` signal carries one to the log.
_MARKED: dict[str, tuple[str, tuple[int, ...] | None, tuple[str, ...]]] = {
    "status": (CLI, None, ("reporter",)),
    "warn": (CLI, None, ("reporter",)),
    "emit": (WIDGET, None, ("log_line",)),
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
    "addRow": (WIDGET, (0,)),
    "setSuffix": (WIDGET, (0,)),
    "setApplicationDisplayName": (WIDGET, (0,)),
    "setHorizontalHeaderLabels": (WIDGET, (0,)),
    "setVerticalHeaderLabels": (WIDGET, (0,)),
    "showMessage": (WIDGET, (0, 1)),
    # The main window's own helpers: a step's header and its tooltip, a line in a log pane.
    "step_header": (WIDGET, None),
    "append_text": (WIDGET, (0,)),
    # What the readiness line and the fix buttons show: the sentence is the first argument.
    "Fix": (WIDGET, (0,)),
    "FixAction": (WIDGET, (0,)),
    "Readiness": (WIDGET, (0,)),
    # A CLI line: every positional argument is read.
    "print": (CLI, None),
}

#: Keyword arguments that carry prose, by the call they belong to.
_KEYWORDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "add_argument": (CLI, ("help",)),
    "ArgumentParser": (CLI, ("description", "epilog")),
    "add_parser": (CLI, ("help", "description")),
    "add_mutually_exclusive_group": (CLI, ()),
    "FixAction": (WIDGET, ("tooltip",)),
}

#: Keyword arguments that carry prose wherever they appear: a result a teacher reads, the reason
#: a step refused, the line a run prints.  Below the adapters a field like this holds data, so a
#: literal in it is a sentence written at the call site.
_FIELD_KEYWORDS: tuple[str, ...] = ("message", "notice", "reason")

#: Suffixes of a module constant that holds a sentence a teacher reads: the one a message says
#: (``BATTERY_WARNING``) and the one a screen shows next to what it belongs to
#: (``GENERAL_TITLE``, ``START_TOOLTIP``).
_CONSTANT_SUFFIXES: tuple[str, ...] = (
    "_MESSAGE",
    "_NOTICE",
    "_WARNING",
    "_TITLE",
    "_TITLES",
    "_LABEL",
    "_TIP",
    "_TOOLTIP",
    "_HINT",
    "_NOTE",
    "_HEADING",
    "_INTRO",
    "_PLACEHOLDER",
    "_LABELS",
    "_LEGEND",
)

#: The suffixes that name a word or two on a widget: ``MODEL_LABEL = "Model:"`` is a sentence's
#: worth of wording even without a space.
_SHORT_SUFFIXES: tuple[str, ...] = (
    "_LABEL",
    "_LABELS",
    "_TITLE",
    "_TIP",
    "_TOOLTIP",
    "_HEADING",
    "_PLACEHOLDER",
    "_LEGEND",
)

#: A colour is layout, not wording.
_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}")

#: Module constants whose name says a teacher reads them, but whose value no teacher reads: a
#: hint written for the LLM, and a download size that is a number.
NON_TEXT_CONSTANTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("teachers_teammate/domain/correction_prompts.py", "NOTES_HEADING"),
        ("teachers_teammate/domain/local_models.py", "PAGE_MODELS_SIZE_LABEL"),
        ("teachers_teammate/domain/local_models.py", "READER_SIZE_LABEL"),
        ("teachers_teammate/domain/review_prompts.py", "_NOTATION_HINT"),
    }
)

#: Where a field holds a note for the debug log, never for a teacher: the reason of a step of the
#: automatic image preparation.
_DEBUG_ONLY_FIELDS: tuple[str, ...] = ("teachers_teammate/infrastructure/preprocessing/",)

#: Where a table of labels follows the language of the *document* a run writes, not the language of
#: the window: the Word output's headings.
_DOCUMENT_TABLES: tuple[str, ...] = (
    "teachers_teammate/infrastructure/docx_builder.py",
    "teachers_teammate/infrastructure/docx_comments.py",
)

#: Where a raised exception states an invariant of the code, never something a teacher reads.
_INVARIANT_ERRORS: tuple[str, ...] = ("teachers_teammate/domain/",)

#: Messages raised outside ``domain`` that state an invariant too: a shape a model answered, a
#: request our own code built without its schema, a structure Windows handed back.  Listed one by
#: one, because the modules of ``infrastructure`` raise sentences for teachers as well.
INVARIANT_MESSAGES: frozenset[tuple[str, str]] = frozenset(
    {
        ("teachers_teammate/infrastructure/gpu_detector.py", "GlobalMemoryStatusEx failed"),
        (
            "teachers_teammate/infrastructure/preprocessing/page_models.py",
            "f'the orientation model answered {scores.size} classes'",
        ),
        (
            "teachers_teammate/infrastructure/preprocessing/page_models.py",
            "f'the flatten model answered the shape {out.shape}'",
        ),
        (
            "teachers_teammate/infrastructure/proofreading/corrector.py",
            "this port sends no request",
        ),
        (
            "teachers_teammate/infrastructure/proofreading/exchanges/base.py",
            "f'unknown exchange {kind!r} (known: {known})'",
        ),
        (
            "teachers_teammate/infrastructure/proofreading/port.py",
            "a structured request needs a schema",
        ),
        (
            "teachers_teammate/gui/_preprocess_preview.py",
            "a preview that did not fail has both pages and its steps",
        ),
        ("teachers_teammate/gui/_qt.py", "f'Qt returned no {what}'"),
        (
            "teachers_teammate/gui/setup/_assistant.py",
            "f'{page_id} is not a {kind.__name__}'",
        ),
        (
            "teachers_teammate/infrastructure/addon_manager.py",
            "the pip process opened no output stream",
        ),
        (
            "teachers_teammate/infrastructure/proofreading/schemas.py",
            "Category names other categories than CHANGE_CATEGORIES does",
        ),
    }
)

#: What a module imports from ``teachers_teammate/texts``: the names it gave the texts modules
#: (``from ..texts import dialogs as dlg``) and the names it took out of them
#: (``from ..texts.run import image_unreadable``).  A sentence is *defined* there, so a literal
#: handed to one of them is a sentence written at the call site after all
#: (``dlg.copy_failed("Could not copy")``).


@dataclass(frozen=True)
class TextImports:
    """The ``texts`` names one module has in scope."""

    modules: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()


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
    """Return the text of *node* when it is a literal or an f-string, else ``None``.

    An f-string whose literal parts hold no letter says nothing either: ``f"{a} {b}"`` glues
    two names together, it does not word a sentence.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        literal = "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        return ast.unparse(node) if any(character.isalpha() for character in literal) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        parts = (_literal(node.left), _literal(node.right))
        if any(part is not None and any(c.isalpha() for c in part) for part in parts):
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
    if last in _MARKED:
        kind, positions, markers = _MARKED[last]
        if any(marker in dotted for marker in markers):
            return dotted, kind, positions
    return None


def _text_imports(tree: ast.Module) -> TextImports:
    """Return the names *tree* takes from ``texts``, by the module they were imported from."""
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        parts = node.module.split(".")
        if "texts" not in parts:
            continue
        taken = {alias.asname or alias.name for alias in node.names}
        # ``from ..texts import dialogs`` names a module, ``from ..texts.run import x`` a thing in one
        (modules if parts[-1] == "texts" else names).update(taken)
    return TextImports(frozenset(modules), frozenset(names))


def _text_module_call(func: ast.expr, imports: TextImports) -> str | None:
    """Return the dotted name when *func* is a function of a texts module, else ``None``."""
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in imports.modules
    ):
        return ast.unparse(func)
    if isinstance(func, ast.Name) and func.id in imports.names:
        return func.id
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
        arguments: Iterable[ast.expr] = call.args
    else:
        arguments = (call.args[index] for index in positions if index < len(call.args))
    for argument in arguments:
        # A list of labels (``setHorizontalHeaderLabels``) shows each of them.
        if isinstance(argument, (ast.List, ast.Tuple)):
            yield from argument.elts
        else:
            yield argument


def _keyword_arguments(call: ast.Call, names: tuple[str, ...]) -> Iterator[ast.expr]:
    """Yield the keyword arguments of *call* named in *names*."""
    for keyword in call.keywords:
        if keyword.arg in names:
            yield keyword.value


def _finding(path: str, node: ast.expr, sink: str, kind: str) -> Finding | None:
    """Return the finding for *node* when it holds a sentence, else ``None``.

    An empty literal says nothing: ``QLabel("")`` clears a label rather than wording one.
    """
    text = _literal(node)
    return None if not text else Finding(path, node.lineno, sink, kind, text)


def _text_call_findings(call: ast.Call, path: str, name: str) -> list[Finding]:
    """Return the sentences *call* hands to a function of ``texts``.

    A word with no space (a model's name, a package, an id) is data the function words into a
    sentence, not a sentence.
    """
    found = [_finding(path, argument, name, TEXT_CALL) for argument in call.args]
    found += [_finding(path, keyword.value, name, TEXT_CALL) for keyword in call.keywords]
    return [finding for finding in found if finding is not None and " " in finding.text]


def _call_findings(call: ast.Call, path: str, imports: TextImports) -> list[Finding]:
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
    text_call = _text_module_call(call.func, imports)
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


def _constant_values(value: ast.expr) -> Iterator[ast.expr]:
    """Yield the literals a constant holds: itself, or the items of a list, tuple or dict."""
    if isinstance(value, ast.Dict):
        for item in value.values:
            yield from _constant_values(item)
    elif isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        for item in value.elts:
            yield from _constant_values(item)
    else:
        yield value


def _constant_findings(node: ast.Assign | ast.AnnAssign, path: str) -> list[Finding]:
    """Return the sentences a module constant holds when its name says a teacher reads them."""
    name = _constant_target(node)
    value = node.value
    if name is None or value is None:
        return []
    short = name.endswith(_SHORT_SUFFIXES)
    findings = []
    for item in _constant_values(value):
        finding = _finding(path, item, name, CONSTANT)
        if finding is None or _COLOUR.fullmatch(finding.text):
            continue
        # a colour or an id named ``*_WARNING`` is no sentence; a label may be a single word
        if " " in finding.text or short:
            findings.append(finding)
    return findings


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
    imports = _text_imports(tree)
    findings: list[Finding] = []
    for node in tree.body:  # a constant is a module's own: a local one is not a sink
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            findings.extend(_constant_findings(node, path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            findings.extend(_call_findings(node, path, imports))
        elif isinstance(node, ast.Raise):
            findings.extend(_raise_findings(node, path))
    return sorted(f for f in findings if not _is_exempt(f))


def _is_exempt(finding: Finding) -> bool:
    """Return whether *finding* sits where its kind is no teacher's text (see the tuples above)."""
    if finding.kind == FIELD:
        return finding.path.startswith(_DEBUG_ONLY_FIELDS) and finding.sink == "StepResult"
    if finding.kind == ERROR:
        return (
            finding.path.startswith(_INVARIANT_ERRORS)
            or (finding.path, finding.text) in INVARIANT_MESSAGES
        )
    if finding.kind == CONSTANT:
        return (
            finding.path,
            finding.sink,
        ) in NON_TEXT_CONSTANTS or finding.path in _DOCUMENT_TABLES
    return False


def _template_calls(tree: ast.Module, path: str) -> list[tuple[str, int, str]]:
    """Return ``(path, line, name)`` of each ``.format`` call on a name taken from ``texts``."""
    imports = _text_imports(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "format":
            continue
        owner = node.func.value
        root = owner
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and (root.id in imports.modules or root.id in imports.names):
            found.append((path, node.lineno, ast.unparse(owner)))
    return sorted(found)


def template_calls(root: Path) -> list[tuple[str, int, str]]:
    """Return every template of ``texts`` that a scanned module fills at its own call site.

    A dynamic part is a function of ``texts``, so renaming a placeholder cannot break a caller.
    """
    found: list[tuple[str, int, str]] = []
    for path in scanned_files(root):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        found.extend(_template_calls(tree, relative))
    return found


def template_calls_in(source: str, *, path: str) -> list[tuple[str, int, str]]:
    """Return the template calls of one *source*, as :func:`template_calls` does for a tree."""
    return _template_calls(ast.parse(source, filename=path), path)


def raised_messages(root: Path) -> set[tuple[str, str]]:
    """Return ``(path, text)`` of every sentence the sources raise, the exempt ones included.

    The stale check of :data:`INVARIANT_MESSAGES` needs the raises that ``scan`` drops.
    """
    found: set[tuple[str, str]] = set()
    for path in scanned_files(root):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise):
                found.update(
                    (finding.path, finding.text) for finding in _raise_findings(node, relative)
                )
    return found


def module_constants(root: Path) -> set[tuple[str, str]]:
    """Return ``(path, name)`` of every module constant the sources define, the exempt ones too.

    The stale check of :data:`NON_TEXT_CONSTANTS` needs the constants that ``scan`` drops.
    """
    found: set[tuple[str, str]] = set()
    for path in scanned_files(root):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found.update(
                (relative, target.id) for target in targets if isinstance(target, ast.Name)
            )
    return found


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
