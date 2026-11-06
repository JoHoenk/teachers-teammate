"""Language names and the mapping from a system locale to one of them.

Pure: no I/O and no Qt, so the CLI and the GUI name languages the same way.  Reading the
operating system's locale happens in
:mod:`~teachers_teammate.infrastructure.system_locale`; this module only turns the code it
returns into a language name.
"""

from __future__ import annotations

#: Languages offered in the document-language picker (a teacher may also type another one).
LANGUAGES: list[str] = [
    "English",
    "German",
    "French",
    "Spanish",
    "Italian",
    "Portuguese",
    "Dutch",
]

#: Locale language subtag (``"de"`` in ``"de_DE"``) to the name used in :data:`LANGUAGES`.
_LANGUAGE_BY_CODE: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
}

#: The same languages by lower-case English name, as Windows writes them in its locale names.
_LANGUAGE_BY_NAME: dict[str, str] = {name.lower(): name for name in _LANGUAGE_BY_CODE.values()}


def language_from_locale(code: str | None) -> str | None:
    """Return the language name for a locale *code*, or ``None`` when it is unknown.

    Accepts what :func:`locale.getlocale` and the ``LANG``/``LC_ALL`` variables use:
    ``"de_DE"``, ``"de_DE.UTF-8"``, ``"de-DE"``, a bare ``"de"`` and the Windows form
    ``"German_Germany"``.  ``"C"``,
    ``"POSIX"``, an empty string and ``None`` all mean "not set" and give ``None``.
    """
    if not code:
        return None
    # Drop the encoding (".UTF-8"), the modifier ("@euro") and any region (after "_" or "-").
    language = code.strip().split(".")[0].split("@")[0].replace("-", "_").split("_")[0]
    language = language.lower()
    # Windows names the locale in English words ("Dutch_Netherlands"), not ISO codes.
    return _LANGUAGE_BY_CODE.get(language) or _LANGUAGE_BY_NAME.get(language)
