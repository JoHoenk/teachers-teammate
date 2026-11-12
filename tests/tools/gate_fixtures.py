"""The runs gate 2 reads, built in memory, so a test can move one number and see one check.

Gate 2 judges committed runs, but its arithmetic is about the numbers of a run.  These helpers
build the typed items, the runs and the groups directly: a :class:`Text` is one text of a group
with its manifest entry and the numbers of one repeat, and :func:`group` turns a list of them into
the group :meth:`~tools.model_eval.gate_two.GateTwoRule.judge` reads.  A test that wants another
coverage, another fallback or another GLEU changes one field of one text.

The baseline (:func:`baseline_texts`) passes every floor of the starting rule: five page texts and
one short text per deciding language, each with the numbers measured for ``gemma4:e4b``, and a
French text whose numbers are bad enough to show that French never decides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tools.model_eval import gleu, grouping, protocol, run_format, spot_check, testset

MODEL = "gemma4:e4b"
OTHER = "qwen3:4b"
TESTSET = "test-2026-11"
MACHINE = "rx9070xt-server"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"
OTHER_DIGEST = "sha256:fedcba9876543210fedcba9876543210"

EXPLANATION = "Das Verb muss im Singular stehen."


def header(  # noqa: PLR0913  # one keyword per part of the key a test may vary
    *,
    model: str = MODEL,
    repeat: int = 1,
    mode: str = "review",
    temperature: float = 0.2,
    testset_id: str = TESTSET,
    machine: str = MACHINE,
    eval_version: int | None = None,
    digest: str = DIGEST,
    compact_json: bool = False,
    num_ctx: int = 8192,
    explainer: str | None = None,
    run_id: str | None = None,
) -> dict:
    """Return the header of one run, with every part of the group key a test may vary."""
    return {
        "schema": run_format.RUN_SCHEMA,
        "type": run_format.RUN_TYPE,
        "run_id": run_id or f"{model.replace(':', '-')}-r{repeat}",
        "testset": testset_id,
        "manifest_sha256": "a" * 64,
        "machine": machine,
        "eval_version": (
            protocol.current_stamp().eval_version if eval_version is None else eval_version
        ),
        "model": model,
        "model_info": {"digest": digest},
        "mode": mode,
        "explainer": explainer,
        "temperature": temperature,
        "request_profile": {"compact_json": compact_json, "temperature": temperature},
        "num_ctx": num_ctx,
        "repeat": repeat,
    }


def gleu_scores(
    *,
    matched: Sequence[int] = (90, 80, 70, 60),
    possible: Sequence[int] = (100, 99, 98, 97),
    source: Sequence[int] = (40, 30, 20, 10),
    hyp_len: int = 100,
    ref_len: int = 100,
) -> dict[str, int]:
    """Return the stored GLEU counts of one text: the model's, and the unchanged source's."""
    model = gleu.GleuCounts(
        hyp_len=hyp_len, ref_len=ref_len, matched=tuple(matched), possible=tuple(possible)
    )
    unchanged = gleu.GleuCounts(
        hyp_len=hyp_len, ref_len=ref_len, matched=tuple(source), possible=tuple(possible)
    )
    return {
        **gleu.to_scores(model),
        **gleu.to_scores(unchanged, gleu.SOURCE_PREFIX, reference_length=False),
    }


def note(
    *,
    edit: int | None = 0,
    explanation: str = EXPLANATION,
    category: str = "grammar",
    orphan: bool = False,
) -> dict:
    """Return one stored note of a review: the edit it explains, and what it says."""
    return {
        "edit": edit,
        "explanation": explanation,
        "category": category,
        "orphan": orphan,
    }


def performance(*, done_reason: str = "stop", model: str = MODEL) -> dict:
    """Return what one request cost, as a run stores it."""
    return {
        "requests": [
            {
                "model": model,
                "prompt_tokens": 100,
                "answer_tokens": 50,
                "load_s": 0.0,
                "prompt_eval_s": 0.1,
                "generation_s": 1.0,
                "total_s": 1.1,
                "wall_s": 1.2,
                "done_reason": done_reason,
            }
        ],
        "loaded": [{"name": model, "size_bytes": 5_000_000_000, "size_vram_bytes": 4_000_000_000}],
    }


@dataclass(frozen=True)
class Text:
    """One text of a group: its manifest entry, and the numbers of one repeat."""

    id: str
    language: str = "de"
    band: str = "page"
    kind: str = "errors"
    words: int = 100
    tp: int = 90
    fp: int = 10
    fn: int = 10
    alone: int = 50
    alone_of: int = 100
    cover: int = 90
    cover_of: int = 100
    explained: int = 85
    needed: int = 100
    failed: bool = False
    fallback: bool = False
    marks_added: bool = False
    seconds: float = 1.0
    counts: Mapping[str, int] | None = None
    """The stored GLEU counts; the baseline's when ``None``."""
    notes: tuple[dict, ...] = ()
    edits: tuple[tuple[int, int, str], ...] = ()
    cost: Mapping[str, Any] | None = None
    """What the text cost on the server, as a run stores it; ``None`` when nothing was measured."""
    output_sha256: str = ""

    def entry(self) -> dict:
        """Return the manifest entry of the text."""
        return {
            "id": self.id,
            "language": self.language,
            "kind": self.kind,
            "band": self.band,
            "source": {"words": self.words},
            "sentence_spans": [[0, 4], [4, 8]],
        }

    def record(self) -> dict:
        """Return the item line of the text, as a run stores it."""
        scores = {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "unchanged": 0,
            "explained": self.explained,
            "needed": self.needed,
            "orphans": 0,
            "marks_added": self.marks_added,
            "alone": self.alone,
            "alone_of": self.alone_of,
            "cover": self.cover,
            "cover_of": self.cover_of,
            **(self.counts if self.counts is not None else gleu_scores()),
        }
        record: dict = {
            "item": self.id,
            "scores": scores,
            "outcome": {"failed": self.failed, "fallback": self.fallback, "seconds": self.seconds},
            "notes": list(self.notes),
            "edits": [list(edit) for edit in self.edits],
            "output_sha256": self.output_sha256,
        }
        if self.cost is not None:
            record["performance"] = dict(self.cost)
        return record

    def item(self, head: dict) -> run_format.StoredItem:
        """Return the typed item of the text, as a run of *head* holds it."""
        return run_format.StoredItem.from_record(self.record(), head, self.entry())


