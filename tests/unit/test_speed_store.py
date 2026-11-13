"""Unit tests for teachers_teammate.infrastructure.speed_store."""

from __future__ import annotations

import json
from pathlib import Path

from teachers_teammate.domain.performance_estimate import ModelSpeed, SpeedStep, StepOutcome
from teachers_teammate.infrastructure.speed_store import (
    KEEP_SAMPLES,
    SCHEMA,
    RunSample,
    SampleKey,
    SampleStore,
    SpeedKey,
    SpeedRecord,
    SpeedStore,
    StepRecord,
    now_stamp,
)


def _speed(*, write_tps: float = 100.0) -> ModelSpeed:
    """Return a measured speed."""
    return ModelSpeed(
        read_tps=1000.0,
        write_tps=write_tps,
        load_s=3.0,
        on_card=0.8,
        image_tokens=1500,
        chars_per_token=4.2,
    )


def _key(**kwargs) -> SpeedKey:
    """Return the key of a measurement on the local server."""
    values = {
        "server": "http://127.0.0.1:11434",
        "tag": "gemma4:e4b",
        "digest": "sha256:aaa",
        "ollama_version": "0.40.15",
        "gpu": "AMD Radeon RX 9070 XT",
    }
    values.update(kwargs)
    return SpeedKey(**values)


def _record(*, key: SpeedKey | None = None, measured_at: str = "2026-11-13T10:00:00+00:00"):
    """Return a measurement of the proofreading step."""
    return SpeedRecord(
        key=key if key is not None else _key(),
        measured_at=measured_at,
        steps=(
            StepRecord(SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed()),
            StepRecord(SpeedStep.READING, StepOutcome.SKIPPED),
        ),
        on_battery=False,
        shared=True,
        swaps=False,
    )


# ── round trip ─────────────────────────────────────────────────────────────


def test_a_saved_measurement_comes_back_unchanged(tmp_path: Path) -> None:
    """
    Given  a store with one measurement saved
    When   the measurement is looked up by its key
    Then   every field comes back as it was saved, and it counts as current
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    stored = store.lookup(_key())

    assert stored is not None
    assert stored.current is True
    assert stored.record.key == _key()
    assert stored.record.measured_at == "2026-11-13T10:00:00+00:00"
    assert stored.record.on_battery is False
    assert stored.record.shared is True
    assert stored.record.swaps is False
    speed = stored.record.measured(SpeedStep.PROOFREADING)
    assert speed == _speed()
    assert stored.record.step(SpeedStep.READING).outcome is StepOutcome.SKIPPED


def test_a_missing_file_has_no_measurements(tmp_path: Path) -> None:
    """
    Given  a store whose file does not exist yet
    When   it is asked for a measurement
    Then   it reports none rather than failing
    """
    assert SpeedStore(tmp_path / "measurements.json").lookup(_key()) is None


def test_saving_the_same_key_twice_keeps_one_measurement(tmp_path: Path) -> None:
    """
    Given  a store with a measurement of one model
    When   the same model is measured again
    Then   the newer measurement replaces the older one
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record(measured_at="2026-11-13T10:00:00+00:00"))
    store.save(_record(measured_at="2026-11-14T10:00:00+00:00"))

    assert len(store.records()) == 1
    assert store.lookup(_key()).record.measured_at == "2026-11-14T10:00:00+00:00"


def test_records_are_newest_first(tmp_path: Path) -> None:
    """
    Given  a store with two measurements of different models
    When   the records are listed
    Then   the newer one comes first
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record(measured_at="2026-11-13T10:00:00+00:00"))
    store.save(_record(key=_key(tag="qwen3:4b"), measured_at="2026-11-14T10:00:00+00:00"))

    assert [item.key.tag for item in store.records()] == ["qwen3:4b", "gemma4:e4b"]


# ── out of date ────────────────────────────────────────────────────────────


def test_a_changed_digest_makes_the_measurement_out_of_date(tmp_path: Path) -> None:
    """
    Given  a measurement of a model whose digest has since changed
    When   it is looked up by the new key
    Then   it comes back marked out of date, so it is shown but never used for an estimate
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    stored = store.lookup(_key(digest="sha256:bbb"))

    assert stored is not None
    assert stored.current is False
    assert stored.record.measured_at == "2026-11-13T10:00:00+00:00"


