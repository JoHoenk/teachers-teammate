"""The numbers of one run, and the check that they are numbers.

A record is what one model read under one preset, page by page: nothing of a page's text, no
prompt and no key, so a store of these can be read anywhere.  The only strings it holds are ids
and names — a page id, a group, a model tag, a preset — and the app's own measurement sentence
per step, which is a fixed phrase and a few numbers.

The dataclasses below are the format: a record is written and read field by field from them, so a
field is added in one place.  :func:`validate` keeps the strings few: a string anywhere a name is
not expected is refused by its path, and a name that could carry a sentence or a path is refused
by its characters.

This module is the format only; it imports nothing that reads a page.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import MISSING, dataclass, fields, is_dataclass
import re
from typing import Any, get_args, get_origin, get_type_hints

from teachers_teammate.domain.data_location import LOCAL_OCR_ENGINES
from tools.evaluation.core.formats import FormatError, check_format

from . import presets

FORMAT = 3
"""The version of a record; a field changes with a bump, never silently."""

TYPE = "ocr-run"
"""What a record file holds, so another kind of record in the store is not read as one."""

APP = "app"
"""The prompt a run sends: the app's own, the one a teacher's run sends."""

CARD = "card"
"""The prompt a run sends: the one the model's card names, where it names one."""

PROMPTS = (APP, CARD)
"""The prompts a run may send, by the id a record holds."""

TUNE = "tune"
CHECK = "check"
ALL = "all"
"""Which pages a run reads: the side a choice is made on, the side it is confirmed on, or both."""

SPLITS = (ALL, TUNE, CHECK)

MEASUREMENT_LIMIT = 120
"""How long a step's measurement sentence may be; the app's own are a phrase and a few numbers."""

LABEL_LIMIT = 80
"""How long an id or a name may be: a model tag, a preset, a machine, a page id."""

_NAME = re.compile(r"[^{}\"\\\n\r\t]*")
"""A header's name: a tag (``hf.co/…``), a licence (``Modified OpenRAIL-M``); no quote or brace."""

_ID = re.compile(r"[\w.+-]*")
"""A page's id, group or writer, a step's name, a part of ``auto_identity``: one word-like run."""

_MEASUREMENT = re.compile(r"[\w .,\u00b0\u2192()%<>=+\u00d7-]*")
"""A step's measurement: words, numbers and degrees, arrows and the app's few signs, no path or quote."""

_SETTING = re.compile(r"[a-z][a-z0-9_]*")
"""A detector setting's name, as the app spells its fields."""


@dataclass(frozen=True)
class Flags:
    """What went wrong with an answer besides the words it got wrong."""

    empty: bool = False
    cut_off: bool = False
    loop: bool = False
    long: bool = False

    def names(self) -> tuple[str, ...]:
        """Return the flags that are set, by the name a record writes."""
        return tuple(name for name in FLAGS if getattr(self, name))


FLAGS = tuple(field.name for field in fields(Flags))
"""The failure flags a page may carry."""


@dataclass(frozen=True)
class StepTrace:
    """One step of the preparation: what it is called, whether it acted, what it measured."""

    name: str
    acted: bool
    measurement: str = ""


@dataclass(frozen=True)
class PageNumbers:
    """One page, read once, scored against its transcription."""

    page: str
    group: str
    split: str
    cer: float
    wer: float
    bag_wer: float
    kept: float | None
    words: int
    """The transcription's words and punctuation marks, the illegible marks not counted."""
    characters: int
    """The transcription's characters the CER is a share of."""
    writer: str = ""
    """The neutral writer id from ``pages.jsonl``: the pages one person wrote are one cluster."""
    flags: tuple[str, ...] = ()
    seconds: float = 0.0
    answer_tokens: int = 0
    steps: tuple[StepTrace, ...] = ()


@dataclass(frozen=True)
class Header:
    """What the run was: the model, the ask, the set and the machine."""

    model: str
    """The Ollama tag, or ``reader`` for the built-in reader."""
    engine: str
    """``ollama`` or ``ppocr``, the app's engine ids."""
    prompt: str
    """``app`` for the app's own prompt, ``card`` for the model's card's."""
    preset: str
    """The preset's name: ``raw``, ``R``, ``R-light``, …."""
    split: str = ALL
    """Which pages of the set the run read: both sides, ``tune`` or ``check``."""
    digest: str = ""
    """The model's digest, so a re-pointed tag is not read as the same model."""
    auto_identity: tuple[str, ...] = ()
    """``auto_identity``: the preparation's version, its output colour and the page models."""
    detector: tuple[tuple[str, float], ...] = ()
    """The detector's numbers, for the built-in reader."""
    set_version: str = ""
    """The version of the page set, so two campaigns cannot mix two sets."""
    pages: int = 0
    language: str = ""
    """The languages the pages were asked in, comma-separated."""
    machine: str = ""
    cpu_only: bool = False
    revision: str = ""
    """The app's commit, with ``+dirty`` when the tree it was built from had changes."""
    licence: str = ""
    """The licence the model's weights come under, as the catalog or a survey claims it."""
    memory_mb: int = 0
    """The model's memory as the server reported it, or the reader's peak memory."""


