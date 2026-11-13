"""Tests of the layout: what a run is called, where it goes, and the machine files."""

from __future__ import annotations

import json

import pytest

from tests.tools.store_workspace import COMMITTED
from tools.model_eval import layout, run_format


# ── the parts of a run id ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tag", "slug"),
    [
        ("gemma4:e2b", "gemma4-e2b"),
        ("hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M", "bartowski-qwen3-8b-gguf-q4_k_m"),
        (" https://hf.co/user/Model:tag ", "user-model-tag"),
    ],
)
def test_the_model_slug_survives_being_a_file_name(tag, slug):
    """
    Given  a model tag with colons, slashes or a URL
    When   its slug is made
    Then   it contains only what a file name can hold
    """
    assert layout.model_slug(tag) == slug


@pytest.mark.parametrize(
    ("digest", "short"),
    [("sha256:0123456789abcdef", "01234567"), ("0123456789abcdef", "01234567"), ("", "unknown")],
)
def test_the_digest_is_eight_hex_digits_or_unknown(digest, short):
    """
    Given  a model digest with or without the sha256 prefix, or none
    When   it is shortened for the run id
    Then   it is eight hex digits, or unknown
    """
    assert layout.digest8({"digest": digest}) == short


def test_a_speed_run_is_isolated_and_sampled():
    """
    Given  run headers with and without isolation and a GPU sampler
    When   the kind of the run is asked for
    Then   only an isolated and sampled run is a speed run
    """
    assert layout.kind_of({"isolated": True, "gpu_sampler": {"kind": "local"}}) == "speed"
    assert layout.kind_of({"isolated": True, "gpu_sampler": None}) == "quality"
    assert layout.kind_of({"isolated": False, "gpu_sampler": {"kind": "local"}}) == "quality"


def test_a_machine_file_needs_its_keys(tmp_path):
    """
    Given  a machine file without a gpu entry
    When   it is read
    Then   it is refused with the missing key named
    """
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"schema": 1, "id": "broken"}), encoding="utf-8")
    with pytest.raises(ValueError, match="has no 'gpu'"):
        layout.read_machine(path)


def test_a_machine_file_and_its_name_have_to_agree(tmp_path):
    """
    Given  a machine file whose id differs from its file name
    When   it is read
    Then   it is refused as disagreeing
    """
    path = tmp_path / "rx9070xt-server.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "id": "another-machine",
                "gpu": {"name": "AMD Radeon RX 9070 XT"},
                "cpu": "TODO",
                "ram_gib": 31,
                "os": "Linux",
                "accelerator_stack": "ROCm 10.1.0",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="disagree"):
        layout.read_machine(path)


def test_every_committed_run_has_its_machine_file():
    """
    Given  every committed run, which names a machine
    When   the machine file is looked for
    Then   it exists, so a reader can compare speeds
    """
    if not COMMITTED.is_dir() or not layout.run_files(COMMITTED):
        pytest.skip("no committed run yet")
    for path in layout.run_files(COMMITTED):
        machine = run_format.read_run(path).header["machine"]
        if machine is None:  # a run of an online provider ran on no machine of ours
            continue
        assert layout.machine_path(COMMITTED, machine).exists(), machine


def _machine_record(**changes) -> dict:
    """Return a complete machine record, with *changes* applied over the valid values."""
    record = {
        "schema": 1,
        "id": "rx9070xt-server",
        "gpu": {"vendor": "amd", "name": "AMD Radeon RX 9070 XT", "vram_mb": 16304},
        "cpu": "AMD Ryzen 7 5800H",
        "ram_gib": 31,
        "os": "Ubuntu 24, Linux 7, bare metal",
        "accelerator_stack": "ROCm 10.1.0, amd-smi 27.1.0",
    }
    record.update(changes)
    return record


@pytest.mark.parametrize("key", ["cpu", "os", "accelerator_stack", "gpu.name"])
@pytest.mark.parametrize("value", ["TODO", " todo ", ""])
def test_a_machine_file_with_a_placeholder_is_refused(tmp_path, key, value):
    """
    Given  a machine file whose cpu, os, accelerator stack or gpu name is a placeholder
    When   it is read
    Then   it is refused with the field named, so a run cannot be exported from it
    """
    record = _machine_record()
    if key == "gpu.name":
        record["gpu"] = {**record["gpu"], "name": value}
    else:
        record[key] = value
    path = tmp_path / "rx9070xt-server.json"
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(ValueError, match=f"'{key}' is still a placeholder"):
        layout.read_machine(path)


def test_every_committed_machine_file_reads():
    """
    Given  the machine files of the repository
    When   each one is read
    Then   it is a complete machine file, with no value left as a placeholder
    """
    paths = sorted((COMMITTED / layout.MACHINES_DIR).glob("*.json"))
    assert paths, f"no machine file below {COMMITTED / layout.MACHINES_DIR}"
    for path in paths:
        assert layout.read_machine(path).id == path.stem


def _id(**changes) -> str:
    """Return a run id from invented facts, with *changes* applied."""
    facts = {
        "date": "2026-12-01T10:00:00",
        "machine": "rx9070xt-server",
        "model": "gemma4:e4b",
        "model_info": {"digest": "sha256:0123456789abcdef"},
        "mode": "review",
        "temperature": 0.2,
        "repeat": 1,
    }
    facts.update(changes)
    return layout.run_id(**facts)


def test_the_id_of_an_ollama_run_names_its_machine():
    """
    Given  the facts of a run on Ollama
    When   its id is made, with the provider named and without
    Then   both give the id the scheme always had: date, machine, model, digest, mode, T, repeat
    """
    expected = "2026-12-01_rx9070xt-server_gemma4-e4b_01234567_review_T0.2_r1"

    assert _id() == expected
    assert _id(provider="ollama") == expected


def test_the_id_of_an_online_run_names_its_provider_in_the_place_of_the_machine():
    """
    Given  the facts of a run of an online provider, which ran on no machine of ours
    When   its id is made
    Then   the provider takes the machine's place, so two providers of one model name differ
    """
    first = _id(machine=None, provider="openai", model_info={})
    second = _id(machine=None, provider="anthropic", model_info={})

    assert first == "2026-12-01_openai_gemma4-e4b_unknown_review_T0.2_r1"
    assert second != first


def test_the_id_of_an_ollama_run_needs_its_machine():
    """
    Given  a run on Ollama without a machine
    When   its id is made
    Then   it is refused, because a speed without a machine cannot be compared
    """
    with pytest.raises(ValueError, match="names the machine"):
        _id(machine=None)


def test_the_providers_a_run_may_name_are_the_ones_the_app_talks_to():
    """
    Given  the provider modules of the app
    When   the names a run may carry are listed
    Then   Ollama and the online providers are in it, and nothing that is no provider
    """
    names = layout.provider_names()

    assert layout.OLLAMA in names
    assert {"openai", "anthropic"} <= set(names)
    assert not any(name.startswith("_") for name in names)
