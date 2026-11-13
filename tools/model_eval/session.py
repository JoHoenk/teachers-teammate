"""The sessions of one run: ``<name>.header.json`` and whether a run may be resumed into it.

A run is one results file and one header file.  Every time ``run`` starts on it, it appends one
entry to the header file: a *session*.  The entries of one run have to agree on everything that
decides what the texts mean (:data:`RESUME_KEYS`), or the run would export as one consistent run
although its texts were made under two protocols.  ``run`` checks that before it sends anything;
the export checks it again as a backstop.

This module needs nothing but the standard library, so the export can use it without the
run-time code of :mod:`.measure` (urllib, git, the GPU sampler, the Ollama client).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from itertools import pairwise
import json
from pathlib import Path

RESUME_KEYS: tuple[str, ...] = (
    "eval_version",
    "fingerprint",
    "testset",
    "testset_sha256",
    "texts",
    "provider",
    "machine",
    "ollama_version",
    "model",
    "mode",
    "temperature",
    "request_profile",
    "num_ctx",
    "timeout",
    "seed",
    "repeat",
    "isolated",
    "gpu_sampler",
    "instructions_sha256",
    "answer_format_version",
)
"""What two sessions of one run have to agree on, so a resumed run stays one run.

``testset_sha256`` and ``texts`` say that the texts are the same ones (a test set rebuilt under
the same name is another set); ``timeout`` decides which texts fail.  The model's digest is
compared as well (:func:`resume_conflicts`).
"""

SESSION_KEYS: tuple[str, ...] = (
    "date",
    "tool_revision",
    "loaded_before",
    "vram_baseline_mb",
)
"""What may differ between sessions of one run: it describes one session, not the run."""


def resume_conflicts(previous: dict, current: dict) -> list[str]:
    """Return one line per fact on which two sessions of a run disagree.

    The model's digest is compared as well as the tag, because a tag can be re-pointed.
    """
    conflicts = [
        f"{key}: {previous.get(key)!r} → {current.get(key)!r}"
        for key in RESUME_KEYS
        if previous.get(key) != current.get(key)
    ]
    before = (previous.get("model_info") or {}).get("digest")
    after = (current.get("model_info") or {}).get("digest")
    if before != after:
        conflicts.append(f"model_info.digest: {before!r} → {after!r}")
    return conflicts


def append_session(path: Path, header: dict) -> None:
    """Add *header* to the list stored in *path*; a resumed run adds a further entry."""
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entries.append(header)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_sessions(path: Path) -> list[dict]:
    """Return the sessions stored in *path*, oldest first.

    Raises:
        ValueError: if the file is not JSON, or does not hold a non-empty list of objects.
    """
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: not JSON ({error})") from error
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: the header list is empty or not a list")
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"{path}: an entry of the header list is not an object")
    return entries


def session_conflicts(entries: Sequence[dict]) -> list[str]:
    """Return what the consecutive sessions of a run disagree on, each line naming the pair."""
    found: list[str] = []
    for number, (previous, current) in enumerate(pairwise(entries), 2):
        found.extend(f"session {number}: {line}" for line in resume_conflicts(previous, current))
    return found


def resume_problem(
    rows_path: Path, header_path: Path, identity: dict, *, done: Collection[str]
) -> str | None:
    """Return why a run may not be resumed into its files, or ``None`` when it may.

    *identity* is the header the new session would write (:func:`..measure.run_identity`).  A
    results file without a header cannot be resumed into: nobody can say which session made the
    rows.  A header file without rows is fine, because the previous session may have stopped
    before its first text.  Every message says which files to delete to start the run over.
    """
    restart = f"delete {rows_path} and {header_path} to start this run over"
    if not header_path.exists():
        if done:
            return (
                f"{rows_path.name}: {len(done)} results but no {header_path.name}, so nobody can "
                f"say which session made them; {restart}"
            )
        return None
    try:
        sessions = read_sessions(header_path)
    except ValueError as error:
        return f"{error}; {restart}"
    conflicts = [*session_conflicts(sessions), *resume_conflicts(sessions[-1], identity)]
    if not conflicts:
        return None
    return (
        f"{rows_path.name}: this run cannot be resumed into, because the sessions would not be "
        "one run:\n  " + "\n  ".join(conflicts) + f"\n{restart}"
    )
