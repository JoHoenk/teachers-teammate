"""The *Side by side* tab: the student's sentences beside each model's answer to them."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import html

from PySide6.QtWidgets import QSplitter, QTextBrowser, QVBoxLayout, QWidget

from ...application.check_items import ItemKind
from ...domain.proofreading.model_comparison import Comparison
from ...texts import compare as txt
from ..check._style import MARK_COLOURS
from ._rows import side_rows

__all__ = ["SideBySidePanel"]


class SideBySidePanel(QWidget):
    """One column per version, one line per sentence, so the rows line up down the page.

    A line is marked where that version changed the sentence: the Check tab's own colour for a
    change, so a mark means the same in both tabs.  What exactly was rewritten is the *Changes*
    tab's business; this tab answers "did it touch this sentence at all".
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._split = QSplitter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._split)
        self._views: list[QTextBrowser] = []
        self._empty = QTextBrowser(self)
        layout.addWidget(self._empty)

    def show_comparison(self, comparison: Comparison | None, labels: Mapping[str, str]) -> None:
        """Show *comparison* as the student's sentences beside every version's."""
        self._clear()
        if comparison is None or not comparison.rows:
            self._empty.setPlainText(txt.NOTHING_STORED)
            self._empty.setVisible(True)
            return
        self._empty.setVisible(False)
        rows = side_rows(comparison)
        self._add_column(txt.STUDENT_WROTE, [row.source for row in rows], [])
        for index, figures in enumerate(comparison.versions):
            self._add_column(
                labels.get(figures.key, figures.key),
                [row.versions[index] for row in rows],
                [row.changed[index] for row in rows],
            )

    def _add_column(self, title: str, lines: Sequence[str], changed: Sequence[bool]) -> None:
        """Add the column of one text, marking the lines *changed* says it changed."""
        view = QTextBrowser(self._split)
        view.setOpenExternalLinks(False)
        background, foreground = MARK_COLOURS[ItemKind.CHANGED]
        marked = "".join(
            f'<div style="background:{background}; color:{foreground};">{html.escape(line)}</div>'
            if did_change
            else f"<div>{html.escape(line)}</div>"
            for line, did_change in zip(lines, _flags(changed, len(lines)), strict=True)
        )
        view.setHtml(f"<b>{html.escape(title)}</b>{marked}")
        self._split.addWidget(view)
        self._views.append(view)

    def _clear(self) -> None:
        """Drop the columns of the previous comparison."""
        for view in self._views:
            view.deleteLater()
        self._views.clear()


def _flags(changed: Sequence[bool], count: int) -> Sequence[bool]:
    """Return one flag per line: *changed* where it names a line, ``False`` for the rest."""
    return list(changed) + [False] * (count - len(changed))
