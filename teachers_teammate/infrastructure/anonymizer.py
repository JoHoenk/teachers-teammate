"""PII anonymization for OCR text before it is sent to a correction LLM.

Implements :class:`~teachers_teammate.interfaces.Anonymizer` using spacy NER for
person names and regex patterns for structural PII (email, phone, IBAN, street, postcode, dates).

spaCy and the model for a language are add-ons: the Add-ons page installs both, and
:class:`~teachers_teammate.infrastructure.spacy_models.SpacyModels` hands the loaded models in.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import re
import threading

from ..domain.anonymization import (
    HIDDEN_TAG,
    AnonymizerConfig,
    keep_key,
    resolve_anonymizer_config,
    restore,
    surface_pattern,
)
from ..domain.formatted_text import INLINE_TAG_ALTERNATION
from ..domain.name_models import spacy_model_for
from ..exceptions import AnonymizerError
from ..interfaces import AnonymizationMap, Anonymizer
from ..texts.run import personal_info_pattern_invalid
from . import system_locale
from .spacy_models import SpacyModels

#: Identifies one cached result: language and settings of the anonymizer, then the text hash.
CacheKey = tuple[object, ...]

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


# ── Result cache ───────────────────────────────────────────────────────────

#: How many anonymized texts one cache keeps before the oldest one is dropped.
DEFAULT_CACHE_CAPACITY = 256


class AnonymizationResultCache:
    """Remembers anonymized texts so spaCy runs once per text and settings.

    Kept in memory only, and it is only a speed-up: it is not where a result's sent text
    lives.  The text a proofreading was really made from (and its placeholder map) is stored
    with that result (``DocumentState``, see ``SentRecord``), so a lost entry here costs one
    more spaCy pass, never a different answer.  Every access takes the cache's own lock: a run, a preview
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


# ── Spans: what is replaced, and where ─────────────────────────────────────


@dataclass(frozen=True)
class _Span:
    """One stretch of the text to replace: where it is, what stands there and under which tag."""

    start: int
    end: int
    surface: str
    tag: str


def _compiled_pattern(tag: str, pattern: str) -> re.Pattern[str]:
    """Return *pattern* compiled, or fail the file rather than send what it should have hidden."""
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise AnonymizerError(personal_info_pattern_invalid(tag, exc)) from exc


@dataclass(frozen=True)
class _Keep:
    """What the teacher's keep list protects in one text: the surfaces and where they stand."""

    keys: frozenset[str] = frozenset()
    ranges: tuple[tuple[int, int], ...] = ()

    @classmethod
    def of(cls, text: str, keep: tuple[str, ...]) -> _Keep:
        """Return what the entries of *keep* protect in *text*."""
        pattern = surface_pattern(keep, ignore_case=True)
        if pattern is None:
            return cls()
        return cls(
            frozenset(keep_key(entry) for entry in keep),
            tuple(match.span() for match in pattern.finditer(text)),
        )

    def covers(self, start: int, end: int, surface: str) -> bool:
        """Whether a span is a kept text, or lies inside the occurrence of one."""
        return keep_key(surface) in self.keys or any(
            kept_start <= start and end <= kept_end for kept_start, kept_end in self.ranges
        )


def _without_overlaps(text: str, ordered: list[_Span], keep: _Keep) -> list[_Span]:
    """Return *ordered* with every part another span already covers taken out.

    *ordered* is the priority order — person names first, then the patterns as they are
    configured.  A span that loses a part to a person name keeps the rest: a street the NER
    model half swallowed is still replaced as a street.  A pattern span another pattern
    overlaps is dropped whole, so a stray fragment never becomes a placeholder of its own.

    A span that *keep* covers — a text of the teacher's keep list, or part of one — is dropped,
    whoever found it.
    """
    accepted: list[_Span] = []
    for candidate in ordered:
        if keep.covers(candidate.start, candidate.end, candidate.surface):
            continue
        pieces = [(candidate.start, candidate.end)]
        for taken in accepted:
            pieces = [
                piece for start, end in pieces for piece in _cut(start, end, taken.start, taken.end)
            ]
        if pieces == [(candidate.start, candidate.end)]:
            accepted.append(candidate)
            continue
        if candidate.tag != "PERSON" and any(
            taken.tag != "PERSON" and taken.start < candidate.end and candidate.start < taken.end
            for taken in accepted
        ):
            continue
        for start, end in pieces:
            piece = text[start:end]
            trimmed = piece.strip()
            offset = start + len(piece) - len(piece.lstrip())
            if trimmed and not keep.covers(offset, offset + len(trimmed), trimmed):
                accepted.append(_Span(offset, offset + len(trimmed), trimmed, candidate.tag))
    return accepted


def _cut(start: int, end: int, taken_start: int, taken_end: int) -> list[tuple[int, int]]:
    """Return *start:end* without the part *taken_start:taken_end* already covers."""
    if taken_end <= start or taken_start >= end:
        return [(start, end)]
    pieces = []
    if taken_start > start:
        pieces.append((start, min(taken_start, end)))
    if taken_end < end:
        pieces.append((max(taken_end, start), end))
    return pieces


