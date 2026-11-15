#!/usr/bin/env python3
"""Cross-platform PyInstaller build script for Teacher's Teammate.

Usage (run from the repo root):
    python tools/build/build_standalone.py

The bundle's contents live in the committed spec next to this file
(``teachers-teammate.spec``); this script generates the platform icons and runs it.

Outputs:
    dist/teachers-teammate                        (Linux onefile)
    dist/teachers-teammate/teachers-teammate.exe  (Windows onedir)
    dist/teachers-teammate.app/                   (macOS onedir .app bundle)
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = Path(__file__).resolve().parent / "teachers-teammate.spec"
ASSETS = ROOT / "teachers_teammate" / "assets"


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


def main() -> None:
    # Windows default console encoding (cp1252) cannot represent some Unicode
    # chars used in the progress messages below.  Force UTF-8 so the output is
    # readable in all CI and terminal environments.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    _ensure_icons()
    print("==> Building standalone binary…")
    _run()
    if sys.platform == "darwin":
        label = "dist/teachers-teammate.app"
    elif sys.platform == "win32":
        label = "dist/teachers-teammate/teachers-teammate.exe"
    else:
        label = "dist/teachers-teammate"
    print(f"    done → {label}\n")


if __name__ == "__main__":
    main()
