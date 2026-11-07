"""Code shared by the Mistral providers (global and EU).

Both providers talk to the same API with the same key; only the base URL differs, and
the base URL decides *where the text is processed*.  The URL is therefore always passed
explicitly (never left to ``MISTRAL_BASE_URL``), so a provider's name in the app always
matches where its requests really go.

LangChain has no "list models" call, so the model query is a plain HTTP request here.
The standard library is enough for that, which keeps the Mistral SDK (and any extra
HTTP dependency) out of the install.
"""

from __future__ import annotations

from datetime import date
import json
import os
import re
from typing import TYPE_CHECKING, Any
import urllib.error
import urllib.request

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.model_listing import (
    CuratedModels,
    ListedModel,
    ModelGroup,
    ModelListing,
)
from teachers_teammate.infrastructure.providers._helpers import import_guard

ENV_KEY = "MISTRAL_API_KEY"

#: Global endpoint: Mistral makes no promise about where requests are processed.
GLOBAL_BASE_URL = "https://api.mistral.ai/v1"

#: EU endpoint: processed in EU/EFTA data centres (billed at a surcharge by Mistral).
EU_BASE_URL = "https://api.eu.mistral.ai/v1"

DEFAULT_MODEL = "mistral-small-latest"

#: Used when the live query is unavailable (no key, offline, auth error).
FALLBACK_MODELS: list[str] = [
    "mistral-large-latest",
    "mistral-medium-latest",
    "mistral-small-latest",
    "open-mistral-nemo",
]

#: Fixed seed so the same text and settings give the same correction when comparing runs.
RANDOM_SEED = 42

_LIST_TIMEOUT_S = 10

#: Model families that cannot proofread: code, OCR, embeddings.  Specialised models for
#: one other job are filtered by capability, but these read as ordinary chat models.
OFF_TOPIC_FAMILIES: tuple[str, ...] = ("codestral", "devstral", "mistral-ocr", "mistral-embed")

#: Order of the groups in the list: the current model, its dated versions, the teacher's
#: own fine-tuned models, and anything else.
_GROUP_ORDER: dict[str, int] = {"current": 0, "pinned": 1, "own": 2, "other": 3}

#: Order inside the "current" group: biggest model first, anything unrecognised last.
_SIZE_ORDER: tuple[str, ...] = ("large", "medium", "small", "ministral")

SUGGESTED_LABEL = "★ suggested"
PINNED_LABEL = "pinned version"
OWN_LABEL = "your model"

#: A dated model ID ends in the year and month of its release, e.g. ``mistral-small-2506``.
_DATED_ID = re.compile(r"-\d{4}$")


def curate_mistral_models(entries: list[dict[str, Any]], today: date) -> CuratedModels:
    """Return the models of *entries* worth offering, one entry per model.

    Mistral answers ``/v1/models`` with one entry per *alias*, so the same model appears as
    ``mistral-small-latest`` and ``mistral-small-2506``, and the answer also holds models
    that cannot proofread (code, audio, OCR, moderation) and models that are being retired.
    This groups the aliases of a model into one choice, keeps the dated alias as a
    selectable *pinned* version below it, and drops everything that is not a usable
    proofreading model.  A model that retires in the future is hidden from the list but
    returned in :attr:`CuratedModels.retiring`, so the dialog can warn a teacher who saved it.
    """
    # Fine-tuned models are listed on their own: their IDs are numbered per account and
    # share no alias with the base model they were trained from.
    base = [entry for entry in entries if _usable(entry) and not _is_fine_tuned(entry)]
    families = _group_families(base)
    current: list[ListedModel] = []
    for entries_of_family in families:
        alive = [entry for entry in entries_of_family if not _retired(entry, today)]
        if not alive:
            continue
        current_id = _preferred_id(alive)
        if not current_id:
            continue
        pinned = _newest_dated(alive, current_id)
        listed = _describe(_entry_for(alive, current_id), current_id, pinned)
        if listed.retires_on is None:
            current.append(listed)
        # else: a model on its way out is only remembered, never offered.  Its dated version
        # has no retirement date of its own, so it stays selectable.
        if pinned is not None:
            current.append(_describe(pinned, str(pinned["id"]), None, group="pinned"))
    own = [
        _describe(entry, str(entry["id"]), None, group="own", label=OWN_LABEL)
        for entry in entries
        if _usable(entry) and _is_fine_tuned(entry) and not _retired(entry, today)
    ]
    current.sort(key=_sort_key)
    own.sort(key=_sort_key)
    offered = [*current, *own]
    shown = {model.id for model in offered}
    retiring = sorted(
        (
            _describe(entry, str(entry["id"]), None)
            for entry in entries
            if _usable(entry)
            and str(entry["id"]) not in shown
            and (_as_date(entry.get("deprecation")) or date.min) > today
        ),
        key=_sort_key,
    )
    # Every other name a live entry answers to (aliases, older dated versions) still works.
    also_served = sorted(
        {
            token
            for entry in entries
            if _usable(entry) and not _retired(entry, today)
            for token in _tokens(entry)
        }
        - shown
    )
    return CuratedModels(tuple(offered), tuple(retiring), tuple(also_served))


