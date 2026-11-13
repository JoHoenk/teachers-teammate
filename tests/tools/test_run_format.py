"""Tests of one run file: the warning codes it stores and the way it is written and read."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.infrastructure import correction
from tools.model_eval import run_format


def test_a_joined_warning_takes_the_most_specific_code():
    """
    Given  the warnings a text in pieces produces, joined by a space
    When   the code of each is asked for
    Then   a partial review wins over a fallback and over an unplaced correction, a fallback wins
           over an unplaced correction, and anything else is other
    """
    fallback = correction.FALLBACK_WARNING
    partial = correction.MISSING_REVIEW_WARNING
    unplaced = "A correction could not be placed in the text."

    assert run_format.warning_code(f"{fallback} {partial}") == "review_partial"
    assert run_format.warning_code(f"{partial} {unplaced}") == "review_partial"
    assert run_format.warning_code(f"{unplaced} {fallback}") == "fallback"
    assert run_format.warning_code(fallback) == "fallback"
    assert run_format.warning_code(unplaced) == "unplaced"
    assert run_format.warning_code("Something else went wrong.") == "other"
    assert run_format.warning_code("") == ""
    assert "review_partial" in run_format.WARNING_CODES


def _header(path: Path) -> dict:
    """Return a minimal header of the run stored at *path*, as the reader requires it."""
    return {"type": run_format.RUN_TYPE, "schema": run_format.RUN_SCHEMA, "run_id": path.stem}


def test_a_written_run_reads_back_with_its_keys_sorted(tmp_path):
    """
    Given  a header and two items whose keys are not in order
    When   they are written and read back
    Then   the file holds one line per object with sorted keys, and the reader returns the same
           header and items
    """
    path = tmp_path / "2026-11-09_run.jsonl"
    header = _header(path)
    items = [{"item": "de-01", "scores": {"tp": 1}}, {"item": "de-02"}]

    run_format.write_run(path, header, items)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"run_id": "2026-11-09_run", "schema": 1, "type": "run"}'
    assert lines[1] == '{"item": "de-01", "scores": {"tp": 1}}'
    run = run_format.read_run(path)
    assert run.header == header
    assert list(run.records) == items


def test_a_renamed_run_file_is_refused(tmp_path):
    """
    Given  a run file whose header names another run
    When   it is read
    Then   it is refused, because a renamed file is not evidence of anything
    """
    path = tmp_path / "2026-11-09_run.jsonl"
    run_format.write_run(path, _header(path), [])
    renamed = tmp_path / "2026-11-10_other.jsonl"
    path.rename(renamed)

    with pytest.raises(ValueError, match="the header names the run"):
        run_format.read_run(renamed)


def _header_dict(**overrides) -> dict:
    """Return the header fields the readers of a run need."""
    header = {
        "run_id": "r1",
        "testset": "gec-2026-12",
        "manifest_sha256": "a" * 64,
        "eval_version": 5,
        "provider": "ollama",
        "machine": "server",
        "date": "2026-11-12T10:00:00+00:00",
        "model": "gemma4:e4b",
        "model_info": {"digest": "sha256:abc"},
        "mode": "review",
        "explainer": None,
        "temperature": 1.0,
        "num_ctx": 8192,
        "repeat": 2,
        "request_profile": {"temperature": 1.0, "compact_json": False},
    }
    header.update(overrides)
    return header


def test_the_typed_header_reads_the_fields_readers_group_runs_by():
    """
    Given  the header dict of a run
    When   it is typed
    Then   the fields come back with their kinds: a mode, a number, the digest, and the request
           profile as sorted items
    """
    meta = run_format.RunHeader.from_dict(_header_dict())

    assert (meta.model, meta.digest, meta.repeat, meta.num_ctx) == (
        "gemma4:e4b",
        "sha256:abc",
        2,
        8192,
    )
    assert meta.mode.value == "review"
    assert meta.temperature == 1.0
    assert meta.request_profile == (("compact_json", False), ("temperature", 1.0))
    assert meta.explainer is None
    assert (meta.provider, meta.machine) == ("ollama", "server")


def test_the_typed_header_of_an_online_run_has_no_machine():
    """
    Given  the header of a run of an online provider, whose machine is null
    When   it is typed
    Then   the provider is named and the machine is empty, so readers that group by machine need
           no case for it
    """
    meta = run_format.RunHeader.from_dict(_header_dict(provider="openai", machine=None))

    assert (meta.provider, meta.machine) == ("openai", "")


def test_the_typed_header_refuses_a_header_without_a_field_readers_need():
    """
    Given  a header without its test set
    When   it is typed
    Then   it is refused with the missing key, instead of a reader meeting a default
    """
    header = _header_dict()
    del header["testset"]

    with pytest.raises(KeyError):
        run_format.RunHeader.from_dict(header)
