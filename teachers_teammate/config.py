"""Pipeline configuration: the :class:`Config` dataclass and TOML file helpers.

This module is the single place a :class:`Config` is assembled.  Both the CLI (argument
parsing → :meth:`Config.from_settings`) and the GUI
(:class:`~teachers_teammate.gui._config_panel.ConfigPanel` → :meth:`Config.from_settings`)
build one and hand it to :class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline`.
Which keys exist, what they default to and what they accept is
:mod:`teachers_teammate.settings_schema`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tomllib
from typing import Any

from ._model_defaults import default_model_for_task
from .atomic_file import write_text_atomic
from .domain.anonymization import (
    SECONDARY_MODEL_AUTO,
    AnonymizerConfig,
    resolve_patterns,
)
from .domain.correction_policy import CorrectionOutput
from .domain.model_catalog import (
    OCR_TEMPERATURE,
    HardwareProfileSetting,
    RequestProfile,
    request_profile,
    review_temperature,
)
from .domain.output_files import DocxFormat
from .domain.pdf_text_layer import PdfTextLayerMode, normalize_pdf_text_layer
from .domain.setting_values import ContextSetting, PreprocessMethod
from .domain.stages import Stage
from .exceptions import ConfigFileParseError
from .settings_schema import (
    DEFAULTS,
    LoadedSettings,
    LogDetail,
    normalize_ocr_engine,
    normalize_ocr_model,
    normalize_preprocess_method,
    normalize_settings,
)


@dataclass(frozen=True)
class OcrConfig:
    """The OCR slice of the runtime configuration.

    Holds only the fields that vary per OCR run (engine/model/
    preprocessing/temperature).  Shared fields the OCR stage *reads* but that are
    also consumed by correction/evaluation — ``language``, ``ollama_url``,
    ``ocr_timeout`` — stay on :class:`Config`.  The pipeline builds it once per run.
    """

    engine: str = DEFAULTS["ocr_engine"]
    """An :class:`~teachers_teammate.domain.data_location.OcrEngine` for a run."""
    model: str = DEFAULTS["ocr_model"]
    preprocess_method: PreprocessMethod = DEFAULTS["preprocess_method"]
    temperature: float = OCR_TEMPERATURE
    """Fixed for the app."""
    pdf_render_dpi: int = DEFAULTS["pdf_render_dpi"]
    pdf_text_layer: PdfTextLayerMode = DEFAULTS["pdf_text_layer"]
    """Whether a PDF's embedded text may replace OCR (``auto``/``always``/``never``)."""

    def __post_init__(self) -> None:
        # Programmatic callers and GUI settings bypass argparse ``choices``; fold
        # case/whitespace and fall back to "auto" for anything that is not a mode.
        object.__setattr__(
            self, "preprocess_method", normalize_preprocess_method(self.preprocess_method)
        )
        object.__setattr__(self, "pdf_render_dpi", int(self.pdf_render_dpi))
        object.__setattr__(self, "pdf_text_layer", normalize_pdf_text_layer(self.pdf_text_layer))

    @classmethod
    def from_settings(cls, values: Mapping[str, object]) -> OcrConfig:
        """Build an :class:`OcrConfig` from a flat settings dict (``ocr.toml`` key names).

        Keys that are missing fall back to :data:`DEFAULTS`.  This is the single place the
        GUI's flat settings (``ocr_engine``, ``preprocess_method``, …) map onto the fields.

        The engine and its model are normalised here and not in :meth:`__post_init__`, so a
        field a caller sets is taken as given.
        """

        def get(key: str) -> Any:
            return values.get(key, DEFAULTS[key])

        engine = get("ocr_engine")
        return cls(
            engine=normalize_ocr_engine(engine),
            model=normalize_ocr_model(engine, get("ocr_model")),
            preprocess_method=get("preprocess_method"),
            pdf_render_dpi=get("pdf_render_dpi"),
            pdf_text_layer=get("pdf_text_layer"),
        )


