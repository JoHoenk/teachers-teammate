"""Which texts a test set consists of, and how to build them again from the corpora.

A test set is not the texts but the *pointers* to them: a manifest records, per item, which corpus
text and which sentences of it were chosen, plus the hashes of what the builder made of them.  The
texts themselves stay on the machine that has the corpora (licences, and real learner text).

That is what makes a committed run checkable: :func:`resolve` turns a pointer back into the same
source and reference text the builder used, and the rebuild of a stored run calls exactly
this function, so building and rebuilding cannot drift apart.

The manifest (``testsets/<testset>/manifest.json``) is what :func:`build_manifest` writes.  It holds
no corpus text: pointers, SHA-256 hashes, word counts and label codes only.

What a corpus *is* — its licence, its files, its pointers — belongs to its adapter in
:mod:`tools.evaluation.input.corpora`, one module per corpus.  Which texts a set chooses belongs to a
recipe in :mod:`tools.evaluation.input.recipes`, one module per test-set id.  This module is the manifest,
and the way from a pointer to a text.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from ..core.constants import TextKind
from ..core.hashing import file_sha256, sha256_text
from ..core.manifest import Manifest, ManifestItem, read_manifest, write_manifest
from ..core.records import TestRecord, key_sha256, question_sha256
from .corpora import CorpusAdapter, ResolvedItem, adapter_class, names, pointers
from .manifest import edit_hashes, manifest_item

RESOLVE_ERRORS = (ValueError, KeyError, IndexError, TypeError, AttributeError, OSError)
"""What a pointer that does not fit its corpus may raise: a problem to report, not a traceback."""


def words(text: str) -> int:
    """Return the number of whitespace-separated words of *text* (the builder's selection unit)."""
    return len(text.split())


BAND_ORDER = ("short", "page", "pages")
"""The bands of the recipes so far, in the order they are listed; any other band follows by name."""


def band_summary(counts: Mapping[str, int]) -> str:
    """Return ``"short 4, page 5, pages 2"`` for the item count per band."""
    known = [band for band in BAND_ORDER if band in counts]
    order = known + sorted(set(counts) - set(known))
    return ", ".join(f"{band} {counts[band]}" for band in order)


class Corpora:
    """The corpora of one command: a path per name, and one adapter instance per corpus.

    The paths are the ones the command line was pointed at; nothing is downloaded or copied.  A
    corpus that is in the repository (``invented-fr``) needs no path: its adapter has a
    ``DEFAULT_ROOT``.
    """

    def __init__(self, paths: Mapping[str, Path]) -> None:
        self._paths: dict[str, Path] = {}
        for name, path in paths.items():
            adapter_class(name)  # refuses an unknown name
            self._paths[name] = Path(path)
        self._adapters: dict[str, CorpusAdapter] = {}

    def adapter(self, name: str) -> CorpusAdapter:
        """Return the adapter of corpus *name*, built once so each corpus is read once.

        Raises:
            ValueError: if no adapter has that name, or the corpus has no path and no default
                location.
        """
        if name not in self._adapters:
            cls = adapter_class(name)
            root = self._paths.get(name, cls.DEFAULT_ROOT)
            if root is None:
                raise ValueError(f"{name}: not given (--corpus {name}={cls.HINT})")
            self._adapters[name] = cls(root)
        return self._adapters[name]

    def root(self, name: str) -> Path:
        """Return the directory (or file) corpus *name* is read from."""
        return self.adapter(name).root

    def missing(self, wanted: Iterable[str]) -> list[str]:
        """Return the names that have neither a path nor a default location, in the given order."""
        found: list[str] = []
        for name in wanted:
            if name in self._paths:
                continue
            try:
                cls = adapter_class(name)
            except ValueError:
                found.append(name)
                continue
            if cls.DEFAULT_ROOT is None:
                found.append(name)
        return found

    @property
    def names(self) -> list[str]:
        """The corpora this command was given, sorted."""
        return sorted(self._paths)

    def __repr__(self) -> str:
        return f"Corpora({', '.join(f'{name}={path}' for name, path in self._paths.items())})"


def corpus_flags(wanted: Iterable[str]) -> str:
    """Return the ``--corpus`` options that name the corpora *wanted*, for a refusal."""
    return ", ".join(f"--corpus {name}={adapter_class(name).HINT}" for name in wanted)


def corpus_paths(pairs: Sequence[str], file: Path | None) -> dict[str, Path]:
    """Return ``{name: path}`` from ``--corpus NAME=PATH`` options and a ``--corpora`` file.

    The file is a JSON object ``{name: path}``; a relative path in it is relative to the file.  A
    ``--corpus`` option overrides the file's entry for the same name.  ``~`` is expanded.

    Raises:
        ValueError: for a value without ``=``, an unknown name, a name given twice, or a file that
            is not a JSON object of strings.
    """
    found = _corpora_file(file) if file is not None else {}
    given: set[str] = set()
    for pair in pairs:
        name, separator, path = pair.partition("=")
        if not separator or not name or not path:
            raise ValueError(f"--corpus {pair!r} is not NAME=PATH")
        if name not in names():
            raise ValueError(f"no corpus {name!r}; known: {', '.join(names())}")
        if name in given:
            raise ValueError(f"{name}: named twice")
        given.add(name)
        found[name] = Path(path).expanduser()
    return found


def _corpora_file(file: Path) -> dict[str, Path]:
    """Return the ``{name: path}`` of a ``--corpora`` file, relative paths taken from the file.

    Raises:
        ValueError: if the file cannot be read, is not a JSON object of strings, or names an
            unknown corpus.
    """
    try:
        content = json.loads(file.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"{file}: {error.strerror}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{file}: not JSON ({error.msg})") from error
    if not isinstance(content, dict) or not all(
        isinstance(name, str) and isinstance(path, str) for name, path in content.items()
    ):
        raise ValueError(f"{file}: not a JSON object of name: path strings")
    found: dict[str, Path] = {}
    for name, path in content.items():
        if name not in names():
            raise ValueError(f"{file}: no corpus {name!r}; known: {', '.join(names())}")
        found[name] = (file.parent / Path(path).expanduser()).resolve()
    return found


def corpus_files(name: str, corpora: Corpora) -> dict[str, Path]:
    """Return the files of corpus *name*, keyed by the path the manifest records.

    The keys are the corpus's own names (``json/A.dev.json``), so a reader can tell which file a
    hash belongs to without knowing where the corpora sit on the builder's disk.
    """
    return corpora.adapter(name).files()


def corpus_problems(manifest: Manifest, corpora: Corpora) -> list[str]:
    """Return what differs between the corpora of *manifest* and the files on disk.

    An empty list means every file still has the hash the manifest pinned, so the items can be
    rebuilt.  A changed corpus is reported, never silently accepted.  A corpus the code does not
    know, one without a path, and a ``restricted`` flag that differs from the adapter's are
    problems too, not tracebacks: format and strict mode would otherwise apply different caps.
    """
    problems: list[str] = []
    for name, entry in manifest.corpora.items():
        if name not in names():
            problems.append(f"{name}: no corpus of that name (known: {', '.join(names())})")
            continue
        if name in corpora.missing([name]):
            problems.append(f"{name}: not given ({corpus_flags([name])})")
            continue
        adapter = corpora.adapter(name)
        if bool(entry.get("restricted")) != adapter.RESTRICTED:
            problems.append(
                f"{name}: the manifest says restricted={entry.get('restricted')}, the adapter "
                f"{adapter.RESTRICTED}: format and strict mode would apply different caps"
            )
        files = adapter.files()
        for key, expected in entry["files"].items():
            path = files.get(key)
            if path is None:
                problems.append(f"{name}: {key} is not a file of this corpus")
            elif not path.exists():
                problems.append(f"{name}: {key} is missing ({path})")
            elif (found := file_sha256(path)) != expected:
                problems.append(
                    f"{name}: {key} has hash {found[:12]}, the manifest {expected[:12]}"
                )
    return problems


def resolve(item: Mapping[str, Any], corpora: Corpora) -> ResolvedItem:
    """Build the item *item* of a manifest from the corpora, by way of its pointer.

    The corpus the item names decides which adapter reads it, not its language: a second German
    corpus is a second name.

    Raises:
        ValueError: if the corpus is unknown or has no path, the corpus has no texts in the item's
            language, or the pointer does not fit the corpus (an unknown text, a sentence that is
            not unchanged for a ``"correct"`` item, a document id that is not in the level).
    """
    corpus = item["corpus"]
    adapter = corpora.adapter(corpus)
    language = item["language"]
    if language not in adapter.LANGUAGES:
        raise ValueError(f"{item['id']}: the corpus {corpus!r} has no {language!r} texts")
    return adapter.resolve(item)


def to_record(
    resolved: ResolvedItem, *, band: str = "", pointer: Mapping[str, Any] | None = None
) -> TestRecord:
    """Return *resolved* as the typed record the tools read and write.

    *band* is the band the recipe drew the text for (``short``, ``page``, ``pages``); ``""`` for a
    recipe that has none.  *pointer* is where in the corpus the text sits, which the record keeps
    as provenance.  The CEFR level a corpus gives is meta too, and lives in the manifest alone: no
    run and no score reads it, and a record that carried it would change the key for a fact about
    the corpus rather than about the text.
    """
    return TestRecord(
        id=resolved.id,
        language=resolved.language,
        kind=resolved.kind,
        origin=resolved.origin,
        source=resolved.source,
        reference=resolved.reference,
        sentences=resolved.sentences,
        labels=resolved.labels,
        band=band,
        corpus=resolved.corpus,
        pointer=dict(pointer or {}),
        sentence_spans=resolved.sentence_spans,
        unchanged_sentences=resolved.unchanged_sentences,
    )


# ── the manifest ──────────────────────────────────────────────────────────────


def build_manifest(
    items: Sequence[dict],
    corpora: Corpora,
    *,
    testset: str,
    built: str,
    selection_seed: int,
    builder_revision: str,
    key_version: int = 1,
) -> Manifest:
    """Return the manifest of the test set *testset*, built from the pointers of *items*.

    Every item is resolved, so a pointer that does not fit its corpus is refused here rather than
    found later by a rebuild.  Reference replacements are stored as hashes only (the annotator's
    words are corpus text), as are the sentences of a ``"correct"`` item.  A corpus is pinned by the
    files of the splits its items use, and by no others.
    """
    entries: list[ManifestItem] = []
    records: list[TestRecord] = []
    used: dict[str, set[str]] = {}
    for item in items:
        resolved = resolve(item, corpora)
        record = to_record(resolved, band=str(item.get("band", "")), pointer=item["pointer"])
        records.append(record)
        entries.append(
            manifest_item(
                record,
                {
                    "corpus": resolved.corpus,
                    "band": str(item.get("band", "")),
                    "level": resolved.level if "level" in item else "",
                    "pointer": item["pointer"],
                },
            )
        )
        used.setdefault(resolved.corpus, set()).update(
            corpora.adapter(resolved.corpus).splits_of(item)
        )

    return Manifest(
        testset=testset,
        built=built,
        selection_seed=selection_seed,
        builder_revision=builder_revision,
        key_version=key_version,
        corpora={name: corpus_record(name, corpora, splits) for name, splits in used.items()},
        question_sha256=question_sha256(records),
        key_sha256=key_sha256(records),
        items=tuple(entries),
    )


def corpus_record(name: str, corpora: Corpora, splits: Collection[str] | None = None) -> dict:
    """Return the manifest entry of corpus *name*: its licence and the hash of every file.

    *splits* narrows the pinned files to the splits the items use; ``None`` pins every split.
    """
    adapter = corpora.adapter(name)
    return {
        "licence": adapter.LICENCE,
        "restricted": adapter.RESTRICTED,
        "files": {key: file_sha256(path) for key, path in adapter.files(splits).items()},
    }


def _pointer_of(entry: ManifestItem) -> dict[str, Any]:
    """Return the item as the corpus adapters read it: its pointer plus what names it."""
    return {
        "id": entry.id,
        "language": entry.language,
        "kind": entry.kind,
        "corpus": entry.corpus,
        "pointer": entry.pointer,
    }


def check_items(manifest: Manifest, corpora: Corpora) -> list[str]:
    """Return what differs between a rebuilt item and the manifest that pinned it.

    An empty list means every source, reference and sentence has the hash the manifest recorded,
    and that every reference edit still hashes to what the corpus makes the annotators write.  An
    item whose pointer does not resolve is a problem of its own, not a traceback.
    """
    problems: list[str] = []
    for entry in manifest.items:
        try:
            resolved = resolve(_pointer_of(entry), corpora)
        except RESOLVE_ERRORS as error:
            problems.append(f"{entry.id}: the pointer does not resolve ({error})")
            continue
        record = to_record(resolved, band=entry.band, pointer=entry.pointer)
        if (found := sha256_text(record.source)) != entry.source.get("sha256"):
            problems.append(f"{entry.id}: source has hash {found[:12]}, the manifest another")
            continue
        if entry.kind == TextKind.CORRECT:
            found_sentences = [sha256_text(sentence) for sentence in record.sentences]
            if found_sentences != list(entry.sentence_sha256):
                problems.append(f"{entry.id}: the correct sentences are not the ones stored")
        elif (found := sha256_text(record.reference)) != entry.reference.get("sha256"):
            problems.append(f"{entry.id}: reference has hash {found[:12]}, the manifest another")
        elif edit_hashes(record) != [dict(edit) for edit in entry.edits]:
            problems.append(f"{entry.id}: the reference edits are not the ones stored")
        elif entry.level and resolved.level != entry.level:
            problems.append(f"{entry.id}: the level is {resolved.level!r}, the manifest another")
        elif entry.sentence_spans and (
            list(record.sentence_spans) != [tuple(span) for span in entry.sentence_spans]
            or list(record.unchanged_sentences) != list(entry.unchanged_sentences)
        ):
            problems.append(f"{entry.id}: the sentence spans are not the ones stored")
    return problems


__all__ = [
    "RESOLVE_ERRORS",
    "Corpora",
    "CorpusAdapter",
    "ResolvedItem",
    "adapter_class",
    "build_manifest",
    "check_items",
    "corpus_files",
    "corpus_flags",
    "corpus_paths",
    "corpus_problems",
    "corpus_record",
    "edit_hashes",
    "file_sha256",
    "names",
    "pointers",
    "read_manifest",
    "resolve",
    "sha256_text",
    "to_record",
    "words",
    "write_manifest",
]
