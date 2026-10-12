"""Hint line and "Use best choice" button guiding Ollama model selection.

Shared by the Text Recognition, Proofreading, and Content Review dialogs.  The
widget decorates an editable model combo purely through item roles (text stays
the raw model tag, so nothing leaks into the settings file), sorts the model
list by suitability for the task, shows a plain-language hint below the model
box, and offers the best model via one button — selecting it when it is
installed, or forwarding to the Downloads dialog when it is not.

The widget never talks to the Ollama server: ratings come from
``rate_ollama_models(..., cached_only=True)`` after a background thread
classified the models, so the GUI thread cannot block on network requests.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from ..domain.model_catalog import (
    HardwareProfile,
    ModelCategory,
    ModelRating,
    Suitability,
    best_model,
    normalize_tag,
    suitability_rank,
)
from ._hardware_profile_selector import HardwareProfileSelector
from ._model_wording import (
    HINT_NOT_DOWNLOADED,
    HINT_NOT_DOWNLOADED_WITH_LINK,
    is_warning,
    model_hint,
    suitability_text,
)

#: Opens the Downloads dialog for the given model tags with the given parent
#: widget and returns the ``hardware_profile`` setting the user left behind
#: (``None`` when unknown).  Supplied by the main window.
OpenDownloads = Callable[[list[str], "QWidget | None"], "str | None"]

_GREY = QColor("#888888")
_ORANGE = QColor("#e67e22")
_HINT_STYLE = "color: #888; font-size: 11px;"
_WARNING_STYLE = "color: #e67e22; font-size: 11px;"


class _GuidanceService(Protocol):
    def rate_ollama_models(
        self,
        tags: list[str],
        task: ModelCategory,
        profile: HardwareProfile,
        ollama_url: str,
        *,
        cached_only: bool = False,
    ) -> list[ModelRating]: ...


class OllamaModelGuidance(QWidget):
    """Hint line + "Use best choice" button for an Ollama model combo."""

    def __init__(
        self,
        *,
        combo: QComboBox,
        app_service: _GuidanceService,
        category: ModelCategory,
        profile_selector: HardwareProfileSelector,
        refetch: Callable[[], None],
        ollama_url: str = "",
        open_downloads: OpenDownloads | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._combo = combo
        self._service = app_service
        self._category = category
        self._selector = profile_selector
        self._ollama_url = ollama_url
        self._open_downloads = open_downloads
        self._refetch = refetch
        self._models: list[str] = []
        self._installed: dict[str, str] = {}  # normalised tag -> spelling as listed
        self._ratings: dict[str, ModelRating] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._hint_lbl = QLabel("")
        self._hint_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._hint_lbl.setWordWrap(True)
        self._hint_lbl.setStyleSheet(_HINT_STYLE)
        self._hint_lbl.linkActivated.connect(self._on_download_link)
        layout.addWidget(self._hint_lbl, stretch=1)
        self._best_btn = QPushButton("Use best choice")
        self._best_btn.clicked.connect(self._on_best_button)
        layout.addWidget(self._best_btn)

        self._combo.currentTextChanged.connect(lambda _txt: self._update_hint())
        self._selector.resolved_changed.connect(self._on_resolved)
        # Use the already-resolved profile when detection finished before wiring.
        resolved = self._selector.resolved()
        if resolved is not None:
            self._on_resolved(resolved)

    # ── public API ─────────────────────────────────────────────────────────

    def set_ollama_url(self, url: str) -> None:
        """Update the Ollama server URL used for classification."""
        self._ollama_url = url

    def set_models(self, models: Sequence[str]) -> None:
        """Show *models* sorted and decorated by suitability for the task.

        Classification must have happened in a background thread already;
        models that could not be classified are rated "no suggestion".
        """
        self._models = list(models)
        self._installed = {normalize_tag(tag): tag for tag in self._models}
        self._rate(self._models)
        previous = self._combo.currentText()
        self._combo.blockSignals(True)
        self._combo.clear()
        for tag in sorted(self._models, key=self._sort_key):
            self._combo.addItem(tag)
            self._decorate(tag)
        if previous:
            idx = self._combo.findText(previous)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            else:
                self._combo.setCurrentText(previous)
        self._combo.blockSignals(False)
        self._update_hint()
        self._update_button()

    def active_profile(self) -> HardwareProfile:
        """Return the active hardware profile ("8gb" until detection lands)."""
        detection = self._selector.resolved()
        if detection is not None:
            return detection.profile
        return "8gb"

    # ── internal ───────────────────────────────────────────────────────────

    def _rate(self, tags: list[str]) -> None:
        """(Re-)rate *tags* for the active profile; cache hits only, no network."""
        ratings = self._service.rate_ollama_models(
            tags, self._category, self.active_profile(), self._ollama_url, cached_only=True
        )
        self._ratings = {rating.tag: rating for rating in ratings}

    def _rating_for(self, tag: str) -> ModelRating:
        """Return the rating of *tag*, rating a typed, not-listed tag on demand."""
        rating = self._ratings.get(tag)
        if rating is None:
            rating = self._service.rate_ollama_models(
                [tag], self._category, self.active_profile(), self._ollama_url, cached_only=True
            )[0]
            self._ratings[tag] = rating
        return rating

    def _sort_key(self, tag: str) -> tuple[int, str]:
        return (suitability_rank(self._rating_for(tag).suitability), tag)

    def _decorate(self, tag: str) -> None:
        """Set foreground/tooltip roles only — the item text stays the raw tag."""
        index = self._combo.findText(tag)
        if index < 0:
            return
        rating = self._rating_for(tag)
        if rating.suitability is Suitability.TOO_LARGE:
            self._combo.setItemData(index, _GREY, Qt.ItemDataRole.ForegroundRole)
        elif rating.suitability is Suitability.WRONG_KIND:
            self._combo.setItemData(index, _ORANGE, Qt.ItemDataRole.ForegroundRole)
        else:
            self._combo.setItemData(index, None, Qt.ItemDataRole.ForegroundRole)
        note = f"\n{rating.note}" if rating.note else ""
        self._combo.setItemData(
            index,
            f"{suitability_text(rating.suitability, self.active_profile(), self._category)}{note}",
            Qt.ItemDataRole.ToolTipRole,
        )

    def _update_hint(self) -> None:
        tag = self._combo.currentText().strip()
        if not tag:
            self._hint_lbl.setText("")
            return
        profile = self.active_profile()
        warning = False
        rating = self._rating_for(tag)
        if self._models and normalize_tag(tag) not in self._installed:
            hint = (
                HINT_NOT_DOWNLOADED_WITH_LINK
                if self._open_downloads is not None
                else HINT_NOT_DOWNLOADED
            )
        else:
            warning = is_warning(rating.suitability)
            hint = model_hint(
                rating.suitability,
                profile,
                task=self._category,
                memory_gb=rating.memory_gb,
                best_for_task=best_model(self._category, profile),
            )
        self._hint_lbl.setStyleSheet(_WARNING_STYLE if warning else _HINT_STYLE)
        self._hint_lbl.setText(hint)

    def _update_button(self) -> None:
        if not self._models:
            self._best_btn.hide()
            return
        best = best_model(self._category, self.active_profile())
        if normalize_tag(best) in self._installed:
            self._best_btn.setText("Use best choice")
            self._best_btn.setVisible(True)
        elif self._open_downloads is not None:
            self._best_btn.setText("Download best choice…")
            self._best_btn.setVisible(True)
        else:
            self._best_btn.hide()

    def _on_best_button(self) -> None:
        best = best_model(self._category, self.active_profile())
        listed = self._installed.get(normalize_tag(best))
        if listed is not None:
            self._combo.setCurrentText(listed)
        else:
            self._request_download([best])

    def _on_download_link(self, link: str) -> None:
        if link != "download":
            return
        tag = self._combo.currentText().strip()
        if tag:
            self._request_download([tag])

    def _request_download(self, tags: list[str]) -> None:
        """Open Downloads on top of this dialog, then follow up on what changed."""
        if self._open_downloads is None:
            return
        new_setting = self._open_downloads(tags, self.window())
        if new_setting is not None and new_setting != self._selector.setting():
            # The user changed "My computer" in Downloads: hints follow it here.
            self._selector.set_setting(new_setting)
        self._refetch()

    def _on_resolved(self, detection: object) -> None:
        if detection is None:
            return
        self._ratings.clear()  # ratings depend on the profile
        if self._models:
            # Re-sort and re-decorate for the (possibly new) profile.
            self.set_models(self._models)
        self._update_hint()
        self._update_button()
