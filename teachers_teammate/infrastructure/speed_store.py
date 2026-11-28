"""Where measured speeds are kept: two JSON files in the data folder.

``measurements.json`` holds what the speed test found, one record per model.  A measurement is
only worth something together with what it was measured on, so the key carries the server, the
model tag, the model's **digest**, the Ollama **version** and the graphics card.  A tag is a name
that can be re-pointed and Ollama counts tokens differently between versions, so a record whose
digest, version or card no longer matches is kept and shown with its date, but it is never used
to estimate a run: it describes a different thing.

``samples.json`` holds what real runs measured, one sample per page or per document
(:class:`RunSample`).  Those are a rolling window of the last :data:`KEEP_SAMPLES` samples per
key, step and mode.  Their key is only the server, the tag, the step and the mode, without the
digest: a sample is one document's worth of evidence, so a model that was updated or a card that
was swapped replaces the evidence within a class set, and asking the server what it is running
before every page would cost a request per page.

Both files are written atomically (a temporary file, then a replace) and guarded by a lock,
because the speed test runs on a background thread while the window may be reading the same file.
A file with a schema number this version does not know is ignored rather than treated as an
error: a newer version of the app may have written it, and losing a measurement is better than
refusing to start.  Before this version writes over such a file it moves it to ``<name>.bak``,
so an older app never destroys what a newer one measured.

Both files are read once per change: the parsed content is kept together with the file's
modification time and size, so a model list that asks for fifty tags does not parse the file
fifty times, and a write by another process (a run's worker) is still seen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import shutil
import threading
from typing import Any, cast

from ..atomic_file import write_text_atomic
from ..domain.ollama_address import normalize_ollama_url
from ..domain.performance_estimate import ModelSpeed, SpeedStep, StepOutcome
from .storage_root import resolve_data_folder

_logger = logging.getLogger(__name__)

#: The schema of the file.  A file with another number is ignored, not migrated: the numbers in
#: it were measured by a version whose requests may differ.
SCHEMA = 1

#: How many samples of one key, step and mode are kept.  A class set is 30 documents, so the
#: window covers the last two thirds of one; more would let a model that was updated long ago
#: keep influencing the median.
KEEP_SAMPLES = 20


@dataclass(frozen=True)
class SpeedKey:
    """What a measurement belongs to: the server, the model and the machine it ran on."""

    server: str
    """The normalised Ollama URL."""
    tag: str
    """The model tag as the app asks for it."""
    digest: str = ""
    """The model's digest; ``""`` when the server did not report one."""
    ollama_version: str = ""
    """The server's version; ``""`` when it did not say."""
    gpu: str = ""
    """The graphics card's name for a local server, ``""`` for a remote one."""


@dataclass(frozen=True)
class StepRecord:
    """What one step of a measurement found."""

    step: SpeedStep
    outcome: StepOutcome
    speed: ModelSpeed | None = None
    message: str = ""


@dataclass(frozen=True)
class SpeedRecord:
    """One run of the speed test, as it is stored."""

    key: SpeedKey
    measured_at: str
    """When it was measured, as an ISO 8601 timestamp in UTC."""
    steps: tuple[StepRecord, ...] = ()
    on_battery: bool | None = None
    """Whether the computer ran on battery power, which slows the card down."""
    shared: bool = False
    """Another model was loaded when the test started, so the times may be too long."""
    swaps: bool = False
    """The models evicted each other, so every file pays both loads."""

    def step(self, step: SpeedStep) -> StepRecord | None:
        """Return the record of *step*, or ``None`` when the test did not measure it."""
        return next((item for item in self.steps if item.step is step), None)

    def measured(self, step: SpeedStep) -> ModelSpeed | None:
        """Return the speed *step* measured, or ``None`` when it was not measured."""
        record = self.step(step)
        return None if record is None else record.speed


@dataclass(frozen=True)
class StoredSpeed:
    """A stored measurement, and whether it still describes this server and this machine."""

    record: SpeedRecord
    current: bool
    """``False`` when the digest, the server version or the card changed since the measurement."""


def _newest(record: SpeedRecord) -> str:
    """Return the stamp of *record*, for picking the newest of several measurements."""
    return record.measured_at


