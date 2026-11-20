"""The answers the structured-output check received, saved so they can be judged again.

A verdict is only valid for the rule it was made under.  When the rule changes, the old record
says what the models answered but not what the new rule would say about it, and re-running the
check costs an evening on the server.  ``structured-output --answers DIR`` therefore writes every
answer to a JSON-lines file, and ``verify_model_catalog recheck FILE`` judges those answers again
without a server.

The files stay local: the fixtures are invented, so they hold no corpus text, but they are large.
Only the command's output, the record, is committed.

A file is one header line and one line per run::

    {"schema": 1, "kind": "gate1-answers", "date": ..., "eval_version": ...}
    {"tag": ..., "fixture": ..., "attempt": 1, "text_sha256": ..., "answer": ..., "verdict": ...}

``text_sha256`` is what makes a stale file visible: a fixture that changed since the answers were
saved is refused instead of judged against a text the model never saw.  ``attempt`` is 1 for the
first try and 2 for the second one after a cut-off answer, so ``recheck`` judges the pair the way
the live run did.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = 1
"""The layout of the file.  A reader refuses a file it does not know."""

KIND = "gate1-answers"
"""What the file holds, so a wrong file is refused by name and not by a missing key."""


@dataclass(frozen=True)
class Header:
    """What every answer in one file was produced under."""

    date: str
    eval_version: int
    fingerprint: str
    answer_format_version: int
    num_ctx: int
    temperature: float | None
    """The temperature every model was asked at, or ``None`` when each got its profile's."""
    tool_commit: str
    ollama_version: str
    compact_json: str = "catalog"
    """The ``--compact-json`` choice of the invocation: ``catalog`` (each model's own), ``on`` or
    ``off``.  A word, where an answer's ``request_profile["compact_json"]`` is the boolean that
    model was really asked with."""
    fit_limit: str = ""
    """The ``--fit-limit`` the setups were screened under, so a record says what was checked.
    Empty in a file written before the screen applied a size limit."""

    def to_json(self) -> dict[str, Any]:
        """Return the header as the first line of the file."""
        return {
            "schema": SCHEMA,
            "kind": KIND,
            "date": self.date,
            "eval_version": self.eval_version,
            "fingerprint": self.fingerprint,
            "answer_format_version": self.answer_format_version,
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            "tool_commit": self.tool_commit,
            "ollama_version": self.ollama_version,
            "compact_json": self.compact_json,
            "fit_limit": self.fit_limit,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Header:
        """Return the header *data* describes.

        Raises:
            ValueError: if *data* is not a header of this schema and kind.
        """
        if data.get("schema") != SCHEMA or data.get("kind") != KIND:
            raise ValueError(
                f"not a {KIND} file of schema {SCHEMA} "
                f"(schema {data.get('schema')!r}, kind {data.get('kind')!r})"
            )
        return cls(
            date=str(data["date"]),
            eval_version=int(data["eval_version"]),
            fingerprint=str(data["fingerprint"]),
            answer_format_version=int(data["answer_format_version"]),
            num_ctx=int(data["num_ctx"]),
            temperature=None if data["temperature"] is None else float(data["temperature"]),
            tool_commit=str(data["tool_commit"]),
            ollama_version=str(data["ollama_version"]),
            compact_json=_choice(data),
            fit_limit=str(data.get("fit_limit", "")),
        )


COMPACT_JSON_CHOICES = ("catalog", "on", "off")


def _choice(data: dict[str, Any]) -> str:
    """Return the ``--compact-json`` choice a header records.

    Files written before the key existed kept it inside ``request_profile``; they are read as the
    word they held.

    Raises:
        ValueError: if the recorded choice is not one of :data:`COMPACT_JSON_CHOICES`.
    """
    legacy = (data.get("request_profile") or {}).get("compact_json")
    choice = data.get("compact_json", "catalog" if legacy is None else legacy)
    if choice not in COMPACT_JSON_CHOICES:
        raise ValueError(
            f"the header's compact_json is {choice!r}, not one of {COMPACT_JSON_CHOICES}"
        )
    return str(choice)


@dataclass(frozen=True)
class Answer:
    """One saved answer, with everything judging it again needs."""

    tag: str
    digest: str
    fixture: str
    text_sha256: str
    round: int
    run: int
    request_failed: bool
    too_long: bool
    done_reason: str
    prompt_eval_count: int
    eval_count: int
    seconds: float
    answer: str
    verdict: str
    """The verdict the live run gave, for comparison only: ``recheck`` judges again."""
    attempt: int = 1
    """1 for the first try, 2 for the second one after a cut-off answer."""
    request_profile: dict[str, Any] = field(default_factory=dict)
    """The profile the model was asked with: its compact-JSON rule and its temperature."""

    @property
    def compact_json(self) -> bool:
        """Whether the model was asked with the compact-JSON rule."""
        return bool(self.request_profile.get("compact_json"))

    def to_json(self) -> dict[str, Any]:
        """Return the answer as one line of the file."""
        return {
            "tag": self.tag,
            "digest": self.digest,
            "fixture": self.fixture,
            "text_sha256": self.text_sha256,
            "round": self.round,
            "run": self.run,
            "request_failed": self.request_failed,
            "too_long": self.too_long,
            "done_reason": self.done_reason,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "seconds": self.seconds,
            "answer": self.answer,
            "verdict": self.verdict,
            "attempt": self.attempt,
            "request_profile": self.request_profile,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Answer:
        """Return the answer *data* describes.

        Raises:
            ValueError: if the recorded ``compact_json`` is not a boolean (a header's word stored
                on an answer would read as ``True`` whatever it said).
        """
        recorded = dict(data.get("request_profile") or {})
        if "compact_json" in recorded and not isinstance(recorded["compact_json"], bool):
            raise ValueError(
                f"the answer's compact_json is {recorded['compact_json']!r}, not true or false"
            )
        return cls(
            tag=str(data["tag"]),
            digest=str(data["digest"]),
            fixture=str(data["fixture"]),
            text_sha256=str(data["text_sha256"]),
            round=int(data["round"]),
            run=int(data["run"]),
            request_failed=bool(data["request_failed"]),
            too_long=bool(data["too_long"]),
            done_reason=str(data["done_reason"]),
            prompt_eval_count=int(data["prompt_eval_count"]),
            eval_count=int(data["eval_count"]),
            seconds=float(data["seconds"]),
            answer=str(data["answer"]),
            verdict=str(data["verdict"]),
            attempt=int(data.get("attempt", 1)),
            request_profile=recorded,
        )


def text_sha256(text: str) -> str:
    """Return the hash of *text*, so a changed fixture is recognised."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def answers_path(directory: Path, num_ctx: int, temperature: float | None, now: datetime) -> Path:
    """Return the file one invocation writes: the time, the window and the temperature.

    The name says what the answers were produced under, so a directory of them can be read
    without opening every file.  ``Tprofile`` stands for "each model's own temperature".
    """
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    label = "profile" if temperature is None else f"{temperature:g}"
    return directory / f"{stamp}_ctx{num_ctx}_T{label}.jsonl"


def start_answers(path: Path, header: Header) -> None:
    """Create *path* with *header* as its only line, ready for :func:`append_answer`.

    The file exists from the first model on, so a run that dies in hour two keeps what it had.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(header.to_json(), ensure_ascii=False) + "\n", encoding="utf-8")


def append_answer(path: Path, answer: Answer) -> None:
    """Append *answer* to *path* as one line, so every finished run is on disk at once."""
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(answer.to_json(), ensure_ascii=False) + "\n")


def write_answers(path: Path, header: Header, answers: Iterable[Answer]) -> int:
    """Write *header* and *answers* to *path* and return how many answers were written."""
    start_answers(path, header)
    count = 0
    for answer in answers:
        append_answer(path, answer)
        count += 1
    return count


def read_answers(path: Path) -> tuple[Header, list[Answer]]:
    """Return the header and the answers of *path*.

    Raises:
        ValueError: if the file is empty, its first line is not a header, or a line is not an
            answer.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path}: no header line")
    header = Header.from_json(_object(path, 1, lines[0]))
    answers = [
        Answer.from_json(_object(path, number, line))
        for number, line in enumerate(lines[1:], start=2)
    ]
    return header, answers


def _object(path: Path, number: int, line: str) -> dict[str, Any]:
    """Return *line* as a JSON object.

    Raises:
        ValueError: if *line* is not a JSON object.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}:{number}: not JSON ({error})") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path}:{number}: not a JSON object")
    return data


def by_tag(answers: Sequence[Answer]) -> dict[str, list[Answer]]:
    """Return the answers grouped by model, in the order the models first appear."""
    grouped: dict[str, list[Answer]] = {}
    for answer in answers:
        grouped.setdefault(answer.tag, []).append(answer)
    return grouped
