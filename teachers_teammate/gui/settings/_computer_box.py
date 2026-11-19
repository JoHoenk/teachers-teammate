"""The "This computer" box at the top of *AI models*, and the profile row on its own.

Two facts about this computer decide what *AI models* suggests: how powerful it is (the model
suggestions follow it) and how long a class set takes with the models chosen there.  Both are
edited on *This computer* and nowhere else, so the page shows them as one read-only box with a
single "change…" link.  The Add-ons page shows the profile row alone (:class:`ProfileLine`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...application.speed_estimates import CLASS_SET_FILES, SpeedChoices, SpeedSummary
from ...config import DEFAULTS
from ...domain.model_catalog import HardwareDetection
from ...texts.settings import PAGE_AI_SETUP
from .. import _user_text as txt
from .._hardware_profile_selector import HardwareProfileSelector, _ProfileService

CHANGE_PROFILE_LINK = '<a href="change">change…</a>'
BOX_TITLE = f"<b>{PAGE_AI_SETUP}</b>"
#: Left margin of the two rows, so they read as part of the title above them.
_ROW_INDENT = 12


class ProfileLine(QWidget):
    """A read-only :class:`HardwareProfileSelector` plus a "change…" link."""

    #: "change…" was clicked.
    change_requested = Signal()

    def __init__(
        self,
        *,
        app_service: _ProfileService,
        setting: str = DEFAULTS["hardware_profile"],
        ollama_url: str = DEFAULTS["ollama_url"],
        with_link: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.selector = HardwareProfileSelector(
            app_service=app_service,
            setting=setting,
            ollama_url=ollama_url,
            read_only=True,
        )
        layout.addWidget(self.selector, stretch=1)
        self.change_link: QLabel | None = None
        if with_link:
            self.change_link = QLabel(CHANGE_PROFILE_LINK)
            self.change_link.linkActivated.connect(lambda _link: self.change_requested.emit())
            layout.addWidget(self.change_link)

    def set_setting(self, setting: str) -> None:
        """Follow the computer profile chosen on *This computer*."""
        self.selector.set_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        """Follow the local AI server address chosen on *This computer*."""
        self.selector.set_ollama_url(url)


class _SpeedService(Protocol):
    """What the class-set row asks the application service."""

    def speed_choices(self, settings: Mapping[str, object]) -> SpeedChoices: ...

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cached_only: bool = True,
    ) -> SpeedSummary: ...


class _BoxService(_ProfileService, _SpeedService, Protocol):
    """What the box asks the application service: the profile and the class-set time."""


class SpeedLine(QWidget):
    """One read-only line: how long a class set takes with the models chosen on *AI models*.

    The speed test itself is on *This computer* (:mod:`._speed_panel`): the speed of a computer
    is a fact about the computer.  The page where the models are chosen only needs the answer,
    so it shows the class-set figure from the stored measurements.  It never contacts the server
    and never starts a test.
    """

    def __init__(
        self,
        *,
        app_service: _SpeedService,
        settings_provider: Callable[[], Mapping[str, object]],
        profile_selector: HardwareProfileSelector,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = app_service
        self._settings_provider = settings_provider
        self._selector = profile_selector
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        layout.addWidget(self._label, stretch=1)

    def refresh(self) -> None:
        """Redraw from the stored measurements; hidden while no step uses Ollama.

        Call it once the row sits in its page: showing a widget that has no parent yet would
        open it as a window of its own.
        """
        choices = self._service.speed_choices(self._settings_provider())
        self.setVisible(bool(choices.models()))
        if not choices.models():
            return
        summary = self._service.speed_summary(choices, hardware=self._selector.resolved())
        self._label.setText(
            txt.speed_class_set_time(
                CLASS_SET_FILES, summary.class_set.low_s, summary.class_set.high_s
            )
            if summary.class_set is not None
            else txt.SPEED_CLASS_SET_UNKNOWN
        )


class ComputerBox(QFrame):
    """The framed "This computer" block: the profile row and the class-set row."""

    #: "change…" was clicked: show the page where both are edited.
    change_requested = Signal()

    def __init__(
        self,
        *,
        app_service: _BoxService,
        settings_provider: Callable[[], Mapping[str, object]],
        setting: str = DEFAULTS["hardware_profile"],
        ollama_url: str = DEFAULTS["ollama_url"],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(QLabel(BOX_TITLE), stretch=1)
        self.change_link = QLabel(CHANGE_PROFILE_LINK)
        self.change_link.linkActivated.connect(lambda _link: self.change_requested.emit())
        header.addWidget(self.change_link)
        layout.addLayout(header)

        self.profile_line = ProfileLine(
            app_service=app_service,
            setting=setting,
            ollama_url=ollama_url,
            with_link=False,
        )
        self.speed_line = SpeedLine(
            app_service=app_service,
            settings_provider=settings_provider,
            profile_selector=self.profile_line.selector,
        )
        rows = QVBoxLayout()
        rows.setContentsMargins(_ROW_INDENT, 0, 0, 0)
        rows.setSpacing(2)
        rows.addWidget(self.profile_line)
        rows.addWidget(self.speed_line)
        layout.addLayout(rows)

    @property
    def selector(self) -> HardwareProfileSelector:
        """Return the one resolution of this computer that the page's widgets follow."""
        return self.profile_line.selector

    def set_setting(self, setting: str) -> None:
        """Follow the computer profile chosen on *This computer*."""
        self.profile_line.set_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        """Follow the local AI server address chosen on *This computer*."""
        self.profile_line.set_ollama_url(url)

    def refresh_speed(self) -> None:
        """Redraw the class-set row: the models chosen on this page changed."""
        self.speed_line.refresh()
