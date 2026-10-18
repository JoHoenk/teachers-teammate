"""Shared Ollama utilities: URL normalisation and the OllamaClient HTTP wrapper.

Centralises all Ollama HTTP access so pipeline stages, GUI helpers, and
connection-check threads use the same code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import functools
import ipaddress
import queue
import socket
import threading
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..exceptions import OllamaConnectionError
from .reporting import Reporter, StdoutReporter

if TYPE_CHECKING:
    import ollama

_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

# How long to wait for the TCP connection to be established.
_CONNECT_TIMEOUT = 10

# Model families that indicate a model can read images (used as a fallback on
# older Ollama servers that do not report capabilities).
_KNOWN_VISION_FAMILIES = frozenset({"clip", "vision", "deepseekocr"})


def normalize_ollama_url(url: str) -> str:
    """Return a normalized Ollama base URL.

    Accepts bare host:port inputs (for example ``127.0.0.1:11434``) and
    prepends ``http://`` so HTTP clients can use it directly.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return _DEFAULT_OLLAMA_URL
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned.rstrip("/")


def ollama_host(url: str) -> str:
    """Return the host name of the Ollama server at *url* (the default server when empty)."""
    normalized = normalize_ollama_url(url)
    return urlparse(normalized).hostname or normalized


@functools.cache
def _own_host_names() -> frozenset[str]:
    """Return the names and addresses under which this computer knows itself.

    Only the machine's own host name is resolved — never an arbitrary host — so
    the lookup does not leak the Ollama URL to a DNS server.  It can still be slow on a
    misconfigured network: call :func:`warm_up_own_host_names` early so the answer is
    ready before a GUI thread asks.
    """
    names: set[str] = set()
    try:
        hostname = socket.gethostname()
        names.update({hostname, socket.getfqdn()})
        names.update(socket.gethostbyname_ex(hostname)[2])
    except OSError:
        pass
    return frozenset(name.lower() for name in names if name)


def warm_up_own_host_names() -> None:
    """Resolve this computer's own names in a background thread (idempotent, non-blocking)."""
    threading.Thread(target=_own_host_names, name="own-host-names", daemon=True).start()


def is_local_ollama_url(url: str) -> bool:
    """Return whether *url* points at this computer.

    True for ``localhost``, the whole loopback range (``127.0.0.0/8``, ``::1``),
    ``0.0.0.0`` and this computer's own host name or address (so the machine's LAN
    IP counts as local too); false for anything else.
    """
    host = (urlparse(normalize_ollama_url(url)).hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "0.0.0.0"):
        return True
    try:
        if ipaddress.ip_address(host).is_loopback:
            return True
    except ValueError:
        pass
    # Only now look up the computer's own names: the cheap checks above answer the
    # common (default) URL without ever waiting on the network.
    return host in _own_host_names()


def models_include(model_name: str, available: list[str]) -> bool:
    """Return whether *model_name* is present in *available*.

    Matches an exact name (``"llama3:8b"``) and also a bare base name
    (``"llama3"``) against any installed tag, since Ollama model identifiers
    carry an optional ``:tag`` suffix that callers may omit.
    """
    if not model_name:
        return False
    model_base = model_name.split(":", maxsplit=1)[0]
    return any(m == model_name or m.split(":", maxsplit=1)[0] == model_base for m in available)


def _consume_iterator_with_first_token_timeout[T](
    it: Iterator[T],
    first_token_timeout: float,
) -> list[T]:
    """Consume *it* in a background thread, enforcing a timeout only on the first item.

    Returns the full list of items, or raises :exc:`~teachers_teammate.exceptions.OCRError`
    when the first item does not arrive within *first_token_timeout* seconds.
    """
    from ..exceptions import OCRError  # noqa: PLC0415  # avoid circular import at module level

    item_queue: queue.Queue[T | BaseException | None] = queue.Queue()

    def _reader() -> None:
        try:
            for item in it:
                item_queue.put(item)
        except Exception as exc:  # noqa: BLE001  # forward any iterator/transport error to the consumer thread via the queue
            item_queue.put(exc)
        finally:
            item_queue.put(None)  # sentinel: stream finished

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    try:
        first = item_queue.get(timeout=first_token_timeout)
    except queue.Empty:
        raise OCRError(
            f"No output within {first_token_timeout}s — the model may still be loading into "
            "memory. Try again in a moment, or check that Ollama is running."
        ) from None

    results: list[T] = []
    item: T | BaseException | None = first
    while item is not None:
        if isinstance(item, BaseException):
            raise OCRError(str(item)) from item
        results.append(item)
        item = item_queue.get()

    t.join()
    return results