@dataclass(frozen=True)
class Config:
    """All runtime configuration, derived from CLI arguments or the GUI."""

    # ── Input ────────────────────────────────────────────────────────────────
    input_dir: Path
    output_dir: Path
    recursive: bool
    debug: bool

    # ── OCR Parameters ────────────────────────────────────────────────────────
    ocr: OcrConfig
    language: str
    ollama_url: str

    # ── Correction ────────────────────────────────────────────────────────────
    correction_enabled: bool
    correction_provider: str
    correction_model: str
    correction_notes: str = DEFAULTS["correction_notes"]
    correction_level: str = DEFAULTS["correction_level"]
    explanation_language: str = DEFAULTS["explanation_language"]
    correction_output: CorrectionOutput = DEFAULTS["correction_output"]
    anonymization_enabled: bool = DEFAULTS["anonymization_enabled"]

    # ── Evaluation ────────────────────────────────────────────────────────────
    evaluation_enabled: bool = DEFAULTS["evaluation_enabled"]
    evaluate_provider: str = DEFAULTS["evaluate_provider"]
    evaluate_model: str = DEFAULTS["evaluate_model"]
    evaluate_prompt: str = DEFAULTS["evaluate_prompt"]

    # ── Output ───────────────────────────────────────────────────────────────
    docx_format: DocxFormat = DEFAULTS["docx_format"]
    docx_enabled: bool = DEFAULTS["docx_enabled"]
    text_enabled: bool = DEFAULTS["text_enabled"]
    text_anonymized_enabled: bool = DEFAULTS["text_anonymized_enabled"]

    # ── Hardware ─────────────────────────────────────────────────────────────
    # Detect this computer, or a profile chosen by hand.  Only the recommendations and the
    # run-start memory warning read it.
    hardware_profile: HardwareProfileSetting = DEFAULTS["hardware_profile"]

    # ── Context window ───────────────────────────────────────────────────────
    # "auto" (as large as the graphics card holds at full speed) or a fixed window in tokens.
    # An unknown value falls back to "auto" with a warning, as hardware_profile does.
    proofreading_context: ContextSetting = DEFAULTS["proofreading_context"]

    # ── Text recognition ─────────────────────────────────────────────────────
    # Deliberately not part of OcrConfig: that slice says *how* pages are read (and
    # fingerprints the cached text); this says *whether* scans are read at all.  Off, a scan
    # is skipped; text files and PDFs with a text layer are used as they are.
    recognition_enabled: bool = DEFAULTS["recognition_enabled"]

    # ── Run ──────────────────────────────────────────────────────────────────
    # Keep the computer awake while a run is going.  Not in any fingerprint: it changes how the
    # run is carried out, not what it produces.
    keep_awake: bool = DEFAULTS["keep_awake"]

    # ── Start ────────────────────────────────────────────────────────────────
    # Whether the app asks GitHub for a newer version when it starts.  The GUI reads it; no
    # pipeline step does, and it is in no fingerprint.
    check_for_updates: bool = DEFAULTS["check_for_updates"]
    # How many days the results of earlier runs are kept; 0 = no expiry.  The window applies it
    # once at start and no run reads it.
    results_keep_days: int = DEFAULTS["results_keep_days"]
    # How much of what the app knows goes into its log file.  The log applies it; no pipeline
    # step reads it, and it is in no fingerprint.
    log_detail: LogDetail = DEFAULTS["log_detail"]

    # ── Anonymizer ───────────────────────────────────────────────────────────
    # None = derive the spaCy model from the document language ("Auto")
    anonymizer_primary_model: str | None = None
    # SECONDARY_MODEL_AUTO (the default), SECONDARY_MODEL_NONE / None, or a model name
    anonymizer_secondary_model: str | None = SECONDARY_MODEL_AUTO
    # None (or the earlier built-in list) = current DEFAULT_PATTERNS from the anonymizer module;
    # [] = no regex patterns
    anonymizer_patterns: list[tuple[str, str]] | None = None
    # Texts the anonymizer hides wherever they stand, and texts it leaves as they are, whatever
    # the models and patterns find.
    anonymizer_hide: list[str] | None = None
    anonymizer_keep: list[str] | None = None

    # ── Internal ─────────────────────────────────────────────────────────────
    ocr_timeout: int = DEFAULTS["ocr_timeout"]

    @classmethod
    def from_settings(
        cls,
        values: Mapping[str, object],
        *,
        input_dir: Path,
        output_dir: Path,
        recursive: bool,
        ocr: OcrConfig | None = None,
    ) -> Config:
        """Build a :class:`Config` from a flat settings dict (``ocr.toml`` key names).

        The one place a :class:`Config` is assembled: the GUI and the CLI both come through
        here, so a key means the same in both.  Keys *values* does not hold
        fall back to :data:`DEFAULTS`; the folders and ``recursive`` are the caller's, because
        they are what a batch is about rather than a setting of the app.
        """
        settings = normalize_settings(values).values

        def get(key: str) -> Any:
            return settings.get(key, DEFAULTS[key])

        return cls(
            input_dir=input_dir,
            output_dir=output_dir,
            recursive=recursive,
            ocr=ocr if ocr is not None else OcrConfig.from_settings(settings),
            debug=get("debug"),
            language=get("language"),
            ollama_url=get("ollama_url"),
            ocr_timeout=get("ocr_timeout"),
            recognition_enabled=get("recognition_enabled"),
            keep_awake=get("keep_awake"),
            check_for_updates=get("check_for_updates"),
            results_keep_days=get("results_keep_days"),
            log_detail=get("log_detail"),
            correction_enabled=get("correction_enabled"),
            anonymization_enabled=get("anonymization_enabled"),
            correction_provider=get("correction_provider"),
            correction_model=get("correction_model"),
            correction_notes=get("correction_notes"),
            correction_level=get("correction_level"),
            explanation_language=get("explanation_language"),
            correction_output=get("correction_output"),
            evaluation_enabled=get("evaluation_enabled"),
            evaluate_provider=get("evaluate_provider"),
            evaluate_model=get("evaluate_model"),
            evaluate_prompt=get("evaluate_prompt"),
            docx_format=get("docx_format"),
            docx_enabled=get("docx_enabled"),
            text_enabled=get("text_enabled"),
            text_anonymized_enabled=get("text_anonymized_enabled"),
            anonymizer_primary_model=get("anonymizer_primary_model"),
            anonymizer_secondary_model=get("anonymizer_secondary_model"),
            anonymizer_patterns=get("anonymizer_patterns"),
            anonymizer_hide=get("anonymizer_hide"),
            anonymizer_keep=get("anonymizer_keep"),
            hardware_profile=get("hardware_profile"),
            proofreading_context=get("proofreading_context"),
        )

    @property
    def writes_text_files(self) -> bool:
        """Whether a run saves text files (which the next run must not read as inputs)."""
        return self.text_enabled or self.text_anonymized_enabled

    @property
    def writes_output_files(self) -> bool:
        """Whether a run saves any file into ``output_dir`` (not just the cache)."""
        return self.docx_enabled or self.writes_text_files

    @property
    def effective_correction_model(self) -> str:
        if self.correction_model:
            return self.correction_model
        return default_model_for_task(self.correction_provider, Stage.CORRECTION)

    @property
    def effective_evaluate_model(self) -> str:
        if self.evaluate_model:
            return self.evaluate_model
        return default_model_for_task(self.evaluate_provider, Stage.EVALUATION)

    @property
    def correction_profile(self) -> RequestProfile:
        """Return how the proofreading requests are built: the compact rule and the temperatures.

        The model's own profile, resolved for the provider.  Teachers set no temperature; step ①
        runs at the value measured for it and steps ② and ③ at the one the model's maker
        recommends (see :data:`~.domain.model_catalog.STEP_ONE_TEMPERATURES` and
        :data:`~.domain.model_catalog.MAKER_TEMPERATURES`).
        """
        return request_profile(self.effective_correction_model, self.correction_provider)

    @property
    def correction_temperature(self) -> float:
        """Return the temperature the online model itself is built with.

        Every step of an online profile is sent at this value
        (:data:`~.domain.model_catalog.DEFAULT_CORRECTION_TEMPERATURE`), so it is the model's own
        temperature and not a request's.  A local model is asked per step instead
        (``correction_profile``), because Ollama takes the temperature with each request.
        """
        return self.correction_profile.review_temperature

    @property
    def evaluate_think(self) -> bool | str:
        """Return how the content review asks a reasoning model to think (see ``RequestProfile``)."""
        return request_profile(self.effective_evaluate_model, self.evaluate_provider).think

    @property
    def evaluate_temperature(self) -> float:
        """Return the temperature the content review is sent at."""
        return review_temperature(self.effective_evaluate_model, self.evaluate_provider)

    @property
    def anonymizer_config(self) -> AnonymizerConfig:
        """Return the anonymizer settings this config asks for, models unresolved.

        The one place the stored settings become an :class:`AnonymizerConfig`, so a run, a
        preview and the status box all start from the same settings.
        """
        return AnonymizerConfig(
            primary_model=self.anonymizer_primary_model,
            secondary_model=self.anonymizer_secondary_model,
            patterns=resolve_patterns(self.anonymizer_patterns),
            hide=tuple(self.anonymizer_hide or ()),
            keep=tuple(self.anonymizer_keep or ()),
        )


