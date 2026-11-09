"""API keys for the online AI services: one row per key, saved when the window's OK is pressed.

Keys are not part of the pipeline settings.  :class:`~.._credential_store.CredentialStore`
keeps them in the system's secret store (Keychain, Credential Manager, Secret Service) when
there is one, otherwise in the app's settings file in plain text, and the note on the group
says which.  Saved keys are put into the process environment at start-up, where a key set
before the app started takes priority (see ``MainWindow._load_credentials_from_settings``).
"""

from __future__ import annotations

import html
import os
from typing import Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .._credential_store import CredentialStore, KeyLocation

SHOW_KEY = "Show"
HIDE_KEY = "Hide"
KEY_NOT_SET = "(not set)"
KEY_PLACEHOLDER = "Paste your API key here"
REMOVE_KEY = "Remove"
ADD_KEY = "Add key"
GROUP_TITLE = "API keys for online services"
GROUP_NOTE_KEYCHAIN = (
    "Enter the access key you got from the service. It is kept in your system's secret "
    "store (keychain) and loaded automatically when the app starts. A key set in the "
    "environment before launching takes priority. Keys are saved when you press OK, and the "
    "services appear in the lists on the AI models page after that."
)
GROUP_NOTE_FILE = (
    "Enter the access key you got from the service. No system keychain was found, so it is "
    "kept in this app's settings file on this computer, readable by anyone who can open your "
    "user account's files (it is not encrypted), and loaded automatically when the app "
    "starts. A key set in the environment before launching takes priority. Keys are saved "
    "when you press OK, and the services appear in the lists on the AI models page after that."
)
FILE_FALLBACK_TITLE = "Key not saved in the keychain"
FILE_FALLBACK_TEXT = (
    "The system keychain did not accept the key for {services} (it may be locked). It was "
    "kept in this app's settings file instead, in plain text."
)


class _KeyService(Protocol):
    def list_providers(self) -> list[str]: ...

    def get_provider_info(self, provider: str) -> dict: ...


