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
from dataclasses import dataclass, field
import re
import unicodedata

from .offset_map import OffsetMap

#: A mapping from a placeholder token to the text it stands for.
PlaceholderMap = Mapping[str, str]


def restore(text: str, mapping: PlaceholderMap) -> str:
    """Put the original values back in place of the placeholders of *text*.

    Every occurrence of a placeholder is replaced, so a name mentioned three times comes
    back three times.  Replacement is literal and made in one pass: nothing in the originals is
    interpreted, and an original that looks like a placeholder is not replaced again.  This is
    :func:`restore_with_offsets` without the offsets, so a text restored here is the same text
    whose offsets that function carries over.
    """
    return restore_with_offsets(text, mapping)[0]


def restore_with_offsets(text: str, mapping: PlaceholderMap) -> tuple[str, OffsetMap]:
    """Put the original values back, and say where every replaced run ended up.

    The text :func:`restore` returns, in one pass over the placeholders, together with the
    :class:`~.offset_map.OffsetMap` that carries a position of *text* over to the restored one.
    A caller that holds offsets into *text* (a review anchored on the anonymized text) needs
    both: the restored words, and where they now stand.

    The placeholders are bracketed (``[PERSON_1]``), so none contains another and one pass finds
    every occurrence of every one of them.
    """
    pairs = [(placeholder, original) for placeholder, original in mapping.items() if placeholder]
    if not pairs or not text:
        return text, OffsetMap()
    # Longest first, so a placeholder that is a prefix of another cannot cut it in two.
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    by_placeholder = dict(pairs)
    pattern = re.compile("|".join(re.escape(placeholder) for placeholder, _ in pairs))

    runs: list[tuple[int, int, int, int]] = []
    pieces: list[str] = []
    position = 0
    new_position = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        pieces.append(text[position:start])
        new_position += start - position
        replacement = by_placeholder[match.group(0)]
        runs.append((start, end, new_position, new_position + len(replacement)))
        pieces.append(replacement)
        new_position += len(replacement)
        position = end
    pieces.append(text[position:])
    return "".join(pieces), OffsetMap(tuple(runs))


def mask(text: str, mapping: PlaceholderMap) -> str:
    """Replace every occurrence of the original values in *text* with their placeholders.

    The inverse of :func:`restore`.  Longer originals win over shorter ones that are part of
    them (``"Anna Müller"`` is replaced as a whole even when ``"Anna"`` is in the map too),
    and an original is only replaced when it stands as a word: ``"Anna"`` does not touch
    ``"Annabel"``.  Values that begin or end with a non-word character (a phone number
    starting with ``+``, a house number ending in ``.``) are still matched as a whole.

    A name the proofreader spelled differently is not found; the caller says so in the help
    text rather than guess.  This is :func:`mask_with_offsets` without the offsets, so a text
    masked here is the same text whose offsets that function carries over.
    """
    return mask_with_offsets(text, mapping)[0]


def mask_with_offsets(text: str, mapping: PlaceholderMap) -> tuple[str, OffsetMap]:
    """Replace the original values by their placeholders, and say where each one landed.

    The text :func:`mask` returns, in one pass over the originals, together with the
    :class:`~.offset_map.OffsetMap` that carries a position of *text* over to the masked one.
    A caller that holds offsets into *text* (a review anchored on the real text) needs both: the
    masked words, and where they now stand.
    """
    pairs = [(placeholder, original) for placeholder, original in mapping.items() if original]
    if not pairs or not text:
        return text, OffsetMap()

    # Longest original first, so a full name is not cut by its own first name.
    pairs.sort(key=lambda pair: len(pair[1]), reverse=True)
    by_original: dict[str, str] = {}
    for placeholder, original in pairs:
        by_original.setdefault(original, placeholder)

    originals: list[str] = list(by_original)
    originals.sort(key=len, reverse=True)
    pattern = re.compile("|".join(_word_bounded(original) for original in originals))

    runs: list[tuple[int, int, int, int]] = []
    pieces: list[str] = []
    position = 0
    new_position = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        pieces.append(text[position:start])
        new_position += start - position
        replacement = by_original[match.group(0)]
        runs.append((start, end, new_position, new_position + len(replacement)))
        pieces.append(replacement)
        new_position += len(replacement)
        position = end
    pieces.append(text[position:])
    return "".join(pieces), OffsetMap(tuple(runs))


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


# ── Default regex patterns ─────────────────────────────────────────────────