def override_root() -> Path | None:
    """Return the folder ``TEACHERS_TEAMMATE_TMPDIR`` names, or ``None`` when it is not set.

    Set, that folder holds what the app would otherwise spread over the cache, the config and
    the data folder: a test, the smoke test and the app walkthrough keep everything of theirs
    in one temporary folder that way.
    """
    override = os.environ.get("TEACHERS_TEAMMATE_TMPDIR", "").strip()
    return Path(override) if override else None


#: The organisation and application the GUI's settings are filed under in the native store.
GUI_STORE_ORGANIZATION = "TeachersTeammate"
GUI_STORE_APPLICATION = "TeachersTeammate"

#: The file name the GUI's settings take under the override folder.
GUI_STORE_FILE_NAME = "gui-settings.ini"


@dataclass(frozen=True)
class StoreLocation:
    """Where the GUI's settings store lives: the native store, or an INI file.

    The location as data, so the windows and the credential store (which keeps its plain-text
    fallback there) cannot drift apart.
    """

    organization: str
    application: str
    ini_file: Path | None = None
    """The INI file to use instead of the native store; ``None`` names the native store."""


def gui_store_location() -> StoreLocation:
    """Return where the GUI keeps its settings: an INI file in the override folder, else native.

    An INI file there, because the native store writes where the platform says (the registry on
    Windows), which no folder of a walkthrough could hold.
    """
    root = override_root()
    return StoreLocation(
        organization=GUI_STORE_ORGANIZATION,
        application=GUI_STORE_APPLICATION,
        ini_file=None if root is None else root / GUI_STORE_FILE_NAME,
    )


