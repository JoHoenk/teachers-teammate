"""Parity tests for the correction policy presets and the DOCX review labels.

Every supported language must carry the same policy: one ``{level}`` placeholder, no
left-over output-format instructions (they live in the answer format, not the preset), and a
complete set of review labels in the Word output.
"""

from __future__ import annotations

from typing import Any

from teachers_teammate.infrastructure.correction import PRESET_PROMPTS
from teachers_teammate.infrastructure.docx_builder import _LABELS, labels_for

_FORMAT_PHRASES = (
    # The old presets ended by asking for the corrected text only; that rule now lives in
    # the contract, which the code appends, so a preset must not repeat it.
    "return only",
    "nothing else",
    "nur dem korrigierten",
    "sonst nichts",
    "rien d'autre",
    "nada más",
    "nient'altro",
    "nada mais",
    "niets anders",
    # The contract forbids markers in the stored text; a preset must not ask for one.
    "[unclear]",
)

_REQUIRED_LABELS = (
    "explanations",
    "unclear",
    "original_expression",
    "why_unclear",
    "recommended_version",
    "unclear_prefix",
    "recommended_prefix",
    "suggestion_prefix",
    "grammar",
    "spelling",
    "punctuation",
    "word_choice",
    "word_order",
    "other",
)


def test_every_language_has_a_preset() -> None:
    """
    Given  the list of supported proofreading languages
    When   PRESET_PROMPTS is read
    Then   every one of the seven languages has a policy text
    """
    assert set(PRESET_PROMPTS) == {
        "english",
        "german",
        "french",
        "spanish",
        "italian",
        "portuguese",
        "dutch",
    }


def test_every_preset_has_exactly_one_level_placeholder() -> None:
    """
    Given  the policy of every supported language
    When   the {level} placeholder is counted
    Then   each preset contains it exactly once, which is what fill_placeholders replaces
    """
    counts = {language: prompt.count("{level}") for language, prompt in PRESET_PROMPTS.items()}

    assert counts == dict.fromkeys(PRESET_PROMPTS, 1)


def test_no_preset_contains_a_leftover_format_instruction() -> None:
    """
    Given  the policy of every supported language
    When   it is searched for the old output-format phrases
    Then   none is found: the answer format comes from the contract in code
    """
    offenders = {
        language: phrase
        for language, prompt in PRESET_PROMPTS.items()
        for phrase in _FORMAT_PHRASES
        if phrase in prompt.lower()
    }

    assert offenders == {}


def test_every_preset_asks_for_the_unclear_check() -> None:
    """
    Given  the policy of every supported language
    When   it is read for the two required ideas of the review policy
    Then   it mentions keeping the student's wording and flagging unclear wording
    """
    # One distinctive fragment per language: "leave it as it is / mark it as unclear".
    marker = {
        "english": "flag it as unclear",
        "german": "kennzeichne sie als unklar",
        "french": "signale-la comme peu claire",
        "spanish": "márcala como poco clara",
        "italian": "segnalala come poco chiara",
        "portuguese": "marque-a como pouco clara",
        "dutch": "markeer die als onduidelijk",
    }

    for language, fragment in marker.items():
        assert fragment in PRESET_PROMPTS[language].lower()


def test_every_preset_names_its_own_language() -> None:
    """
    Given  the policy of every supported language
    When   it is read for the name of the student text's language
    Then   the language is named in that language, not in English
    """
    # The subject of "correct the following <language> student text".
    subject = {
        "english": "english student text",
        "german": "deutschen",
        "french": "français",
        "spanish": "español",
        "italian": "italiano",
        "portuguese": "português",
        "dutch": "nederlandse",
    }

    for language, fragment in subject.items():
        assert fragment in PRESET_PROMPTS[language].lower()


def test_every_explanation_language_has_complete_labels() -> None:
    """
    Given  the label table of the Word output
    When   the languages and their keys are compared
    Then   every language has every label the review sections need
    """
    assert set(_LABELS) == set(PRESET_PROMPTS)

    for language, labels in _LABELS.items():
        missing = [key for key in _REQUIRED_LABELS if not labels.get(key)]
        assert missing == [], language


def test_labels_for_resolves_aliases_and_falls_back_to_english() -> None:
    """
    Given  an explanation language given as a name, a code or an endonym
    When   labels_for() is called
    Then   it resolves each form, and any unknown language yields the English labels
    """
    assert labels_for("German") == _LABELS["german"]
    assert labels_for("de") == _LABELS["german"]
    assert labels_for("Deutsch") == _LABELS["german"]
    assert labels_for("klingon") == _LABELS["english"]
    assert labels_for("") == _LABELS["english"]


def test_saved_legacy_preset_text_is_recognised() -> None:
    """
    Given  a correction prompt stored as text by a released version before this branch
    When   the migration checks it
    Then   the text is recognised as a built-in preset copy and reset to ""
    """
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        LEGACY_PRESET_PROMPTS,
        migrate_saved_prompt,
    )

    assert set(LEGACY_PRESET_PROMPTS) == set(PRESET_PROMPTS)

    for prompt in (*PRESET_PROMPTS.values(), *LEGACY_PRESET_PROMPTS.values()):
        assert migrate_saved_prompt(prompt) == ""

    # A copy with different whitespace is still a preset copy.
    assert migrate_saved_prompt(f"  {LEGACY_PRESET_PROMPTS['english']}  \n") == ""


def test_saved_custom_prompt_is_kept() -> None:
    """
    Given  a teacher's own instruction text
    When   the migration checks it
    Then   the text is kept as a custom prompt
    """
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        migrate_saved_prompt,
    )

    assert migrate_saved_prompt("") == ""
    assert migrate_saved_prompt("Fix my students' homework at {level}.") == (
        "Fix my students' homework at {level}."
    )


