"""The API keys the app uses, out of the environment.

A key loaded from the teacher's secret store, or typed into Settings, stays in this object.
``os.environ`` is only read: a key exported before the app started is found just the same, and
the window says where it came from.  Nothing a child process inherits sees a key the teacher
gave this app, and neither does a library that reads the environment on its own.
"""

from __future__ import annotations

import os

from ..infrastructure.llm_factory import get_provider_info


class ApiKeys:
    """The API keys of the online AI services: what the app holds and what the environment has."""

    def __init__(self) -> None:
        #: The keys the app holds, by environment-variable name.  A name that is here wins over
        #: the environment, so a key typed into Settings takes effect for the rest of the session.
        self._held: dict[str, str] = {}

    def key_for(self, provider: str) -> str | None:
        """Return the key of *provider*, or ``None`` when the app has none for it."""
        info = get_provider_info(provider)
        env_key = info.env_key if info is not None else ""
        return (self.key(env_key) or None) if env_key else None

    def key(self, env_key: str) -> str:
        """Return the key for *env_key*: the one the app holds, else the environment's."""
        if env_key in self._held:
            return self._held[env_key]
        return os.environ.get(env_key, "").strip()

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
