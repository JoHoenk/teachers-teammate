"""A stand-in Ollama server: the whole conversation the app has with one, over loopback HTTP.

The app talks to Ollama through the ``ollama`` client, so a test that wants to see the real
client, the real threads and the real pull stream needs a server that answers like one.  This
fake answers the five requests the app makes — ``/api/version``, ``/api/tags``, ``/api/ps``,
``/api/show`` and ``/api/pull`` — on ``127.0.0.1:0``, so it never clashes with an Ollama running
on the developer's computer.

It is threaded on purpose: the GUI lists the models while a pull is streaming, and a
single-threaded server would answer the second request only after the first had finished.

What it records is what the tests assert on: :attr:`FakeOllama.requests` (every request, in
order) and :attr:`FakeOllama.pulled` (the tags a pull really installed, in order).

It is the one Ollama stand-in of the test suite: a test that needs a server over HTTP uses this,
a test that needs another answer teaches this one a switch instead of writing a second server.

It is not Ollama.  ``tests/unit/test_fake_ollama.py`` keeps it honest against the ``ollama``
client the app is locked to.  The shapes it sends were read off Ollama's own source (``api/types.go``,
``server/routes.go``, ``server/images.go``, at commit ``b8db8d5b``): a list entry always carries
``modified_at``, ``details.format`` and ``details.parent_model``, and a pull that fails says
``pulling manifest`` before its error line.  Nothing here can say what a future server answers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any

#: The version the fake reports; the app only shows it.
VERSION = "0.12.3"
#: The capabilities of a model a test does not describe: a text model.
TEXT_CAPABILITIES: tuple[str, ...] = ("completion",)
#: The families of a text model, for servers too old to send ``capabilities``.
TEXT_FAMILIES: tuple[str, ...] = ("llama",)
#: The family that makes a model able to read images.
VISION_FAMILIES: tuple[str, ...] = ("clip",)
#: The status lines a pull streams, as a real server words them.
PULL_MANIFEST = "pulling manifest"
PULL_SUCCESS = "success"
#: The error line a tag in :attr:`FakeOllama.fail_pull` answers, after ``pulling manifest``.
PULL_FAILED = "pull model manifest: file does not exist"
#: The digest the fake reports for the layer it pretends to download.
PULL_DIGEST = "sha256:" + "1" * 64
#: The bytes the fake reports for a pull, so a progress line has something to show.
PULL_TOTAL = 1_000_000
#: The size the fake reports for an installed model a test gives no size.
MODEL_SIZE = 4_000_000_000
#: When the fake says every model was last changed (the real server always sends the field).
MODIFIED_AT = "2026-01-01T00:00:00Z"
#: The file format of every installed model.
MODEL_FORMAT = "gguf"


@dataclass(frozen=True)
class Request:
    """One request the app made, as the fake recorded it."""

    method: str
    path: str
    body: dict[str, Any]


@dataclass(frozen=True)
class _Model:
    """One installed model: what ``/api/tags`` and ``/api/show`` say about it."""

    tag: str
    capabilities: tuple[str, ...]
    families: tuple[str, ...]
    size: int


class FakeOllama:
    """A loopback Ollama server for the tests that need the whole conversation.

    Use it as a context manager, or call :meth:`close` yourself::

        with FakeOllama() as fake:
            fake.install("qwen3:8b")
            settings["ollama_url"] = fake.url
    """

    def __init__(self, *, version: str = VERSION) -> None:
        self.version = version
        #: Every request the app made, in order.
        self.requests: list[Request] = []
        #: The tags a pull really installed, in order.
        self.pulled: list[str] = []
        #: Tags whose pull answers an error line instead of a stream that ends in ``success``.
        self.fail_pull: set[str] = set()
        self._models: dict[str, _Model] = {}
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fake-ollama", daemon=True
        )
        self._thread.start()

    # ── what a test sets up ───────────────────────────────────────────────────

    @property
    def url(self) -> str:
        """Return the address the app should be pointed at."""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def install(
        self,
        tag: str,
        capabilities: Iterable[str] = TEXT_CAPABILITIES,
        *,
        families: Iterable[str] | None = None,
        size: int = MODEL_SIZE,
    ) -> None:
        """Pretend *tag* is downloaded, with *capabilities*, *families* and *size* in bytes.

        The families default to the vision family for a model that can read images and to the
        text family otherwise, so a test only says what it cares about.
        """
        caps = tuple(capabilities)
        if families is None:
            families = VISION_FAMILIES if "vision" in caps else TEXT_FAMILIES
        with self._lock:
            self._models[tag] = _Model(
                tag=tag, capabilities=caps, families=tuple(families), size=size
            )

    def installed(self) -> list[str]:
        """Return the tags the fake holds, in the order they were installed."""
        with self._lock:
            return list(self._models)

    def model(self, tag: str) -> _Model | None:
        """Return the model *tag*, or ``None`` when the fake does not hold it."""
        with self._lock:
            return self._models.get(tag)

    def tags(self) -> list[dict[str, Any]]:
        """Return the ``/api/tags`` answer: one entry per installed model."""
        with self._lock:
            models = list(self._models.values())
        return [
            {
                "name": model.tag,
                "model": model.tag,
                "modified_at": MODIFIED_AT,
                "size": model.size,
                "digest": PULL_DIGEST,
                "details": _details(model),
            }
            for model in models
        ]

    # ── what a test reads back ────────────────────────────────────────────────

    def record(self, method: str, path: str, body: dict[str, Any]) -> None:
        """Remember one request the app made."""
        with self._lock:
            self.requests.append(Request(method, path, body))

    def record_pull(self, tag: str) -> None:
        """Remember that *tag* was pulled, and install it as a real server would."""
        with self._lock:
            self.pulled.append(tag)
        self.install(tag)

    def paths(self) -> list[str]:
        """Return the paths the app asked for, in order."""
        with self._lock:
            return [request.path for request in self.requests]

    # ── life cycle ────────────────────────────────────────────────────────────

    def close(self) -> None:
        """Stop answering and give the port back."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeOllama:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _details(model: _Model) -> dict[str, Any]:
    """Return the ``details`` block of *model*, as ``/api/tags`` and ``/api/show`` send it."""
    return {
        "parent_model": "",
        "format": MODEL_FORMAT,
        "family": model.families[0] if model.families else "",
        "families": list(model.families),
        "parameter_size": "8B",
        "quantization_level": "Q4_K_M",
    }


