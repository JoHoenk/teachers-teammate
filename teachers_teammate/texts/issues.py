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
OLLAMA_UNREACHABLE = "Ollama does not answer."


def models_missing(models: tuple[str, ...]) -> str:
    """Return the sentence for downloaded-to-be models: one is singular, more are plural."""
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


def issue_line(issue: RequirementIssue) -> str:
    """Return the bullet line a dialog lists for *issue*."""
    return f"• {issue_text(issue)}"


def issue_text(issue: RequirementIssue) -> str:
    """Return the sentence a teacher reads for *issue*."""
    if issue.kind is IssueKind.MODEL_MISSING:
        return models_missing(issue.models)
    template = _SENTENCES.get(issue.kind)
    if template is None:
        # A refusal or a connection error: the service's answer is the sentence, and it is the
        # one place that knows what it looked at.
        return issue.reason or OLLAMA_UNREACHABLE
    return template.format(name=issue.name, model=issue.models[0] if issue.models else "")
