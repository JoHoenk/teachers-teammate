"""Exchange T: correct the text, explain the edits, find the unclear wording.

Three requests, each depending only on the one before it::

    source ─shield─► ① correct ─restore─► stored corrected text
                                            │ word_diff, edits, ids, optional commas
                         ┌──────────────────┴──────────────────┐
                         ▼                                     ▼
                  ② explain (batches)                   ③ unclear (switch)

The edits are always the diff of the student's text against the corrected text: the model
explains edits, it never places them.  So a note can never point at words the correction did not
change, and a teacher who edits the text afterwards still sees the notes that still apply.

What a failure means:

* ① cut off after its tries, a transport error, framing twice, or much more of the text twice —
  the file **fails** and the source is kept.  A half-corrected text is never stored.
* ① answering twice with much less of the text — the stretches it left out are **put back** from the
  student's text, the answer is kept everywhere else, and the file is stored with a warning and one
  item per stretch.  The text is whole, so it is never a half-corrected one; the review is made
  from the repaired text.
* ② a batch that fails leaves its edits unexplained and warns; it never fails the file.
* ③ no answer leaves the unclear list empty and warns; it never fails the file.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
import logging

from ....domain.correction_policy import DEFAULT_LEVEL
from ....domain.correction_prompts import (
    EXPLAIN_SYSTEM,
    UNCLEAR_SYSTEM,
    correct_system,
    correct_user,
    explain_body,
    explain_user,
    unclear_user,
)
from ....domain.formatted_text import TextFormat
from ....domain.model_catalog import RequestProfile
from ....domain.proofreading.edits import (
    Batch,
    Edit,
    NoteAnswer,
    batches,
    edits_from_hunks,
    framing_problem,
    merge_notes,
    optional_comma,
    sentence_groups,
)
from ....domain.proofreading.markdown_shield import ShieldMap, restore, shield
from ....domain.proofreading.quotes import find_spans, map_back
from ....domain.proofreading.result import (
    DEFAULT_EXCHANGE_COST,
    ExchangeCost,
    ExchangeKind,
    StepName,
    StepReport,
)
from ....domain.proofreading.review import CorrectionReview, UnclearNote, edit_note
from ....domain.proofreading.sentences import sentence_spans
from ....domain.proofreading.text_loss import LeftOut, Repaired, measure, repair
from ....domain.proofreading.word_diff import Hunk, word_diff
from ...ocr_formats import (
    MarkdownRenderer,
    clean_llm_answer,
    normalize_llm_answer,
    parse_stored_text,
    plain_text_of,
)
from ..port import ModelPort, StepRequest
from ..runner import StepRunner
from ..schemas import (
    UnclearAnswer,
    explain_notes,
    explain_schema,
    parse_explain,
    parse_unclear,
)
from .base import ExchangeResult, ProofreadContext

_logger = logging.getLogger(__name__)

CORRECT_FAILED = "Proofreading failed; keeping original text."
"""Warning of a file whose step ① did not produce a usable answer."""

FRAMING_WARNING = "The model answered with a remark around the text; keeping original text."
"""Warning of a file whose step ① twice answered with a lead-in or a closing remark."""

LOST_TEXT_WARNING = "The model dropped part of the text; keeping original text."
"""Warning of a file whose step ① twice answered with much less of the text than it was sent."""

ADDED_TEXT_WARNING = "The model added text of its own; keeping original text."
"""Warning of a file whose step ① twice answered with much more text than it was sent."""

LEFT_OUT_WARNING = (
    "The AI left out {count} passages ({words} words); they are kept as you wrote them."
)
"""Warning of a file whose step ① twice answered without a stretch of the text, which is put back."""

LEFT_OUT_WARNING_ONE = "The AI left out a passage ({words} words); it is kept as you wrote it."
"""The same warning for one stretch."""

LEFT_OUT_WHY = (
    "The AI left this passage out of its answer. It is kept here as you wrote it, without "
    "corrections: read it with the rest."
)
"""What the item of a stretch that was put back says."""

EXPLAIN_FAILED = "Explanations are missing for part of the text."
"""Warning of a file whose step ② left edits unexplained."""

UNCLEAR_FAILED = "Unclear wording could not be checked for this text."
"""Warning of a file whose step ③ did not answer."""

UNSTYLED_WARNING = "Part of the text lost its formatting."
"""Warning of a Markdown text whose answer could not be restored everywhere."""

ANSWER_FACTOR = 1.0
"""How long a step's answer is compared with its request: as long as the text.

