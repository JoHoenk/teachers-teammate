"""PII anonymization for OCR text before it is sent to a correction LLM.

Implements :class:`~teachers_teammate.interfaces.Anonymizer` using spacy NER for
person names and regex patterns for structural PII (email, phone, IBAN, street, postcode, dates).

Install the optional dependency group to use this module::

    pip install "teachers_teammate[privacy]"

Then download a spacy model for your language, e.g.::

    python -m spacy download en_core_web_sm
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Collection
from dataclasses import dataclass, field, replace
import hashlib
import re
import sys
import threading
from typing import TYPE_CHECKING

from ..config import SECONDARY_MODEL_AUTO, SECONDARY_MODEL_NONE
from ..domain.anonymization import restore
from ..domain.formatted_text import INLINE_TAG_ALTERNATION
from ..interfaces import AnonymizationMap, Anonymizer
from . import system_locale
from .spacy_models import spacy_model_for

if TYPE_CHECKING:
    from ..config import Config

#: Identifies one cached result: language and settings of the anonymizer, then the text hash.
CacheKey = tuple[object, ...]

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


# ── Markdown markup ────────────────────────────────────────────────────────

# Stored text may be Markdown: inline style tags, emphasis/strikethrough runs and the
# backslash of an escape would split names for the NER model (``<sup>Anna</sup>``).
_MARKUP_RE = re.compile(
    rf"</?(?:{INLINE_TAG_ALTERNATION}|br)\s*/?>"
    r"|\*+|~~+|\\(?=[!-/:-@\[-`{-~])",
    re.IGNORECASE,
)


# NER models often absorb the street word that follows a name ("Anna Müller Hauptstraße 12");
# a trailing token like this is left to the STREET pattern instead of becoming part of the name.
_TRAILING_STREET_RE = re.compile(
    r"(?:^|\s+)(?:[\w.-]+(?:weg|platz|gasse|allee|damm|ufer)|[\w.-]*(?:straße|strasse|str\.))\s*$",
    re.IGNORECASE,
)


def _trim_street(start: int, end: int, surface: str) -> tuple[int, int, str] | None:
    """Return the span without a trailing street word, or ``None`` if nothing is left.

    A bare surname such as "Platz" stays whole: only a street word with a stem before it counts.
    """
    match = _TRAILING_STREET_RE.search(surface)
    if match is None:
        return start, end, surface
    surface = surface[: match.start()]
    return (start, start + len(surface), surface) if surface.strip() else None


def _ner_view(text: str, markup: list[tuple[int, int]]) -> str:
    """Return *text* with every markup span blanked out, so offsets still match *text*."""
    chars = list(text)
    for start, end in markup:
        chars[start:end] = " " * (end - start)
    return "".join(chars)


def _split_at_markup(
    start: int, end: int, surface: str, text: str, markup: list[tuple[int, int]]
) -> list[tuple[int, int, str]]:
    """Return the entity ``start:end`` as one span per stretch of text between markup.

    ``**Anna** Mueller`` becomes two names rather than one span that would swallow the
    closing ``**`` and leave the opening one unbalanced.
    """
    inside = [(s, e) for s, e in markup if s < end and e > start]
    if not inside:
        return [(start, end, surface)]
    pieces: list[tuple[int, int, str]] = []
    cursor = start
    for mark_start, mark_end in [*inside, (end, end)]:
        piece = text[cursor : max(cursor, mark_start)]
        lead = len(piece) - len(piece.lstrip())
        stripped = piece.strip()
        if stripped:
            pieces.append((cursor + lead, cursor + lead + len(stripped), stripped))
        cursor = max(cursor, mark_end)
    return pieces


# ── Language → spacy model mapping ─────────────────────────────────────────

_NER_PERSON_LABELS = frozenset({"PER", "PERSON"})


# ── AnonymizerConfig ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnonymizerConfig:
    """Configuration for the anonymization passes.

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
            Defaults to :data:`DEFAULT_PATTERNS` (IBAN, EMAIL, street,
            postcode + city, dates, PHONE).
    """

    #: SECONDARY_MODEL_AUTO ("the system language's model"), SECONDARY_MODEL_NONE / None
    #: (no extra pass) or a model name.  Left unresolved here: :class:`SpacyAnonymizer`
    #: and :func:`resolve_anonymizer_config` turn it into a model name or ``None``.
    secondary_model: str | None = None
    primary_model: str | None = None
    patterns: tuple[tuple[str, str], ...] = field(default_factory=lambda: tuple(DEFAULT_PATTERNS))


#: Called with a warning sentence the user should see (e.g. the reporter's ``warn``).
Warner = Callable[[str], None]


def resolve_anonymizer_config(
    config: AnonymizerConfig,
    *,
    document_language: str,
    system_language: str | None = None,
    installed_models: Collection[str] | None = None,
    warn: Warner | None = None,
) -> AnonymizerConfig:
    """Return *config* with the models that will actually be loaded.

    ``secondary_model`` of :data:`~teachers_teammate.config.SECONDARY_MODEL_AUTO` means "the
    model of the language the operating system is set to".  It is resolved here, once, so a
    run, a preview and an export all use the same models:

    * no system language could be determined → no extra pass (:data:`SECONDARY_MODEL_NONE`);
    * the system language has the same model as the primary pass → no extra pass (a second
      pass with the same model would only repeat the first one);
    * the model is missing while *installed_models* is known → warning, no extra pass
      (an automatic default must never make a run fail);
    * an explicitly chosen model is kept as it is, missing or not: the anonymizer then fails
      with the same error as before, which is what the user asked for.
    """
    secondary = config.secondary_model
    missing: str | None = None
    if secondary is None or str(secondary).strip().lower() in ("", SECONDARY_MODEL_NONE):
        return replace(config, secondary_model=None)
    if str(secondary) != SECONDARY_MODEL_AUTO:
        return config

    primary = config.primary_model or spacy_model_for(document_language)
    if not system_language:
        if warn is not None:
            warn(
                "WARNING: the language of this computer could not be determined, so no "
                f"extra detection model is used (the model for {document_language} is "
                "enough). Choose a model in the personal-info settings."
            )
        return replace(config, secondary_model=None)
    system_model = spacy_model_for(system_language)
    if system_model == primary:
        return replace(config, secondary_model=None)
    if installed_models is not None and system_model not in installed_models:
        missing = system_model
    if missing is not None:
        if warn is not None:
            warn(
                f"WARNING: the extra detection model '{missing}' for the language of this "
                f"computer ({system_language}) is not downloaded, so only '{primary}' is "
                "used. Download it in Advanced Settings → Downloads to catch names the "
                f"{document_language} model misses."
            )
        return replace(config, secondary_model=None)
    return replace(config, secondary_model=system_model)


def anonymizer_config_from(config: Config) -> AnonymizerConfig:
    """Return the :class:`AnonymizerConfig` that *config* asks for, models unresolved.

    The single place that maps the stored settings onto the anonymizer, so a run, a
    preview and the status box all start from the same settings.
    """
    return AnonymizerConfig(
        primary_model=config.anonymizer_primary_model,
        secondary_model=config.anonymizer_secondary_model,
        patterns=resolve_patterns(config.anonymizer_patterns),
    )


# ── Result cache ───────────────────────────────────────────────────────────

#: How many anonymized texts one cache keeps before the oldest one is dropped.
DEFAULT_CACHE_CAPACITY = 256


class AnonymizationResultCache:
    """Remembers anonymized texts so spaCy runs once per text and settings.

    Kept in memory only: the anonymized text is the very data the user wants hidden, so it
    must not outlive the process.  Every access takes the cache's own lock: a run, a preview
    and a dialog share one cache, and a lookup (which reorders the entries) must not meet an
    eviction half-way.  The spaCy pass itself is serialized separately by the callers.
    """

    def __init__(self, capacity: int = DEFAULT_CACHE_CAPACITY) -> None:
        self._capacity = max(1, capacity)
        self._guard = threading.Lock()
        self._entries: OrderedDict[CacheKey, tuple[str, AnonymizationMap]] = OrderedDict()

    def get(self, key: CacheKey) -> tuple[str, AnonymizationMap] | None:
        """Return the cached ``(text, mapping)`` of *key*, or ``None`` when it is not cached."""
        with self._guard:
            value = self._entries.get(key)
            if value is None:
                return None
            self._entries.move_to_end(key)
            return value

    def put(self, key: CacheKey, value: tuple[str, AnonymizationMap]) -> None:
        """Store *value* under *key*, dropping the least recently used entry when full."""
        with self._guard:
            self._entries[key] = value
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Forget every cached text."""
        with self._guard:
            self._entries.clear()

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)


