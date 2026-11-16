"""A test set and local run caches on disk, so a viewer test reads what a run writes.

The viewer reads two things a run leaves behind: ``<language>.jsonl`` test records and, per run,
``<name>.jsonl`` result rows next to a ``<name>.header.json``.  These helpers write both with the
repository's own writers, so a test exercises the real format and not a copy of it.  The texts are
invented and short: two German texts, one of them with two sentences and one reference error, the
other with two reference errors in one sentence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path

from tools.model_eval import item_scores, quality, results
from tools.model_eval.error_labels import ReferenceLabel
from tools.model_eval.performance import Performance
from tools.model_eval.results import ResultRow, TestRecord

MODEL = "gemma4:12b"
OTHER_MODEL = "qwen3.5:9b"
TESTSET = "set-2026-12"
DIGEST = "6114515d63c17436a7c0417d82820ac65ad643e2806c5a3c89cb62846436ed0b"
OTHER_DIGEST = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
PROMPT = "4e3cd8e9d67d13c01c92486a9f2804d70c032806060df90a2be8e67191921c48"
EXPLANATION = "Das Objekt steht im Akkusativ."

SOURCE_ONE = "Ich habe ein Hund. Er ist sehr nett."
REFERENCE_ONE = "Ich habe einen Hund. Er ist sehr nett."
SPANS_ONE = ((0, 8), (9, 17))
"""``Ich habe ein Hund.`` and ``Er ist sehr nett.``, as token spans of the source."""

SOURCE_TWO = "Gestern ich bin gegangen zur Schule."
REFERENCE_TWO = "Gestern bin ich zur Schule gegangen."


def labels_of(source: str, reference: str, codes: Sequence[str]) -> tuple[ReferenceLabel, ...]:
    """Return the corpus labels of the reference changes, one code per change in text order."""
    return tuple(
        ReferenceLabel(start=start, end=end, replacement=replacement, code=code)
        for (start, end, replacement), code in zip(
            quality.ordered_edits(source, reference), codes, strict=True
        )
    )


def record(  # noqa: PLR0913  # one keyword per part of a record a test may vary
    *,
    item: str = "de-01",
    language: str = "de",
    source: str = SOURCE_ONE,
    reference: str = REFERENCE_ONE,
    codes: Sequence[str] = ("R:DET:FORM",),
    spans: Sequence[tuple[int, int]] = SPANS_ONE,
    unchanged: Sequence[int] = (1,),
    band: str = "short",
    kind: str = "errors",
) -> TestRecord:
    """Return one test record, its labels taken from the reference changes."""
    return TestRecord(
        id=item,
        language=language,
        kind=kind,
        origin="invented",
        source=source,
        reference=reference,
        labels=labels_of(source, reference, codes) if kind == "errors" else (),
        band=band,
        sentence_spans=tuple(tuple(span) for span in spans),
        unchanged_sentences=tuple(unchanged),
    )


def records() -> tuple[TestRecord, ...]:
    """Return the two texts of the fixture test set."""
    return (
        record(),
        record(
            item="de-02",
            source=SOURCE_TWO,
            reference=REFERENCE_TWO,
            codes=("R:VERB:FORM", "R:WO", "R:WO"),
            spans=((0, 12),),
            unchanged=(),
            band="page",
        ),
    )


def write_testset(directory: Path, texts: Sequence[TestRecord] | None = None) -> Path:
    """Write the fixture test set to *directory* and return it."""
    directory.mkdir(parents=True, exist_ok=True)
    by_language: dict[str, list[TestRecord]] = {}
    for text in texts if texts is not None else records():
        by_language.setdefault(text.language, []).append(text)
    for language, part in by_language.items():
        results.write_records(directory / f"{language}.jsonl", part)
    return directory


def header(  # noqa: PLR0913  # one keyword per fact a test may vary
    *,
    model: str = MODEL,
    repeat: int = 1,
    mode: str = "exchange",
    temperature: float = 1.0,
    digest: str = DIGEST,
    testset: str = TESTSET,
    machine: str = "rx9070xt-server",
    eval_version: int = 2,
    compact_json: bool = False,
    num_ctx: int = 8192,
    explainer: str | None = None,
    isolated: bool = True,
    prompt: str = PROMPT,
    date: str = "2026-11-14T23:22:00+00:00",
) -> dict:
    """Return the header of one run of a local cache, as a run writes it."""
    return {
        "date": date,
        "tool_revision": "ffa440f",
        "eval_version": eval_version,
        "fingerprint": "c5d66dae736fd35834af32dfa9ce3be467febb2e34eb70ba6a76da06ac9effda",
        "instructions_sha256": prompt,
        "answer_format_version": 1,
        "provider": "ollama",
        "machine": machine,
        "ollama_version": "0.40.16",
        "model": model,
        "model_info": {
            "digest": digest,
            "quantization": "Q4_K_M",
            "parameter_size": "11.9B",
            "family": "gemma4",
        },
        "mode": mode,
        "explainer": explainer,
        "temperature": temperature,
        "request_profile": {"compact_json": compact_json, "temperature": temperature},
        "num_ctx": num_ctx,
        "timeout": 900.0,
        "seed": 20261109 + repeat - 1,
        "repeat": repeat,
        "isolated": isolated,
        "testset": testset,
        "testset_sha256": results.records_sha256(records()),
        "texts": 2,
        "loaded_before": [],
        "vram_baseline_mb": 357,
    }


def performance(*, done_reason: str = "stop", answer_tokens: int = 50) -> Performance:
    """Return what one text cost, as a run stores it."""
    return Performance.from_dict(
        {
            "requests": [
                {
                    "model": MODEL,
                    "prompt_tokens": 100,
                    "answer_tokens": answer_tokens,
                    "load_s": 0.0,
                    "prompt_eval_s": 0.1,
                    "generation_s": 1.0,
                    "total_s": 1.1,
                    "wall_s": 1.2,
                    "done_reason": done_reason,
                    "failed": False,
                }
            ],
            "loaded": [],
            "shared": False,
            "vram_peak_mb": 9773,
            "vram_baseline_mb": 357,
            "seed": 20261109,
            "repeat": 1,
        }
    )


def row(  # noqa: PLR0913  # one keyword per part of a row a test may vary
    text: TestRecord,
    *,
    model: str = MODEL,
    temperature: float = 1.0,
    mode: str = "exchange",
    output: str | None = None,
    explanations: Sequence[str] | None = None,
    categories: Sequence[str] | None = None,
    failed: bool = False,
    fallback: bool = False,
    seconds: float = 1.0,
    done_reason: str = "stop",
) -> ResultRow:
    """Return one result row of *text*, its changes and scores from the real functions.

    Args:
        text: The test record the row answers.
        output: The model's corrected text; the reference when ``None``.
        explanations: One explanation per change, in text order; a default when ``None``.
        categories: One category per change, in text order; ``grammar`` when ``None``.
    """
    corrected = text.reference if output is None else output
    score = results.edit_score(text.kind, text.source, corrected, text.reference)
    edits = quality.ordered_edits(text.source, corrected)
    changes = tuple(
        {
            "edit": [start, end, "".join(replacement)],
            "category": (categories or ["grammar"] * len(edits))[number],
            "explanation": (explanations or [EXPLANATION] * len(edits))[number],
        }
        for number, (start, end, replacement) in enumerate(edits)
    )
    return ResultRow(
        id=results.record_key(text),
        model=model,
        temperature=temperature,
        language=text.language,
        kind=text.kind,
        origin=text.origin,
        seconds=seconds,
        failed=failed,
        fallback=fallback,
        has_review=mode != "text",
        source=text.source,
        output=corrected,
        reference=text.reference,
        changes=changes,
        explained=len(changes),
        needed=len(changes),
        true_positives=score.true_positives,
        false_positives=score.false_positives,
        false_negatives=score.false_negatives,
        band=text.band,
        scores=(
            item_scores.item_scores(
                text.source,
                corrected,
                text.reference,
                sentence_spans=text.sentence_spans,
                unchanged_sentences=text.unchanged_sentences,
            )
            if text.kind == "errors"
            else {}
        ),
        mode=results.parse_mode(mode),
        performance=performance(done_reason=done_reason),
    )


def write_cache(
    directory: Path,
    name: str,
    run_header: Mapping,
    rows: Sequence[ResultRow],
    *,
    headers: Sequence[Mapping] | None = None,
) -> Path:
    """Write one run of a cache: its rows and its header file, and return the rows' path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.jsonl"
    results.write_rows(path, rows)
    stored = list(headers) if headers is not None else [run_header]
    path.with_suffix(".header.json").write_text(
        json.dumps(stored, ensure_ascii=False), encoding="utf-8"
    )
    return path


