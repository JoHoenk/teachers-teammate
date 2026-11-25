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

``--host-libs`` checks the libraries the bundle took from the build machine rather than from a
wheel.  The .deb does not ship those: ``teachers-teammate.spec`` leaves out every one a Debian
package owns (:func:`system_libraries`), and ``--depends`` names the packages instead, so the
distribution keeps them current and carries their sources.  What may stay at the top of the
bundle is a ``[host_rows]`` library (the interpreter's own).  A library the policy's
``[host_deny]`` names, or one no Debian package owns, fails the build.

``--strip-debug`` and ``--debug-info`` are about size.  The Python that ``actions/setup-python``
installs ships ``libpython3.x.so`` and its extension modules with their debug information
(about 24 MB of a 32 MB library), and PyInstaller copies them as they are.  ``--strip-debug``
removes it from the bundle's copy (never the main program, which carries PyInstaller's archive,
and never the build machine's files); ``--debug-info`` fails while any ELF file in the bundle
still has it, so the saving cannot be lost unnoticed.

Usage (from the repo root, after build_standalone.py):

    python tools/build/check_linux_bundle.py --max-glibc 2.39
    python tools/build/check_linux_bundle.py --unused
    python tools/build/check_linux_bundle.py --depends
    python tools/build/check_linux_bundle.py --strip-debug
    python tools/build/check_linux_bundle.py --debug-info
    python tools/build/check_linux_bundle.py --host-libs
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


#: The folder build, and the folder inside it that holds everything but the main program.
BUNDLE = ROOT / "dist" / "teachers-teammate"

_ELF_MAGIC = b"\x7fELF"


def is_elf(path: Path) -> bool:
    """Return whether *path* is a regular file that starts with the ELF magic number."""
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(4) == _ELF_MAGIC


def bundle_elf_files(bundle: Path) -> list[Path]:
    """Return every ELF file below ``bundle/_internal``, sorted.

    The main program beside ``_internal`` is left out: PyInstaller appends its archive to it, and
    ``strip`` would drop that.
    """
    return sorted(path for path in (bundle / "_internal").rglob("*") if is_elf(path))


def has_debug_info(path: Path) -> bool:
    """Return whether the ELF file *path* has a ``.debug_info`` section, read from ``readelf``."""
    result = subprocess.run(
        ["readelf", "-S", "--wide", str(path)], capture_output=True, text=True, check=False
    )
    return result.returncode == 0 and ".debug_info" in result.stdout


def with_debug_info(bundle: Path) -> list[Path]:
    """Return the ELF files of *bundle* that still carry debug information."""
    return [path for path in bundle_elf_files(bundle) if has_debug_info(path)]


def strip_debug_info(bundle: Path) -> list[Path]:
    """Remove the debug information from the ELF files of *bundle*; return the ones changed.

    Raises:
        RuntimeError: ``strip`` failed on a file.
    """
    changed: list[Path] = []
    for path in with_debug_info(bundle):
        result = subprocess.run(
            ["strip", "--strip-debug", str(path)], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"strip failed on {path}: {result.stderr.strip()}")
        changed.append(path)
    return changed


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
            "(see the Qt system libraries step in .github/workflows/build_package.yml)."
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


def _row_of(name: str, rows: dict[str, str]) -> str | None:
    """Return the inventory row that owns the host library *name*, or ``None``."""
    return next((component for prefix, component in rows.items() if name.startswith(prefix)), None)


def system_libraries(
    files: list[tuple[str, Path]], deny: tuple[str, ...], rows: dict[str, str]
) -> set[str]:
    """Return the destinations of the host libraries the .deb depends on instead of shipping.

    Those are the host libraries a Debian package owns.  A ``[host_rows]`` library (the
    interpreter's own) stays, and so does a denied one, so that :func:`host_library_problems`
    fails on it instead of the dependency hiding it.
    """
    return {
        name
        for name, source in host_libraries(files)
        if _row_of(name, rows) is None
        and not any(pattern in name for pattern in deny)
        and _debian_owners(source)
    }


def host_library_problems(
    files: list[tuple[str, Path]], deny: tuple[str, ...], rows: dict[str, str]
) -> list[str]:
    """Return what keeps the bundle's host libraries from being accounted for.

    A library ``[host_deny]`` names is one we must not distribute.  Every other one a Debian
    package owns is meant to be left out (:func:`system_libraries`), and one no package owns cannot
    be named in ``Depends:``.  Only a ``[host_rows]`` match (``libpython3.14`` is the interpreter)
    may stay: the inventory row carries its license text.
    """
    problems: list[str] = []
    for name, source in host_libraries(files):
        denied = [pattern for pattern in deny if pattern in name]
        if denied:
            problems.append(f"{name} is a host library we must not ship ({denied[0]})")
        elif _row_of(name, rows) is None:
            owners = _debian_owners(source)
            if owners:
                problems.append(
                    f"{name} ({', '.join(owners)}) is in the bundle; the .deb depends on it "
                    "instead (teachers-teammate.spec leaves it out)"
                )
            else:
                problems.append(f"no Debian package owns {source} (bundled as {name})")
    return problems


def _debug_info_command(bundle: Path, *, strip: bool) -> int:
    """Strip the bundle (``strip``) or check that it is stripped; return the exit code."""
    for tool in ("readelf", "strip"):
        if shutil.which(tool) is None:
            print(f"{tool} is missing; install binutils")
            return 1
    if not (bundle / "_internal").is_dir():
        print(f"{bundle / '_internal'} is missing; run tools/build/build_standalone.py first")
        return 1
    if strip:
        try:
            changed = strip_debug_info(bundle)
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Removed the debug information from {len(changed)} ELF files.")
    left = with_debug_info(bundle)
    if left:
        print("ELF files that still carry debug information:")
        for path in left:
            print(f"  {path.relative_to(bundle)}")
        print("\nRun `python tools/build/check_linux_bundle.py --strip-debug` after PyInstaller.")
        return 1
    print("No ELF file in the bundle carries debug information.")
    return 0


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
        action="store_true",
        help="fail when the bundle ships a library of the build machine (the .deb depends on "
        "those) instead of checking glibc",
    )
    parser.add_argument(
        "--strip-debug",
        action="store_true",
        help="remove the debug information from the bundle's ELF files (run after PyInstaller)",
    )
    parser.add_argument(
        "--debug-info",
        action="store_true",
        help="fail when an ELF file in the bundle still has debug information",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=BUNDLE,
        help="the folder build (default: dist/teachers-teammate)",
    )
    parser.add_argument(
        "--toc",
        type=Path,
        default=None,
        help="the bundle's table of contents (default: the last build's, in pyinstaller-work/)",
    )
    args = parser.parse_args()

    if args.strip_debug or args.debug_info:
        return _debug_info_command(args.bundle, strip=args.strip_debug)

    if shutil.which("objdump") is None:
        print("objdump is missing; install binutils")
        return 1
    toc = args.toc or default_toc()
    if not toc.is_file():
        print(f"{toc} is missing; run tools/build/build_standalone.py first")
        return 1

    files = bundled_elf_files(toc)
    if args.host_libs:
        return _host_libs_command(files)
    if args.depends:
        return _depends_command(files)
    if args.unused:
        return _unused_command(files)
    return _glibc_command(files, args.max_glibc)


