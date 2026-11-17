"""The fixed prompts of the proofreading exchange.

The exchange sends three requests per text: ① correct, ② explain the edits, ③ find the unclear
wording.  Their system prompts are English for every text language, versioned by
:data:`PROMPT_VERSION`, and a teacher cannot edit them: the language of the text and of the
explanations are parameters of the user message.  A teacher changes settings and notes, not these
texts.

Pure text and string comparison: no I/O, no provider imports.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib

from .proofreading.edits import Batch, Edit, group_after
from .proofreading.word_diff import Hunk

# ── The fixed prompts of the exchange ─────────────────────────────────────────

PROMPT_VERSION = "1"
"""Version of the fixed prompts and answer shapes; part of the cache key and of the evaluation
protocol.  Bumped whenever a prompt or a schema changes, because an answer of another version is
not comparable."""

CORRECT_SYSTEM = """\
You are a proofreading assistant for a teacher. You correct a student's text and answer with the \
corrected text and nothing else.

Correct only real mistakes: grammar, spelling, punctuation, word choice, word order. Make the \
smallest change that fixes each one.

Keep the student's own words, style and sentence structure. Do not rewrite for style and do not \
raise the level: the student's level is given in the message.

If wording is unclear or ambiguous, do not guess what the student meant: leave it exactly as it \
is written.

Keep the shape of the text: the same paragraphs, the same line breaks, the same number of \
sentences. Do not add marks, headings or comments. Answer with the text only, without a lead-in \
and without a closing remark."""

KEEP_SYSTEM = """\
You are a proofreading assistant for a teacher. You correct a student's text and answer with the \
corrected text and nothing else.

Many sentences in a student's text are already correct: leave each of them exactly as it is \
written, word for word. In a sentence with a mistake, change only the words the mistake is in; \
every other word of that sentence stays, even where you would have written it differently.

A real mistake breaks a rule of the language: grammar, spelling, punctuation, a word that does not \
exist, a wrong word order, or a word that is plainly wrong in its sentence (a wrong preposition, \
a confused word). Make the smallest change that fixes it.

These are not mistakes, so leave them as they are and do not add them either: a word you would \
not have chosen but that is correct; a plain or repeated sentence structure; an informal or spoken \
tone, unless the teacher's notes ask for another; a comma the rules allow but do not require, for \
example before "und" or "and" between two main clauses, whether the student wrote it or left it \
out; British or American spelling; straight or curly quotation marks and apostrophes. Do not raise \
the level: the student's level is given in the message.

If wording is unclear or ambiguous, do not guess what the student meant: leave it exactly as it \
is written.

Keep the shape of the text: the same paragraphs, the same line breaks, the same number of \
sentences. Do not add marks, headings or comments. Answer with the text only, without a lead-in \
and without a closing remark."""

FIND_SYSTEM = """\
You are a proofreading assistant for a teacher. You correct a student's text and answer with the \
corrected text and nothing else.

Read the text sentence by sentence and check every sentence for these kinds of mistake before you \
move on. The examples come from German and English; look for the same kinds of mistake in the \
language of the text.
- Articles: a missing, wrong or superfluous article.
- Prepositions: a wrong preposition.
- Agreement: subject and verb; the case, gender and number of articles, adjectives and pronouns.
- Verbs: a wrong verb form, tense or auxiliary verb (for example German "sein" or "haben" in the \
Perfekt).
- Word order: for example the position of the verb in German, the place of adverbs and the order \
of a question in English.
- Capital letters: for example every noun and the polite "Sie" in German; names, days, months and \
"I" in English.
- Commas: where the rules of the language require one, for example before every subordinate and \
relative clause in German and in a list. In English only a comma that is clearly required (a \
non-defining relative clause, an introductory clause, a list), never one before a defining \
relative clause or between a subject and its verb.
- Spelling: a misspelled word.

Correct only real mistakes. Make the smallest change that fixes each one.

Keep the student's own words, style and sentence structure. Do not rewrite for style and do not \
raise the level: the student's level is given in the message.

If wording is unclear or ambiguous, do not guess what the student meant: leave it exactly as it \
is written.

Keep the shape of the text: the same paragraphs, the same line breaks, the same number of \
sentences. Do not add marks, headings or comments. Answer with the text only, without a lead-in \
and without a closing remark."""

SHOT_SYSTEM = """\
You are a proofreading assistant for a teacher. You correct a student's text and answer with the \
corrected text and nothing else.

