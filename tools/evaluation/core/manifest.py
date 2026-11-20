"""The manifest of a test set: what pins every item without holding a word of its text.

A committed test set is a manifest and nothing else.  Per item it records where the text sits in its
corpus (the pointer), the hashes of what the builder made of it, and the reference edits as
character positions plus hashes.  That is enough to rebuild the set on the machine that has the
corpora, and to check a committed run without them.

One format, one record.  The builder (:func:`~..input.manifest.manifest_item`) and the converter of
the old sets both write this shape, so a set built today and a set converted from the old tools are
the same file.  Positions are **characters** of the stored text, end exclusive, as everywhere else.

A reader refuses a format it does not know and keeps the fields it does not understand, so a newer
writer's field survives a round trip through an older reader.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, ClassVar

from .formats import MANIFEST_FORMAT, FormatError, check_format, extra_fields
from .records import text_id


@dataclass(frozen=True)
class ManifestItem:
    """One text of a set, as the manifest pins it: a pointer, hashes and character positions."""

    id: str
    language: str
    kind: str
    """``errors`` (a text with mistakes) or ``correct`` (one that is already right)."""

    corpus: str = ""
    band: str = ""
    """The band the recipe drew the text for (``short``, ``page``, ``pages``); ``""`` for none."""

    level: str = ""
    """The CEFR level the corpus gives the text; meta only, no run and no score reads it."""

    pointer: Any = field(default_factory=dict)
    """Where in the corpus the text sits, as the corpus adapter names it: one object or several."""

    text_sha256: str = ""
    """The hash of the question, which is what a run binds to."""

    key_part_sha256: str = ""
    """The hash of this text's key group, so a changed key is visible per text."""

    request_sha256: Mapping[str, str] = field(default_factory=dict)
    """The hash of every request part the text has, keyed by part name."""

    source: Mapping[str, Any] = field(default_factory=dict)
    """``{sha256, words, chars}`` of the source text."""

    reference: Mapping[str, Any] = field(default_factory=dict)
    """``{sha256}`` of the reference, for an ``errors`` text; empty for a ``correct`` one."""

    sentence_sha256: tuple[str, ...] = ()
    """The hash of every already-correct sentence, for a ``correct`` text; empty otherwise."""

    sentence_spans: tuple[tuple[int, int], ...] = ()
    """Character spans of the sentences, when the corpus gives them."""

    unchanged_sentences: tuple[int, ...] = ()
    """The indices of the sentences the annotators left alone."""

    edits: tuple[Mapping[str, Any], ...] = ()
    """The reference edits: character span, replacement hash, word count and label code."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "id",
        "language",
        "kind",
        "corpus",
        "band",
        "level",
        "pointer",
        "text_sha256",
        "key_part_sha256",
        "request_sha256",
        "source",
        "reference",
        "sentence_sha256",
        "sentence_spans",
        "unchanged_sentences",
        "edits",
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable record; an empty optional field is left out."""
        record: dict[str, Any] = {
            "id": self.id,
            "language": self.language,
            "kind": self.kind,
        }
        for name in ("corpus", "band", "level", "text_sha256", "key_part_sha256"):
            value = getattr(self, name)
            if value:
                record[name] = value
        if self.pointer:
            record["pointer"] = self.pointer
        if self.request_sha256:
            record["request_sha256"] = dict(self.request_sha256)
        if self.source:
            record["source"] = dict(self.source)
        if self.reference:
            record["reference"] = dict(self.reference)
        if self.sentence_sha256:
            record["sentence_sha256"] = list(self.sentence_sha256)
        if self.sentence_spans:
            record["sentence_spans"] = [list(span) for span in self.sentence_spans]
        if self.unchanged_sentences:
            record["unchanged_sentences"] = list(self.unchanged_sentences)
        if self.edits:
            record["edits"] = [dict(edit) for edit in self.edits]
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> ManifestItem:
        """Build an item from :meth:`to_dict` output."""
        return cls(
            id=str(record["id"]),
            language=str(record["language"]),
            kind=str(record.get("kind", "errors")),
            corpus=str(record.get("corpus", "")),
            band=str(record.get("band", "")),
            level=str(record.get("level", "")),
            pointer=record.get("pointer") or {},
            text_sha256=str(record.get("text_sha256", "")),
            key_part_sha256=str(record.get("key_part_sha256", "")),
            request_sha256={
                str(name): str(value)
                for name, value in (record.get("request_sha256") or {}).items()
            },
            source=dict(record.get("source") or {}),
            reference=dict(record.get("reference") or {}),
            sentence_sha256=tuple(str(value) for value in record.get("sentence_sha256") or ()),
            sentence_spans=tuple(
                (int(start), int(end)) for start, end in record.get("sentence_spans") or ()
            ),
            unchanged_sentences=tuple(
                int(index) for index in record.get("unchanged_sentences") or ()
            ),
            edits=tuple(dict(edit) for edit in record.get("edits") or ()),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class Manifest:
    """A whole test set: which texts it holds, what pins them, and the hashes of the set."""

    testset: str
    built: str = ""
    """The day the set was built."""

    selection_seed: int = 0
    """The seed the recipe drew the texts with."""

    builder_revision: str = ""
    """The commit of the builder that made the set."""

    key_version: int = 1
    """Raised by hand when the key changes: the same runs, scored against a better key."""

    corpora: Mapping[str, Any] = field(default_factory=dict)
    """Per corpus: its licence, whether it is restricted, and the hash of every file used."""

    question_sha256: str = ""
    """The hash of the whole set's question: its texts' hashes, in order."""

    key_sha256: str = ""
    """The hash of the whole set's key."""

    items: tuple[ManifestItem, ...] = ()

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "format",
        "testset",
        "built",
        "selection_seed",
        "builder_revision",
        "key_version",
        "corpora",
        "question_sha256",
        "key_sha256",
        "items",
    )

    def item(self, text: str) -> ManifestItem | None:
        """Return the item of ``<language>/<id>``, which is how an answer finds its text."""
        language, _, name = text.partition("/")
        return next(
            (item for item in self.items if item.language == language and item.id == name), None
        )

    def by_text(self) -> dict[str, ManifestItem]:
        """Return the items keyed ``<language>/<id>``, in the manifest's order."""
        return {text_id(item): item for item in self.items}

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable manifest, ``format`` first so a reader knows what it has."""
        record: dict[str, Any] = {
            "format": MANIFEST_FORMAT,
            "testset": self.testset,
            "built": self.built,
            "selection_seed": self.selection_seed,
            "builder_revision": self.builder_revision,
            "key_version": self.key_version,
            "corpora": dict(self.corpora),
            "question_sha256": self.question_sha256,
            "key_sha256": self.key_sha256,
            "items": [item.to_dict() for item in self.items],
        }
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Manifest:
        """Build a manifest from :meth:`to_dict` output.

        Raises:
            FormatError: if the record is of a format this reader does not know.
        """
        source = f"manifest of {record.get('testset', '?')!r}"
        if "schema" in record and "format" not in record:
            raise FormatError(
                f"{source}: the old schema {record['schema']}, which pinned token positions; "
                f"this reader knows format {MANIFEST_FORMAT}"
            )
        check_format(record, MANIFEST_FORMAT, source)
        items = record.get("items")
        if not isinstance(items, list):
            raise FormatError(f"manifest of {record.get('testset', '?')!r}: no items")
        return cls(
            testset=str(record["testset"]),
            built=str(record.get("built", "")),
            selection_seed=int(record.get("selection_seed", 0)),
            builder_revision=str(record.get("builder_revision", "")),
            key_version=int(record.get("key_version", 1)),
            corpora=dict(record.get("corpora") or {}),
            question_sha256=str(record.get("question_sha256", "")),
            key_sha256=str(record.get("key_sha256", "")),
            items=tuple(ManifestItem.from_dict(item) for item in items),
            extra=extra_fields(record, cls._KNOWN),
        )


def read_manifest(path: Path) -> Manifest:
    """Return the manifest stored in *path*.

    Raises:
        FormatError: if the file cannot be read, is not a JSON object, or is of another format.
    """
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FormatError(f"{path}: cannot be read as JSON ({error})") from error
    if not isinstance(record, dict):
        raise FormatError(f"{path}: a manifest is a JSON object")
    return Manifest.from_dict(record)


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Write *manifest* to *path* as indented UTF-8 JSON, so ``git diff`` shows what changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


__all__ = ["Manifest", "ManifestItem", "read_manifest", "write_manifest"]
