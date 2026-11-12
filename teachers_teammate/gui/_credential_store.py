"""Where the API keys of the online AI services are kept.

A key goes to the system's own secret store when there is one -- the macOS Keychain, the
Windows Credential Manager, or the Secret Service on Linux (GNOME Keyring, KWallet,
KeePassXC) -- through the ``keyring`` package.  Where there is none (a Linux session without
a desktop keyring, a minimal install, ``keyring`` missing), the key stays in the app's
settings file (``QSettings``, plain text) as before; :meth:`CredentialStore.location` says
which of the two is in use, so the Settings window never claims more than is true.

A key found in the settings file is moved into the secret store the first time the store is
available (:meth:`CredentialStore.load_into_environment`), and the plain copy is deleted.
A key set in the environment before the app started always wins and is never written.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
import logging
import os
from types import ModuleType

from PySide6.QtCore import QSettings

LOG = logging.getLogger(__name__)

#: The service name the keys are filed under in the secret store.
KEYRING_SERVICE = "TeachersTeammate"

_QSETTINGS_ORG = "TeachersTeammate"
_QSETTINGS_APP = "TeachersTeammate"
_FILE_GROUP = "credentials"

#: The keys that were already in the environment when :meth:`CredentialStore.load_into_environment`
#: ran at start-up, i.e. set outside the app.  Only that call can tell them from keys the app put
#: there itself, and the environment is process-wide, so the answer is kept here.  Asking the
#: keychain instead could pop up an unlock prompt.
_SET_OUTSIDE_APP: set[str] = set()
#: The keys ``load_into_environment`` put into the environment itself.
_LOADED_BY_APP: set[str] = set()


def set_outside_app(env_key: str) -> bool:
    """Return whether *env_key* was set in the environment before the app started."""
    return env_key in _SET_OUTSIDE_APP


class KeyLocation(StrEnum):
    """Where new keys are kept."""

    KEYCHAIN = "keychain"
    SETTINGS_FILE = "settings_file"


def _keyring() -> ModuleType | None:
    """Return the ``keyring`` module if it is installed and has a usable backend, else ``None``.

    ``keyring`` picks its backend from what the system offers; with none it falls back to a
    "fail" backend whose priority is 0, which must not count as a store.
    """
    try:
        import keyring  # noqa: PLC0415
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
        return keyring if getattr(backend, "priority", 0) > 0 else None
    except Exception:  # noqa: BLE001 -- a broken backend is "no keychain", whatever it raised
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
            except Exception:  # noqa: BLE001 -- locked or cancelled keyring: use the file copy
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
                # shadow the new one.  If that delete fails too, ``get`` and ``_migrate``
                # still prefer the file copy (see :meth:`get`).
                self._delete_keyring_value(keyring, env_key)
            else:
                self._remove_file_value(env_key)
                return KeyLocation.KEYCHAIN
        QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).setValue(f"{_FILE_GROUP}/{env_key}", value)
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
        except Exception:  # noqa: BLE001 -- nothing stored (PasswordDeleteError) or locked
            LOG.debug("keyring delete failed for %s", env_key, exc_info=True)

    # ── all keys ──────────────────────────────────────────────────────────────

    def load_into_environment(self, env_keys: Iterable[str]) -> None:
        """Put the stored keys into ``os.environ`` (a key already set there wins).

        Moves keys still kept in the settings file into the secret store when there is one.
        *env_keys* are the names the providers use; a secret store cannot be listed.
        """
        for env_key in env_keys:
            self._migrate(env_key)
            if os.environ.get(env_key):
                if env_key not in _LOADED_BY_APP:
                    _SET_OUTSIDE_APP.add(env_key)
                continue
            value = self.get(env_key)
            if value:
                os.environ[env_key] = value
                _LOADED_BY_APP.add(env_key)

    def _migrate(self, env_key: str) -> None:
        keyring = _keyring()
        plain = self._file_value(env_key)
        if keyring is None or not plain:
            return
        try:
            # Overwrites: the plain copy is the newer key (see ``get``).
            keyring.set_password(KEYRING_SERVICE, env_key, plain)
        except Exception:  # noqa: BLE001 -- leave the plain copy; it is tried again next start
            LOG.debug("moving %s into the keyring failed", env_key, exc_info=True)
            return
        self._remove_file_value(env_key)

    # ── the settings file ─────────────────────────────────────────────────────

    @staticmethod
    def _file_value(env_key: str) -> str:
        value = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(f"{_FILE_GROUP}/{env_key}", "")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _remove_file_value(env_key: str) -> None:
        QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).remove(f"{_FILE_GROUP}/{env_key}")
