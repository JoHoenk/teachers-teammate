"""What is sent for this file: the exact text and instructions each step receives.

One section per step that leaves the computer, in the order the steps run.  Each section
names the destination (provider, host, EU or outside the EU), says whether names are hidden,
shows the text exactly as the service receives it — placeholders highlighted — and keeps the
instructions in a collapsed block.  The second view compares the extracted text with its
anonymized form.

The content comes from :func:`~teachers_teammate.application.sent_text.describe_sent_texts`,
so this module only presents it.
"""

from __future__ import annotations

from html import escape

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..application.sent_text import SentText, SentTexts
from ._data_flow_summary import level_color, level_text
from ._formatted_text import chip_placeholders
from ._preview_panel import DiffWidget

TITLE = "What is sent for this file"
TAB_SENT = "What is sent"
TAB_PERSONAL = "Personal information"
NOTHING_LEAVES = "Everything stays on this computer."
NO_SECTION = "No step sends this file's text away."
PAGES_SENT = "The page images of this file are sent to another computer you control."
SHOW_PROMPT = "Show the instructions sent"
HIDE_PROMPT = "Hide the instructions sent"
EMPTY_TEXT = "(the text is empty)"


def highlighted_html(text: str, placeholders: tuple[str, ...]) -> str:
    """Return *text* as HTML with every occurrence of *placeholders* marked.

    Longer placeholders win over shorter ones that are part of them, so ``[PERSON_1]`` is
    highlighted as a whole even when ``[PERSON]`` is also in the list.
    """
    body = escape(text) if text else escape(EMPTY_TEXT)
    if text:
        body = chip_placeholders(body, placeholders)
    return (
        "<html><body>"
        "<pre style='white-space: pre-wrap; font-family: monospace;'>"
        f"{body}</pre></body></html>"
    )


class _SentSection(QGroupBox):
    """One step that leaves the computer: destination, names note, text, instructions."""

    def __init__(self, section: SentText, parent: QWidget | None = None) -> None:
        super().__init__(f"{section.title} — {section.badge}", parent)
        self._section = section
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        names = QLabel(section.names_note)
        names.setWordWrap(True)
        names.setStyleSheet("color: #c0392b; font-weight: bold;")
        if section.names_hidden:
            names.setStyleSheet("color: #27ae60; font-weight: bold;")
        layout.addWidget(names)

        self._body = QTextEdit()
        self._body.setReadOnly(True)
        self._body.setObjectName("sent_text")
        self._body.setHtml(highlighted_html(section.text, section.placeholders))
        layout.addWidget(self._body, stretch=1)

        self._prompt_button = QPushButton(SHOW_PROMPT)
        self._prompt_button.setCheckable(True)
        self._prompt_button.setFlat(True)
        layout.addWidget(self._prompt_button)

        self._prompt = QTextEdit()
        self._prompt.setReadOnly(True)
        self._prompt.setObjectName("sent_prompt")
        self._prompt.setPlainText(section.prompt or "(no instructions are sent with this step)")
        self._prompt.setVisible(False)
        layout.addWidget(self._prompt)

        self._prompt_button.toggled.connect(self._on_prompt_toggled)

    def _on_prompt_toggled(self, shown: bool) -> None:
        self._prompt.setVisible(shown)
        self._prompt_button.setText(HIDE_PROMPT if shown else SHOW_PROMPT)

    def text_view(self) -> QTextEdit:
        """The view holding the text exactly as it is sent."""
        return self._body

    def prompt_view(self) -> QTextEdit:
        """The view holding the instructions, hidden until the user asks for them."""
        return self._prompt

    def prompt_button(self) -> QPushButton:
        """The button that shows and hides the instructions."""
        return self._prompt_button


