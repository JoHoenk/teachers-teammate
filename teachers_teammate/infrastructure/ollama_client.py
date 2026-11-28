"""The Ollama client: every request the app makes to an Ollama server.

Where a server is — its host, and whether it is this computer — is
:mod:`~teachers_teammate.infrastructure.ollama_host`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
import logging
import threading
from typing import TYPE_CHECKING, Any

from ..domain.ollama_address import models_include, normalize_ollama_url
from ..domain.ollama_discovery import WaitDetail, WaitKind
from ..domain.ollama_snapshot import InstalledModel, ServerSnapshot
from ..domain.system_facts import LoadedModel, ServerActivity, ServerFacts
from ..exceptions import (
    OCRError,
    OllamaUnreachableError,
    ResponseTruncatedError,
    RunStoppedError,
    stop_requested,
)
from ..interfaces import ConnectionCheck, Reporter
from ..messages import (
    cannot_connect,
    cannot_reach,
    connection_timed_out,
    contact_failed,
    empty_answer,
    empty_answer_cut_off,
    message_of,
    model_not_found,
    model_pull_failed,
    no_models_installed,
    unreachable,
)
from ..texts.servers import (
    CHECK_NO_MODELS,
    check_cannot_connect,
    check_connected,
    check_model_available,
    check_model_missing,
    check_timed_out,
    check_unexpected,
    model_not_loaded,
    vision_not_supported,
)
from .ollama_request import (
    ChatStats,
    chat_request,
    chunk_stats,
    ensure_prompt_fits,
    open_chat_stream,
    rejects_think,
)
from .ollama_stream import consume_with_first_token_timeout, cut_connection, mapped_stream
from .reporting import StdoutReporter

if TYPE_CHECKING:
    import ollama

logger = logging.getLogger(__name__)


# How long to wait for the TCP connection to be established.
_CONNECT_TIMEOUT = 10


#: Model families that indicate a model can read images (used as a fallback on
#: older Ollama servers that do not report capabilities).
_KNOWN_VISION_FAMILIES = frozenset({"clip", "vision", "deepseekocr"})


#: How long a request may wait before the run is told why (seconds).  A model that is already
#: loaded answers well within this, so it never shows a note.
WAIT_DETAIL_AFTER_S = 3.0


def _is_transport_error(exc: BaseException) -> bool:
    """Return whether *exc* means "server unreachable or timed out".

    Anything else raised by the client (for example ``ollama.ResponseError`` for
    an unknown model) means the server did answer.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return type(exc).__module__.split(".", 1)[0] == "httpx"


