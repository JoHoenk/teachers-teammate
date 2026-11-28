"""The path a run walks: a scripted corrector writes a run file, the report scores it, the check
passes it.

The layers are tested alone elsewhere; this file walks the path once, end to end, so a change that
breaks the seam between two layers fails here with the step that broke.  Nothing here reads the real
store or sends a request: the corrector is scripted, the server is a fake, and the store is built in
``tmp_path``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote
from teachers_teammate.interfaces import Corrector

from tools.evaluation.core.hashing import sha256_text
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.core.modes import ALL_STEPS, Exchange
from tools.evaluation.core.performance import LoadedModel, RequestStats
from tools.evaluation.core.records import TestRecord, key_sha256, question_sha256
from teachers_teammate.domain.proofreading.word_diff import text_offsets
from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits
from tools.evaluation.report import compare, gates, groups, load
from tools.evaluation.run import protocol, runner
from tools.evaluation.store import validate, writer

MODEL = "gemma4:12b"
MACHINE = "rx9070xt-server"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"
DATE = "2026-11-14"
TESTSET = "gec-2026-12"

DE_SOURCE = "Der Hund laufen schnell. Das Wetter ist schön."
DE_REFERENCE = "Der Hund läuft schnell. Das Wetter ist schön."
EN_SOURCE = "He go to school every day. The weather is nice."
EN_REFERENCE = "He goes to school every day. The weather is nice."


# ── the test set ──────────────────────────────────────────────────────────────


def _spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return the character span of every sentence of *text*, the full stop included."""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == ".":
            spans.append((start, index + 1))
            start = index + 2
    return tuple(spans)


def _label(source: str, reference: str, code: str) -> ReferenceLabel:
    """Return the first edit of *reference* against *source* as a label, in characters."""
    offsets = text_offsets(source)
    start, end, replacement = ordered_edits(source, reference)[0]
    return ReferenceLabel(
        start=offsets[start],
        end=offsets[end],
        replacement="".join(replacement),
        code=code,
    )


def _record(identifier: str, language: str, source: str, reference: str, code: str) -> TestRecord:
    """Return one ``short`` test record of *language* whose one error is the first edit.

    The second sentence is the one the reference leaves alone, so the gate's ``left alone`` floor
    has something to measure.
    """
    return TestRecord(
        id=identifier,
        language=language,
        source=source,
        kind="errors",
        reference=reference,
        labels=(_label(source, reference, code),),
        sentence_spans=_spans(source),
        unchanged_sentences=(1,),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )


def _records() -> tuple[TestRecord, ...]:
    """Return one German and one English text, each with one error."""
    return (
        _record("de-01", "de", DE_SOURCE, DE_REFERENCE, "R:VERB:FORM"),
        _record("en-01", "en", EN_SOURCE, EN_REFERENCE, "R:VERB:FORM"),
    )


def _manifest(records: tuple[TestRecord, ...]) -> Manifest:
    """Return a manifest that pins *records*, as the store writes it."""
    return Manifest(
        testset=TESTSET,
        built=f"{DATE}T23:22:00+00:00",
        selection_seed=20261109,
        builder_revision="ffa440f",
        question_sha256=question_sha256(list(records)),
        key_sha256=key_sha256(list(records)),
        items=tuple(
            ManifestItem(
                id=record.id,
                language=record.language,
                kind=record.kind,
                corpus=record.corpus,
                band=record.band,
                pointer={},
                source={
                    "sha256": sha256_text(record.source),
                    "words": len(record.source.split()),
                    "chars": len(record.source),
                },
                reference={"sha256": sha256_text(record.reference)},
            )
            for record in records
        ),
    )


# ── the scripted run ──────────────────────────────────────────────────────────


class _Probe:
    """A server that answers without a server."""

    def version(self) -> str:
        """Return the version of the invented server."""
        return "0.40.16"

    def model_info(self, tag: str) -> dict:
        """Return the digest and details of the invented model."""
        return {
            "digest": DIGEST,
            "quantization": "Q4_K_M",
            "parameter_size": "12B",
            "family": "gemma3",
        }

    def loaded(self) -> tuple[LoadedModel, ...]:
        """Return nothing loaded: the invented server is always idle."""
        return ()

    def unload(self, name: str) -> None:
        """Drop the model from memory; the invented server holds nothing."""

    def unload_all(self, *, wait_s: float = 60.0, sleep: Callable[[float], None] = lambda _s: None):
        """Unload everything; the invented server is idle already."""