def cache(
    directory: Path,
    *,
    model: str = MODEL,
    mode: str = "exchange",
    temperature: float = 1.0,
    repeats: int = 1,
    unchanged: bool = False,
    explainer: str | None = None,
    compact_json: bool = False,
) -> Path:
    """Write a whole cache of one model and mode, every text answered with the reference.

    With *unchanged*, the model corrects nothing, so the cache is clearly worse than one that
    answers with the reference.
    """
    texts = records()
    for repeat in range(1, repeats + 1):
        write_cache(
            directory,
            f"{model.replace(':', '_')}__T{temperature:g}__{mode}__r{repeat}",
            header(
                model=model,
                mode=mode,
                repeat=repeat,
                temperature=temperature,
                explainer=explainer,
                compact_json=compact_json,
            ),
            [
                row(
                    text,
                    model=model,
                    mode=mode,
                    temperature=temperature,
                    output=text.source if unchanged else None,
                )
                for text in texts
            ],
        )
    return directory


__all__ = [
    "DIGEST",
    "EXPLANATION",
    "MODEL",
    "OTHER_DIGEST",
    "OTHER_MODEL",
    "PROMPT",
    "REFERENCE_ONE",
    "REFERENCE_TWO",
    "SOURCE_ONE",
    "SOURCE_TWO",
    "SPANS_ONE",
    "TESTSET",
    "cache",
    "header",
    "labels_of",
    "performance",
    "record",
    "records",
    "row",
    "write_cache",
    "write_testset",
]
