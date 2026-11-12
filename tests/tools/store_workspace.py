"""A local run cache and the repository it is exported into, for the tests of the run format.

The helpers here build what a real run leaves behind: an invented corpus, a manifest, a local
cache of result rows and a machine file, and they export that cache into a repository directory.
Every test file of the run format starts from :func:`workspace`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionOutcome,
    CorrectionReview,
    SentenceNote,
)
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.interfaces import Corrector as CorrectorPort
from tests.tools import invented_corpus
from tools.model_eval import export as export_module
from tools.model_eval import protocol, run_format, testset
from tools.model_eval.answer_mode import DEFAULT_MODE, AnswerMode
from tools.model_eval.comparison import WritingModelComparison
from tools.model_eval.export import ExportedRun
from tools.model_eval.testset import Corpora

MODEL = "gemma4:e2b"
TESTSET = "test-2026-11"
MACHINE = "rx9070xt-server"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"


# ── a local cache and the repository it is exported into ─────────────────────


@dataclass
class Workspace:
    """A local run cache, a manifest, a machine file and the repository directory to export into."""

    root: Path
    corpora: Corpora
    manifest: Path
    cache: Path
    out: Path
    overrides: dict[str, Answer] = field(default_factory=dict)
    reviews: dict[str, CorrectionReview] = field(default_factory=dict)


Answer = str | Callable[[str], str]
"""What a test scripts for one item: the text itself, or a way to derive it from the reference."""


class Corrector(CorrectorPort):
    """A corrector that answers with a text and a review per source, both from dictionaries."""

    def __init__(self, answers: dict[str, str], reviews: dict[str, CorrectionReview]) -> None:
        self._answers = answers
        self._reviews = reviews

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the scripted answer for *raw_text*."""
        return CorrectionOutcome(
            text=self._answers[raw_text],
            review=self._reviews.get(raw_text),
            failed=False,
            warning=None,
        )


def session(  # noqa: PLR0913  # one keyword per field of a header, as the header reads
    *,
    mode: AnswerMode = DEFAULT_MODE,
    tool_revision: str = "cfdc0ea",
    testset_id: str = TESTSET,
    model: str = MODEL,
    texts: int = 7,
    date: str = "2026-11-09T18:00:00",
    digest: str = DIGEST,
    seed: int = 111,
    eval_version: int | None = None,
    fingerprint: str | None = None,
    instructions: str | None = None,
    answer_format: int | None = None,
) -> dict:
    """Return one session entry of a local ``.header.json``, as ``run`` writes it.

    The protocol fields are the ones the tool records today; a test that wants a run made before
    the protocol was recorded, or with an unrecorded one, overrides them.
    """
    stamp = protocol.current_stamp()
    return {
        "date": date,
        "tool_revision": tool_revision,
        "machine": MACHINE,
        "ollama_version": "0.40.13",
        "model": model,
        "model_info": {
            "digest": digest,
            "quantization": "Q4_K_M",
            "parameter_size": "2B",
            "family": "gemma3",
        },
        "mode": mode,
        "temperature": 0.7,
        "request_profile": {"compact_json": False, "temperature": 0.7},
        "num_ctx": 4096,
        "seed": seed,
        "repeat": 1,
        "isolated": False,
        "loaded_before": [],
        "gpu_sampler": None,
        "vram_baseline_mb": None,
        "testset": testset_id,
        "texts": texts,
        "timeout": 600.0,
        "testset_sha256": "b" * 64,
        "eval_version": stamp.eval_version if eval_version is None else eval_version,
        "fingerprint": stamp.fingerprint if fingerprint is None else fingerprint,
        "instructions_sha256": (
            protocol.instructions_sha256(["de", "en", "fr"], mode=mode)
            if instructions is None
            else instructions
        ),
        "answer_format_version": ANSWER_FORMAT_VERSION if answer_format is None else answer_format,
    }