def _http_json(url: str, timeout: float) -> dict:
    """Return the JSON answer of *url*, read with the standard library.

    The ``ollama`` client has no method for ``/api/version``, and a version is not worth a
    second HTTP library.  A module-level function so a test can replace it.
    """
    import json  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    with urllib.request.urlopen(url, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8") or "{}")


#: The words a transport error uses when the server could not be reached at all.
_CONNECT_WORDS = ("connect", "refused", "network", "unreachable")


def _trouble_kind(exc: BaseException) -> str:
    """Return why a request stayed unanswered: ``connect``, ``timeout`` or ``other``.

    Read from the message, because the ``ollama`` client raises its own types for all three
    and the wording is the only thing that tells them apart.  It decides which sentence a
    teacher reads, so a caller of a different kind of request keeps its own wording.
    """
    reason = str(exc).lower()
    if any(word in reason for word in _CONNECT_WORDS):
        return "connect"
    return "timeout" if "timeout" in reason or "timed out" in reason else "other"


def _trouble_message(base_url: str, exc: BaseException) -> str:
    """Return the check line for a request to *base_url* that *exc* kept from being answered."""
    kind = _trouble_kind(exc)
    if kind == "connect":
        return check_cannot_connect(base_url)
    if kind == "timeout":
        return check_timed_out(base_url)
    return check_unexpected(exc)


class OllamaClient:
    """All HTTP access to a single Ollama server.

    Holds the normalised base URL so callers never repeat URL normalisation.
    All methods delegate to the ``ollama`` library (lazy-imported via
    :meth:`_client`).
    """

    def __init__(
        self,
        url: str,
        *,
        timeout: float = _CONNECT_TIMEOUT,
        cancel_event: threading.Event | None = None,
        stats_sink: Callable[[str, ChatStats], None] | None = None,
    ) -> None:
        self.base_url = normalize_ollama_url(url)
        self._timeout = timeout
        #: When set, a chat request that is waiting is abandoned (the server stops generating)
        #: and raises :exc:`~teachers_teammate.exceptions.RunStoppedError`.
        self._cancel_event = cancel_event
        #: Called as ``stats_sink(model, stats)`` after every chat request, with what the server
        #: reported about it.  A run shares one client between all its stages, so one sink sees
        #: every request of the run and can turn them into a measurement of the machine.
        self._stats_sink = stats_sink
        self._last_stats: ChatStats | None = None
        #: Called with a :class:`WaitDetail` while a request waits for its first token, so the
        #: run can say what the server is doing.  ``None`` outside a run.
        self.detail_sink: Callable[[WaitDetail], None] | None = None
        #: Called when a request's first token arrives: the run's clock starts there, not when
        #: the request was sent, so a wait behind another model does not move the bar.
        self.first_token_sink: Callable[[], None] | None = None
        #: ``{model: expires_at}`` of the previous look at ``/api/ps``, which is what tells a
        #: generating model from an idle one.
        self._last_expiry: dict[str, str] = {}

    @property
    def last_stats(self) -> ChatStats | None:
        """Return what the server reported about the most recent chat request.

        ``None`` before the first request and after one that never reached the server, so a
        caller that reads it right after its own request cannot pick up an older answer's
        numbers.
        """
        return self._last_stats

    def _client(self) -> ollama.Client:
        """Return an ``ollama.Client`` for :attr:`base_url`."""
        import ollama  # noqa: PLC0415

        return ollama.Client(host=self.base_url, timeout=self._timeout)

    # ── Model discovery ────────────────────────────────────────────────────

    def _listed_or_reason(self) -> tuple[tuple[InstalledModel, ...], BaseException | None]:
        """Return the installed models, or the failure that kept them from being read.

        One request to ``/api/tags``, with this client's timeout.  The caller decides how to
        word the failure: the connection check and a run's start say it differently.
        """
        try:
            resp = self._client().list()
        except Exception as exc:  # noqa: BLE001  # any client error means the server is unreachable
            return (), exc
        return (
            tuple(
                InstalledModel(
                    tag=str(m.model),
                    digest=str(m.digest or ""),
                    size_bytes=int(m.size or 0),
                )
                for m in resp.models
                if m.model
            ),
            None,
        )

    def snapshot(self) -> ServerSnapshot:
        """Return one answer of the server: its version, its models, their digests and sizes.

        Never raises: a server that cannot be asked says why in
        :attr:`~teachers_teammate.domain.ollama_snapshot.ServerSnapshot.unreachable` instead,
        which is what tells "no connection" apart from "no model downloaded".  Two requests:
        ``/api/tags`` for what it holds and ``/api/version`` for what it is.
        """
        models, trouble = self._listed_or_reason()
        if trouble is not None:
            return ServerSnapshot(unreachable=_trouble_message(self.base_url, trouble))
        return ServerSnapshot(version=self.server_version(), models=models)

    def list_models(self) -> list[str]:
        """Return model names from Ollama, or ``[]`` on any error (one request)."""
        models, _trouble = self._listed_or_reason()
        return [model.tag for model in models]

    # ── Server facts ───────────────────────────────────────────────────────

    def server_version(self) -> str:
        """Return the version of the Ollama server, or ``""`` when it does not say."""
        try:
            return str(_http_json(f"{self.base_url}/api/version", self._timeout).get("version", ""))
        except Exception:  # noqa: BLE001  # an old server or a hiccup: the version is optional
            return ""

    def _ps_models(self) -> tuple[LoadedModel, ...] | None:
        """Return the models in memory, or ``None`` when the server could not be asked."""
        try:
            resp = self._client().ps()
            return tuple(
                LoadedModel(
                    name=str(m.model or m.name or ""),
                    size_bytes=int(m.size or 0),
                    size_vram_bytes=int(m.size_vram or 0),
                    context_length=int(m.context_length or 0),
                    expires_at=str(getattr(m, "expires_at", "") or ""),
                )
                for m in resp.models
                if (m.model or m.name)
            )
        except Exception:  # noqa: BLE001  # older Ollama versions may not have /api/ps
            return None

    def loaded_models(self) -> tuple[LoadedModel, ...]:
        """Return the models the server holds in memory right now, or ``()`` on any error."""
        return self._ps_models() or ()

    def activity(self, model: str = "") -> ServerActivity:  # noqa: ARG002  # callers ask `has(model)`
        """Return what the server is doing, and whether *model* is in memory.

        A model whose ``expires_at`` moved since the previous call is generating.  That is
        undocumented behaviour, so it only chooses the wording of a note.  Never raises: an
        unreachable server reports nothing loaded and nothing busy.
        """
        loaded = self._ps_models()
        if loaded is None:
            return ServerActivity(reachable=False)
        busy: list[str] = []
        seen: dict[str, str] = {}
        for item in loaded:
            seen[item.name] = item.expires_at
            before = self._last_expiry.get(item.name)
            if before and item.expires_at and before != item.expires_at:
                busy.append(item.name)
        self._last_expiry = seen
        return ServerActivity(loaded=loaded, busy=tuple(busy))

    def unload(self, model: str) -> None:
        """Ask the server to drop *model* from memory (``keep_alive=0``).

        Never raises: a model that stays loaded only costs memory, and the caller is on its way
        out anyway.
        """
        try:
            self._client().generate(model=model, prompt="", keep_alive=0)
        except Exception:  # an old server or a hiccup: nothing to do about it
            logger.debug("could not unload %s from Ollama", model, exc_info=True)

    def server_facts(self) -> ServerFacts:
        """Return version, model digests and loaded models in one go.

        Never raises: a server that answers only part of it gives what it gave.  The caller
        decides what a missing version or digest means (a measurement without them cannot be
        told apart from one of another build).  The version and the digests come from one
        answer (:meth:`snapshot`); what is in memory is asked separately, because it changes
        with every request.
        """
        return self.snapshot().facts(loaded=self.loaded_models())

    # ── Connectivity checks ────────────────────────────────────────────────

    def check_connection(self, model: str = "") -> ConnectionCheck:
        """Return ``(connected, model_ok, message)`` for the Ollama server.

        * ``connected=False`` — server is unreachable.
        * ``connected=True, model_ok=False`` — server is up, but *model* is
          not installed (only meaningful when *model* is provided).
        * ``connected=True, model_ok=True`` — server is up and *model* is
          available (or no model was requested).
        """
        # Only /api/tags: the readiness line asks every few seconds and needs no version.
        listed, trouble = self._listed_or_reason()
        if trouble is not None:
            return ConnectionCheck(False, False, _trouble_message(self.base_url, trouble))

        models = [model.tag for model in listed]
        if not models:
            return ConnectionCheck(True, False, CHECK_NO_MODELS)
        if not model:
            return ConnectionCheck(True, True, check_connected(len(models)))
        if models_include(model, models):
            return ConnectionCheck(True, True, check_model_available(model))
        return ConnectionCheck(True, False, check_model_missing(model, ", ".join(models)))

    def model_capabilities(self, tag: str) -> frozenset[str] | None:
        """Return the capabilities of *tag* (e.g. ``{"completion", "vision"}``).

        Uses ``show().capabilities`` on newer Ollama servers.  On older servers
        it falls back to the model-families heuristic: a family in
        ``{"clip", "vision", "deepseekocr"}`` means the model can read images,
        any other family means text-only completion.

        Returns:
            Frozenset of capability names, or ``None`` when the model is unknown
            to the server or the server reports neither capabilities nor families.

        Raises:
            :exc:`~teachers_teammate.exceptions.OllamaUnreachableError`: The server
                is unreachable or timed out, so callers can stop asking it.
        """
        try:
            resp = self._client().show(tag)
        except Exception as exc:  # the Ollama client may raise anything; classified below
            if _is_transport_error(exc):
                raise OllamaUnreachableError(cannot_reach(self.base_url, message_of(exc))) from exc
            return None  # the server answered but cannot describe the model

        try:
            caps = getattr(resp, "capabilities", None)
            if isinstance(caps, (list, tuple, frozenset)) and caps:
                return frozenset(str(c).lower() for c in caps)

            details = getattr(resp, "details", None)
            families = list(details.families or []) if details else []
        except Exception:  # noqa: BLE001  # an unexpected response shape is "no information"
            return None
        if any(f.lower() in _KNOWN_VISION_FAMILIES for f in families):
            return frozenset({"vision"})
        if families:
            return frozenset({"completion"})
        return None

    def check_model(self, model_name: str, reporter: Reporter | None = None) -> None:
        """Verify the server is reachable and *model_name* is available.

        Also warns when the model does not appear vision-capable, and notes
        when the model is not yet loaded (first request will be slow).  Both requests are bound
        by the client's timeout, so a server that hangs fails the check instead of the run start.  Advisory
        lines go through *reporter* (defaulting to :class:`StdoutReporter`, i.e.
        stderr) so adapters can route them into the log file.

        Raises:
            :exc:`~teachers_teammate.exceptions.OllamaUnreachableError`: Server
                is unreachable, or the model is not available.
        """
        reporter = reporter or StdoutReporter()
        models, trouble = self._listed_or_reason()

        if trouble is not None:
            kind = _trouble_kind(trouble)
            if kind == "connect":
                raise OllamaUnreachableError(cannot_connect(self.base_url)) from trouble
            if kind == "timeout":
                raise OllamaUnreachableError(connection_timed_out(self.base_url)) from trouble
            raise OllamaUnreachableError(
                contact_failed(self.base_url, message_of(trouble))
            ) from trouble

        available = [model.tag for model in models]
        if not available:
            raise OllamaUnreachableError(no_models_installed(self.base_url))

        if not models_include(model_name, available):
            avail_str = ", ".join(available)
            raise OllamaUnreachableError(model_not_found(model_name, avail_str))

        try:
            capabilities = self.model_capabilities(model_name)
        except OllamaUnreachableError:
            capabilities = None  # introspection is best-effort; skip the advisory
        if capabilities is not None and "vision" not in capabilities:
            reporter.warn(vision_not_supported(model_name))

        try:
            ps_resp = self._client().ps()
            running = [m.model for m in ps_resp.models if m.model]
            if running and model_name not in running:
                reporter.warn(model_not_loaded(model_name))
        except Exception:  # noqa: BLE001  # older Ollama versions may not have /api/ps; the note is optional
            pass

    # ── Generation ─────────────────────────────────────────────────────────

    def chat(  # noqa: PLR0913  # one keyword shape, shared with chat_with_stats()
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | str | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,
        system: str = "",
        allow_truncated: bool = False,
        answer_tokens: int | None = None,
    ) -> str:
        """Send a chat request and return the response text, refusing a cut-off one.

        See :meth:`chat_with_stats` for the arguments.  An answer Ollama stopped at a limit
        (``done_reason="length"``: the context window was full, or ``num_predict`` was reached)
        would be a beginning only, so this raises instead of returning it — the text it was asked
        about goes on past the end of such an answer.  A request that sets ``num_ctx`` and is too
        long for it is refused up front (see :func:`ensure_prompt_fits`).

        Args:
            allow_truncated: Return a cut-off answer instead of raising (logging a warning).
                For callers whose output limit is deliberate, like the OCR page limit, where a
                partial page is still the best available result.
            answer_tokens: How many tokens the answer may take.  ``None`` — as many as the
                request, which is the proofreading shape (the answer repeats the text).

        Raises:
            :exc:`~teachers_teammate.exceptions.ResponseTruncatedError`: the request did not fit
                the context window, or the answer was cut off.
            :exc:`~teachers_teammate.exceptions.OCRError`: on any other failure.
        """
        num_ctx = (options or {}).get("num_ctx")
        if num_ctx and not images:
            ensure_prompt_fits(system, prompt, int(num_ctx), answer_tokens=answer_tokens)
        content, stats = self.chat_with_stats(
            model,
            prompt,
            images=images,
            options=options,
            think=think,
            timeout=timeout,
            format=format,
            system=system,
        )
        if stats.truncated:
            if not allow_truncated:
                raise ResponseTruncatedError()
            logger.warning("ollama chat %s: answer cut off at its output limit", model)
            if not content.strip():
                # A cut-off answer without any text is no partial page: it is nothing, and
                # returning "" would silently drop the page from the document.
                raise OCRError(empty_answer_cut_off())
        return content

    def chat_with_stats(  # noqa: PLR0913  # one keyword shape, shared with chat()
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | str | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,
        system: str = "",
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Send a chat request and return ``(text, stats)``.

        Unlike :meth:`chat` this returns a cut-off answer too, together with the statistics that
        mark it as cut off, so a caller can report what happened instead of guessing.

        Args:
            model:   Ollama model tag.
            prompt:  User message text.
            images:  Optional list of base64-encoded images (for vision models).
            options: Optional Ollama options dict (e.g. ``{"temperature": 0}``).
            think:   Whether to let reasoning models emit ``<think>`` blocks.
                     Defaults to ``False`` to keep reasoning out of OCR output.  A family that
                     takes a reasoning level instead (``gpt-oss``) needs that level, which the
                     caller reads from ``RequestProfile.think``.
                     Forwarded to Ollama; ignored (with a no-``think`` retry) on
                     older clients or models that don't support it.
            timeout: Seconds to wait for the first token of the answer.
            system:  Optional system message sent before the user message.
            total_timeout: Seconds the whole answer may take; ``None`` (the app's choice) sets no
                     limit once the answer has begun.  Evaluation tools set it so a model that
                     loops cannot hold a run for a quarter of an hour.
            format:  Optional response format.  A JSON schema (dict) makes Ollama
                     constrain the decoding to that schema, which is how proofreading
                     gets a machine-readable answer; ``None`` leaves it free-form.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on any failure.
        """
        if stop_requested(self._cancel_event):
            raise RunStoppedError
        self._last_stats = None
        request = chat_request(
            model,
            prompt,
            images=images,
            options=options,
            response_format=format,
            system=system,
        )
        chunks, waited = self._stream_chunks(
            model, request, think=think, timeout=timeout, total_timeout=total_timeout
        )
        return self._answer_from_chunks(model, chunks, waited=waited)

    def _stream_chunks(
        self,
        model: str,
        request: dict,
        *,
        think: bool | str | None,
        timeout: float,
        total_timeout: float | None,
    ) -> tuple[list[Any], bool]:
        """Return the chunks of the answer, and whether the server was busy with another model.

        A model that rejects ``think`` is asked again without it.  Any other error is raised:
        a model that is not pulled or a server that is down does not answer twice as fast.
        """
        client, stream = self._open_answer(request, think, timeout=timeout)
        waited = False
        reported: WaitDetail | None = None

        def _waiting(elapsed: float) -> bool:
            """Say what the server is doing; return whether it is busy with another model.

            Only a busy server pauses the timeout and marks the sample: loading the model is
            this request's own work, so its time stays in the timeout and in the load figure.
            """
            nonlocal waited, reported
            detail = self.activity(model).wait_detail(model)
            if not detail:
                return False
            if elapsed >= WAIT_DETAIL_AFTER_S and detail != reported:
                reported = detail
                if self.detail_sink is not None:
                    self.detail_sink(detail)
            if detail.kind is not WaitKind.BUSY:
                return False
            waited = True
            return True

        try:
            chunks = self._consume(stream, client, timeout, total_timeout, _waiting)
        except OCRError as exc:
            # A model that does not support ``think`` rejects it with a 400 that names it.
            if think is None or not rejects_think(exc):
                raise
            client, stream = self._open_answer(request, None, timeout=timeout)
            chunks = self._consume(stream, client, timeout, total_timeout, _waiting)
        return chunks, waited

    def _open_answer(
        self, request: dict, think: bool | str | None, *, timeout: float
    ) -> tuple[Any, Iterator]:
        """Return the client and the stream of *request*; a client without ``think`` retries."""
        import ollama  # noqa: PLC0415

        try:
            client = ollama.Client(host=self.base_url, timeout=timeout)
            try:
                stream = open_chat_stream(client, request, think)
            except TypeError:
                # Older ollama clients lack the ``think`` kwarg. The output cleaner strips any
                # leaked reasoning regardless.
                stream = open_chat_stream(client, request, None)
        except Exception as exc:
            raise OllamaUnreachableError(unreachable(self.base_url, message_of(exc))) from exc
        return client, stream

    def _consume(
        self,
        stream: Iterator,
        client: Any,
        timeout: float,
        total_timeout: float | None,
        waiting: Callable[[float], bool],
    ) -> list[Any]:
        """Read the answer to its end, returning the chunks in the order they arrived."""
        return consume_with_first_token_timeout(
            mapped_stream(stream, self.base_url),
            timeout,
            total_timeout,
            self._cancel_event,
            on_abandon=lambda: cut_connection(client),
            waiting=waiting,
            on_first_token=self.first_token_sink,
        )

    def _answer_from_chunks(
        self, model: str, chunks: list[Any], *, waited: bool
    ) -> tuple[str, ChatStats]:
        """Return the text of the answer and its statistics, and report the statistics."""
        parts = [(chunk.message.content or "") for chunk in chunks if chunk.message]
        content = "".join(parts)
        # The last chunk carries the summary of the whole answer; earlier ones leave these unset.
        stats = chunk_stats(chunks[-1]) if chunks else ChatStats()
        if waited:
            stats = replace(stats, waited=True)
        self._last_stats = stats
        # Before the checks below: a request that is refused here still cost the time the
        # server reported, and the sink decides for itself whether the numbers are usable.
        if self._stats_sink is not None:
            self._stats_sink(model, stats)
        # A reasoning model can spend the whole window on thinking it does not return: no text,
        # but a stop at the limit.  That is a cut-off answer, not an empty one -- say so.
        if not content.strip() and not stats.truncated:
            raise OCRError(empty_answer())
        logger.debug(
            "ollama chat %s: done_reason=%r prompt_eval_count=%d eval_count=%d",
            model,
            stats.done_reason,
            stats.prompt_eval_count,
            stats.eval_count,
        )
        return content, stats

    # ── Model management ───────────────────────────────────────────────────

    def pull(
        self,
        model_name: str,
        progress_cb: Callable[[str, float, float], None] | None = None,
        abort_event: threading.Event | None = None,
    ) -> None:
        """Pull (download) *model_name* from Ollama.

        Args:
            model_name:  Model tag to pull (e.g. ``"llama3.2:latest"``).
            progress_cb: Optional callback ``progress_cb(status, completed, total)``.
            abort_event: When set, stops consuming the stream after the current chunk.
        """
        import ollama  # noqa: PLC0415

        client = ollama.Client(host=self.base_url)
        try:
            for resp in client.pull(model_name, stream=True):
                if abort_event and abort_event.is_set():
                    return
                if progress_cb:
                    progress_cb(
                        resp.status or "", float(resp.completed or 0), float(resp.total or 0)
                    )
        except Exception as exc:
            raise OCRError(model_pull_failed(message_of(exc))) from exc
