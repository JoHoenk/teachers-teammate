"""The connection to a model, behind one port.

An exchange asks for a step; a port sends it.  The two vary independently: the same exchange
runs on a local Ollama server and on an online provider, and the same connection serves every
step of every exchange.  A port returns what the model said and how the request ended — never a
domain result — so the exchange alone decides what an answer means.

The Ollama port is the one that knows the context window (:meth:`ModelPort.window`); a LangChain
provider manages its own, so it returns ``None`` and the runner skips the window checks.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Protocol

from ...domain.context_budget import ContextBudget
from ...domain.proofreading.result import FailureKind, StepName
from ...exceptions import TRUNCATED_MESSAGE
from .._llm_invoke import (
    _CORRECTION_FAILURE,
    _failure_warning,
    _rejected_request,
    invoke_langchain_chain,
    invoke_langchain_structured,
)
from ..correction_schema import escape_braces
from ..ollama_utils import PROOFREADING_NUM_CTX

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from pydantic import BaseModel

    from ..ollama_utils import OllamaClient

REVIEW_TIMEOUT_CAP = 600.0
"""Upper bound in seconds for a request whose answer is long, whatever the text length is."""


def _review_timeout(base_timeout: float, text: str) -> float:
    """Return the timeout of a request with a long answer: it needs longer than the OCR timeout.

    The cap bounds the growth with the text, never a timeout the user configured themselves.
    """
    return max(base_timeout, min(REVIEW_TIMEOUT_CAP, 60.0 + 0.05 * len(text)))


@dataclass(frozen=True)
class StepRequest:
    """One request of one step: the messages, the answer shape, and how to send it."""

    step: StepName
    system: str
    user: str
    schema: type[BaseModel] | None = None
    """The answer shape of a structured step; ``None`` asks for plain text."""
    temperature: float = 0.0
    think: bool | str = False
    answer_factor: float = 1.0
    """How long the answer is compared with the request: the window check and the timeout."""
    repairable: bool = True
    """Whether an answer that does not match the schema is worth one repair request."""


@dataclass(frozen=True)
class PortAnswer:
    """What one request returned: the answer, how it ended, and what it cost."""

    raw: str = ""
    """The answer text, as the model wrote it (empty when it wrote nothing usable)."""
    failure: FailureKind = ""
    error: str = ""
    """For ``transport``, the complete warning to show; otherwise the reason, for the model."""
    parsed: object | None = None
    """The answer the provider already validated, when it did; the runner uses it as it is."""
    prompt_tokens: int | None = None
    answer_tokens: int | None = None
    seconds: float = 0.0


class ModelPort(Protocol):
    """One connection to a model, as the steps of an exchange see it."""

    def ask_text(self, req: StepRequest) -> PortAnswer:
        """Send *req* and return the plain-text answer."""
        ...

    def ask_structured(self, req: StepRequest) -> PortAnswer:
        """Send *req* and return the answer to its schema."""
        ...

    def window(self) -> ContextBudget | None:
        """Return the context window this port enforces, or ``None`` when the provider does."""
        ...


class OllamaPort:
    """A local or school Ollama server, asked through the native HTTP API."""

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        *,
        timeout: float = 120.0,
        num_ctx: int = PROOFREADING_NUM_CTX,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout = timeout
        self._num_ctx = num_ctx

    def window(self) -> ContextBudget:
        """Return the window this port asks Ollama for and checks every request against."""
        return ContextBudget(self._num_ctx)

    def ask_text(self, req: StepRequest) -> PortAnswer:
        """Send *req* as a plain chat request."""
        return self._send(req, schema=None)

    def ask_structured(self, req: StepRequest) -> PortAnswer:
        """Send *req* with its schema as Ollama's ``format``, which constrains the answer."""
        if req.schema is None:
            raise ValueError("a structured request needs a schema")
        return self._send(req, schema=req.schema.model_json_schema())

    def _send(self, req: StepRequest, *, schema: dict | None) -> PortAnswer:
        """Send *req* and return what came back, with the statistics of the request."""
        from ...exceptions import ResponseTruncatedError, RunStoppedError  # noqa: PLC0415
        from ..ollama_utils import proofreading_options  # noqa: PLC0415

        started = time.perf_counter()
        try:
            text = self._client.chat(
                self._model,
                req.user,
                options=proofreading_options(req.temperature, self._num_ctx),
                think=req.think,
                timeout=self._timeout_of(req),
                format=schema,
                system=req.system,
            )
        except RunStoppedError:
            raise  # a stop is not a failed request: the caller leaves the stage
        except ResponseTruncatedError:
            return self._answer("", TRUNCATED_MESSAGE, started, failure="truncated")
        except Exception as exc:  # noqa: BLE001  # the client may raise any transport error
            if schema is not None and _rejected_request(exc):
                # An older server or a model that refuses ``format``: a plain answer may still
                # work, so this is an unusable answer, not an unreachable server.
                return PortAnswer(failure="invalid", error=str(exc))
            return PortAnswer(failure="transport", error=_failure_warning(_CORRECTION_FAILURE, exc))
        return self._answer(text, None, started)

    def _answer(
        self, text: str, warning: str | None, started: float, *, failure: FailureKind = ""
    ) -> PortAnswer:
        """Return the answer of a request that came back, with what the server reported."""
        stats = self._client.last_stats
        return PortAnswer(
            raw=text,
            failure=failure or ("transport" if warning else ""),
            error=warning or "",
            prompt_tokens=stats.prompt_eval_count if stats is not None else None,
            answer_tokens=stats.eval_count if stats is not None else None,
            seconds=time.perf_counter() - started,
        )

    def _timeout_of(self, req: StepRequest) -> float:
        """Return the timeout of *req*: a long answer needs longer than the OCR timeout."""
        if req.answer_factor <= 0:
            return self._timeout
        return _review_timeout(self._timeout, req.user)


