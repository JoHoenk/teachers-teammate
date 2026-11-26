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


def _fake_owners(monkeypatch, owners: dict[str, str | list[str]]) -> None:
    """Point the Debian lookup at *owners* instead of this machine."""

    def owners_of(path: Path) -> list[str]:
        owner = owners.get(str(path), [])
        return [owner] if isinstance(owner, str) else owner

    monkeypatch.setattr(check_linux_bundle, "_debian_owners", owners_of)


def _fake_linked(monkeypatch, sonames: set[str]) -> None:
    """Make every bundled file link against *sonames*, instead of reading them with objdump."""
    monkeypatch.setattr(check_linux_bundle, "needed_sonames", lambda path: set(sonames))


_GCC = ("libgcc_s.so.1", Path("/usr/lib/x86_64-linux-gnu/libgcc_s.so.1"))
_PYTHON = ("libpython3.14.so.1.0", Path("/opt/hostedtoolcache/Python/lib/libpython3.14.so.1.0"))
_READLINE = ("libreadline.so.8", Path("/usr/lib/x86_64-linux-gnu/libreadline.so.8"))


def test_system_libraries_takes_what_a_debian_package_owns(monkeypatch) -> None:
    """
    Given  a bundle with a library a Debian package owns, a wheel's library and the interpreter,
           all linked against by some bundled file
    When   the libraries the .deb depends on are worked out
    Then   only the owned host library is named, so the interpreter and the wheel's file stay
    """
    _fake_owners(monkeypatch, {str(_GCC[1]): "libgcc-s1"})
    _fake_linked(monkeypatch, {_GCC[0], _PYTHON[0], "libpng16-c770ec27.so.16"})
    files = [
        _GCC,
        _PYTHON,
        ("opencv_python_headless.libs/libpng16-c770ec27.so.16", Path("/tmp/wheel/libpng16.so")),
    ]

    result = check_linux_bundle.system_libraries(files, (), {"libpython3.14": "python"})

    assert result == {"libgcc_s.so.1"}


def test_system_libraries_keeps_a_library_nothing_links_against(monkeypatch) -> None:
    """
    Given  a library a Debian package owns that no bundled file links against
    When   the libraries the .deb depends on are worked out
    Then   it is not among them: Depends: would never name it, so only a run-time open could
           reach it, and the check must see it in the bundle
    """
    _fake_owners(monkeypatch, {str(_GCC[1]): "libgcc-s1"})
    _fake_linked(monkeypatch, set())

    assert check_linux_bundle.system_libraries([_GCC], (), {}) == set()


def test_system_libraries_keeps_a_denied_library_so_the_check_fails_on_it(monkeypatch) -> None:
    """
    Given  a bundle that carries libreadline, which the policy denies, and a Debian package owns
    When   the libraries the .deb depends on are worked out
    Then   it is not among them: it must not be needed at all, so the check has to fail on it
    """
    _fake_owners(monkeypatch, {str(_READLINE[1]): "libreadline8t64"})
    _fake_linked(monkeypatch, {_READLINE[0]})

    assert check_linux_bundle.system_libraries([_READLINE], ("libreadline",), {}) == set()


def test_system_libraries_leaves_a_library_no_package_owns(monkeypatch) -> None:
    """
    Given  a host library that no Debian package owns
    When   the libraries the .deb depends on are worked out
    Then   it is not among them, because Depends: cannot name it
    """
    _fake_owners(monkeypatch, {})
    _fake_linked(monkeypatch, {"libmystery.so.1"})
    files = [("libmystery.so.1", Path("/opt/vendor/libmystery.so.1"))]

    assert check_linux_bundle.system_libraries(files, (), {}) == set()


def test_host_library_problems_accepts_the_interpreter_alone(monkeypatch) -> None:
    """
    Given  a bundle whose only host library is libpython3.14, which the policy maps to a row
    When   the host libraries are checked
    Then   there is nothing to report
    """
    _fake_owners(monkeypatch, {})

    assert (
        check_linux_bundle.host_library_problems([_PYTHON], (), {"libpython3.14": "python"}) == []
    )


def test_host_library_problems_fails_on_an_owned_library_nothing_links_against(
    monkeypatch,
) -> None:
    """
    Given  a bundle that still carries a library a Debian package owns, because nothing links
           against it and the spec left it in
    When   the host libraries are checked
    Then   the build fails and names the library and its package
    """
    _fake_owners(monkeypatch, {str(_GCC[1]): "libgcc-s1"})

    problems = check_linux_bundle.host_library_problems([_GCC], (), {})

    assert len(problems) == 1
    assert "libgcc_s.so.1 (libgcc-s1) is in the bundle" in problems[0]
    assert "if none does, drop it there" in problems[0]


