#!/usr/bin/env python3
"""Cross-platform PyInstaller build script for Teacher's Teammate.

Usage (run from the repo root):
    python tools/build/build_standalone.py

The bundle's contents live in the committed spec next to this file
(``teachers-teammate.spec``); this script generates the platform icons, writes the add-on
constraints, fetches the model files the app carries itself, writes the Windows version
resource and runs the spec.

Outputs:
    dist/teachers-teammate/teachers-teammate      (Linux folder)
    dist/teachers-teammate/teachers-teammate.exe  (Windows folder)
    dist/teachers-teammate.app/                   (macOS .app bundle)
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = Path(__file__).resolve().parent / "teachers-teammate.spec"
ASSETS = ROOT / "teachers_teammate" / "assets"

# The build environment installs the project, but a developer running this from a plain checkout
# has not: the repo root makes ``tools.build`` importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build import platform_lock  # noqa: E402


def _ensure_assets() -> None:
    """Generate installer assets (ICO, welcome BMP) via make_ico.py on Windows."""
    if sys.platform != "win32":
        return
    script = ROOT / "tools" / "packaging" / "make_ico.py"
    if script.exists():
        subprocess.run([sys.executable, str(script)], check=True, cwd=ROOT)


def _ensure_icns(source_png: Path, out_icns: Path) -> None:
    """Create a macOS .icns file from the canonical PNG source (macOS only)."""
    if sys.platform != "darwin":
        return
    try:
        with tempfile.TemporaryDirectory(prefix="profpet_iconset_") as td:
            iconset = Path(td) / "teachers_teammate.iconset"
            iconset.mkdir(parents=True, exist_ok=True)
            for size in (16, 32, 64, 128, 256, 512):
                subprocess.run(
                    [
                        "sips",
                        "-z",
                        str(size),
                        str(size),
                        str(source_png),
                        "--out",
                        str(iconset / f"icon_{size}x{size}.png"),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                double = size * 2
                subprocess.run(
                    [
                        "sips",
                        "-z",
                        str(double),
                        str(double),
                        str(source_png),
                        "--out",
                        str(iconset / f"icon_{size}x{size}@2x.png"),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(out_icns)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:  # noqa: BLE001
        return


def _ensure_icons() -> None:
    """Generate the platform icon symbols the spec refers to, from the canonical PNG."""
    _ensure_assets()
    source_png = ASSETS / "teachers_teammate_icon.png"
    if sys.platform == "darwin" and source_png.exists():
        _ensure_icns(source_png, ASSETS / "teachers_teammate.icns")


def _write_addon_constraints() -> None:
    """Write the versions the bundle ships, which the installed app gives to pip (the spec adds it)."""
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "addon_constraints.py")],
        check=True,
        cwd=ROOT,
    )


def _write_addon_lock() -> None:
    """Write the add-on versions this release was tested with, with this platform's pins.

    A build that cannot resolve the add-ons fails: a lock without the platform's pins would ship
    them unpinned.
    """
    try:
        count, extra = platform_lock.write_platform_lock()
    except platform_lock.ResolveError as exc:
        raise SystemExit(f"cannot resolve the add-ons for the lock: {exc}") from exc
    if extra:
        print(f"    {len(extra)} of them are this platform's own: {' '.join(extra)}")
    print(f"    wrote {count} add-on pins")


def _fetch_models() -> None:
    """Fetch the model files the app carries itself (the spec adds them)."""
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "fetch_models.py")],
        check=True,
        cwd=ROOT,
    )


def _write_version_resource() -> None:
    """Write the version resource the spec puts into the Windows exe.

    The release version comes from ``$VERSION`` when the workflow sets it — the same string
    ``makensis`` gets — and from the stamped ``__version__`` otherwise.
    """
    if sys.platform != "win32":
        return
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "make_version_resource.py")],
        check=True,
        cwd=ROOT,
    )


def _run() -> None:
    env = os.environ.copy()
    # PySide6 needs a display driver even at analysis time on Linux
    if sys.platform.startswith("linux"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            # Wipe PyInstaller's cache/work dir so a stale analysis can never leak
            # previously-installed packages into a fresh build.
            "--clean",
            "--noconfirm",
            "--workpath",
            "pyinstaller-work",
            "--distpath",
            "dist",
            str(SPEC),
        ],
        check=True,
        cwd=ROOT,
        env=env,
    )


def _strip_debug_info() -> None:
    """Remove the debug information PyInstaller copied from the build machine (Linux only).

    The Python from ``actions/setup-python`` ships ``libpython3.x.so`` and its extension modules
    with debug information: about 46 MB of a 510 MB folder, and 30 MB of the download. Nobody
    debugs the frozen app with it, and ``check_linux_bundle.py --debug-info`` fails the build
    when it comes back. The bundle's copy is stripped, never the build machine's files.
    """
    if not sys.platform.startswith("linux"):
        return
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "check_linux_bundle.py"),
            "--strip-debug",
        ],
        check=True,
        cwd=ROOT,
    )


def _license_destination() -> Path | None:
    """Return the folder that carries the license files, or ``None`` where packaging does it.

    The Windows installer takes the whole folder, so the files land next to the program. The
    macOS app is already signed when PyInstaller finishes, and a file added to it now would break
    the signature, so ``make_dmg.sh`` puts them into a ``Licenses`` folder beside the app. On
    Linux ``make_deb.sh`` puts them into the package's documentation folder.
    """
    if sys.platform == "win32":
        return ROOT / "dist" / "teachers-teammate"
    return None


def _add_license_files() -> None:
    """Copy NOTICE and the full third-party license list into the finished bundle.

    The LGPL needs the notice and the license texts to travel with every copy of Qt.
    """
    destination = _license_destination()
    if destination is None:
        return
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "NOTICE", destination / "NOTICE.txt")
    shutil.copyfile(
        ROOT / "third_party_licenses" / "third_party_license.txt",
        destination / "third_party_license.txt",
    )


def main() -> None:
    # Windows default console encoding (cp1252) cannot represent some Unicode
    # chars used in the progress messages below.  Force UTF-8 so the output is
    # readable in all CI and terminal environments.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")

    _ensure_icons()
    _write_addon_constraints()
    _write_addon_lock()
    _fetch_models()
    _write_version_resource()
    print("==> Building standalone binary…")
    _run()
    _strip_debug_info()
    _add_license_files()
    if sys.platform == "darwin":
        label = "dist/teachers-teammate.app"
    elif sys.platform == "win32":
        label = "dist/teachers-teammate/teachers-teammate.exe"
    else:
        label = "dist/teachers-teammate/teachers-teammate"
    print(f"    done → {label}\n")


if __name__ == "__main__":
    main()
