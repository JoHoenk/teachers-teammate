"""The summary page's lines: what the assistant will set, and what it downloads.

:func:`summary_rows` turns a plan into the lines the page shows, so the wording can be tested
without a window; :class:`SummaryRows` draws them, one checkbox per download, and hands back the
ones the teacher left ticked.  The place words come from the caller
(:func:`...texts.privacy.short_place`), so the summary, the service list and the "uses" lines
never describe one place differently.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QWidget

from ...application.setup_plan import DownloadKind, PlannedDownload, PlannedStep, SetupPlan
from ...domain.data_location import (
    OCR_ENGINE_NAMES,
    STAGE_ANONYMIZATION,
)
from ...domain.model_catalog import licence_note, short_model_name
from ...domain.stages import Stage
from ...texts import setup as words
from ...texts.setup import name_model_label

_MB_BELOW_GB = 0.1

#: How wide the step name column is, so the details line up.
_NAME_WIDTH = 150
_HINT_STYLE = "color: #666;"


@dataclass(frozen=True)
class SummaryRow:
    """One line of the summary page."""

    #: The step's name, e.g. ``"Reading handwriting"``.
    name: str
    #: What the step uses, e.g. ``"qwen2.5vl:7b · this computer"``.
    detail: str
    #: What this row downloads; empty when there is nothing to download.
    downloads: tuple[PlannedDownload, ...] = ()
    #: The short note at the end of the row, or ``""``.
    note: str = ""


def size_text(download: PlannedDownload) -> str:
    """Return the download's size as the row shows it: ``"6.0 GB"`` or ``"small"``."""
    if download.size_gb is not None and download.size_gb < _MB_BELOW_GB:
        return words.size_mb(download.size_gb * 1000)
    if download.size_gb is None:
        return words.SIZE_SMALL
    return words.size_gb(download.size_gb)


def download_words(download: PlannedDownload, language: str) -> tuple[str, str]:
    """Return what a download is for and what it is called, in words a teacher knows."""
    kind = download.kind
    if kind is DownloadKind.READER_MODEL:
        return words.DONE_PURPOSE_READER, words.DONE_NAME_READER
    if kind is DownloadKind.PAGE_MODELS:
        return words.DONE_PURPOSE_PAGE_MODELS, words.DONE_NAME_PAGE_MODELS
    if kind is DownloadKind.SPACY_MODEL:
        if download.extra:
            return words.DONE_PURPOSE_NAMES, name_model_label(download.name)
        return words.DONE_PURPOSE_NAMES, language or download.name
    if kind is DownloadKind.OLLAMA_MODEL:
        purpose = (
            words.DONE_PURPOSE_READING
            if download.stage == Stage.OCR
            else words.DONE_PURPOSE_PROOFREADING
        )
        return purpose, short_model_name(download.name)
    purpose = (
        words.DONE_PURPOSE_NAMES
        if download.stage == STAGE_ANONYMIZATION
        else words.DONE_PURPOSE_ADDON
    )
    return purpose, download.name


def summary_rows(
    plan: SetupPlan,
    place_of: Callable[[str], str],
    *,
    key_missing: Collection[str] = (),
) -> list[SummaryRow]:
    """Return the summary page's lines for *plan*.

    Args:
        plan: The setup plan the assistant worked out.
        place_of: Names where a provider runs in the few words the panel has room for.
        key_missing: The online providers the setup plan uses that have no key yet.
    """
    correction = next((step for step in plan.steps if step.stage == Stage.CORRECTION), None)
    rows = [_classes_row(plan)]
    for step in plan.steps:
        rows.append(_step_row(step, plan, correction, place_of, key_missing))
        if step.stage == Stage.OCR:
            rows += _page_models_rows(plan)
    return rows


def _page_models_rows(plan: SetupPlan) -> list[SummaryRow]:
    """Return the row of the page models' download; none when they are there or not needed."""
    downloads = tuple(item for item in plan.downloads if item.kind is DownloadKind.PAGE_MODELS)
    if not downloads:
        return []
    return [
        SummaryRow(
            name=words.PAGE_MODELS_NAME, detail=words.PAGE_MODELS_DETAIL, downloads=downloads
        )
    ]


def _classes_row(plan: SetupPlan) -> SummaryRow:
    """Return the row that says what the teacher answered about their classes."""
    hand_in = words.HANDWRITTEN if plan.panel.get("recognition_enabled") else words.TYPED
    return SummaryRow(
        name=words.CLASSES_NAME,
        detail=words.row_classes(str(plan.panel.get("language", "")), hand_in),
    )


