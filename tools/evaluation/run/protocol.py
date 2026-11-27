"""The protocol of a measured run: the capture of its request side, and the version it records.

Three versions are easy to mix up:

* ``format`` — the layout of a JSON file, which fields exist.  It sits in every run, manifest and
  machine file and changes only when the format changes;
* ``eval_version`` — what a run measured and how: prompts, answer format, window.  It lives in
  ``core/protocol.json`` and is copied into every run header;
* ``tool_revision`` — the commit the tools were at.  Information only: nothing checks it, groups
  by it or refuses because of it.

Runs are grouped and compared by ``instructions`` and the other axes, never by commit.  The
*fingerprint* of a version is the SHA-256 of the request side, *captured* from the correctors a run
builds: what one request per language and answer mode carries (system and user message, answer
schema, options), the text budget per language, the repair message and the answer format version.
Building the corrector with the factory a run uses means a new request setting is covered without
anyone editing this module.  ``run`` computes it and refuses to start when the table does not
accept it, so a prompt change, committed or not, forces a new version.  A change of the scoring,
the caps or the edit extraction does not move the fingerprint; the golden numbers of the tests fail
instead and remind you to raise the version by hand.

The table itself — which fingerprints a version accepts, and which version a run measured — is
:mod:`..core.protocol_table`; this module is the capture that the table's entries are made of.

Old entries are never edited or removed, so every committed run stays checkable against the
version it names.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Any

from teachers_teammate.domain.context_budget import (
    BASE_NUM_CTX,
    ContextBudget,
    answers_with_the_same_text,
)
from teachers_teammate.domain.correction_policy import DEFAULT_LEVEL
from teachers_teammate.domain.correction_prompts import CORRECT_VARIANTS, repair_message
from teachers_teammate.domain.formatted_text import PLAIN, TextFormat
from teachers_teammate.domain.model_catalog import (
    DEFAULT_REQUEST_PROFILE,
    RequestProfile,
    StepProfile,
)
from teachers_teammate.infrastructure.ollama_utils import OllamaClient
from teachers_teammate.infrastructure.proofreading.corrector import ollama_exchange_corrector
from teachers_teammate.infrastructure.proofreading.port import PortAnswer, StepRequest

from ..core import protocol_table
from ..core.hashing import sha256_text
from ..core.languages import LANGUAGE_NAMES
from ..core.modes import (
    ALL_STEPS,
    DEFAULT_EXCHANGE,
    DEFAULT_STEPS,
    ONE_REQUEST_OUTPUT,
    Exchange,
    Setup,
    Step,
    setup_label,
)
from .app_corrector import (
    ONE_REQUEST_EXCHANGES,
    RUNNABLE_SETUPS,
    OnlyStepsPort,
    app_corrector_factory,
    innermost,
    steps_that_run,
)
from .one_request.answer_format import ANSWER_FORMAT_VERSION
from .one_request.correction import instruction_parts

NUM_CTX = BASE_NUM_CTX
"""The window the protocol measures at; the run and the fingerprint both use it."""

SETUPS: tuple[Setup, ...] = RUNNABLE_SETUPS
"""The setups the fingerprint covers: every one a run can ask in."""

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

EXCHANGE_PROFILE_VARIANTS: tuple[tuple[str, RequestProfile], ...] = (
    ("plain", DEFAULT_REQUEST_PROFILE),
    ("no-unclear", RequestProfile(unclear_check=False)),
    *(
        (f"prompt-{variant}", RequestProfile(correct=StepProfile(prompt=variant)))
        for variant in sorted(CORRECT_VARIANTS)
    ),
    (
        "sampled",
        RequestProfile(
            correct=StepProfile(options=(("presence_penalty", 0.5), ("top_p", 0.9))),
            explain=StepProfile(options=(("top_k", 20.0),)),
        ),
    ),
)
"""The request profiles the fingerprint covers for the exchange: the knobs that change which
requests it sends are whether step ③ runs and the sampling values of a step.  Temperature and
thinking are the run's, in its header, and ``compact_json`` is a knob of the review request only.
The sampled profile stands for every set of values: it proves the values reach the request of the
step they belong to, and only that one.  Each step-① prompt variant is covered by its own entry,
so its text is part of the fingerprint: changing a variant needs a new version."""


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
    exchange: Exchange = DEFAULT_EXCHANGE,
    steps: tuple[Step, ...] = DEFAULT_STEPS,
    level: str = DEFAULT_LEVEL,
    text_format: TextFormat = PLAIN,
    compact_json: bool = False,
    prompt: str = "",
) -> str:
    """Return the hash of the instructions a run's requests carried, one part per language.

    The prompt the app sends depends on the language, the level, the text format, the exchange and
    the steps of it that run, and, in a one-request review, whether the model's request profile
    adds the compact-JSON rule.  The comparison sends the language *name* ("German") and plain
    text, so that is what is asked here; *languages* are the codes of the run (``de``), mapped
    through :data:`~..core.languages.LANGUAGE_NAMES`.  A run covers several languages, so the hash
    covers all of them, in the alphabetical order of their names.  A changed prompt, level or
    answer format therefore changes the hash, and a reader sees that a stored run was asked
    differently from today's code.

    Raises:
        ValueError: if a language has no name (its prompt would silently be the English one), or
            if *exchange* is not one the tools build a corrector for.
    """
    names = []
    for code in languages:
        if code not in LANGUAGE_NAMES:
            raise ValueError(f"no language name for {code!r}; the instructions cannot be hashed")
        names.append(LANGUAGE_NAMES[code])
    if exchange is Exchange.TEXT_FIRST:
        return _exchange_instructions_sha256(
            names, level, steps=tuple(step.value for step in steps), prompt=prompt
        )
    if exchange not in ONE_REQUEST_EXCHANGES:
        raise ValueError(f"no instructions for the exchange {exchange.value!r}")
    parts: list[str] = []
    for name in sorted(names):
        policy, fixed = instruction_parts(
            "",
            name,
            level=level,
            text_format=text_format,
            mode=ONE_REQUEST_OUTPUT[exchange],
            answer_format="",
            compact_json=compact_json,
        )
        parts.append(f"## {name}\n{policy}\n\n{fixed}")
    return sha256_text("\n\n---\n\n".join(parts))


def _exchange_instructions_sha256(
    names: Iterable[str],
    level: str,
    *,
    steps: tuple[str, ...] = ("correct", "explain", "unclear"),
    prompt: str = "",
) -> str:
    """Return the hash of the exchange's system prompts of *steps* and its parameter lines.

    The exchange has no per-language prompt: its system prompts are fixed and versioned, and the
    language, level and notes travel in the user message.  So the hash covers the prompts, their
    version and the parameter lines of every language of the run.  It is recorded in the run
    header, so a changed prompt stops a run from being resumed; that a changed prompt needs a new
    ``eval_version`` is the fingerprint's job.
    """
    from teachers_teammate.domain.correction_prompts import (  # noqa: PLC0415
        EXPLAIN_SYSTEM,
        PROMPT_VERSION,
        UNCLEAR_SYSTEM,
        correct_system,
        parameters,
    )

    prompts = {
        "correct": correct_system(prompt),
        "explain": EXPLAIN_SYSTEM,
        "unclear": UNCLEAR_SYSTEM,
    }
    parts = [f"prompt version {PROMPT_VERSION}", *(prompts[step] for step in steps)]
    parts.extend(f"## {name}\n{parameters(name, level=level)}" for name in sorted(names))
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
        format: dict | str | None = None,
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
    exchange: Exchange, code: str, profile: RequestProfile
) -> tuple[list[dict[str, Any]], int]:
    """Return the requests the one-request corrector sends for the probe text, and its text budget.

    A review request is cut off once, so the corrector asks its second try: two requests, the
    same one twice.  A text request has no retry, so it sends one.

    Raises:
        RuntimeError: if the corrector sent another number of requests, so the capture cannot
            quietly change shape.
    """
    client = _CapturingClient()
    corrector = app_corrector_factory(
        client,
        timeout=60.0,
        exchange=exchange,
        steps=ALL_STEPS,
        num_ctx=NUM_CTX,
        profile=profile,
    )("fingerprint", 0.0)
    name = LANGUAGE_NAMES[code]
    corrector.correct(_PROBE_TEXT, name)
    expected = 2 if exchange is Exchange.ONE_REQUEST_REVIEW else 1
    if len(client.requests) != expected:
        raise RuntimeError(
            f"the fingerprint expected {expected} request(s) for {exchange}/{code}, "
            f"the corrector sent {len(client.requests)}"
        )
    budget = innermost(corrector).max_text_chars(name)
    return client.requests, int(budget or 0)


class _CapturingPort:
    """A model port that keeps every request of the exchange and answers each step validly.

    The probe text ``Probe.`` is "corrected" to ``Probe!``: one edit, so step ② is asked, and
    step ③ answers that nothing is unclear.  What each step sends — its system and user message,
    its answer schema, its thinking and its answer budget — is the request side of the exchange;
    the temperature belongs to the run and is not kept.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def window(self) -> ContextBudget:
        """Return the window the protocol measures at."""
        return ContextBudget(NUM_CTX)

    def ask_text(self, req: StepRequest) -> PortAnswer:
        """Keep *req* and answer a corrected probe text."""
        self._keep(req)
        return PortAnswer(raw="Probe!")

    def ask_structured(self, req: StepRequest) -> PortAnswer:
        """Keep *req* and answer an explanation of the one edit, or nothing unclear."""
        self._keep(req)
        if req.step == "explain":
            raw = '{"edits": [{"id": 1, "category": "punctuation", "explanation": "x"}]}'
        else:
            raw = '{"unclear": []}'
        return PortAnswer(raw=raw)

    def _keep(self, req: StepRequest) -> None:
        self.requests.append(
            {
                "step": req.step,
                "system": req.system,
                "prompt": req.user,
                "format": None if req.schema is None else req.schema.model_json_schema(),
                "think": req.think,
                "answer_factor": req.answer_factor,
                "repairable": req.repairable,
                # Only a request that carries sampling values lists them, so a request without
                # (every request until protocol version 3) hashes as it always did.
                **({"options": dict(req.options)} if req.options else {}),
            }
        )


