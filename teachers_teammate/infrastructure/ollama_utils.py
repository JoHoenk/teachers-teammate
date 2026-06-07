"""Shared Ollama utilities: URL normalisation and the OllamaClient HTTP wrapper.

Centralises all Ollama HTTP access so pipeline stages, GUI helpers, and
connection-check threads use the same code path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import queue
import threading
from typing import TYPE_CHECKING

from ..exceptions import OllamaConnectionError
from .reporting import Reporter, StdoutReporter

if TYPE_CHECKING:
    import ollama

_DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

# How long to wait for the TCP connection to be established.
_CONNECT_TIMEOUT = 10


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


class OllamaClient:
    """All HTTP access to a single Ollama server.

    Holds the normalised base URL so callers never repeat URL normalisation.
    All methods delegate to the ``ollama`` library (lazy-imported via
    :meth:`_client`).
    """

    def __init__(self, url: str) -> None:
        self.base_url = normalize_ollama_url(url)

    def _client(self) -> ollama.Client:
        """Return an ``ollama.Client`` for :attr:`base_url`."""
        import ollama  # noqa: PLC0415

        return ollama.Client(host=self.base_url, timeout=_CONNECT_TIMEOUT)

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

    def check_connection(self, model: str = "") -> tuple[bool, bool, str]:
        """Return ``(connected, model_ok, message)`` for the Ollama server.

        * ``connected=False`` — server is unreachable.
        * ``connected=True, model_ok=False`` — server is up, but *model* is
          not installed (only meaningful when *model* is provided).
        * ``connected=True, model_ok=True`` — server is up and *model* is
          available (or no model was requested).
        """
        try:
            resp = self._client().list()
            models = [m.model for m in resp.models if m.model]
        except Exception as exc:  # noqa: BLE001  # any client error means unreachable; the message is classified below
            exc_str = str(exc).lower()
            if any(w in exc_str for w in ("connect", "refused", "network", "unreachable")):
                return (
                    False,
                    False,
                    f"✗ Cannot connect to Ollama at {self.base_url} — is it running? (ollama serve)",
                )
            if "timeout" in exc_str:
                return (
                    False,
                    False,
                    f"✗ Connection timed out at {self.base_url} — check URL and network",
                )
            return False, False, f"✗ Unexpected error contacting Ollama: {exc}"

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
            show_resp = client.show(model_name)
            families = (show_resp.details.families or []) if show_resp.details else []
            _KNOWN_VISION_FAMILIES = frozenset({"clip", "vision", "deepseekocr"})
            if families and not any(f.lower() in _KNOWN_VISION_FAMILIES for f in families):
                reporter.warn(
                    f"\nWARNING: Model '{model_name}' may not support vision "
                    f"(families: {families}). OCR results may be poor."
                )
        except Exception:  # noqa: BLE001  # model introspection is best-effort; skip the advisory on any error
            pass

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
