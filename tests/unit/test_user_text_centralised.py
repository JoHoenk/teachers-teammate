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
    Given  every scanned module: the GUI, the CLI, and the layers below them
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


def test_the_invariant_messages_are_all_still_raised() -> None:
    """
    Given  the messages listed as stating an invariant of the code
    When   they are compared with what the sources raise
    Then   no entry is left over, so a reworded raise deletes its entry
    """
    raised = inventory.raised_messages(_root())
    stale = sorted(entry for entry in inventory.INVARIANT_MESSAGES if entry not in raised)

    assert not stale, "Delete these entries; the raise is gone or reworded:\n" + "\n".join(
        f"  {path}  {text!r}" for path, text in stale
    )


def test_an_invariant_raise_is_exempt() -> None:
    """
    Given  a module outside ``domain`` that raises one of the listed invariant messages
    When   the source is scanned under its own path
    Then   it is no finding, because no teacher reads it
    """
    path = "teachers_teammate/infrastructure/gpu_detector.py"
    source = 'raise RuntimeError("GlobalMemoryStatusEx failed")\n'

    assert inventory.scan_source(source, path=path) == []


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
        "from ..texts import dialogs as dlg\n"
        "from ..texts import privacy\n"
        "def f(self, name):\n"
        '    _dialogs.notify(self, dlg.copy_failed("Could not copy"))\n'
        '    _dialogs.notify(self, privacy.what_is_sent_failed(reason=f"No {name}"))\n'
    )

    found = [(f.sink, f.kind) for f in inventory.scan_source(source, path="x.py")]

    assert found == [
        ("dlg.copy_failed", inventory.TEXT_CALL),
        ("privacy.what_is_sent_failed", inventory.TEXT_CALL),
    ]


def test_a_sentence_handed_to_a_function_imported_by_name_is_a_finding() -> None:
    """
    Given  a module that imports a function from a ``texts`` module by name and calls it with a
           sentence, an id and a model's name
    When   the source is scanned
    Then   only the sentence is a finding, whatever alias the module gave the ``texts`` module
    """
    source = (
        "from ..texts.run import image_unreadable\n"
        "from ..texts import models as m\n"
        "def f():\n"
        '    image_unreadable("Cannot read this page")\n'
        '    m.asking_for_models_text("Ollama")\n'
        '    m.preview_title("a sentence")\n'
    )

    found = [(f.sink, f.text) for f in inventory.scan_source(source, path="x.py")]

    assert found == [
        ("image_unreadable", "Cannot read this page"),
        ("m.preview_title", "a sentence"),
    ]


def test_a_form_label_a_header_and_an_html_fragment_are_findings() -> None:
    """
    Given  a form row, a list of header labels, a suffix and the fragments of ``gui/_html``
    When   the source is scanned
    Then   each label is a finding, but not the target of a link or a name put into a fragment
    """
    source = (
        "def f(self, name):\n"
        '    form.addRow("PDF resolution:", self._dpi)\n'
        '    form.addRow("", self._hint)\n'
        '    table.setHorizontalHeaderLabels(["Tag", other])\n'
        '    spin.setSuffix(" DPI")\n'
        '    label.setText(_html.green("Done"))\n'
        '    label.setText(_html.link("retry", "Try again"))\n'
        "    label.setText(_html.bold(name))\n"
    )

    found = [f.text for f in inventory.scan_source(source, path="x.py")]

    assert found == ["PDF resolution:", "Tag", " DPI", "Done", "Try again"]


def test_a_sentence_in_a_field_or_a_constant_is_a_finding() -> None:
    """
    Given  a result built with a sentence in ``reason=`` and a ``*_WARNING`` module constant
    When   the source is scanned
    Then   both are findings, so the layers below the adapters are held to the same rule
    """
    source = (
        'BATTERY_WARNING = "Running on battery."\n'
        'NOT_A_SINK = "a developer log line"\n'
        'result = StepResult(False, reason=f"step {n} failed")\n'
        'raise StageError(message="Could not read")\n'
    )

    found = [(f.sink, f.kind) for f in inventory.scan_source(source, path="x.py")]

    assert found == [
        ("BATTERY_WARNING", inventory.CONSTANT),
        ("StepResult", inventory.FIELD),
        ("StageError", inventory.FIELD),
    ]