class CachedAnonymizer(Anonymizer):
    """:class:`Anonymizer` that answers from :class:`AnonymizationResultCache` first.

    The key combines the language and settings the wrapped anonymizer was built for with the
    hash of the text, so the same text is analysed once per session — no matter whether a run,
    a preview or an export asks for it.  ``restore`` is passed straight through: it does not
    depend on the text.

    Args:
        inner: The anonymizer that actually runs spaCy.
        cache: The shared result cache.
        key_prefix: Language and resolved settings, identifying *inner*.
        lock: Optional lock held while the wrapped anonymizer runs.
    """

    def __init__(
        self,
        inner: Anonymizer,
        cache: AnonymizationResultCache,
        key_prefix: tuple[object, ...],
        lock: threading.RLock | None = None,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._key_prefix = key_prefix
        self._lock = lock

    def anonymize(self, text: str) -> tuple[str, AnonymizationMap]:
        """Return ``(anonymized_text, mapping)``, from the cache when the text was seen."""
        key = (*self._key_prefix, hashlib.sha256(text.encode("utf-8")).hexdigest())
        result = self._cache.get(key)
        if result is not None:
            return result[0], dict(result[1])
        if self._lock is None:
            return self._store(key, self._inner.anonymize(text))
        with self._lock:
            # Another caller may have filled the entry while this one waited for the lock.
            result = self._cache.get(key)
            if result is None:
                result = self._store(key, self._inner.anonymize(text))
        return result[0], dict(result[1])

    def _store(
        self, key: CacheKey, result: tuple[str, AnonymizationMap]
    ) -> tuple[str, AnonymizationMap]:
        """Cache *result* under *key* and return it."""
        self._cache.put(key, result)
        return result

    def restore(self, text: str, mapping: AnonymizationMap) -> str:
        """Put the placeholders of *text* back; delegated, results are not cached."""
        return self._inner.restore(text, mapping)


# ── SpacyAnonymizer ────────────────────────────────────────────────────────


class SpacyAnonymizer(Anonymizer):
    """Anonymizes PII using spacy NER (person names) and regex (configurable patterns).

    The first NER pass uses the model for the document language unless
    ``config.primary_model`` overrides it.  An optional second pass with a
    different model (e.g. a multilingual model) catches names that the
    first model misses; its results are merged with the primary pass by
    taking the union of non-overlapping spans.

    The same surface text always receives the same placeholder within one call
    to :meth:`anonymize`, so the correction LLM sees consistent tokens and
    coreference is preserved.

    Args:
        language: Natural language of the documents (matches ``Config.language``).
            Used to select the primary spacy model unless overridden by
            ``config.primary_model``.
        config: Anonymization configuration.  Defaults to
            :class:`AnonymizerConfig` with no secondary model and the
            built-in patterns (IBAN, EMAIL, street, postcode + city, dates, PHONE).

    Raises:
        ImportError: If spacy is not installed.
        OSError: If a required spacy model is not downloaded.
    """

    def __init__(self, language: str = "English", config: AnonymizerConfig | None = None) -> None:
        self._config = resolve_anonymizer_config(
            config if config is not None else AnonymizerConfig(),
            document_language=language,
            system_language=system_locale.system_language(),
        )
        primary_model = self._config.primary_model or spacy_model_for(language)
        try:
            import spacy  # noqa: PLC0415
        except ImportError as exc:
            print(
                "ERROR: spacy is not installed. "
                "Install the privacy extras: pip install 'teachers_teammate[privacy]'",
                file=sys.stderr,
            )
            raise ImportError("spacy is required for anonymization") from exc
        try:
            spacy.prefer_gpu()
            self._nlp = spacy.load(primary_model)
        except OSError:
            print(
                f"ERROR: spacy model '{primary_model}' is not downloaded. "
                f"Run: python -m spacy download {primary_model}",
                file=sys.stderr,
            )
            raise

        self._nlp_secondary = None
        if self._config.secondary_model:
            try:
                self._nlp_secondary = spacy.load(self._config.secondary_model)
            except OSError:
                print(
                    f"ERROR: secondary spacy model '{self._config.secondary_model}' "
                    "is not downloaded. "
                    f"Run: python -m spacy download {self._config.secondary_model}",
                    file=sys.stderr,
                )
                raise

    def anonymize(self, text: str) -> tuple[str, AnonymizationMap]:
        """Replace PII in *text* with stable placeholders.

        Returns:
            Tuple of *(anonymized_text, mapping)* where mapping is
            ``{placeholder: original_surface_text}``.
        """
        mapping: AnonymizationMap = {}
        seen: dict[str, str] = {}  # original surface → assigned placeholder
        result = text

        # ── NER: collect person spans ──────────────────────────────────────
        markup = [match.span() for match in _MARKUP_RE.finditer(text)]
        ner_text = _ner_view(text, markup)

        def person_spans(nlp) -> list[tuple[int, int, str]]:
            pieces = (
                piece
                for ent in nlp(ner_text).ents
                if ent.label_ in _NER_PERSON_LABELS
                for piece in _split_at_markup(ent.start_char, ent.end_char, ent.text, text, markup)
            )
            return [span for piece in pieces if (span := _trim_street(*piece)) is not None]

        primary_spans = person_spans(self._nlp)

        if self._nlp_secondary is not None:
            secondary_spans = person_spans(self._nlp_secondary)
            # Union: add secondary spans that don't overlap any primary span.
            covered = [(s, e) for s, e, _ in primary_spans]
            extra = [
                span
                for span in secondary_spans
                if not any(max(s, span[0]) < min(e, span[1]) for s, e in covered)
            ]
            ner_spans = primary_spans + extra
        else:
            ner_spans = primary_spans

        person_counter = 0
        ner_replacements: list[tuple[int, int, str]] = []
        for start, end, surface in ner_spans:
            if surface in seen:
                placeholder = seen[surface]
            else:
                person_counter += 1
                placeholder = f"[PERSON_{person_counter}]"
                seen[surface] = placeholder
                mapping[placeholder] = surface
            ner_replacements.append((start, end, placeholder))

        for start, end, placeholder in sorted(ner_replacements, key=lambda x: x[0], reverse=True):
            result = result[:start] + placeholder + result[end:]

        # ── Regex: configured patterns ─────────────────────────────────────
        for tag, pattern_str in self._config.patterns:
            try:
                compiled = re.compile(pattern_str)
            except re.error:
                continue
            matches = list(compiled.finditer(result))
            tag_counter = 0
            replacements: list[tuple[int, int, str]] = []
            for m in matches:
                surface = m.group(0)
                if surface in seen:
                    placeholder = seen[surface]
                else:
                    tag_counter += 1
                    placeholder = f"[{tag}_{tag_counter}]"
                    seen[surface] = placeholder
                    mapping[placeholder] = surface
                replacements.append((m.start(), m.end(), placeholder))
            for start, end, placeholder in reversed(replacements):
                result = result[:start] + placeholder + result[end:]

        return result, mapping

    def restore(self, text: str, mapping: AnonymizationMap) -> str:
        """Replace all placeholder tokens in *text* with their original values."""
        return restore(text, mapping)
