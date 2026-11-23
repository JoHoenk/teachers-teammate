"""Small helpers shared by every module that starts a child process."""

from __future__ import annotations

from functools import cache
import os
import sys
from typing import Any

from .llm_factory import get_provider_info, list_providers

_CREATE_NO_WINDOW = 0x08000000


def no_console_window_kwargs() -> dict[str, Any]:
    """Return ``subprocess`` keyword arguments that stop a console window flashing up.

    Windowed Windows builds show a console for every child process otherwise.  Empty
    on other platforms (``creationflags`` exists only on Windows).
    """
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


@cache
def _provider_env_keys() -> frozenset[str]:
    """Return the environment variables the providers read their API keys from."""
    return frozenset(
        info.env_key
        for provider in list_providers()
        if (info := get_provider_info(provider)) is not None and info.env_key
    )


def child_env() -> dict[str, str]:
    """Return this process's environment without the providers' API keys.

    A key exported before the app started is in this environment, where the provider libraries
    read it.  Every child would inherit it — including the detached ``ollama serve`` that
    outlives the run — so a child is given a copy without the keys.  Nothing a child does asks
    a provider on the teacher's account.
    """
    keys = _provider_env_keys()
    return {name: value for name, value in os.environ.items() if name not in keys}
