"""Settings → AI models: which AI reads the handwriting, proofreads and reviews.

Two groups.  *Reading handwriting* is always local.  *Proofreading and content review* run on
this computer or at an online service, and by default **one model serves both steps**: the
content review gets its own service and model only when the teacher ticks "Use a different
model for content review".  That checkbox has no setting of its own; whether it starts ticked
is worked out from the saved models (:func:`review_has_own_model`), and with it unticked the
review settings are saved as a copy of the proofreading ones.

Three things on this page follow from the choice of service:

- an online service shows its API key row right below it (the key is entered where the service
  is picked, see :mod:`._credentials`);
- the context window row appears only when it changes something (a content review on its own
  Ollama model);
- an online service with names in the text turns "Remove personal info" on when OK is pressed,
  unless the teacher keeps it off (:func:`privacy_line_state`).

There is no temperature to set: the app sends the value each model's maker recommends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...application.correction_options import review_has_own_model
from ...config import CONTEXT_SETTINGS, DEFAULTS, OcrConfig
from .. import _user_text as txt
from .._background import LatestTask
from .._ocr_config_selector import OcrModelPicker
from . import _page
from ._ai_service_section import CONTENT_REVIEW, PROOFREADING, AiServiceSection
from ._credentials import ApiKeyDrafts
from ._page import SettingsContext, SettingsPage, SettingsPageBase
from ._profile_line import ProfileLine
from ._speed_hint import SpeedHint

TITLE = "AI models"
DESCRIPTION = (
    "Which AI reads the handwriting, and which AI proofreads and reviews the text. "
    "You set this once."
)
READING_GROUP = "Reading handwriting"
READING_NOTE = "Always on this computer (or your school's own server): pages never go online."
PROOFREADING_GROUP = "Proofreading and content review"
PROOFREADING_NOTE = "On this computer, or at an online service you choose."
OVERRIDE_LABEL = "Use a different model for content review"
OVERRIDE_TOOLTIP = (
    "Off: the content review uses the proofreading model.\n"
    "On: choose another service or model for the review only."
)
CONTEXT_LABEL = "Context window for the content review:"
CONTEXT_TOOLTIP = (
    "How much text the content review may read in one request.\n"
    "Automatic grows the window with the size of the graphics card;\n"
    "a larger window lets longer texts through but needs more memory."
)
LINE_NEEDS_DETECTION = (
    "Names are sent as written. Replacing them needs name detection for {language}."
)
LINE_PROTECTED = "Names are replaced before the text is sent."
LINE_WILL_TURN_ON = (
    f'"{txt.REMOVE_PERSONAL_INFO}" will be turned on when you press OK, so names are replaced '
    "before the text is sent."
)
LINE_NAMES_SENT = "Names are sent as written."
BUTTON_INSTALL = "Install…"
BUTTON_KEEP_OFF = "Keep it off"
BUTTON_TURN_ON = f'Turn on "{txt.REMOVE_PERSONAL_INFO}"'
_GREEN = "#27ae60"
_ORANGE = "#e67e22"
_OCR_KEYS = ("ocr_engine", "ocr_model")
_PROOFREADING_KEYS = (PROOFREADING.provider_key, PROOFREADING.model_key)
_REVIEW_KEYS = (CONTENT_REVIEW.provider_key, CONTENT_REVIEW.model_key)
#: Left margin of the review rows, so they read as part of the checkbox above them.
_REVIEW_INDENT = 20


class PrivacyLineState(StrEnum):
    """What the line about names at the end of the block says."""

    HIDDEN = "hidden"
    NEEDS_DETECTION = "needs_detection"
    PROTECTED = "protected"
    WILL_TURN_ON = "will_turn_on"
    NAMES_SENT = "names_sent"


def privacy_line_state(
    *, online: bool, removal_on: bool, available: bool, turn_on: bool
) -> PrivacyLineState:
    """Return the state of the privacy line.

    *online*: a step sends text to a service. *removal_on*: "Remove personal info" is ticked in
    the main window. *available*: name detection can run for the document language. *turn_on*:
    the page plans to tick the box on OK (the teacher has not said to keep it off).
    """
    if not online:
        return PrivacyLineState.HIDDEN
    if not available:
        return PrivacyLineState.NEEDS_DETECTION
    if removal_on:
        return PrivacyLineState.PROTECTED
    return PrivacyLineState.WILL_TURN_ON if turn_on else PrivacyLineState.NAMES_SENT


class AiModelsPage(SettingsPageBase):
    """Recognition picker, one shared proofreading/review model with an optional override."""

    page = SettingsPage.AI_MODELS
    title = TITLE
    description = DESCRIPTION

    #: The user chose another recognition method (not emitted while loading).
    ocr_engine_changed = Signal(str)
    #: The service or model of a step changed: the speed test on *This computer* measures
    #: other models now.
    speed_choices_changed = Signal()
    #: The built-in reader's models were downloaded from this page.
    addon_installed = Signal(str)

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        settings = context.settings
        self._app_service = context.app_service
        hardware = str(settings.get("hardware_profile", DEFAULTS["hardware_profile"]))
        self._ollama_url = str(settings.get("ollama_url") or DEFAULTS["ollama_url"])
        self._loading = False
        self._drafts = context.key_drafts or ApiKeyDrafts(context.app_service, parent=self)
        # The privacy line (see _refresh_privacy_line).
        self._available = bool(context.privacy_available)
        self._was_online = False
        self._turn_on = False
        self._kept_off = False
        self._privacy_state = PrivacyLineState.HIDDEN
        self._availability_task = LatestTask(self)
        #: True once the computer has been checked (or the check failed), so the context row
        #: is not drawn on a guess.
        self._profile_known = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._profile_line = ProfileLine(
            app_service=context.app_service, setting=hardware, ollama_url=self._ollama_url
        )
        self._profile_line.change_requested.connect(lambda: self._navigate(SettingsPage.AI_SETUP))
        self._profile_line.selector.resolved_changed.connect(self._on_profile_resolved)
        layout.addWidget(self._profile_line)

        self._speed_hint = SpeedHint(
            app_service=context.app_service,
            settings_provider=self.speed_settings,
            profile_selector=self._profile_line.selector,
            parent=self,
        )
        self._speed_hint.open_requested.connect(
            lambda: self._navigate(SettingsPage.AI_SETUP, _page.FOCUS_SPEED)
        )
        layout.addWidget(self._speed_hint)

        self._reading_group = self._build_reading_group()
        layout.addWidget(self._reading_group)
        self._proofreading_group = self._build_text_group()
        layout.addWidget(self._proofreading_group)
        layout.addStretch(1)

    # ── construction ──────────────────────────────────────────────────────────

    def _build_reading_group(self) -> QGroupBox:
        reading = QGroupBox(READING_GROUP)
        layout = QVBoxLayout(reading)
        note = QLabel(READING_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)
        self._ocr_picker = OcrModelPicker(
            app_service=self._app_service,
            ollama_url=self._ollama_url,
            open_downloads=self._open_addons_callback(_page.FOCUS_LOCAL_MODELS),
            profile_selector=self._profile_line.selector,
            parent=reading,
        )
        self._ocr_picker.addon_installed.connect(self.addon_installed.emit)
        self._ocr_picker.engine_changed.connect(self._on_engine_changed)
        layout.addWidget(self._ocr_picker)
        return reading

    def _build_text_group(self) -> QGroupBox:
        group = QGroupBox(PROOFREADING_GROUP)
        layout = QVBoxLayout(group)
        note = QLabel(PROOFREADING_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)

        self._shared = self._build_section(PROOFREADING, same_model_tip=False)
        layout.addWidget(self._shared)

        self._override = QCheckBox(OVERRIDE_LABEL)
        self._override.setToolTip(OVERRIDE_TOOLTIP)
        self._override.toggled.connect(self._on_override_toggled)
        layout.addWidget(self._override)

        self._review_box = QWidget()
        box_layout = QVBoxLayout(self._review_box)
        box_layout.setContentsMargins(_REVIEW_INDENT, 0, 0, 0)
        self._review = self._build_section(CONTENT_REVIEW, same_model_tip=True)
        box_layout.addWidget(self._review)
        self._review_box.setVisible(False)
        layout.addWidget(self._review_box)

        # The same-model tip compares both choices as they are now, not as saved.
        self._shared.choice_changed.connect(self._on_shared_choice_changed)
        self._review.choice_changed.connect(lambda _p, _m: self._on_choices_changed())

        self._context_row, self._context_combo = self._build_context_row()
        layout.addWidget(self._context_row)
        line, self._privacy_label, self._privacy_button = self._build_privacy_line()
        layout.addLayout(line)
        return group

    def _build_section(self, spec, *, same_model_tip: bool) -> AiServiceSection:
        # One "Suggestions are for …" line at the top of the page, and one resolution of this
        # computer behind it: the picker and both sections follow that selector.
        return AiServiceSection(
            spec,
            app_service=self._app_service,
            ollama_url=self._ollama_url,
            open_downloads=self._open_addons_callback(_page.FOCUS_LOCAL_MODELS),
            key_drafts=self._drafts,
            profile_selector=self._profile_line.selector,
            same_model_tip=same_model_tip,
        )

    def _build_context_row(self) -> tuple[QWidget, QComboBox]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(CONTEXT_LABEL)
        label.setToolTip(CONTEXT_TOOLTIP)
        layout.addWidget(label)
        combo = QComboBox()
        for setting in CONTEXT_SETTINGS:
            combo.addItem(setting, setting)
        combo.setToolTip(CONTEXT_TOOLTIP)
        layout.addWidget(combo, stretch=1)
        row.setVisible(False)
        return row, combo

    def _build_privacy_line(self) -> tuple[QHBoxLayout, QLabel, QPushButton]:
        line = QHBoxLayout()
        label = QLabel()
        label.setWordWrap(True)
        line.addWidget(label, stretch=1)
        button = QPushButton()
        button.clicked.connect(self._on_privacy_button)
        line.addWidget(button)
        label.setVisible(False)
        button.setVisible(False)
        return line, label, button

    # ── settings ──────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        self._loading = True
        try:
            self._load_ocr(values)
            self._shared.load(values)
            ticked = review_has_own_model(values, self._app_service.effective_model)
            self._set_override(ticked)
            if ticked:
                self._review.load(values)
                self._review.set_other_choice(*self._shared.choice())
            else:
                self._retire_review()
            self._review_box.setVisible(ticked)
            if "proofreading_context" in values:
                index = self._context_combo.findData(str(values["proofreading_context"]))
                self._context_combo.setCurrentIndex(index if index >= 0 else 0)
            self._refresh_context_row()
            self._refresh_privacy_line()
            self._speed_hint.refresh()
        finally:
            self._loading = False

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
        result.update(self._shared.values())
        result["proofreading_context"] = str(self._context_combo.currentData())
        if self._override.isChecked():
            result.update(self._review.values())
        else:
            provider, model = self._shared_choice_for_review()
            result[CONTENT_REVIEW.provider_key] = provider
            result[CONTENT_REVIEW.model_key] = model
        return result

    def _shared_choice_for_review(self) -> tuple[str, str]:
        """Return the ``(provider, model)`` the review runs while it shares the proofreading one.

        The review really runs the proofreading model, even if the task defaults differ one
        day: an empty proofreading model is written out as its proofreading default.
        """
        provider, model = self._shared.choice()
        return provider, self._app_service.effective_model(provider, model, "correction")

    def owned_keys(self) -> frozenset[str]:
        return frozenset((*_OCR_KEYS, *_PROOFREADING_KEYS, *_REVIEW_KEYS, "proofreading_context"))

    def apply(self) -> None:
        """Hand the key rows to the drafts (OK): a key pasted a moment ago must not be lost.

        A row commits 800 ms after the last keystroke, and a click on OK does not always take
        the focus away from it, so the window's shutdown would otherwise stop a commit that is
        still waiting.
        """
        self._shared.commit_key()
        self._review.commit_key()

    def turn_on_anonymization(self) -> bool:
        """Return whether OK should tick "Remove personal info" in the main window."""
        return self._privacy_state is PrivacyLineState.WILL_TURN_ON

    # ── shared state ──────────────────────────────────────────────────────────

    def set_hardware_setting(self, setting: str) -> None:
        # The picker and both sections follow the profile line's selector.
        self._profile_line.set_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        self._ollama_url = url or DEFAULTS["ollama_url"]
        self._profile_line.set_ollama_url(url)
        self._ocr_picker.set_ollama_url(url)
        self._shared.set_ollama_url(url)
        self._review.set_ollama_url(url)
        # The address decides whether the local server is this computer, another one, or online.
        self._on_choices_changed()

    def on_packages_changed(self) -> None:
        # Looking again re-applies the method's state and says "changed" with the same method;
        # that is no choice by the user and must not reset the image preparation.
        self._loading = True
        try:
            self._ocr_picker.on_packages_changed()
        finally:
            self._loading = False
        self._shared.on_packages_changed()
        self._review.on_packages_changed()
        self._ask_availability()

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        _ = preselect
        if focus == _page.FOCUS_PROOFREADING_KEY:
            self._focus_key(self._shared, self._proofreading_group)
            return
        if focus == _page.FOCUS_REVIEW_KEY:
            # Unticked, the review shares the proofreading model, and so its key.
            if self._override.isChecked():
                self._focus_key(self._review, self._review_box)
            else:
                self._focus_key(self._shared, self._proofreading_group)
            return
        target = {
            _page.FOCUS_OCR: self._reading_group,
            _page.FOCUS_PROOFREADING: self._proofreading_group,
            _page.FOCUS_REVIEW: self._review_box if self._override.isChecked() else self._override,
        }.get(focus)
        if target is not None:
            self._scroll_to(target)

    def _focus_key(self, section: AiServiceSection, fallback: QWidget) -> None:
        """Scroll to the key row of *section* and put the cursor in its field.

        A service that needs no key has no row: the *fallback* (its group) is shown instead.
        """
        row = section.key_row()
        if row.isHidden():
            self._scroll_to(fallback)
            return
        self._scroll_to(row)
        QTimer.singleShot(0, row.field, row.field.setFocus)

    def shutdown(self) -> None:
        self._ocr_picker.stop_threads()
        self._shared.shutdown()
        self._review.shutdown()

    # ── override ──────────────────────────────────────────────────────────────

    def _set_override(self, ticked: bool) -> None:
        self._override.blockSignals(True)
        self._override.setChecked(ticked)
        self._override.blockSignals(False)

    def _on_override_toggled(self, ticked: bool) -> None:
        """Start the review's own choice from the shared one, or drop it again.

        Ticking always starts from the current shared choice, so ticking alone changes nothing
        and nothing from an earlier tick in the same window is kept.
        """
        if self._loading:
            return
        if ticked:
            provider, model = self._shared_choice_for_review()
            self._review.load(
                {
                    CONTENT_REVIEW.provider_key: provider,
                    CONTENT_REVIEW.model_key: model,
                    PROOFREADING.provider_key: provider,
                    PROOFREADING.model_key: model,
                }
            )
            self._review.set_other_choice(*self._shared.choice())
        else:
            self._retire_review()
        self._review_box.setVisible(ticked)
        self._on_choices_changed()

    def _retire_review(self) -> None:
        """Stop the review section; a key typed into its row is kept for the drafts first."""
        self._review.commit_key()
        self._review.shutdown()

    def _on_shared_choice_changed(self, provider: str, model: str) -> None:
        self._review.set_other_choice(provider, model)
        self._on_choices_changed()

    def _on_choices_changed(self) -> None:
        """A choice of service or model changed: what depends on it is looked at again."""
        self._refresh_context_row()
        self._speed_hint.refresh()
        if not self._loading:
            self._refresh_privacy_line()
            self.speed_choices_changed.emit()

    # ── context window row ────────────────────────────────────────────────────

    def _live_settings(self) -> dict[str, str]:
        """Return the four service/model keys as :meth:`values` would return them now."""
        provider, model = self._shared.choice()
        if self._override.isChecked():
            review_provider, review_model = self._review.choice()
        else:
            review_provider, review_model = self._shared_choice_for_review()
        return {
            PROOFREADING.provider_key: provider,
            PROOFREADING.model_key: model,
            CONTENT_REVIEW.provider_key: review_provider,
            CONTENT_REVIEW.model_key: review_model,
        }

    def refresh_speed(self) -> None:
        """Redraw the class-set line: a speed test stored a new measurement."""
        self._speed_hint.refresh()

    def speed_settings(self) -> dict[str, object]:
        """Return the settings the speed test measures: this page as it is now.

        The recognition method and model come from the picker rather than from the loaded
        values, so a method chosen a moment ago is what gets measured.
        """
        return {
            **self._context.settings,
            **self._live_settings(),
            "ollama_url": self._ollama_url,
            "ocr_engine": self._ocr_picker.engine(),
            "ocr_model": self._ocr_picker.model(),
        }

    def _on_profile_resolved(self, _detection: object) -> None:
        self._profile_known = True
        self._refresh_context_row()
        self._speed_hint.refresh()

    def _refresh_context_row(self) -> None:
        """Show the context window row only while it changes something.

        With proofreading capped at the base window the setting only sizes a content review
        that runs on its own Ollama model.  The row waits until this computer has been checked
        (its automatic entry names a window that depends on the graphics card); a failed check
        counts as a computer without a card.
        """
        if not self._profile_known:
            self._context_row.setVisible(False)
            return
        detection = self._profile_line.selector.resolved()
        card_gb = detection.card_gb if detection is not None else None
        choice = self._app_service.context_choice(self._live_settings(), card_gb)
        if choice.applies:
            index = self._context_combo.findData("auto")
            self._context_combo.setItemText(index, txt.context_auto_review(choice.auto_review))
        self._context_row.setVisible(choice.applies)

    # ── privacy line ──────────────────────────────────────────────────────────

    def _online_now(self) -> bool:
        """Return whether a step that will run sends text to a service outside the school.

        The application layer answers, from the choices as they are now and the steps the main
        window has switched on (nothing is sent by a proofreading that is off).
        """
        return self._app_service.sends_text_online(
            {**self._context.settings, **self._live_settings(), "ollama_url": self._ollama_url}
        )

    def _refresh_privacy_line(self) -> None:
        """Work out and draw the line about names.

        A *transition* from not online to online decides whether the box will be turned on, not
        the signal that a choice changed: that is also sent while a list arrives.
        """
        online = self._online_now()
        if online and not self._was_online and not self._loading:
            self._turn_on = not self._kept_off
        self._was_online = online
        self._privacy_state = privacy_line_state(
            online=online,
            removal_on=bool(self._context.settings.get("anonymization_enabled", False)),
            available=self._available,
            turn_on=self._turn_on,
        )
        self._draw_privacy_line()

    def _draw_privacy_line(self) -> None:
        state = self._privacy_state
        language = self._context.language or "this language"
        text, color, button = {
            PrivacyLineState.NEEDS_DETECTION: (
                "● " + LINE_NEEDS_DETECTION.format(language=language),
                _ORANGE,
                BUTTON_INSTALL,
            ),
            PrivacyLineState.PROTECTED: ("✓ " + LINE_PROTECTED, _GREEN, ""),
            PrivacyLineState.WILL_TURN_ON: ("✓ " + LINE_WILL_TURN_ON, _GREEN, BUTTON_KEEP_OFF),
            PrivacyLineState.NAMES_SENT: ("● " + LINE_NAMES_SENT, _ORANGE, BUTTON_TURN_ON),
        }.get(state, ("", "", ""))
        self._privacy_label.setText(text)
        self._privacy_label.setStyleSheet(f"color: {color};" if color else "")
        self._privacy_label.setVisible(bool(text))
        self._privacy_button.setText(button)
        self._privacy_button.setVisible(bool(button))

    def _on_privacy_button(self) -> None:
        state = self._privacy_state
        if state is PrivacyLineState.NEEDS_DETECTION:
            self._navigate(SettingsPage.ADDONS, _page.FOCUS_NAME_DETECTION)
            return
        if state is PrivacyLineState.WILL_TURN_ON:
            self._turn_on, self._kept_off = False, True
        elif state is PrivacyLineState.NAMES_SENT:
            self._turn_on, self._kept_off = True, False
        self._refresh_privacy_line()

    def _ask_availability(self) -> None:
        """Ask again whether name detection can run (an install may have finished).

        That may import spaCy, so it is not done on the GUI thread.
        """
        language = self._context.language

        def done(result: object, error: BaseException | None) -> None:
            if error is None and isinstance(result, bool):
                self._available = result
                self._refresh_privacy_line()

        self._availability_task.submit(
            lambda: self._app_service.anonymizer_available(language), done
        )

    # ── internal ──────────────────────────────────────────────────────────────

    def ocr_choice(self) -> tuple[str, str]:
        """Return ``(engine, model)`` of text recognition as shown."""
        return self._ocr_picker.engine(), self._ocr_picker.model()

    def _on_engine_changed(self, engine: str) -> None:
        if not self._loading:
            self._speed_hint.refresh()
            self.speed_choices_changed.emit()
            self.ocr_engine_changed.emit(engine)
