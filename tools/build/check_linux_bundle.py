#!/usr/bin/env python3
"""Check that the Linux bundle runs on the oldest distribution we support.

PyInstaller bundles the libraries of the machine it builds on, so a build on a newer
distribution silently raises the minimum glibc — and the app then refuses to start on an
older one with a message about ``GLIBC_2.xx not found``. This reads the bundle's table of
contents, runs ``objdump -T`` on every ELF file in it, and fails when one needs a newer
``GLIBC_x.y`` than ``--max-glibc``.

``--depends`` prints the ``Depends:`` line of the ``.deb``: the libraries the bundle needs but
does not carry, as Debian package names, measured from this build on this machine rather than
written by hand. ``tools/packaging/make_deb.sh`` uses it.

``--unused`` answers the other question: which libraries did PyInstaller collect that
nothing in the bundle loads? Those are dead weight, and they are how a dropped Qt plugin
still drags its dependencies in.

``--host-libs`` writes the third file a Linux download carries: the libraries the bundle took
from the build machine rather than from a wheel.  No wheel's license text covers those, so each
one is listed with the Debian package that owns it and that package's copyright file.  A library
the policy's ``[host_deny]`` names, or one no Debian package owns, fails the build.

Usage (from the repo root, after build_standalone.py):

    python tools/build/check_linux_bundle.py --max-glibc 2.39
    python tools/build/check_linux_bundle.py --unused
    python tools/build/check_linux_bundle.py --depends
    python tools/build/check_linux_bundle.py --host-libs dist/teachers-teammate/_internal/third_party_host_libraries.txt
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parent.parent.parent
WORK = ROOT / "pyinstaller-work" / "teachers-teammate"
#: A folder build writes COLLECT-00.toc, a single-file build PKG-00.toc.
TOCS = (WORK / "COLLECT-00.toc", WORK / "PKG-00.toc")

#: The kinds in the table of contents that are ELF files.
_ELF_KINDS = frozenset({"BINARY", "EXTENSION"})

_GLIBC = re.compile(r"GLIBC_(\d+)\.(\d+)")

#: A shared library at the top level of the bundle, as opposed to a Python extension module.
_SHARED_LIBRARY = re.compile(r"^lib.+\.so")

#: Libraries the bootloader opens by name rather than through ``DT_NEEDED``.
_DLOPENED = ("libpython",)

#: auditwheel's hash suffix on a library it renamed: ``libavcodec-e751de63.so.62``.
_HASHED = re.compile(r"-[0-9a-f]{8}\.so")

#: The policy the license inventory owns.  Only its two host tables are read here, so this script
#: does not have to import the inventory and, with it, the whole app.
POLICY = ROOT / "tools" / "licenses" / "policy.toml"

#: The C library and its companions: the package ``libc6`` carries them, with a version bound
#: instead of a name, so they never appear as separate dependencies.
_GLIBC_LIBRARIES = re.compile(
    r"^(ld-linux.*|libc|libm|libdl|libpthread|librt|libutil|libresolv|libnsl)\.so(\.\d+)*$"
)


def default_toc() -> Path:
    """Return the table of contents of the last build, folder build first."""
    for candidate in TOCS:
        if candidate.is_file():
            return candidate
    return TOCS[0]


def bundled_elf_files(toc_path: Path) -> list[tuple[str, Path]]:
    """Return ``(destination, source)`` for every ELF file the bundle carries."""
    contents = ast.literal_eval(toc_path.read_text(encoding="utf-8"))
    # COLLECT-00.toc is a one-tuple holding the list; PKG-00.toc keeps it at index 2.
    entries = contents[2] if len(contents) > 2 else contents[0]
    return [(dest, Path(source)) for dest, source, kind in entries if kind in _ELF_KINDS]


def needed_glibc(path: Path) -> set[tuple[int, int]]:
    """Return the ``GLIBC_x.y`` versions *path* needs, read from ``objdump -T``."""
    result = subprocess.run(
        ["objdump", "-T", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {(int(major), int(minor)) for major, minor in _GLIBC.findall(result.stdout)}


def too_new(files: list[tuple[str, Path]], limit: tuple[int, int]) -> list[str]:
    """Return a line per file that needs a newer glibc than *limit*."""
    problems: list[str] = []
    seen: set[Path] = set()
    for destination, source in files:
        if source in seen or not source.is_file():
            continue
        seen.add(source)
        newest = max(needed_glibc(source), default=None)
        if newest is not None and newest > limit:
            problems.append(f"{destination} needs GLIBC_{newest[0]}.{newest[1]}")
    return sorted(problems)


def needed_sonames(path: Path) -> set[str]:
    """Return the ``DT_NEEDED`` entries of *path*, read from ``objdump -p``."""
    result = subprocess.run(
        ["objdump", "-p", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {
        line.split()[-1] for line in result.stdout.splitlines() if line.strip().startswith("NEEDED")
    }


def unused_libraries(files: list[tuple[str, Path]]) -> list[str]:
    """Return the top-level shared libraries that no other bundled file needs."""
    needed: set[str] = set()
    for _, source in files:
        if source.is_file():
            needed |= needed_sonames(source)
    unused = []
    for destination, _ in files:
        name = destination.rsplit("/", 1)[-1]
        if "/" in destination or not _SHARED_LIBRARY.match(name):
            continue
        if name in needed or name.startswith(_DLOPENED):
            continue
        unused.append(destination)
    return sorted(set(unused))


def missing_sonames(needed: set[str], bundled: set[str]) -> list[str]:
    """Return the ``DT_NEEDED`` names the bundle neither carries nor leaves to glibc."""
    return sorted(name for name in needed - bundled if not _GLIBC_LIBRARIES.match(name))


def debian_packages(sonames: list[str]) -> list[str]:
    """Return the Debian packages that provide *sonames* on this machine (``ldconfig``, ``dpkg``)."""
    listing = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, check=False).stdout
    paths: dict[str, str] = {}
    for line in listing.splitlines():
        match = re.match(r"\s*(\S+) \(([^)]*)\) => (\S+)", line)
        if match and "x86-64" in match.group(2):
            paths.setdefault(match.group(1), match.group(3))
    packages: set[str] = set()
    problems: list[str] = []
    for soname in sonames:
        path = paths.get(soname)
        if path is None:
            problems.append(f"{soname} is not installed here")
            continue
        # A merged-/usr system lists /lib/... while dpkg may know /usr/lib/... (or the reverse).
        for candidate in (path, str(Path(path).resolve()), "/usr" + path):
            owner = subprocess.run(
                ["dpkg-query", "-S", candidate], capture_output=True, text=True, check=False
            )
            if owner.returncode == 0:
                packages.update(_owner_names(owner.stdout))
                break
        else:
            problems.append(f"no Debian package owns {path} (needed as {soname})")
    if problems:
        raise RuntimeError(
            "The Depends: line cannot be measured:\n  "
            + "\n  ".join(problems)
            + "\nInstall the libraries the bundle links against on the build machine first "
            "(see the Qt system libraries step in .github/workflows/build_artifacts.yml)."
        )
    return sorted(packages)


def depends_line(files: list[tuple[str, Path]]) -> str:
    """Return the ``Depends:`` value: glibc at the floor the bundle needs, plus what it links."""
    needed: set[str] = set()
    newest = (2, 0)
    for _, source in files:
        if source.is_file():
            needed |= needed_sonames(source)
            newest = max(newest, max(needed_glibc(source), default=(2, 0)))
    bundled = {destination.rsplit("/", 1)[-1] for destination, _ in files}
    packages = debian_packages(missing_sonames(needed, bundled))
    return ", ".join([f"libc6 (>= {newest[0]}.{newest[1]})", *packages])


def host_policy(path: Path = POLICY) -> tuple[tuple[str, ...], dict[str, str]]:
    """Return the policy's ``[host_deny]`` patterns and its ``[host_rows]`` map."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return (
        tuple(data.get("host_deny", {}).get("patterns", [])),
        dict(data.get("host_rows", {})),
    )


