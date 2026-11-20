"""Building a manifest item: the one place a test text becomes a pinned manifest entry.

Two writers need this shape — the builder of a new set (:mod:`..input.testset`) and the converter of
the old sets (:mod:`..convert.v1`) — and they must produce the same bytes for the same record, or a
converted set and a rebuilt one would be two sets.  So the item is built here, from a
:class:`~..core.records.TestRecord` and the meta the caller knows, and nowhere else.

It sits in ``input`` because it needs the tokenizer: the reference edits are found on tokens and
stored as characters.  ``core`` may not import ``metrics``, and both writers sit above ``input``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..core.constants import TextKind
from ..core.hashing import sha256_text
from ..core.manifest import ManifestItem
from ..core.records import TestRecord
from ..metrics.offsets import token_offsets
from ..metrics.quality import ordered_edits


def manifest_item(record: TestRecord, meta: Mapping[str, Any] | None = None) -> ManifestItem:
    """Return *record* as the manifest item that pins it.

    Args:
        record: The text, with its reference, labels and character positions.
        meta: What the caller knows and the record does not: ``corpus``, ``band``, ``level`` and
            ``pointer``.  A missing key is left out of the item.

    Returns:
        The item, with the hashes of the question, the key and every request part, and the
        reference edits as character positions plus hashes.
    """
    values = dict(meta or {})
    return ManifestItem(
        id=record.id,
        language=record.language,
        kind=record.kind,
        corpus=str(values.get("corpus", record.corpus)),
        band=str(values.get("band", record.band)),
        level=str(values.get("level", "")),
        pointer=dict(values.get("pointer", record.pointer)),
        text_sha256=record.text_sha256,
        key_part_sha256=record.key_sha256,
        request_sha256=record.request_hashes(),
        source={
            "sha256": sha256_text(record.source),
            "words": record.word_count,
            "chars": len(record.source),
        },
        reference={"sha256": sha256_text(record.reference)}
        if record.kind == TextKind.ERRORS
        else {},
        sentence_sha256=(
            tuple(sha256_text(sentence) for sentence in record.sentences)
            if record.kind != TextKind.ERRORS
            else ()
        ),
        sentence_spans=record.sentence_spans,
        unchanged_sentences=record.unchanged_sentences,
        edits=tuple(edit_hashes(record)),
    )


def edit_hashes(record: TestRecord) -> list[dict[str, Any]]:
    """Return the reference edits of *record* as positions, label codes and hashes.

    The positions are **characters** of the source, as every stored position is, so a tokenizer
    change moves none of them.  The replacement itself is the annotator's word, hence corpus text:
    only its SHA-256 is stored, of the replacement's words and marks (whitespace tokens left out)
    joined by single spaces (``""`` for a deletion); ``words`` counts the same tokens.
    """
    if record.kind != TextKind.ERRORS:
        return []
    offsets = token_offsets(record.source)
    codes = {(label.start, label.end): label.code for label in record.labels}
    entries: list[dict[str, Any]] = []
    for start, end, replacement in ordered_edits(record.source, record.reference):
        kept = [token for token in replacement if token.strip()]
        chars = (offsets[start], offsets[end])
        entries.append(
            {
                "start": chars[0],
                "end": chars[1],
                "h": sha256_text(" ".join(kept)),
                "words": len(kept),
                "label": codes.get(chars, ""),
            }
        )
    return entries


__all__ = ["edit_hashes", "manifest_item"]
