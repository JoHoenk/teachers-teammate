"""Hide and restore personal information in a text, given a placeholder map.

Pure: no spaCy, no Qt, no I/O.  :func:`restore` is what a run does with the text a
proofreader returns; :func:`mask` is its inverse, used to show the *other* tabs of the preview
(proofread text, changes, review notes) with the same placeholders the extracted text got, so a
teacher reads one consistent set of stand-ins.

The map is the one an anonymizer produces: placeholder → original surface text, for example
``{"[PERSON_1]": "Anna Müller"}``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
import re
import unicodedata

from .name_models import spacy_model_for
from .offset_map import OffsetMap

#: A mapping from a placeholder token to the text it stands for.
PlaceholderMap = Mapping[str, str]

#: Value of ``anonymizer_secondary_model`` meaning "the model of the system language".
SECONDARY_MODEL_AUTO = "auto:system"
#: Value meaning "no extra detection model" (what an explicit "(none)" in the GUI stores).
SECONDARY_MODEL_NONE = "none"


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


def surface_pattern(values: Iterable[str], *, ignore_case: bool = False) -> re.Pattern[str] | None:
    """Return a regex that finds any of *values* as a whole word, the longest one first.

    ``None`` when there is nothing to look for.  This is what :func:`mask` matches with, and
    what an anonymizer uses to find a further occurrence of a name it has already replaced.
    *ignore_case* is for the lists a teacher writes (:attr:`AnonymizerConfig.hide` and
    :attr:`AnonymizerConfig.keep`), which do not tell ``Anna`` from ``anna``.
    """
    surfaces = sorted({value for value in values if value}, key=len, reverse=True)
    if not surfaces:
        return None
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile("|".join(_word_bounded(surface) for surface in surfaces), flags)


def leftover_placeholders(text: str, mapping: PlaceholderMap) -> tuple[str, ...]:
    """Return the placeholders of *mapping* that still stand in *text*.

    A placeholder that is still there was never restored, and so stands in the text a teacher
    reads; one written without its brackets (``PERSON_1``) is a placeholder the model has
    mangled.  Both are reported, sorted, so a caller can name them in a warning.
    """
    if not mapping or not text:
        return ()
    shapes: dict[str, str] = {}
    for placeholder in mapping:
        if not placeholder:
            continue
        shapes[placeholder] = placeholder
        if placeholder.startswith("[") and placeholder.endswith("]"):
            shapes.setdefault(placeholder[1:-1], placeholder)
    shapes_in_order = sorted(shapes, key=len, reverse=True)
    pattern = re.compile(
        "|".join(
            re.escape(shape) if shape.startswith("[") else _word_bounded(shape)
            for shape in shapes_in_order
        )
    )
    found = {shapes[match.group(0)] for match in pattern.finditer(text)}
    return tuple(sorted(found))


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
# A postcode starts a line, follows a comma, "in ", "D-" or the house number of a street on the
# same line.  A lookbehind has a fixed width, so each street ending ("straße", "weg", ...) and each
# house-number shape ("5", "5a", "5 a", "12", ...) is one alternative; a number after any other
# word ("Aufgabe 3 10115 Berlin") is not a house number.
_STREET_ENDINGS = (
    "[Ss]traße",
    "[Ss]trasse",
    r"[Ss]tr\.",
    "[Ww]eg",
    "[Pp]latz",
    "[Aa]llee",
    "[Gg]asse",
    "[Rr]ing",
    "[Dd]amm",
    "[Uu]fer",
)
_AFTER_HOUSE_NO = "|".join(
    rf"(?<={ending}[ ]{digits}{letter}[ ])"
    for ending in _STREET_ENDINGS
    for digits in (r"\d", r"\d\d", r"\d\d\d")
    for letter in ("", "[a-z]", "[ ][a-z]")
)
# The lookahead keeps the many lookbehinds from being tried where no postcode follows.
_POSTCODE_START = (
    r"(?=\d{5})(?:(?<![^\n])|(?<=,)|(?<=,[ ])|(?<=\bin[ ])|(?<=\bD-)|" + _AFTER_HOUSE_NO + ")"
)

#: What a pattern tag may look like: it becomes part of the placeholder ``[TAG_1]``.
_PATTERN_TAG = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


class PatternProblemKind(StrEnum):
    """Why a ``(tag, pattern)`` pair cannot be an anonymizer pattern."""

    TAG_INVALID = "tag_invalid"
    TAG_TWICE = "tag_twice"
    DOES_NOT_COMPILE = "does_not_compile"


@dataclass(frozen=True)
class PatternProblem:
    """What is wrong with one anonymizer pattern; ``texts`` turns it into a sentence."""

    kind: PatternProblemKind
    tag: str
    detail: str = ""
    """The compiler's reason, for :attr:`PatternProblemKind.DOES_NOT_COMPILE`."""


def pattern_problem(tag: str, pattern: str, taken_tags: Collection[str]) -> PatternProblem | None:
    """Return why ``(tag, pattern)`` cannot be an anonymizer pattern, or ``None`` if it can.

    The tag becomes part of the placeholder ``[TAG_1]``, so it must be a plain word and
    unique among *taken_tags*; the pattern must compile.
    """
    if not _PATTERN_TAG.fullmatch(tag):
        return PatternProblem(PatternProblemKind.TAG_INVALID, tag)
    if tag in taken_tags:
        return PatternProblem(PatternProblemKind.TAG_TWICE, tag)
    try:
        re.compile(pattern)
    except re.error as exc:
        return PatternProblem(PatternProblemKind.DOES_NOT_COMPILE, tag, str(exc))
    return None