# Street and postcode patterns are German formats. They stay on one line and never span more
# than one capitalised word before/after the match, so they cannot swallow a neighbouring name.
_WORD = r"[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*"
_STREET_SUFFIX = r"(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer)"
_STREET_WORD = r"(?:Straße|Strasse|Str\.|Weg|Platz|Allee|Gasse|Ring|Damm|Ufer)"
# "Der Weg 5 km" is a sentence, not an address: a leading article rules out the two-word form.
_ARTICLE = (
    r"(?:Der|Die|Das|Dem|Den|Des|Ein|Eine|Einem|Einen|Einer|Kein|Keine|Jeder|Jede|"
    r"Diese|Dieser|Mein|Meine|Sein|Seine|Ihr|Ihre|Unser|Unsere)"
)
# One to three digits (so years do not count) and an optional letter: "12", "5a", "12 a".
_HOUSE_NO = r"[ ]?\d{1,3}(?:[ ]?[a-z])?\b"
_MONTHS = (
    r"(?:Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)"
)
_MONTHS_EN = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
)
_ORDINAL_EN = r"(?:st|nd|rd|th)?"
_DAY = r"(?:0?[1-9]|[12]\d|3[01])"
_MONTH = r"(?:0?[1-9]|1[0-2])"
# Capitalised nouns that follow a five-digit number in ordinary prose ("20000 Menschen").
_NOT_CITY = (
    r"(?:Euro|Dollar|Menschen|Einwohner|Einwohnern|Jahre|Jahren|Meter|Metern|"
    r"Kilometer|Kilometern|Tonnen|Soldaten|Mal)\b"
)
# A postcode starts a line, follows a comma, "in ", "D-" or a placeholder (a street just replaced).
_POSTCODE_START = r"(?:(?<![^\n])|(?<=,)|(?<=,[ ])|(?<=\bin[ ])|(?<=\][ ])|(?<=\bD-))"

DEFAULT_PATTERNS: list[tuple[str, str]] = [
    ("IBAN", r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"),
    ("EMAIL", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    # German street + house number: "Hauptstraße 12", "Berliner Straße 5a",
    # "Konrad-Adenauer-Straße 5"
    (
        "STREET",
        r"\b(?:(?:St\.|Sankt)[ ])?(?:"
        + _WORD
        + _STREET_SUFFIX
        + "|(?!"
        + _ARTICLE
        + "[ -])"
        + _WORD
        + "[ -]"
        + _STREET_WORD
        + ")"
        + _HOUSE_NO,
    ),
    # German postcode + one-word city: "10115 Berlin"
    ("POSTCODE_CITY", _POSTCODE_START + r"\d{5}[ ]+(?!" + _NOT_CITY + ")" + _WORD),
    # Dates: "01.02.2010", "1/2/10", "2010-02-01", "1. Februar 2010", "March 5, 2024"
    (
        "DATE",
        r"(?<![\d.])" + _DAY + r"[./][ ]?" + _MONTH + r"[./][ ]?(?:\d{4}|\d{2})(?!\d|\.\d)",
    ),
    ("DATE_ISO", r"(?<!\d)\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)"),
    (
        "DATE_TEXT",
        r"(?:(?<!\d)"
        + _DAY
        + r"\.?[ ]*"
        + _MONTHS
        + "|(?<!\\d)"
        + _DAY
        + _ORDINAL_EN
        + "[ ]+"
        + _MONTHS_EN
        + r"|\b"
        + _MONTHS_EN
        + "[ ]+"
        + _DAY
        + _ORDINAL_EN
        + r",?)[ ]+\d{4}(?!\d)",
    ),
    (
        "PHONE",
        r"(?<!\d)(?:\+?\d{1,3}[\s\-.]?)?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,6}(?!\d)",
    ),
]

# The patterns saved by versions that only knew IBAN, EMAIL and PHONE.  A configuration that still
# holds exactly this list never customised the patterns, so it follows the current defaults.
_LEGACY_DEFAULT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("IBAN", r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"),
    ("EMAIL", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    (
        "PHONE",
        r"(?<!\d)(?:\+?\d{1,3}[\s\-.]?)?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,6}(?!\d)",
    ),
)


def resolve_patterns(
    configured: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None,
) -> tuple[tuple[str, str], ...]:
    """Return the regex patterns to apply for a *configured* pattern list.

    ``None`` and the list of the earlier built-in defaults mean "use the current defaults";
    any other list, including an empty one (no regex anonymization), is kept as it is.
    """
    if configured is None:
        return tuple(DEFAULT_PATTERNS)
    patterns = tuple((tag, pattern) for tag, pattern in configured)
    if patterns == _LEGACY_DEFAULT_PATTERNS:
        return tuple(DEFAULT_PATTERNS)
    return patterns


@dataclass(frozen=True)
class AnonymizerConfig:
    """Configuration for the anonymization passes.

    The patterns live here with it: what counts as personal information is a business rule,
    not a detail of the reader, and the GUI offers the same list the run applies.

    Args:
        secondary_model: spaCy model name for a second NER pass (e.g.
            ``"xx_ent_wiki_sm"`` to catch foreign names in documents whose
            primary language differs from the names' origin).  ``None``
            disables the second NER pass.
        primary_model: Override the primary spaCy model.  ``None`` (default)
            means the model is derived automatically from the document language.
            Set to e.g. ``"xx_ent_wiki_sm"`` to force the multilingual model
            regardless of language.
        patterns: Regex patterns applied after NER, as ``(tag, pattern)``
            pairs.  An empty tuple skips regex anonymization entirely.
    """

    #: SECONDARY_MODEL_AUTO ("the system language's model"), SECONDARY_MODEL_NONE / None
    #: (no extra pass) or a model name.  Left unresolved here: the anonymizer turns it into a
    #: model name or ``None``.
    secondary_model: str | None = None
    primary_model: str | None = None
    patterns: tuple[tuple[str, str], ...] = field(default_factory=lambda: tuple(DEFAULT_PATTERNS))