def host_libraries(files: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """Return the top-level shared libraries that came from the build machine.

    A library inside a package folder (``opencv_python_headless.libs/``) or renamed by
    auditwheel (``libavcodec-e751de63.so.62``) came out of a wheel, so a bundle row's license
    text covers it.  What is left at the top of the bundle is the build machine's own.
    """
    return sorted(
        (destination, source)
        for destination, source in files
        if "/" not in destination
        and _SHARED_LIBRARY.match(destination)
        and not _HASHED.search(destination)
    )


def _owner_names(output: str) -> list[str]:
    """Return the package names in ``dpkg-query -S`` output.

    The line is ``pkg: /path``, or ``pkg1, pkg2:amd64: /path`` when several packages own the file.
    """
    head = output.splitlines()[0].rpartition(": ")[0] if output.strip() else ""
    return [name.strip().split(":")[0] for name in head.split(", ") if name.strip()]


def _debian_owners(path: Path) -> list[str]:
    """Return the Debian packages that own *path* on this machine; empty when none does."""
    for candidate in (str(path), str(path.resolve()), "/usr" + str(path)):
        owner = subprocess.run(
            ["dpkg-query", "-S", candidate], capture_output=True, text=True, check=False
        )
        if owner.returncode == 0:
            return _owner_names(owner.stdout)
    return []


def _source_package(package: str) -> tuple[str, str]:
    """Return ``(source package, version)`` of a Debian binary package."""
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Source}\t${Version}", package],
        capture_output=True,
        text=True,
        check=False,
    )
    source, _, version = result.stdout.partition("\t")
    # ${Source} is empty when the binary package is its own source, and carries the version in
    # parentheses when the two differ.
    return (source.split(" ")[0] if source else package), version.strip()