def test_a_changed_server_version_makes_the_measurement_out_of_date(tmp_path: Path) -> None:
    """
    Given  a measurement taken on an older Ollama
    When   it is looked up for the current version
    Then   it comes back marked out of date
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    assert store.lookup(_key(ollama_version="0.41.0")).current is False


def test_a_changed_card_makes_the_measurement_out_of_date(tmp_path: Path) -> None:
    """
    Given  a measurement taken on one graphics card
    When   it is looked up for another card
    Then   it comes back marked out of date
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    assert store.lookup(_key(gpu="NVIDIA RTX 4090")).current is False


def test_another_server_has_no_measurement(tmp_path: Path) -> None:
    """
    Given  a measurement taken on the local server
    When   it is looked up for a server on the network
    Then   nothing is found, because another machine's speed says nothing about this one
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    assert store.lookup(_key(server="http://192.168.1.20:11434")) is None


def test_another_model_has_no_measurement(tmp_path: Path) -> None:
    """
    Given  a measurement of one model
    When   another model is looked up
    Then   nothing is found
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    assert store.lookup(_key(tag="qwen3:4b")) is None


def test_a_current_measurement_wins_over_an_out_of_date_one(tmp_path: Path) -> None:
    """
    Given  a store with an out-of-date and a current measurement of one model
    When   the current key is looked up
    Then   the current measurement is returned
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record(key=_key(digest="sha256:old"), measured_at="2026-11-01T10:00:00+00:00"))
    store.save(_record(key=_key(digest="sha256:new"), measured_at="2026-11-13T10:00:00+00:00"))

    stored = store.lookup(_key(digest="sha256:new"))

    assert stored.current is True
    assert stored.record.key.digest == "sha256:new"


def test_many_keys_are_answered_from_one_read(tmp_path: Path) -> None:
    """
    Given  a store with a current measurement of one model and an out-of-date one of another
    When   both keys are looked up at once, together with a model that was never measured
    Then   the current one comes back current, the other out of date, and the third is absent
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())
    store.save(_record(key=_key(tag="qwen3:4b", digest="sha256:old")))

    found = store.lookup_many(
        [_key(), _key(tag="qwen3:4b", digest="sha256:new"), _key(tag="gemma4:12b")]
    )

    assert found["gemma4:e4b"].current is True
    assert found["qwen3:4b"].current is False
    assert "gemma4:12b" not in found


# ── a file this version does not understand ────────────────────────────────


def test_an_unknown_schema_is_ignored(tmp_path: Path) -> None:
    """
    Given  a file written by a version with another schema
    When   the store reads it
    Then   it reports no measurements rather than failing
    """
    path = tmp_path / "measurements.json"
    path.write_text(json.dumps({"schema": SCHEMA + 1, "measurements": []}), encoding="utf-8")

    assert SpeedStore(path).records() == ()


def test_a_broken_file_is_ignored(tmp_path: Path) -> None:
    """
    Given  a file that is not JSON
    When   the store reads it
    Then   it reports no measurements rather than failing
    """
    path = tmp_path / "measurements.json"
    path.write_text("{not json", encoding="utf-8")

    assert SpeedStore(path).records() == ()