def _usable(entry: dict[str, Any]) -> bool:
    """Return whether an API model entry can proofread text (not code, audio, moderation…).

    An entry without an ID cannot be chosen or sent to the API, so it is not usable either.
    """
    if entry.get("archived"):
        return False
    if not str(entry.get("id") or ""):
        return False
    if _is_off_topic(str(entry.get("id", ""))):
        return False
    if entry.get("audio_transcription_capabilities"):
        return False
    capabilities = entry.get("capabilities")
    if isinstance(capabilities, dict):
        if "completion_chat" in capabilities and not capabilities["completion_chat"]:
            return False
        if capabilities.get("classification"):
            return False
    return True


def _is_off_topic(model_id: str) -> bool:
    """Return whether *model_id* belongs to a family that cannot proofread."""
    return any(model_id.startswith(family) for family in OFF_TOPIC_FAMILIES)


def _is_fine_tuned(entry: dict[str, Any]) -> bool:
    """Return whether *entry* is one of the teacher's own fine-tuned models."""
    return entry.get("type") == "fine-tuned"


def _retired(entry: dict[str, Any], today: date) -> bool:
    """Return whether *entry*'s model is already gone (its retirement date has passed)."""
    retires_on = _as_date(entry.get("deprecation"))
    return retires_on is not None and retires_on <= today


def _as_date(value: object) -> date | None:
    """Return *value* (a ``date`` or an ISO 8601 string) as a date, else ``None``."""
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _tokens(entry: dict[str, Any]) -> list[str]:
    """Return the names *entry* is known by: its ID, its name and its aliases.

    ``name`` is a display name for some models ("Mistral Small"), which would join
    unrelated models into one entry, so only a name that reads like a model ID is used.
    """
    id_and_name = [str(entry.get("id", "")), str(entry.get("name") or "")]
    aliases = entry.get("aliases")
    extra = [str(alias) for alias in aliases] if isinstance(aliases, list) else []
    return [name for name in [*id_and_name, *extra] if name and " " not in name]


