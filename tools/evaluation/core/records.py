"""The typed records of every stored format: test set, run file, review and marks.

Four records, one module, because they share one rule: **a record holds what was said, and nothing
that was derived from it**.  A run file holds the model's answer, the requests it took and what
the server and the machine reported; it holds no score and no copy of the input.  A test record
holds the question, the request data and the key, each with its own hash.

Every record keeps the fields it does not know (:attr:`extra`) and writes them back, so a newer
writer's field survives a round trip through an older reader.

Positions in every stored record are **character offsets** into the stored text, end exclusive.
Token indices exist only inside the scorer, so a tokenizer change moves no stored data.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, ClassVar
from typing import Protocol as TypingProtocol

from teachers_teammate.domain.proofreading.review import CorrectionReview

from .axis_table import AXES
from .constants import OLLAMA, TextKind
from .formats import (
    MARKS_FORMAT,
    RUN_FORMAT,
    TESTSET_FORMAT,
    FormatError,
    TornTail,
    check_format,
    extra_fields,
)
from .hashing import sha256_json
from .labels import ReferenceLabel
from .modes import DEFAULT_EXCHANGE, DEFAULT_STEPS, Exchange, Step
from .performance import LoadedModel, RequestStats
from .text import word_count

TASK_PROOFREAD = "proofread"
"""The task of this test set and every run of it; a different task gets its own scorer."""

TASKS: tuple[str, ...] = (TASK_PROOFREAD,)
"""Every task the tools know how to score; a run of another task is refused, not guessed at."""


# ── the test set: question, request, key, meta ────────────────────────────────


@dataclass(frozen=True)
class RequestData:
    """What a run *may* send besides the text itself (task-text concept).

    Each part has its own hash, so a run that sends the notes binds to the notes' hash and a run
    that does not is unaffected by them.  All parts are optional and none is sent today.
    """

    level: str = ""
    """The CEFR level the text is asked about at (``B1``)."""

    notes: str = ""
    """The teacher's notes about the text."""

    passages: tuple[tuple[int, int], ...] = ()
    """Character spans of the printed task: form labels, headers, the instruction."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Parts a newer writer added; kept and written back, and not part of any hash."""

    _KNOWN: ClassVar[tuple[str, ...]] = ("level", "notes", "passages")

    def parts(self) -> dict[str, Any]:
        """Return the parts that are present, as the values their hashes are made of."""
        found: dict[str, Any] = {}
        if self.level:
            found["level"] = self.level
        if self.notes:
            found["notes"] = self.notes
        if self.passages:
            found["passages"] = [list(span) for span in self.passages]
        return found

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record; an absent part is left out."""
        return {**self.extra, **self.parts()}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any] | None) -> RequestData:
        """Build the request data from :meth:`to_dict` output."""
        data = record or {}
        return cls(
            level=str(data.get("level", "")),
            notes=str(data.get("notes", "")),
            passages=tuple((int(start), int(end)) for start, end in data.get("passages", ())),
            extra=extra_fields(data, cls._KNOWN),
        )


@dataclass(frozen=True)
class AcceptedEdit:
    """A correction a marker called valid, which the key accepts as a second annotator's fix.

    Part of the key group, so adding one moves ``key_sha256`` and no run.
    """

    span: tuple[int, int]
    """Character span of the source the edit replaces."""

    replacement: str
    """The text the edit puts there."""

    markers: tuple[str, ...] = ()
    """Who accepted it.  Provenance, not key data: the same alternative from another marker is the
    same alternative, so :meth:`key_dict` leaves it out."""

    def key_dict(self) -> dict:
        """Return what the key group hashes: the edit, and not who accepted it."""
        return {"span": list(self.span), "replacement": self.replacement}

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record, with the markers when there are any."""
        record = self.key_dict()
        if self.markers:
            record["markers"] = list(self.markers)
        return record

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> AcceptedEdit:
        """Build an accepted edit from :meth:`to_dict` output."""
        start, end = record["span"]
        return cls(
            span=(int(start), int(end)),
            replacement=str(record.get("replacement", "")),
            markers=tuple(str(marker) for marker in record.get("markers") or ()),
        )


