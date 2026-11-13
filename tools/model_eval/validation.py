"""The two modes of checking a run file: format mode, and strict mode with the corpora.

**Format mode** (no corpora, used by the test suite over every committed file) checks the schema,
the types, the caps that need no corpus (at most :data:`~.redaction.MAX_REPLACEMENT_WORDS` words,
explanations under :data:`~.redaction.MAX_EXPLANATION_CHARS` characters, nothing long in an
unknown field, no words at all for a restricted item) and that the header agrees with the file
name and the manifest pin.  **Strict mode** (with the corpora) adds the five-word rule against the
real text of every item and the source, reference and output hashes.

This module judges malformed lines, so it works on the raw dicts of a
:class:`~.run_format.StoredRun`, not on typed items.  It must not import :mod:`.export`,
:mod:`.index`, :mod:`.report` or the command line.
"""

from __future__ import annotations

from collections.abc import Iterable
import math
from pathlib import Path
import re
from typing import Any

from . import protocol
from . import testset as testset_module
from .answer_mode import AnswerMode
from .gleu import ORDER, SOURCE_PREFIX
from .layout import OLLAMA, RUNS_DIR, manifest_for, provider_names
from .redaction import MAX_EXPLANATION_CHARS, Redactor, forbidden_runs, sha256_text, word_count
from .run_format import (
    HEADER_KEYS,
    INPUTS,
    ITEM_KEYS,
    KINDS,
    KNOWN_KEYS,
    NOTE_KEYS,
    OUTCOME_KEYS,
    REPLACEMENT_WORDS,
    RUN_SCHEMA,
    RUN_TYPE,
    SCORE_KEYS,
    TEXT_FIELD_NAMES,
    WARNING_CODES,
    StoredRun,
    apply_stored_edits,
    is_number,
    read_run,
)
from .testset import Corpora, ResolvedItem

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_MODE_VALUES = frozenset(mode.value for mode in AnswerMode)
"""The answer modes a run header may name."""

GLEU_KEYS: tuple[str, ...] = (
    "gleu_c",
    "gleu_r",
    *(f"gleu_m{n}" for n in range(1, ORDER + 1)),
    *(f"gleu_p{n}" for n in range(1, ORDER + 1)),
    f"{SOURCE_PREFIX}_c",
    *(f"{SOURCE_PREFIX}_m{n}" for n in range(1, ORDER + 1)),
    *(f"{SOURCE_PREFIX}_p{n}" for n in range(1, ORDER + 1)),
)
"""The GLEU counts of the model and of the source; the source shares the reference length."""

HALF_KEYS: tuple[str, ...] = ("tp_h1", "fn_h1", "tp_h2", "fn_h2")
"""The exact matches and the misses of each half of the text."""

SPAN_KEYS: tuple[str, ...] = ("alone", "alone_of", "cover", "cover_of")
"""Left alone and coverage, which only a text with sentence spans can have."""


def _is_int(value: Any) -> bool:
    """Whether *value* is an integer that is not a boolean (JSON ``true`` is not a count)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    """Whether *value* is a number a run may store: an int or a finite float, never a bool.

    A new metric needs no schema change, and a number cannot carry corpus text, so the rule
    against corpus text holds for a key nobody has seen yet.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _is_hash(value: Any) -> bool:
    """Whether *value* is a SHA-256 in lower-case hex."""
    return isinstance(value, str) and bool(_HEX64.match(value))


def _strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    """Yield ``(field path, string)`` for every string inside *value*, nested or not."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for position, item in enumerate(value):
            yield from _strings(item, f"{path}[{position}]")


def _keys_of(value: Any, only: frozenset[str]) -> Iterable[str]:
    """Yield the path of every string that sits under a field name in *only*."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in only and isinstance(item, str):
                yield key
            yield from _keys_of(item, only)
    elif isinstance(value, list):
        for item in value:
            yield from _keys_of(item, only)


