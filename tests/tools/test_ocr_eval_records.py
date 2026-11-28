"""A run's record: what it holds, and the check that it holds no text."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from tools.evaluation.core.formats import FormatError

from tools.ocr_eval import records


def _record() -> records.RunRecord:
    """Return one small record of one page."""
    return records.RunRecord(
        header=records.Header(
            model="hf.co/richardyoung/olmOCR-2-7B-1025-GGUF:Q8_0",
            engine="ollama",
            prompt="app",
            preset="R",
            digest="abc123",
            auto_identity=("v1", "gray", "+turn"),
            detector=(("max_side", 960.0),),
            set_version="deadbeef",
            pages=1,
            language="English,German",
            machine="rx9070xt-server",
            licence="Modified OpenRAIL-M",
            memory_mb=6144,
        ),
        pages=(
            records.PageNumbers(
                page="s01",
                group="handwritten_scan",
                split="tune",
                cer=0.1,
                wer=0.2,
                bag_wer=0.15,
                kept=0.5,
                words=10,
                characters=52,
                writer="w3",
                flags=("cut_off",),
                seconds=2.0,
                answer_tokens=42,
                steps=(records.StepTrace("light", True, "unevenness 0.31 > 0.25"),),
            ),
        ),
    )


def test_a_record_reads_back_as_it_was_written() -> None:
    """Given a record of one page
    When   it is written and read back
    Then   the header and the page are the same, so a report never guesses
    """
    written = _record()

    read = records.RunRecord.from_dict(written.to_dict())

    assert read == written


def test_a_page_without_a_field_that_has_no_default_is_refused() -> None:
    """Given a stored page that lacks its character count
    When   the record is read back
    Then   it is refused by the field's path, rather than read as a page of none
    """
    stored = _record().to_dict()
    del stored["pages"][0]["characters"]

    with pytest.raises(FormatError, match=r"pages\[0\]: no characters"):
        records.RunRecord.from_dict(stored)


def test_a_record_holds_numbers_and_names_only() -> None:
    """Given a record as a run writes it, a tag with slashes and a licence with spaces in it
    When   validate() checks it
    Then   nothing is refused
    """
    records.validate(_record().to_dict())


def test_a_string_besides_the_names_is_refused() -> None:
    """Given a record with a sentence added where a number belongs
    When   validate() checks it
    Then   it refuses, naming the field
    """
    broken = _record().to_dict()
    broken["pages"][0]["cer"] = "the model read it well"

    with pytest.raises(FormatError, match=r"pages\[0\]\.cer"):
        records.validate(broken)


def test_a_field_a_record_does_not_have_is_refused() -> None:
    """Given a record with a field of somebody's own
    When   validate() checks it
    Then   the field is refused by its name
    """
    broken = _record().to_dict()
    broken["header"]["note"] = 3

    with pytest.raises(FormatError, match="note"):
        records.validate(broken)


def test_a_flag_the_scores_do_not_have_is_refused() -> None:
    """Given a page flagged with a word this tool never writes
    When   validate() checks it
    Then   it refuses the flag
    """
    broken = _record().to_dict()
    broken["pages"][0]["flags"] = ["plausible"]

    with pytest.raises(FormatError, match="flags"):
        records.validate(broken)


def test_a_step_s_measurement_longer_than_the_app_s_is_refused() -> None:
    """Given a step measurement long enough to carry a page's text
    When   validate() checks it
    Then   it is refused
    """
    broken = _record().to_dict()
    broken["pages"][0]["steps"][0]["measurement"] = "x" * (records.MEASUREMENT_LIMIT + 1)

    with pytest.raises(FormatError, match="measurement"):
        records.validate(broken)


def test_a_step_s_measurement_that_holds_a_path_is_refused() -> None:
    """Given a step measurement that carries a file's path, as a library's message can
    When   validate() checks it
    Then   it is refused by its characters, whatever the words around it say
    """
    broken = _record().to_dict()
    broken["pages"][0]["steps"][0]["measurement"] = "no model at /home/anna/models"

    with pytest.raises(FormatError, match="measurement"):
        records.validate(broken)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("page", "Anna wrote this page"),
        ("group", "../../home/anna"),
        ("writer", "x" * (records.LABEL_LIMIT + 1)),
    ],
)
def test_a_page_s_id_that_is_not_an_id_is_refused(field: str, value: str) -> None:
    """Given a page whose id, group or writer holds a sentence, a path or a page's worth of text
    When   validate() checks it
    Then   it is refused by the field's path: a page is named by a neutral id
    """
    broken = _record().to_dict()
    broken["pages"][0][field] = value

    with pytest.raises(FormatError, match=rf"pages\[0\]\.{field}"):
        records.validate(broken)


def test_a_page_on_no_side_of_the_split_is_refused() -> None:
    """Given a page whose side of the split is neither tune nor check
    When   validate() checks it
    Then   it is refused
    """
    broken = _record().to_dict()
    broken["pages"][0]["split"] = "all"

    with pytest.raises(FormatError, match=r"pages\[0\]\.split"):
        records.validate(broken)


def test_a_prompt_id_a_record_does_not_know_is_refused() -> None:
    """Given a record whose header names a prompt's text where its id belongs
    When   validate() checks it
    Then   it is refused, so a record cannot smuggle a prompt in as its name
    """
    broken = _record().to_dict()
    broken["header"]["prompt"] = "Text Recognition:"

    with pytest.raises(FormatError, match="prompt"):
        records.validate(broken)


def test_a_header_name_long_enough_to_hold_a_page_is_refused() -> None:
    """Given a record whose machine field holds a page's worth of characters
    When   validate() checks it
    Then   it is refused, so a name stays a name
    """
    broken = _record().to_dict()
    broken["header"]["machine"] = "x" * (records.LABEL_LIMIT + 1)

    with pytest.raises(FormatError, match="machine"):
        records.validate(broken)


def test_a_detector_key_that_is_not_a_setting_s_name_is_refused() -> None:
    """Given a record whose detector numbers are keyed by a sentence
    When   validate() checks it
    Then   it is refused by the detector's field: a key is a string the values' check never sees
    """
    broken = _record().to_dict()
    broken["header"]["detector"] = {"max_side": 1600.0, "Der Hund bellt laut": 1.0}

    with pytest.raises(FormatError, match="detector"):
        records.validate(broken)


def test_the_totals_weight_each_rate_by_what_it_is_a_share_of() -> None:
    """Given a long page read perfectly and a short page read wholly wrong
    When   the totals are made
    Then   the CER is weighted by characters and the WER by words, so the short page is not
           weighed like the long one
    """
    pages = [
        records.PageNumbers("s01", "g", "tune", 0.0, 0.0, 0.0, None, words=10, characters=60),
        records.PageNumbers("s02", "g", "tune", 1.0, 1.0, 1.0, None, words=1, characters=20),
    ]

    totals = records.Totals.of(pages)

    assert totals.words == 11
    assert totals.characters == 80
    assert totals.cer == pytest.approx(20 / 80)
    assert totals.wer == pytest.approx(1 / 11)
    assert totals.failures == 0


@pytest.mark.parametrize("name", ["presets", "records", "runs", "report"])
def test_the_record_modules_import_no_page_and_no_engine(name: str) -> None:
    """Given a module of the record format, or one that reads records back
    When   its imports are read
    Then   it imports nothing of the app's infrastructure and no module that reads a page, so
           validate and report need nothing that reads one
    """
    module = importlib.import_module(f"tools.ocr_eval.{name}")
    tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    readers = {"pages", "prepare", "engine", "metrics", "readings", "runner", "screen", "cli"}

    assert not {one for one in imported if one.startswith("teachers_teammate.infrastructure")}
    assert not readers & {one.rsplit(".", 1)[-1] for one in imported}
