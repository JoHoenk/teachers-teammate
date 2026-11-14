"""The Settings window: one modal window with a page per topic.

Pages are built the first time they are shown (each starts background work: model lists,
hardware detection, installed-package detection) and only built pages contribute to the
result, so a page that was never opened changes nothing.

The window owns the wiring between pages, so the pages never know about each other:

- **Computer profile:** edited on *This computer*, followed read-only by AI models and Add-ons.
- **Local AI server address:** edited on *This computer*, followed by AI models and Add-ons.
- **Packages changed:** a finished install on Add-ons makes every built page look again, and
  is passed on to the main window.
- **API keys:** one :class:`~._credentials.ApiKeyDrafts` for the whole window, shared by every
  page that shows a key.  A key is used right away, stored on OK, and put back out of the
  environment by any other way out of the window.
- **Recognition method → image preparation:** choosing another method on AI models applies
  that method's default image preparation on Scan quality (as the old dialog did), also when
  Scan quality was not opened yet.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QModelIndex, QSettings, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QPainter, QShowEvent
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
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ...config import OcrConfig
from ._addons_page import AddonsPage
from ._ai_models_page import AiModelsPage
from ._ai_setup_page import AiSetupPage
from ._credentials import ApiKeyDrafts, discard_key_drafts, store_key_drafts
from ._general_page import GeneralPage
from ._online_services_page import OnlineServicesPage
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
    SettingsPage.GENERAL: GeneralPage,
    SettingsPage.AI_MODELS: AiModelsPage,
    SettingsPage.AI_SETUP: AiSetupPage,
    SettingsPage.ONLINE_SERVICES: OnlineServicesPage,
    SettingsPage.SCAN_QUALITY: ScanQualityPage,
    SettingsPage.PRIVACY: PrivacyPage,
    SettingsPage.ADDONS: AddonsPage,
}
_ORDER: tuple[SettingsPage, ...] = tuple(SettingsPage)
#: Pages that only hold what the page above them needs, drawn indented under it in the
#: sidebar: *This computer* and *Online services* are no choice of their own, they serve
#: *AI models*.
SUB_PAGES: frozenset[SettingsPage] = frozenset(
    {SettingsPage.AI_SETUP, SettingsPage.ONLINE_SERVICES}
)
#: How far a sub-page is indented in the sidebar (pixels).
SUB_PAGE_INDENT = 18


class _SidebarDelegate(QStyledItemDelegate):
    """Draws the rows of :data:`SUB_PAGES` indented, highlight included."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if 0 <= index.row() < len(_ORDER) and _ORDER[index.row()] in SUB_PAGES:
            option = QStyleOptionViewItem(option)
            option.rect.setLeft(option.rect.left() + SUB_PAGE_INDENT)
        super().paint(painter, option, index)


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
    #: An add-on (the built-in reader, GPU monitoring) was installed from a page.
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
        #: The API keys as edited here: used right away, stored on OK, undone otherwise.
        self._key_drafts = ApiKeyDrafts(context.app_service, parent=self)

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
        self._sidebar.setItemDelegate(_SidebarDelegate(self._sidebar))
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
            key_drafts=self._key_drafts,
        )

    def _wire(self, page: SettingsPageBase) -> None:
        page.navigate_requested.connect(self._on_navigate)
        page.validity_changed.connect(lambda _valid: self._update_ok_button())
        if isinstance(page, AiSetupPage):
            page.hardware_setting_changed.connect(self._on_hardware_setting_changed)
            page.ollama_url_changed.connect(self._on_ollama_url_changed)
            page.addon_installed.connect(self.addon_installed.emit)
            page.set_speed_settings_provider(self._speed_settings)
            page.speed_measured.connect(self._on_speed_measured)
        elif isinstance(page, AddonsPage):
            page.packages_changed.connect(self._on_packages_changed)
        elif isinstance(page, AiModelsPage):
            page.addon_installed.connect(self.addon_installed.emit)
            page.speed_choices_changed.connect(self._on_speed_choices_changed)
        elif isinstance(page, ScanQualityPage):
            page.preview_requested.connect(self._on_preview_requested)
            page.addon_installed.connect(self.addon_installed.emit)

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

    def _speed_settings(self) -> dict[str, Any]:
        """Return the settings the speed test measures: the models as *AI models* shows them.

        Unsaved choices count, so two models can be compared before OK.  While that page was
        never opened, the working settings are the choices.
        """
        models = self._pages.get(SettingsPage.AI_MODELS)
        if isinstance(models, AiModelsPage):
            return models.speed_settings()
        return dict(self._working)

    def _on_speed_choices_changed(self) -> None:
        """The models chosen on *AI models* changed: the speed test measures other ones now."""
        setup = self._pages.get(SettingsPage.AI_SETUP)
        if isinstance(setup, AiSetupPage):
            setup.refresh_speed()

    def _on_speed_measured(self) -> None:
        """A speed test stored a measurement: the line on *AI models* shows it too."""
        models = self._pages.get(SettingsPage.AI_MODELS)
        if isinstance(models, AiModelsPage):
            models.refresh_speed()

    def _on_packages_changed(self) -> None:
        for built in self._pages.values():
            built.on_packages_changed()
        self.packages_changed.emit()

    def _on_preview_requested(self) -> None:
        """Build the whole ``OcrConfig`` -- method and model from AI models -- and pass it on."""
        scan = self._pages.get(SettingsPage.SCAN_QUALITY)
        if not isinstance(scan, ScanQualityPage):
            return
        ai_models = self._pages.get(SettingsPage.AI_MODELS)
        if isinstance(ai_models, AiModelsPage):
            engine, model = ai_models.ocr_choice()
        else:
            saved = OcrConfig.from_settings(self._working)
            engine, model = saved.engine, saved.model
        self.preprocess_preview_requested.emit(scan.ocr_config(engine, model))

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
        """Show the defaults of *page*."""
        self._pages[page].restore_defaults()

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
        # Committing a key rings the pages' key listeners (new model lists, new marks); the
        # window is closing, so they would only start work that is thrown away at once.
        with QSignalBlocker(self._key_drafts):
            for built in self._pages.values():
                built.apply()
        store_key_drafts(self._key_drafts, self)
        super().accept()

    def done(self, result: int) -> None:
        """Refuse while an install runs; otherwise stop every page's background work.

        Any way out but OK puts the API keys typed here back out of the environment.  The pages
        stop first, so a key waiting for its pause cannot be written after it.
        """
        reason = self._close_blocker()
        if reason:
            QMessageBox.information(self, CANNOT_CLOSE_TITLE, reason)
            return
        for built in self._pages.values():
            built.shutdown()
        if result != QDialog.DialogCode.Accepted:
            discard_key_drafts(self._key_drafts, self._context.app_service)
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
        ai_models = self._pages.get(SettingsPage.AI_MODELS)
        return SettingsResult(
            settings=settings,
            anonymizer_config=anonymizer_config,
            display_anonymized=display_anonymized,
            restore_all=self._restore_all,
            turn_on_anonymization=(
                isinstance(ai_models, AiModelsPage) and ai_models.turn_on_anonymization()
            ),
        )
