"""The readiness line's fixes: what is missing in one sentence, and the button that fixes it.

``gui/_fix_targets.py`` decides which fix an unmet requirement gets and where its buttons lead;
the words of the line and of each button are here.
"""

from __future__ import annotations

from ..domain.language import language_name
from .addons import UPDATE_ADDONS

# ── The buttons ───────────────────────────────────────────────────────────────

USE_IT = "Use it"
FIND_SERVER = "Find server…"
GET_OLLAMA = "Get Ollama…"
CHECK_AGAIN = "Check again"
START_OLLAMA = "Start Ollama"
CHOOSE_MODEL = "Choose a model…"
DOWNLOAD = "Download…"
INSTALL = "Install…"
ENTER_KEY = "Enter key…"
CHOOSE_FOLDER = "Choose…"
UPDATE_ADDONS_BUTTON = f"{UPDATE_ADDONS}…"

# ── The lines ─────────────────────────────────────────────────────────────────

OLLAMA_NOT_FOUND = "Ollama wasn't found on this computer"
OLLAMA_NOT_RUNNING = "Ollama isn't running"
OLLAMA_UNREACHABLE = "Ollama isn't reachable"
OCR_NOT_ALLOWED = "Text recognition must run on this computer"
READER_MISSING = "The built-in reader isn't downloaded yet"
NO_OLLAMA_MODEL = "No AI model is downloaded in Ollama yet"
RESULTS_INSIDE_DOCUMENTS = "Choose a results folder outside the documents folder"
RESULTS_NO_FOLDER = "Choose where to save the results"


def found_server(where: str) -> str:
    """Return the line for an unreachable Ollama when a search found one on *where*."""
    return f"Ollama isn't reachable at the saved address, but one answers on {where}"


def models_missing(models: tuple[str, ...]) -> str:
    """Return the line for AI models that are not downloaded yet."""
    if len(models) == 1:
        return f"{models[0]} isn't downloaded yet"
    return f"{len(models)} AI models aren't downloaded yet"


def package_missing(service: str) -> str:
    """Return the line for the support package of the online *service*."""
    return f"{service} support isn't installed"


def key_missing(service: str) -> str:
    """Return the line for the online *service* that has no API key yet."""
    return f"No API key for {service} yet"


def name_detection_missing(language: str) -> str:
    """Return the line for name detection that is not installed at all (spaCy and a model).

    *language* is the document language's id, or ``""`` when it is not known.
    """
    named = f"the {language_name(language)} model" if language else "a model"
    return f"Name detection isn't installed yet (spaCy and {named})"


def name_model_missing(language: str) -> str:
    """Return the line for the name detection model of *language* that is not downloaded."""
    what = f"The {language_name(language)} name detection model" if language else "The model"
    return f"{what} isn't downloaded"