def _captured_exchange(
    code: str, profile: RequestProfile, steps: tuple[Step, ...]
) -> tuple[list[dict[str, Any]], int]:
    """Return the requests the exchange sends for the probe text, and its text budget.

    *steps* limits the capture to those steps, narrowed by *profile* as a run narrows them; the
    other steps are refused at the port exactly as a run refuses them, so what is captured is what
    that run sends.

    Raises:
        RuntimeError: if the exchange sent another number of requests than its steps, so the
            capture cannot quietly change shape.
    """
    port = _CapturingPort()
    ran = steps_that_run(steps, profile)
    asked = frozenset(step.value for step in ran)
    every = frozenset(step.value for step in ALL_STEPS)
    sent_to = port if asked >= every else OnlyStepsPort(port, asked)
    corrector = ollama_exchange_corrector(
        OllamaClient("http://capture.invalid"),
        "fingerprint",
        timeout=60.0,
        num_ctx=NUM_CTX,
        profile=profile,
        port=sent_to,
    )
    name = LANGUAGE_NAMES[code]
    corrector.correct(_PROBE_TEXT, name)
    expected = len(asked)
    if len(port.requests) != expected:
        raise RuntimeError(
            f"the fingerprint expected {expected} request(s) for exchange/{code}, "
            f"the exchange sent {len(port.requests)}"
        )
    return port.requests, int(corrector.max_text_chars(name) or 0)


