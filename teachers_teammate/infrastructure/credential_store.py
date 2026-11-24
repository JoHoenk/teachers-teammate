"""Where the API keys of the online AI services are kept.

A key goes to the system's own secret store when there is one -- the macOS Keychain, the
Windows Credential Manager, or the Secret Service on Linux (GNOME Keyring, KWallet,
KeePassXC) -- through the ``keyring`` package.  Where there is none (a Linux session without
a desktop keyring, a minimal install, ``keyring`` missing), the key stays in the app's
settings file (``QSettings``, plain text) as before; :meth:`CredentialStore.location` says
which of the two is in use, so the Settings window never claims more than is true.  That file is
the one the GUI itself keeps its state in: both read the location from
:func:`~..config.gui_store_location`.

A key found in the settings file is moved into the secret store the first time the store is
available (:meth:`CredentialStore.migrate`), and the plain copy is deleted.  The keys reach
:class:`~..application.api_keys.ApiKeys`, never the environment: a key set in the environment
before the app started always wins, and the app writes none.

Under ``TEACHERS_TEAMMATE_TMPDIR`` (a test, the smoke test, the app walkthrough) there is no
secret store: the folder holds everything of the run, and the teacher's own keys stay out of it.
"""

from __future__ import annotations

import logging
from types import ModuleType
from typing import TYPE_CHECKING

from ..config import gui_store_location, override_root
from ..domain.credentials import KeyLocation

if TYPE_CHECKING:
    from PySide6.QtCore import QSettings

LOG = logging.getLogger(__name__)

#: The service name the keys are filed under in the secret store.
KEYRING_SERVICE = "TeachersTeammate"

_FILE_GROUP = "credentials"


def app_settings() -> QSettings:
    """Return the app's settings file, where a key without a keychain is kept in plain text.

    ``PySide6.QtCore`` is imported on first use only: this module is reachable from the command
    line, which asks the store for a key without ever building a window.
    """
    from PySide6.QtCore import QSettings  # noqa: PLC0415

    location = gui_store_location()
    if location.ini_file is not None:
        return QSettings(str(location.ini_file), QSettings.Format.IniFormat)
    return QSettings(location.organization, location.application)


def _keyring() -> ModuleType | None:
    """Return the ``keyring`` module if it is installed and has a usable backend, else ``None``.

    ``keyring`` picks its backend from what the system offers; with none it falls back to a
    "fail" backend whose priority is 0, which must not count as a store.  An override folder
    means no store at all.
    """
    if override_root() is not None:
        return None
    try:
        import keyring  # noqa: PLC0415
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
        return keyring if getattr(backend, "priority", 0) > 0 else None
    except Exception:  # a broken backend is "no keychain", whatever it raised
        LOG.debug("keyring backend could not be loaded", exc_info=True)
        return None


class CredentialStore:
    """Read, write and remove API keys; the secret store first, the settings file as fallback."""

    # ── where ─────────────────────────────────────────────────────────────────

    @staticmethod
    def location() -> KeyLocation:
        """Return where a key saved now would be kept."""
        return KeyLocation.KEYCHAIN if _keyring() is not None else KeyLocation.SETTINGS_FILE

    # ── single keys ───────────────────────────────────────────────────────────

    def get(self, env_key: str) -> str:
        """Return the stored key for *env_key* (``""`` when there is none).

        A copy in the settings file wins over the keychain: it only exists because a keychain
        write failed, so it is the newer key.
        """
        plain = self._file_value(env_key)
        if plain:
            return plain
        keyring = _keyring()
        if keyring is not None:
            try:
                value = keyring.get_password(KEYRING_SERVICE, env_key)
            except Exception:  # locked or cancelled keyring: use the file copy
                LOG.debug("keyring read failed for %s", env_key, exc_info=True)
                value = None
            if value:
                return str(value)
        return ""

    def set(self, env_key: str, value: str) -> KeyLocation:
        """Store *value* for *env_key* and return where it ended up.

        If the secret store refuses (locked, prompt cancelled), the key is kept in the
        settings file instead, so typing it never loses it.
        """
        keyring = _keyring()
        if keyring is not None:
            try:
                keyring.set_password(KEYRING_SERVICE, env_key, value)
            except Exception:  # noqa: BLE001
                LOG.warning("keyring write failed for %s; keeping it in the settings file", env_key)
                # An older key still in the keychain would otherwise be read first and
                # shadow the new one.  If that delete fails too, ``get`` and :meth:`migrate`
                # still prefer the file copy (see :meth:`get`).
                self._delete_keyring_value(keyring, env_key)
            else:
                self._remove_file_value(env_key)
                return KeyLocation.KEYCHAIN
        app_settings().setValue(f"{_FILE_GROUP}/{env_key}", value)
        return KeyLocation.SETTINGS_FILE

    def remove(self, env_key: str) -> None:
        """Forget the key for *env_key* wherever it is kept."""
        keyring = _keyring()
        if keyring is not None:
            self._delete_keyring_value(keyring, env_key)
        self._remove_file_value(env_key)

    @staticmethod
    def _delete_keyring_value(keyring: ModuleType, env_key: str) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, env_key)
        except Exception:  # nothing stored (PasswordDeleteError) or locked
            LOG.debug("keyring delete failed for %s", env_key, exc_info=True)

    # ── an old plain-text key ─────────────────────────────────────────────────

    def migrate(self, env_key: str) -> None:
        """Move a key still kept in the settings file into the secret store, when there is one.

        Overwrites: the plain copy is the newer key (see :meth:`get`).  A move that fails leaves
        the plain copy where it is, and it is tried again at the next start.
        """
        keyring = _keyring()
        plain = self._file_value(env_key)
        if keyring is None or not plain:
            return
        try:
            keyring.set_password(KEYRING_SERVICE, env_key, plain)
        except Exception:  # keep the plain copy; the next start tries again
            LOG.debug("moving %s into the keyring failed", env_key, exc_info=True)
            return
        self._remove_file_value(env_key)

    # ── the settings file ─────────────────────────────────────────────────────

    @staticmethod
    def _file_value(env_key: str) -> str:
        value = app_settings().value(f"{_FILE_GROUP}/{env_key}", "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _remove_file_value(env_key: str) -> None:
        app_settings().remove(f"{_FILE_GROUP}/{env_key}")