def _copyright(package: str) -> str | None:
    """Return the copyright file of a Debian package, or ``None`` when it has none."""
    path = Path("/usr/share/doc") / package / "copyright"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip()


_HOST_HEADER = """\
# Host libraries (Linux)

The Linux bundle carries these libraries from the build machine: the distribution provides
them, so no Python wheel brings them and no wheel's license text covers them. Each entry names
the Debian package that owns the library and reproduces that package's copyright file, which
states the license and the source.

Generated by `tools/build/check_linux_bundle.py --host-libs` on the build machine, so it is not
in the repository: the runner image gets security updates, and a committed list would go stale.
"""


def host_library_report(
    files: list[tuple[str, Path]], deny: tuple[str, ...], rows: dict[str, str]
) -> tuple[str, list[str]]:
    """Return the text of the host-library file and the problems that must stop the build.

    A library ``[host_deny]`` names is one we must not distribute; one no Debian package owns
    cannot be attributed at all, and neither can one whose package has no copyright file.  A ``[host_rows]`` match belongs to an inventory row instead
    (``libpython3.14`` is the interpreter), so it is named but needs no copyright file.
    """
    problems: list[str] = []
    sections: list[str] = []
    for name, source in host_libraries(files):
        denied = [pattern for pattern in deny if pattern in name]
        if denied:
            problems.append(f"{name} is a host library we must not ship ({denied[0]})")
            continue
        row = next(
            (component for prefix, component in rows.items() if name.startswith(prefix)), None
        )
        if row is not None:
            sections.append(
                f"## {name}\n\n"
                f"Belongs to the `{row}` row of the license inventory, which carries its "
                f"license text.\n"
            )
            continue
        packages = _debian_owners(source)
        if not packages:
            problems.append(f"no Debian package owns {source} (bundled as {name})")
            continue
        for package in packages:
            text = _copyright(package)
            if text is None:
                problems.append(f"{package} has no copyright file (owns {name})")
                continue
            source_name, version = _source_package(package)
            sections.append(
                f"## {name}\n\n"
                f"Debian package: `{package}` {version}  \n"
                f"Source package: `{source_name}` {version}\n\n"
                f"````\n{text}\n````\n"
            )
    if problems:
        return "", problems
    return _HOST_HEADER + "\n" + "\n".join(sections), []


def main() -> int:
    """Check the bundle against ``--max-glibc``; return 0 when every file fits."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-glibc",
        default="2.39",
        help="the oldest glibc the bundle may need (default: 2.39, Ubuntu 24.04)",
    )
    parser.add_argument(
        "--unused",
        action="store_true",
        help="list the bundled libraries nothing loads instead of checking glibc",
    )
    parser.add_argument(
        "--depends",
        action="store_true",
        help="print the .deb Depends: line measured from the bundle instead of checking glibc",
    )
    parser.add_argument(
        "--host-libs",
        type=Path,
        default=None,
        metavar="OUT",
        help="write the libraries the bundle took from the build machine, with their Debian "
        "copyright files, to OUT instead of checking glibc",
    )
    parser.add_argument(
        "--toc",
        type=Path,
        default=None,
        help="the bundle's table of contents (default: the last build's, in pyinstaller-work/)",
    )
    args = parser.parse_args()

    if shutil.which("objdump") is None:
        print("objdump is missing; install binutils")
        return 1
    toc = args.toc or default_toc()
    if not toc.is_file():
        print(f"{toc} is missing; run tools/build/build_standalone.py first")
        return 1

    files = bundled_elf_files(toc)
    if args.host_libs is not None:
        if shutil.which("dpkg-query") is None:
            print("dpkg-query is missing; the host libraries can only be attributed on Debian")
            return 1
        deny, rows = host_policy()
        text, problems = host_library_report(files, deny, rows)
        if problems:
            print("The bundle carries host libraries we cannot account for:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
        args.host_libs.parent.mkdir(parents=True, exist_ok=True)
        args.host_libs.write_text(text, encoding="utf-8")
        print(f"Wrote {args.host_libs}")
        return 0
    if args.depends:
        try:
            print(depends_line(files))
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    if args.unused:
        unused = unused_libraries(files)
        if not unused:
            print("Every bundled library is needed by another bundled file.")
            return 0
        print("Bundled libraries that nothing loads:")
        for name in unused:
            print(f"  {name}")
        print("\nDrop them in tools/build/teachers-teammate.spec, or say why they stay.")
        return 1

    limit = tuple(int(part) for part in args.max_glibc.split("."))
    problems = too_new(files, limit)
    if problems:
        print(f"The bundle needs a newer glibc than {args.max_glibc}:")
        for problem in problems:
            print(f"  {problem}")
        print("\nBuild on an older distribution, or raise --max-glibc and say so in the docs.")
        return 1
    print(f"{len(files)} bundled ELF files fit glibc {args.max_glibc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
