"""Settings → AI models: which AI reads the handwriting, proofreads and reviews.

The widgets stacked one above the other -- text recognition, proofreading, content review.
There is no temperature to set: the app sends the value each model's maker recommends.  The API
keys the online services need are on *Where the AI runs*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

from ...config import DEFAULTS, OcrConfig
from .._model_fetch import _available_providers
from .._ocr_config_selector import OcrModelPicker
from . import _page
from ._ai_service_section import CONTENT_REVIEW, PROOFREADING, AiServiceSection, AiServiceSpec
from ._page import SettingsContext, SettingsPage, SettingsPageBase

TITLE = "AI models"
DESCRIPTION = (
    "Which AI reads the handwriting, and which AI proofreads and reviews the text. "
    "You set this once."
)
READING_GROUP = "Reading handwriting"
READING_NOTE = "Always on this computer (or your school's own server): pages never go online."
PROOFREADING_GROUP = "Proofreading"
REVIEW_GROUP = "Content review"
KEY_HINT_LINK = "Where the AI runs"
KEY_HINT = (
    "Online AI services such as OpenAI or Anthropic are offered in the lists above once "
    "their key is saved under <a href='keys'>{page}</a>."
)
_OCR_KEYS = ("ocr_engine", "ocr_model")


class AiModelsPage(SettingsPageBase):
    """Text recognition picker, proofreading and review sections, temperatures."""

    page = SettingsPage.AI_MODELS
    title = TITLE
    description = DESCRIPTION

    #: The user chose another recognition method (not emitted while loading).
    ocr_engine_changed = Signal(str)
    #: An add-on (PaddleOCR) was installed from this page.
    addon_installed = Signal(str)
    #: ``(provider, model)`` of the proofreading step changed (not emitted while loading).
    proofreading_choice_changed = Signal(str, str)
    #: ``(provider, model)`` of the content review changed (not emitted while loading).
    review_choice_changed = Signal(str, str)

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        settings = context.settings
        hardware = str(settings.get("hardware_profile", DEFAULTS["hardware_profile"]))
        ollama_url = str(settings.get("ollama_url") or DEFAULTS["ollama_url"])
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        reading = QGroupBox(READING_GROUP)
        reading_layout = QVBoxLayout(reading)
        note = QLabel(READING_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        reading_layout.addWidget(note)
        self._ocr_picker = OcrModelPicker(
            app_service=context.app_service,
            ollama_url=ollama_url,
            hardware_profile=hardware,
            open_downloads=self._open_addons_callback(_page.FOCUS_LOCAL_MODELS),
            parent=reading,
        )
        self._ocr_picker.addon_installed.connect(self.addon_installed.emit)
        self._ocr_picker.engine_changed.connect(self._on_engine_changed)
        reading_layout.addWidget(self._ocr_picker)
        layout.addWidget(reading)
        self._reading_group = reading

        self._proofreading, self._proofreading_group = self._build_section(
            PROOFREADING_GROUP, PROOFREADING, hardware, ollama_url
        )
        layout.addWidget(self._proofreading_group)
        self._review, self._review_group = self._build_section(
            REVIEW_GROUP, CONTENT_REVIEW, hardware, ollama_url
        )
        layout.addWidget(self._review_group)
        # One "Suggestions are for …" line (under Reading handwriting) is enough for the page.
        self._proofreading.hide_profile_line()
        self._review.hide_profile_line()
        # The same-model tip compares both steps' choices as they are now, not as saved.
        self._proofreading.choice_changed.connect(self._review.set_other_choice)
        self._proofreading.choice_changed.connect(self.proofreading_choice_changed.emit)
        self._review.choice_changed.connect(self._proofreading.set_other_choice)
        self._review.choice_changed.connect(self.review_choice_changed.emit)

        key_hint = self._build_key_hint()
        if key_hint is not None:
            layout.addWidget(key_hint)

        layout.addStretch(1)

    def _build_key_hint(self) -> QLabel | None:
        """Say why only some services are offered, and where the missing keys are entered.

        ``None`` when every service is offered already (nothing is missing).
        """
        service = self._context.app_service
        if len(_available_providers(service)) >= len(service.list_providers()):
            return None
        label = QLabel(KEY_HINT.format(page=KEY_HINT_LINK))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        label.setStyleSheet("color: #888; font-size: 11px;")
        label.linkActivated.connect(
            lambda _link: self._navigate(SettingsPage.AI_SETUP, _page.FOCUS_API_KEYS)
        )
        return label

    def _build_section(
        self, title: str, spec: AiServiceSpec, hardware: str, ollama_url: str
    ) -> tuple[AiServiceSection, QGroupBox]:
        group = QGroupBox(title)
        group_layout = QVBoxLayout(group)
        section = AiServiceSection(
            spec,
            app_service=self._context.app_service,
            hardware_profile=hardware,
            ollama_url=ollama_url,
            open_downloads=self._open_addons_callback(_page.FOCUS_LOCAL_MODELS),
            parent=group,
        )
        group_layout.addWidget(section)
        return section, group

    # ── settings ──────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        self._loading = True
        try:
            self._load_ocr(values)
            self._proofreading.load(values)
            self._review.load(values)
        finally:
            self._loading = False
        # Hand each step the other's choice as it is now shown (the load read the saved one).
        self._review.set_other_choice(*self._proofreading.choice())
        self._proofreading.set_other_choice(*self._review.choice())

    def _load_ocr(self, values: Mapping[str, Any]) -> None:
        if "ocr_engine" not in values and "ocr_model" not in values:
            return
        ocr = OcrConfig.from_settings(values)
        self._ocr_picker.set_engine(ocr.engine)
        # Re-apply engine-dependent state first (it populates the model list), then the model.
        self._ocr_picker.refresh()
        self._ocr_picker.set_model(ocr.model)

    def values(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ocr_engine": self._ocr_picker.engine(),
            "ocr_model": self._ocr_picker.model(),
        }
        result.update(self._proofreading.values())
        result.update(self._review.values())
        return result

    def owned_keys(self) -> frozenset[str]:
        return frozenset(
            (
                *_OCR_KEYS,
                PROOFREADING.provider_key,
                PROOFREADING.model_key,
                CONTENT_REVIEW.provider_key,
                CONTENT_REVIEW.model_key,
            )
        )

    # ── shared state ──────────────────────────────────────────────────────────

    def set_hardware_setting(self, setting: str) -> None:
        self._ocr_picker.set_hardware_setting(setting)
        self._proofreading.set_hardware_setting(setting)
        self._review.set_hardware_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        self._ocr_picker.set_ollama_url(url)
        self._proofreading.set_ollama_url(url)
        self._review.set_ollama_url(url)

    def on_packages_changed(self) -> None:
        # Looking again re-applies the method's state and says "changed" with the same method;
        # that is no choice by the user and must not reset the image preparation.
        self._loading = True
        try:
            self._ocr_picker.on_packages_changed()
        finally:
            self._loading = False
        self._proofreading.on_packages_changed()
        self._review.on_packages_changed()

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        _ = preselect
        target = {
            _page.FOCUS_OCR: self._reading_group,
            _page.FOCUS_PROOFREADING: self._proofreading_group,
            _page.FOCUS_REVIEW: self._review_group,
        }.get(focus)
        if target is not None:
            self._scroll_to(target)

    def shutdown(self) -> None:
        self._ocr_picker.stop_threads()
        self._proofreading.shutdown()
        self._review.shutdown()

    # ── internal ──────────────────────────────────────────────────────────────

    def ocr_choice(self) -> tuple[str, str]:
        """Return ``(engine, model)`` of text recognition as shown."""
        return self._ocr_picker.engine(), self._ocr_picker.model()

    def _on_engine_changed(self, engine: str) -> None:
        if not self._loading:
            self.ocr_engine_changed.emit(engine)
