"""Everything a proofreading request says that no cache key hashes, pinned with ``PROMPT_VERSION``.

The key of a stored proofreading result hashes the system prompts of the steps (and the request
profile).  What else a model reads is not in it: the wording around the text in the user
messages, the layout of the explain body, the repair message and the answer schemas, whose field
descriptions the model sees.  A change to one of them changes the requests but not the key, so
stored results of the old wording would be served as if they were the new one.

This test is the guard: it pins those texts together with the prompt version.  When it fails,
bump ``PROMPT_VERSION`` (then the keys move, and ``test_cache_keys_golden.py`` says so) and write
the new pair here.
"""

from __future__ import annotations

import hashlib
import json

from teachers_teammate.domain.correction_prompts import (
    PROMPT_VERSION,
    correct_user,
    explain_body,
    explain_user,
    repair_message,
    unclear_user,
)
from teachers_teammate.domain.proofreading.edits import batches, edits, sentence_groups
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.domain.proofreading.word_diff import word_diff
from teachers_teammate.infrastructure.proofreading.schemas import (
    ExplainAnswer,
    UnclearAnswer,
    explain_schema,
)

#: ``(PROMPT_VERSION, digest)`` of :func:`_unhashed_request_texts`.
_PINNED = ("2", "7f0e9ae2d8686afbd34248b1b4d7b4ec12fffe2e167516f38ce074faa3a14bb5")


def _unhashed_request_texts() -> str:
    """Return the texts of a request that its cache key does not hash, as one string."""
    source = "Ich habe ein grosse Hund. Weil es regnete."
    corrected = "Ich habe einen großen Hund, weil es regnete."
    spans = sentence_spans(source)
    found = edits(source, corrected)
    batch = batches(found, sentence_groups(spans, found), 20)[0]
    body = explain_body(source, corrected, spans, batch, word_diff(source, corrected))
    parts = [
        correct_user("TEXT", language="German", level="B1", notes="NOTES"),
        explain_user(
            body, language="German", level="B1", explanation_language="English", notes="NOTES"
        ),
        unclear_user("TEXT", language="German", level="B1", explanation_language="English"),
        repair_message("ERROR"),
        *(
            json.dumps(shape.model_json_schema(), sort_keys=True, ensure_ascii=False)
            for shape in (ExplainAnswer, UnclearAnswer, explain_schema([3, 7]))
        ),
    ]
    return "\n\n---\n\n".join(parts)


def test_the_texts_around_the_text_change_only_with_the_prompt_version() -> None:
    """
    Given  the user messages, the explain body, the repair message and the answer schemas
    When   their texts are hashed next to the prompt version
    Then   the pair is the pinned one, so a changed wording cannot keep the version and with it
           the cache keys of the stored results it no longer matches
    """
    digest = hashlib.sha256(_unhashed_request_texts().encode("utf-8")).hexdigest()

    assert (PROMPT_VERSION, digest) == _PINNED, (
        "A text a proofreading request carries changed: bump PROMPT_VERSION in "
        "domain/correction_prompts.py, update the cache-key goldens, and pin "
        f"({PROMPT_VERSION!r}, {digest!r}) here."
    )
