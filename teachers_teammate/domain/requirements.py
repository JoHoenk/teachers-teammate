"""What a step or a run needs before it can start, as data.

The service answers with a :class:`RequirementIssue` — *what* is missing and the few facts a
sentence about it names — and the adapter words it with
:func:`~teachers_teammate.texts.issues.issue_text`.  No sentence lives here: the same issue
also decides which fix is offered, and that decision must not depend on wording.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum


class IssueKind(StrEnum):
    """What is missing, so the GUI can offer the fix without reading the sentence."""

    OCR_NOT_ALLOWED = "ocr_not_allowed"
    #: The built-in reader's model files are not downloaded yet.
    READER_MODEL_MISSING = "reader_model_missing"
    #: An address on this computer that does not answer, and no Ollama program was found.
    OLLAMA_NOT_FOUND = "ollama_not_found"
    #: Ollama is installed on this computer but does not answer: it has to be started.
    OLLAMA_NOT_RUNNING = "ollama_not_running"
    #: An address that does not answer, and nothing can be started from here.
    OLLAMA_UNREACHABLE = "ollama_unreachable"
    NO_OLLAMA_MODEL = "no_ollama_model"
    MODEL_MISSING = "model_missing"
    PACKAGE_MISSING = "package_missing"
    KEY_MISSING = "key_missing"
    NAME_DETECTION_MISSING = "name_detection_missing"
    #: spaCy itself is not installed, so no name detection model can run.
    PRIVACY_ADDON_MISSING = "privacy_addon_missing"
    #: Text recognition is switched off and no queued file has text to use without it.
    NOTHING_TO_READ = "nothing_to_read"
    #: An app update changed packages the add-ons share with it: they stay off until updated.
    ADDONS_OUTDATED = "addons_outdated"
    #: An install could not replace a file the running app has loaded: a restart finishes it.
    ADDON_RESTART_NEEDED = "addon_restart_needed"


@dataclasses.dataclass(frozen=True)
class RequirementIssue:
    """A single unmet requirement for enabling a pipeline step or starting a run."""

    kind: IssueKind
    #: The provider whose package or key is missing (``PACKAGE_MISSING``, ``KEY_MISSING``).
    provider: str = ""
    #: How the sentence names it: the provider, the step, or the add-on that waits.
    name: str = ""
    #: Ollama tags that are not downloaded, or the spaCy model that is missing.
    models: tuple[str, ...] = ()
    #: The app can start Ollama itself (``OLLAMA_NOT_RUNNING``): the fix offers a button.
    startable: bool = False
    #: What the service answered where the sentence quotes it: the refusal of text recognition,
    #: or the connection error of an Ollama that does not answer.
    reason: str = ""