class CredentialRow(QWidget):
    """One API key: the service it belongs to, a field wide enough to paste into, Show/Remove.

    The name sits above the field instead of in a column beside it, so no column can be
    squeezed to a sliver and cut the name or the buttons.
    """

    remove_requested = Signal(object)

    def __init__(
        self,
        env_key: str,
        title: str,
        value: str = "",
        *,
        placeholder: str = KEY_NOT_SET,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.env_key = env_key
        #: What the field showed when the row was built (a stored key, one from the
        #: environment, or nothing for a row just added).
        self.initial_value = value
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setHorizontalSpacing(6)
        layout.setColumnStretch(0, 1)

        name = QLabel(
            f"<b>{html.escape(title)}</b> <span style='color:#888;'>({html.escape(env_key)})</span>"
        )
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setWordWrap(True)
        layout.addWidget(name, 0, 0)

        self.field = QLineEdit(value)
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText(placeholder)
        self.field.setMinimumWidth(220)
        layout.addWidget(self.field, 1, 0)

        self._reveal = QPushButton(SHOW_KEY)
        self._reveal.setCheckable(True)
        self._reveal.toggled.connect(self._on_reveal_toggled)
        layout.addWidget(self._reveal, 1, 1)

        remove = QPushButton(REMOVE_KEY)  # shares the column with Show, so both are as wide as it
        remove.clicked.connect(lambda _=False: self.remove_requested.emit(self))
        layout.addWidget(remove, 0, 1)

    def is_changed(self) -> bool:
        """Return whether the user typed another key than the row started with."""
        return self.field.text().strip() != self.initial_value.strip()

    def _on_reveal_toggled(self, shown: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        self.field.setEchoMode(mode)
        self._reveal.setText(HIDE_KEY if shown else SHOW_KEY)


class CredentialsGroup(QGroupBox):
    """The saved API keys, one row each, plus a picker to add a key for another service."""

    def __init__(
        self,
        app_service: _KeyService,
        parent: QWidget | None = None,
        *,
        store: CredentialStore | None = None,
    ) -> None:
        super().__init__(GROUP_TITLE, parent)
        self._app_service = app_service
        self._store = store or CredentialStore()
        layout = QVBoxLayout(self)

        self._in_keychain = self._store.location() is KeyLocation.KEYCHAIN
        note = QLabel(GROUP_NOTE_KEYCHAIN if self._in_keychain else GROUP_NOTE_FILE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)

        self._rows: list[CredentialRow] = []
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(2)
        layout.addLayout(self._rows_layout)

        self._add_row = QWidget()
        add_layout = QHBoxLayout(self._add_row)
        add_layout.setContentsMargins(0, 0, 0, 0)
        self._add_combo = QComboBox()
        add_layout.addWidget(self._add_combo, stretch=1)
        self._add_btn = QPushButton(ADD_KEY)
        self._add_btn.clicked.connect(self._add_new_row)
        add_layout.addWidget(self._add_btn)
        layout.addWidget(self._add_row)

        self._load_rows()

    # ── public API ────────────────────────────────────────────────────────────

    def rows(self) -> list[CredentialRow]:
        """Return the key rows, in the order shown."""
        return list(self._rows)

    def field(self, env_key: str) -> QLineEdit | None:
        """Return the input field of the row for *env_key*, or ``None`` when it has no row."""
        for row in self._rows:
            if row.env_key == env_key:
                return row.field
        return None

    def apply(self) -> list[str]:
        """Save the keys the user typed; removed rows are forgotten.

        A row nobody touched is left alone: a key that only came from the environment
        (set for this session) must not be stored just because OK was pressed.

        Returns the names of the keys that could not go into the keychain although it is in
        use (locked, prompt cancelled) and were kept in the settings file instead, so the
        caller can say so.
        """
        in_file: list[str] = []
        kept: set[str] = set()
        for row in self._rows:
            value = row.field.text().strip()
            if not value:
                continue
            kept.add(row.env_key)
            if row.is_changed():
                location = self._store.set(row.env_key, value)
                if location is KeyLocation.SETTINGS_FILE and self._in_keychain:
                    in_file.append(row.env_key)
                os.environ[row.env_key] = value
        for env_key in self.key_titles():
            if env_key not in kept and self._store.get(env_key):
                self._store.remove(env_key)
                os.environ.pop(env_key, None)
        return in_file

    # ── rows ──────────────────────────────────────────────────────────────────

    def key_titles(self) -> dict[str, str]:
        """Return ``{env_key: title}``: providers that share one key (Mistral, Mistral EU) share a row."""
        names: dict[str, list[str]] = {}
        for provider in self._app_service.list_providers():
            info = self._app_service.get_provider_info(provider)
            env_key: str = info.get("env_key", "")
            if env_key:
                names.setdefault(env_key, []).append(str(info.get("display_name", provider)))
        return {env_key: " · ".join(titles) for env_key, titles in names.items()}

    def _load_rows(self) -> None:
        for env_key in self.key_titles():
            value = os.environ.get(env_key) or self._store.get(env_key)
            if value:
                self._append_row(env_key, value)
        self._refresh_add_combo()

    def _used_env_keys(self) -> set[str]:
        return {row.env_key for row in self._rows}

    def _append_row(
        self, env_key: str, value: str = "", *, placeholder: str = KEY_NOT_SET
    ) -> CredentialRow:
        row = CredentialRow(
            env_key,
            self.key_titles().get(env_key, env_key),
            value,
            placeholder=placeholder,
        )
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        return row

    def _refresh_add_combo(self) -> None:
        """List the services that have no key row yet; hide the picker when all have one."""
        used = self._used_env_keys()
        self._add_combo.clear()
        for env_key, title in self.key_titles().items():
            if env_key not in used:
                self._add_combo.addItem(title, userData=env_key)
        self._add_row.setVisible(self._add_combo.count() > 0)

    def _add_new_row(self) -> None:
        env_key = self._add_combo.currentData()
        if not env_key:
            return
        row = self._append_row(str(env_key), placeholder=KEY_PLACEHOLDER)
        self._refresh_add_combo()
        row.field.setFocus()

    def _remove_row(self, row: object) -> None:
        if not isinstance(row, CredentialRow) or row not in self._rows:
            return
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_add_combo()
