"""PII anonymization for OCR text before it is sent to a correction LLM.

Implements :class:`~teachers_teammate.interfaces.Anonymizer` using spacy NER for
person names and regex patterns for structural PII (email, phone, IBAN).

Install the optional dependency group to use this module::

    pip install "teachers_teammate[privacy]"

Then download a spacy model for your language, e.g.::

    python -m spacy download en_core_web_sm
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import sys

from ..interfaces import AnonymizationMap, Anonymizer

# ── Default regex patterns ─────────────────────────────────────────────────

DEFAULT_PATTERNS: list[tuple[str, str]] = [
    ("IBAN", r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}[A-Z0-9]{0,16}\b"),
    ("EMAIL", r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    (
        "PHONE",
        r"(?<!\d)(?:\+?\d{1,3}[\s\-.]?)?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,6}(?!\d)",
    ),
]

# ── Language → spacy model mapping ─────────────────────────────────────────

_SPACY_MODELS: dict[str, str] = {
    "english": "en_core_web_sm",
    "german": "de_core_news_sm",
    "french": "fr_core_news_sm",
    "spanish": "es_core_news_sm",
    "italian": "it_core_news_sm",
    "portuguese": "pt_core_news_sm",
    "dutch": "nl_core_news_sm",
}
_FALLBACK_MODEL = "xx_ent_wiki_sm"

_NER_PERSON_LABELS = frozenset({"PER", "PERSON"})


def _spacy_model_for(language: str) -> str:
    return _SPACY_MODELS.get(language.lower(), _FALLBACK_MODEL)


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
            Defaults to :data:`DEFAULT_PATTERNS` (IBAN, EMAIL, PHONE).
    """

    secondary_model: str | None = None
    primary_model: str | None = None
    patterns: tuple[tuple[str, str], ...] = field(default_factory=lambda: tuple(DEFAULT_PATTERNS))


# ── SpacyAnonymizer ────────────────────────────────────────────────────────


class SpacyAnonymizer(Anonymizer):
    """Anonymizes PII using spacy NER (person names) and regex (configurable patterns).

    The first NER pass always uses the model for the document language.  An
    optional second pass with a different model (e.g. a multilingual model)
    catches names that the language-specific model misses; its results are
    merged with the primary pass by taking the union of non-overlapping spans.

    The same surface text always receives the same placeholder within one call
    to :meth:`anonymize`, so the correction LLM sees consistent tokens and
    coreference is preserved.

    Args:
        language: Natural language of the documents (matches ``Config.language``).
            Used to select the primary spacy model unless overridden by
            ``config.primary_model``.
        config: Anonymization configuration.  Defaults to
            :class:`AnonymizerConfig` with no secondary model and the
            built-in patterns (IBAN, EMAIL, PHONE).

    Raises:
        ImportError: If spacy is not installed.
        OSError: If a required spacy model is not downloaded.
    """

    def __init__(self, language: str = "English", config: AnonymizerConfig | None = None) -> None:
        self._config = config if config is not None else AnonymizerConfig()
        primary_model = self._config.primary_model or _spacy_model_for(language)
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
                    f"ERROR: secondary spacy model '{self._config.secondary_model}' is not downloaded. "
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
        primary_spans = [
            (ent.start_char, ent.end_char, ent.text)
            for ent in self._nlp(result).ents
            if ent.label_ in _NER_PERSON_LABELS
        ]

        if self._nlp_secondary is not None:
            secondary_spans = [
                (ent.start_char, ent.end_char, ent.text)
                for ent in self._nlp_secondary(result).ents
                if ent.label_ in _NER_PERSON_LABELS
            ]
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
        result = text
        for placeholder, original in mapping.items():
            result = result.replace(placeholder, original)
        return result