@dataclass(frozen=True)
class TestRecord:
    """One text of a test set, in the four groups below.

    The rule that sorts a field: **question** is the text's identity and what is sent with every
    request; **request** is what a run *may* send besides it; **key** is what is only read when
    scoring; **meta** is provenance.  A change of the question is another set; a change of the key
    is the same runs scored against a better key.
    """

    __test__ = False
    """Not a test class, though its name starts like one; pytest would try to collect it."""

    # ── question: the text's identity ──
    id: str
    language: str
    source: str

    # ── request: what a run may send besides the text ──
    request: RequestData = field(default_factory=RequestData)

    # ── key: what is only read when scoring ──
    kind: str = TextKind.ERRORS
    """A :class:`~.constants.TextKind`: a text with mistakes, or one that is already correct."""

    reference: str = ""
    """The reference correction; empty for a ``"correct"`` text."""

    sentences: tuple[str, ...] = ()
    """The already-correct sentences of a ``"correct"`` text, to check they stay untouched."""

    labels: tuple[ReferenceLabel, ...] = ()
    """The corpus label of every change the reference makes."""

    sentence_spans: tuple[tuple[int, int], ...] = ()
    """The **character** span of every sentence of the corpus's own segmentation, in text order."""

    unchanged_sentences: tuple[int, ...] = ()
    """Indices into :attr:`sentence_spans` of the sentences the reference leaves alone."""

    accepted: tuple[AcceptedEdit, ...] = ()
    """Corrections a marker called valid, scored as a second annotator's fix."""

    # ── meta: provenance ──
    origin: str = ""
    """Where the text comes from; kept so a licence question can be answered later."""

    band: str = ""
    """The band the recipe drew the text for (``short``, ``page``, ``pages``)."""

    corpus: str = ""
    """The corpus adapter the text came from."""

    pointer: Mapping[str, Any] = field(default_factory=dict)
    """Where in the corpus the text sits, as the manifest records it."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    # ── identities ──

    @cached_property
    def word_count(self) -> int:
        """Return how many words and marks the source has, as the manifest counts them.

        Cached: every table that weighs a text by its length asks for this, and tokenising the
        source again for each of them is the same answer at a cost.
        """
        return word_count(self.source)

    @property
    def text_sha256(self) -> str:
        """Return the hash of the question: the text's language and its source.

        A run binds to this, so a corrected reference, a new label or an accepted alternative
        leaves every run of the text valid.
        """
        return sha256_json({"language": self.language, "source": self.source})

    def request_hashes(self) -> dict[str, str]:
        """Return the hash of every request part that is present, keyed by part name."""
        return {name: sha256_json(value) for name, value in self.request.parts().items()}

    @property
    def key_sha256(self) -> str:
        """Return the hash of the key group: everything read only when scoring."""
        return sha256_json(self._key_group())

    def _key_group(self) -> dict[str, Any]:
        """Return the key group as the value its hash is made of."""
        return {
            "kind": self.kind,
            "reference": self.reference,
            "sentences": list(self.sentences),
            "labels": [label.to_dict() for label in self.labels],
            "sentence_spans": [list(span) for span in self.sentence_spans],
            "unchanged_sentences": list(self.unchanged_sentences),
            "accepted": [edit.key_dict() for edit in self.accepted],
        }

    # ── the stored form ──

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "format",
        "id",
        "language",
        "source",
        "request",
        "kind",
        "reference",
        "sentences",
        "labels",
        "sentence_spans",
        "unchanged_sentences",
        "accepted",
        "origin",
        "band",
        "corpus",
        "pointer",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record; an empty optional field is left out."""
        record: dict[str, Any] = {
            "format": TESTSET_FORMAT,
            "id": self.id,
            "language": self.language,
            "source": self.source,
            "kind": self.kind,
        }
        if request := self.request.to_dict():
            record["request"] = request
        if self.reference:
            record["reference"] = self.reference
        if self.sentences:
            record["sentences"] = list(self.sentences)
        if self.labels:
            record["labels"] = [label.to_dict() for label in self.labels]
        if self.sentence_spans:
            record["sentence_spans"] = [list(span) for span in self.sentence_spans]
        if self.unchanged_sentences:
            record["unchanged_sentences"] = list(self.unchanged_sentences)
        if self.accepted:
            record["accepted"] = [edit.to_dict() for edit in self.accepted]
        if self.origin:
            record["origin"] = self.origin
        if self.band:
            record["band"] = self.band
        if self.corpus:
            record["corpus"] = self.corpus
        if self.pointer:
            record["pointer"] = dict(self.pointer)
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> TestRecord:
        """Build a record from :meth:`to_dict` output.

        Raises:
            FormatError: if the record is of a format this reader does not know.
        """
        check_format(record, TESTSET_FORMAT, f"test record {record.get('id', '?')!r}")
        return cls(
            id=str(record["id"]),
            language=str(record["language"]),
            source=str(record["source"]),
            request=RequestData.from_dict(record.get("request")),
            kind=str(record.get("kind", TextKind.ERRORS)),
            reference=str(record.get("reference", "")),
            sentences=tuple(str(sentence) for sentence in record.get("sentences", ())),
            labels=tuple(ReferenceLabel.from_dict(label) for label in record.get("labels", ())),
            sentence_spans=tuple(
                (int(start), int(end)) for start, end in record.get("sentence_spans", ())
            ),
            unchanged_sentences=tuple(
                int(index) for index in record.get("unchanged_sentences", ())
            ),
            accepted=tuple(AcceptedEdit.from_dict(edit) for edit in record.get("accepted", ())),
            origin=str(record.get("origin", "")),
            band=str(record.get("band", "")),
            corpus=str(record.get("corpus", "")),
            pointer=dict(record.get("pointer") or {}),
            extra=extra_fields(record, cls._KNOWN),
        )