def _restricted_items(manifest: dict | None) -> dict[str, bool]:
    """Return ``{item id: restricted}`` from a manifest, empty when there is none."""
    if manifest is None:
        return {}
    return {
        str(entry["id"]): bool(manifest["corpora"].get(entry["corpus"], {}).get("restricted"))
        for entry in manifest["items"]
    }


def _check_header(  # noqa: PLR0912, PLR0915  # one branch per required field of the header
    path: Path, header: dict
) -> list[str]:
    """Return what is wrong with the header of a run file, in format mode's sense."""
    problems: list[str] = []
    missing = [key for key in HEADER_KEYS if key not in header]
    if missing:
        problems.append(f"{path.name}: the header has no {', '.join(missing)}")
        return problems
    extra = sorted(set(header) - set(HEADER_KEYS))
    if extra:
        problems.append(f"{path.name}: the header has unknown fields {', '.join(extra)}")
    if not _is_int(header["schema"]) or header["schema"] != RUN_SCHEMA:
        problems.append(f"{path.name}: unsupported run schema {header['schema']!r}")
    if header["type"] != RUN_TYPE:
        problems.append(f"{path.name}: type is {header['type']!r}, not {RUN_TYPE!r}")
    if not isinstance(header["run_id"], str) or header["run_id"] != path.stem:
        problems.append(f"{path.name}: the header names the run {header['run_id']!r}")
    for key in ("testset", "model", "date"):
        if not isinstance(header[key], str) or not header[key]:
            problems.append(f"{path.name}: {key} is not a non-empty string")
    problems.extend(_check_provider(path, header))
    if header["mode"] not in _MODE_VALUES:
        problems.append(
            f"{path.name}: mode is {header['mode']!r}, not one of {sorted(_MODE_VALUES)}"
        )
    if header["kind"] not in KINDS:
        problems.append(f"{path.name}: kind is {header['kind']!r}, not one of {sorted(KINDS)}")
    if header["input"] not in INPUTS:
        problems.append(f"{path.name}: input is {header['input']!r}, not one of {sorted(INPUTS)}")
    if header["explainer"] is not None and not isinstance(header["explainer"], str):
        problems.append(f"{path.name}: explainer is neither null nor a string")
    if header["explainer"] is not None and header["mode"] != AnswerMode.TWO_STEP.value:
        problems.append(
            f"{path.name}: explainer is set, but mode is {header['mode']!r}, not "
            f"{AnswerMode.TWO_STEP.value!r}"
        )
    if not isinstance(header["model_info"], dict):
        problems.append(f"{path.name}: model_info is not an object")
    if not _is_hash(header["manifest_sha256"]):
        problems.append(f"{path.name}: manifest_sha256 is not a SHA-256")
    if not _is_hash(header["instructions_sha256"]):
        problems.append(f"{path.name}: instructions_sha256 is not a SHA-256")
    if not _is_hash(header["fingerprint"]):
        problems.append(f"{path.name}: fingerprint is not a SHA-256")
    if not _is_int(header["eval_version"]) or header["eval_version"] < 1:
        problems.append(f"{path.name}: eval_version is not a positive integer")
    if not is_number(header["temperature"]):
        problems.append(f"{path.name}: temperature is not a number")
    problems.extend(_check_profile(path, header["request_profile"]))
    if header["num_ctx"] is not None and not _is_int(header["num_ctx"]):
        problems.append(f"{path.name}: num_ctx is neither null nor an integer")
    if header["seed"] is not None and not _is_int(header["seed"]):
        problems.append(f"{path.name}: seed is neither null nor an integer")
    if header["vram_baseline_mb"] is not None and not _is_int(header["vram_baseline_mb"]):
        problems.append(f"{path.name}: vram_baseline_mb is neither null nor an integer")
    if not isinstance(header["isolated"], bool):
        problems.append(f"{path.name}: isolated is not true or false")
    for key in ("repeat", "answer_format_version", "cut_fields", "texts"):
        if not _is_int(header[key]):
            problems.append(f"{path.name}: {key} is not an integer")
    if not isinstance(header["loaded_before"], list) or not all(
        isinstance(name, str) for name in header["loaded_before"]
    ):
        problems.append(f"{path.name}: loaded_before is not a list of names")
    sampler = header["gpu_sampler"]
    if sampler is not None and not isinstance(sampler, dict):
        problems.append(f"{path.name}: gpu_sampler is neither null nor an object")
    return problems


