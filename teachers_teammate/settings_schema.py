"""The settings schema: one entry per key, with its default, its normaliser and its scope.

Every layer reads a settings key through this module, so a value means the same in the
settings file, on the command line and in the window.  :mod:`teachers_teammate.config` builds
a :class:`~teachers_teammate.config.Config` from it; the GUI's panel stores the same keys.

A normaliser takes the raw value and returns the value the app uses, or raises
:exc:`ValueError` for "ignore this and warn" — :func:`normalize_settings` turns that into a
:class:`~teachers_teammate.domain.settings_notice.SettingNotice`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from ._model_defaults import default_model_for_task
from .domain.anonymization import (
    SECONDARY_MODEL_AUTO,
    SECONDARY_MODEL_NONE,
    pattern_problem,
)
from .domain.correction_policy import (
    DEFAULT_LEVEL,
    CorrectionOutput,
    normalize_level,
    normalize_output_mode,
)
from .domain.correction_prompts import NOTES_LIMIT
from .domain.data_location import (
    ENGINE_OLLAMA,
    LOCAL_OCR_ENGINES,
    PROVIDER_OLLAMA,
    RETIRED_OCR_ENGINES,
    OcrEngine,
)
from .domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    HardwareProfileSetting,
    parse_profile_setting,
)
from .domain.ollama_address import normalize_ollama_url
from .domain.ollama_discovery import DEFAULT_SERVER_URL
from .domain.output_files import DocxFormat
from .domain.pdf_text_layer import PDF_TEXT_LAYER_MODES, parse_pdf_text_layer
from .domain.prompt_text import migrate_saved_default
from .domain.setting_values import ContextSetting, PreprocessMethod
from .domain.settings_notice import SettingNotice, SettingNoticeKind
from .domain.stages import Stage

#: Values accepted for ``proofreading_context``: ``"auto"`` or a window from this ladder.
CONTEXT_SETTINGS: tuple[ContextSetting, ...] = tuple(ContextSetting)

PREPROCESS_METHODS: tuple[PreprocessMethod, ...] = tuple(PreprocessMethod)
"""The two image preparations: the automatic one, and "Original page" (no step)."""

_DEFAULT_LANGUAGE = "English"

#: What the content review asks.  The teacher can replace it (Options… of "Review content").
_DEFAULT_EVALUATE_PROMPT = (
    "You help a teacher read a student's text. Review its content, not its spelling, "
    "grammar or punctuation. Write a short report with: "
    "1) what the text is about, in one sentence; "
    "2) whether it is complete and consistent; "
    "3) gaps, contradictions or passages that are hard to follow, each with a short quote; "
    "4) one short suggestion for the teacher. "
    "Write in the language of the text and keep the report brief."
)


class SettingScope(StrEnum):
    """Who reads a setting, and therefore where it is stored."""

    RUN = "run"
    """A field of :class:`~teachers_teammate.config.Config`: it shapes one run."""

    APP = "app"
    """Read by the GUI only, never by a pipeline step (``check_for_updates``)."""

    FILE = "file"
    """Where a run reads and writes (``input``, ``output``, ``recursive``)."""


def _text(value: object) -> str:
    """Return *value* as text, raising :exc:`ValueError` for a missing one (``None``)."""
    if value is None:
        raise ValueError("it needs a value")
    return str(value)


def _folder(value: object) -> str:
    """Return the folder *value* as text; a missing one is no folder."""
    return "" if value is None else str(value)


def _flag(value: object) -> bool:
    """Return *value* as a switch, raising :exc:`ValueError` for anything but true or false.

    ``bool("false")`` is true: a text that merely looks like a switch must not turn one on.
    """
    if not isinstance(value, bool):
        raise ValueError("it must be true or false")
    return value


def _count(value: object) -> int:
    """Return *value* as a whole number, raising :exc:`ValueError` for anything else."""
    return int(str(value).strip())


#: The pages the app renders: below 72 dpi a page cannot be read, above 600 dpi it only costs
#: memory.  The command line and the window's spin box take the range from here.
PDF_RENDER_DPI_RANGE = (72, 600)

#: How long one recognition request may wait, in seconds.  No timeout at all would let a run
#: hang forever on a server that stopped answering.
OCR_TIMEOUT_RANGE = (1, 86400)

#: How long the results of earlier runs may stay, in days; ``0`` keeps them.  A year is the
#: longest a teacher can sensibly ask for, and less than a day would keep nothing.
RESULTS_KEEP_DAYS_RANGE = (0, 365)


def _count_between(low: int, high: int) -> Callable[[object], int]:
    """Return a normaliser that accepts a whole number from *low* to *high* only.

    A value outside is refused like text where a number belongs: the run needs a number it can
    work with, and a page rendered at 0 dpi or a timeout of no seconds is none.
    """

    def normalize(value: object) -> int:
        number = _count(value)
        if not low <= number <= high:
            raise ValueError(f"it must be a whole number from {low} to {high}")
        return number

    return normalize


def _language(value: object) -> str:
    """Return the document language, falling back to the default for an empty one."""
    return str(value).strip() or _DEFAULT_LANGUAGE


def _optional_text(value: object) -> str | None:
    """Return *value* as text, or ``None`` when it is empty."""
    return str(value) if value else None


def _secondary_model(value: object) -> str:
    """Return the extra detection model, where an empty value means "no extra model"."""
    return str(value) if value else SECONDARY_MODEL_NONE


def _hardware_profile(value: object) -> HardwareProfileSetting:
    """Return *value* as a hardware profile setting, raising :exc:`ValueError` otherwise."""
    setting = parse_profile_setting(value)
    if setting is None:
        allowed = ", ".join(member.value for member in HardwareProfileSetting)
        raise ValueError(f"'{value}' is not one of {allowed}")
    return setting


def _member_of[E: StrEnum](kind: type[E]) -> Callable[[object], E]:
    """Return a normaliser that accepts only the values of *kind* (case and whitespace folded)."""

    def normalize(value: object) -> E:
        try:
            return kind(str(value).strip().lower())
        except ValueError:
            allowed = ", ".join(member.value for member in kind)
            raise ValueError(f"'{value}' is not one of {allowed}") from None

    return normalize


def _provider_of(task: Stage) -> Callable[[object], str]:
    """Return a normaliser that accepts only a provider that has a default model for *task*.

    A saved provider the app no longer knows (a removed one, a hand edit) is ignored with a
    notice, so the default applies instead of every later model lookup failing.
    """

    def normalize(value: object) -> str:
        name = str(value).strip()
        try:
            default_model_for_task(name, task)
        except ValueError:
            raise ValueError(f"'{value}' is not a provider") from None
        return name

    return normalize


def _pdf_text_layer(value: object) -> str:
    """Return *value* as a PDF text-layer mode, raising :exc:`ValueError` for anything else."""
    mode = parse_pdf_text_layer(value)
    if mode is None:
        raise ValueError(f"'{value}' is not one of {', '.join(PDF_TEXT_LAYER_MODES)}")
    return mode


def _anonymizer_patterns(value: object) -> list[tuple[str, str]] | None:
    """Return *value* as ``[tag, pattern]`` pairs, raising :exc:`ValueError` for anything else.

    ``None`` is the default ("the anonymizer's own list") and stays ``None``.  A pattern that
    does not compile, or a tag used twice, is refused here: the anonymizer would otherwise
    skip it silently, and the text it was meant to hide would be sent as it is.
    """
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and all(isinstance(item, str) for item in pair)
        for pair in value
    ):
        raise ValueError("it must be a list of [tag, pattern] string pairs")
    pairs = [(str(tag), str(pattern)) for tag, pattern in value]
    tags: set[str] = set()
    for tag, pattern in pairs:
        problem = pattern_problem(tag, pattern, tags)
        if problem:
            raise ValueError(f"{problem.kind.value}: '{problem.tag}'")
        tags.add(tag)
    return pairs


def _evaluate_prompt(value: object) -> str:
    """Return the content review's instructions; a saved copy of the default means "built-in".

    Trailing whitespace is dropped: it is not part of the instructions, and the value is part
    of the review's fingerprint.
    """
    return migrate_saved_default(str(value).rstrip(), _DEFAULT_EVALUATE_PROMPT)


def normalize_saved_ollama_url(value: object) -> str:
    """Return *value* as the address the app uses: the default server when it is blank.

    A saved setup can hold an empty address (a cleared field, an older file).  The one meaning
    of that is :data:`~teachers_teammate.domain.ollama_discovery.DEFAULT_SERVER_URL`, decided
    here so no reader of ``Config.ollama_url`` has to decide it again.
    """
    return normalize_ollama_url("" if value is None else str(value))


def normalize_ocr_engine(value: object) -> str:
    """Return *value* if it is a local OCR engine, else the engine used instead.

    Settings files written by older versions may name an online engine (``langchain`` or a
    provider such as ``openai``).  Text recognition never goes online any more, so those fall
    back to the default instead of silently uploading pages.  A removed local engine
    (``tesseract``, ``paddleocr``) becomes the built-in reader.  What was replaced is reported
    by :func:`normalize_settings`, not here.
    """
    engine = str(value).strip().lower()
    if engine in LOCAL_OCR_ENGINES:
        return OcrEngine(engine)
    if engine in RETIRED_OCR_ENGINES:
        return RETIRED_OCR_ENGINES[engine]
    return DEFAULTS["ocr_engine"]


def normalize_ocr_model(value: object, model: object) -> str:
    """Return *model*, or the default OCR model when *value* named an unavailable engine.

    A model name saved next to an online engine (``gpt-4o``) means nothing to the engine that
    replaces it, so it is reset together with the engine.
    """
    if str(value).strip().lower() in (*LOCAL_OCR_ENGINES, *RETIRED_OCR_ENGINES):
        return str(model)
    return DEFAULTS["ocr_model"]


def normalize_preprocess_method(method: object) -> PreprocessMethod:
    """Return ``"none"`` (Original page) for ``none``, ``"auto"`` for anything else.

    Settings files from before the automatic preparation name ``grayscale``, ``clahe`` or
    ``adaptive_threshold``: those methods are gone and read as the automatic preparation.
    """
    if str(method).strip().lower() == PreprocessMethod.NONE:
        return PreprocessMethod.NONE
    return PreprocessMethod.AUTO


def normalize_notes(notes: object) -> str:
    """Return the teacher's notes as a request carries them: stripped, at most ``NOTES_LIMIT``.

    The same rule in the settings file, on the command line and in the dialog, so a request
    never carries more than the limit.  That a longer text was cut is reported by
    :func:`normalize_settings`.
    """
    text = str(notes or "").strip()
    return text[:NOTES_LIMIT].rstrip() if len(text) > NOTES_LIMIT else text


@dataclass(frozen=True)
class Setting:
    """One settings key: what it defaults to, what it accepts, and who reads it."""

    key: str
    default: Any
    scope: SettingScope
    normalize: Callable[[object], Any] = _text


#: Every settings key, in the order a settings file lists them.
SETTINGS: tuple[Setting, ...] = (
    # ── Where the run reads and writes ───────────────────────────────────────
    Setting("input", "", SettingScope.FILE, _folder),
    Setting("output", "", SettingScope.FILE, _folder),
    Setting("recursive", False, SettingScope.FILE, _flag),
    # ── OCR ──────────────────────────────────────────────────────────────────
    Setting("ocr_engine", ENGINE_OLLAMA, SettingScope.RUN, normalize_ocr_engine),
    Setting("ocr_model", DEFAULT_OCR_MODEL, SettingScope.RUN),
    Setting("language", _DEFAULT_LANGUAGE, SettingScope.RUN, _language),
    Setting(
        "preprocess_method", PreprocessMethod.AUTO, SettingScope.RUN, normalize_preprocess_method
    ),
    Setting("ollama_url", DEFAULT_SERVER_URL, SettingScope.RUN, normalize_saved_ollama_url),
    Setting("ocr_timeout", 180, SettingScope.RUN, _count_between(*OCR_TIMEOUT_RANGE)),
    # Whether scans (images, PDFs without a text layer) are read.  Off = only text that already
    # exists is used (text files, PDFs with a text layer) and scans are skipped.
    Setting("recognition_enabled", True, SettingScope.RUN, _flag),
    # Keep the computer from going to sleep while documents are processed (the screen may still
    # turn off).  Not part of any cache key.
    Setting("keep_awake", True, SettingScope.RUN, _flag),
    # Ask GitHub once at every start whether a newer version exists.  The only thing the app
    # sends without being asked, so it can be switched off; a settings file without the key
    # keeps the check on.
    Setting("check_for_updates", True, SettingScope.APP, _flag),
    # How long the results of earlier runs stay, in days; 0 = keep them.  The window applies it
    # once at start, so turning it on never deletes anything mid-run.
    Setting("results_keep_days", 0, SettingScope.APP, _count_between(*RESULTS_KEEP_DAYS_RANGE)),
    # ── Preprocessing (image preparation and PDF rendering) ───────────────────
    Setting("pdf_render_dpi", 300, SettingScope.RUN, _count_between(*PDF_RENDER_DPI_RANGE)),
    Setting("pdf_text_layer", "auto", SettingScope.RUN, _pdf_text_layer),
    Setting("debug", False, SettingScope.RUN, _flag),
    # ── Correction ────────────────────────────────────────────────────────────
    Setting("correction_enabled", True, SettingScope.RUN, _flag),
    Setting("anonymization_enabled", False, SettingScope.RUN, _flag),
    Setting(
        "correction_provider", PROVIDER_OLLAMA, SettingScope.RUN, _provider_of(Stage.CORRECTION)
    ),
    Setting("correction_model", DEFAULT_WRITING_MODEL, SettingScope.RUN),
    # What the teacher wants the model to know about this class or this text.  The instructions
    # themselves are fixed by the app; the notes are the one place a teacher adds context.
    Setting("correction_notes", "", SettingScope.RUN, normalize_notes),
    # CEFR level of the proofreading (A1-C2); language-neutral, so one setting for all languages.
    Setting("correction_level", DEFAULT_LEVEL, SettingScope.RUN, lambda v: normalize_level(str(v))),
    # "" = same as the language of the text. Otherwise the language of the explanations.
    Setting("explanation_language", "", SettingScope.RUN),
    Setting(
        "correction_output",
        CorrectionOutput.REVIEW,
        SettingScope.RUN,
        lambda v: normalize_output_mode(str(v)),
    ),
    # ── Evaluation ────────────────────────────────────────────────────────────
    Setting("evaluation_enabled", False, SettingScope.RUN, _flag),
    Setting("evaluate_provider", PROVIDER_OLLAMA, SettingScope.RUN, _provider_of(Stage.EVALUATION)),
    Setting("evaluate_model", DEFAULT_WRITING_MODEL, SettingScope.RUN),
    Setting("evaluate_prompt", _DEFAULT_EVALUATE_PROMPT, SettingScope.RUN, _evaluate_prompt),
    # ── Output ────────────────────────────────────────────────────────────────
    Setting("docx_format", DocxFormat.TABLE, SettingScope.RUN, _member_of(DocxFormat)),
    Setting("docx_enabled", False, SettingScope.RUN, _flag),
    # Text files of the extracted text, plain and with personal data replaced.
    Setting("text_enabled", False, SettingScope.RUN, _flag),
    Setting("text_anonymized_enabled", False, SettingScope.RUN, _flag),
    # ── Anonymizer ────────────────────────────────────────────────────────────
    Setting("anonymizer_primary_model", None, SettingScope.RUN, _optional_text),
    # "auto:system" = the model for the language the operating system is set to;
    # "none" / None = no extra model.
    Setting("anonymizer_secondary_model", SECONDARY_MODEL_AUTO, SettingScope.RUN, _secondary_model),
    # None (or the earlier built-in list) = the current DEFAULT_PATTERNS of the anonymizer;
    # [] = no regex patterns.
    Setting("anonymizer_patterns", None, SettingScope.RUN, _anonymizer_patterns),
    # ── Hardware recommendations ──────────────────────────────────────────────
    # Detect this computer, or pin one of the three tiers.  The pipeline never reads it, but
    # the run-start memory warning does: a hand-picked profile decides how much of a model has
    # to fit where.
    Setting(
        "hardware_profile",
        HardwareProfileSetting.AUTO,
        SettingScope.RUN,
        _hardware_profile,
    ),
    # ── Context window ────────────────────────────────────────────────────────
    # "auto" = as large as the graphics card still holds at full speed (8192 / 16384 / 32768,
    # never smaller than 8192).  Sized from the card's total memory, not from the memory free
    # right now: free memory changes between runs, and a window that changed with it would make
    # Ollama reload the model and would invalidate the result cache.  A fixed value from
    # CONTEXT_SETTINGS pins it — for advanced users, and for a remote server whose card the app
    # cannot see.
    Setting(
        "proofreading_context", ContextSetting.AUTO, SettingScope.RUN, _member_of(ContextSetting)
    ),
)

#: The defaults of the settings a run or the app reads.  The folders are not among them: a
#: "restore defaults" must not clear the folder a teacher chose.
DEFAULTS: Mapping[str, Any] = MappingProxyType(
    {setting.key: setting.default for setting in SETTINGS if setting.scope is not SettingScope.FILE}
)

#: The two keys that are normalised together: a model means nothing without its engine.
_PAIRED_KEYS = frozenset({"ocr_engine", "ocr_model"})


@dataclass(frozen=True)
class LoadedSettings:
    """Settings values as the app takes them, and what it had to change about them."""

    values: dict[str, Any]
    notices: tuple[SettingNotice, ...] = ()
    keys: frozenset[str] = frozenset()
    """The keys the file held, before any value was dropped: a file whose only key is
    unusable is still a saved setup, not a first start."""


def _engine_replaced(value: object, replacement: str) -> SettingNotice | None:
    """Return what was replaced about *value*, or ``None`` when the engine is still available."""
    engine = str(value).strip().lower()
    if engine in LOCAL_OCR_ENGINES:
        return None
    return SettingNotice(
        SettingNoticeKind.ENGINE_REPLACED,
        "ocr_engine",
        str(value),
        replacement,
        online=engine not in RETIRED_OCR_ENGINES,
    )


def normalize_settings(values: Mapping[str, object]) -> LoadedSettings:
    """Return *values* as the app takes them, with a notice for every value it changed.

    Only the keys *values* holds are returned, so a caller can tell a settings file that sets
    one key from one that sets none.  A value the key does not accept is left out (the default
    applies) and reported.
    """
    notices: list[SettingNotice] = []
    result: dict[str, Any] = {}

    engine = values.get("ocr_engine")
    if engine is not None:
        result["ocr_engine"] = normalize_ocr_engine(engine)
        replaced = _engine_replaced(engine, result["ocr_engine"])
        if replaced is not None:
            notices.append(replaced)
    if "ocr_model" in values:
        # A model without an engine belongs to the default engine, which is local.
        result["ocr_model"] = normalize_ocr_model(
            engine if engine is not None else DEFAULTS["ocr_engine"], values["ocr_model"]
        )

    for setting in SETTINGS:
        if setting.key in _PAIRED_KEYS or setting.key not in values:
            continue
        raw = values[setting.key]
        try:
            result[setting.key] = setting.normalize(raw)
        except TypeError, ValueError:
            notices.append(SettingNotice(SettingNoticeKind.VALUE_IGNORED, setting.key, str(raw)))
            continue
        if setting.key == "correction_notes" and len(str(raw or "").strip()) > NOTES_LIMIT:
            notices.append(SettingNotice(SettingNoticeKind.NOTES_CUT, setting.key))
    return LoadedSettings(result, tuple(notices), frozenset(values))
