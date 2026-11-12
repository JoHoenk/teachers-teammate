"""API keys for the online AI services: one draft per key, shown in two places.

Keys are not part of the pipeline settings.  :class:`~.._credential_store.CredentialStore`
keeps them in the system's secret store (Keychain, Credential Manager, Secret Service) when
there is one, otherwise in the app's settings file in plain text, and the note on the group
says which.  Saved keys are put into the process environment at start-up, where a key set
before the app started takes priority (see ``MainWindow._load_credentials_from_settings``).

While the Settings window is open, a key is edited as an :class:`ApiKeyDrafts` entry that every
page shares.  A key goes into the environment as soon as it is committed -- the providers read
it from there when they list their models, so the list can load and check it before OK -- into
the store only on OK, and every other way out of the window puts the environment back.
"""

from __future__ import annotations

import html
import os
from typing import Protocol
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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

from .. import _user_text as txt
from .._credential_store import CredentialStore, KeyLocation, set_outside_app

SHOW_KEY = "Show"
HIDE_KEY = "Hide"
KEY_PLACEHOLDER = "Paste your API key here"
REMOVE_KEY = "Remove"
ADD_KEY = "Add key"
GROUP_TITLE = "Access keys (API keys)"
#: Opens both notes: the key list shows the keys that are also entered under the service.
_GROUP_NOTE_SAME_KEYS = (
    "A key is what an online service gives you to use its AI. You can also enter it under "
    f"the service on {txt.PAGE_AI_MODELS}; here you see all of them, can remove one, or add "
    "one for another service. "
)
GROUP_NOTE_KEYCHAIN = (
    _GROUP_NOTE_SAME_KEYS + "Keys are kept in your system's secret store (keychain) and loaded "
    "automatically when the app starts. A key set in the environment before launching takes "
    "priority. A key you enter is used right away and saved when you press OK."
)
GROUP_NOTE_FILE = (
    _GROUP_NOTE_SAME_KEYS + "No system keychain was found, so keys are kept in this app's "
    "settings file on this computer, readable by anyone who can open your user account's files "
    "(they are not encrypted), and loaded automatically when the app starts. A key set in the "
    "environment before launching takes priority. A key you enter is used right away and saved "
    "when you press OK."
)
KEY_LINE_OUTSIDE = (
    "Set outside the app (environment variable). It is used instead of a key saved here."
)
KEY_LINE_KEYCHAIN = "Kept in your system's keychain."
KEY_LINE_FILE = "Kept in this app's settings file, not encrypted (no keychain found)."
KEY_LINE_GET_KEY = "Get a key at {link}."
FILE_FALLBACK_TITLE = "Key not saved in the keychain"
FILE_FALLBACK_TEXT = (
    "The system keychain did not accept the key for {services} (it may be locked). It was "
    "kept in this app's settings file instead, in plain text."
)

#: How long after the last keystroke a typed key counts as entered.
COMMIT_DELAY_MS = 800


class _KeyService(Protocol):
    def list_providers(self) -> list[str]: ...

    def get_provider_info(self, provider: str) -> dict: ...