def _is_transport_error(exc: BaseException) -> bool:
    """Return whether *exc* means "server unreachable or timed out".

    Anything else raised by the client (for example ``ollama.ResponseError`` for
    an unknown model) means the server did answer.
    """
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return type(exc).__module__.split(".", 1)[0] == "httpx"


class OllamaClient:
    """All HTTP access to a single Ollama server.

    Holds the normalised base URL so callers never repeat URL normalisation.
    All methods delegate to the ``ollama`` library (lazy-imported via
    :meth:`_client`).
    """

    def __init__(self, url: str, *, timeout: float = _CONNECT_TIMEOUT) -> None:
        self.base_url = normalize_ollama_url(url)
        self._timeout = timeout

    def _client(self) -> ollama.Client:
        """Return an ``ollama.Client`` for :attr:`base_url`."""
        import ollama  # noqa: PLC0415

        return ollama.Client(host=self.base_url, timeout=self._timeout)

    # ── Model discovery ────────────────────────────────────────────────────

    def list_models(self) -> list[str]:
        """Return model names from Ollama, or ``[]`` on any error."""
        try:
            resp = self._client().list()
            return [m.model for m in resp.models if m.model]
        except Exception:  # noqa: BLE001  # Ollama client/transport may raise anything; degrade to an empty list
            return []

    def list_models_with_size(self) -> list[tuple[str, int]]:
        """Return ``[(name, size_bytes)]`` for installed models, or ``[]`` on any error."""
        try:
            resp = self._client().list()
            return [(m.model, int(m.size or 0)) for m in resp.models if m.model]
        except Exception:  # noqa: BLE001  # Ollama client/transport may raise anything; degrade to an empty list
            return []

    # ── Connectivity checks ────────────────────────────────────────────────

    def list_models_or_error(self) -> tuple[list[str], str]:
        """Return ``(models, error)`` from a single request to the server.

        *error* is ``""`` when the server answered (even with no model downloaded)
        and a user-facing message when it could not be reached.  Unlike
        :meth:`list_models` this tells "unreachable" apart from "no models" without
        a second round-trip, which matters when the server times out.
        """
        try:
            resp = self._client().list()
            return [m.model for m in resp.models if m.model], ""
        except Exception as exc:  # noqa: BLE001  # any client error means unreachable; the message is classified below
            exc_str = str(exc).lower()
            if any(w in exc_str for w in ("connect", "refused", "network", "unreachable")):
                return (
                    [],
                    f"✗ Cannot connect to Ollama at {self.base_url} — is it running? (ollama serve)",
                )
            if "timeout" in exc_str:
                return [], f"✗ Connection timed out at {self.base_url} — check URL and network"
            return [], f"✗ Unexpected error contacting Ollama: {exc}"

    def check_connection(self, model: str = "") -> tuple[bool, bool, str]:
        """Return ``(connected, model_ok, message)`` for the Ollama server.

        * ``connected=False`` — server is unreachable.
        * ``connected=True, model_ok=False`` — server is up, but *model* is
          not installed (only meaningful when *model* is provided).
        * ``connected=True, model_ok=True`` — server is up and *model* is
          available (or no model was requested).
        """
        models, error = self.list_models_or_error()
        if error:
            return False, False, error

        if not models:
            return (
                True,
                False,
                "✗ Connected to Ollama but no models are pulled — run: ollama pull <model>",
            )
        if not model:
            return True, True, f"✓ Connected — {len(models)} model(s) available"
        if models_include(model, models):
            return True, True, f"✓ Connected — model '{model}' is available"
        avail_str = ", ".join(models)
        return True, False, f"✗ Model '{model}' not found. Available: {avail_str}"

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
            :exc:`~teachers_teammate.exceptions.OllamaConnectionError`: The server
                is unreachable or timed out, so callers can stop asking it.
        """
        try:
            resp = self._client().show(tag)
        except Exception as exc:  # the Ollama client may raise anything; classified below
            if _is_transport_error(exc):
                raise OllamaConnectionError(
                    f"Cannot reach Ollama at {self.base_url}: {exc}"
                ) from exc
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
        when the model is not yet loaded (first request will be slow).  Advisory
        lines go through *reporter* (defaulting to :class:`StdoutReporter`, i.e.
        stderr) so adapters can route them to a log pane.

        Raises:
            :exc:`~teachers_teammate.exceptions.OllamaConnectionError`: Server
                is unreachable, or the model is not available.
        """
        import ollama  # noqa: PLC0415

        reporter = reporter or StdoutReporter()
        client = ollama.Client(host=self.base_url)

        try:
            resp = client.list()
            available = [m.model for m in resp.models if m.model]
        except Exception as exc:
            exc_str = str(exc).lower()
            if any(w in exc_str for w in ("connect", "refused", "network", "unreachable")):
                raise OllamaConnectionError(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Is it running? Start it with: ollama serve"
                ) from exc
            if "timeout" in exc_str:
                raise OllamaConnectionError(
                    f"Connection to Ollama at {self.base_url} timed out. "
                    "Check the URL and network settings."
                ) from exc
            raise OllamaConnectionError(
                f"Unexpected error contacting Ollama at {self.base_url}: {exc}"
            ) from exc

        if not available:
            raise OllamaConnectionError(
                f"Connected to Ollama at {self.base_url} but no models are installed. "
                "Pull a model first: ollama pull <model-name>"
            )

        if not models_include(model_name, available):
            avail_str = ", ".join(available)
            raise OllamaConnectionError(
                f"Model '{model_name}' not found in Ollama. Available: {avail_str}"
            )

        try:
            capabilities = self.model_capabilities(model_name)
        except OllamaConnectionError:
            capabilities = None  # introspection is best-effort; skip the advisory
        if capabilities is not None and "vision" not in capabilities:
            reporter.warn(
                f"\nWARNING: Model '{model_name}' may not support vision — "
                "it cannot read scans. OCR results may be poor."
            )

        try:
            ps_resp = client.ps()
            running = [m.model for m in ps_resp.models if m.model]
            if running and model_name not in running:
                reporter.warn(
                    f"\nNOTE: '{model_name}' is not currently loaded in Ollama — "
                    "the first OCR request will be slow while the model loads."
                )
        except Exception:  # noqa: BLE001  # older Ollama versions may not have /api/ps; the note is optional
            pass

    # ── Generation ─────────────────────────────────────────────────────────

    def chat(
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | None = False,
        timeout: float = 120.0,
    ) -> str:
        """Send a chat request and return the concatenated response text.

        Args:
            model:   Ollama model tag.
            prompt:  User message text.
            images:  Optional list of base64-encoded images (for vision models).
            options: Optional Ollama options dict (e.g. ``{"temperature": 0}``).
            think:   Whether to let reasoning models emit ``<think>`` blocks.
                     Defaults to ``False`` to keep reasoning out of OCR output.
                     Forwarded to Ollama; ignored (with a no-``think`` retry) on
                     older clients or models that don't support it.
            timeout: Overall request timeout in seconds.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on any failure.
        """
        import ollama  # noqa: PLC0415

        from ..exceptions import OCRError  # noqa: PLC0415

        message: dict = {"role": "user", "content": prompt}
        if images:
            message["images"] = images

        try:
            client = ollama.Client(host=self.base_url, timeout=timeout)
            try:
                stream = client.chat(
                    model=model,
                    messages=[message],
                    stream=True,
                    options=options,
                    think=think,
                )
            except (TypeError, ollama.ResponseError):
                # Older ollama clients lack the ``think`` kwarg (TypeError) and
                # non-thinking models reject it (ResponseError) — retry without
                # it. The output cleaner strips any leaked reasoning regardless.
                stream = client.chat(
                    model=model,
                    messages=[message],
                    stream=True,
                    options=options,
                )
        except ollama.ResponseError as exc:
            raise OCRError(f"HTTP error from Ollama: {exc}") from exc
        except Exception as exc:
            raise OCRError(f"cannot connect to Ollama at {self.base_url} — {exc}") from exc

        chunks = _consume_iterator_with_first_token_timeout(stream, timeout)
        parts = [(chunk.message.content or "") for chunk in chunks if chunk.message]
        content = "".join(parts)
        if not content.strip():
            raise OCRError("model returned an empty response")
        return content

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

        from ..exceptions import OCRError  # noqa: PLC0415

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
            raise OCRError(f"Download failed: {exc}") from exc