class Texted(TypingProtocol):
    """What a text id is made of: a language and an id, which a record and a manifest item both have."""

    @property
    def language(self) -> str:
        """The language code of the text."""
        ...

    @property
    def id(self) -> str:
        """The record's id of the text, unique inside its own set file."""
        ...


def text_id(record: Texted) -> str:
    """Return the one name a text has outside its own set file: ``<language>/<id>``.

    A record's ``id`` is unique only inside its set file, and two corpora number alike, so every
    surface that names a text from outside — an answer, a mark, an accepted alternative, a
    spot-check sample, a per-text key — uses this form.  The viewer prefixes the set as well.
    """
    return f"{record.language}/{record.id}"


def question_sha256(records: Sequence[TestRecord]) -> str:
    """Return the hash of a whole set's question: its texts' hashes, in order.

    A set rebuilt under the same name with other texts is another set, and every run of the old one
    is about other texts.  A corrected reference does not move this hash.
    """
    return sha256_json([record.text_sha256 for record in records])


def key_sha256(records: Sequence[TestRecord]) -> str:
    """Return the hash of a whole set's key: every text's key group, in order."""
    return sha256_json([record._key_group() for record in records])  # noqa: SLF001


# ── the run file: header, session, answer ─────────────────────────────────────


@dataclass(frozen=True)
class Input:
    """Which texts a run was asked about."""

    testset: str
    """The committed manifest's id (``gec-2026-12``), never the local folder name."""

    question_sha256: str
    """The hash of the set's question, so a rebuilt set is not mistaken for this one."""

    texts: int
    """How many texts the run covers."""

    languages: tuple[str, ...] = ()
    """The language codes the run covers, in the table's order."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = ("testset", "question_sha256", "texts", "languages")

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        record = {
            "testset": self.testset,
            "question_sha256": self.question_sha256,
            "texts": self.texts,
            "languages": list(self.languages),
        }
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Input:
        """Build the input block from :meth:`to_dict` output."""
        return cls(
            testset=str(record["testset"]),
            question_sha256=str(record["question_sha256"]),
            texts=int(record["texts"]),
            languages=tuple(str(code) for code in record.get("languages", ())),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class Protocol:
    """The axes of a run, each stored once under a readable name.

    The file name is a label derived from these and never read back.
    """

    model: str
    model_digest: str = ""
    exchange: Exchange = DEFAULT_EXCHANGE
    steps: tuple[Step, ...] = DEFAULT_STEPS
    step_one_prompt: str = "standard"
    """The variant of step ①'s prompt (``standard``, ``keep``, ``find``, ``shot``…)."""

    temperature: float = 0.0
    sampling: tuple[tuple[str, float], ...] = ()
    """The sampling options that were sent, sorted by name; empty when none was."""

    compact_json: bool = False
    num_ctx: int = 8192
    provider: str = OLLAMA
    answer_format_version: int = 1
    uses: tuple[str, ...] = ()
    """The request parts the run sent (``level``, ``notes``, ``passages``); empty when none."""

    instructions: tuple[tuple[str, str], ...] = ()
    """The hash of what was sent, per language, so two runs compare on the languages they share."""

    fingerprint: str = ""
    """The protocol fingerprint, only to look up ``eval_version``; never part of a key."""

    explainer: str = ""
    """The model tag that wrote the explanations, when another model did (review mode B)."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Axes a newer writer added; kept, written back, and part of the compare key.

    An axis this reader does not know is still an axis: two runs that differ in it measured
    different things and must not pool, so :func:`~.axes.compare_key` hashes it too.
    """

    _KNOWN: ClassVar[tuple[str, ...]] = (
        *(axis.name for axis in AXES),
        "fingerprint",
    )

    def sampling_dict(self) -> dict[str, float]:
        """Return the sampling options as a dict, for a reader that wants one."""
        return dict(self.sampling)

    def instructions_dict(self) -> dict[str, str]:
        """Return the per-language instruction hashes as a dict."""
        return dict(self.instructions)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record, one entry per axis and the fingerprint.

        Generated from :data:`~.axis_table.AXES`, so a new axis is one declaration and one field.
        """
        record = {axis.name: axis.dump(getattr(self, axis.field)) for axis in AXES}
        record["fingerprint"] = self.fingerprint
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Protocol:
        """Build the protocol block from :meth:`to_dict` output.

        Raises:
            ValueError: if an exchange or a step is not one this reader knows.
        """
        values = {axis.field: axis.parse(record.get(axis.name, axis.default)) for axis in AXES}
        return cls(
            **values,
            fingerprint=str(record.get("fingerprint", "")),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class RunSettings:
    """The run's own fields: not axes, and not part of the compare key."""

    seed: int
    """The seed of repeat 1; repeat *k* uses ``seed + k - 1``.  Not an axis: repeats must pool."""

    repeat: int = 1
    """Which repeat this file holds."""

    timeout: float = 900.0
    """The request timeout in seconds."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = ("seed", "repeat", "timeout")

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        record = {"seed": self.seed, "repeat": self.repeat, "timeout": self.timeout}
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> RunSettings:
        """Build the run block from :meth:`to_dict` output."""
        return cls(
            seed=int(record["seed"]),
            repeat=int(record.get("repeat", 1)),
            timeout=float(record.get("timeout", 900.0)),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class Environment:
    """Where a run ran: the machine, the server, and whether anything else shared the card."""

    machine: str = ""
    """The machine an Ollama run ran on; ``""`` for a provider that ran on none of ours."""

    ollama_version: str = ""
    """The server's version; another version is another backend."""

    isolated: bool = False
    """Whether the run had the card to itself."""

    gpu_sampler: Mapping[str, Any] = field(default_factory=dict)
    """The graphics-memory sampler: the local vendor, or a remote agent's record; empty if none."""

    machine_source: str = ""
    """How an empty machine was filled in (``assumed by convert_v1: …``), or ``""``."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "machine",
        "ollama_version",
        "isolated",
        "gpu_sampler",
        "machine_source",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        record: dict[str, Any] = {
            "machine": self.machine,
            "ollama_version": self.ollama_version,
            "isolated": self.isolated,
            "gpu_sampler": dict(self.gpu_sampler),
        }
        if self.machine_source:
            record["machine_source"] = self.machine_source
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Environment:
        """Build the environment block from :meth:`to_dict` output."""
        return cls(
            machine=str(record.get("machine", "")),
            ollama_version=str(record.get("ollama_version", "")),
            isolated=bool(record.get("isolated", False)),
            gpu_sampler=dict(record.get("gpu_sampler") or {}),
            machine_source=str(record.get("machine_source", "")),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class RunHeader:
    """Line 1 of a run file: what was asked, of which model, and where."""

    input: Input
    protocol: Protocol
    run: RunSettings
    task: str = TASK_PROOFREAD
    """What the run does; each task has its own scorer."""

    model_info: Mapping[str, Any] = field(default_factory=dict)
    """The tag's ``digest``, ``quantization``, ``parameter_size`` and ``family``."""

    environment: Environment = field(default_factory=Environment)
    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "format",
        "type",
        "task",
        "input",
        "protocol",
        "run",
        "model_info",
        "environment",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable header, ``type`` first so a reader knows what it has."""
        record = {
            "format": RUN_FORMAT,
            "type": "run",
            "task": self.task,
            "input": self.input.to_dict(),
            "protocol": self.protocol.to_dict(),
            "run": self.run.to_dict(),
            "model_info": dict(self.model_info),
            "environment": self.environment.to_dict(),
        }
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> RunHeader:
        """Build a header from :meth:`to_dict` output.

        Raises:
            FormatError: if the line is not a run header of a format this reader knows.
        """
        check_format(record, RUN_FORMAT, "run header")
        if record.get("type") != "run":
            raise FormatError(f"line 1 is not a run header (type {record.get('type')!r})")
        return cls(
            input=Input.from_dict(record["input"]),
            protocol=Protocol.from_dict(record["protocol"]),
            run=RunSettings.from_dict(record["run"]),
            task=str(record.get("task", TASK_PROOFREAD)),
            model_info=dict(record.get("model_info") or {}),
            environment=Environment.from_dict(record.get("environment") or {}),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class Session:
    """One start or resume of a run; a run file appends one per session."""

    date: str
    """When the session started, as an ISO timestamp."""

    tool_revision: str = ""
    """The commit the tools were at; information only."""

    vram_baseline_mb: int | None = None
    """The graphics memory before the model was loaded."""

    loaded_before: tuple[Mapping[str, Any], ...] = ()
    """What the server had loaded when the session started."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "format",
        "type",
        "date",
        "tool_revision",
        "vram_baseline_mb",
        "loaded_before",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        record: dict[str, Any] = {"format": RUN_FORMAT, "type": "session", "date": self.date}
        if self.tool_revision:
            record["tool_revision"] = self.tool_revision
        if self.vram_baseline_mb is not None:
            record["vram_baseline_mb"] = self.vram_baseline_mb
        if self.loaded_before:
            record["loaded_before"] = [dict(item) for item in self.loaded_before]
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Session:
        """Build a session from :meth:`to_dict` output."""
        check_format(record, RUN_FORMAT, "session line")
        return cls(
            date=str(record["date"]),
            tool_revision=str(record.get("tool_revision", "")),
            vram_baseline_mb=record.get("vram_baseline_mb"),
            loaded_before=tuple(dict(item) for item in record.get("loaded_before", ())),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class Answer:
    """One text's answer: what the model said, what it cost, and nothing derived."""

    text: str
    """The text's id, ``<language>/<record id>``."""

    text_sha256: str
    """The hash of the question, so an answer cannot be read against another text."""

    output: str
    """The corrected text as returned, marks included; for a failed text what the teacher got."""

    failed: bool = False
    """Whether the text failed; the source is kept."""

    fallback: bool = False
    """Whether the text fell back to corrections only."""

    warning: str = ""
    """The app's warning; empty when none."""

    left_out: int = 0
    """How many stretches the model left out and the app put back, so the output is whole.

    The stored output has them again, so the text-loss numbers see a whole text; the model's own
    answer did not, so a row with a count here is lost text all the same
    (:func:`~..metrics.text_loss.is_lost`).
    """

    step_one_answers: tuple[str, ...] = ()
    """The model's own step-① answers of a text that failed, fell back or lost text, as it gave
    them; empty for every other text.  The output is what the teacher got, so a failure is read
    here."""

    review: CorrectionReview | None = None
    """The app's own review, offsets in characters of the source; ``None`` without explanations."""

    review_unreadable: str = ""
    """The version of a stored review this reader does not know; ``""`` when there is none.

    Derived from the stored review, so it is never written: the raw review is kept in
    :attr:`extra` and written back, so a newer writer's review survives a round trip.  Its
    explanation numbers are unknown, not zero.
    """

    unclear_count: int = 0
    """Converted rows only: the count of a run before the unclear step quoted its findings."""

    requests: tuple[RequestStats, ...] = ()
    """What each request cost: tokens, durations and the step."""

    loaded: tuple[LoadedModel, ...] = ()
    """The models the server had loaded around this text."""

    shared: bool = False
    """Whether another model was loaded too; such a text is left out of speed and memory figures."""

    vram_baseline_mb: int | None = None
    vram_peak_mb: int | None = None
    """The graphics memory before the text and at its peak."""

    pieces: int = 1
    """How many pieces the app split the text into; a text within the budget is one."""

    seconds: float = 0.0
    """How long the text took."""

    step_inferred: bool = False
    """Converted rows only: the steps were inferred from the request count, not recorded."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "format",
        "type",
        "text",
        "text_sha256",
        "output",
        "failed",
        "fallback",
        "warning",
        "left_out",
        "step_one_answers",
        "review",
        "review_unreadable",
        "unclear_count",
        "requests",
        "loaded",
        "shared",
        "vram_baseline_mb",
        "vram_peak_mb",
        "pieces",
        "seconds",
        "step_inferred",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record; an empty optional field is left out."""
        record: dict[str, Any] = {
            "format": RUN_FORMAT,
            "type": "answer",
            "text": self.text,
            "text_sha256": self.text_sha256,
            "output": self.output,
            "failed": self.failed,
            "fallback": self.fallback,
        }
        if self.warning:
            record["warning"] = self.warning
        if self.left_out:
            record["left_out"] = self.left_out
        if self.step_one_answers:
            record["step_one_answers"] = list(self.step_one_answers)
        if self.review is not None:
            record["review"] = self.review.to_dict()
        if self.unclear_count:
            record["unclear_count"] = self.unclear_count
        if self.requests:
            record["requests"] = [request.to_dict() for request in self.requests]
        if self.loaded:
            record["loaded"] = [model.to_dict() for model in self.loaded]
        if self.shared:
            record["shared"] = True
        if self.vram_baseline_mb is not None:
            record["vram_baseline_mb"] = self.vram_baseline_mb
        if self.vram_peak_mb is not None:
            record["vram_peak_mb"] = self.vram_peak_mb
        if self.pieces != 1:
            record["pieces"] = self.pieces
        if self.seconds:
            record["seconds"] = self.seconds
        if self.step_inferred:
            record["step_inferred"] = True
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Answer:
        """Build an answer from :meth:`to_dict` output.

        Raises:
            FormatError: if the line is not an answer of a format this reader knows.
        """
        check_format(record, RUN_FORMAT, "answer line")
        raw_review = record.get("review")
        review = CorrectionReview.from_dict(raw_review)
        extra = extra_fields(record, cls._KNOWN)
        review_unreadable = ""
        if raw_review is not None and review is None:
            extra["review"] = raw_review
            review_unreadable = (
                str(raw_review.get("version", "")) if isinstance(raw_review, dict) else ""
            )
        return cls(
            text=str(record["text"]),
            text_sha256=str(record["text_sha256"]),
            output=str(record["output"]),
            failed=bool(record.get("failed", False)),
            fallback=bool(record.get("fallback", False)),
            warning=str(record.get("warning", "")),
            left_out=int(record.get("left_out", 0)),
            step_one_answers=tuple(str(item) for item in record.get("step_one_answers", ())),
            review=review,
            review_unreadable=review_unreadable,
            unclear_count=int(record.get("unclear_count", 0)),
            requests=tuple(RequestStats.from_dict(item) for item in record.get("requests", ())),
            loaded=tuple(LoadedModel.from_dict(item) for item in record.get("loaded", ())),
            shared=bool(record.get("shared", False)),
            vram_baseline_mb=record.get("vram_baseline_mb"),
            vram_peak_mb=record.get("vram_peak_mb"),
            pieces=int(record.get("pieces", 1)),
            seconds=float(record.get("seconds", 0.0)),
            step_inferred=bool(record.get("step_inferred", False)),
            extra=extra,
        )


@dataclass(frozen=True)
class RunFile:
    """One run file: a header, the sessions it took, and one answer per text."""

    header: RunHeader
    sessions: tuple[Session, ...] = ()
    answers: tuple[Answer, ...] = ()
    torn: TornTail | None = field(default=None, compare=False, repr=False)
    """The last line, when a killed append left it cut off; the text it was about is not answered."""

    def lines(self) -> list[dict]:
        """Return every line of the file, header first, as the dicts a writer stores."""
        return [
            self.header.to_dict(),
            *(session.to_dict() for session in self.sessions),
            *(answer.to_dict() for answer in self.answers),
        ]

    def answer(self, text: str) -> Answer | None:
        """Return the answer of *text*, or ``None`` when the run has none."""
        return next((answer for answer in self.answers if answer.text == text), None)

    def done(self) -> set[str]:
        """Return the ids already answered, so an interrupted run can resume."""
        return {answer.text for answer in self.answers}


def header_from_line(record: Mapping[str, Any], source: str = "run file") -> RunHeader:
    """Return the header a run file's first line holds.

    Raises:
        FormatError: if the line is not a header of a format this reader knows.
    """
    return _line_of(RunHeader.from_dict, record, source, 1)


def read_run_file(
    records: Sequence[Mapping[str, Any]],
    source: str = "run file",
    torn: TornTail | None = None,
) -> RunFile:
    """Return the run file made of *records*, the lines of one file in order.

    A line a newer writer wrote in a shape this reader does not expect is refused as a
    :class:`~.formats.FormatError` naming the file and the line, never as the ``KeyError`` or
    ``TypeError`` of a record's constructor.

    Args:
        records: The lines as :func:`~.formats.read_jsonl` returned them.
        source: Where they came from, for the message of a broken file.
        torn: The last line, when it was cut off mid-write and left out of *records*.

    Raises:
        FormatError: if the file is empty, its first line is not a header, or a later line is
            neither a session nor an answer.
    """
    if not records:
        raise FormatError(f"{source}: an empty run file")
    header = header_from_line(records[0], source)
    sessions: list[Session] = []
    answers: list[Answer] = []
    for number, record in enumerate(records[1:], start=2):
        kind = record.get("type")
        if kind == "session":
            sessions.append(_line_of(Session.from_dict, record, source, number))
        elif kind == "answer":
            answers.append(_line_of(Answer.from_dict, record, source, number))
        else:
            raise FormatError(f"{source}:{number}: neither a session nor an answer (type {kind!r})")
    return RunFile(header=header, sessions=tuple(sessions), answers=tuple(answers), torn=torn)


def _line_of[T](
    build: Callable[[Mapping[str, Any]], T], record: Mapping[str, Any], source: str, number: int
) -> T:
    """Return ``build(record)``, turning a record's own refusal into a message naming file and line."""
    try:
        return build(record)
    except FormatError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise FormatError(f"{source}:{number}: {error}") from error


# ── marks: one identity for everything a person marks ─────────────────────────

MARK_KINDS: tuple[str, ...] = ("edit", "explanation")
"""The two questions a marker answers: is this edit right, and is this explanation right."""

MARK_VALUES: dict[str, tuple[str, ...]] = {
    "edit": ("valid", "wrong", "vague"),
    "explanation": ("correct", "wrong", "vague"),
}
"""What a marker can say, per kind.

``wrong`` means two different things and that is why a mark has a kind: an edit mark's ``wrong``
refuses the edit, an explanation mark's ``wrong`` says the sentence does not explain it.  Only an
edit mark can accept an alternative.
"""


@dataclass(frozen=True)
class Mark:
    """One person's verdict on one edit or one explanation.

    A mark's identity is four parts: its :attr:`kind`, its :attr:`text`, the :attr:`span` and the
    :attr:`replacement` — plus, for an explanation mark, the :attr:`explanation_sha256` of the
    sentence it is about.  The span is in **characters** of the text's ``source``, so a mark
    survives a tokenizer change and only a moved hunk boundary can invalidate it — which the
    reader lists, never drops.
    """

    kind: str
    """``"edit"`` or ``"explanation"``; see :data:`MARK_KINDS`."""

    text: str
    """The text's id, ``<language>/<record id>``."""

    span: tuple[int, int]
    """Character span of the source the edit replaces."""

    replacement: str
    """The text the edit puts there."""

    value: str
    """One of :data:`MARK_VALUES` for this mark's kind."""

    explanation_sha256: str = ""
    """The hash of the explanation the mark is about; required for an explanation mark.

    An explanation verdict is about one model's text, so it is part of the mark's identity: two
    models that wrote the same explanation share the verdict, and two that wrote different ones do
    not.  An edit mark forbids it, because an edit is the same edit whoever explained it.
    """

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "format",
        "kind",
        "text",
        "span",
        "replacement",
        "value",
        "explanation_sha256",
    )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record; a mark holds no text of the corpus."""
        record = {
            "format": MARKS_FORMAT,
            "kind": self.kind,
            "text": self.text,
            "span": list(self.span),
            "replacement": self.replacement,
            "value": self.value,
        }
        if self.explanation_sha256:
            record["explanation_sha256"] = self.explanation_sha256
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Mark:
        """Build a mark from :meth:`to_dict` output.

        Raises:
            FormatError: if the record is of a format this reader does not know.
            ValueError: if its kind or its value is not one a marker can give, or if an explanation
                mark carries no hash or an edit mark carries one.
        """
        check_format(record, MARKS_FORMAT, f"mark of {record.get('text', '?')!r}")
        kind = str(record["kind"])
        if kind not in MARK_VALUES:
            allowed = ", ".join(MARK_KINDS)
            raise ValueError(f"unknown mark kind {kind!r}; use one of: {allowed}")
        value = str(record["value"])
        if value not in MARK_VALUES[kind]:
            allowed = ", ".join(MARK_VALUES[kind])
            raise ValueError(f"unknown {kind} mark value {value!r}; use one of: {allowed}")
        explanation_sha256 = str(record.get("explanation_sha256", ""))
        if kind == "explanation" and not explanation_sha256:
            raise ValueError("an explanation mark needs the hash of the explanation it is about")
        if kind == "edit" and explanation_sha256:
            raise ValueError("an edit mark is about the edit, not about an explanation")
        start, end = record["span"]
        return cls(
            kind=kind,
            text=str(record["text"]),
            span=(int(start), int(end)),
            replacement=str(record.get("replacement", "")),
            value=value,
            explanation_sha256=explanation_sha256,
            extra=extra_fields(record, cls._KNOWN),
        )


__all__ = [
    "MARK_KINDS",
    "MARK_VALUES",
    "TASKS",
    "TASK_PROOFREAD",
    "AcceptedEdit",
    "Answer",
    "Environment",
    "Input",
    "Mark",
    "Protocol",
    "RequestData",
    "RunFile",
    "RunHeader",
    "RunSettings",
    "Session",
    "TestRecord",
    "Texted",
    "extra_fields",
    "header_from_line",
    "key_sha256",
    "question_sha256",
    "read_run_file",
    "text_id",
]