def now_stamp() -> str:
    """Return the current time as the ISO 8601 stamp a record carries."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SampleKey:
    """What a sample of a real run belongs to: the server, the model, the step and the mode."""

    server: str
    """The normalised Ollama URL."""
    tag: str
    """The model tag as the app asked for it."""
    step: SpeedStep
    mode: str = ""
    """``"exchange"`` or ``"text"`` for proofreading, ``""`` for the other steps.

    The mode is part of the key because it decides how long the answer is: the exchange answers
    with the text and its explanations, a text-mode answer (corrections only) is about the
    text's length.  Mixing the two would make the answer length meaningless.
    """


@dataclass(frozen=True)
class RunSample:
    """One step's requests for one page or one document, as numbers only.

    Nothing here is text: no page, no file name, no path.  The file can be read, copied or sent
    along without exposing a student's essay.
    """

    read_tps: float = 0.0
    """Tokens of the request the server read per second."""
    write_tps: float = 0.0
    """Tokens of the answer the server wrote per second."""
    load_s: float = 0.0
    """Seconds the server spent loading the model; ``0.0`` when it was already in memory."""
    image_tokens: int = 0
    """Text recognition: tokens of the page image, as the request counted them."""
    answer_tokens: int = 0
    """Tokens of the answer."""
    words: int = 0
    """Words of the text that went through: the page's text, or the text that was proofread."""
    pieces: int = 1
    """Requests the step took: a long text is proofread in several pieces."""
    at: str = ""
    """When the sample was taken, as an ISO 8601 timestamp in UTC."""


class SampleStore:
    """Reads and writes the samples file; see the module docstring."""

    def __init__(self, path: Path | None = None) -> None:
        """Create the store.

        Args:
            path: The file to use; ``<data root>/speed/samples.json`` when ``None``.
        """
        self._path = path
        self._lock = threading.Lock()
        self._memo: tuple[_Stamp | None, dict[SampleKey, list[RunSample]]] | None = None

    @property
    def path(self) -> Path:
        """Return the file the store reads and writes."""
        if self._path is None:
            self._path = resolve_data_folder("speed") / "samples.json"
        return self._path

    def samples(self, server: str, tag: str, step: SpeedStep) -> dict[str, tuple[RunSample, ...]]:
        """Return the samples of *tag* for *step*, grouped by mode, newest first.

        Grouped by mode because the mode decides how long an answer is: a review answer repeats
        the text and explains every change, a text-mode answer is about the text's length, so
        the two cannot be averaged into one answer length.

        Args:
            server: The normalised Ollama URL.
            tag: The model tag.
            step: The step the samples belong to.
        """
        with self._lock:
            stored = self._read()
        grouped: dict[str, tuple[RunSample, ...]] = {}
        for key, samples in stored.items():
            if key.server == server and key.tag == tag and key.step is step:
                grouped[key.mode] = tuple(samples)
        return grouped

    def server_samples(self, server: str) -> dict[SampleKey, tuple[RunSample, ...]]:
        """Return every sample of *server*, keyed by what it belongs to.

        One read of the file answers for every model on the server, which is what a caller
        that compares several models needs.
        """
        with self._lock:
            stored = self._read()
        return {key: tuple(samples) for key, samples in stored.items() if key.server == server}

    def add(self, key: SampleKey, sample: RunSample) -> None:
        """Store *sample* under *key*, dropping the oldest beyond :data:`KEEP_SAMPLES`."""
        with self._lock:
            stored = self._read()
            kept = [*stored.get(key, ()), sample][-KEEP_SAMPLES:]
            stored[key] = kept
            self._write(stored)

    # ── the file ───────────────────────────────────────────────────────────

    def _read(self) -> dict[SampleKey, list[RunSample]]:
        """Return the samples in the file; ``{}`` when it is missing, unreadable or unknown.

        The parsed file is kept with its stamp, so an unchanged file is parsed once.
        """
        stamp = _stamp(self.path)
        if self._memo is not None and self._memo[0] == stamp:
            return dict(self._memo[1])
        stored = self._parse()
        self._memo = (stamp, stored)
        return dict(stored)

    def _parse(self) -> dict[SampleKey, list[RunSample]]:
        """Parse the file, without the memo."""
        path = self.path
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except FileNotFoundError:
            _logger.debug("speed store: no samples file at %s yet", path)
            return {}
        except (OSError, ValueError) as exc:
            _logger.warning("speed store: cannot read %s: %s", path, exc)
            return {}
        data = _as_object(raw)
        if data is None or data.get("schema") != SCHEMA:
            _logger.info("speed store: %s has another schema, ignoring it", path)
            return {}
        entries = data.get("samples")
        if not isinstance(entries, list):
            return {}
        stored: dict[SampleKey, list[RunSample]] = {}
        for entry in entries:
            parsed = _samples_from_json(entry)
            if parsed is None:
                _logger.info("speed store: skipping unreadable samples in %s", path)
                continue
            key, samples = parsed
            # A key written by an earlier build may carry the address as typed; the lookups ask
            # for the normalised one, and two spellings of one server must be one list.
            key = replace(key, server=normalize_ollama_url(key.server))
            merged = [*stored.get(key, []), *samples]
            stored[key] = sorted(merged, key=lambda sample: sample.at)[-KEEP_SAMPLES:]
        return stored

    def _write(self, stored: dict[SampleKey, list[RunSample]]) -> None:
        """Write *stored* to the file, atomically."""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        _keep_foreign_file(path)
        self._memo = None
        text = json.dumps(
            {
                "schema": SCHEMA,
                "samples": [
                    {
                        "server": key.server,
                        "tag": key.tag,
                        "step": str(key.step),
                        "mode": key.mode,
                        "samples": [_sample_to_json(sample) for sample in samples],
                    }
                    for key, samples in stored.items()
                ],
            },
            indent=2,
        )
        write_text_atomic(path, text)