def _assign_placeholders(spans: list[_Span], text: str) -> dict[str, str]:
    """Return ``{surface: placeholder}`` for *spans*, one counter per tag.

    The same surface keeps one placeholder wherever it stands, so the model sees a consistent
    token and coreference is preserved.  A token the text already holds would be restored into
    the wrong place, so such a placeholder gets a letter suffix instead.
    """
    counters: dict[str, int] = {}
    placeholders: dict[str, str] = {}
    for span in spans:
        if span.surface in placeholders:
            continue
        counters[span.tag] = counters.get(span.tag, 0) + 1
        placeholders[span.surface] = _free_placeholder(span.tag, counters[span.tag], text)
    return placeholders


def _free_placeholder(tag: str, number: int, text: str) -> str:
    """Return ``[TAG_<number>]``, lettered further while the text already holds that token."""
    candidate = f"[{tag}_{number}]"
    suffix = 0
    while candidate in text:
        suffix += 1
        candidate = f"[{tag}_{number}{_letters(suffix)}]"
    return candidate


def _letters(number: int) -> str:
    """Return *number* as a letter label: 1 → ``"a"``, 26 → ``"z"``, 27 → ``"aa"``."""
    label = ""
    while number:
        number, rest = divmod(number - 1, 26)
        label = chr(ord("a") + rest) + label
    return label


def _further_surfaces(
    text: str, spans: list[_Span], placeholders: dict[str, str], keep: _Keep
) -> list[_Span]:
    """Return the further occurrences of the surfaces already in *placeholders*.

    A name the model found in one sentence is replaced in all of them, so a run never sends a
    name just because the NER pass was unsure about the second mention.  What *keep* covers
    stays as written.
    """
    pattern = surface_pattern(placeholders)
    if pattern is None:
        return []
    return [
        _Span(match.start(), match.end(), match.group(0), "")
        for match in pattern.finditer(text)
        if not any(max(span.start, match.start()) < min(span.end, match.end()) for span in spans)
        and not keep.covers(match.start(), match.end(), match.group(0))
    ]


def _replace(text: str, spans: list[_Span], placeholders: dict[str, str]) -> str:
    """Return *text* with every span replaced by its placeholder, in one pass."""
    pieces: list[str] = []
    position = 0
    for span in sorted(spans, key=lambda span: span.start):
        if span.start < position:
            continue
        pieces.append(text[position : span.start])
        pieces.append(placeholders[span.surface])
        position = span.end
    pieces.append(text[position:])
    return "".join(pieces)


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

    def __init__(
        self,
        language: str = "English",
        config: AnonymizerConfig | None = None,
        *,
        models: SpacyModels,
    ) -> None:
        # Nothing is said about a dropped extra model here: the run that builds this resolved
        # the same settings and worded the notice itself.
        self._config, _notice = resolve_anonymizer_config(
            config if config is not None else AnonymizerConfig(),
            document_language=language,
            system_language=system_locale.system_language(),
        )
        primary_model = self._config.primary_model or spacy_model_for(language)
        self._nlp, self._nlp_secondary = models.load(primary_model, self._config.secondary_model)

    def anonymize(self, text: str) -> tuple[str, AnonymizationMap]:
        """Replace PII in *text* with stable placeholders.

        Two passes, both over the text as it was given: the NER models find person names, the
        configured patterns find the rest.  Their spans are resolved against each other and
        replaced in one go, so no pattern ever runs over a placeholder; the tag that comes
        first in the settings wins an overlap, a person name before all of them.  A surface
        that was found once is also replaced where a pass missed it, with its own placeholder.
        The texts the teacher asked to hide come first, the ones to keep are never replaced.

        Returns:
            Tuple of *(anonymized_text, mapping)* where mapping is
            ``{placeholder: original_surface_text}``.
        """
        keep = _Keep.of(text, self._config.keep)
        spans = _without_overlaps(
            text,
            [*self._hidden_spans(text), *self._person_spans(text), *self._pattern_spans(text)],
            keep,
        )
        placeholders = _assign_placeholders(spans, text)
        spans = [*spans, *_further_surfaces(text, spans, placeholders, keep)]
        return _replace(text, spans, placeholders), {
            placeholder: surface for surface, placeholder in placeholders.items()
        }

    def _person_spans(self, text: str) -> list[_Span]:
        """Return the person names the NER models find in *text*."""
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
        spans = [_Span(start, end, surface, "PERSON") for start, end, surface in primary_spans]
        if self._nlp_secondary is None:
            return spans
        covered = [(s, e) for s, e, _ in primary_spans]
        return [
            *spans,
            *(
                _Span(start, end, surface, "PERSON")
                for start, end, surface in person_spans(self._nlp_secondary)
                if not any(max(s, start) < min(e, end) for s, e in covered)
            ),
        ]

    def _hidden_spans(self, text: str) -> list[_Span]:
        """Return the texts of the teacher's hide list where they stand in *text*."""
        pattern = surface_pattern(self._config.hide, ignore_case=True)
        if pattern is None:
            return []
        return [
            _Span(match.start(), match.end(), match.group(0), HIDDEN_TAG)
            for match in pattern.finditer(text)
        ]

    def _pattern_spans(self, text: str) -> list[_Span]:
        """Return what the configured patterns find in *text*, pattern by pattern."""
        spans: list[_Span] = []
        for tag, pattern in self._config.patterns:
            compiled = _compiled_pattern(tag, pattern)
            spans.extend(
                _Span(match.start(), match.end(), match.group(0), tag)
                for match in compiled.finditer(text)
            )
        return spans

    def restore(self, text: str, mapping: AnonymizationMap) -> str:
        """Replace all placeholder tokens in *text* with their original values."""
        return restore(text, mapping)
