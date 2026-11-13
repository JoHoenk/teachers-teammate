"""The protocol of a measured run: which prompts, answer format and window it used.

Three versions are easy to mix up:

* ``schema`` — the layout of a JSON file, which fields exist.  It sits in every run, manifest and
  machine file and changes only when the format changes;
* ``eval_version`` — what a run measured and how: prompts, answer format, window, scoring.  It
  lives in ``protocol.json`` next to this module and is copied into every run header;
* ``tool_revision`` — the commit the tools were at.  Information only: nothing checks it, groups
  by it or refuses because of it.

Runs are grouped and compared by ``eval_version``, never by commit.  The *fingerprint* of a
version is the SHA-256 of the request side, *captured* from the app's own corrector: what one
request per language and answer mode carries (system and user message, answer schema, options),
the text budget per language, the repair message and the answer format version.  Building the
corrector with the factory a run uses means a new request setting is covered without anyone
editing this module.  ``run`` computes it and refuses to start when it differs from the entry of
``current``, so a prompt change, committed or not, forces a new version.  A change of the scoring, the caps or the edit extraction does not move
the fingerprint; the golden numbers of the tests fail instead and remind you to raise the version
by hand.

Old entries are never edited or removed, so every committed run stays checkable against the
version it names.

This module imports nothing from :mod:`.store`, :mod:`.measure` or :mod:`.report`, so the run, the
export and the tests can all use it without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.domain.context_budget import answers_with_the_same_text
from teachers_teammate.domain.correction_policy import DEFAULT_LEVEL
from teachers_teammate.domain.formatted_text import PLAIN, TextFormat
from teachers_teammate.domain.model_catalog import DEFAULT_REQUEST_PROFILE, RequestProfile
from teachers_teammate.infrastructure.correction import instruction_parts
from teachers_teammate.infrastructure.correction_schema import repair_message
from teachers_teammate.infrastructure.ollama_utils import PROOFREADING_NUM_CTX, OllamaClient

from .answer_mode import CORRECTOR_MODES, DEFAULT_MODE, AnswerMode
from .app_corrector import app_corrector_factory, innermost
from .languages import LANGUAGE_NAMES
from .redaction import sha256_text

PROTOCOL_PATH = Path(__file__).with_name("protocol.json")
"""The version file, next to this module (``//tools:model_eval`` carries it as data)."""

NUM_CTX = PROOFREADING_NUM_CTX
"""The window the protocol measures at; the run and the fingerprint both use it."""

MODES: tuple[AnswerMode, ...] = CORRECTOR_MODES
"""The answer modes the fingerprint covers: the app's review answer and corrections only."""

FINGERPRINT_LANGUAGES: tuple[str, ...] = tuple(LANGUAGE_NAMES)
"""The languages the fingerprint covers: every one the tool can ask in, so a prompt change in any
is caught, and a new language forces a new eval version."""

PROFILE_VARIANTS: tuple[tuple[str, RequestProfile], ...] = (
    ("plain", DEFAULT_REQUEST_PROFILE),
    ("compact", RequestProfile(compact_json=True)),
)
"""The request profiles the fingerprint covers: every value of every knob of the profile.

The catalog's choice of profile per model is *not* part of the fingerprint — it is recorded per
run, like the temperature — but the request each choice produces is, so a change to the rule text
or to a further knob changes the fingerprint and needs a new eval version.
"""

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """The version file or the request side does not allow a run to start."""


@dataclass(frozen=True)
class Stamp:
    """What a run header records about its protocol: the version and its fingerprint."""

    eval_version: int
    fingerprint: str


def instructions_sha256(
    languages: Iterable[str],
    *,
    mode: AnswerMode = DEFAULT_MODE,
    level: str = DEFAULT_LEVEL,
    text_format: TextFormat = PLAIN,
    compact_json: bool = False,
) -> str:
    """Return the hash of the instructions a run's requests carried, one part per language.

    The prompt the app sends depends on the language, the level, the text format, the answer
    mode and, in review mode, whether the model's request profile adds the compact-JSON rule.  The comparison sends the language *name* ("German") and plain text, so that is what is
    asked here; *languages* are the codes of the run (``de``), mapped through
    :data:`~.languages.LANGUAGE_NAMES`.  A run covers several languages, so the hash covers all of
    them, in the alphabetical order of their names.  A changed prompt, level or answer format
    therefore changes the hash, and a reader sees that a stored run was asked differently from
    today's code.

    Raises:
        ValueError: if a language has no name (its prompt would silently be the English one).
    """
    names = []
    for code in languages:
        if code not in LANGUAGE_NAMES:
            raise ValueError(f"no language name for {code!r}; the instructions cannot be hashed")
        names.append(LANGUAGE_NAMES[code])
    parts: list[str] = []
    for name in sorted(names):
        policy, fixed = instruction_parts(
            "",
            name,
            level=level,
            text_format=text_format,
            mode=mode,
            answer_format="",
            compact_json=compact_json,
        )
        parts.append(f"## {name}\n{policy}\n\n{fixed}")
    return sha256_text("\n\n---\n\n".join(parts))


_PROBE_TEXT = "Probe."
"""The text the fingerprint asks the corrector about: short, so one request carries it."""

_PROBE_REVIEW_ANSWER = '{"sentences": [], "unclear": []}'
"""A valid review answer with nothing to change: the answer to the second try."""

_RUN_SETTINGS = ("temperature", "seed")
"""Options of a request that belong to the run (its header), not to the request side."""


class _CapturingClient(OllamaClient):
    """Answers every request at once and keeps what would have been sent.

    Constructing an :class:`OllamaClient` connects to nothing, so the fingerprint needs no
    server.  A request with a ``format`` is a review request: the *first* one is cut off, so the
    corrector asks its second try, and that one gets a valid empty review.  The retry is
    therefore part of the capture: removing it changes the fingerprint.
    """

    def __init__(self) -> None:
        super().__init__("http://capture.invalid")
        self.requests: list[dict[str, Any]] = []
        self._cut_off = False

    def chat(  # noqa: PLR0913  # the signature of the client it replaces
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | str = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        system: str = "",
        allow_truncated: bool = False,
        answer_tokens: int | None = None,
    ) -> str:
        """Keep the request and return a valid answer to it."""
        del model, images, timeout, allow_truncated  # the signature of the client, not kept
        kept = {k: v for k, v in (options or {}).items() if k not in _RUN_SETTINGS}
        self.requests.append(
            {
                "system": system,
                "prompt": prompt,
                "format": format,
                "think": think,
                "answer_tokens": answer_tokens,
                "options": kept,
            }
        )
        if format is None:
            return _PROBE_TEXT
        if not self._cut_off:
            from teachers_teammate.exceptions import ResponseTruncatedError  # noqa: PLC0415

            self._cut_off = True
            raise ResponseTruncatedError()
        return _PROBE_REVIEW_ANSWER


def _captured(
    mode: AnswerMode, code: str, profile: RequestProfile
) -> tuple[list[dict[str, Any]], int]:
    """Return the requests the app's corrector sends for the probe text, and its text budget.

    A review request is cut off once, so the corrector asks its second try: two requests, the
    same one twice.  Text mode has no retry, so it sends one.

    Raises:
        RuntimeError: if the corrector sent another number of requests, so the capture cannot
            quietly change shape.
    """
    client = _CapturingClient()
    corrector = app_corrector_factory(
        client, timeout=60.0, mode=mode, num_ctx=NUM_CTX, profile=profile
    )("fingerprint", 0.0)
    name = LANGUAGE_NAMES[code]
    corrector.correct(_PROBE_TEXT, name)
    expected = 2 if mode is AnswerMode.REVIEW else 1
    if len(client.requests) != expected:
        raise RuntimeError(
            f"the fingerprint expected {expected} request(s) for {mode}/{code}, "
            f"the corrector sent {len(client.requests)}"
        )
    budget = innermost(corrector).max_text_chars(name)
    return client.requests, int(budget or 0)


def fingerprint() -> dict:
    """Return the request side of a run: what the fingerprint hashes, as sorted JSON.

    Captured from the app's own corrector (built by :func:`~.app_corrector.app_corrector_factory`,
    the factory a run uses): for every answer mode, language and profile variant, a hash of every
    request the probe text produces (system and user message, answer schema, options) and the
    text budget the app splits a long text by; plus the repair message and the answer format
    version.  A review request is cut off once, so its second try is captured too.  A change
    anywhere in that means the run measured something else, so it needs a new version.

    Printing it before and after a change shows which part moved.
    """
    requests: dict[str, dict[str, dict[str, list[str]]]] = {}
    budgets: dict[str, dict[str, dict[str, int]]] = {}
    for mode in MODES:
        requests[mode.value], budgets[mode.value] = {}, {}
        for code in FINGERPRINT_LANGUAGES:
            requests[mode.value][code], budgets[mode.value][code] = {}, {}
            for label, profile in PROFILE_VARIANTS:
                sent, budget = _captured(mode, code, profile)
                requests[mode.value][code][label] = [
                    sha256_text(json.dumps(request, sort_keys=True, ensure_ascii=False))
                    for request in sent
                ]
                budgets[mode.value][code][label] = budget
    return {
        "requests": requests,
        "text_budget": budgets,
        "repair": sha256_text(repair_message("{previous}", "{error}")),
        "repair_answer_tokens": answers_with_the_same_text(NUM_CTX),
        "answer_format_version": ANSWER_FORMAT_VERSION,
    }


def fingerprint_sha256() -> str:
    """Return the SHA-256 of :func:`fingerprint`, which is what a run header records."""
    return sha256_text(json.dumps(fingerprint(), sort_keys=True, ensure_ascii=False))


def read_protocol(path: Path | None = None) -> dict[str, Any]:
    """Return the version file, refusing one that cannot be read or is not an object.

    Raises:
        ProtocolError: if the file is missing, is not JSON, or does not hold a JSON object.
    """
    source = PROTOCOL_PATH if path is None else path
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError(f"{source}: cannot be read as JSON ({error})") from error
    if not isinstance(data, dict):
        raise ProtocolError(f"{source}: the protocol is not an object")
    return data


def entry(version: int, protocol: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return the entry of *version*, or ``None`` when the file does not record it."""
    data = read_protocol() if protocol is None else protocol
    for candidate in data.get("versions", ()):
        if isinstance(candidate, dict) and candidate.get("version") == version:
            return candidate
    return None


def is_recorded(
    version: Any, fingerprint_hash: Any, protocol: dict[str, Any] | None = None
) -> bool:
    """Whether ``(version, fingerprint)`` is an entry of the version file.

    A run made with a protocol the file does not record is evidence of nothing: nobody can say
    which prompts, window or scoring it used.
    """
    if not isinstance(version, int) or isinstance(version, bool):
        return False
    recorded = entry(version, protocol)
    return recorded is not None and recorded.get("fingerprint") == fingerprint_hash


def current_stamp(protocol: dict[str, Any] | None = None) -> Stamp:
    """Return the stamp of ``current``, after checking the request side against it.

    This is the one way to get the version a new run records, and it is the checked one: what a
    header records is what was compared.  It replaces the git "dirty" check: an uncommitted
    prompt edit is caught the same way as a committed one, because what is compared is the
    request the code builds, not the tree.

    Raises:
        ProtocolError: if the file cannot be read, breaks its rules (:func:`problems`), has no
            entry for ``current``, or the request side hashes differently from that entry.
    """
    data = read_protocol() if protocol is None else protocol
    broken = problems(data)
    if broken:
        raise ProtocolError(f"{PROTOCOL_PATH.name} is not valid: " + "; ".join(broken))
    version = int(data["current"])
    recorded = entry(version, data)
    found = fingerprint_sha256()
    if recorded is None or recorded.get("fingerprint") != found:
        raise ProtocolError(
            f"the prompts, the answer format or the window have changed since version {version}: "
            f"the request side hashes to {found}, {PROTOCOL_PATH.name} records "
            f"{None if recorded is None else recorded.get('fingerprint')}.  Either add a version "
            f"to {PROTOCOL_PATH.name} with a line saying why, or undo the change."
        )
    return Stamp(eval_version=version, fingerprint=found)


def _as_object(value: Any) -> dict[str, Any] | None:
    """Return *value* as a JSON object, or ``None`` when it is not one."""
    return value if isinstance(value, dict) else None


def problems(protocol: dict[str, Any] | None = None) -> list[str]:
    """Return what is wrong with the version file: its order, its entries, its ``current``.

    A version file is the record of what every committed run measured, so it is checked like a
    run file: ascending versions, no duplicate, a fingerprint and a reason per entry, and a
    ``current`` that is one of them.
    """
    data = read_protocol() if protocol is None else protocol
    found: list[str] = []
    if data.get("schema") != 1:
        found.append(f"schema is {data.get('schema')!r}, not 1")
    versions = data.get("versions")
    if not isinstance(versions, list) or not versions:
        return [*found, "versions is not a non-empty list"]
    numbers: list[int] = []
    for position, item in enumerate(versions):
        record = _as_object(item)
        if record is None:
            found.append(f"entry {position} is not an object")
            continue
        number = record.get("version")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            found.append(f"entry {position} has no positive integer version")
            continue
        numbers.append(number)
        if not (isinstance(record.get("fingerprint"), str) and _HEX64.match(record["fingerprint"])):
            found.append(f"version {number} has no SHA-256 fingerprint")
        if not str(record.get("why", "")).strip():
            found.append(f"version {number} has no reason")
        if not str(record.get("date", "")).strip():
            found.append(f"version {number} has no date")
    if numbers != sorted(numbers):
        found.append("the versions are not in ascending order")
    if len(set(numbers)) != len(numbers):
        found.append("a version appears twice")
    current = data.get("current")
    if not isinstance(current, int) or isinstance(current, bool) or current not in numbers:
        found.append(f"current {current!r} is not one of the versions")
    return found


__all__ = [
    "FINGERPRINT_LANGUAGES",
    "MODES",
    "NUM_CTX",
    "PROFILE_VARIANTS",
    "PROTOCOL_PATH",
    "ProtocolError",
    "Stamp",
    "current_stamp",
    "entry",
    "fingerprint",
    "fingerprint_sha256",
    "instructions_sha256",
    "is_recorded",
    "problems",
    "read_protocol",
]