class SpeedStore:
    """Reads and writes the measurements file; see the module docstring."""

    def __init__(self, path: Path | None = None) -> None:
        """Create the store.

        Args:
            path: The file to use; ``<data root>/speed/measurements.json`` when ``None``.
        """
        self._path = path
        self._lock = threading.Lock()
        self._memo: tuple[_Stamp | None, list[SpeedRecord]] | None = None

    @property
    def path(self) -> Path:
        """Return the file the store reads and writes."""
        if self._path is None:
            self._path = resolve_data_folder("speed") / "measurements.json"
        return self._path

    # ── reading ────────────────────────────────────────────────────────────

    def records(self) -> tuple[SpeedRecord, ...]:
        """Return every stored measurement, newest first."""
        with self._lock:
            return tuple(sorted(self._read(), key=lambda item: item.measured_at, reverse=True))

    def lookup(self, key: SpeedKey) -> StoredSpeed | None:
        """Return the measurement for *key*, or ``None`` when nothing was measured for it.

        The exact key wins.  Failing that, a measurement of the same model on the same server
        comes back marked out of date, so the panel can show it with its date.
        """
        return self.lookup_many([key]).get(key.tag)

    def lookup_many(self, keys: Sequence[SpeedKey]) -> dict[str, StoredSpeed]:
        """Return the measurement of every key, keyed by tag, from one read of the file.

        The rules are those of :meth:`lookup`.  A model list is filled in one go, so asking for
        every tag at once must not read the file once per tag.
        """
        with self._lock:
            stored = self._read()
        found: dict[str, StoredSpeed] = {}
        for key in keys:
            exact = [item for item in stored if item.key == key]
            if exact:
                found[key.tag] = StoredSpeed(max(exact, key=_newest), current=True)
                continue
            same_model = [
                item for item in stored if item.key.server == key.server and item.key.tag == key.tag
            ]
            if same_model:
                found[key.tag] = StoredSpeed(max(same_model, key=_newest), current=False)
        return found

    # ── writing ────────────────────────────────────────────────────────────

    def save(self, record: SpeedRecord) -> None:
        """Store *record*, replacing an earlier measurement with the same key."""
        with self._lock:
            kept = [item for item in self._read() if item.key != record.key]
            kept.append(record)
            self._write(kept)

    # ── the file ───────────────────────────────────────────────────────────

    def _read(self) -> list[SpeedRecord]:
        """Return the records in the file; ``[]`` when it is missing, unreadable or unknown.

        The parsed file is kept with its stamp, so an unchanged file is parsed once.
        """
        stamp = _stamp(self.path)
        if self._memo is not None and self._memo[0] == stamp:
            return list(self._memo[1])
        records = self._parse()
        self._memo = (stamp, records)
        return list(records)

    def _parse(self) -> list[SpeedRecord]:
        """Parse the file, without the memo."""
        path = self.path
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except FileNotFoundError:
            _logger.debug("speed store: no measurements file at %s yet", path)
            return []
        except (OSError, ValueError) as exc:
            _logger.warning("speed store: cannot read %s: %s", path, exc)
            return []
        data = _as_object(raw)
        if data is None or data.get("schema") != SCHEMA:
            _logger.info("speed store: %s has another schema, ignoring it", path)
            return []
        entries = data.get("measurements")
        if not isinstance(entries, list):
            return []
        records: list[SpeedRecord] = []
        for entry in entries:
            record = _record_from_json(entry)
            if record is None:
                _logger.info("speed store: skipping an unreadable measurement in %s", path)
                continue
            records.append(record)
        return records

    def _write(self, records: list[SpeedRecord]) -> None:
        """Write *records* to the file, atomically."""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        _keep_foreign_file(path)
        self._memo = None
        text = json.dumps(
            {
                "schema": SCHEMA,
                "measurements": [_record_to_json(item) for item in records],
            },
            indent=2,
        )
        write_text_atomic(path, text)