@dataclass(frozen=True)
class Totals:
    """A set of pages in a few figures: a run's whole, or one row of a report."""

    pages: int
    cer: float
    """Weighted by the characters of each page, as the rate is a share of them."""
    wer: float
    """Weighted by the words of each page; so is ``bag_wer``."""
    bag_wer: float
    kept: float | None
    """The mean over the pages that hold a misspelt word; ``None`` when none does."""
    words: int
    characters: int
    failures: int
    seconds: float

    @classmethod
    def of(cls, pages: Sequence[PageNumbers]) -> Totals:
        """Return the figures of *pages*."""
        words = sum(page.words for page in pages)
        characters = sum(page.characters for page in pages)
        kept = [page.kept for page in pages if page.kept is not None]
        return cls(
            pages=len(pages),
            cer=_weighted([(page.characters, page.cer) for page in pages]),
            wer=_weighted([(page.words, page.wer) for page in pages]),
            bag_wer=_weighted([(page.words, page.bag_wer) for page in pages]),
            kept=sum(kept) / len(kept) if kept else None,
            words=words,
            characters=characters,
            failures=sum(1 for page in pages if page.flags),
            seconds=sum(page.seconds for page in pages),
        )


def _weighted(rates: Sequence[tuple[int, float]]) -> float:
    """Return the mean of ``(weight, rate)`` pairs, each rate counted by its weight."""
    whole = sum(weight for weight, _rate in rates)
    return sum(weight * rate for weight, rate in rates) / whole if whole else 0.0


