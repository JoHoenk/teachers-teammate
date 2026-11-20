"""The machines the evaluations ran on: the registry, its ids, and what it refuses.

The registry is data, so the tests write their own files; one test reads the committed registry,
because a record that names a machine has to find it there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.machines import (
    DEVICES,
    MACHINES_DIR,
    Machine,
    known_machines,
    read_machine,
    require_machine,
)


def _machine(**overrides) -> dict:
    """Return a machine file as the registry holds it."""
    data = {
        "format": 1,
        "id": "invented-server",
        "gpu": {"vendor": "amd", "name": "AMD Radeon Invented", "vram_mb": 16304},
        "cpu": "Intel Core i5-13600KF",
        "ram_gib": 31,
        "os": "Ubuntu 24",
        "accelerator_stack": "ROCm 10.1.0",
        "notes": "",
        "aliases": [],
    }
    data.update(overrides)
    return data


def _write(root: Path, name: str, data: dict) -> Path:
    """Write *data* as the machine file *name* below *root*'s registry."""
    path = root / MACHINES_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_a_known_machine_is_read_with_its_card_and_its_processor(tmp_path: Path) -> None:
    """
    Given  a registry holding one machine
    When   the machine is asked for by its id
    Then   its card, its memory and its processor come back, and its line names the card
    """
    _write(tmp_path, "invented-server.json", _machine())

    machine = require_machine(tmp_path, "invented-server")

    assert machine == Machine(
        id="invented-server",
        gpu_vendor="amd",
        gpu_name="AMD Radeon Invented",
        vram_mb=16304,
        cpu="Intel Core i5-13600KF",
        ram_gib=31,
        os="Ubuntu 24",
        accelerator_stack="ROCm 10.1.0",
    )
    assert (
        machine.line() == "invented-server: AMD Radeon Invented (16304 MB), Intel Core i5-13600KF"
    )


def test_an_unknown_machine_is_refused_naming_the_known_ones(tmp_path: Path) -> None:
    """
    Given  a registry holding one machine
    When   another id is asked for, as a typo on the command line would
    Then   the refusal names the ids the registry does hold, so the typo is easy to see
    """
    _write(tmp_path, "invented-server.json", _machine())

    with pytest.raises(ValueError, match="unknown machine 'invented-serverr'"):
        require_machine(tmp_path, "invented-serverr")

    with pytest.raises(ValueError, match="the registry holds: invented-server"):
        require_machine(tmp_path, "invented-serverr")


def test_two_files_naming_one_id_are_refused(tmp_path: Path) -> None:
    """
    Given  two machine files that name the same id
    When   the registry is read
    Then   it is refused, because one id would otherwise mean two machines
    """
    _write(tmp_path, "one.json", _machine())
    _write(tmp_path, "two.json", _machine(cpu="AMD Ryzen 7 5800H"))

    with pytest.raises(FormatError, match="the id 'invented-server' is already in the registry"):
        known_machines(tmp_path)


def test_a_file_of_another_format_is_refused(tmp_path: Path) -> None:
    """
    Given  a machine file written by another version of the registry
    When   it is read
    Then   it is refused instead of guessed at
    """
    path = _write(tmp_path, "invented-server.json", _machine(format=2))

    with pytest.raises(FormatError, match="format 2, this reader knows format 1"):
        read_machine(path)


def test_a_file_without_its_card_is_refused(tmp_path: Path) -> None:
    """
    Given  a machine file with no card
    When   it is read
    Then   it is refused, because a machine without a card says nothing about what fits on it
    """
    data = _machine()
    del data["gpu"]
    path = _write(tmp_path, "invented-server.json", data)

    with pytest.raises(FormatError, match="no gpu object"):
        read_machine(path)


def test_the_committed_registry_reads_and_no_alias_is_an_id() -> None:
    """
    Given  the registry in the repository
    When   it is read, and an alias of a machine is asked for by that name
    Then   every machine reads, and the alias is refused: it is documentation of an old label,
           not a second id
    """
    known = known_machines(Path("."))

    assert known, "the committed registry is empty"
    for machine in known.values():
        assert machine.id not in machine.aliases
        assert machine.gpu_name
    aliases = {alias for machine in known.values() for alias in machine.aliases}
    assert aliases
    for alias in aliases:
        assert alias not in known
        with pytest.raises(ValueError, match="unknown machine"):
            require_machine(Path("."), alias)


def test_the_devices_are_the_card_and_the_processor() -> None:
    """
    Given  the devices a measurement can be made on
    When   they are read
    Then   they are the card and the processor, the two a machine is measured on
    """
    assert DEVICES == ("gpu", "cpu")