Step ① rewrites the text, so its answer is about the text's length.  Steps ② and ③ answer a
short JSON object, but the same allowance is kept: it is the cautious figure, and it gives every
step the longer timeout of a request with a long answer.
"""


EXPLAIN_EDITS_CHARS = 2_000
"""Characters one explain request carries besides the sentences: the numbered list of its edits.

A batch holds at most ``RequestProfile.explain_batch`` edits (20 by default), each listed as its
number and ``"old" → "new"``; the figure covers a batch of long edits.
"""


class TExchange:
    """The three-step exchange: ① correct, ② explain, ③ unclear."""

    kind: ExchangeKind = "T"

    def __init__(
        self,
        port: ModelPort,
        *,
        cut_off_tries: int = 1,
        clause_check: Callable[[str, str], bool] | None = None,
    ) -> None:
        self._port = port
        self._runner = StepRunner(port, cut_off_tries=cut_off_tries)
        self._clause_check = clause_check

    # ── the exchange ──────────────────────────────────────────────────────────

    def proofread(self, source: str, ctx: ProofreadContext) -> ExchangeResult:
        """Return the corrected text and its review for *source*."""
        reports: list[StepReport] = []
        warnings: list[str] = []
        request_text, shield_map, document = _shielded(source, ctx.text_format)
        answer, failure, left_out = self._correct(request_text, ctx, reports)
        if answer is None:
            return ExchangeResult(
                text=source, failed=True, warnings=(failure,), reports=tuple(reports)
            )
        corrected, unstyled = _restored(source, ctx.text_format, shield_map, document, answer)
        if unstyled:
            warnings.append(UNSTYLED_WARNING)
        if left_out:
            warnings.append(_left_out_warning(left_out))
        if ctx.corrections_only:
            # The teacher asked for the corrected text alone: steps ② and ③ are not sent, so
            # there is no review and nothing to explain.
            return ExchangeResult(
                text=corrected,
                review=None,
                warnings=tuple(warnings),
                reports=tuple(reports),
                left_out=len(left_out),
            )

        raw_plain = plain_text_of(source, ctx.text_format)
        corrected_plain = plain_text_of(corrected, ctx.text_format)
        hunks = word_diff(raw_plain, corrected_plain)
        spans = sentence_spans(raw_plain)
        found = edits_from_hunks(hunks, raw_plain, spans)

        notes, explain_warnings = self._explain(
            found, raw_plain, corrected_plain, spans, hunks, ctx, reports
        )
        warnings += explain_warnings
        unclear, unclear_warnings = self._unclear(corrected_plain, hunks, ctx, reports)
        warnings += unclear_warnings
        unclear = _dedupe([*_left_out_notes(left_out, raw_plain), *unclear])

        review = CorrectionReview(
            edits=tuple(
                edit_note(
                    edit,
                    category=notes[edit.id].category if edit.id in notes else "other",
                    explanation=notes[edit.id].explanation if edit.id in notes else "",
                    optional=optional_comma(edit, raw_plain, self._clause_check),
                )
                for edit in found
            ),
            unclear=unclear,
        )
        return ExchangeResult(
            text=corrected,
            review=review,
            warnings=tuple(warnings),
            reports=tuple(reports),
            left_out=len(left_out),
        )

    def max_text_chars(self, ctx: ProofreadContext) -> int | None:
        """Return how many characters of text one request can carry, ``None`` for no limit.

        The smallest limit of the steps that run, each computed from its own request so the
        figure cannot drift from what is sent: the system prompt and the fixed part of the user
        message are overhead, the answer takes as much room as the text (:data:`ANSWER_FACTOR`),
        and step ② carries the text twice (every sentence before and after) next to the edit
        list.  A text this long passes every request check of the exchange.  With corrections
        only, step ① is the only request, so its own limit is the one that counts.
        """
        budget = self._port.window()
        if budget is None:
            return None
        correct = self._correct_request("", ctx)
        limits = [
            budget.max_text_chars(
                len(correct.system) + len(correct.user), answer_factor=correct.answer_factor
            ),
        ]
        if ctx.corrections_only:
            return min(limits)
        limits.append(
            budget.max_text_chars(
                len(EXPLAIN_SYSTEM) + len(self._explain_head(ctx)) + EXPLAIN_EDITS_CHARS,
                answer_factor=ANSWER_FACTOR,
                text_copies=2,
            )
        )
        if ctx.profile.unclear_check:
            unclear = self._unclear_request("", ctx)
            limits.append(
                budget.max_text_chars(
                    len(unclear.system) + len(unclear.user), answer_factor=unclear.answer_factor
                )
            )
        return min(limits)

    def prompts(
        self, profile: RequestProfile, *, corrections_only: bool = False
    ) -> tuple[tuple[StepName, str], ...]:
        """Return the system prompts of the steps that run for *profile*, in step order."""
        prompts: list[tuple[StepName, str]] = [("correct", correct_system(profile.correct.prompt))]
        if corrections_only:
            return tuple(prompts)
        prompts.append(("explain", EXPLAIN_SYSTEM))
        if profile.unclear_check:
            prompts.append(("unclear", UNCLEAR_SYSTEM))
        return tuple(prompts)

    def cost_model(self) -> ExchangeCost:
        """Return what this exchange costs per page and per 100 words."""
        return DEFAULT_EXCHANGE_COST

    # ── step ① ────────────────────────────────────────────────────────────────

    def _correct(
        self, request_text: str, ctx: ProofreadContext, reports: list[StepReport]
    ) -> tuple[str | None, str, tuple[LeftOut, ...]]:
        """Return the corrected text, the stretches put back, and why not when there is no text.

        An answer that is not the text is asked for once more: a lead-in or a closing remark
        around it, or much less or much more of the text than was sent.  Twice is a failure for a
        remark or added text, because the file must not be stored with a remark in it.  Twice
        with much less of the text is not: the stretches the answer left out are put back from the
        student's text (:func:`~teachers_teammate.domain.proofreading.text_loss.repair`), and the
        file is stored with a warning, so a model that drops a paragraph still gives the teacher
        its corrections of the rest.
        """
        request = self._correct_request(request_text, ctx)
        outcome = self._runner.run(request, _plain_answer)
        reports.extend(outcome.reports)
        if outcome.value is None:
            return None, outcome.error or CORRECT_FAILED, ()
        if not outcome.value.strip():
            return None, CORRECT_FAILED, ()
        problem = self._judge(request_text, outcome.value, reports, attempt=1)
        if problem is None:
            return outcome.value, "", ()
        retry = self._runner.run(request, _plain_answer)
        reports.extend(retry.reports)
        if retry.value is None:
            return None, retry.error or CORRECT_FAILED, ()
        if not retry.value.strip():
            return None, CORRECT_FAILED, ()
        problem = self._judge(request_text, retry.value, reports, attempt=2)
        if problem is None:
            return retry.value, "", ()
        repaired = _repaired(request_text, retry.value) if problem.lost_only else None
        if repaired is None:
            return None, problem.warning, ()
        return repaired.text, "", repaired.left_out

    def _judge(
        self, request_text: str, answer: str, reports: list[StepReport], *, attempt: int
    ) -> _Rejection | None:
        """Return why *answer* must not be stored, or ``None``; a rejection is made visible.

        What the detector measured goes into the counters of the answer's report, and into the
        log, so a file that fails with "the model dropped part of the text" says by how much and
        by which rule.  The rejected answer itself is written to the log only at debug level: it
        is the student's text.
        """
        rejection = _answer_problem(request_text, answer)
        if rejection is None:
            return None
        counters = dict(rejection.counters)
        window = self._port.window()
        if window is not None:
            counters["num_ctx"] = window.num_ctx
        if reports:
            reports[-1] = replace(reports[-1], counters={**reports[-1].counters, **counters})
        _logger.warning(
            "Step ① answer %d was rejected (%s): %s",
            attempt,
            rejection.warning,
            ", ".join(f"{name}={value}" for name, value in counters.items()),
        )
        _logger.debug("The rejected answer %d of step ①:\n%s", attempt, answer)
        return rejection

    def _correct_request(self, text: str, ctx: ProofreadContext) -> StepRequest:
        """Return the request of step ① for *text*."""
        return StepRequest(
            step="correct",
            system=correct_system(ctx.profile.correct.prompt),
            user=correct_user(
                text,
                language=ctx.language,
                level=ctx.level or DEFAULT_LEVEL,
                notes=ctx.teacher_notes,
            ),
            temperature=ctx.profile.correct.temperature,
            options=ctx.profile.correct.options,
            think=ctx.profile.correct.think,
            answer_factor=ANSWER_FACTOR,
        )

    # ── step ② ────────────────────────────────────────────────────────────────

    def _explain(
        self,
        found: Sequence[Edit],
        source: str,
        corrected: str,
        spans: tuple[tuple[int, int], ...],
        hunks: Sequence[Hunk],
        ctx: ProofreadContext,
        reports: list[StepReport],
    ) -> tuple[dict[int, NoteAnswer], list[str]]:
        """Return the note of every edit, and the warnings of the batches that failed."""
        if not found:
            return {}, []
        groups = sentence_groups(spans, found)
        notes: dict[int, NoteAnswer] = {}
        warnings: list[str] = []
        for batch in batches(found, groups, ctx.profile.explain_batch):
            answered, warning = self._explain_batch(
                batch, source, corrected, spans, hunks, ctx, reports
            )
            notes.update(answered)
            if warning:
                warnings.append(warning)
        return notes, warnings

    def _explain_batch(
        self,
        batch: Batch,
        source: str,
        corrected: str,
        spans: tuple[tuple[int, int], ...],
        hunks: Sequence[Hunk],
        ctx: ProofreadContext,
        reports: list[StepReport],
    ) -> tuple[dict[int, NoteAnswer], str]:
        """Return the notes of one batch, asking once more for the ids it left out."""
        ids = [edit.id for edit in batch.edits]
        answered, missing, failed = self._ask_explain(
            batch, ids, source, corrected, spans, hunks, ctx, reports
        )
        if failed:
            return answered, EXPLAIN_FAILED
        if not missing:
            return answered, ""
        follow = replace(batch, edits=tuple(edit for edit in batch.edits if edit.id in missing))
        more, still, failed = self._ask_explain(
            follow, sorted(missing), source, corrected, spans, hunks, ctx, reports
        )
        answered.update(more)
        return answered, EXPLAIN_FAILED if failed or still else ""

    def _ask_explain(
        self,
        batch: Batch,
        ids: Sequence[int],
        source: str,
        corrected: str,
        spans: tuple[tuple[int, int], ...],
        hunks: Sequence[Hunk],
        ctx: ProofreadContext,
        reports: list[StepReport],
    ) -> tuple[dict[int, NoteAnswer], set[int], bool]:
        """Send one explain request; return its notes, the ids it left out, and whether it failed."""
        request = StepRequest(
            step="explain",
            system=EXPLAIN_SYSTEM,
            user=self._explain_head(ctx, explain_body(source, corrected, spans, batch, hunks)),
            schema=explain_schema(ids),
            temperature=ctx.profile.explain.temperature,
            options=ctx.profile.explain.options,
            think=ctx.profile.explain.think,
            answer_factor=ANSWER_FACTOR,
        )
        outcome = self._runner.run(request, parse_explain)
        reports.extend(outcome.reports)
        if outcome.value is None:
            return {}, set(), True
        merged = merge_notes(explain_notes(outcome.value), ids)
        return {note.id: note for note in merged.notes}, set(merged.missing), False

    @staticmethod
    def _explain_head(ctx: ProofreadContext, body: str = "") -> str:
        """Return the user message of step ② for *body* (the fixed part when it is empty)."""
        return explain_user(
            body,
            language=ctx.language,
            level=ctx.level or DEFAULT_LEVEL,
            explanation_language=ctx.explanation_language,
            notes=ctx.teacher_notes,
        )

    # ── step ③ ────────────────────────────────────────────────────────────────

    def _unclear(
        self,
        corrected_plain: str,
        hunks: Sequence[Hunk],
        ctx: ProofreadContext,
        reports: list[StepReport],
    ) -> tuple[tuple[UnclearNote, ...], list[str]]:
        """Return the unclear expressions of the corrected text, anchored on the student's text.

        The model quotes the corrected text; the quote is found there and mapped back through the
        diff.  A quote that touches an edit is dropped: the edit already says what is wrong
        there, and two notes on one span would contradict each other.
        """
        if not ctx.profile.unclear_check:
            return (), []
        request = self._unclear_request(corrected_plain, ctx)
        outcome = self._runner.run(request, parse_unclear)
        reports.extend(outcome.reports)
        if outcome.value is None:
            return (), [UNCLEAR_FAILED]
        items = outcome.value.unclear
        spans = find_spans(corrected_plain, [item.quote for item in items])
        notes: list[UnclearNote] = []
        for item, span in zip(items, spans, strict=True):
            if span is None:
                continue
            raw = map_back(span, hunks)
            if raw is None:
                continue
            notes.append(
                UnclearNote(
                    raw_start=raw[0],
                    raw_end=raw[1],
                    expression=item.quote,
                    why=item.why,
                    recommendation=item.suggestion,
                    is_suggestion=not item.determinable,
                )
            )
        return _dedupe(notes), []

    @staticmethod
    def _unclear_request(text: str, ctx: ProofreadContext) -> StepRequest:
        """Return the request of step ③ for the corrected *text*."""
        return StepRequest(
            step="unclear",
            system=UNCLEAR_SYSTEM,
            user=unclear_user(
                text,
                language=ctx.language,
                level=ctx.level or DEFAULT_LEVEL,
                explanation_language=ctx.explanation_language,
                notes=ctx.teacher_notes,
            ),
            schema=UnclearAnswer,
            temperature=ctx.profile.unclear.temperature,
            options=ctx.profile.unclear.options,
            think=ctx.profile.unclear.think,
            answer_factor=ANSWER_FACTOR,
        )


def _plain_answer(text: str) -> tuple[str, str]:
    """Return a plain-text answer as it is: step ① has no schema to validate."""
    return text, ""


@dataclass(frozen=True)
class _Rejection:
    """Why an answer of step ① is not the text, and what the detector measured."""

    warning: str
    """The sentence the file fails with."""
    counters: dict[str, int]
    """``words_in``, ``words_out``, ``cut_run`` and one flag per rule that fired."""
    lost_only: bool = False
    """``True`` when the answer left text out and wrote nothing of its own: it can be repaired."""


def _answer_problem(request_text: str, answer: str) -> _Rejection | None:
    """Return why an answer must not be stored, ``None`` for a usable one."""
    hunks = word_diff(request_text.strip(), answer.strip())
    if framing_problem(request_text, answer, hunks) is not None:
        return _Rejection(FRAMING_WARNING, {"rule_framing": 1})
    measured = measure(request_text, answer, hunks)
    if not measured.problem:
        return None
    counters = {
        "words_in": measured.words_in,
        "words_out": measured.words_out,
        "cut_run": measured.cut_run,
        "rule_ratio_lost": int(measured.ratio_lost),
        "rule_run_lost": int(measured.run_lost),
        "rule_added": int(measured.added),
    }
    warning = LOST_TEXT_WARNING if measured.lost else ADDED_TEXT_WARNING
    return _Rejection(warning, counters, lost_only=measured.lost and not measured.added)


def _repaired(request_text: str, answer: str) -> Repaired | None:
    """Return *answer* with the stretches it left out put back, or ``None`` when that does not help.

    The repaired text is judged like any answer: if it is still not the text (the answer cut
    many short stretches, or wrote text of its own besides), the file fails as it did before.
    """
    repaired = repair(request_text, answer)
    if repaired is None or _answer_problem(request_text, repaired.text) is not None:
        return None
    return repaired


def _left_out_warning(left_out: Sequence[LeftOut]) -> str:
    """Return the warning for the stretches the answer left out, and the words they hold."""
    words = sum(part.words for part in left_out)
    if len(left_out) == 1:
        return LEFT_OUT_WARNING_ONE.format(words=words)
    return LEFT_OUT_WARNING.format(count=len(left_out), words=words)


def _left_out_notes(left_out: Sequence[LeftOut], raw_plain: str) -> list[UnclearNote]:
    """Return one note per stretch put back, placed on the student's text.

    The stretch is looked up in *raw_plain* rather than carried over by offset: the text that was
    sent may differ from it (a Markdown text is sent without its marks), and a note on the wrong
    words is worse than none.  A stretch that cannot be found keeps no note; the warning still
    counts it.
    """
    spans = find_spans(raw_plain, [part.text for part in left_out], ordered=True)
    return [
        UnclearNote(
            raw_start=span[0],
            raw_end=span[1],
            expression=raw_plain[span[0] : span[1]],
            why=LEFT_OUT_WHY,
            recommendation="",
            left_out=True,
        )
        for span in spans
        if span is not None
    ]


def _shielded(source: str, text_format: TextFormat) -> tuple[str, ShieldMap | None, object | None]:
    """Return the text the request carries, and what restores the notation afterwards.

    A plain text goes out as it is.  A Markdown text is parsed, its marks are stripped and its
    crossed-out words are held back, because every Markdown failure of the proof of concept was
    the notation and not the correction.
    """
    if text_format != "markdown":
        return source, None, None
    document = parse_stored_text(source, text_format)
    shield_map = shield(document)
    return shield_map.text, shield_map, document


def _restored(
    source: str,
    text_format: TextFormat,
    shield_map: ShieldMap | None,
    document: object | None,
    answer: str,
) -> tuple[str, int]:
    """Return the stored corrected text, and how many regions lost their formatting."""
    if shield_map is None or document is None:
        return normalize_llm_answer(answer, text_format, source=source), 0
    restored = restore(document, shield_map, clean_llm_answer(answer))  # ty: ignore[invalid-argument-type]
    return MarkdownRenderer().render(restored.document), restored.unstyled


def _dedupe(notes: Sequence[UnclearNote]) -> tuple[UnclearNote, ...]:
    """Return *notes* without the ones that repeat or overlap an earlier one.

    The model may report the same expression twice, or two expressions that share words; the
    first one is kept, because it is the one the model noticed first.
    """
    kept: list[UnclearNote] = []
    for note in notes:
        if any(_overlaps(note, other) for other in kept):
            continue
        kept.append(note)
    return tuple(kept)


def _overlaps(first: UnclearNote, second: UnclearNote) -> bool:
    """Whether two unclear spans share a character."""
    return max(first.raw_start, second.raw_start) < min(first.raw_end, second.raw_end)