def page_texts(language: str, *, count: int = 5, notes: int = 2, **fields: Any) -> list[Text]:
    """Return *count* page texts of one language, each with *notes* linked notes."""
    return [
        Text(
            id=f"{language}-{number:02d}",
            language=language,
            band="page",
            notes=tuple(note(edit=index) for index in range(notes)),
            edits=tuple((index, index + 1, "läuft") for index in range(notes)),
            **fields,
        )
        for number in range(1, count + 1)
    ]


def baseline_texts() -> list[Text]:
    """Return the texts of a group that passes every floor of the starting rule."""
    german = [*page_texts("de"), Text(id="de-06", language="de", band="short")]
    english = page_texts("en")
    french = [
        Text(
            id="fr-01",
            language="fr",
            band="page",
            cover=10,
            alone=10,
            explained=10,
            counts=gleu_scores(matched=(10, 5, 2, 1)),
        )
    ]
    return german + english + french


def minimal_texts() -> list[Text]:
    """Return one short text per deciding language: no page text, so no explanation sample."""
    return [
        Text(id="de-01", language="de", band="short"),
        Text(id="en-01", language="en", band="short"),
    ]


def run(*texts: Text, **fields: Any) -> run_format.StoredRun:
    """Return one run over *texts*, with the header *fields* describe."""
    head = header(**fields)
    return run_format.StoredRun(
        path=Path(f"{head['run_id']}.jsonl"),
        header=head,
        records=tuple(text.record() for text in texts),
    )


def group(*texts: Text, repeats: int = 3, **fields: Any) -> grouping.RunGroup:
    """Return a group of *repeats* runs over *texts*, every run with the same numbers."""
    return group_of([list(texts) for _ in range(repeats)], **fields)


def group_of(per_repeat: Sequence[Sequence[Text]], **fields: Any) -> grouping.RunGroup:
    """Return a group whose repeats hold the texts of *per_repeat*, in repeat order."""
    runs = [
        run(*texts, repeat=repeat, **fields) for repeat, texts in enumerate(per_repeat, start=1)
    ]
    return grouping.RunGroup(
        key=grouping.GroupKey.of(runs[0]),
        runs=tuple(runs),
        items=tuple(
            tuple(text.item(stored.header) for text in texts)
            for stored, texts in zip(runs, per_repeat, strict=True)
        ),
    )


def varying(
    values: Sequence[int], *, field: str = "tp", texts: Sequence[Text] | None = None, **fields: Any
) -> grouping.RunGroup:
    """Return a group whose repeats differ in one number: *field* takes one value per repeat."""
    base = list(minimal_texts() if texts is None else texts)
    return group_of(
        [[replace(text, **{field: value}) for text in base] for value in values], **fields
    )


def marks_for(
    sample: spot_check.Sample, *, correct: int | None = None, vague: int = 0
) -> spot_check.Marks:
    """Return marks of *sample*: *correct* notes of each language correct, then *vague* vague."""
    marks: list[spot_check.Mark] = []
    for notes in sample.per_language().values():
        counted = len(notes) if correct is None else correct
        for number, found in enumerate(notes):
            mark = "correct" if number < counted else ("vague" if number < counted + vague else "")
            if mark:
                marks.append(
                    spot_check.Mark(
                        item=found.item,
                        note=found.note,
                        explanation_sha256=found.explanation_sha256,
                        mark=mark,
                    )
                )
    return spot_check.Marks(
        run_id=sample.run_id,
        date="2026-11-12",
        marker="joho",
        sample_seed=sample.seed,
        marks=tuple(marks),
    )


def repository(root: Path, *texts: Text, run_id: str = "run-r1", **fields: Any) -> Path:
    """Write one run and its manifest below *root*; return the repository directory."""
    out = root / "evaluations"
    head = header(repeat=1, run_id=run_id, **fields)
    run_format.write_run(
        out / "runs" / TESTSET / f"{run_id}.jsonl", head, [text.record() for text in texts]
    )
    testset.write_manifest(
        out / "testsets" / TESTSET / "manifest.json",
        {
            "schema": testset.MANIFEST_SCHEMA,
            "testset": TESTSET,
            "built": "2026-11-09",
            "selection_seed": 20261107,
            "builder_revision": "abc1234",
            "corpora": {},
            "items": [text.entry() for text in texts],
        },
    )
    return out


__all__ = [
    "DIGEST",
    "EXPLANATION",
    "MACHINE",
    "MODEL",
    "OTHER",
    "OTHER_DIGEST",
    "TESTSET",
    "Text",
    "baseline_texts",
    "gleu_scores",
    "group",
    "group_of",
    "header",
    "marks_for",
    "minimal_texts",
    "note",
    "page_texts",
    "performance",
    "repository",
    "run",
    "varying",
]
