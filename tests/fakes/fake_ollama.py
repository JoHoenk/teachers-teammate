"""A stand-in Ollama server: the whole conversation the app has with one, over loopback HTTP.

The app talks to Ollama through the ``ollama`` client, so a test that wants to see the real
client, the real threads and the real pull stream needs a server that answers like one.  This
fake answers the seven requests the app makes — ``/api/version``, ``/api/tags``, ``/api/ps``,
``/api/show``, ``/api/pull``, ``/api/chat`` and ``/api/generate`` — on ``127.0.0.1:0``, so it never
clashes with an Ollama running on the developer's computer.

It is threaded on purpose: the GUI lists the models while a pull is streaming, and a
single-threaded server would answer the second request only after the first had finished.

What it records is what the tests assert on: :attr:`FakeOllama.requests` (every request, in
order), :attr:`FakeOllama.pulled` (the tags a pull really installed, in order) and
:attr:`FakeOllama.unloaded` (the tags the app dropped out of memory, in order).

It is the one Ollama stand-in of the test suite: a test that needs a server over HTTP uses this,
a test that needs another answer teaches this one a switch instead of writing a second server.
``answer_chats()`` is that switch: :data:`CHAT_ANSWER` unless a test says otherwise, and
:class:`ProofreadingAnswers` for a test that wants a model which really proofreads.

It is not Ollama.  ``tests/unit/test_fake_ollama.py`` keeps it honest against the ``ollama``
client the app is locked to.  The shapes it sends were read off Ollama's own source (``api/types.go``,
``server/routes.go``, ``server/images.go``, at commit ``b8db8d5b``): a list entry always carries
``modified_at``, ``details.format`` and ``details.parent_model``, and a pull that fails says
``pulling manifest`` before its error line.  Nothing here can say what a future server answers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import re
import threading
import time
from typing import Any

from teachers_teammate.domain.correction_prompts import correct_user, parameters
from teachers_teammate.infrastructure.proofreading.schemas import (
    Category,
    ExplainAnswer,
    ExplainNote,
    UnclearAnswer,
)

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
#: The expiry ``/api/ps`` reports for a model that is in memory and idle.
IDLE_EXPIRY = "2026-01-01T00:00:00+00:00"
#: The answer a chat request gives, so a test can read a whole request back.
CHAT_ANSWER = "ok"
#: The category a proofreading answer gives an edit it does not explain by itself.
PROOFREADING_CATEGORY: Category = "spelling"
#: The second the moving expiry of a busy model starts from (a fixed point, so it is readable).
_MOVING_EXPIRY_BASE = 1_800_000_000
#: What a chat request answers: the request body, and the assistant's content for it.
ChatAnswer = Callable[[dict[str, Any]], str]


def _text_marker() -> str:
    """Return what the exchange writes in front of a text, from the exchange's own format code."""
    # Without language, level and notes the parameter lines are the request's whole head, so what
    # is left of the message is exactly the framing that stands before the text.
    empty_head = parameters("", level="")
    framed = correct_user("", language="", level="")
    return framed[len(empty_head) :]


#: Where the text of a step ① request begins.  Built from
#: :func:`~teachers_teammate.domain.correction_prompts.correct_user`, so a request framed
#: differently no longer yields a text here and the tests that rely on it fail.
TEXT_MARKER = _text_marker()
assert TEXT_MARKER, "the proofreading exchange writes nothing in front of the text"