def _check_provider(path: Path, header: dict) -> list[str]:
    """Return what is wrong with the provider of a header and the facts that depend on it.

    The provider is one the app can talk to.  A run on Ollama names the machine and the Ollama
    version, because the speed and the memory of a model depend on both.  A run of another
    provider ran on none of ours, so it names neither: ``null``, not a placeholder that would
    look like a machine.
    """
    provider = header["provider"]
    if not isinstance(provider, str) or provider not in provider_names():
        return [f"{path.name}: provider is {provider!r}, not one of {', '.join(provider_names())}"]
    found: list[str] = []
    for key in ("machine", "ollama_version"):
        value = header[key]
        if provider == OLLAMA and (not isinstance(value, str) or not value):
            found.append(f"{path.name}: {key} is not a non-empty string (the run is on Ollama)")
        if provider != OLLAMA and value is not None:
            found.append(f"{path.name}: {key} is not null (the run is of provider {provider!r})")
    return found


def _check_profile(path: Path, profile: Any) -> list[str]:
    """Return what is wrong with the request profile of a header.

    The profile is what the requests were built with, so it is checked like the other facts a
    group is formed by: an object with the compact-JSON rule as a boolean and the temperature as
    a number or ``null`` (``null`` means the app's own default).
    """
    if not isinstance(profile, dict):
        return [f"{path.name}: request_profile is not an object"]
    found: list[str] = []
    if not isinstance(profile.get("compact_json"), bool):
        found.append(f"{path.name}: request_profile.compact_json is not true or false")
    temperature = profile.get("temperature")
    if temperature is not None and not is_number(temperature):
        found.append(f"{path.name}: request_profile.temperature is neither null nor a number")
    extra = sorted(set(profile) - {"compact_json", "temperature"})
    if extra:
        found.append(f"{path.name}: request_profile has unknown fields {', '.join(extra)}")
    return found


def _check_fallback_codes(where: str, outcome: dict) -> list[str]:
    """Return where ``fallback``, ``has_review`` and ``warning_code`` contradict each other.

    ``review_partial`` is a text some of whose pieces fell back and the others did not, so it
    has a review; ``fallback`` is a text that fell back as a whole, so it has none; and a text
    that fell back at all carries one of the two codes.
    """
    flags = (outcome["fallback"], outcome["has_review"])
    if not all(isinstance(flag, bool) for flag in flags):
        return []
    fallback, has_review = flags
    code = outcome["warning_code"]
    found: list[str] = []
    if code == "review_partial" and not (fallback and has_review):
        found.append(f"{where}: review_partial needs fallback and has_review")
    if code == "fallback" and not (fallback and not has_review):
        found.append(f"{where}: warning_code fallback needs fallback and no review")
    if fallback and code not in {"fallback", "review_partial"}:
        found.append(f"{where}: outcome.fallback needs warning_code fallback or review_partial")
    return found


def _check_replacement(field: Any, where: str, restricted: bool) -> list[str]:
    """Return what is wrong with a stored replacement: its shape, its length, its words."""
    problems: list[str] = []
    if not isinstance(field, dict):
        return [f"{where}: a replacement is not an object"]
    if "r" in field:
        if set(field) != {"r"}:
            problems.append(f"{where}: a replacement with text has only 'r'")
        if not isinstance(field["r"], str):
            problems.append(f"{where}: 'r' is not a string")
        elif word_count(field["r"]) > REPLACEMENT_WORDS:
            problems.append(
                f"{where}: the replacement has {word_count(field['r'])} words, "
                f"at most {REPLACEMENT_WORDS} may be stored"
            )
        elif restricted:
            problems.append(f"{where}: a restricted item stores no words")
    else:
        if set(field) != {"h", "n"}:
            problems.append(f"{where}: a replacement without text has 'h' and 'n' only")
        if not _is_hash(field.get("h")):
            problems.append(f"{where}: 'h' is not a SHA-256")
        if not _is_int(field.get("n")):
            problems.append(f"{where}: 'n' is not an integer")
    return problems


