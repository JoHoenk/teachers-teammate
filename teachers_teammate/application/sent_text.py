"""What each step that leaves this computer receives, exactly as it is sent.

Answers the only question a teacher still has after the status box: "which text does the
service see?".  Pure rules plus two callables (anonymize, the prompt strings the stages
send), so a dialog can show it and a test can prove it without spaCy or a model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..domain.data_location import (
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    DataFlow,
    DataFlowStage,
    PrivacyLevel,
)

#: Returns ``(anonymized_text, placeholders)`` for a text: one spaCy pass per text.
Anonymize = Callable[[str], tuple[str, Mapping[str, str]]]


@dataclass(frozen=True)
class SentText:
    """One step that leaves this computer: what it receives and what it is told."""

    stage: DataFlowStage
    text: str
    prompt: str = ""
    #: The placeholder tokens inside :attr:`text`, so a view can highlight them.
    placeholders: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        """The step's name as the user knows it."""
        return self.stage.title

    @property
    def badge(self) -> str:
        """Short line: where the text goes and how names are handled."""
        return self.stage.badge

    @property
    def details(self) -> str:
        """The tooltip: where the data is processed, and where the vendor is at home."""
        return self.stage.location.details

    @property
    def names_hidden(self) -> bool:
        """Whether personal data is replaced before this step sees the text."""
        return self.stage.personal_info_hidden

    @property
    def names_note(self) -> str:
        """One sentence about names, for the section under its title."""
        if self.names_hidden:
            return "Names are hidden before the text is sent."
        return "Names are sent as written."


@dataclass(frozen=True)
class SentTexts:
    """Everything that leaves this computer for one file, step by step."""

    sections: tuple[SentText, ...] = ()
    #: Outbound steps that send page images, not text (text recognition on another computer).
    #: They have no text to show, but they do leave this computer.
    page_stages: tuple[DataFlowStage, ...] = ()
    level: PrivacyLevel = PrivacyLevel.LOCAL
    #: The extracted text as written, for the original-vs-anonymized comparison.
    raw_text: str = ""
    #: The extracted text with names replaced — the dialog's second view.  Empty when
    #: personal-info removal is off.
    anonymized_raw: str = ""
    #: The placeholder tokens of :attr:`anonymized_raw`.
    preview_placeholders: tuple[str, ...] = ()

    @property
    def leaves_computer(self) -> bool:
        """Whether any enabled step sends something out (text or page images)."""
        return bool(self.sections or self.page_stages)

    @property
    def has_preview(self) -> bool:
        """Whether the original-vs-anonymized comparison has something to show."""
        return bool(self.anonymized_raw)


def describe_sent_texts(
    flow: DataFlow,
    *,
    raw_text: str,
    correction_text: str = "",
    anonymize: Anonymize | None = None,
    preview_extracted: bool = False,
    correction_prompt: str = "",
    evaluation_prompt: str = "",
) -> SentTexts:
    """Return what every outbound step of *flow* receives, exactly as it is sent.

    Proofreading receives the extracted text and content review the proofread text; each
    is anonymized first when the step says names are hidden.  *anonymize* must be given
    for such a step — without it the text is reported as written, because hiding it is
    impossible.  Every text is anonymized at most once, so a proofreader, a content review
    and the comparison view share one spaCy pass per text.

    *preview_extracted* asks for the anonymized extracted text even when no step needs it,
    for the dialog's original-vs-anonymized comparison.
    """
    corpus = {STAGE_CORRECTION: raw_text, STAGE_EVALUATION: correction_text}
    prompts = {STAGE_CORRECTION: correction_prompt, STAGE_EVALUATION: evaluation_prompt}
    hidden_texts: dict[str, tuple[str, Mapping[str, str]]] = {}

    def hidden(text: str) -> tuple[str, Mapping[str, str]]:
        """Anonymize *text* once, and reuse the result for the rest of this report."""
        if text not in hidden_texts:
            hidden_texts[text] = anonymize(text) if anonymize is not None else (text, {})
        return hidden_texts[text]

    sections: list[SentText] = []
    page_stages: list[DataFlowStage] = []
    for stage in flow.outbound:
        if stage.stage not in corpus:
            page_stages.append(stage)  # page images to another computer: no text to show
            continue
        text = corpus[stage.stage]
        placeholders: tuple[str, ...] = ()
        if stage.personal_info_hidden:
            text, mapping = hidden(text)
            placeholders = tuple(mapping)
        sections.append(SentText(stage, text, prompts[stage.stage], placeholders))

    anonymized_raw, preview_placeholders = "", ()
    if preview_extracted and raw_text:
        anonymized_raw, mapping = hidden(raw_text)
        preview_placeholders = tuple(mapping)

    return SentTexts(
        sections=tuple(sections),
        page_stages=tuple(page_stages),
        level=flow.level,
        raw_text=raw_text,
        anonymized_raw=anonymized_raw,
        preview_placeholders=preview_placeholders,
    )