def test_a_title_a_label_and_a_tooltip_are_findings() -> None:
    """
    Given  module constants named after what a screen shows: a title, a label, a tooltip, a tip,
         a hint, a note, a heading, an intro and a placeholder
    When   the source is scanned
    Then   each is a finding, so a screen's own words live in ``texts`` like a message does
    """
    source = (
        'GENERAL_TITLE = "General settings"\n'
        'LANGUAGE_LABEL = "Language of your documents"\n'
        'START_TOOLTIP = "Starts the run."\n'
        'KEY_TIP = "Where the key is found."\n'
        'SERVER_HINT = "Ollama runs on this computer."\n'
        'ADDONS_CANCEL_NOTE = "The download stops."\n'
        'NOTES_HEADING = "Notes for the review"\n'
        'PAGE_INTRO = "What this page asks."\n'
        'FOLDER_PLACEHOLDER = "Choose a folder"\n'
    )

    found = [f.sink for f in inventory.scan_source(source, path="x.py")]

    assert found == [
        "GENERAL_TITLE",
        "LANGUAGE_LABEL",
        "START_TOOLTIP",
        "KEY_TIP",
        "SERVER_HINT",
        "ADDONS_CANCEL_NOTE",
        "NOTES_HEADING",
        "PAGE_INTRO",
        "FOLDER_PLACEHOLDER",
    ]


def test_a_constant_listed_as_no_text_is_no_finding() -> None:
    """
    Given  a heading written for the LLM and a download size written as teachers read it
    When   each source is scanned under the path the list names
    Then   neither is a finding, while the same literal elsewhere is one: the entry is by place
    """
    heading = 'NOTES_HEADING = "Notes from the teacher (context for the correction):"\n'
    size = 'READER_SIZE_LABEL = "31 MB"\n'

    assert (
        inventory.scan_source(heading, path="teachers_teammate/domain/correction_prompts.py") == []
    )
    assert inventory.scan_source(size, path="teachers_teammate/domain/local_models.py") == []
    assert [f.sink for f in inventory.scan_source(heading, path="x.py")] == ["NOTES_HEADING"]
    assert [f.sink for f in inventory.scan_source(size, path="x.py")] == ["READER_SIZE_LABEL"]


def test_the_non_text_constants_are_all_still_there() -> None:
    """
    Given  the constants listed as holding no sentence a teacher reads
    When   they are compared with the constants the sources define
    Then   no entry is left over, so a renamed constant deletes its entry
    """
    defined = inventory.module_constants(_root())
    stale = sorted(entry for entry in inventory.NON_TEXT_CONSTANTS if entry not in defined)

    assert not stale, "Delete these entries; the constant is gone or renamed:\n" + "\n".join(
        f"  {path}  {name}" for path, name in stale
    )


def test_an_empty_literal_is_not_a_finding() -> None:
    """
    Given  a label, a tooltip and a table cell cleared with an empty string
    When   the source is scanned
    Then   nothing is found: an empty literal clears a text instead of wording one
    """
    source = (
        "def f(self):\n"
        '    self._error.setText("")\n'
        '    self._table.setToolTip("")\n'
        '    self._table.setItem(0, 1, QTableWidgetItem(""))\n'
    )

    assert inventory.scan_source(source, path="x.py") == []


def test_an_fstring_that_glues_names_together_is_not_a_finding() -> None:
    """
    Given  a status line and a marked label built by putting two names together with a space
    When   the source is scanned
    Then   neither is a finding, so a separator alone is no sentence at the call site
    """
    source = (
        "def f(self, head, note, mark):\n"
        '    self._status.setText(f"{head} {note}")\n'
        '    box.setText(f"{note}  {mark}")\n'
    )

    assert inventory.scan_source(source, path="x.py") == []


def test_names_and_values_handed_to_a_text_function_are_not_findings() -> None:
    """
    Given  calls of ``texts`` functions with variables, calls, numbers and an empty start folder
    When   the source is scanned
    Then   nothing is found: only a sentence is a finding
    """
    source = (
        "from ..texts import dialogs as dlg\n"
        "def f(self, folder):\n"
        "    dlg.no_files_found(str(folder))\n"
        "    dlg.restore_settings(applies_immediately=True)\n"
        '    dlg.select_input_folder("")\n'
        '    other.helper("a sentence")\n'
    )

    assert inventory.scan_source(source, path="x.py") == []


def test_a_colour_a_local_name_and_a_debug_note_are_not_findings() -> None:
    """
    Given  a ``*_WARNING`` colour, a ``*_WARNING`` name inside a function, a step reason of the
           automatic image preparation and an invariant raised in ``domain``
    When   each source is scanned under its own path
    Then   none is a finding: no teacher reads any of them
    """
    colour = 'COLOUR_WARNING = "#e67e22"\n'
    local = 'def f():\n    LOCAL_WARNING = "Something is wrong here."\n'
    step = 'result = StepResult(True, reason="outline too small")\n'
    invariant = 'raise ValueError("a batch holds at least one edit")\n'

    assert inventory.scan_source(colour, path="x.py") == []
    assert inventory.scan_source(local, path="x.py") == []
    path = "teachers_teammate/infrastructure/preprocessing/auto.py"
    assert inventory.scan_source(step, path=path) == []
    assert inventory.scan_source(invariant, path="teachers_teammate/domain/x.py") == []
    assert [
        f.kind for f in inventory.scan_source(invariant, path="teachers_teammate/gui/x.py")
    ] == [inventory.ERROR]