def _check_item(  # noqa: PLR0912, PLR0915  # one pass over the fields of one item
    item: dict, entry: dict | None, restricted: bool
) -> list[str]:
    """Return what is wrong with one item line, in format mode's sense."""
    where = f"item {item.get('item', '?')}"
    problems: list[str] = []
    missing = [key for key in ITEM_KEYS if key not in item]
    if missing:
        return [f"{where}: no {', '.join(missing)}"]
    extra = sorted(set(item) - set(ITEM_KEYS))
    if extra:
        problems.append(f"{where}: unknown fields {', '.join(extra)}")
    if entry is not None and item["item"] != entry["id"]:
        problems.append(f"{where}: the manifest has no such item")

    outcome = item["outcome"]
    if not isinstance(outcome, dict) or set(outcome) != set(OUTCOME_KEYS):
        problems.append(f"{where}: outcome is not {', '.join(OUTCOME_KEYS)}")
    else:
        for key in ("failed", "fallback", "has_review"):
            if not isinstance(outcome[key], bool):
                problems.append(f"{where}: outcome.{key} is not true or false")
        if outcome["warning_code"] not in WARNING_CODES:
            problems.append(f"{where}: unknown warning_code {outcome['warning_code']!r}")
        if not _is_int(outcome["pieces"]) or outcome["pieces"] < 1:
            problems.append(f"{where}: outcome.pieces is not a positive integer")
        if not is_number(outcome["seconds"]):
            problems.append(f"{where}: outcome.seconds is not a number")
        problems.extend(_check_fallback_codes(where, outcome))

    scores = item["scores"]
    if not isinstance(scores, dict) or not set(SCORE_KEYS) <= set(scores):
        problems.append(f"{where}: scores is not {', '.join(SCORE_KEYS)}")
    else:
        for key in SCORE_KEYS[:-1]:
            if not _is_int(scores[key]):
                problems.append(f"{where}: scores.{key} is not an integer")
        if not isinstance(scores["notes_linked"], bool):
            problems.append(f"{where}: scores.notes_linked is not true or false")
        for key in sorted(set(scores) - set(SCORE_KEYS)):
            if not _is_finite_number(scores[key]):
                problems.append(f"{where}: scores.{key} is not a number")
        if entry is not None and entry.get("kind") == "errors":
            problems.extend(_check_numbers(where, scores, entry))

    edits = item["edits"]
    if not isinstance(edits, list):
        problems.append(f"{where}: edits is not a list")
        edits = []
    for position, edit in enumerate(edits):
        if not isinstance(edit, list) or len(edit) != 3:
            problems.append(f"{where}: edit {position} is not [start, end, replacement]")
            continue
        if not _is_int(edit[0]) or not _is_int(edit[1]):
            problems.append(f"{where}: edit {position} has no integer positions")
        problems.extend(_check_replacement(edit[2], f"{where} edit {position}", restricted))

    reference = item["reference"]
    if not isinstance(reference, list):
        problems.append(f"{where}: reference is not a list")
    else:
        if entry is not None and len(reference) != len(entry.get("edits", ())):
            problems.append(
                f"{where}: {len(reference)} reference outcomes, "
                f"the manifest has {len(entry.get('edits', ()))} edits"
            )
        for position, outcome in enumerate(reference):
            if (
                not isinstance(outcome, list)
                or len(outcome) != 3
                or not all(isinstance(flag, bool) for flag in outcome[:2])
                or not (outcome[2] is None or isinstance(outcome[2], bool))
            ):
                problems.append(f"{where}: reference {position} is not [exact, located, given]")

    notes = item["notes"]
    if not isinstance(notes, list):
        problems.append(f"{where}: notes is not a list")
        notes = []
    for position, note in enumerate(notes):
        if not isinstance(note, dict) or set(note) != set(NOTE_KEYS):
            problems.append(f"{where}: note {position} is not {', '.join(NOTE_KEYS)}")
            continue
        if note["edit"] is not None and not (
            _is_int(note["edit"]) and 0 <= note["edit"] < len(edits)
        ):
            problems.append(f"{where}: note {position} points at no edit of this item")
        if not isinstance(note["category"], str):
            problems.append(f"{where}: note {position} has no category")
        explanation = note["explanation"]
        if not isinstance(explanation, str):
            problems.append(f"{where}: note {position} has no explanation text")
        elif len(explanation) > MAX_EXPLANATION_CHARS:
            problems.append(
                f"{where}: note {position} has {len(explanation)} characters, "
                f"at most {MAX_EXPLANATION_CHARS} may be stored"
            )
        elif restricted and explanation:
            problems.append(f"{where}: a restricted item stores no explanation text")
        if note["explanation_sha256"] is not None and not _is_hash(note["explanation_sha256"]):
            problems.append(f"{where}: note {position} has no SHA-256 of the explanation")
        if not isinstance(note["orphan"], bool):
            problems.append(f"{where}: note {position} is neither an orphan nor linked")

    if not _is_int(item["unclear"]):
        problems.append(f"{where}: unclear is not an integer")
    if not _is_hash(item["output_sha256"]):
        problems.append(f"{where}: output_sha256 is not a SHA-256")
    if item["performance"] is not None and not isinstance(item["performance"], dict):
        problems.append(f"{where}: performance is neither null nor an object")
    return problems