def _step_row(
    step: PlannedStep,
    plan: SetupPlan,
    correction: PlannedStep | None,
    place_of: Callable[[str], str],
    key_missing: Collection[str],
) -> SummaryRow:
    """Return the row for one step of the setup plan."""
    name = words.STEP_NAMES.get(step.stage, step.stage)
    downloads = _downloads_for(step, plan)
    if step.stage == STAGE_ANONYMIZATION:
        language = str(plan.panel.get("language", ""))
        if step.view_only:
            return SummaryRow(
                name=words.NAMES_IN_APP_NAME,
                detail=words.row_names_hidden(language),
                downloads=downloads,
            )
        return SummaryRow(name=name, detail=words.row_names_on(language), downloads=downloads)
    if step.kept:
        if step.stage == Stage.OCR:
            engine = OCR_ENGINE_NAMES.get(step.provider, step.provider)
            detail = words.row_kept(engine)
        else:
            detail = words.ROW_KEEPS_OWN
        # A kept reader that is not downloaded yet lists its download (and no "installed").
        return SummaryRow(
            name=name,
            detail=detail,
            downloads=downloads,
            note="" if downloads else words.ROW_INSTALLED,
        )
    if not step.provider:
        return SummaryRow(name=name, detail=words.ROW_OFF)
    if _same_as_correction(step, correction):
        return SummaryRow(name=name, detail=words.ROW_SAME_MODEL)
    note = words.ROW_KEY_MISSING if step.provider in key_missing else _note(step, downloads)
    return SummaryRow(
        name=name,
        detail=f"{step.model} · {place_of(step.provider)}",
        downloads=downloads,
        note=note,
    )


def _same_as_correction(step: PlannedStep, correction: PlannedStep | None) -> bool:
    """Return whether *step* runs the same model as the proofreading, so the row can say so."""
    if correction is None or step.stage != Stage.EVALUATION:
        return False
    return (step.provider, step.model) == (correction.provider, correction.model)


def _downloads_for(step: PlannedStep, plan: SetupPlan) -> tuple[PlannedDownload, ...]:
    """Return the downloads that belong to *step*'s stage."""
    return tuple(
        download
        for download in plan.downloads
        if download.stage == step.stage and download.kind is not DownloadKind.PAGE_MODELS
    )


def _note(step: PlannedStep, downloads: Sequence[PlannedDownload]) -> str:
    """Return the note a row ends in when it has nothing to download."""
    if downloads:
        return ""
    return words.ROW_DOWNLOADED if step.installed else ""


def _download_tooltip(download: PlannedDownload) -> str:
    """Return the tooltip of a download's checkbox: its name, and the terms when they restrict use.

    A model the teacher pulls from Ollama comes under its own licence; only one that restricts
    *use* says so here (see :class:`...domain.model_catalog.ModelLicence`).
    """
    note = licence_note(download.name) if download.kind is DownloadKind.OLLAMA_MODEL else ""
    return f"{download.name}\n{note}" if note else download.name


class SummaryRows(QWidget):
    """The summary page's lines, with a checkbox for every download."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setColumnStretch(1, 1)
        self._boxes: dict[str, QCheckBox] = {}
        self._downloads: dict[str, PlannedDownload] = {}
        self._texts: list[str] = []

    def set_rows(self, rows: Sequence[SummaryRow], language: str = "") -> None:
        """Replace the lines with *rows*, every download ticked.

        *language* is the document language, which names the name-detection model.
        """
        self._clear()
        line = 0
        for row in rows:
            line = self._add_row(line, row, language)

    def downloads(self) -> list[PlannedDownload]:
        """Return the downloads the teacher left ticked."""
        return [
            download for name, download in self._downloads.items() if self._boxes[name].isChecked()
        ]

    def row_texts(self) -> list[str]:
        """Return the lines as the page shows them, one string per line."""
        return list(self._texts)

    def _add_row(self, line: int, row: SummaryRow, language: str) -> int:
        """Draw *row* at *line*, its downloads on the lines below, and return the next free line."""
        name = QLabel(row.name)
        name.setMinimumWidth(_NAME_WIDTH)
        name.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._grid.addWidget(name, line, 0)
        detail = QLabel(row.detail)
        detail.setWordWrap(True)
        self._grid.addWidget(detail, line, 1)
        self._texts.append(f"{row.name}: {row.detail}")
        if not row.downloads:
            if row.note:
                self._grid.addWidget(_hint(row.note), line, 2)
                self._texts[-1] = f"{self._texts[-1]} — {row.note}"
            return line + 1
        for offset, download in enumerate(row.downloads, start=1):
            _purpose, what = download_words(download, language)
            label = f"{what} · {size_text(download)}"
            box = QCheckBox(label)
            box.setChecked(True)
            box.setToolTip(_download_tooltip(download))
            self._boxes[download.name] = box
            self._downloads[download.name] = download
            self._grid.addWidget(box, line + offset, 1, 1, 2)
            self._texts.append(label)
        return line + 1 + len(row.downloads)

    def _clear(self) -> None:
        """Drop every line, so a new plan starts from an empty grid."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._boxes.clear()
        self._downloads.clear()
        self._texts.clear()


def _hint(text: str) -> QLabel:
    """Return *text* as a small grey note at the end of a row."""
    label = QLabel(text)
    label.setStyleSheet(_HINT_STYLE)
    label.setAlignment(Qt.AlignmentFlag.AlignTop)
    return label