def test_the_csv_is_written_where_bazel_run_was_started(monkeypatch, tmp_path: Path) -> None:
    """
    Given  ``bazel run`` started in a folder, which it names in ``BUILD_WORKING_DIRECTORY``
    When   a relative ``--out`` is resolved
    Then   it lies in that folder, while an absolute one stays as it is
    """
    monkeypatch.setenv("BUILD_WORKING_DIRECTORY", str(tmp_path))

    assert inventory.output_path(Path("user_text.csv")) == tmp_path / "user_text.csv"
    assert inventory.output_path(Path("/elsewhere/a.csv")) == Path("/elsewhere/a.csv")


def test_a_fix_a_readiness_a_log_line_and_a_report_line_are_findings() -> None:
    """
    Given  a fix with its button, a readiness, a tray message, a log line, a log pane line and
           a reporter line, each carrying a sentence
    When   the source is scanned
    Then   every sentence is a finding, so the new routes are no way around the ratchet
    """
    source = (
        "def f(self, reporter, worker):\n"
        '    Fix("Ollama is not running", (FixAction("Check again", tooltip="Ask again"),))\n'
        '    Readiness("Select documents to start", Tone.HINT)\n'
        '    self._tray.showMessage("Title", "Done")\n'
        '    self.log_line.emit("Installing the add-ons again")\n'
        '    self._log.append_text("Run stored.")\n'
        '    reporter.status("Found 3 files")\n'
        '    self._reporter.warn("Careful")\n'
        '    other.status("not a reporter")\n'
        '    self.finished.emit("not a log line")\n'
    )

    found = [f.text for f in inventory.scan_source(source, path="x.py")]

    assert sorted(found) == sorted(
        [
            "Ollama is not running",
            "Check again",
            "Ask again",
            "Select documents to start",
            "Title",
            "Done",
            "Installing the add-ons again",
            "Run stored.",
            "Found 3 files",
            "Careful",
        ]
    )


def test_a_single_word_label_a_container_and_a_legend_are_findings() -> None:
    """
    Given  a one-word ``*_LABEL``, a dict of ``*_LABELS``, a ``*_LEGEND`` of text and colour, a
           colour named ``*_LABEL`` and a Word label table in the document-language module
    When   each source is scanned
    Then   the words are findings but the colour and the document table are not
    """
    source = (
        'MODEL_LABEL = "Model:"\n'
        'EXPORT_LABELS = {"docx": "Word Document", "text": "Text File"}\n'
        '_LEGEND = (("OCR", "#27ae60"), ("Proofreading", "#e67e22"))\n'
        'COLOUR_LABEL = "#888"\n'
    )
    table = 'HEADINGS_LABELS = {"english": {"grammar": "Grammar"}}\n'

    found = [f.text for f in inventory.scan_source(source, path="x.py")]

    assert sorted(found) == sorted(["Model:", "Word Document", "Text File", "OCR", "Proofreading"])
    path = "teachers_teammate/infrastructure/docx_builder.py"
    assert inventory.scan_source(table, path=path) == []


def test_a_name_added_to_a_literal_with_no_words_is_not_a_finding() -> None:
    """
    Given  a log line that is a variable plus a newline
    When   the source is scanned
    Then   nothing is found: a newline is no sentence
    """
    source = 'def f(self, msg):\n    self.log_line.emit(msg + "\\n")\n'

    assert inventory.scan_source(source, path="x.py") == []


def test_a_template_of_texts_is_never_filled_at_a_call_site() -> None:
    """
    Given  a module that fills a template taken from ``texts`` with ``.format``, by module and by
           name, and a local template and a plain string filled the same way
    When   the source is scanned for template calls
    Then   only the two templates of ``texts`` are reported
    """
    source = (
        "from ..texts import setup as words\n"
        "from ..texts.setup import DONE_FAILED\n"
        '_LOCAL = "{x}"\n'
        "def f(message):\n"
        "    words.SIZE_MB.format(size=1)\n"
        "    DONE_FAILED.format(message=message)\n"
        "    _LOCAL.format(x=1)\n"
        '    "{a}".format(a=1)\n'
    )

    found = [name for _path, _line, name in inventory.template_calls_in(source, path="x.py")]

    assert found == ["words.SIZE_MB", "DONE_FAILED"]


def test_no_module_fills_a_template_of_texts_itself() -> None:
    """
    Given  every scanned module
    When   its ``.format`` calls on names taken from ``texts`` are collected
    Then   there are none: the dynamic part is a function of ``texts``
    """
    found = inventory.template_calls(_root())

    assert not found, "Make each of these a function in teachers_teammate/texts:\n" + "\n".join(
        f"  {path}:{line}  {name}.format(…)" for path, line, name in found
    )