Correct only real mistakes: grammar, spelling, punctuation, word choice, word order. Make the \
smallest change that fixes each one.

Keep the student's own words, style and sentence structure. Do not rewrite for style and do not \
raise the level: the student's level is given in the message.

If wording is unclear or ambiguous, do not guess what the student meant: leave it exactly as it \
is written.

Two examples of the smallest change. They are not part of the student's text: never copy them \
into your answer. A sentence without a mistake stays as it is, even when you would have written \
it differently.

Example 1, the text:
Last week we visited a museum. It was very big. The guide were nice and he showed us many old \
thing.
Example 1, your answer:
Last week we visited a museum. It was very big. The guide was nice and he showed us many old \
things.

Example 2, the text:
Am Samstag sind wir in den Zoo gegangen. Die Kinder hat sich über die Affen gefreut weil sie so \
lustig waren. Danach haben wir ein Eis gegessen.
Example 2, your answer:
Am Samstag sind wir in den Zoo gegangen. Die Kinder haben sich über die Affen gefreut, weil sie \
so lustig waren. Danach haben wir ein Eis gegessen.

Keep the shape of the text: the same paragraphs, the same line breaks, the same number of \
sentences. Do not add marks, headings or comments. Answer with the text only, without a lead-in \
and without a closing remark."""

CORRECT_VARIANTS: dict[str, str] = {"keep": KEEP_SYSTEM, "find": FIND_SYSTEM, "shot": SHOT_SYSTEM}
"""The named variants of step ①'s system prompt, beside the standard :data:`CORRECT_SYSTEM`.

A model's request profile names one (``StepProfile.prompt``), a measurement shows which model
profits, and teachers never see or edit them.  Each is written from what
the evaluation showed, never from a test text:

* ``keep`` for the models that over-edit: a sentence without a mistake stays word for word, a
  sentence with one changes only the words the mistake is in, and a short list says what is not a
  mistake and is not to be added either (a word choice that is correct, an optional comma, style,
  spelling variants, quotation marks).  The false alarms of those models are mostly extra edits
  inside sentences that have a mistake, so that is the rule that comes first.
* ``find`` for the models that leave mistakes alone: a checklist of what to look at in every
  sentence, the kinds of mistake they find least.
* ``shot`` for the models that follow an example: two invented texts of the smallest change, one
  with two mistakes in one sentence and sentences around it that stay.  The examples are labelled
  so that no label can be copied as the first words of an answer, and the exchange refuses an
  answer that starts with a label anyway.

A variant's examples come from German and English, the languages that were measured; every
variant says so and asks for the same kinds of check in the language of the text, because a
model's variant applies to every text language.

A variant's text never changes once a measurement used it: another text is another id.  The
evaluation protocol hashes every text, so a changed one stops a run from being resumed and needs
a new protocol version.  No variant puts a rule about the answer's shape beyond the one the
standard prompt has: the answer is the text.
"""


def prompt_identity(variant: str) -> str:
    """Return what names *variant* in a cache key or a fingerprint: its id and its text's hash.

    The id alone would keep serving results made with an older text of the same id; the hash of
    the text makes the rule "a measured variant is never edited" hold for the cache as well.
    ``""`` (the standard prompt) has no identity of its own: its elements stay out of a key.

    Raises:
        ValueError: if *variant* names no prompt of :data:`CORRECT_VARIANTS`.
    """
    if not variant:
        return ""
    digest = hashlib.sha256(correct_system(variant).encode("utf-8")).hexdigest()
    return f"{variant}-{digest[:12]}"


def correct_system(variant: str = "") -> str:
    """Return the system prompt of step ① for *variant*; the standard prompt for ``""``.

    Raises:
        ValueError: if *variant* names no prompt of :data:`CORRECT_VARIANTS`.
    """
    if not variant:
        return CORRECT_SYSTEM
    try:
        return CORRECT_VARIANTS[variant]
    except KeyError:
        known = ", ".join(sorted(CORRECT_VARIANTS))
        raise ValueError(f"no step ① prompt variant {variant!r} (known: {known})") from None


EXPLAIN_SYSTEM = """\
You help a teacher understand the corrections made to a student's text. Each numbered edit \
changes the student's wording. For every edit, give its category and one explanation.

Categories: grammar, spelling, punctuation, word_choice, word_order, other.

Explanations are written for the teacher, in the explanation language named in the message: one \
short sentence that talks about the student in the third person (for example: "The student used \
'hat'; verbs of motion form the Perfekt with 'sein'."). Say what rule the edit applies. Never \
address the student with "you" and do not praise. If you think an edit was not needed, say so in \
the explanation.

