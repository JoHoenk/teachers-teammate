"""An Ollama server: where it answers, whether it does, and how a found one is described.

One card says what a found server is everywhere (the assistant's *Local AI* page, Settings →
*This computer* and the *Find the AI server* dialog): how many models it holds, whose computer
it is on and where, and a button to use it.  The status line under the address and the search
dialog read their words here too.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..domain.ollama_discovery import ServerKind
from .review import OLLAMA_SERVICE_HINT

# ── Settings → This computer: the address of the local AI server ──────────────

SERVER_GROUP = "Local AI server (Ollama)"
SERVER_NOTE = (
    "Usually runs on this computer. Only change the address if Ollama runs on another "
    "computer you use."
)
SERVER_URL_LABEL = "Server address:"
SERVER_URL_TOOLTIP = (
    "The address of your Ollama server. Used for all text recognition,\n"
    "proofreading, and content review steps that use Ollama."
)
FIND_SERVER_BUTTON = "Find…"
FIND_SERVER_TOOLTIP = "Look for an Ollama server on this computer, or on your network, and pick it."

# ── The status line under that address ────────────────────────────────────────

STATUS_CHECKING = "Checking…"
STATUS_NOT_RUNNING = "● Ollama is installed but isn't running."
STATUS_NOT_FOUND = (
    "● Ollama wasn't found on this computer. Install it like any other program; "
    "this line turns green by itself."
)
BUTTON_START_OLLAMA = "Start Ollama"
#: What the app says about a server that is not running, wherever there is no button to press.
START_OLLAMA_HINT = "Use “Start Ollama” to start it."
BUTTON_GET_OLLAMA = "Get Ollama…"
BUTTON_CHECK_AGAIN = "Check again"


def status_not_running_hint() -> str:
    """Return the line for an Ollama that is installed, not running, and how to start it."""
    return f"{STATUS_NOT_RUNNING} {OLLAMA_SERVICE_HINT}"


def status_start_failed(text: str, reason: str) -> str:
    """Return the line for an Ollama the app tried to start and that did not come up."""
    return f"{text} It could not be started: {reason}"


def status_no_answer(host: str) -> str:
    """Return the line for a server at *host* that did not answer."""
    return f"● No answer from {host}. Is the server switched on?"


# ── The "Find the AI server" dialog ───────────────────────────────────────────

SEARCH_TITLE = "Find the AI server"
SEARCH_INTRO = (
    "Looks for a local AI server (Ollama) that answers. Nothing is changed until you choose "
    "one and press “Use this server”."
)
SEARCHING_HERE = "Searching this computer…"
SEARCHING_NETWORK = "Searching the network…"
SEARCH_NOTHING_HERE = (
    "No server answered on this computer.\n\n"
    "• Is Ollama started? Open the Ollama app, or run “ollama serve”.\n"
    "• Ollama on the Windows computer, and this app in WSL: Ollama has to listen on the "
    "network there (set OLLAMA_HOST=0.0.0.0 and restart it), and the firewall has to allow "
    "it.\n"
    "• A school server: use “Search the network…”."
)
SEARCH_NOTHING_ON_NETWORK = (
    "No server answered on the network. It may be switched off, listen only on its own "
    "computer, or sit behind a firewall. Ask your IT department for its address and type it "
    "in by hand."
)
SEARCH_NETWORK_BUTTON = "Search the network…"
SEARCH_NETWORK_TOOLTIP = (
    "Your documents would be sent to that computer. Only choose a computer you trust."
)
SEARCH_STOP_BUTTON = "Stop"
SEARCH_USE_BUTTON = "Use this server"
SEARCH_TYPE_BUTTON = "Enter address myself"
SEARCH_CLOSE_BUTTON = "Close"


def search_found(count: int) -> str:
    """Return what the dialog says about a search that found *count* servers."""
    if count == 1:
        return "Found 1 server. Press “Use it” to let the AI run there."
    return f"Found {count} servers. Press “Use it” on the one the AI should run on."


# ── A found server ───────────────────────────────────────────────────────────

CARD_USE = "Use it"
CARD_USE_TOOLTIP = "Use this server for the AI"

_ONE_MODEL = "1 model"
_MODELS = "{count} models"
_NO_MODELS = "no models yet"
_ON_THIS_PC = "on this PC · {url}"
_ON_THE_WINDOWS_PC = "on the Windows PC (this computer) · {url}"
_ON_THE_NETWORK = "on the network · {host}"


def card_found(url: str) -> str:
    """Return the line above the card: that Ollama was found, and where it answers."""
    return f"Found Ollama running at {url}"


def card_title(version: str, model_count: int | None) -> str:
    """Return the card's bold line: ``"Ollama 0.12.3 · 4 models"``."""
    name = f"Ollama {version}" if version else "Ollama"
    if model_count is None:
        return name
    if model_count == 0:
        models = _NO_MODELS
    elif model_count == 1:
        models = _ONE_MODEL
    else:
        models = _MODELS.format(count=model_count)
    return f"{name} · {models}"