class _Client:
    """A recording client without a server: it keeps the requests the corrector sends."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.requests: list[RequestStats] = []

    def drain(self) -> tuple[RequestStats, ...]:
        """Return the requests recorded since the last call and forget them."""
        taken, self.requests = tuple(self.requests), []
        return taken


def _review(source: str, reference: str) -> CorrectionReview:
    """Return the review of one text: its one edit, explained."""
    offsets = text_offsets(source)
    start, end, replacement = ordered_edits(source, reference)[0]
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(
                        raw_start=offsets[start],
                        raw_end=offsets[end],
                        original=source[offsets[start] : offsets[end]],
                        corrected="".join(replacement),
                    ),
                ),
                sentences=(0,),
                category="R:VERB:FORM",
                explanation="The verb agrees with the subject.",
            ),
        )
    )


class _Scripted(Corrector):
    """A corrector that answers with the reference of the text it was given, explained."""

    def __init__(self, client: _Client, references: dict[str, str]) -> None:
        self._client = client
        self._references = references

    def correct(self, raw_text, language, *, text_format="plain") -> CorrectionOutcome:
        """Note the request, then answer with the reference of *raw_text*.

        The warm-up is not one of the texts, so it is answered with itself and not scored.
        """
        self._client.requests.append(RequestStats.failure(MODEL, 0.1))
        reference = self._references.get(raw_text)
        if reference is None:
            return CorrectionOutcome(text=raw_text)
        return CorrectionOutcome(text=reference, review=_review(raw_text, reference))


class _Harness:
    """The seams of one run, with the server, the client and the corrector replaced."""

    def __init__(self, references: dict[str, str]) -> None:
        self._references = references
        self.probe = _Probe()

    def new_client(self, seed: int) -> _Client:
        """Return a recording client for *seed*."""
        return _Client(seed)

    def new_factory(self, client, exchange, steps, profile) -> Callable[[str, float], Corrector]:
        """Return a factory whose correctors answer with the reference of each text."""

        def build(model: str, temperature: float) -> Corrector:
            return _Scripted(client, self._references)

        return build

    def seams(self) -> runner.Seams:
        """Return the seams of this run."""
        return runner.Seams(
            probe=self.probe,
            new_client=self.new_client,
            new_factory=self.new_factory,
            new_sampler=lambda: None,
            now=lambda: f"{DATE}T18:00:00+00:00",
            revision="ffa440f",
        )


def _matrix(root: Path) -> runner.Matrix:
    """Return the matrix of one model at two temperatures, three repeats, one setup."""
    return runner.Matrix(
        root=root,
        testset=TESTSET,
        languages=("de", "en"),
        models=(MODEL,),
        temperatures=(0.0, 0.7),
        setups=((Exchange.ONE_REQUEST_REVIEW, ALL_STEPS),),
        repeats=3,
        base_seed=20261109,
        timeout=5.0,
        isolate=False,
        gpu_sampler=None,
        machine=MACHINE,
    )


def _store(root: Path, records: tuple[TestRecord, ...]) -> Path:
    """Write *records* into a store at *root* and return the root."""
    writer.write_testset(root, TESTSET, records, _manifest(records))
    return root


# ── the path ──────────────────────────────────────────────────────────────────


def test_a_scripted_run_becomes_a_report_and_a_checked_store(tmp_path: Path):
    """
    Given  a store of two texts and a scripted corrector that answers with each reference
    When   the run writes its files, the report scores them, and the store is checked
    Then   every step reads what the one before it wrote, and every file passes the check
    """
    records = _records()
    root = _store(tmp_path / "store", records)
    references = {record.source: record.reference for record in records}

    result = runner.run_matrix(
        _matrix(root), records, protocol.current_stamp(), _Harness(references).seams()
    )
    assert result.refused == ()
    assert (result.sessions, result.rows) == (6, 12)

    loaded = load.load(root)
    chosen = groups.groups(loaded.runs)
    assert len(chosen) == 2

    comparison = compare.compare(chosen, along=compare.Along(axes=("temperature",), where={}))
    assert sorted(row.temperature for row in comparison.rows) == [0.0, 0.7]
    assert [row.f_beta for row in comparison.rows] == [1.0, 1.0]
    assert [row.delta.delta for row in comparison.rows if row.delta is not None] == [0.0]

    for group in chosen:
        judgement = gates.GateTwoRule().judge(group)
        assert judgement.outcome is gates.Outcome.PASS, [
            check.line() for check in judgement.checks if check.passed is not True
        ]

    checked = validate.check_store(root)
    assert [item.name for item in checked if not item.ok] == []
    assert len(checked) == 7, "one test set and six runs"
