"""The Linux bundle check: which libraries the .deb has to depend on."""

from __future__ import annotations

from pathlib import Path
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
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{owner}: {command[-1]}\n", stderr=""
        )

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


def test_host_libraries_leaves_out_what_a_wheel_brought() -> None:
    """
    Given  a bundle's ELF files: two top-level libraries, one auditwheel-renamed library and
    one inside a package folder
    When   the host libraries are worked out
    Then   only the two top-level ones remain — the others came out of a wheel
    """
    files = [
        ("libgcc_s.so.1", Path("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1")),
        ("libpython3.14.so.1.0", Path("/usr/lib/x86_64-linux-gnu/libpython3.14.so.1.0")),
        ("libavcodec-e751de63.so.62", Path("/tmp/wheel/libavcodec-e751de63.so.62")),
        ("opencv_python_headless.libs/libpng16-c770ec27.so.16", Path("/tmp/wheel/libpng16.so")),
        ("teachers_teammate/gui/__init__.py", Path("/tmp/gui/__init__.py")),
    ]

    assert check_linux_bundle.host_libraries(files) == [
        ("libgcc_s.so.1", Path("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1")),
        ("libpython3.14.so.1.0", Path("/usr/lib/x86_64-linux-gnu/libpython3.14.so.1.0")),
    ]


def test_host_policy_reads_the_two_tables(tmp_path: Path) -> None:
    """
    Given  a policy file with the two host tables
    When   the host policy is read
    Then   the deny patterns and the row map come back, and a missing table is empty
    """
    policy = tmp_path / "policy.toml"
    policy.write_text(
        '[host_deny]\npatterns = ["libreadline", "libgdbm"]\n\n'
        '[host_rows]\n"libpython3.14" = "python"\n',
        encoding="utf-8",
    )

    assert check_linux_bundle.host_policy(policy) == (
        ("libreadline", "libgdbm"),
        {"libpython3.14": "python"},
    )
    empty = tmp_path / "empty.toml"
    empty.write_text("", encoding="utf-8")
    assert check_linux_bundle.host_policy(empty) == ((), {})


def _fake_host_lookup(
    monkeypatch, owners: dict[str, str | list[str]], copyrights: dict[str, str]
) -> None:
    """Point the Debian lookups at *owners* and *copyrights* instead of this machine."""

    def owners_of(path: Path) -> list[str]:
        owner = owners.get(str(path), [])
        return [owner] if isinstance(owner, str) else owner

    monkeypatch.setattr(check_linux_bundle, "_debian_owners", owners_of)
    monkeypatch.setattr(
        check_linux_bundle,
        "_source_package",
        lambda package: (f"{package}-source", "1.2-3"),
    )
    monkeypatch.setattr(check_linux_bundle, "_copyright", lambda package: copyrights.get(package))


def test_host_library_report_names_the_package_and_its_copyright(monkeypatch) -> None:
    """
    Given  a bundle with one host library, a Debian package that owns it and its copyright file
    When   the report is written
    Then   the library's section names the package, the source package and the copyright text
    """
    _fake_host_lookup(
        monkeypatch,
        {"/usr/lib/x86_64-linux-gnu/libgcc_s.so.1": "libgcc-s1"},
        {"libgcc-s1": "GPL-3 with the GCC runtime exception"},
    )
    files = [("libgcc_s.so.1", Path("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1"))]

    text, problems = check_linux_bundle.host_library_report(files, (), {})

    assert problems == []
    assert "## libgcc_s.so.1" in text
    assert "Debian package: `libgcc-s1` 1.2-3" in text
    assert "Source package: `libgcc-s1-source` 1.2-3" in text
    assert "GPL-3 with the GCC runtime exception" in text


def test_host_library_report_fails_on_a_denied_library(monkeypatch) -> None:
    """
    Given  a bundle that carries libreadline, which the policy denies
    When   the report is written
    Then   the build fails and names the library and the pattern
    """
    _fake_host_lookup(monkeypatch, {}, {})
    files = [("libreadline.so.8", Path("/usr/lib/x86_64-linux-gnu/libreadline.so.8"))]

    text, problems = check_linux_bundle.host_library_report(files, ("libreadline",), {})

    assert text == ""
    assert problems == ["libreadline.so.8 is a host library we must not ship (libreadline)"]


def test_host_library_report_fails_on_a_library_no_package_owns(monkeypatch) -> None:
    """
    Given  a host library that no Debian package owns
    When   the report is written
    Then   the build fails, because we could not say what its license is
    """
    _fake_host_lookup(monkeypatch, {}, {})
    files = [("libmystery.so.1", Path("/opt/vendor/libmystery.so.1"))]

    text, problems = check_linux_bundle.host_library_report(files, (), {})

    assert text == ""
    assert problems == [
        "no Debian package owns /opt/vendor/libmystery.so.1 (bundled as libmystery.so.1)"
    ]


def test_host_library_report_points_a_host_row_at_the_inventory(monkeypatch) -> None:
    """
    Given  a bundle that carries libpython3.14, which the policy maps to the python row
    When   the report is written
    Then   it names the row instead of a Debian package, and needs no copyright file
    """
    _fake_host_lookup(monkeypatch, {}, {})
    files = [("libpython3.14.so.1.0", Path("/usr/lib/x86_64-linux-gnu/libpython3.14.so.1.0"))]

    text, problems = check_linux_bundle.host_library_report(files, (), {"libpython3.14": "python"})

    assert problems == []
    assert "Belongs to the `python` row" in text
    assert "Source package" not in text


def test_host_library_report_fails_on_a_package_without_a_copyright_file(monkeypatch) -> None:
    """
    Given  a host library whose Debian package has no copyright file
    When   the report is written
    Then   the build fails, because the library would ship without its license
    """
    _fake_host_lookup(monkeypatch, {"/usr/lib/libx.so.1": "libx1"}, {})
    files = [("libx.so.1", Path("/usr/lib/libx.so.1"))]

    text, problems = check_linux_bundle.host_library_report(files, (), {})

    assert text == ""
    assert problems == ["libx1 has no copyright file (owns libx.so.1)"]


def test_host_library_report_names_every_package_that_owns_a_library(monkeypatch) -> None:
    """
    Given  a host library that two Debian packages own
    When   the report is written
    Then   both packages get their section
    """
    _fake_host_lookup(
        monkeypatch,
        {"/usr/lib/libx.so.1": ["libx1", "libx-common"]},
        {"libx1": "first terms", "libx-common": "second terms"},
    )
    files = [("libx.so.1", Path("/usr/lib/libx.so.1"))]

    text, problems = check_linux_bundle.host_library_report(files, (), {})

    assert problems == []
    assert "first terms" in text
    assert "second terms" in text


def test_owner_names_reads_one_and_several_packages() -> None:
    """
    Given  dpkg-query -S output for a file with one owner and one with two, one multiarch
    When   the names are read
    Then   each package is named without its architecture
    """
    assert check_linux_bundle._owner_names("libgcc-s1:amd64: /lib/libgcc_s.so.1\n") == ["libgcc-s1"]
    assert check_linux_bundle._owner_names("libx1, libx-common: /usr/lib/libx.so.1\n") == [
        "libx1",
        "libx-common",
    ]
    assert check_linux_bundle._owner_names("") == []
