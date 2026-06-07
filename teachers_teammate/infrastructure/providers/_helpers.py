"""Shared helpers for provider modules.

Removes the env-key health-check and import-guard boilerplate that every
API-key-based provider would otherwise repeat verbatim.  Provider modules that
talk to a local service (e.g. ``ollama``) do not use these and supply their own
``check_connection``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import os

from ...exceptions import ProviderNotAvailableError


def api_key_check(env_key: str) -> Callable[..., tuple[bool, str]]:
    """Return a ``check_connection``-shaped function that verifies *env_key* is set.

    The returned callable accepts arbitrary keyword arguments (the factory may
    forward ``base_url`` etc.) and returns ``(ok, message)``.
    """

    def check_connection(**_kwargs: object) -> tuple[bool, str]:
        if os.environ.get(env_key, "").strip():
            return True, f"✓ {env_key} is set"
        return False, f"✗ {env_key} is not set"

    return check_connection


@contextmanager
def import_guard(package_name: str) -> Iterator[None]:
    """Convert an ``ImportError`` inside the block into a standard provider error.

    Wrap a provider's lazy LangChain import so a missing optional dependency
    surfaces as a uniform :exc:`ProviderNotAvailableError` with an install hint.
    """
    try:
        yield
    except ImportError as exc:
        raise ProviderNotAvailableError(
            f"{package_name} is not installed. Run: pip install {package_name}"
        ) from exc
