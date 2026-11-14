"""The Linux bundle check: which libraries the .deb has to depend on."""

from __future__ import annotations

import subprocess

import pytest

from tools.build import check_linux_bundle


def test_missing_sonames_leaves_out_what_the_bundle_carries_and_glibc() -> None:
    """
    Given  needed library names, some bundled and some belonging to glibc
    When   the missing ones are worked out
    Then   only the libraries the system has to provide remain
    """
    needed = {"libc.so.6", "libm.so.6", "ld-linux-x86-64.so.2", "libz.so.1", "libEGL.so.1"}

    assert check_linux_bundle.missing_sonames(needed, {"libz.so.1"}) == ["libEGL.so.1"]


def _fake_run(listing: str, owners: dict[str, str]):
    def run(command, **_kwargs):
        if command[0] == "ldconfig":
            return subprocess.CompletedProcess(command, 0, stdout=listing, stderr="")
        owner = owners.get(command[-1])
        if owner is None:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="no path found")
        return subprocess.CompletedProcess(command, 0, stdout=f"{owner}: {command[-1]}\n", stderr="")

    return run


_LISTING = (
    "\tlibxcb.so.1 (libc6,x86-64) => /lib/x86_64-linux-gnu/libxcb.so.1\n"
    "\tlibxcb.so.1 (libc6) => /lib32/libxcb.so.1\n"
    "\tlibEGL.so.1 (libc6,x86-64) => /lib/x86_64-linux-gnu/libEGL.so.1\n"
)


def test_debian_packages_maps_sonames_to_the_owning_packages(monkeypatch) -> None:
    """
    Given  an ldconfig listing and dpkg owners, one of them only under /usr
    When   the packages for two sonames are looked up
    Then   both owners are returned, sorted, from the 64-bit entries
    """
    owners = {
        "/lib/x86_64-linux-gnu/libxcb.so.1": "libxcb1:amd64",
        "/usr/lib/x86_64-linux-gnu/libEGL.so.1": "libegl1:amd64",
    }
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", _fake_run(_LISTING, owners))

    result = check_linux_bundle.debian_packages(["libxcb.so.1", "libEGL.so.1"])

    assert result == ["libegl1", "libxcb1"]


def test_debian_packages_names_every_library_it_cannot_map(monkeypatch) -> None:
    """
    Given  one library that is not installed and one that no package owns
    When   the packages are looked up
    Then   a single error names both
    """
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", _fake_run(_LISTING, {}))

    with pytest.raises(RuntimeError) as error:
        check_linux_bundle.debian_packages(["libxcb.so.1", "libgone.so.9"])

    message = str(error.value)
    assert "libgone.so.9 is not installed here" in message
    assert "no Debian package owns /lib/x86_64-linux-gnu/libxcb.so.1" in message