def _check_numbers(where: str, scores: dict, entry: dict) -> list[str]:
    """Return what is wrong with the numbers an item must carry.

    The halves have to add up to the counts of the whole text, and left alone and coverage have
    to fit the sentences the manifest says the reference leaves alone and touches.  Whether the
    numbers are the right ones needs the source, so that is strict mode's business.
    """
    problems: list[str] = []
    missing = [key for key in (*GLEU_KEYS, *HALF_KEYS) if key not in scores]
    if missing:
        return [f"{where}: scores has no {', '.join(missing)}"]
    for key in (*GLEU_KEYS, *HALF_KEYS):
        if not _is_int(scores[key]):
            problems.append(f"{where}: scores.{key} is not an integer")
    for count, first, second in (("tp", "tp_h1", "tp_h2"), ("fn", "fn_h1", "fn_h2")):
        if all(_is_int(scores[key]) for key in (count, first, second)):
            if scores[first] + scores[second] != scores[count]:
                problems.append(f"{where}: {first} + {second} is not {count}")
    if not entry.get("sentence_spans"):
        return problems
    missing = [key for key in SPAN_KEYS if key not in scores]
    if missing:
        return [*problems, f"{where}: scores has no {', '.join(missing)}"]
    alone, alone_of, cover, cover_of = (scores[key] for key in SPAN_KEYS)
    if not all(_is_int(value) for value in (alone, alone_of, cover, cover_of)):
        return [*problems, f"{where}: {', '.join(SPAN_KEYS)} are not all integers"]
    if alone_of != len(entry.get("unchanged_sentences") or ()):
        problems.append(
            f"{where}: alone_of is {alone_of}, the manifest leaves "
            f"{len(entry.get('unchanged_sentences') or ())} sentences alone"
        )
    elif not 0 <= alone <= alone_of:
        problems.append(f"{where}: alone is {alone} of {alone_of}")
    if not 0 <= cover <= cover_of:
        problems.append(f"{where}: cover is {cover} of {cover_of}")
    return problems


def _check_text_fields(payload: Any, where: str) -> list[str]:
    """Return a problem for every string that sits under a field named like a text."""
    return [
        f"{where}: a field named {name!r} holds text"
        for name in _keys_of(payload, TEXT_FIELD_NAMES)
    ]