def _group_families(entries: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group *entries* into model families, joined by any shared name, alias or ID."""
    parent: dict[str, str] = {}

    def find(token: str) -> str:
        while parent[token] != token:
            parent[token] = parent[parent[token]]
            token = parent[token]
        return token

    for entry in entries:
        tokens = _tokens(entry)
        for token in tokens:
            parent.setdefault(token, token)
        for token in tokens[1:]:
            root, other = find(tokens[0]), find(token)
            if root != other:
                parent[other] = root

    families: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        tokens = _tokens(entry)
        if tokens:
            families.setdefault(find(tokens[0]), []).append(entry)
    return list(families.values())


def _preferred_id(family: list[dict[str, Any]]) -> str:
    """Return the name to show for *family*: its ``*-latest`` alias, else the shortest."""
    tokens = list(dict.fromkeys(token for entry in family for token in _tokens(entry)))
    latest = [token for token in tokens if token.endswith("-latest")]
    if latest:
        ids = {str(entry.get("id", "")) for entry in family}
        return (sorted(token for token in latest if token in ids) or sorted(latest))[0]
    return sorted(tokens, key=lambda token: (len(token), token))[0] if tokens else ""


def _entry_for(family: list[dict[str, Any]], current_id: str) -> dict[str, Any]:
    """Return the entry *current_id* came from, so its description can be shown."""
    for entry in family:
        if str(entry.get("id", "")) == current_id:
            return entry
    for entry in family:
        if current_id in _tokens(entry):
            return entry
    return family[0]


def _newest_dated(family: list[dict[str, Any]], current_id: str) -> dict[str, Any] | None:
    """Return the newest dated version of *family* that is worth offering, else ``None``.

    A version that already has a retirement date is skipped: pinning to a model that is
    about to disappear is not what "reproducible runs" means.
    """
    dated = [
        entry
        for entry in family
        if (model_id := str(entry.get("id", "")))
        and model_id != current_id
        and _DATED_ID.search(model_id)
        and _as_date(entry.get("deprecation")) is None
    ]
    if not dated:
        return None
    return max(dated, key=lambda entry: str(entry.get("id", "")))


def _describe(
    entry: dict[str, Any],
    model_id: str,
    pinned: dict[str, Any] | None,
    *,
    group: ModelGroup = "current",
    label: str = "",
) -> ListedModel:
    """Return the :class:`ListedModel` for *entry*, shown under *model_id*."""
    description = str(entry.get("description") or "")
    if pinned is not None:
        note = f"Currently {pinned['id']}."
        description = f"{description} {note}".strip()
    if not label and group == "current" and model_id == DEFAULT_MODEL:
        label = SUGGESTED_LABEL
    elif not label and group == "pinned":
        label = PINNED_LABEL
    return ListedModel(
        id=model_id,
        group=group,
        label=label,
        description=description,
        context_tokens=_context_tokens(entry),
        retires_on=_as_date(entry.get("deprecation")),
        replacement=str(entry.get("deprecation_replacement_model") or ""),
    )


def _context_tokens(entry: dict[str, Any]) -> int | None:
    """Return how much text *entry*'s model can hold at once, if the service says so."""
    value = entry.get("max_context_length")
    return value if isinstance(value, int) and value > 0 else None


def _sort_key(model: ListedModel) -> tuple[int, int, str]:
    """Return the sort key of *model*: group, then size, then name."""
    return (_GROUP_ORDER.get(model.group, len(_GROUP_ORDER)), _size_rank(model.id), model.id)


def _size_rank(model_id: str) -> int:
    """Return where *model_id*'s model size sorts: large, medium, small, ministral, other."""
    for rank, family in enumerate(_SIZE_ORDER):
        if family in model_id:
            return rank
    return len(_SIZE_ORDER)


def list_chat_models_checked(base_url: str, fallback: list[str]) -> ModelListing:
    """Return the chat-capable model IDs served at *base_url*, saying whether they are live.

    Asking the endpoint the provider will use means the EU provider only lists models the
    EU endpoint actually serves.  Any problem gives the *fallback* list with
    ``source="failed"`` and the reason, never a list that looks like the service's answer.
    """
    api_key = os.environ.get(ENV_KEY, "").strip()
    if not api_key:
        return ModelListing(list(fallback), "failed", "no Mistral key is set")
    request = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_LIST_TIMEOUT_S) as response:
            entries = json.load(response).get("data", [])
        curated = curate_mistral_models(entries, date.today())
    except urllib.error.HTTPError as exc:  # an OSError too, but its status says why
        if exc.code in (401, 403):
            problem = "Mistral did not accept the key"
        else:
            problem = f"Mistral answered with error {exc.code}"
        return ModelListing(list(fallback), "failed", problem)
    except OSError:  # URLError, timeout: no connection
        return ModelListing(list(fallback), "failed", "no connection to Mistral")
    except (ValueError, AttributeError, TypeError, KeyError):
        return ModelListing(list(fallback), "failed", "Mistral answered unexpectedly")
    if not curated.offered:
        return ModelListing(list(fallback), "failed", "Mistral returned no chat models")
    return ModelListing(
        curated.ids,
        "live",
        details=curated.offered,
        retiring=curated.retiring,
        also_served=curated.also_served,
    )


def create_chat_model(model: str, temperature: float, base_url: str) -> BaseChatModel:
    """Return a ``ChatMistralAI`` for *model* talking to *base_url*.

    The API key is read from ``$MISTRAL_API_KEY`` by LangChain.  Retries (5) and the
    request timeout (120 s) are LangChain's defaults.
    """
    with import_guard("langchain-mistralai"):
        from langchain_mistralai import ChatMistralAI  # noqa: PLC0415
    return ChatMistralAI(
        model=model,
        temperature=temperature,
        endpoint=base_url,
        random_seed=RANDOM_SEED,
    )
