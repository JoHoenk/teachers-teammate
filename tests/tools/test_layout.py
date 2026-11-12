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
