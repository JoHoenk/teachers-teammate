"""A stand-in for the app's secret store: the keys a test puts in it, and who asked."""

from __future__ import annotations

from teachers_teammate.domain.credentials import KeyLocation


class FakeSecretStore:
    """An in-memory key store that remembers every name it was asked about."""

    def __init__(self, location: KeyLocation = KeyLocation.SETTINGS_FILE) -> None:
        #: The keys the store holds, by environment-variable name.
        self.keys: dict[str, str] = {}
        #: The names it was asked for, in order: a test reads how often the store was consulted.
        self.asked: list[str] = []
        self._location = location

    def location(self) -> KeyLocation:
        """Return where a key saved now would be kept."""
        return self._location

    def get(self, env_key: str) -> str:
        """Return the stored key for *env_key* (``""`` when there is none)."""
        self.asked.append(env_key)
        return self.keys.get(env_key, "")

    def set(self, env_key: str, value: str) -> KeyLocation:
        """Store *value* for *env_key* and return where it ended up."""
        self.keys[env_key] = value
        return self._location

    def remove(self, env_key: str) -> None:
        """Forget the key for *env_key*."""
        self.keys.pop(env_key, None)

    def migrate(self, env_key: str) -> None:
        """Do nothing: this store keeps no plain-text copy that could be moved."""