def fingerprint() -> dict:
    """Return the request side of a run: what the fingerprint hashes, as sorted JSON.

    Captured from the correctors a run builds (:func:`~.app_corrector.app_corrector_factory` and
    the app's exchange): for every answer mode, language and profile variant, a hash of every
    request the probe text produces (system and user message, answer schema, options) and the
    text budget the app splits a long text by; plus the repair message and the answer format
    version.  A review request is cut off once, so its second try is captured too.  A change
    anywhere in that means the run measured something else, so it needs a new version.

    Printing it before and after a change shows which part moved.
    """
    requests: dict[str, dict[str, dict[str, list[str]]]] = {}
    budgets: dict[str, dict[str, dict[str, int]]] = {}
    for setup in SETUPS:
        exchange, steps = setup
        key = setup_label(setup)
        requests[key], budgets[key] = {}, {}
        for code in FINGERPRINT_LANGUAGES:
            requests[key][code], budgets[key][code] = {}, {}
            variants = (
                PROFILE_VARIANTS if exchange in ONE_REQUEST_EXCHANGES else EXCHANGE_PROFILE_VARIANTS
            )
            for label, profile in variants:
                if exchange in ONE_REQUEST_EXCHANGES:
                    sent, budget = _captured(exchange, code, profile)
                else:
                    sent, budget = _captured_exchange(code, profile, steps)
                requests[key][code][label] = [
                    sha256_text(json.dumps(request, sort_keys=True, ensure_ascii=False))
                    for request in sent
                ]
                budgets[key][code][label] = budget
    return {
        "requests": requests,
        "text_budget": budgets,
        "repair": sha256_text(repair_message("{error}")),
        "repair_answer_tokens": answers_with_the_same_text(NUM_CTX),
        "answer_format_version": ANSWER_FORMAT_VERSION,
    }