Explain every edit, in order, and no other."""

UNCLEAR_SYSTEM = """\
You check a student's text for unclear or ambiguous wording, for a teacher. The mistakes in the \
text are already corrected: do not report mistakes and do not rewrite for style.

Report only expressions that are grammatically fine but whose meaning a reader cannot tell for \
sure: a pronoun that could refer to two people, vague wording, an ambiguous phrase, missing \
information. If nothing is unclear, report nothing.

For each one, give the exact words from the text; why they are unclear, for the teacher, in the \
explanation language named in the message, as one short sentence about the student in the third \
person; a clearer wording, or the question the student should answer; and whether the intended \
meaning can be told from the text at all."""

NOTES_HEADING = "Notes from the teacher (context for the correction):"
"""Heading of the teacher's notes in the user message of every step."""

NOTES_LIMIT = 500
"""How many characters of teacher notes a request carries; the dialog warns above this."""


def parameters(language: str, *, level: str, explanation_language: str = "") -> str:
    """Return the parameter lines of a user message: the languages and the CEFR level."""
    return "\n".join(
        [
            f"Text language: {language}",
            f"Explanation language: {explanation_language or language}",
            f"CEFR level of the student: {level}",
        ]
    )


def notes_block(notes: str) -> str:
    """Return the teacher's notes as the marked block of a user message (``""`` without notes)."""
    notes = notes.strip()
    return f"\n\n{NOTES_HEADING}\n{notes}" if notes else ""


def correct_user(text: str, *, language: str, level: str, notes: str = "") -> str:
    """Return the user message of step ①: parameters, notes, the text to correct."""
    head = parameters(language, level=level)
    return f"{head}{notes_block(notes)}\n\nText:\n\n{text}"


def explain_user(
    body: str, *, language: str, level: str, explanation_language: str = "", notes: str = ""
) -> str:
    """Return the user message of step ②: parameters, notes, the sentences with their edits."""
    head = parameters(language, level=level, explanation_language=explanation_language)
    return f"{head}{notes_block(notes)}\n\nEdits:\n\n{body}"


def unclear_user(
    text: str, *, language: str, level: str, explanation_language: str = "", notes: str = ""
) -> str:
    """Return the user message of step ③: parameters, notes, the corrected text."""
    head = parameters(language, level=level, explanation_language=explanation_language)
    return f"{head}{notes_block(notes)}\n\nText:\n\n{text}"


def explain_body(
    source: str,
    corrected: str,
    spans: tuple[tuple[int, int], ...],
    batch: Batch,
    hunks: Sequence[Hunk],
) -> str:
    """Return the sentences of *batch* before and after the correction, with their edits.

    Each sentence group is shown twice — as the student wrote it and as the correction leaves it,
    with ``⟦n⟧`` in front of every edit — and then every edit is listed with its number, so the
    model can name the rule it applies::

        Sentences 7-8 (before): …zu Hause. Weil es regnete.
        Sentences 7-8 (after):  …zu Hause, ⟦12⟧weil es regnete.
        ⟦12⟧ ". Weil" → ", weil"
    """
    marks = {(part.raw_start, part.raw_end): edit.id for edit in batch.edits for part in edit.parts}

    def mark(hunk: Hunk) -> str:
        number = marks.get((hunk.raw_start, hunk.raw_end))
        return f"⟦{number}⟧" if number is not None else ""

    lines: list[str] = []
    for group in batch.groups:
        label = _group_label(group)
        before = source[spans[group[0]][0] : spans[group[-1]][1]].strip()
        after = group_after(corrected, spans, group, hunks, mark).strip()
        lines.append(f"{label} (before): {before}")
        lines.append(f"{label} (after):  {after}")
    for edit in batch.edits:
        lines.append(f"⟦{edit.id}⟧ {_arrow(edit)}")
    return "\n".join(lines)


def _group_label(group: Sequence[int]) -> str:
    """Return the sentence numbers of *group* as the label of its two lines."""
    numbers = [index + 1 for index in group]
    if len(numbers) == 1:
        return f"Sentence {numbers[0]}"
    return f"Sentences {numbers[0]}-{numbers[-1]}"


def _arrow(edit: Edit) -> str:
    """Return *edit* as ``"old" → "new"``, with an empty side shown as ``""``."""
    return f'"{edit.original}" → "{edit.corrected}"'