def _check_long_unknown_fields(payload: Any, where: str) -> list[str]:
    """Return a problem for a long string under a field the format does not know.

    A field that is not part of the schema has no business holding a sentence: this is what makes
    "no corpus text in the repository" a check and not a promise.
    """
    problems: list[str] = []
    for path, text in _strings(payload):
        name = path.split(".")[-1].split("[")[0]
        if name in KNOWN_KEYS or name in TEXT_FIELD_NAMES:
            continue
        if word_count(text) > REPLACEMENT_WORDS:
            problems.append(f"{where}: the field {path!r} holds a sentence-like text")
    return problems


def validate_run(
    path: Path,
    corpora: Corpora | None = None,
    *,
    manifest_path: Path | None = None,
) -> list[str]:
    """Return every problem of the run file *path*; an empty list means it may be committed.

    Args:
        path: The run file.
        corpora: The corpora, for **strict mode**: with them the five-word rule is checked against
            the real text of every item, and the source, reference and output hashes are verified.
            Without them only the **format mode** checks run, which is what the test suite can do
            over every committed file.
        manifest_path: The manifest that pins the run; by default the one of its test set in
            ``testsets/<testset>/manifest.json`` next to ``runs/``.

    Returns:
        One line per problem, in the order the checks found them.
    """
    try:
        run = read_run(path)
    except ValueError as problem:
        return [str(problem)]
    header = run.header
    problems = _check_header(path, header)
    if "eval_version" in header and "fingerprint" in header:
        if not protocol.is_recorded(header["eval_version"], header["fingerprint"]):
            problems.append(
                f"{path.name}: eval version {header['eval_version']!r} with fingerprint "
                f"{str(header['fingerprint'])[:12]} is not in {protocol.PROTOCOL_PATH.name}"
            )
    if "texts" in header and _is_int(header["texts"]) and header["texts"] != len(run.records):
        problems.append(
            f"{path.name}: the header counts {header['texts']} texts, "
            f"the file has {len(run.records)}"
        )

    pin = manifest_path if manifest_path is not None else manifest_for(path, header)
    manifest: dict | None = None
    if pin.exists():
        manifest = testset_module.read_manifest(pin)
        found = testset_module.manifest_sha256(pin)
        if header.get("manifest_sha256") != found:
            problems.append(
                f"{path.name}: the run pins the manifest "
                f"{str(header.get('manifest_sha256'))[:12]}, {pin.name} is {found[:12]}"
            )
        if manifest["testset"] != header.get("testset"):
            problems.append(
                f"{path.name}: the run names the test set {header.get('testset')!r}, "
                f"the manifest {manifest['testset']!r}"
            )
    elif corpora is not None:
        problems.append(f"{path.name}: no manifest at {pin}")

    entries = {str(entry["id"]): entry for entry in (manifest or {}).get("items", ())}
    restricted = _restricted_items(manifest)
    for item in run.records:
        entry = entries.get(str(item.get("item", ""))) if entries else None
        problems.extend(_check_item(item, entry, restricted.get(str(item.get("item", "")), False)))
        problems.extend(_check_text_fields(item, f"item {item.get('item', '?')}"))
        problems.extend(_check_long_unknown_fields(item, f"item {item.get('item', '?')}"))
    problems.extend(_check_text_fields(header, "header"))
    problems.extend(_check_long_unknown_fields(header, "header"))

    if entries:
        found = {str(item.get("item", "")) for item in run.records}
        missing = sorted(set(entries) - found)
        if missing:
            problems.append(f"{path.name}: no item for {', '.join(missing)}")

    if corpora is not None and manifest is not None:
        problems.extend(_strict_problems(run, manifest, corpora))
    return problems


def _strict_problems(run: StoredRun, manifest: dict, corpora: Corpora) -> list[str]:
    """Return what strict mode finds: the five-word rule against the real text, and the hashes.

    The items of a corpus that was not given are skipped: ``corpus_problems`` names that corpus
    once, and one line per item would only repeat it.
    """
    problems: list[str] = []
    missing = set(corpora.missing(list(manifest["corpora"])))
    entries = {str(entry["id"]): entry for entry in manifest["items"]}
    for item in run.records:
        entry = entries.get(str(item.get("item", "")))
        if entry is None or entry["corpus"] in missing:
            continue
        try:
            resolved = testset_module.resolve(entry, corpora)
        except testset_module.RESOLVE_ERRORS as error:
            problems.append(f"item {entry['id']}: the pointer does not resolve ({error})")
            continue
        problems.extend(_strict_item(item, entry, resolved))
    return problems