def fingerprint_sha256() -> str:
    """Return the SHA-256 of :func:`fingerprint`, which is what a run header records."""
    return sha256_text(json.dumps(fingerprint(), sort_keys=True, ensure_ascii=False))


def current_stamp(table: dict[str, Any] | None = None) -> Stamp:
    """Return the stamp of ``current``, after checking the request side against it.

    This is the one way to get the version a new run records, and it is the checked one: what a
    header records is what was compared.  It replaces the git "dirty" check: an uncommitted
    prompt edit is caught the same way as a committed one, because what is compared is the
    request the code builds, not the tree.

    Raises:
        ProtocolError: if the table cannot be read, breaks its rules
            (:func:`~..core.protocol_table.problems`), has no ``current``, or the request side
            hashes to a fingerprint that version does not accept.
    """
    try:
        data = protocol_table.read_table() if table is None else table
        broken = protocol_table.problems(data)
        if broken:
            raise ProtocolError(
                f"{protocol_table.TABLE_PATH.name} is not valid: " + "; ".join(broken)
            )
        version = protocol_table.current_version(data)
    except protocol_table.ProtocolTableError as error:
        raise ProtocolError(str(error)) from error
    found = fingerprint_sha256()
    if not protocol_table.accepts(version, found, data):
        raise ProtocolError(
            f"the prompts, the answer format or the window have changed since version {version}: "
            f"the request side hashes to {found}, {protocol_table.TABLE_PATH.name} accepts "
            f"{', '.join(protocol_table.fingerprints_of(version, data)) or 'nothing'}.  Either "
            f"add the fingerprint to version {version} with a line saying why, or raise the "
            f"version by hand, or undo the change."
        )
    return Stamp(eval_version=version, fingerprint=found)


__all__ = [
    "EXCHANGE_PROFILE_VARIANTS",
    "FINGERPRINT_LANGUAGES",
    "NUM_CTX",
    "PROFILE_VARIANTS",
    "SETUPS",
    "ProtocolError",
    "Stamp",
    "current_stamp",
    "fingerprint",
    "fingerprint_sha256",
    "instructions_sha256",
]
