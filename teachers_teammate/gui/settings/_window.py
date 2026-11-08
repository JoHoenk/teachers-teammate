"""The Settings window: one modal window with a page per topic.

Pages are built the first time they are shown (each starts background work: model lists,
hardware detection, installed-package detection) and only built pages contribute to the
result, so a page that was never opened changes nothing.

The window owns the wiring between pages, so the pages never know about each other:

- **Computer profile:** edited on *Where the AI runs*, followed read-only by AI models and Add-ons.
- **Local AI server address:** edited on *Where the AI runs*, followed by AI models and Add-ons.
- **Packages changed:** a finished install on Add-ons makes every built page look again, and
  is passed on to the main window.
- **Recognition method → image preparation:** choosing another method on AI models applies
  that method's default image preparation on Scan quality (as the old dialog did), also when
  Scan quality was not opened yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...config import OcrConfig
from ._addons_page import AddonsPage
from ._ai_models_page import AiModelsPage
from ._ai_setup_page import AiSetupPage
from ._page import (
    PAGE_TITLES,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
    SettingsResult,
)
from ._privacy_page import PrivacyPage
from ._scan_quality_page import ScanQualityPage

WINDOW_TITLE = "Settings"
RESTORE_DEFAULTS = "Restore defaults…"
RESTORE_QUESTION_TITLE = "Restore defaults"
RESTORE_QUESTION = "Which settings should go back to their built-in values?"
RESTORE_THIS_PAGE = "This page"
RESTORE_ALL = "All settings"
RESTORE_THIS_PAGE_NOTE = "The values on this page change now and are saved when you press OK."
RESTORE_ADDONS_NOTE = "Add-ons has no settings to restore: downloads and installs are not undone."
RESTORE_ALL_CONFIRM = (
    "This resets every setting, including the choices in the main window and the answers "
    "you asked not to be asked about again. "
    "Downloaded models and saved API keys are kept."
)
RESTORE_ALL_BUTTON = "Reset everything"
ADDONS_NOTE = "Downloads and installs start right away and are not undone by Cancel."
CANNOT_CLOSE_TITLE = "Please wait"

#: QSettings keys (GUI state, not pipeline settings).
GEOMETRY_KEY = "gui/settings_geometry"
LAST_PAGE_KEY = "gui/settings_page"
_QSETTINGS_ORG = "TeachersTeammate"
_QSETTINGS_APP = "TeachersTeammate"

#: Room around and between the topics, and a rounded highlight in the system's own colours.
SIDEBAR_STYLE = (
    "QListWidget { padding: 8px 6px; outline: 0; }"
    "QListWidget::item { padding: 9px 12px; border-radius: 6px; }"
    "QListWidget::item:selected { background: palette(highlight); "
    "color: palette(highlighted-text); }"
)
#: Space above the first group of a page (a group's title would be cut at the edge), and
#: between the page and the scroll bar.
PAGE_MARGINS = (2, 12, 10, 4)

_PAGE_CLASSES: dict[SettingsPage, type[SettingsPageBase]] = {
    SettingsPage.AI_MODELS: AiModelsPage,
    SettingsPage.AI_SETUP: AiSetupPage,
    SettingsPage.SCAN_QUALITY: ScanQualityPage,
    SettingsPage.PRIVACY: PrivacyPage,
    SettingsPage.ADDONS: AddonsPage,
}
_ORDER: tuple[SettingsPage, ...] = tuple(SettingsPage)


def last_page() -> SettingsPage:
    """Return the page the Settings window showed last (AI models the first time)."""
    stored = str(QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(LAST_PAGE_KEY, "") or "")
    try:
        return SettingsPage(stored)
    except ValueError:
        return SettingsPage.AI_MODELS


class SettingsWindow(QDialog):
    """Sidebar of topics, one lazily built page each, Restore defaults / OK / Cancel."""

    #: ``QDialog.exec()`` result when "Restore defaults → All settings" was confirmed.
    RESTORE_ALL_RESULT = 2

    #: The full ``OcrConfig`` to preview the image preparation with.
    preprocess_preview_requested = Signal(object)
    #: An install round on Add-ons finished (the main window refreshes its checks).
    packages_changed = Signal()
    #: An add-on (PaddleOCR, GPU monitoring) was installed from a page.
    addon_installed = Signal(str)

    def __init__(
        self,
        context: SettingsContext,
        parent: QWidget | None = None,
        *,
        page: SettingsPage = SettingsPage.AI_MODELS,
        focus: str = "",
        preselect: Sequence[str] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self._context = context
        #: The settings unbuilt pages are built from: the saved ones plus what other pages
        #: changed meanwhile (profile, server address, an engine's image preparation).
        self._working: dict[str, Any] = dict(context.settings)
        #: Values changed on behalf of a page that was not built yet (merged into the result).
        self._carried: dict[str, Any] = {}
        self._pages: dict[SettingsPage, SettingsPageBase] = {}
        self._containers: dict[SettingsPage, QWidget] = {}
        self._container_layouts: dict[SettingsPage, QVBoxLayout] = {}
        self._restore_all = False

        self._build()
        self._restore_geometry()
        self.show_page(page, focus, preselect)

    # ── construction ──────────────────────────────────────────────────────────

    def _build(self) -> None:
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, stretch=1)

        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(190)
        font = self._sidebar.font()
        font.setPointSize(11)
        self._sidebar.setFont(font)
        self._sidebar.setSpacing(2)
        self._sidebar.setStyleSheet(SIDEBAR_STYLE)
        for page in _ORDER:
            self._sidebar.addItem(PAGE_TITLES[page])
        self._sidebar.currentRowChanged.connect(self._on_sidebar_row)
        body.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        for page in _ORDER:
            container = QWidget()
            layout = QVBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            self._containers[page] = container
            self._container_layouts[page] = layout
            self._stack.addWidget(container)
        body.addWidget(self._stack, stretch=1)

        self._addons_note = QLabel(ADDONS_NOTE)
        self._addons_note.setStyleSheet("color: #888; font-size: 11px;")
        self._addons_note.setVisible(False)
        root.addWidget(self._addons_note)

        footer = QHBoxLayout()
        self._restore_btn = QPushButton(RESTORE_DEFAULTS)
        self._restore_btn.setAutoDefault(False)
        self._restore_btn.clicked.connect(self._on_restore_defaults)
        footer.addWidget(self._restore_btn)
        footer.addStretch(1)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        footer.addWidget(self._buttons)
        root.addLayout(footer)

    def _restore_geometry(self) -> None:
        geometry = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(GEOMETRY_KEY)
        if geometry is not None and self.restoreGeometry(geometry):
            return
        width, height = 960, 680
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, available.width() - 40)
            height = min(height, available.height() - 60)
        self.resize(width, height)

    def _wrap(self, page: SettingsPageBase) -> QWidget:
        """Return *page* under its header, in a scroll area without frame or horizontal bar."""
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel(page.title)
        font = title.font()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        description = QLabel(page.description)
        description.setWordWrap(True)
        description.setStyleSheet("color: #888;")
        layout.addWidget(description)
        layout.addSpacing(6)

        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(*PAGE_MARGINS)
        holder_layout.addWidget(page)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        layout.addWidget(scroll, stretch=1)
        return wrapper

    # ── pages ─────────────────────────────────────────────────────────────────

    def page(self, page: SettingsPage) -> SettingsPageBase | None:
        """Return *page* if it was built (``None`` while it was never shown)."""
        return self._pages.get(page)

    def built_pages(self) -> list[SettingsPage]:
        """Return the pages that were built, in sidebar order."""
        return [page for page in _ORDER if page in self._pages]

    def current_page(self) -> SettingsPage:
        """Return the page shown now."""
        return _ORDER[self._stack.currentIndex()]

    def show_page(self, page: SettingsPage, focus: str = "", preselect: Sequence[str] = ()) -> None:
        """Show *page* (building it the first time), focused on *focus*."""
        built = self._ensure_page(page)
        self._sidebar.blockSignals(True)
        self._sidebar.setCurrentRow(_ORDER.index(page))
        self._sidebar.blockSignals(False)
        self._stack.setCurrentWidget(self._containers[page])
        self._update_footer()
        built.on_shown()
        if focus or preselect:
            built.focus(focus, preselect)

    def _on_sidebar_row(self, row: int) -> None:
        if 0 <= row < len(_ORDER):
            self.show_page(_ORDER[row])

    def _ensure_page(self, page: SettingsPage) -> SettingsPageBase:
        existing = self._pages.get(page)
        if existing is not None:
            return existing
        built = _PAGE_CLASSES[page](self._page_context())
        built.load(self._working)
        self._wire(built)
        self._pages[page] = built
        self._container_layouts[page].addWidget(self._wrap(built))
        self._update_ok_button()
        return built

    def _page_context(self) -> SettingsContext:
        """Return the context a page is built from: saved settings plus changes made since."""
        context = self._context
        return SettingsContext(
            settings=dict(self._working),
            app_service=context.app_service,
            language=context.language,
            anonymizer_config=context.anonymizer_config,
            acknowledgements=context.acknowledgements,
            display_anonymized=context.display_anonymized,
            privacy_available=context.privacy_available,
            pull_manager=context.pull_manager,
        )

    def _wire(self, page: SettingsPageBase) -> None:
        page.navigate_requested.connect(self._on_navigate)
        page.validity_changed.connect(lambda _valid: self._update_ok_button())
        if isinstance(page, AiSetupPage):
            page.hardware_setting_changed.connect(self._on_hardware_setting_changed)
            page.ollama_url_changed.connect(self._on_ollama_url_changed)
            page.addon_installed.connect(self.addon_installed.emit)
        elif isinstance(page, AddonsPage):
            page.packages_changed.connect(self._on_packages_changed)
        elif isinstance(page, AiModelsPage):
            page.ocr_engine_changed.connect(self._on_ocr_engine_changed)
            page.addon_installed.connect(self.addon_installed.emit)
        elif isinstance(page, ScanQualityPage):
            page.preview_requested.connect(self._on_preview_requested)

    def _on_navigate(self, page: object, focus: str, preselect: list) -> None:
        if isinstance(page, SettingsPage):
            self.show_page(page, focus, [str(entry) for entry in preselect])

    # ── shared state ──────────────────────────────────────────────────────────

    def _on_hardware_setting_changed(self, setting: str) -> None:
        self._working["hardware_profile"] = setting
        for built in self._pages.values():
            if not isinstance(built, AiSetupPage):
                built.set_hardware_setting(setting)

    def _on_ollama_url_changed(self, url: str) -> None:
        self._working["ollama_url"] = url
        for built in self._pages.values():
            if not isinstance(built, AiSetupPage):
                built.set_ollama_url(url)

    def _on_packages_changed(self) -> None:
        for built in self._pages.values():
            built.on_packages_changed()
        self.packages_changed.emit()

    def _on_ocr_engine_changed(self, engine: str) -> None:
        """Apply the new method's default image preparation, as choosing it always did."""
        scan = self._pages.get(SettingsPage.SCAN_QUALITY)
        if isinstance(scan, ScanQualityPage):
            scan.set_engine_default(engine)
            return
        default = self._context.app_service.default_preprocess_for_engine(engine)
        if default:
            self._working["preprocess_method"] = default
            self._carried["preprocess_method"] = default

    def _on_preview_requested(self) -> None:
        """Build the whole ``OcrConfig`` -- method and model from AI models -- and pass it on."""
        scan = self._pages.get(SettingsPage.SCAN_QUALITY)
        if not isinstance(scan, ScanQualityPage):
            return
        ai_models = self._pages.get(SettingsPage.AI_MODELS)
        if isinstance(ai_models, AiModelsPage):
            engine, model, temperature = ai_models.ocr_choice()
        else:
            saved = OcrConfig.from_settings(self._working)
            engine, model, temperature = saved.engine, saved.model, saved.temperature
        self.preprocess_preview_requested.emit(scan.ocr_config(engine, model, temperature))

    # ── footer ────────────────────────────────────────────────────────────────

    def _update_footer(self) -> None:
        on_addons = self.current_page() is SettingsPage.ADDONS
        self._addons_note.setVisible(on_addons)
        # On Add-ons, Enter in "Add a model by name" must only add the model: neither OK
        # nor Cancel may be the default button there, or Enter would close the window.
        for kind in (QDialogButtonBox.StandardButton.Ok, QDialogButtonBox.StandardButton.Cancel):
            button = self._buttons.button(kind)
            if button is not None:
                button.setAutoDefault(not on_addons)
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setDefault(not on_addons)

    def showEvent(self, event: QShowEvent) -> None:
        """Settle the default button after Qt chose one: it picks OK again when shown."""
        super().showEvent(event)
        self._update_footer()

    def _update_ok_button(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(all(built.is_valid() for built in self._pages.values()))

    def _on_restore_defaults(self) -> None:
        current = self.current_page()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(RESTORE_QUESTION_TITLE)
        box.setText(RESTORE_QUESTION)
        applies_immediately = self._pages[current].applies_immediately
        box.setInformativeText(
            RESTORE_ADDONS_NOTE if applies_immediately else RESTORE_THIS_PAGE_NOTE
        )
        this_page = box.addButton(RESTORE_THIS_PAGE, QMessageBox.ButtonRole.AcceptRole)
        this_page.setEnabled(not applies_immediately)
        everything = box.addButton(RESTORE_ALL, QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is this_page:
            self._restore_page(current)
        elif clicked is everything and self._confirm_restore_all():
            reason = self._close_blocker()
            if reason:
                QMessageBox.information(self, CANNOT_CLOSE_TITLE, reason)
                return
            self._restore_all = True
            self.done(self.RESTORE_ALL_RESULT)

    def _restore_page(self, page: SettingsPage) -> None:
        """Show the defaults of *page*; image preparation follows the recognition method."""
        before = self._current_engine()
        self._pages[page].restore_defaults()
        if page is SettingsPage.SCAN_QUALITY:
            # The built-in preset belongs to the default method; the one in use has its own.
            scan = self._pages[page]
            if isinstance(scan, ScanQualityPage):
                scan.set_engine_default(before)
        elif page is SettingsPage.AI_MODELS and self._current_engine() != before:
            self._on_ocr_engine_changed(self._current_engine())

    def _current_engine(self) -> str:
        """Return the recognition method as shown (saved one while AI models was not opened)."""
        ai_models = self._pages.get(SettingsPage.AI_MODELS)
        if isinstance(ai_models, AiModelsPage):
            return ai_models.ocr_choice()[0]
        return OcrConfig.from_settings(self._working).engine

    def _confirm_restore_all(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(RESTORE_QUESTION_TITLE)
        box.setText(RESTORE_ALL_CONFIRM)
        reset = box.addButton(RESTORE_ALL_BUTTON, QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is reset

    # ── closing ───────────────────────────────────────────────────────────────

    def _close_blocker(self) -> str:
        return next((reason for p in self._pages.values() if (reason := p.close_blocker())), "")

    def accept(self) -> None:
        """Save what pages keep outside the settings (API keys, remembered answers), then close."""
        reason = self._close_blocker()
        if reason:
            QMessageBox.information(self, CANNOT_CLOSE_TITLE, reason)
            return
        if not all(built.is_valid() for built in self._pages.values()):
            return
        for built in self._pages.values():
            built.apply()
        super().accept()

    def done(self, result: int) -> None:
        """Refuse while an install runs; otherwise stop every page's background work."""
        reason = self._close_blocker()
        if reason:
            QMessageBox.information(self, CANNOT_CLOSE_TITLE, reason)
            return
        for built in self._pages.values():
            built.shutdown()
        qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        qs.setValue(GEOMETRY_KEY, self.saveGeometry())
        qs.setValue(LAST_PAGE_KEY, self.current_page().value)
        super().done(result)

    # ── result ────────────────────────────────────────────────────────────────

    def values(self) -> SettingsResult:
        """Return what the pages that were opened show (read after OK)."""
        settings: dict[str, Any] = dict(self._carried)
        for page in _ORDER:
            built = self._pages.get(page)
            if built is not None:
                settings.update(built.values())
        privacy = self._pages.get(SettingsPage.PRIVACY)
        anonymizer_config = None
        display_anonymized = None
        if isinstance(privacy, PrivacyPage):
            anonymizer_config = privacy.anonymizer_config()
            display_anonymized = privacy.display_anonymized()
        return SettingsResult(
            settings=settings,
            anonymizer_config=anonymizer_config,
            display_anonymized=display_anonymized,
            restore_all=self._restore_all,
        )
