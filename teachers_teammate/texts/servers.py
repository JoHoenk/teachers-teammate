"""How a found Ollama server is described: the card on the Ollama page and in the search dialog.

One card says it everywhere (the assistant's *Local AI* page, Settings → *This computer* and the
*Find the AI server* dialog): what it is, how many models it holds, whose computer it is on and
where, and a button to use it.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..domain.ollama_discovery import ServerKind

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
