"""Which language the operating system is set to — the base for "Auto (system language)".

Deliberately no Qt: the CLI resolves the same default as the GUI, and a test can call these
helpers without a QApplication.
"""

from __future__ import annotations

import locale
import os
import subprocess
import sys

from ..domain.language import language_from_locale


def system_locale_code() -> str:
    """Return the operating system's locale code, e.g. ``"de_DE"`` (``""`` when unknown).

    :func:`locale.getlocale` answers on most systems but is empty when the process was
    started with ``LANG`` unset; ``LC_ALL`` and ``LANG`` are the variables the C library
    itself falls back to, so they are read afterwards, and macOS is asked last.  On Windows
    the code is an English name such as ``"Dutch_Netherlands"``, which
    :func:`~teachers_teammate.domain.language.language_from_locale` understands.
    """
    try:
        code, _encoding = locale.getlocale()
    except (ValueError, TypeError):  # a broken locale setting must not stop a run
        code = None
    if code:
        return code
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return _macos_locale() if sys.platform == "darwin" else ""


def _macos_locale() -> str:
    """Return macOS's own language setting, e.g. ``"de_DE"`` (``""`` when it is not readable).

    An app started from the Finder has no ``LANG``; the system setting is the only source.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/defaults", "read", "-g", "AppleLocale"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def system_language() -> str | None:
    """Return the language name of the system locale, or ``None`` when it is not supported."""
    return language_from_locale(system_locale_code())