def _handler_for(fake: FakeOllama) -> type[BaseHTTPRequestHandler]:
    """Return a handler class that answers for *fake*."""

    class _Handler(BaseHTTPRequestHandler):
        """Answers the five requests the app makes of an Ollama server."""

        def do_GET(self) -> None:  # BaseHTTPRequestHandler's spelling
            self._dispatch("GET")

        def do_POST(self) -> None:  # BaseHTTPRequestHandler's spelling
            self._dispatch("POST")

        def log_message(self, format: str, *args: object) -> None:  # the base class names it
            """Say nothing: the fake records its requests, it does not print them."""

        def _dispatch(self, method: str) -> None:
            """Answer the one request this handler was built for."""
            body = self._read_body()
            fake.record(method, self.path, body)
            if method == "GET" and self.path == "/api/version":
                self._json({"version": fake.version})
            elif method == "GET" and self.path == "/api/tags":
                self._json({"models": fake.tags()})
            elif method == "GET" and self.path == "/api/ps":
                self._json({"models": []})
            elif method == "POST" and self.path == "/api/show":
                self._show(str(body.get("model") or ""))
            elif method == "POST" and self.path == "/api/pull":
                self._pull(str(body.get("model") or ""))
            else:
                self._json({"error": f"no such route: {method} {self.path}"}, status=404)

        def _read_body(self) -> dict[str, Any]:
            """Return the JSON body of the request, or ``{}`` when it has none."""
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                parsed = json.loads(self.rfile.read(length))
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            """Answer with *payload* as JSON."""
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _show(self, tag: str) -> None:
            """Describe *tag*, or answer the 404 a server answers for a model it does not have."""
            model = fake.model(tag)
            if model is None:
                self._json({"error": f"model '{tag}' not found"}, status=404)
                return
            self._json(
                {
                    "capabilities": list(model.capabilities),
                    "details": _details(model),
                    # The client's ShowResponse requires it, and a real server always sends it.
                    "model_info": {},
                }
            )

        def _pull(self, tag: str) -> None:
            """Stream the pull of *tag* as NDJSON, without a length, and close the connection."""
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            # A real server says it is pulling before it finds out it cannot, so the failure
            # arrives as an error line in a stream that is already a 200.
            self._line({"status": PULL_MANIFEST})
            if tag in fake.fail_pull:
                self._line({"error": PULL_FAILED})
                return
            for completed in (PULL_TOTAL // 2, PULL_TOTAL):
                self._line(
                    {
                        "status": f"pulling {PULL_DIGEST}",
                        "digest": PULL_DIGEST,
                        "total": PULL_TOTAL,
                        "completed": completed,
                    }
                )
            self._line({"status": PULL_SUCCESS})
            fake.record_pull(tag)

        def _line(self, payload: dict[str, Any]) -> None:
            """Write one NDJSON line and flush it, so the client sees it while the pull runs."""
            self.wfile.write(json.dumps(payload).encode() + b"\n")
            self.wfile.flush()

    return _Handler
