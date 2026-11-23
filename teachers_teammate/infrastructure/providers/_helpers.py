"""Shared helpers for provider modules.

Removes the key health-check and import-guard boilerplate that every API-key-based provider
would otherwise repeat verbatim, and holds the two places a provider's key is read: the health
check and the model listing, each taking the key the app hands over and falling back to the
environment.  Provider modules that talk to a local service (e.g. ``ollama``) do not use these
and supply their own ``check_connection``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import os
import sys
import urllib.error

from ...domain.model_listing import ModelListing
from ...exceptions import ProviderNotAvailableError
from ...messages import package_not_installed


def api_key_check(env_key: str) -> Callable[..., tuple[bool, str]]:
    """Return a ``check_connection``-shaped function that reports whether a key is set.

    The key comes in as ``api_key``; ``None`` asks the environment (a key exported before the
    app started), ``""`` means the app has none.  The returned callable accepts the factory's
    other keyword arguments (``base_url`` and the like) and returns ``(ok, message)``.
    """

    def check_connection(*, api_key: str | None = None, **_kwargs: object) -> tuple[bool, str]:
        if key_of(env_key, api_key):
            return True, f"✓ {env_key} is set"
        return False, f"✗ {env_key} is not set"

    return check_connection


def key_of(env_key: str, api_key: str | None) -> str:
    """Return the key to ask a service with: *api_key*, or the environment's when it is ``None``.

    ``""`` is an answer too: the app holds no key, and one exported elsewhere is not used.
    """
    return (os.environ.get(env_key, "") if api_key is None else api_key).strip()


@contextmanager
def import_guard(package_name: str) -> Iterator[None]:
    """Convert an ``ImportError`` inside the block into a standard provider error.

    Wrap a provider's lazy LangChain import so a missing optional dependency
    surfaces as a uniform :exc:`ProviderNotAvailableError` with an install hint.
    """
    try:
        yield
    except ImportError as exc:
        installed_app = bool(getattr(sys, "frozen", False))
        raise ProviderNotAvailableError(
            package_not_installed(package_name, installed_app=installed_app)
        ) from exc


def problem_from_exception(exc: BaseException, service: str) -> str:
    """Return a short plain-language reason for a failed model query.

    SDKs raise their own exception types, so the reason is read from the HTTP status when
    there is one and from the type name otherwise; anything else is reported as unexpected.
    A plain HTTP request (``urllib``) that is refused with 400 is a key the service did not
    accept as well: asked only for its list, that is how Google answers a wrong key.
    """
    if isinstance(exc, urllib.error.HTTPError):  # an OSError too, but its status says why
        if exc.code in (400, 401, 403):
            return f"{service} did not accept the key"
        return f"{service} answered with error {exc.code}"
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__
    if status in (401, 403) or any(
        word in name for word in ("Authentication", "PermissionDenied", "Unauthenticated")
    ):
        return f"{service} did not accept the key"
    if isinstance(exc, OSError) or any(word in name for word in ("Connection", "Timeout")):
        return f"no connection to {service}"
    return f"{service} answered unexpectedly"


def checked_listing(
    fetch: Callable[[str], Sequence[str]],
    fallback: Sequence[str],
    *,
    env_key: str,
    service: str,
    api_key: str | None = None,
) -> ModelListing:
    """Ask *fetch* for the models and say whether the answer is live.

    *fetch* is given the key to ask with (:func:`key_of`).
    No key set, any error, and an empty answer all give the *fallback* list with
    ``source="failed"`` and the reason, so the settings dialog never presents the built-in
    names as if the service had returned them.
    """
    key = key_of(env_key, api_key)
    if not key:
        return ModelListing(list(fallback), "failed", f"no {service} key is set")
    try:
        models = list(fetch(key))
    except Exception as exc:  # noqa: BLE001  # SDKs raise their own auth/network errors; report the reason instead
        return ModelListing(list(fallback), "failed", problem_from_exception(exc, service))
    if not models:
        return ModelListing(list(fallback), "failed", f"{service} returned no chat models")
    return ModelListing(models, "live")