@dataclass(frozen=True)
class ProofreadingAnswers:
    """A model that corrects the text it is sent, explains every edit the same way, and doubts none.

    ``edits`` are the word pairs it changes (``{"have": "has"}``), and ``last_word`` replaces the
    last word of every text it is sent, whatever that word is: a test that cannot know what a
    reader made of a page still gets exactly one change per document; ``last_words`` gives a model
    of its own tag another such word, so two models disagree about it.  A document with neither
    comes back as it was sent.  ``category`` and ``explanation`` are what it says about every
    change; ``skips_before_gap`` makes it answer only the part after a run of blank lines, as a
    model that reads a wide gap as two texts does.  The two structured steps answer in the app's
    own answer shapes, so a renamed field or a new required one raises here instead of somewhere
    in the walk.
    """

    edits: tuple[tuple[str, str], ...] = ()
    last_word: str = ""
    last_words: tuple[tuple[str, str], ...] = ()
    explanation: str = "The spelling was corrected."
    category: Category = PROOFREADING_CATEGORY
    skips_before_gap: int = 0
    """A run of this many line breaks in a row, and the answer holds only what follows it.

    ``0`` reads the whole text, as a model that answers it does.  ``3`` is the model of the
    held-out text: four blank lines between two essays and the answer was the second essay alone.
    """

    def __call__(self, body: dict[str, Any]) -> str:
        """Answer one chat request of the proofreading exchange."""
        schema = body.get("format") or {}
        properties = schema.get("properties") or {}
        if "edits" in properties:
            return self._explained(schema)
        if "unclear" in properties:
            return UnclearAnswer(unclear=[]).model_dump_json()
        return self._corrected(_asked_text(body), str(body.get("model") or ""))

    def _explained(self, schema: dict[str, Any]) -> str:
        """Answer step ②: one note per edit number the request pinned."""
        answer = ExplainAnswer(
            edits=[
                ExplainNote(id=number, category=self.category, explanation=self.explanation)
                for number in _edit_numbers(schema)
            ]
        )
        return answer.model_dump_json()

    def _corrected(self, text: str, model: str) -> str:
        """Answer step ①/③: what it reads of the text, with the words *model* changes."""
        text = _after_gap(text, self.skips_before_gap)
        for original, corrected in self.edits:
            text = text.replace(original, corrected)
        last = dict(self.last_words).get(model, self.last_word)
        if last:
            text = re.sub(r"\S+(\s*)$", lambda found: last + found.group(1), text)
        return text


def _after_gap(text: str, breaks: int) -> str:
    """Return the part of *text* after its first run of *breaks* line breaks, or all of it."""
    if breaks <= 0:
        return text
    found = re.search(rf"\n{{{breaks},}}", text)
    return text[found.end() :] if found else text


def _asked_text(body: dict[str, Any]) -> str:
    """Return the text a proofreading request carries, read with the exchange's own framing."""
    user = "".join(str(message.get("content") or "") for message in body.get("messages") or ())
    _framing, marker, text = user.rpartition(TEXT_MARKER)
    if not marker:
        raise ValueError(f"this request carries no text to proofread: {user[:200]!r}")
    return text


def _edit_numbers(schema: dict[str, Any]) -> list[int]:
    """Return the edit numbers step ② pinned, in the order the schema lists them."""
    note = (schema.get("properties") or {}).get("edits", {}).get("items") or {}
    # A single-use model is written inline; one that is used twice is a ``$ref`` into ``$defs``.
    reference = str(note.get("$ref") or "")
    if reference:
        note = (schema.get("$defs") or {}).get(reference.rsplit("/", 1)[-1]) or {}
    pinned = (note.get("properties") or {}).get("id") or {}
    # One id is a ``Literal`` of one value, which a schema writes as ``const``, not as an enum.
    numbers = pinned.get("enum") or ([pinned["const"]] if "const" in pinned else [])
    if not numbers:
        raise ValueError(f"this explain request pins no edit numbers: {schema!r}")
    return [int(number) for number in numbers]


@dataclass(frozen=True)
class Request:
    """One request the app made, as the fake recorded it."""

    method: str
    path: str
    body: dict[str, Any]


def _digest_of(tag: str) -> str:
    """Return the digest the fake reports for *tag*: one per model, as a real server has."""
    return "sha256:" + hashlib.sha256(tag.encode()).hexdigest()