class WhatIsSentDialog(QDialog):
    """Show, per step, what leaves this computer for one file and what it looks like."""

    def __init__(
        self,
        report: SentTexts,
        *,
        file_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._report = report
        self.setWindowTitle(f"{TITLE} — {file_name}" if file_name else TITLE)
        self.resize(900, 650)

        layout = QVBoxLayout(self)
        level = report.level
        headline = QLabel(level_text(level))
        headline.setObjectName("headline")
        headline.setStyleSheet(f"color: {level_color(level)}; font-weight: bold;")
        headline.setWordWrap(True)
        layout.addWidget(headline)

        self._section_widgets: list[_SentSection] = []
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_sent_tab(), TAB_SENT)
        if report.has_preview:
            self._tabs.addTab(self._build_personal_tab(), TAB_PERSONAL)
        layout.addWidget(self._tabs, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _build_sent_tab(self) -> QWidget:
        """Return the tab with one section per step."""
        page = QWidget()
        outer = QVBoxLayout(page)
        if not self._report.leaves_computer:
            note = QLabel(NOTHING_LEAVES)
            note.setWordWrap(True)
            note.setStyleSheet("font-weight: bold;")
            outer.addWidget(note)
            hint = QLabel(NO_SECTION)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888;")
            outer.addWidget(hint)
            outer.addStretch(1)
            return page

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        sections = QVBoxLayout(inner)
        sections.setSpacing(8)
        for stage in self._report.page_stages:
            pages = QLabel(f"{stage.title} — {stage.badge}\n{PAGES_SENT}")
            pages.setObjectName("page_stage")
            pages.setWordWrap(True)
            pages.setToolTip(stage.location.details)
            pages.setStyleSheet("font-weight: bold;")
            sections.addWidget(pages)
        for section in self._report.sections:
            widget = _SentSection(section)
            widget.setToolTip(section.details)
            sections.addWidget(widget)
            self._section_widgets.append(widget)
        sections.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, stretch=1)
        return page

    def _build_personal_tab(self) -> QWidget:
        """Return the tab comparing the extracted text with its anonymized form."""
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Left: the extracted text. Right: what a service sees when names are hidden.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888;")
        layout.addWidget(note)
        diff = DiffWidget()
        diff.setObjectName("personal_info_diff")
        diff.set_texts(self._report.raw_text, self._report.anonymized_raw)
        layout.addWidget(diff, stretch=1)
        return page

    def sections(self) -> tuple[SentText, ...]:
        """Return the steps shown, in the order they run."""
        return self._report.sections

    def section_widgets(self) -> list[_SentSection]:
        """Return the section widgets shown, top to bottom."""
        return list(self._section_widgets)

    def shown_texts(self) -> list[str]:
        """Return each section's text view content as plain text, top to bottom."""
        views = self.findChildren(QTextEdit, "sent_text")
        return [view.toPlainText() for view in views]

    def headline(self) -> str:
        """Return the sentence under the title, without markup."""
        label = self.findChild(QLabel, "headline")
        return label.text() if label is not None else level_text(self._report.level)

    def tabs(self) -> list[str]:
        """Return the tab titles, left to right."""
        return [self._tabs.tabText(i) for i in range(self._tabs.count())]

    def tab_text(self, index: int = 0) -> str:
        """Return every label of one tab, joined by newlines (empty for a missing tab)."""
        page = self._tabs.widget(index)
        if page is None:
            return ""
        return "\n".join(label.text() for label in page.findChildren(QLabel))

    def section_texts(self) -> str:
        """Return every label inside the sections, joined by newlines."""
        return "\n".join(
            label.text()
            for widget in self._section_widgets
            for label in widget.findChildren(QLabel)
        )

    def personal_info_text(self) -> str:
        """Return the text of the original-vs-anonymized comparison (empty without it)."""
        page = (
            self._tabs.widget(self.tabs().index(TAB_PERSONAL))
            if TAB_PERSONAL in self.tabs()
            else None
        )
        if page is None:
            return ""
        diff = page.findChild(QTextEdit, "personal_info_diff")
        return diff.toPlainText() if diff is not None else ""
