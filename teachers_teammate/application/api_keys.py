"""The API keys the app uses, out of the store and the environment.

A key the teacher saved in Settings is kept by the store (the system's keychain, or the app's
settings file); a key typed into the open Settings window stays in this object.  ``os.environ``
is only read: a key exported before the app started is found just the same, and the window says
where it came from.  Nothing a child process inherits sees a key the teacher gave this app, and
neither does a library that reads the environment on its own.

The store is asked on first use of a key and the answer is remembered, ``"no key"`` included: a
keychain read can ask the teacher to unlock it, so a run that asks nothing online never touches
the keychain.

What a provider declares is not worked out here: which providers there are and what each one's
key is called come in through the constructor.
"""

from __future__ import annotations

from collections.abc import Iterable
import os

from ..domain.credentials import KeyLocation
from .ports import ProviderDeclarations, SecretStore


class ApiKeys:
    """The API keys of the online AI services: what the app holds, the environment, the store."""

    def __init__(self, store: SecretStore, *, providers: ProviderDeclarations) -> None:
        self._store = store
        self._providers = providers
        #: The keys the app holds, by environment-variable name.  A name that is here wins over
        #: the environment, so a key typed into Settings takes effect for the rest of the session.
        self._held: dict[str, str] = {}
        #: What the store answered for a name, ``""`` included, so the keychain is asked once.
        self._stored: dict[str, str] = {}

    def key_for(self, provider: str) -> str | None:
        """Return the key of *provider*, or ``None`` when the app has none for it."""
        info = self._providers.info(provider)
        env_key = info.env_key if info is not None else ""
        return (self.key(env_key) or None) if env_key else None

    def key(self, env_key: str) -> str:
        """Return the key for *env_key*: what the app holds, else the environment's, else the stored."""
        if env_key in self._held:
            return self._held[env_key]
        from_environment = os.environ.get(env_key, "").strip()
        if from_environment:
            return from_environment
        if env_key not in self._stored:
            self._stored[env_key] = self._store.get(env_key).strip()
        return self._stored[env_key]

    def held(self, env_key: str) -> str | None:
        """Return what the app holds for *env_key*, or ``None`` when it holds nothing."""
        return self._held.get(env_key)

    def remember(self, env_key: str, value: str | None) -> None:
        """Hold *value* for *env_key*, or hold nothing again when *value* is ``None``."""
        if value is None:
            self._held.pop(env_key, None)
        else:
            self._held[env_key] = value.strip()

    def set_outside_app(self, env_key: str) -> bool:
        """Return whether *env_key* is set in the environment, i.e. outside the app."""
        return env_key not in self._held and bool(os.environ.get(env_key, "").strip())

    # ── the store ─────────────────────────────────────────────────────────────

    def load_stored(self, env_keys: Iterable[str]) -> None:
        """Hand the stored keys to the app, where the app reads them (a key in the environment wins).

        Moves keys still kept in the settings file into the secret store when there is one.
        *env_keys* are the names the providers use; a secret store cannot be listed.
        """
        for env_key in env_keys:
            self._store.migrate(env_key)
            if self.set_outside_app(env_key):
                continue
            value = self._store.get(env_key).strip()
            self._stored[env_key] = value
            if value:
                self.remember(env_key, value)

    def save(self, env_key: str, value: str) -> KeyLocation:
        """Keep *value* for *env_key* in the store, and return where it ended up."""
        value = value.strip()
        location = self._store.set(env_key, value)
        self._stored[env_key] = value
        return location

    def forget(self, env_key: str) -> None:
        """Forget the key for *env_key* wherever it is kept."""
        self._store.remove(env_key)
        self._stored[env_key] = ""

    def forget_all(self) -> None:
        """Forget every provider's key: what the uninstaller runs before the program files go."""
        for provider in self._providers.names():
            info = self._providers.info(provider)
            if info is not None and info.env_key:
                self.forget(info.env_key)

    def location(self) -> KeyLocation:
        """Return where a key saved now would be kept."""
        return self._store.location()