@dataclass(frozen=True)
class _Model:
    """One installed model: what ``/api/tags`` and ``/api/show`` say about it."""

    tag: str
    capabilities: tuple[str, ...]
    families: tuple[str, ...]
    size: int
    digest: str = PULL_DIGEST


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
        #: The tags the app dropped out of memory with ``keep_alive: 0``, in order.
        self.unloaded: list[str] = []
        #: Tags whose pull answers an error line instead of a stream that ends in ``success``.
        self.fail_pull: set[str] = set()
        self._models: dict[str, _Model] = {}
        #: Tags the fake holds in memory and idle: listed, with an expiry that does not move.
        self._loaded: set[str] = set()
        #: ``(tag, until)`` of a model that is generating: listed, with a moving expiry.
        self._busy: tuple[str, float] | None = None
        #: ``(tag, until)`` of a model that is loading: not listed until *until*, and its first
        #: answer waits that long.
        self._loading: tuple[str, float] | None = None
        #: How often ``/api/ps`` was asked, which is what moves the expiry of a busy model.
        self._ps_calls = 0
        #: What ``/api/chat`` answers; ``None`` is :data:`CHAT_ANSWER`.
        self._chat_answer: ChatAnswer | None = None
        #: How long ``/api/tags`` waits before it answers, in seconds.
        self._tags_delay = 0.0
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
        digest: str = "",
    ) -> None:
        """Pretend *tag* is downloaded, with *capabilities*, *families* and *size* in bytes.

        The families default to the vision family for a model that can read images and to the
        text family otherwise, so a test only says what it cares about.  *digest* is the layer
        the server reports for it, one per model unless a test says otherwise: that is what
        tells a model from another, and what a re-pull of a tag changes.
        """
        caps = tuple(capabilities)
        if families is None:
            families = VISION_FAMILIES if "vision" in caps else TEXT_FAMILIES
        with self._lock:
            self._models[tag] = _Model(
                tag=tag,
                capabilities=caps,
                families=tuple(families),
                size=size,
                digest=digest or _digest_of(tag),
            )

    def uninstall(self, tag: str) -> None:
        """Pretend *tag* was removed (``ollama rm``): ``/api/tags`` no longer lists it."""
        with self._lock:
            self._models.pop(tag, None)

    def loaded(self, tag: str) -> None:
        """Pretend *tag* is in memory and idle: ``/api/ps`` lists it, its expiry does not move."""
        with self._lock:
            self._loaded.add(tag)

    def busy_with(self, tag: str, seconds: float) -> None:
        """Pretend *tag* is generating for *seconds*: listed, and its expiry moves on every poll.

        That is what a real server does while a model writes an answer, and it is the only sign
        the app has that a request is queued behind another one.  Afterwards the model stays in
        memory and idle, as a real server keeps it.
        """
        with self._lock:
            self._busy = (tag, time.monotonic() + seconds)
            self._loaded.add(tag)

    def loading_for(self, tag: str, seconds: float) -> None:
        """Pretend *tag* takes *seconds* to load.

        Until then ``/api/ps`` does not list it and its first answer waits; afterwards it is
        listed and idle, as a real server reports a model it just loaded.
        """
        with self._lock:
            self._loading = (tag, time.monotonic() + seconds)

    def answers_tags_after(self, seconds: float) -> None:
        """Make ``/api/tags`` wait *seconds* before it answers.

        That is a server that is busy, and it is what tells a lock held during a request from one
        held around the dict it guards.
        """
        with self._lock:
            self._tags_delay = seconds

    def tags_delay(self) -> float:
        """Return how long ``/api/tags`` waits before it answers."""
        with self._lock:
            return self._tags_delay

    def answer_chats(self, answer: ChatAnswer) -> None:
        """Answer every ``/api/chat`` request with *answer* instead of :data:`CHAT_ANSWER`.

        *answer* gets the request body and returns the assistant's content, so a test that needs
        the proofreading exchange's three steps answers all of them from one place
        (:class:`ProofreadingAnswers`).
        """
        with self._lock:
            self._chat_answer = answer

    def chat_answer(self, body: dict[str, Any]) -> str:
        """Return the assistant's content for one chat request."""
        with self._lock:
            answer = self._chat_answer
        return CHAT_ANSWER if answer is None else answer(body)

    def ps(self) -> list[dict[str, Any]]:
        """Return the ``/api/ps`` answer: what is in memory, and when it will be dropped."""
        now = time.monotonic()
        with self._lock:
            self._ps_calls += 1
            calls = self._ps_calls
            busy, loading, loaded = self._busy, self._loading, set(self._loaded)
        entries: list[dict[str, Any]] = []
        if busy is not None and now < busy[1]:
            # A generating model's expiry moves on every poll; that is the only sign of it.
            entries.append(_ps_entry(busy[0], _moving_expiry(calls)))
        if loading is not None and now < loading[1]:
            return entries
        if loading is not None:
            loaded.add(loading[0])
        entries.extend(_ps_entry(tag, IDLE_EXPIRY) for tag in sorted(loaded))
        return entries

    def wait_for_turn(self, tag: str) -> None:
        """Block until the server is free and *tag* is loaded, as it would before answering.

        A request that arrives while another model is generating waits behind it, and then the
        model it asks for has to be loaded.
        """
        with self._lock:
            busy = self._busy
        if busy is not None:
            time.sleep(max(0.0, busy[1] - time.monotonic()))
        with self._lock:
            loading = self._loading
        if loading is None or loading[0] != tag:
            return
        time.sleep(max(0.0, loading[1] - time.monotonic()))

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
                "digest": model.digest,
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

    def drop(self, tag: str) -> None:
        """Drop *tag* out of memory, as a generate request with ``keep_alive: 0`` does."""
        with self._lock:
            self.unloaded.append(tag)
            self._loaded.discard(tag)
            if self._busy is not None and self._busy[0] == tag:
                self._busy = None

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