#: What identifies one version of a file on disk: modification time, size and inode.  An atomic
#: replace changes the inode, so a write by another process is seen even within one clock tick.
_Stamp = tuple[int, int, int]


def _stamp(path: Path) -> _Stamp | None:
    """Return the stamp of the file at *path*, or ``None`` when it does not exist."""
    try:
        info = path.stat()
    except OSError:
        _logger.debug("speed store: cannot stat %s", path)
        return None
    return info.st_mtime_ns, info.st_size, info.st_ino


def _keep_foreign_file(path: Path) -> None:
    """Move a file this version cannot read to ``<name>.bak`` before it is written over.

    A file with another schema number was probably written by a newer version of the app;
    reading it as empty is fine, destroying it is not.  A missing, unparsable or current file
    is left alone.
    """
    try:
        data = _as_object(json.loads(path.read_text(encoding="utf-8") or "{}"))
    except OSError, ValueError:
        # Missing or unparsable: no other version's content is in there to keep safe.
        return
    if data is None or data.get("schema") == SCHEMA:
        return
    backup = path.with_name(path.name + ".bak")
    try:
        shutil.copy2(path, backup)
    except OSError as exc:
        _logger.warning("speed store: cannot back up %s: %s", path, exc)
    else:
        _logger.info("speed store: kept the file of another schema as %s", backup)


# ── the file's shape ───────────────────────────────────────────────────────


def _record_to_json(record: SpeedRecord) -> dict:
    """Return *record* as the JSON object the file holds."""
    return {
        "server": record.key.server,
        "tag": record.key.tag,
        "digest": record.key.digest,
        "ollama_version": record.key.ollama_version,
        "gpu": record.key.gpu,
        "measured_at": record.measured_at,
        "on_battery": record.on_battery,
        "shared": record.shared,
        "swaps": record.swaps,
        "steps": [
            {
                "step": str(item.step),
                "outcome": str(item.outcome),
                "message": item.message,
                "speed": None if item.speed is None else _speed_to_json(item.speed),
            }
            for item in record.steps
        ],
    }


def _speed_to_json(speed: ModelSpeed) -> dict:
    """Return *speed* as a JSON object."""
    return {
        "read_tps": speed.read_tps,
        "write_tps": speed.write_tps,
        "load_s": speed.load_s,
        "on_card": speed.on_card,
        "image_tokens": speed.image_tokens,
        "chars_per_token": speed.chars_per_token,
    }