def _read_toml(path: Path) -> dict[str, Any]:
    """Return the parsed TOML of *path*, raising :exc:`ConfigFileParseError` on failure."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except OSError as exc:
        raise ConfigFileParseError(f"Cannot read config file '{path}': {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigFileParseError(
            f"Could not read config file {path}: it is not UTF-8 ({exc.reason} at byte {exc.start})"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigFileParseError(f"Could not parse config file {path}: {exc}") from exc


#: The settings the installer may write before the app has ever started.  A settings file that
#: holds nothing else is not a teacher's saved setup: the first start (the Setup Assistant) still
#: runs, and carries these values over.
INSTALLER_SETTING_KEYS: frozenset[str] = frozenset({"check_for_updates"})


def load_config_file(path: Path) -> LoadedSettings:
    """Parse *path* as TOML and return the settings it holds, and what was changed about them.

    Keys the schema does not know are dropped; a value a key does not accept is left out (the
    default applies) and reported in ``notices``, so a caller can say so instead of changing a
    teacher's setting silently.

    Args:
        path: Path to a TOML file.

    Returns:
        The recognised values, suitable for :meth:`Config.from_settings` and for
        :meth:`argparse.ArgumentParser.set_defaults`.
    """
    return normalize_settings(_read_toml(path))


_TOML_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _toml_escape(value: str) -> str:
    """Return *value* escaped for a single-line TOML basic string."""
    out: list[str] = []
    for char in value:
        escape = _TOML_ESCAPES.get(char)
        if escape is not None:
            out.append(escape)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        else:
            out.append(char)
    return "".join(out)


def _survives_block_string(value: str) -> bool:
    """Return whether *value* reads back unchanged from a multi-line basic string.

    A block string trims the newline after its opening delimiter and takes the one before its
    closing delimiter as content, so only a value of several lines without a backslash, a
    carriage return, a control character or a trailing quote can use it.
    """
    if "\n" not in value or value.endswith('"') or '"""' in value:
        return False
    for char in value:
        if char in "\n\t":
            continue
        if char in "\\\r" or ord(char) < 0x20 or ord(char) == 0x7F:
            return False
    return True


def _toml_string(value: str) -> str:
    """Return *value* as a TOML string that reads back as *value*.

    A value of several lines keeps the block form, so a prompt stays readable in ``ocr.toml``;
    anything a block cannot carry is written as one escaped line.
    """
    if _survives_block_string(value):
        return f'"""\n{value}"""'
    return f'"{_toml_escape(value)}"'


def _toml_value(value: object) -> str:
    """Return *value* as a TOML value.

    Raises:
        TypeError: the value is of a type a settings file cannot hold.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"a settings file cannot hold a {type(value).__name__}")


def dump_config_file(values: dict) -> str:
    """Serialize a settings *values* dict to ``ocr.toml`` text.

    Inverse of :func:`load_config_file`: the output round-trips back through it.  ``None`` values
    are skipped.  Keys are written in iteration order.

    Raises:
        TypeError: a value is of a type a settings file cannot hold (the schema produces none).
    """
    lines = ["# ocr.toml — Teacher's Teammate configuration\n"]
    for key, val in values.items():
        if val is None:
            continue
        try:
            lines.append(f"{key} = {_toml_value(val)}\n")
        except TypeError as exc:
            raise TypeError(f"{key}: {exc}") from exc
    return "".join(lines)


def save_settings_file(path: Path, values: dict) -> None:
    """Write *values* to *path* in one step, so a crash never leaves half a file behind.

    Raises:
        OSError: the file cannot be written at all.
    """
    write_text_atomic(path, dump_config_file(values))


def broken_settings_copy(path: Path) -> Path | None:
    """Copy the settings file at *path* that cannot be read, and return the copy.

    The copy is ``<name>.broken``, or the next free ``<name>.broken.<n>``: an existing copy is
    never overwritten, because the first broken file is the one worth keeping.  ``None`` when
    there is no file to copy.
    """
    if not path.is_file():
        return None
    target = path.with_name(f"{path.name}.broken")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.broken.{counter}")
        counter += 1
    shutil.copy2(path, target)
    return target