def _moving_expiry(calls: int) -> str:
    """Return an expiry that moves with every poll, as a generating model's does."""
    return datetime.fromtimestamp(_MOVING_EXPIRY_BASE + calls, UTC).isoformat()


def _ps_entry(tag: str, expires_at: str) -> dict[str, Any]:
    """Return one ``/api/ps`` entry for *tag*, as a real server sends it."""
    return {
        "name": tag,
        "model": tag,
        "size": MODEL_SIZE,
        "size_vram": MODEL_SIZE,
        "context_length": 4096,
        "expires_at": expires_at,
    }


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
        """Answers the seven requests the app makes of an Ollama server."""

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
                time.sleep(fake.tags_delay())
                self._json({"models": fake.tags()})
            elif method == "GET" and self.path == "/api/ps":
                self._json({"models": fake.ps()})
            elif method == "POST" and self.path == "/api/chat":
                self._chat(body)
            elif method == "POST" and self.path == "/api/show":
                self._show(str(body.get("model") or ""))
            elif method == "POST" and self.path == "/api/pull":
                self._pull(str(body.get("model") or ""))
            elif method == "POST" and self.path == "/api/generate":
                self._generate(body)
            else:
                self._json({"error": f"no such route: {method} {self.path}"}, status=404)

        def _generate(self, body: dict[str, Any]) -> None:
            """Answer a generate request: an empty prompt with ``keep_alive: 0`` drops the model."""
            model = str(body.get("model") or "")
            fake.drop(model)
            self._json({"model": model, "created_at": MODIFIED_AT, "response": "", "done": True})

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

        def _chat(self, body: dict[str, Any]) -> None:
            """Stream one answer, after the server is free and the model is loaded."""
            tag = str(body.get("model") or "")
            try:
                answer = fake.chat_answer(body)
            except Exception as exc:  # noqa: BLE001  # a stand-in that cannot answer says why
                # The caller sees this as the model's error, not as a connection that dropped.
                self._json({"error": f"the stand-in cannot answer: {exc}"}, status=500)
                return
            fake.wait_for_turn(tag)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self._line(
                {
                    "model": tag,
                    "created_at": MODIFIED_AT,
                    "message": {"role": "assistant", "content": answer},
                    "done": False,
                }
            )
            self._line(
                {
                    "model": tag,
                    "created_at": MODIFIED_AT,
                    "message": {"role": "assistant", "content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 10,
                    "eval_count": 2,
                    "total_duration": 2_000_000_000,
                    "load_duration": 1_500_000_000,
                    "prompt_eval_duration": 300_000_000,
                    "eval_duration": 200_000_000,
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
