"""``INDEX.md``: the one row per run that lets a reader find and check a run file.

The row is computed from what the file itself holds, so the index cannot say more than the run
does.  ``mean F0.5`` is the mean over the languages that have scored texts (``—`` when none has),
and ``s/100 words`` is the seconds the model needed per 100 words of student text.  This module
must not import :mod:`.export`, :mod:`.validation`, :mod:`.report` or the command line.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import testset as testset_module
from .layout import INDEX_NAME, date_of, digest8, manifest_for, run_files
from .performance import per_100_words
from .redaction import sha256_text
from .run_format import StoredItem, StoredRun, read_run
from .scoring import DECIDING_LANGUAGES, mean_f_beta, pooled

# ── the index ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunSummary:
    """The one row the index shows of a run, computed from what the file itself holds."""

    run_id: str
    date: str
    machine: str
    model: str
    digest: str
    mode: str
    explainer: str
    temperature: float
    repeat: int
    kind: str
    texts: int
    mean_f_beta: float | None
    """Mean F0.5 over German and English, as the report's deciding line; ``None`` if neither."""
    seconds_per_100_words: float | None
    eval_version: int
    """The protocol version the run measured; runs of different versions are never compared."""
    sha256: str


def summarise_run(
    path: Path, manifest: dict | None = None, run: StoredRun | None = None
) -> RunSummary:
    """Return the index row of the run in *path*.

    The mean F-beta and the seconds per 100 words are computed from the typed items, as the
    report computes them, so the index and the report cannot disagree.  Both need the manifest:
    it names the language and kind of every item and counts the words.  Without one the row shows
    a dash for both, because a run file alone cannot be typed.

    Args:
        path: A run file.
        manifest: The manifest of its test set; read from the repository when none is given.
        run: The run file as it was read, when the caller has read it already.
    """
    run = run or read_run(path)
    header = run.header
    if manifest is None:
        pin = manifest_for(path, header)
        manifest = testset_module.read_manifest(pin) if pin.exists() else None
    mean: float | None = None
    speed: float | None = None
    items = _typed(run, manifest)
    if items:
        by_language: dict[str, list[StoredItem]] = {}
        for item in items:
            if item.kind == "errors":
                by_language.setdefault(item.language, []).append(item)
        mean = mean_f_beta(
            {
                language: pooled(part)
                for language, part in by_language.items()
                if language in DECIDING_LANGUAGES
            }
        )
        words = sum(item.words for item in items)
        speed = per_100_words(sum(item.seconds for item in items), words) if words else None
    model_info = header.get("model_info") or {}
    return RunSummary(
        run_id=str(header.get("run_id", path.stem)),
        date=date_of(str(header.get("date", ""))),
        machine=str(header.get("machine", "")),
        model=str(header.get("model", "")),
        digest=digest8(model_info),
        mode=str(header.get("mode", "")),
        explainer=str(header.get("explainer") or ""),
        temperature=float(header.get("temperature", 0.0)),
        repeat=int(header.get("repeat", 1)),
        kind=str(header.get("kind", "")),
        texts=len(run.records),
        mean_f_beta=mean,
        seconds_per_100_words=speed,
        eval_version=int(header.get("eval_version", 0)),
        sha256=sha256_text(path.read_text(encoding="utf-8")),
    )


def _typed(run: StoredRun, manifest: dict | None) -> tuple[StoredItem, ...]:
    """Return the typed items of *run*, or none when they cannot be typed.

    A run without a manifest, or whose lines the manifest does not match, gets a dash in the
    index instead of stopping it: ``proofreading_store validate`` names what is wrong with it.
    """
    if manifest is None:
        return ()
    try:
        return run.items(manifest)
    except ValueError:
        return ()


def summaries(out: Path) -> list[RunSummary]:
    """Return the index row of every run below *out*, with the manifest of each test set."""
    manifests: dict[str, dict | None] = {}
    rows = []
    for path in run_files(out):
        run = read_run(path)
        header = run.header
        testset = str(header.get("testset", ""))
        if testset not in manifests:
            pin = manifest_for(path, header)
            manifests[testset] = testset_module.read_manifest(pin) if pin.exists() else None
        rows.append(summarise_run(path, manifests[testset], run))
    return rows


_INDEX_PREAMBLE = """\
# Proofreading runs

Generated by `proofreading_store index`; do not edit, and do not add a run by hand.  A run file is
written once by `proofreading_store export` and never changed afterwards; this list is the way to
find one, and its last column lets a reader check that the file is still the one that was written.

`mean F0.5` is the mean over the languages that have scored texts; `s/100 words` is the seconds the
model needed per 100 words of student text.  `eval` is the protocol version the run measured
(`tools/model_eval/protocol.json`): runs of different versions are never compared, and the commit
in the header is information only.
"""


def index_markdown(out: Path) -> str:
    """Return the contents of ``INDEX.md`` for the runs below *out*."""
    rows = summaries(out)
    lines = [_INDEX_PREAMBLE, ""]
    if not rows:
        lines.append("No runs yet.")
        return "\n".join(lines) + "\n"
    lines.append(
        "| Run | Date | Machine | Model | Digest | Mode | Explainer | T | r | Kind | Texts "
        "| Mean F0.5 | s/100 words | Eval | SHA-256 |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    for row in rows:
        lines.append(
            "| {run} | {date} | {machine} | {model} | {digest} | {mode} | {explainer} | {t:g} "
            "| {r} | {kind} | {texts} | {f} | {speed} | {eval} | `{sha}` |".format(
                run=row.run_id,
                date=row.date,
                machine=row.machine,
                model=row.model,
                digest=row.digest,
                mode=row.mode,
                explainer=row.explainer or "—",
                t=row.temperature,
                r=row.repeat,
                kind=row.kind,
                texts=row.texts,
                f="—" if row.mean_f_beta is None else f"{row.mean_f_beta:.3f}",
                speed=(
                    "—" if row.seconds_per_100_words is None else f"{row.seconds_per_100_words:.1f}"
                ),
                eval=row.eval_version or "—",
                sha=row.sha256,
            )
        )
    return "\n".join(lines) + "\n"


def write_index(out: Path) -> Path:
    """Write ``INDEX.md`` below *out* and return its path."""
    target = out / INDEX_NAME
    target.write_text(index_markdown(out), encoding="utf-8")
    return target