def _as_object(value: object) -> dict[str, Any] | None:
    """Return *value* as a JSON object, or ``None`` when it is not one."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _record_from_json(entry: object) -> SpeedRecord | None:
    """Return the record *entry* describes, or ``None`` when it is not one."""
    data = _as_object(entry)
    if data is None:
        return None
    try:
        key = SpeedKey(
            server=str(data["server"]),
            tag=str(data["tag"]),
            digest=str(data.get("digest", "")),
            ollama_version=str(data.get("ollama_version", "")),
            gpu=str(data.get("gpu", "")),
        )
        measured_at = str(data["measured_at"])
        steps = tuple(
            record
            for record in (_step_from_json(item) for item in data.get("steps", []))
            if record is not None
        )
    except KeyError, TypeError, ValueError:
        # The caller reports the measurement it cannot use.
        return None
    on_battery = data.get("on_battery")
    return SpeedRecord(
        key=key,
        measured_at=measured_at,
        steps=steps,
        on_battery=on_battery if isinstance(on_battery, bool) else None,
        shared=bool(data.get("shared", False)),
        swaps=bool(data.get("swaps", False)),
    )


def _step_from_json(entry: object) -> StepRecord | None:
    """Return the step record *entry* describes, or ``None`` when it is not one."""
    data = _as_object(entry)
    if data is None:
        return None
    try:
        step = SpeedStep(str(data["step"]))
        outcome = StepOutcome(str(data["outcome"]))
    except KeyError, ValueError:
        # A step or an outcome that cannot be read: the record keeps the steps it can.
        return None
    speed = data.get("speed")
    return StepRecord(
        step=step,
        outcome=outcome,
        speed=None if speed is None else _speed_from_json(speed),
        message=str(data.get("message", "")),
    )


def _speed_from_json(entry: object) -> ModelSpeed | None:
    """Return the speed *entry* describes, or ``None`` when it is not one."""
    data = _as_object(entry)
    if data is None:
        return None
    try:
        return ModelSpeed(
            read_tps=float(data["read_tps"]),
            write_tps=float(data["write_tps"]),
            load_s=None if data.get("load_s") is None else float(data["load_s"]),
            on_card=None if data.get("on_card") is None else float(data["on_card"]),
            image_tokens=(None if data.get("image_tokens") is None else int(data["image_tokens"])),
            chars_per_token=(
                None if data.get("chars_per_token") is None else float(data["chars_per_token"])
            ),
        )
    except KeyError, TypeError, ValueError:
        # Unreadable speed numbers: the step keeps its outcome and shows no speed.
        return None


def _sample_to_json(sample: RunSample) -> dict:
    """Return *sample* as a JSON object."""
    return {
        "read_tps": sample.read_tps,
        "write_tps": sample.write_tps,
        "load_s": sample.load_s,
        "image_tokens": sample.image_tokens,
        "answer_tokens": sample.answer_tokens,
        "words": sample.words,
        "pieces": sample.pieces,
        "at": sample.at,
    }


def _samples_from_json(entry: object) -> tuple[SampleKey, list[RunSample]] | None:
    """Return the key and the samples *entry* describes, or ``None`` when it is not one."""
    data = _as_object(entry)
    if data is None:
        return None
    try:
        key = SampleKey(
            server=str(data["server"]),
            tag=str(data["tag"]),
            step=SpeedStep(str(data["step"])),
            mode=str(data.get("mode", "")),
        )
    except KeyError, ValueError:
        # The caller reports the group of samples it cannot use.
        return None
    entries = data.get("samples")
    if not isinstance(entries, list):
        return None
    samples = [sample for sample in (_sample_from_json(item) for item in entries) if sample]
    return key, samples[-KEEP_SAMPLES:]


def _sample_from_json(entry: object) -> RunSample | None:
    """Return the sample *entry* describes, or ``None`` when it is not one."""
    data = _as_object(entry)
    if data is None:
        return None
    try:
        return RunSample(
            read_tps=float(data.get("read_tps", 0.0)),
            write_tps=float(data.get("write_tps", 0.0)),
            load_s=float(data.get("load_s", 0.0)),
            image_tokens=int(data.get("image_tokens", 0)),
            answer_tokens=int(data.get("answer_tokens", 0)),
            words=int(data.get("words", 0)),
            pieces=int(data.get("pieces", 1)),
            at=str(data.get("at", "")),
        )
    except TypeError, ValueError:
        # One sample with unreadable numbers: dropping it leaves the group's median intact.
        return None