class LangChainPort:
    """Any LangChain chat model, asked through its own structured-output support."""

    def __init__(self, llm: BaseChatModel, *, strict: bool = False) -> None:
        self._llm = llm
        self._strict = strict

    def window(self) -> None:
        """Return ``None``: the provider manages the window of its own requests."""
        return None

    def ask_text(self, req: StepRequest) -> PortAnswer:
        """Send *req* as a plain chat request."""
        text, warning = invoke_langchain_chain(
            self._llm,
            system=escape_braces(req.system),
            human=_TEXT_VARIABLE,
            variables={"text": req.user},
            fallback="",
            failure_prefix="Correction failed",
            failure_suffix="; keeping original text.",
        )
        if warning is not None:
            return PortAnswer(failure="transport", error=warning)
        return PortAnswer(raw=text)

    def ask_structured(self, req: StepRequest) -> PortAnswer:
        """Send *req* constrained to its schema, through the provider's own mechanism."""
        if req.schema is None:
            raise ValueError("a structured request needs a schema")
        result = invoke_langchain_structured(
            self._llm,
            system=escape_braces(req.system),
            human=_TEXT_VARIABLE,
            variables={"text": req.user},
            schema=req.schema,
            strict=self._strict,
        )
        return PortAnswer(
            raw=result.raw,
            parsed=result.parsed,
            failure=_FAILURES.get(result.failure, ""),
            error=result.error,
        )


_TEXT_VARIABLE = "{text}"
"""The message of a LangChain request, with the text as a template variable.

A template variable is filled in after the template is parsed, so braces in the text are not
read as template syntax and need no escaping.
"""

_FAILURES: dict[str, FailureKind] = {
    "": "",
    "truncated": "truncated",
    "invalid": "invalid",
    "transport": "transport",
    # A provider that cannot bind a schema at all answered nothing usable, and there is
    # nothing to repair: the caller falls back the same way as for an invalid answer.
    "unsupported": "invalid",
}
"""How a LangChain answer's failure is named in the domain."""