def workspace(  # noqa: PLR0913  # one keyword per thing a test may vary
    tmp_path: Path,
    *,
    overrides: dict[str, Answer] | None = None,
    reviews: dict[str, CorrectionReview] | None = None,
    tool_revision: str = "cfdc0ea",
    testset_id: str = TESTSET,
    model: str = MODEL,
    mode: AnswerMode = DEFAULT_MODE,
    instrumentation=None,
    extra: Mapping[str, Path] | None = None,
    pointers: Sequence[dict] | None = None,
) -> Workspace:
    """Write an invented corpus, a local run cache and a machine file, ready to export.

    ``overrides`` maps an item id to the text the model wrote in its place, ``reviews`` an item id
    to the review the model gave: together they stand in for a real run's answers.
    ``instrumentation`` measures what each text cost, as :mod:`.measure` does in a real run.
    ``extra`` adds corpora of the test's own, ``pointers`` replaces the items of the set.
    """
    corpora, _ = invented_corpus.write_corpus(tmp_path, extra=extra, pointers=pointers)
    pinned = invented_corpus.manifest(tmp_path, corpora, testset_id=testset_id, pointers=pointers)
    manifest = tmp_path / "manifest.json"
    testset.write_manifest(manifest, pinned)

    space = Workspace(
        root=tmp_path,
        corpora=corpora,
        manifest=manifest,
        cache=tmp_path / "results",
        out=tmp_path / "evaluations",
        overrides=dict(overrides or {}),
        reviews=dict(reviews or {}),
    )
    space.cache.mkdir()
    records = [
        testset.to_record(testset.resolve(entry, corpora), band=entry.get("band", ""))
        for entry in pinned["items"]
    ]
    # A perfect model: the reference where the corpus has one, the text itself where it has none.
    answers: dict[str, str] = {}
    asked: dict[str, CorrectionReview] = {}
    for item, record in zip(pinned["items"], records, strict=True):
        perfect = record.reference or record.source
        answer = space.overrides.get(item["id"], perfect)
        answers[record.source] = answer(perfect) if callable(answer) else answer
        if item["id"] in space.reviews:
            asked[record.source] = space.reviews[item["id"]]
    comparison = WritingModelComparison(
        lambda tag, temperature: Corrector(answers, asked),
        clock=lambda: 0.0,
        instrumentation=instrumentation,
    )
    rows = list(comparison.run(records, model=model, temperature=0.7))
    (space.cache / "run.jsonl").write_text(
        "".join(json.dumps(row.to_dict(), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (space.cache / "run.header.json").write_text(
        json.dumps(
            [
                session(
                    mode=mode,
                    tool_revision=tool_revision,
                    testset_id=testset_id,
                    model=model,
                    texts=len(rows),
                )
            ]
        ),
        encoding="utf-8",
    )
    (space.out / "machines").mkdir(parents=True)
    (space.out / "machines" / f"{MACHINE}.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": MACHINE,
                "gpu": {"vendor": "amd", "name": "AMD Radeon RX 9070 XT", "vram_mb": 16304},
                "cpu": "AMD Ryzen 7 5800H, 8 cores / 16 threads",
                "ram_gib": 31,
                "os": "Linux 7.0.0-42-generic, bare metal",
                "accelerator_stack": "ROCm 10.1.0, amd-smi 27.1.0",
            }
        ),
        encoding="utf-8",
    )
    return space


def export(space: Workspace, **kwargs) -> list[ExportedRun]:
    """Export the workspace's cache, with optionally another test-set name or label."""
    return export_module.export(
        space.cache,
        testset=kwargs.pop("testset", TESTSET),
        manifest=space.manifest,
        corpora=space.corpora,
        machine=MACHINE,
        out=space.out,
        **kwargs,
    )


def one(space: Workspace, **kwargs) -> ExportedRun:
    """Export the workspace's cache and return the one run that export wrote."""
    written = export(space, **kwargs)
    assert len(written) == 1, written
    return written[0]


def source_of(space: Workspace, item: str) -> str:
    """Return the source text of item *item*, as the invented corpus rebuilds it."""
    return testset.resolve(entry(space, item), space.corpora).source


def entry(space: Workspace, item: str) -> dict:
    """Return the manifest entry of item *item*."""
    pinned = testset.read_manifest(space.manifest)
    return next(entry for entry in pinned["items"] if entry["id"] == item)


def review(*changes: ChangeNote, original: str, corrected: str) -> CorrectionReview:
    """Return a review of one sentence with *changes*."""
    return CorrectionReview(
        sentences=(SentenceNote(original=original, corrected=corrected, changes=changes),)
    )


COMMITTED = Path(__file__).resolve().parents[2] / "evaluations" / "proofreading"
"""The repository directory the committed runs live in."""


def append_session(space: Workspace, **overrides) -> None:
    """Append a second session to the local header, as a resumed run does."""
    path = space.cache / "run.header.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries.append(session(**overrides))
    path.write_text(json.dumps(entries), encoding="utf-8")


def rewrite(path: Path, change) -> None:
    """Read the run file at *path*, let *change* edit the header and items, and write it back."""
    run = run_format.read_run(path)
    header = dict(run.header)
    items = [dict(item) for item in run.records]
    change(header, items)
    run_format.write_run(path, header, items)


def first_item_as(space: Workspace, **changes) -> Path:
    """Change the first row of the local run (the fields of a result row), then export it."""
    rows_path = space.cache / "run.jsonl"
    lines = rows_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first.update(changes)
    lines[0] = json.dumps(first, ensure_ascii=False)
    rows_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return one(space).path