@dataclass(frozen=True)
class RunRecord:
    """One run: its header, its pages and their totals."""

    header: Header
    pages: tuple[PageNumbers, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable record."""
        return {
            "format": FORMAT,
            "type": TYPE,
            "header": _encoded(self.header),
            "pages": [_encoded(page) for page in self.pages],
            "totals": _encoded(Totals.of(self.pages)),
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> RunRecord:
        """Build a record from :meth:`to_dict` output.

        Raises:
            FormatError: if *record* is not an ocr run of this format.
        """
        check_format(record, FORMAT, "ocr run")
        if record.get("type") != TYPE:
            raise FormatError(f"an ocr run holds {TYPE!r}, not {record.get('type')!r}")
        return cls(
            header=_decoded(Header, record.get("header"), "header"),
            pages=tuple(
                _decoded(PageNumbers, page, f"pages[{index}]")
                for index, page in enumerate(record.get("pages") or ())
            ),
        )


_PAIRS = tuple[tuple[str, float], ...]
"""A field of named numbers, which a record writes as an object: the detector's settings."""


def _encoded(value: Any) -> dict[str, Any]:
    """Return the dataclass *value* as a record writes it, field by field."""
    hints = get_type_hints(type(value))
    found: dict[str, Any] = {}
    for field in fields(value):
        item = getattr(value, field.name)
        if hints[field.name] == _PAIRS:
            found[field.name] = dict(item)
        elif isinstance(item, tuple):
            found[field.name] = [_encoded(one) if is_dataclass(one) else one for one in item]
        else:
            found[field.name] = item
    return found


def _decoded[T](cls: type[T], data: Any, where: str) -> T:
    """Return the dataclass *cls* read from *data*, a field it lacks taking its default.

    Raises:
        FormatError: when *data* is not an object or lacks a field that has no default.
    """
    if not isinstance(data, Mapping):
        raise FormatError(f"{where}: not an object")
    hints = get_type_hints(cls)
    values: dict[str, Any] = {}
    for field in fields(cls):  # ty: ignore[invalid-argument-type]
        if field.name in data:
            values[field.name] = _value(
                hints[field.name], data[field.name], f"{where}.{field.name}"
            )
        elif field.default is MISSING and field.default_factory is MISSING:
            raise FormatError(f"{where}: no {field.name}")
    return cls(**values)


def _value(hint: Any, value: Any, where: str) -> Any:
    """Return one field's *value* in the type *hint* names."""
    if hint == _PAIRS:
        return tuple(sorted((str(key), float(number)) for key, number in value.items()))
    if get_origin(hint) is tuple:
        item = get_args(hint)[0]
        if is_dataclass(item):
            return tuple(
                _decoded(item, one, f"{where}[{index}]") for index, one in enumerate(value)
            )
        return tuple(item(one) for one in value)
    if hint == float | None:
        return None if value is None else float(value)
    return hint(value)


def _names(cls: Any) -> frozenset[str]:
    """Return the fields of the dataclass *cls*: the only ones its object in a record may hold."""
    return frozenset(field.name for field in fields(cls))


_FIELDS = frozenset({"format", "type", "header", "pages", "totals"})
"""The fields a record holds, top to bottom; a field nobody writes is refused by name."""


def validate(record: Mapping[str, Any]) -> None:
    """Refuse *record* when its shape or one of its strings is not one a run writes.

    Raises:
        FormatError: naming the path of the field that is not a number or a known name.
    """
    _shape(record)
    _strings(record, "")


def _shape(record: Mapping[str, Any]) -> None:
    """Refuse a record whose objects hold fields :class:`RunRecord` does not write."""
    _fields(record, _FIELDS, "the record")
    missing = _FIELDS - set(record)
    if missing:
        raise FormatError(f"the record: no {', '.join(sorted(missing))}")
    for name, cls in (("header", Header), ("totals", Totals)):
        if not isinstance(record[name], Mapping):
            raise FormatError(f"{name}: not an object")
        _fields(record[name], _names(cls), name)
    detector = record["header"].get("detector") or {}
    if not isinstance(detector, Mapping):
        raise FormatError("header.detector: not an object")
    for key in detector:
        if not isinstance(key, str) or not _SETTING.fullmatch(key):
            raise FormatError(f"header.detector: {str(key)[:20]!r} is not a setting's name")
    if not isinstance(record["pages"], list):
        raise FormatError("pages: not a list")
    for index, page in enumerate(record["pages"]):
        if not isinstance(page, Mapping):
            raise FormatError(f"pages[{index}]: not an object")
        _fields(page, _names(PageNumbers), f"pages[{index}]")
        for number, step in enumerate(page.get("steps") or ()):
            if not isinstance(step, Mapping):
                raise FormatError(f"pages[{index}].steps[{number}]: not an object")
            _fields(step, _names(StepTrace), f"pages[{index}].steps[{number}]")


def _fields(found: Mapping[str, Any], expected: frozenset[str], where: str) -> None:
    """Refuse a field the format does not have, naming it."""
    unknown = set(found) - expected
    if unknown:
        raise FormatError(f"{where}: unknown field(s) {sorted(unknown)}")


def _strings(value: Any, where: str) -> None:
    """Refuse every string at a path a record does not hold one at, or that is not a name."""
    if isinstance(value, str):
        if not _allowed(where, value):
            raise FormatError(f"{where}: a record holds no such string here ({value[:20]!r}…)")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _strings(item, f"{where}.{key}" if where else str(key))
        return
    if isinstance(value, Sequence):
        for index, item in enumerate(value):
            _strings(item, f"{where}[{index}]")


_HEADER_LABEL = re.compile(r"header\.(\w+)")
_AUTO_IDENTITY = re.compile(r"header\.auto_identity\[\d+\]")
_PAGE_LABEL = re.compile(r"pages\[\d+\]\.(?:page|group|writer)")
_PAGE_SPLIT = re.compile(r"pages\[\d+\]\.split")
_FLAG = re.compile(r"pages\[\d+\]\.flags\[\d+\]")
_STEP_NAME = re.compile(r"pages\[\d+\]\.steps\[\d+\]\.name")
_STEP_MEASUREMENT = re.compile(r"pages\[\d+\]\.steps\[\d+\]\.measurement")


def _allowed(path: str, value: str) -> bool:
    """Whether a string may sit at *path*: an id, a name, or a step's measurement."""
    if path == "type":
        return value == TYPE
    if _FLAG.fullmatch(path):
        return value in FLAGS
    if _PAGE_SPLIT.fullmatch(path):
        return value in (TUNE, CHECK)
    if _STEP_MEASUREMENT.fullmatch(path):
        return len(value) <= MEASUREMENT_LIMIT and bool(_MEASUREMENT.fullmatch(value))
    if _AUTO_IDENTITY.fullmatch(path) or _PAGE_LABEL.fullmatch(path) or _STEP_NAME.fullmatch(path):
        return _label(value, _ID)
    header = _HEADER_LABEL.fullmatch(path)
    if header and header.group(1) in _names(Header):
        return _header_label(header.group(1), value)
    return False


def _label(value: str, form: re.Pattern[str]) -> bool:
    """Whether *value* is short enough to be a name, and of its *form*."""
    return len(value) <= LABEL_LIMIT and bool(form.fullmatch(value))


def _header_label(name: str, value: str) -> bool:
    """Whether *value* may be the header's *name*: a name, and one the app knows where it can."""
    if name == "prompt":
        return value in PROMPTS
    if name == "engine":
        return value in LOCAL_OCR_ENGINES
    if name == "split":
        return value in SPLITS
    if name == "preset":
        return _is_preset(value)
    return _label(value, _NAME)


def _is_preset(value: str) -> bool:
    """Whether *value* names a preset a run can be given."""
    try:
        presets.parse(value)
    except ValueError:
        return False
    return True