def test_an_unreadable_measurement_is_skipped(tmp_path: Path) -> None:
    """
    Given  a file whose measurement has no model tag
    When   the store reads it
    Then   that measurement is skipped and the readable one is returned
    """
    path = tmp_path / "measurements.json"
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "measurements": [
                    {"server": "http://127.0.0.1:11434", "measured_at": "2026-11-13T10:00:00Z"},
                    {
                        "server": "http://127.0.0.1:11434",
                        "tag": "gemma4:e4b",
                        "measured_at": "2026-11-13T10:00:00Z",
                        "steps": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert [item.key.tag for item in SpeedStore(path).records()] == ["gemma4:e4b"]


def test_a_step_with_an_unknown_name_is_dropped(tmp_path: Path) -> None:
    """
    Given  a file whose measurement has a step this version does not know
    When   the store reads it
    Then   the measurement is kept and the unknown step is dropped
    """
    path = tmp_path / "measurements.json"
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "measurements": [
                    {
                        "server": "http://127.0.0.1:11434",
                        "tag": "gemma4:e4b",
                        "measured_at": "2026-11-13T10:00:00Z",
                        "steps": [
                            {"step": "translation", "outcome": "measured"},
                            {"step": "review", "outcome": "measured"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    record = SpeedStore(path).records()[0]

    assert [item.step for item in record.steps] == [SpeedStep.REVIEW]


# ── the write ──────────────────────────────────────────────────────────────


def test_the_write_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """
    Given  a store that has saved a measurement
    When   the directory is listed
    Then   only the measurements file is there, with no temporary file beside it
    """
    store = SpeedStore(tmp_path / "measurements.json")
    store.save(_record())

    assert [item.name for item in tmp_path.iterdir()] == ["measurements.json"]


def test_the_file_carries_its_schema_number(tmp_path: Path) -> None:
    """
    Given  a store that has saved a measurement
    When   the file is read as JSON
    Then   it names the schema, so a later version can tell what it is looking at
    """
    path = tmp_path / "measurements.json"
    SpeedStore(path).save(_record())

    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == SCHEMA


def test_the_directory_is_created_on_the_first_save(tmp_path: Path) -> None:
    """
    Given  a store whose directory does not exist yet
    When   a measurement is saved
    Then   the directory is created and the file is written
    """
    path = tmp_path / "speed" / "measurements.json"

    SpeedStore(path).save(_record())

    assert path.exists()


def test_the_default_path_lives_under_the_storage_root(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a storage root
    When   the store is asked for its path
    Then   the file sits in a ``speed`` directory below that root
    """
    from teachers_teammate.infrastructure import speed_store  # noqa: PLC0415
    from teachers_teammate.infrastructure.storage_root import StorageRoot  # noqa: PLC0415

    monkeypatch.setattr(
        speed_store, "resolve_storage_root", lambda: StorageRoot(path=tmp_path, source="test")
    )

    assert SpeedStore().path == tmp_path / "speed" / "measurements.json"


# ── the stamp ──────────────────────────────────────────────────────────────


def test_the_stamp_is_an_iso_timestamp_in_utc() -> None:
    """
    Given  nothing
    When   now_stamp() is called
    Then   the result is an ISO 8601 timestamp that says it is UTC
    """
    stamp = now_stamp()

    assert stamp.endswith("+00:00")
    assert "T" in stamp


# ── Samples of real runs ───────────────────────────────────────────────────


def _sample(*, write_tps: float = 50.0, at: str = "2026-11-13T10:00:00+00:00") -> RunSample:
    """Return one sample of a proofread document."""
    return RunSample(
        read_tps=2000.0,
        write_tps=write_tps,
        load_s=0.0,
        answer_tokens=300,
        words=250,
        pieces=1,
        at=at,
    )


def _sample_key(**kwargs) -> SampleKey:
    """Return the key of a proofreading sample on the local server."""
    values = {
        "server": "http://127.0.0.1:11434",
        "tag": "gemma4:e4b",
        "step": SpeedStep.PROOFREADING,
        "mode": "review",
    }
    values.update(kwargs)
    return SampleKey(**values)


def test_a_saved_sample_comes_back_unchanged(tmp_path: Path) -> None:
    """
    Given  a store with one sample of a proofread document
    When   the samples of that model and step are read back
    Then   the sample is there with all its numbers
    """
    store = SampleStore(tmp_path / "samples.json")
    store.add(_sample_key(), _sample())

    stored = store.samples("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.PROOFREADING)

    assert stored == {"review": (_sample(),)}


def test_the_samples_of_another_step_are_not_returned(tmp_path: Path) -> None:
    """
    Given  a store with a reading sample and a proofreading sample
    When   the proofreading samples are asked for
    Then   only that step's sample comes back
    """
    store = SampleStore(tmp_path / "samples.json")
    store.add(_sample_key(step=SpeedStep.READING, mode=""), _sample())
    store.add(_sample_key(), _sample(write_tps=40.0))

    stored = store.samples("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.PROOFREADING)

    assert list(stored) == ["review"]
    assert stored["review"][0].write_tps == 40.0


def test_the_two_proofreading_modes_are_kept_apart(tmp_path: Path) -> None:
    """
    Given  a review sample and a text-mode sample of the same model
    When   the samples are read back
    Then   they are grouped by mode: their answer lengths cannot be averaged
    """
    store = SampleStore(tmp_path / "samples.json")
    store.add(_sample_key(), _sample(write_tps=50.0))
    store.add(_sample_key(mode="text"), _sample(write_tps=60.0))

    stored = store.samples("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.PROOFREADING)

    assert sorted(stored) == ["review", "text"]
    assert stored["text"][0].write_tps == 60.0


def test_only_the_last_samples_of_a_key_are_kept(tmp_path: Path) -> None:
    """
    Given  more samples than the window holds
    When   they are read back
    Then   the oldest are gone and the newest are there
    """
    store = SampleStore(tmp_path / "samples.json")
    for index in range(KEEP_SAMPLES + 5):
        store.add(_sample_key(), _sample(write_tps=float(index)))

    stored = store.samples("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.PROOFREADING)

    assert len(stored["review"]) == KEEP_SAMPLES
    assert stored["review"][0].write_tps == 5.0
    assert stored["review"][-1].write_tps == float(KEEP_SAMPLES + 4)


def test_a_missing_samples_file_has_no_samples(tmp_path: Path) -> None:
    """
    Given  a store whose file does not exist
    When   samples are asked for
    Then   there are none and nothing raises
    """
    store = SampleStore(tmp_path / "samples.json")

    assert store.samples("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.READING) == {}


def test_a_samples_file_with_another_schema_is_ignored(tmp_path: Path) -> None:
    """
    Given  a samples file written by a version this one does not know
    When   samples are asked for
    Then   the file is ignored rather than treated as an error
    """
    path = tmp_path / "samples.json"
    path.write_text(json.dumps({"schema": SCHEMA + 1, "samples": []}), encoding="utf-8")
    store = SampleStore(path)

    assert store.samples("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.READING) == {}


def test_a_sample_holds_no_text(tmp_path: Path) -> None:
    """
    Given  a stored sample
    When   the file is read as text
    Then   it holds numbers and a stamp only: no page, no file name, no essay
    """
    path = tmp_path / "samples.json"
    store = SampleStore(path)
    store.add(_sample_key(), _sample())

    written = json.loads(path.read_text(encoding="utf-8"))

    entry = written["samples"][0]
    assert set(entry) == {"server", "tag", "step", "mode", "samples"}
    assert set(entry["samples"][0]) == {
        "read_tps",
        "write_tps",
        "load_s",
        "image_tokens",
        "answer_tokens",
        "words",
        "pieces",
        "at",
    }


def test_the_answer_length_per_hundred_words_is_derived_from_the_sample() -> None:
    """
    Given  a sample of 300 answer tokens for 250 words
    When   its answer length is asked for
    Then   it is 120 tokens per 100 words
    """
    assert _sample().answer_per_100_words == 120.0


def test_a_sample_without_words_has_no_answer_length() -> None:
    """
    Given  a sample of a step that carried no text
    When   its answer length is asked for
    Then   it is unknown rather than a division by zero
    """
    assert RunSample(answer_tokens=300, words=0).answer_per_100_words is None


# ── Reading once, normalised servers, and the file of another schema ───────


def test_an_unchanged_file_is_parsed_once(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a store with a saved measurement
    When   it is asked for the records many times
    Then   the file is parsed once, and a write by another store is still seen
    """
    path = tmp_path / "measurements.json"
    store = SpeedStore(path)
    store.save(_record())
    parsed: list[int] = []
    original = store._parse
    monkeypatch.setattr(store, "_parse", lambda: parsed.append(1) or original())

    for _ in range(5):
        store.records()
    SpeedStore(path).save(_record(key=_key(tag="mistral:latest")))
    tags = {item.key.tag for item in store.records()}

    assert len(parsed) == 2
    assert tags == {"gemma4:e4b", "mistral:latest"}


def test_a_sample_filed_under_a_typed_address_is_found_under_the_normalised_one(
    tmp_path: Path,
) -> None:
    """
    Given  a samples file written with the address as the teacher typed it
    When   the samples are asked for under the normalised address
    Then   they are found, and two spellings of one server are one list
    """
    path = tmp_path / "samples.json"
    entry = lambda server, at: {  # noqa: E731
        "server": server,
        "tag": "gemma4:e4b",
        "step": "proofreading",
        "mode": "review",
        "samples": [{"read_tps": 1.0, "write_tps": 2.0, "at": at}],
    }
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "samples": [
                    entry("127.0.0.1:11434/", "2026-11-13T10:00:00+00:00"),
                    entry("http://127.0.0.1:11434", "2026-11-13T11:00:00+00:00"),
                ],
            }
        ),
        encoding="utf-8",
    )

    found = SampleStore(path).samples(
        "http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.PROOFREADING
    )

    assert [sample.at for sample in found["review"]] == [
        "2026-11-13T10:00:00+00:00",
        "2026-11-13T11:00:00+00:00",
    ]


def test_a_file_of_another_schema_is_kept_as_a_backup_before_it_is_written_over(
    tmp_path: Path,
) -> None:
    """
    Given  a measurements file written by a newer version (another schema)
    When   this version saves a measurement
    Then   the old file is kept as <name>.bak and the new file has this version's schema
    """
    path = tmp_path / "measurements.json"
    newer = json.dumps({"schema": SCHEMA + 1, "measurements": ["future"]})
    path.write_text(newer, encoding="utf-8")

    SpeedStore(path).save(_record())

    assert (tmp_path / "measurements.json.bak").read_text(encoding="utf-8") == newer
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == SCHEMA


def test_a_samples_file_of_another_schema_is_kept_as_a_backup(tmp_path: Path) -> None:
    """
    Given  a samples file written by a newer version
    When   this version adds a sample
    Then   the old file is kept as <name>.bak
    """
    path = tmp_path / "samples.json"
    newer = json.dumps({"schema": SCHEMA + 1, "samples": ["future"]})
    path.write_text(newer, encoding="utf-8")

    SampleStore(path).add(
        SampleKey("http://127.0.0.1:11434", "gemma4:e4b", SpeedStep.PROOFREADING, "review"),
        RunSample(read_tps=1.0, write_tps=2.0, at=now_stamp()),
    )

    assert (tmp_path / "samples.json.bak").read_text(encoding="utf-8") == newer


def test_a_current_file_is_not_backed_up(tmp_path: Path) -> None:
    """
    Given  a measurements file of this version
    When   a second measurement is saved
    Then   no backup is made
    """
    path = tmp_path / "measurements.json"
    store = SpeedStore(path)
    store.save(_record())

    store.save(_record(key=_key(tag="mistral:latest")))

    assert not (tmp_path / "measurements.json.bak").exists()