def test_host_library_problems_fails_on_a_denied_library(monkeypatch) -> None:
    """
    Given  a bundle that carries libreadline, which the policy denies
    When   the host libraries are checked
    Then   the build fails and names the library and the pattern, not the package
    """
    _fake_owners(monkeypatch, {str(_READLINE[1]): "libreadline8t64"})

    problems = check_linux_bundle.host_library_problems([_READLINE], ("libreadline",), {})

    assert problems == ["libreadline.so.8 is a host library we must not ship (libreadline)"]


def test_host_library_problems_fails_on_a_library_no_package_owns(monkeypatch) -> None:
    """
    Given  a host library that no Debian package owns
    When   the host libraries are checked
    Then   the build fails, because the .deb can neither ship it unnoticed nor depend on it
    """
    _fake_owners(monkeypatch, {})
    files = [("libmystery.so.1", Path("/opt/vendor/libmystery.so.1"))]

    problems = check_linux_bundle.host_library_problems(files, (), {})

    assert problems == [
        "no Debian package owns /opt/vendor/libmystery.so.1 (bundled as libmystery.so.1)"
    ]


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


def _bundle(tmp_path: Path) -> Path:
    """A folder build: the main program, two ELF libraries, a symlink and a data file."""
    bundle = tmp_path / "teachers-teammate"
    internal = bundle / "_internal"
    internal.mkdir(parents=True)
    (bundle / "teachers-teammate").write_bytes(b"\x7fELF main program with an appended archive")
    (internal / "libpython3.14.so.1.0").write_bytes(b"\x7fELF debug")
    (internal / "libclean.so.1").write_bytes(b"\x7fELF clean")
    (internal / "libclean.so").symlink_to("libclean.so.1")
    (internal / "base_library.zip").write_bytes(b"PK data")
    return bundle


def _fake_binutils(with_debug: set[str], fail_strip: bool = False):
    """Stand in for readelf and strip; the second element records the files strip saw."""
    stripped: list[str] = []

    def run(command, **_kwargs):
        name = Path(command[-1]).name
        if command[0] == "readelf":
            sections = ".text\n.debug_info\n" if name in with_debug else ".text\n"
            return subprocess.CompletedProcess(command, 0, stdout=sections, stderr="")
        stripped.append(name)
        code = 1 if fail_strip else 0
        return subprocess.CompletedProcess(command, code, stdout="", stderr="bad file")

    return run, stripped


def test_bundle_elf_files_skips_the_main_program_symlinks_and_data(tmp_path: Path) -> None:
    """
    Given  a folder build with a main program, ELF libraries, a symlink and a zip
    When   the ELF files are listed
    Then   only the real ELF files below _internal remain (the main program carries
           PyInstaller's archive, which strip would drop)
    """
    found = check_linux_bundle.bundle_elf_files(_bundle(tmp_path))

    assert [path.name for path in found] == ["libclean.so.1", "libpython3.14.so.1.0"]


def test_with_debug_info_names_the_files_that_still_have_it(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a bundle in which only libpython has a .debug_info section
    When   the files with debug information are listed
    Then   only libpython is named
    """
    run, _ = _fake_binutils({"libpython3.14.so.1.0"})
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)

    left = check_linux_bundle.with_debug_info(_bundle(tmp_path))

    assert [path.name for path in left] == ["libpython3.14.so.1.0"]


def test_strip_debug_info_touches_only_files_that_have_it(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a bundle in which only libpython has debug information
    When   the debug information is stripped
    Then   strip runs on libpython alone and reports it as changed
    """
    run, stripped = _fake_binutils({"libpython3.14.so.1.0"})
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)

    changed = check_linux_bundle.strip_debug_info(_bundle(tmp_path))

    assert stripped == ["libpython3.14.so.1.0"]
    assert [path.name for path in changed] == ["libpython3.14.so.1.0"]


def test_strip_debug_info_reports_a_failing_strip(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a strip that fails on a file
    When   the debug information is stripped
    Then   a RuntimeError names the file, so the build stops instead of shipping it
    """
    run, _ = _fake_binutils({"libpython3.14.so.1.0"}, fail_strip=True)
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)

    with pytest.raises(RuntimeError, match=r"libpython3\.14\.so\.1\.0"):
        check_linux_bundle.strip_debug_info(_bundle(tmp_path))


def test_debug_info_command_fails_while_a_file_has_it(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a bundle with debug information in libpython
    When   the --debug-info check runs
    Then   it returns 1 and lists the file, and after --strip-debug the same check passes
    """
    run, _ = _fake_binutils({"libpython3.14.so.1.0"})
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", run)
    monkeypatch.setattr(check_linux_bundle.shutil, "which", lambda name: f"/usr/bin/{name}")
    bundle = _bundle(tmp_path)

    assert check_linux_bundle._debug_info_command(bundle, strip=False) == 1
    assert "libpython3.14.so.1.0" in capsys.readouterr().out

    clean, _ = _fake_binutils(set())
    monkeypatch.setattr(check_linux_bundle.subprocess, "run", clean)
    assert check_linux_bundle._debug_info_command(bundle, strip=True) == 0
