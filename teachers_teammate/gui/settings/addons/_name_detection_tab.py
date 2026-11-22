"""The *Name detection* tab: spaCy itself and the name-detection models.

spaCy is the library that finds names and other personal information; without it the
anonymizer cannot run at all, so it has a box of its own above the models.  A model alone never
ticks spaCy (only the fix that says spaCy is missing does), and neither box installs anything
without the teacher's click on the page's install button.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QVBoxLayout, QWidget

from ....application.composition import AppServices
from ....domain.language import language_name
from ....domain.name_models import SPACY_MODELS
from ....texts import addons as txt
from ....texts.settings import license_tooltip
from ..._constants import _SPACY_MODEL_CHOICES as _SPACY_MODEL_DOWNLOAD_CHOICES
from ..._install_round import PACKAGE_PREFIX
from ..._model_guidance import SPACY_PREFIX
from ._common import ScrolledTab, apply_installed, tick_enabled

#: The key of spaCy's own box; also the pip package the round installs for it.
SPACY_ITEM = "spacy"
#: The language of each name-detection model, for the status line ("the German model").
_SPACY_MODEL_LANGUAGES: dict[str, str] = {
    model: language_name(language) for language, model in SPACY_MODELS.items()
}


class NameDetectionTab(ScrolledTab):
    """spaCy's box, then one box per name-detection model, in the order the app offers them."""

    def __init__(self, app_service: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._svc = app_service
        #: The items of this tab, keyed like the installation check reports them.
        self.checkboxes: dict[str, QCheckBox] = {}
        self._pending: list[str] = []

        install_group = QGroupBox("Name detection")
        install_layout = QVBoxLayout(install_group)
        intro = QLabel(
            "spaCy is the library that finds names and other personal information. "
            "Install it first, then download a name detection model below."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa; font-size: 9pt;")
        install_layout.addWidget(intro)
        cb_spacy = QCheckBox("spaCy  (spacy)")
        cb_spacy.setToolTip(license_tooltip(self._svc.addons.addon_license(SPACY_ITEM)))
        self.checkboxes[SPACY_ITEM] = cb_spacy
        install_layout.addWidget(cb_spacy)
        self.body_layout.addWidget(install_group)

        model_group = QGroupBox("Name detection models")
        model_layout = QVBoxLayout(model_group)
        for lbl, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES:
            cb = QCheckBox(lbl)
            cb.setToolTip(license_tooltip(self._svc.addons.addon_license(model_id)))
            self.checkboxes[f"{SPACY_PREFIX}{model_id}"] = cb
            model_layout.addWidget(cb)
        self.body_layout.addWidget(model_group)
        self.body_layout.addStretch(1)

    def pip_packages(self) -> list[str]:
        """Return the pip packages whose box is ticked (spaCy, when it is)."""
        cb = self.checkboxes.get(SPACY_ITEM)
        return [SPACY_ITEM] if cb and cb.isChecked() and cb.isEnabled() else []

    def spacy_models(self) -> list[str]:
        """Return the name-detection models whose box is ticked, in the offered order."""
        models: list[str] = []
        for _, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES:
            cb = self.checkboxes.get(f"{SPACY_PREFIX}{model_id}")
            if cb and cb.isChecked() and cb.isEnabled():
                models.append(model_id)
        return models

    def step_label(self, item: str) -> str | None:
        """Return how the status line names *item*, or ``None`` when this tab does not own it."""
        if item == f"{PACKAGE_PREFIX}{SPACY_ITEM}":
            return txt.ADDONS_ITEM_SPACY
        if item.startswith(SPACY_PREFIX):
            language = _SPACY_MODEL_LANGUAGES.get(item.removeprefix(SPACY_PREFIX))
            return txt.addons_spacy_model(language) if language else txt.ADDONS_ITEM_MULTILINGUAL
        return None

    def preselect(self, entries: Sequence[str]) -> None:
        """Tick spaCy's box and the model boxes a deep link names.

        A model alone ticks only that model: a tick always means "will be installed", and a
        model does not need spaCy to be downloaded again.
        """
        keys = [
            entry.removeprefix(PACKAGE_PREFIX) if entry.startswith(PACKAGE_PREFIX) else entry
            for entry in entries
            if entry.startswith((PACKAGE_PREFIX, SPACY_PREFIX))
        ]
        self._pending.extend(keys)
        tick_enabled(self.checkboxes, keys)

    def tick_preselect(self) -> None:
        """Tick the requested boxes again, now that the check knows what is installed."""
        tick_enabled(self.checkboxes, self._pending)
        self._pending.clear()

    def mark_installed(self, results: Mapping[str, object]) -> None:
        """Show whether spaCy and which models are importable already, and disable those boxes."""
        apply_installed(self.checkboxes, results)