def _strict_item(item: dict, entry: dict, resolved: ResolvedItem) -> list[str]:
    """Return what one item of a run gets wrong against the text it was made from."""
    where = f"item {entry['id']}"
    problems: list[str] = []
    # Strict mode checks the caps, which need no hash, so no key is needed here.
    redactor = Redactor(
        forbidden_runs(resolved.source, resolved.reference), restricted=resolved.restricted
    )
    for position, edit in enumerate(item.get("edits", ())):
        field = edit[2] if isinstance(edit, list) and len(edit) == 3 else None
        if isinstance(field, dict) and not redactor.accepts(field):
            problems.append(f"{where} edit {position}: copies a run of corpus words")
    for position, note in enumerate(item.get("notes", ())):
        text = note.get("explanation", "") if isinstance(note, dict) else ""
        if isinstance(text, str) and not redactor.accepts_explanation(text):
            problems.append(f"{where} note {position}: copies a run of corpus words")
    if sha256_text(resolved.source) != entry["source"]["sha256"]:
        problems.append(f"{where}: the source is not the text the manifest pinned")
        return problems
    stored = item.get("edits", ())
    if all(isinstance(edit[2], dict) and "r" in edit[2] for edit in stored if len(edit) == 3):
        applied = apply_stored_edits(
            resolved.source, [(edit[0], edit[1], edit[2]["r"]) for edit in stored]
        )
        if (found := sha256_text(applied)) != item.get("output_sha256"):
            problems.append(
                f"{where}: the edits give {found[:12]}, the header records "
                f"{str(item.get('output_sha256'))[:12]}"
            )
    return problems


# ── the marks of the explanation check ────────────────────────────────────────


def validate_marks(path: Path, out: Path) -> list[str]:
    """Return every problem of the marks file *path*, checked against its run below *out*.

    A marks file holds no text, so it can only be checked against the run it belongs to: the run
    has to exist, every ``(item, note)`` has to be in that run's sample, and the hash of the
    explanation has to be the one the run stored.  A mark of another sample, or of an explanation
    that changed since, is refused.

    Args:
        path: The marks file, ``marks/<run-id>.json``.
        out: The repository directory that holds ``runs/``.
    """
    from . import spot_check  # noqa: PLC0415

    try:
        marks = spot_check.read_marks(path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"{path.name}: {error}"]
    problems: list[str] = []
    if not marks.date.strip():
        problems.append(f"{path.name}: the date is empty")
    if not marks.marker.strip():
        problems.append(f"{path.name}: the marker is empty")
    found = sorted((out / RUNS_DIR).glob(f"*/{marks.run_id}.jsonl"))
    if not found:
        return [*problems, f"{path.name}: no run called {marks.run_id!r} below {out}"]
    if len(found) > 1:
        return [*problems, f"{path.name}: {len(found)} runs are called {marks.run_id!r}"]
    run = read_run(found[0])
    pin = manifest_for(found[0], run.header)
    if not pin.exists():
        return [*problems, f"{path.name}: no manifest at {pin}"]
    drawn = spot_check.sample(run, run.items(testset_module.read_manifest(pin)))
    if marks.sample_seed != drawn.seed:
        problems.append(
            f"{path.name}: the marks name the sample {marks.sample_seed!r}, "
            f"the run's is {drawn.seed!r}"
        )
    for mark in marks.marks:
        note = drawn.find(mark.item, mark.note)
        if note is None:
            problems.append(f"{path.name}: {mark.item} note {mark.note} is not in the sample")
            continue
        if mark.explanation_sha256 != note.explanation_sha256:
            problems.append(
                f"{path.name}: the explanation of {mark.item} note {mark.note} changed since "
                "the run"
            )
    return problems