def _host_libs_command(files: list[tuple[str, Path]]) -> int:
    """Return 1 when the bundle carries a host library the .deb should depend on instead."""
    if shutil.which("dpkg-query") is None:
        print("dpkg-query is missing; the host libraries can only be attributed on Debian")
        return 1
    deny, rows = host_policy()
    problems = host_library_problems(files, deny, rows)
    if problems:
        print("The bundle carries host libraries it must not:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("The bundle carries no host library but the interpreter's own.")
    return 0


def _depends_command(files: list[tuple[str, Path]]) -> int:
    """Print the .deb ``Depends:`` line; return 1 when it cannot be measured."""
    try:
        print(depends_line(files))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _unused_command(files: list[tuple[str, Path]]) -> int:
    """List the libraries nothing loads; return 1 when there are any."""
    unused = unused_libraries(files)
    if not unused:
        print("Every bundled library is needed by another bundled file.")
        return 0
    print("Bundled libraries that nothing loads:")
    for name in unused:
        print(f"  {name}")
    print("\nDrop them in tools/build/teachers-teammate.spec, or say why they stay.")
    return 1


def _glibc_command(files: list[tuple[str, Path]], max_glibc: str) -> int:
    """Return 1 when a bundled file needs a newer glibc than *max_glibc*."""
    major, minor = (int(part) for part in max_glibc.split("."))
    problems = too_new(files, (major, minor))
    if problems:
        print(f"The bundle needs a newer glibc than {max_glibc}:")
        for problem in problems:
            print(f"  {problem}")
        print("\nBuild on an older distribution, or raise --max-glibc and say so in the docs.")
        return 1
    print(f"{len(files)} bundled ELF files fit glibc {max_glibc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