DEFAULT_PATTERNS: tuple[tuple[str, str], ...] = (
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
)

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
        hide: Texts the anonymizer hides wherever they stand, as ``[NAME_n]``: a
            text a teacher wants hidden whatever the models and patterns find
            (:func:`config_hiding`).  Whole words, case-folded (:func:`keep_key`).
        keep: Texts the anonymizer must leave as they are: a text a teacher wants
            sent as written (:func:`config_keeping`).  What a pass found is dropped
            when it is such a text or lies inside one; a text in both lists is kept.
    """

    #: SECONDARY_MODEL_AUTO ("the system language's model"), SECONDARY_MODEL_NONE / None
    #: (no extra pass) or a model name.  Left unresolved here: the anonymizer turns it into a
    #: model name or ``None``.
    secondary_model: str | None = None
    primary_model: str | None = None
    patterns: tuple[tuple[str, str], ...] = field(default_factory=lambda: tuple(DEFAULT_PATTERNS))
    hide: tuple[str, ...] = ()
    keep: tuple[str, ...] = ()


class AnonymizerNoticeKind(StrEnum):
    """Why an automatic extra detection model was left out."""

    LANGUAGE_UNKNOWN = "language_unknown"
    """The language of this computer could not be determined."""
    MODEL_MISSING = "model_missing"
    """The model of that language is not downloaded."""


@dataclass(frozen=True)
class AnonymizerNotice:
    """What a run should tell the teacher about an automatic extra model it did not use."""

    kind: AnonymizerNoticeKind
    document_language: str
    system_language: str = ""
    """The language this computer is set to; empty when it could not be determined."""
    missing_model: str = ""
    """The model of :attr:`system_language` that is not downloaded."""
    primary_model: str = ""
    """The model the first pass loads; the second one would stand next to it."""


def resolve_anonymizer_config(
    config: AnonymizerConfig,
    *,
    document_language: str,
    system_language: str | None = None,
    installed_models: Collection[str] | None = None,
) -> tuple[AnonymizerConfig, AnonymizerNotice | None]:
    """Return *config* with the models that will actually be loaded, and what to say about it.

    ``secondary_model`` of :data:`SECONDARY_MODEL_AUTO` means "the model of the language the
    operating system is set to".  It is resolved here, once, so a run, a preview and an export
    all use the same models:

    * no system language could be determined → no extra pass (:data:`SECONDARY_MODEL_NONE`),
      and a notice says why;
    * the system language has the same model as the primary pass → no extra pass (a second
      pass with the same model would only repeat the first one), and nothing to say;
    * the model is missing while *installed_models* is known → no extra pass and a notice
      (an automatic default must never make a run fail);
    * an explicitly chosen model is kept as it is, missing or not: the anonymizer then fails
      with the same error as before, which is what the teacher asked for.

    The wording of a notice is :func:`~teachers_teammate.texts.addons.anonymizer_notice`.
    """
    secondary = config.secondary_model
    if secondary is None or str(secondary).strip().lower() in ("", SECONDARY_MODEL_NONE):
        return replace(config, secondary_model=None), None
    if str(secondary) != SECONDARY_MODEL_AUTO:
        return config, None

    primary = config.primary_model or spacy_model_for(document_language)
    if not system_language:
        return (
            replace(config, secondary_model=None),
            AnonymizerNotice(
                AnonymizerNoticeKind.LANGUAGE_UNKNOWN,
                document_language=document_language,
                primary_model=primary,
            ),
        )
    system_model = spacy_model_for(system_language)
    if system_model == primary:
        return replace(config, secondary_model=None), None
    if installed_models is not None and system_model not in installed_models:
        return (
            replace(config, secondary_model=None),
            AnonymizerNotice(
                AnonymizerNoticeKind.MODEL_MISSING,
                document_language=document_language,
                system_language=system_language,
                missing_model=system_model,
                primary_model=primary,
            ),
        )
    return replace(config, secondary_model=system_model), None


#: The tag of what :attr:`AnonymizerConfig.hide` finds: it becomes the placeholder ``[NAME_1]``.
HIDDEN_TAG = "NAME"


def keep_key(surface: str) -> str:
    """Return *surface* as the lists compare it: NFC, case-folded, without outer space."""
    return unicodedata.normalize("NFC", surface).strip().casefold()


def config_hiding(config: AnonymizerConfig, literal: str) -> AnonymizerConfig:
    """Return *config* with *literal* hidden from now on.

    The literal joins :attr:`AnonymizerConfig.hide` and leaves the keep list, which would
    otherwise win: the rule that hides it must be the newer of the two.
    """
    word = literal.strip()
    if not word:
        return config
    key = keep_key(word)
    hidden = config.hide
    if all(keep_key(entry) != key for entry in hidden):
        hidden = (*hidden, word)
    return replace(
        config,
        hide=hidden,
        keep=tuple(entry for entry in config.keep if keep_key(entry) != key),
    )


def config_keeping(config: AnonymizerConfig, literal: str) -> AnonymizerConfig:
    """Return *config* with *literal* left as it is in what goes out.

    The literal joins :attr:`AnonymizerConfig.keep` and leaves the hide list, so the two
    cannot contradict each other.
    """
    word = literal.strip()
    if not word:
        return config
    key = keep_key(word)
    kept = config.keep
    if all(keep_key(entry) != key for entry in kept):
        kept = (*kept, word)
    return replace(
        config,
        hide=tuple(entry for entry in config.hide if keep_key(entry) != key),
        keep=kept,
    )
