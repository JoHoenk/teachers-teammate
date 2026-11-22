"""What the app says when a step or a run cannot start: one sentence per unmet requirement.

The service answers with a :class:`~teachers_teammate.domain.requirements.RequirementIssue`
— what is missing, never the words for it; this module says it.  The dialog that stops a run,
the requirement guard that unticks a step and the readiness line above Run all read from here,
so a teacher never sees two wordings of the same gap.
"""

from __future__ import annotations

from ..domain.requirements import IssueKind, RequirementIssue

NOTHING_TO_READ = (
    "Text recognition is switched off, and none of these files has text to use without it."
)
READER_MODEL_MISSING = "The built-in reader is not downloaded yet."
NO_OLLAMA_MODEL = "No model is downloaded in Ollama yet."
PRIVACY_ADDON_MISSING = "spaCy is not installed. Install it under Add-ons in the app."
ADDONS_OUTDATED = "The add-ons need an update after the app update."

#: Templates; :func:`issue_text` fills the name of the provider or of the step.
PACKAGE_MISSING = "{name} support is not installed yet."
KEY_MISSING = "No API key for {name} is set."
NAME_DETECTION_MISSING = "spaCy NER model '{model}' is not downloaded."
ADDON_RESTART_NEEDED = "Restart the app to finish installing {name}."
MODEL_NOT_DOWNLOADED = "A model is not downloaded."
NAME_DETECTION_MODEL_MISSING = "A spaCy NER model is not downloaded."

#: What a template names when the issue names nothing (the service always does).
_UNNAMED: dict[IssueKind, str] = {
    IssueKind.PACKAGE_MISSING: "AI service",
    IssueKind.KEY_MISSING: "AI service",
    IssueKind.ADDON_RESTART_NEEDED: "the add-ons",
}

#: What an issue says when the service gave no answer of its own to quote.
OCR_NOT_ALLOWED = "This kind of text recognition cannot be used here."
OLLAMA_NOT_FOUND = "Ollama is not installed."
OLLAMA_NOT_RUNNING = "Ollama is not running."
OLLAMA_UNREACHABLE = "Ollama does not answer."


def models_missing(models: tuple[str, ...]) -> str:
    """Return the sentence for models that are not downloaded: one is singular, more plural."""
    if not models:
        return MODEL_NOT_DOWNLOADED
    listed = ", ".join(f"'{model}'" for model in models)
    noun, verb = ("Model", "is") if len(models) == 1 else ("Models", "are")
    return f"{noun} {listed} {verb} not downloaded."


#: The sentence of each issue that is one fixed sentence or one template.
_SENTENCES: dict[IssueKind, str] = {
    IssueKind.NOTHING_TO_READ: NOTHING_TO_READ,
    IssueKind.READER_MODEL_MISSING: READER_MODEL_MISSING,
    IssueKind.NO_OLLAMA_MODEL: NO_OLLAMA_MODEL,
    IssueKind.PRIVACY_ADDON_MISSING: PRIVACY_ADDON_MISSING,
    IssueKind.ADDONS_OUTDATED: ADDONS_OUTDATED,
    IssueKind.PACKAGE_MISSING: PACKAGE_MISSING,
    IssueKind.KEY_MISSING: KEY_MISSING,
    IssueKind.NAME_DETECTION_MISSING: NAME_DETECTION_MISSING,
    IssueKind.ADDON_RESTART_NEEDED: ADDON_RESTART_NEEDED,
}

#: The issues that quote what the service answered, and what they say without it.
_QUOTING: dict[IssueKind, str] = {
    IssueKind.OCR_NOT_ALLOWED: OCR_NOT_ALLOWED,
    IssueKind.OLLAMA_NOT_FOUND: OLLAMA_NOT_FOUND,
    IssueKind.OLLAMA_NOT_RUNNING: OLLAMA_NOT_RUNNING,
    IssueKind.OLLAMA_UNREACHABLE: OLLAMA_UNREACHABLE,
}


def issue_line(issue: RequirementIssue) -> str:
    """Return the bullet line a dialog lists for *issue*."""
    return f"• {issue_text(issue)}"


def issue_text(issue: RequirementIssue) -> str:
    """Return the sentence a teacher reads for *issue*."""
    if issue.kind is IssueKind.MODEL_MISSING:
        return models_missing(issue.models)
    if issue.kind is IssueKind.NAME_DETECTION_MISSING and not issue.models:
        return NAME_DETECTION_MODEL_MISSING
    template = _SENTENCES.get(issue.kind)
    if template is None:
        # A refusal or a connection error: the service's answer is the sentence, and it is the
        # one place that knows what it looked at.
        return issue.reason or _QUOTING[issue.kind]
    name = issue.name or _UNNAMED.get(issue.kind, "")
    return template.format(name=name, model=issue.models[0] if issue.models else "")


def package_missing_line(name: str) -> str:
    """Return the line that says the support package of *name* is not installed."""
    return PACKAGE_MISSING.format(name=name)