def test_presets_do_not_ask_for_a_labelled_suggestion() -> None:
    """
    Given  the built-in presets of every language
    When   they are read
    Then   none of them asks for a "labelled suggestion": the answer format has a field for it,
           and in corrections-only mode the sentence would put a label into the text
    """
    for name, text in PRESET_PROMPTS.items():
        lowered = text.lower()
        assert "suggestion" not in lowered, name
        assert "vorschlag" not in lowered, name
        assert "suggerimento" not in lowered, name
        assert "sugerencia" not in lowered, name
        assert "sugestão" not in lowered, name
        assert "suggestie" not in lowered, name


def test_saved_copy_of_a_superseded_preset_is_reset() -> None:
    """
    Given  a prompt saved by a build whose presets still ended with the suggestion sentence
    When   the migration checks it
    Then   it counts as a preset copy and is reset to "", while a real edit is kept
    """
    from teachers_teammate.domain.correction_prompts import (  # noqa: PLC0415
        SUPERSEDED_PRESET_PROMPTS,
        migrate_saved_prompt,
    )

    assert set(SUPERSEDED_PRESET_PROMPTS) == set(PRESET_PROMPTS)
    for name, text in SUPERSEDED_PRESET_PROMPTS.items():
        assert text != PRESET_PROMPTS[name]
        assert migrate_saved_prompt(text) == ""
    assert migrate_saved_prompt(SUPERSEDED_PRESET_PROMPTS["german"] + " Extra.") != ""


# ── Answer format ─────────────────────────────────────────────────────────


def _schema_field_names() -> set[str]:
    from teachers_teammate.infrastructure.correction_schema import (  # noqa: PLC0415
        CorrectionAnswer,
    )

    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            names.update(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(CorrectionAnswer.model_json_schema())
    return names


def test_review_format_names_every_field_of_the_schema() -> None:
    """
    Given  the answer schema the model is constrained to
    When   the built-in review answer format is read
    Then   it names every field of the schema (Ollama only constrains the keys, so the format
           is where the model learns what they mean) and REVIEW_FIELDS lists exactly those
    """
    from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
        REVIEW_FIELDS,
        REVIEW_FORMAT,
    )

    fields = _schema_field_names()
    assert set(REVIEW_FIELDS) == fields
    for name in fields:
        assert f'"{name}"' in REVIEW_FORMAT, name


def test_answer_formats_name_no_language_and_have_no_placeholders() -> None:
    """
    Given  the built-in answer formats
    When   they are read
    Then   they contain no language name and no braces: the languages travel in the message,
           so the text is plain text a teacher can edit
    """
    from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
        REVIEW_FORMAT,
        TEXT_FORMAT,
    )

    for text in (REVIEW_FORMAT, TEXT_FORMAT):
        assert "{" not in text and "}" not in text
        for language in ("English", "German", "French", "Spanish", "Italian", "Dutch"):
            assert language not in text


def test_custom_answer_format_is_used_and_blank_means_built_in() -> None:
    """
    Given  a custom answer format, and a blank one
    When   the format of a mode is resolved
    Then   the custom text wins and blank text falls back to the built-in format
    """
    from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
        REVIEW_FORMAT,
        TEXT_FORMAT,
        resolve_format,
    )

    assert resolve_format("review", " mine ") == "mine"
    assert resolve_format("review", "  ") == REVIEW_FORMAT
    assert resolve_format("text") == TEXT_FORMAT


def test_missing_fields_reports_what_an_edited_format_dropped() -> None:
    """
    Given  an edited review format that lost two field names
    When   it is checked
    Then   exactly those fields are reported, and an empty format (= built-in) reports none
    """
    from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
        REVIEW_FORMAT,
        missing_fields,
    )

    edited = REVIEW_FORMAT.replace("why_unclear", "reason").replace("category", "kind")
    assert missing_fields(edited) == ["category", "why_unclear"]
    assert missing_fields("") == []
    assert missing_fields(REVIEW_FORMAT) == []


def test_saved_answer_formats_of_another_version_are_dropped() -> None:
    """
    Given  edited answer formats saved for an older answer-format version, and ones for this one
    When   the migration checks them
    Then   the old edits are dropped with a reset flag, the current edits are kept, and a saved
           copy of a built-in text is stored as ""
    """
    from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
        ANSWER_FORMAT_VERSION,
        REVIEW_FORMAT,
        migrate_saved_formats,
    )

    assert migrate_saved_formats("mine", "mine too", ANSWER_FORMAT_VERSION - 1) == ("", "", True)
    assert migrate_saved_formats("", "", ANSWER_FORMAT_VERSION - 1) == ("", "", False)
    assert migrate_saved_formats("mine", "", ANSWER_FORMAT_VERSION) == ("mine", "", False)
    assert migrate_saved_formats(REVIEW_FORMAT, "", ANSWER_FORMAT_VERSION) == ("", "", False)


def test_config_file_with_an_outdated_answer_format_falls_back(tmp_path, capsys) -> None:
    """
    Given  a config file with an answer format written for an older version
    When   the file is loaded
    Then   the edit is dropped and a warning names it
    """
    from teachers_teammate.config import load_config_file  # noqa: PLC0415

    path = tmp_path / "ocr.toml"
    path.write_text('answer_format_review = "mine"\nanswer_format_version = 0\n', encoding="utf-8")

    loaded = load_config_file(path)

    assert loaded["answer_format_review"] == ""
    assert "answer format" in capsys.readouterr().err
