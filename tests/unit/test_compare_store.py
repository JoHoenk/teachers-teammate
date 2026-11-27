"""The compared results on disk: one file per text and model, written and read back.

The store is the cache's one writer for a comparison: a result holds the student's text, so it
can be made again and is never logged.  What it must do is give back exactly what went in, ignore
and ignore a file of another shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from teachers_teammate.domain.data_location import SentRecord
from teachers_teammate.domain.proofreading.compared_result import ComparedResult
from teachers_teammate.infrastructure.compare_store import CompareStore

TEXT_SHA = "a1b2c3"


def _result(
    key: str = "key-1",
    *,
    text_sha: str = TEXT_SHA,
    model: str = "gemma3:12b",
    repeat: int = 0,
) -> ComparedResult:
    """Return one compared result as the service stores it."""
    return ComparedResult(
        text_sha=text_sha,
        key=key,
        provider="ollama",
        model=model,
        language="de",
        text="Der Hund ist laut.",
        review=None,
        seconds=41.5,
        fell_back=False,
        left_out=1,
        warning="Zwei Stellen fehlten.",
        sent=SentRecord(
            provider="ollama",
            model=model,
            endpoint="http://127.0.0.1:11434",
            names_hidden=2,
            hiding_on=True,
            privacy="privacy-1",
        ),
        digest="sha256:" + "a" * 64,
        exchange="exchange-1",
        app_version="0.0.0.dev0",
        created=1_800_000_000.0,
        repeat=repeat,
    )


def test_a_stored_result_comes_back_as_it_went_in(tmp_path: Path) -> None:
    """
    Given  a compared result with the record of how it was sent
    When   it is saved and read back
    Then   it equals what went in, and it sits under the text and the settings it belongs to
    """
    store = CompareStore(tmp_path)
    result = _result()

    store.save(result)

    assert (tmp_path / TEXT_SHA / "key-1.json").is_file()
    assert store.load(TEXT_SHA, "key-1") == result
    assert store.results(TEXT_SHA) == {"key-1": result}


def test_a_file_of_another_shape_is_no_result(tmp_path: Path) -> None:
    """
    Given  a file of another format in the folder of a text, and one that is no JSON at all
    When   the stored results of that text are read
    Then   neither is a result, and nothing raises
    """
    store = CompareStore(tmp_path)
    folder = tmp_path / TEXT_SHA
    folder.mkdir(parents=True)
    (folder / "other.json").write_text(json.dumps({"format": "compare-result/2"}), encoding="utf-8")
    (folder / "broken.json").write_text("{not json", encoding="utf-8")

    assert store.results(TEXT_SHA) == {}
    assert store.load(TEXT_SHA, "other") is None


def test_a_text_without_a_folder_has_no_results(tmp_path: Path) -> None:
    """
    Given  a cache folder a comparison never wrote into
    When   the results and one result of a text are asked for
    Then   there are none, and nothing raises
    """
    store = CompareStore(tmp_path / "compare")

    assert store.results(TEXT_SHA) == {}
    assert store.load(TEXT_SHA, "key-1") is None


def test_a_second_answer_sits_beside_the_first(tmp_path: Path) -> None:
    """
    Given  one model's answer, and then the same model's answer to a second run
    When   both are saved and read back
    Then   the text holds two results, the first one unchanged
    """
    store = CompareStore(tmp_path)
    store.save(_result())
    store.save(_result(repeat=1))

    assert sorted(store.results(TEXT_SHA)) == ["key-1", "key-1~2"]
    assert store.load(TEXT_SHA, "key-1", 1) is not None
    first = store.load(TEXT_SHA, "key-1", 0)
    assert first is not None
    assert first.text == "Der Hund ist laut."
