"""Hide and restore personal information in a text, given a placeholder map.

Pure: no spaCy, no Qt, no I/O.  :func:`restore` is what a run does with the text a
proofreader returns; :func:`mask` is its inverse, used to show the *other* tabs of the preview
(proofread text, changes, review notes) with the same placeholders the extracted text got, so a
teacher reads one consistent set of stand-ins.

The map is the one an anonymizer produces: placeholder → original surface text, for example
``{"[PERSON_1]": "Anna Müller"}``.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
import unicodedata

#: A mapping from a placeholder token to the text it stands for.
PlaceholderMap = Mapping[str, str]


def restore(text: str, mapping: PlaceholderMap) -> str:
    """Put the original values back in place of the placeholders of *text*.

    Every occurrence of a placeholder is replaced, so a name mentioned three times comes
    back three times.  Replacement is literal: nothing in the originals is interpreted.
    """
    result = text
    for placeholder, original in mapping.items():
        if placeholder:
            result = result.replace(placeholder, original)
    return result


def mask(text: str, mapping: PlaceholderMap) -> str:
    """Replace every occurrence of the original values in *text* with their placeholders.

    The inverse of :func:`restore`.  Longer originals win over shorter ones that are part of
    them (``"Anna Müller"`` is replaced as a whole even when ``"Anna"`` is in the map too),
    and an original is only replaced when it stands as a word: ``"Anna"`` does not touch
    ``"Annabel"``.  Values that begin or end with a non-word character (a phone number
    starting with ``+``, a house number ending in ``.``) are still matched as a whole.

    A name the proofreader spelled differently is not found; the caller says so in the help
    text rather than guess.
    """
    pairs = [(placeholder, original) for placeholder, original in mapping.items() if original]
    if not pairs or not text:
        return text

    # Longest original first, so a full name is not cut by its own first name.
    pairs.sort(key=lambda pair: len(pair[1]), reverse=True)
    by_original: dict[str, str] = {}
    for placeholder, original in pairs:
        by_original.setdefault(original, placeholder)

    originals: list[str] = list(by_original)
    originals.sort(key=len, reverse=True)
    pattern = re.compile("|".join(_word_bounded(original) for original in originals))

    def replace(match: re.Match[str]) -> str:
        return by_original.get(match.group(0), match.group(0))

    return pattern.sub(replace, text)


def _word_bounded(value: str) -> str:
    """Return *value* escaped for a regex, refusing to match inside a longer word."""
    escaped = re.escape(value)
    if _starts_with_word(value):
        escaped = r"(?<!\w)" + escaped
    if _ends_with_word(value):
        escaped = escaped + r"(?!\w)"
    return escaped


def _starts_with_word(value: str) -> bool:
    """Whether *value* begins with a character that can be part of a word."""
    return bool(value) and _is_word_char(value[0])


def _ends_with_word(value: str) -> bool:
    """Whether *value* ends with a character that can be part of a word."""
    return bool(value) and _is_word_char(value[-1])


def _is_word_char(char: str) -> bool:
    """Whether *char* is a letter, digit or underscore — what a regex counts as ``\\w``."""
    return char == "_" or unicodedata.category(char)[0] in ("L", "N")