def card_place(kind: ServerKind, url: str) -> str:
    """Return the card's second line: whose computer, and the address or host."""
    if kind is ServerKind.THIS_COMPUTER:
        return _ON_THIS_PC.format(url=url)
    if kind is ServerKind.WINDOWS_HOST:
        return _ON_THE_WINDOWS_PC.format(url=url)
    return _ON_THE_NETWORK.format(host=urlparse(url).hostname or url)


# ── Reaching the server ───────────────────────────────────────────────────────


def cannot_reach(url: str, reason: BaseException) -> str:
    """Return what the app says about a server whose answer could not be read."""
    return f"Cannot reach Ollama at {url}: {reason}"


def cannot_connect(url: str) -> str:
    """Return what the app says about a server it cannot open a connection to."""
    return f"Cannot connect to Ollama at {url}. Is it running? {START_OLLAMA_HINT}"


def connection_timed_out(url: str) -> str:
    """Return what the app says about a server that answered too late."""
    return f"Connection to Ollama at {url} timed out. Check the URL and network settings."


def contact_failed(url: str, reason: BaseException) -> str:
    """Return what the app says about a server that failed in a way it cannot name."""
    return f"Unexpected error contacting Ollama at {url}: {reason}"


def unreachable(url: str, reason: BaseException) -> str:
    """Return what a proofreading request says about the server it cannot reach."""
    return f"cannot connect to Ollama at {url} — {reason}"


def http_error(reason: BaseException) -> str:
    """Return what a request says about an answer the server refused with an HTTP error."""
    return f"HTTP error from Ollama: {reason}"


#: Shown in the search dialog while it ends the search it was asked to stop.
SEARCH_STOPPING = "Stopping…"


# ── The models of the server ──────────────────────────────────────────────────


def no_models_installed(url: str) -> str:
    """Return what the app says about a server that answered but holds no model."""
    return (
        f"Connected to Ollama at {url} but no models are installed. "
        "Pull a model first: ollama pull <model-name>"
    )


def model_not_found(model: str, available: str) -> str:
    """Return what the app says about a model the server does not hold, naming *available*."""
    return f"Model '{model}' not found in Ollama. Available: {available}"


# ── The answer ────────────────────────────────────────────────────────────────

#: What a request says about a model that answered with nothing at all.
EMPTY_ANSWER = "model returned an empty response"

#: The same, for a model whose reasoning used up its output limit before any text.
EMPTY_ANSWER_CUT_OFF = "model returned an empty response (stopped at its output limit)"


def no_first_token(seconds: float) -> str:
    """Return what a request says about a model that sent nothing within *seconds*."""
    return (
        f"No output within {seconds}s — the model may still be loading into memory. "
        "Try again in a moment, or check that Ollama is running."
    )


def answer_abandoned(seconds: float) -> str:
    """Return what a request says about an answer still being written after *seconds*."""
    if seconds is None:
        return "the answer was still being written; the request was abandoned"
    return f"the answer was still being written after {seconds:g}s; the request was abandoned"


# ── The connection check: one line for the Settings page and the command line ─


def check_cannot_connect(url: str) -> str:
    """Return the check's line for a server that refused the connection."""
    return f"✗ Cannot connect to Ollama at {url} — is it running? {START_OLLAMA_HINT}"


def check_timed_out(url: str) -> str:
    """Return the check's line for a server that answered too late."""
    return f"✗ Connection timed out at {url} — check URL and network"


def check_unexpected(reason: BaseException) -> str:
    """Return the check's line for any other failure to reach the server."""
    return f"✗ Unexpected error contacting Ollama: {reason}"


CHECK_NO_MODELS = "✗ Connected to Ollama but no models are pulled — run: ollama pull <model>"


def check_connected(count: int) -> str:
    """Return the check's line for a server that answered, with *count* models."""
    return f"✓ Connected — {count} model(s) available"


def check_model_available(model: str) -> str:
    """Return the check's line for a server that holds *model*."""
    return f"✓ Connected — model '{model}' is available"


def check_model_missing(model: str, available: str) -> str:
    """Return the check's line for a server that does not hold *model*, naming *available*."""
    return f"✗ Model '{model}' not found. Available: {available}"


# ── What a run warns about the OCR model ──────────────────────────────────────


def vision_not_supported(model: str) -> str:
    """Return the warning for an OCR *model* that cannot read images."""
    return (
        f"\nWARNING: Model '{model}' may not support vision — "
        "it cannot read scans. OCR results may be poor."
    )


def model_not_loaded(model: str) -> str:
    """Return the note for an OCR *model* that is not in memory yet."""
    return (
        f"\nNOTE: '{model}' is not currently loaded in Ollama — "
        "the first OCR request will be slow while the model loads."
    )