class ApiKeyDrafts(QObject):
    """The API keys as edited in the open Settings window, shared by every page.

    A key goes into ``os.environ`` as soon as it is committed (the providers read it from
    there when they list their models), into the store only on OK (:meth:`apply`), and every
    other way out of the window puts the environment back (:meth:`discard`).
    """

    #: The key of this environment variable changed (only on a real change).
    changed = Signal(str)

    def __init__(
        self,
        app_service: _KeyService,
        *,
        store: CredentialStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_service = app_service
        self._store = store or CredentialStore()
        #: What the key was when the window first touched it: the value that was shown, and
        #: what ``os.environ`` held (``None`` = not set), which :meth:`discard` puts back.
        self._initial: dict[str, str] = {}
        self._env_before: dict[str, str | None] = {}
        #: Keys read from the store because the environment had none (one read per key).
        self._stored: dict[str, str] = {}
        self._location: KeyLocation | None = None

    # ── what the services need ────────────────────────────────────────────────

    def titles(self) -> dict[str, str]:
        """Return ``{env_key: title}``: providers that share one key (Mistral, Mistral EU) share a title."""
        names: dict[str, list[str]] = {}
        for provider in self._app_service.list_providers():
            info = self._app_service.get_provider_info(provider)
            env_key: str = info.get("env_key", "")
            if env_key:
                names.setdefault(env_key, []).append(str(info.get("display_name", provider)))
        return {env_key: " · ".join(titles) for env_key, titles in names.items()}

    def env_key_of(self, provider: str) -> str:
        """Return the environment variable *provider* reads its key from (``""`` = needs none)."""
        info = self._app_service.get_provider_info(provider)
        if not info.get("needs_api_key"):
            return ""
        return str(info.get("env_key", ""))

    def providers_of(self, env_key: str) -> list[str]:
        """Return the providers that read their key from *env_key* (Mistral and Mistral EU share)."""
        return [
            provider
            for provider in self._app_service.list_providers()
            if self._app_service.get_provider_info(provider).get("env_key") == env_key
        ]

    def key_url_of(self, env_key: str) -> str:
        """Return the page where a key for *env_key* is created (``""`` when no provider says)."""
        for provider in self._app_service.list_providers():
            info = self._app_service.get_provider_info(provider)
            if info.get("env_key") == env_key and info.get("key_url"):
                return str(info["key_url"])
        return ""

    # ── values ────────────────────────────────────────────────────────────────

    def value(self, env_key: str) -> str:
        """Return the key as it is now: the environment, else the store, else ``""``.

        The store is read only for a key the environment has none of, and only once: a
        keychain read can ask the user to unlock it.  A key this window touched is whatever
        the environment says.
        """
        if not env_key:
            return ""
        current = os.environ.get(env_key, "")
        if current or env_key in self._env_before:
            return current
        if env_key not in self._stored:
            self._stored[env_key] = self._store.get(env_key)
        return self._stored[env_key]

    def set_outside_app(self, env_key: str) -> bool:
        """Return whether the key was already in the environment when the app started."""
        return set_outside_app(env_key)

    def location(self) -> KeyLocation:
        """Return where a saved key would be kept (asked once per window)."""
        if self._location is None:
            self._location = self._store.location()
        return self._location

    def set(self, env_key: str, value: str) -> None:
        """Use *value* for *env_key* from now on; an empty value removes the key.

        Setting the value the key already has does nothing: no snapshot, no signal, so the
        two views of a key cannot echo each other.  Both sides are compared stripped, so a key
        that only came from the environment is not "changed" by the whitespace around it.  A
        key that only the store has is not in the environment yet, so setting it is a change
        (it goes into the environment) -- though not one :meth:`apply` stores again.
        """
        value = value.strip()
        shown = self.value(env_key)
        in_environment = bool(os.environ.get(env_key))
        if value == shown.strip() and (in_environment or not value):
            return
        self._snapshot(env_key)
        if value:
            os.environ[env_key] = value
        else:
            os.environ.pop(env_key, None)
        self.changed.emit(env_key)

    def remove(self, env_key: str) -> None:
        """Forget the key (also one that only the environment had, for the rest of the session)."""
        self.set(env_key, "")

    def _snapshot(self, env_key: str) -> None:
        if env_key in self._initial:
            return
        self._initial[env_key] = self.value(env_key).strip()
        self._env_before[env_key] = os.environ.get(env_key)

    # ── leaving the window ────────────────────────────────────────────────────

    def apply(self) -> list[str]:
        """Save the keys the window changed (OK).

        A key nobody touched is left alone: one that only came from the environment must not
        be stored just because OK was pressed.  Returns the names of the keys that could not
        go into the keychain although it is in use (locked, prompt cancelled) and were kept in
        the settings file instead, so the caller can say so.
        """
        in_file: list[str] = []
        for env_key, initial in self._initial.items():
            final = os.environ.get(env_key, "")
            if final == initial:
                continue
            if final:
                location = self._store.set(env_key, final)
                if (
                    location is KeyLocation.SETTINGS_FILE
                    and self.location() is KeyLocation.KEYCHAIN
                ):
                    in_file.append(env_key)
            else:
                self._store.remove(env_key)
        self._initial.clear()
        self._env_before.clear()
        self._stored.clear()
        return in_file

    def discard(self) -> list[str]:
        """Put ``os.environ`` back the way it was for every key the window changed.

        Emits nothing: the window is closing.  Returns the keys that were put back, so the
        caller can drop what was learned with the discarded keys (cached model lists).
        """
        restored = list(self._env_before)
        for env_key, before in self._env_before.items():
            if before is None:
                os.environ.pop(env_key, None)
            else:
                os.environ[env_key] = before
        self._initial.clear()
        self._env_before.clear()
        return restored


class ApiKeyRow(QWidget):
    """One API key: the service it belongs to, a field wide enough to paste into, Show/Remove.

    The name sits above the field instead of in a column beside it, so no column can be
    squeezed to a sliver and cut the name or the buttons.  What is typed is committed to the
    shared :class:`ApiKeyDrafts` when the field loses focus and 800 ms after the last
    keystroke, so a pasted key is checked without the teacher having to leave the field.

    An *inline* row (under the AI service) adds a grey line saying where the key is kept.
    """

    remove_requested = Signal(object)

    def __init__(
        self,
        drafts: ApiKeyDrafts,
        env_key: str,
        title: str,
        *,
        inline: bool = False,
        key_url: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._drafts = drafts
        self.env_key = env_key
        self._inline = inline
        self._key_url = key_url
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setHorizontalSpacing(6)
        layout.setColumnStretch(0, 1)

        self._name = QLabel()
        self._name.setTextFormat(Qt.TextFormat.RichText)
        self._name.setWordWrap(True)
        layout.addWidget(self._name, 0, 0)

        self.field = QLineEdit(drafts.value(env_key))
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setPlaceholderText(KEY_PLACEHOLDER)
        self.field.setMinimumWidth(220)
        layout.addWidget(self.field, 1, 0)

        self._reveal = QPushButton(SHOW_KEY)
        self._reveal.setCheckable(True)
        self._reveal.toggled.connect(self._on_reveal_toggled)
        layout.addWidget(self._reveal, 1, 1)

        self.remove_button = QPushButton(REMOVE_KEY)  # shares the column with Show
        self.remove_button.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.remove_button, 0, 1)

        self.line = QLabel()
        self.line.setTextFormat(Qt.TextFormat.RichText)
        self.line.setWordWrap(True)
        self.line.setStyleSheet("color: #888; font-size: 11px;")
        self.line.linkActivated.connect(self._open_key_page)
        self.line.setVisible(inline)
        layout.addWidget(self.line, 2, 0, 1, 2)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COMMIT_DELAY_MS)
        self._timer.timeout.connect(self.commit)
        self.field.textEdited.connect(lambda _text: self._timer.start())
        self.field.editingFinished.connect(self.commit)
        self.field.textChanged.connect(lambda _text: self._update_remove_button())

        self._set_title(title)
        self._update_remove_button()
        self._update_line()

    # ── public API ────────────────────────────────────────────────────────────

    def commit(self) -> None:
        """Hand what the field shows to the drafts (also used by OK, which must not lose it)."""
        self._timer.stop()
        self._drafts.set(self.env_key, self.field.text())

    def stop(self) -> None:
        """Forget a commit that is still waiting (the window is closing without saving)."""
        self._timer.stop()

    def show_value(self, value: str) -> None:
        """Show *value* without committing it (``setText`` emits neither edit signal)."""
        self._timer.stop()
        if self.field.text() != value:
            self.field.setText(value)

    def retarget(self, env_key: str, title: str, key_url: str = "") -> None:
        """Edit the key of another service with this row (a pending edit is committed first)."""
        self.commit()
        self.env_key = env_key
        self._key_url = key_url
        self._set_title(title)
        self.show_value(self._drafts.value(env_key))
        self._update_line()

    def refresh_line(self) -> None:
        """Say again where the key is kept (the answer can change when it is set or removed)."""
        self._update_line()

    # ── internal ──────────────────────────────────────────────────────────────

    def _set_title(self, title: str) -> None:
        self._name.setText(
            f"<b>{html.escape(title)}</b> "
            f"<span style='color:#888;'>({html.escape(self.env_key)})</span>"
        )

    def _update_remove_button(self) -> None:
        self.remove_button.setEnabled(bool(self.field.text().strip()))

    def _update_line(self) -> None:
        if not self._inline:
            return
        if self._drafts.set_outside_app(self.env_key):
            text = KEY_LINE_OUTSIDE
        elif self._drafts.location() is KeyLocation.KEYCHAIN:
            text = KEY_LINE_KEYCHAIN
        else:
            text = KEY_LINE_FILE
        if self._key_url:
            host = urlparse(self._key_url).netloc or self._key_url
            link = f'<a href="{html.escape(self._key_url, quote=True)}">{html.escape(host)}</a>'
            text = f"{html.escape(text)} {KEY_LINE_GET_KEY.format(link=link)}"
        else:
            text = html.escape(text)
        self.line.setText(text)

    def _open_key_page(self, link: str) -> None:
        QDesktopServices.openUrl(QUrl(link))

    def _on_remove_clicked(self) -> None:
        self._timer.stop()
        self.field.clear()
        self.remove_requested.emit(self)

    def _on_reveal_toggled(self, shown: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        self.field.setEchoMode(mode)
        self._reveal.setText(HIDE_KEY if shown else SHOW_KEY)


class CredentialsGroup(QGroupBox):
    """The API keys with a value, one row each, plus a picker to add a key for another service.

    The rows are views of the shared :class:`ApiKeyDrafts`: a key typed under the AI service
    on the AI models page appears here, and a key removed here empties the field there.
    """

    def __init__(
        self,
        app_service: _KeyService,
        parent: QWidget | None = None,
        *,
        drafts: ApiKeyDrafts | None = None,
        store: CredentialStore | None = None,
    ) -> None:
        super().__init__(GROUP_TITLE, parent)
        self._app_service = app_service
        self.drafts = drafts or ApiKeyDrafts(app_service, store=store, parent=self)
        layout = QVBoxLayout(self)

        in_keychain = self.drafts.location() is KeyLocation.KEYCHAIN
        note = QLabel(GROUP_NOTE_KEYCHAIN if in_keychain else GROUP_NOTE_FILE)
        note.setWordWrap(True)
        note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(note)

        self._rows: list[ApiKeyRow] = []
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
        self.drafts.changed.connect(self._on_key_changed)

    # ── public API ────────────────────────────────────────────────────────────

    def rows(self) -> list[ApiKeyRow]:
        """Return the key rows, in the order shown."""
        return list(self._rows)

    def field(self, env_key: str) -> QLineEdit | None:
        """Return the input field of the row for *env_key*, or ``None`` when it has no row."""
        row = self._row_of(env_key)
        return row.field if row is not None else None

    def commit(self) -> None:
        """Hand every field to the drafts and stop the waiting timers (OK pressed)."""
        for row in self._rows:
            row.commit()

    def stop(self) -> None:
        """Forget edits that are still waiting for their pause (the window closes unsaved)."""
        for row in self._rows:
            row.stop()

    # ── rows ──────────────────────────────────────────────────────────────────

    def _row_of(self, env_key: str) -> ApiKeyRow | None:
        return next((row for row in self._rows if row.env_key == env_key), None)

    def _load_rows(self) -> None:
        for env_key in self.drafts.titles():
            if self.drafts.value(env_key):
                self._append_row(env_key)
        self._refresh_add_combo()

    def _append_row(self, env_key: str) -> ApiKeyRow:
        row = ApiKeyRow(self.drafts, env_key, self.drafts.titles().get(env_key, env_key))
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        return row

    def _refresh_add_combo(self) -> None:
        """List the services that have no key row yet; hide the picker when all have one."""
        used = {row.env_key for row in self._rows}
        self._add_combo.clear()
        for env_key, title in self.drafts.titles().items():
            if env_key not in used:
                self._add_combo.addItem(title, userData=env_key)
        self._add_row.setVisible(self._add_combo.count() > 0)

    def _add_new_row(self) -> None:
        env_key = self._add_combo.currentData()
        if not env_key:
            return
        row = self._append_row(str(env_key))
        self._refresh_add_combo()
        row.field.setFocus()

    def _remove_row(self, row: object) -> None:
        if not isinstance(row, ApiKeyRow) or row not in self._rows:
            return
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()
        self._refresh_add_combo()
        self.drafts.remove(row.env_key)

    def _on_key_changed(self, env_key: str) -> None:
        """Follow a key edited elsewhere: add its row, or show the new value."""
        value = self.drafts.value(env_key)
        row = self._row_of(env_key)
        if row is None:
            if value and env_key in self.drafts.titles():
                self._append_row(env_key)
                self._refresh_add_combo()
            return
        # A cleared field keeps its row, so it is not yanked away while a new key is pasted.
        row.show_value(value)
