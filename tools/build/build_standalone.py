#!/usr/bin/env python3
"""Cross-platform PyInstaller build script for Teacher's Teammate.

Usage (run from the repo root):
    python tools/build/build_standalone.py

Outputs:
    dist/teachers-teammate                        (Linux onefile)
    dist/teachers-teammate/teachers-teammate.exe     (Windows onedir)
    dist/teachers-teammate.app/                   (macOS onedir .app bundle)
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent.parent

# ── Flags shared by every build ────────────────────────────────────────────

_COMMON: list[str] = [
    "--noupx",
    # Wipe PyInstaller's cache/work dir so a stale analysis can never leak
    # previously-installed packages into a fresh build.
    "--clean",
    "--workpath",
    "pyinstaller-work",
    "--paths",
    ".",
    # ':' separator works on all platforms with PyInstaller >= 6.0
    "--add-data",
    "teachers_teammate/assets:teachers_teammate/assets",
    "--add-data",
    "teachers_teammate/infrastructure/providers:teachers_teammate/infrastructure/providers",
    "--add-data",
    "docs/using_the_app.md:docs",
    "--add-data",
    "docs/advanced_user_guide.md:docs",
    "--add-data",
    "docs/index.md:docs",
    "--add-data",
    "README.md:.",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers.ollama",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers.openai",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers.anthropic",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers.google",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers.mistral",
    "--hidden-import",
    "teachers_teammate.infrastructure.providers.cohere",
    # Bundle pip so the frozen binary can re-invoke itself in pip-dispatch mode
    # for the in-app addon installer.
    "--collect-all",
    "pip",
    # timeit and cProfile are stdlib but some pip/spaCy code paths import them;
    # ensure they are bundled. cProfile depends on the C extension _lsprof.
    "--hidden-import",
    "timeit",
    "--hidden-import",
    "cProfile",
    "--hidden-import",
    "_lsprof",
]


def _ensure_assets() -> None:
    """Generate installer assets (ICO, welcome BMP) via make_ico.py on Windows."""
    if sys.platform != "win32":
        return
    script = ROOT / "tools" / "packaging" / "make_ico.py"
    if script.exists():
        subprocess.run([sys.executable, str(script)], check=True, cwd=ROOT)


def _icon_flags() -> list[str]:
    """Return the --icon flag(s) for the current platform, or [] if not found.

    The canonical icon source is teachers_teammate_icon.png. Platform-specific
    icon symbols (.ico/.icns) are generated from that image when needed.
    """
    assets = ROOT / "teachers_teammate" / "assets"
    source_png = assets / "teachers_teammate_icon.png"
    if not source_png.exists():
        return []

    if sys.platform == "win32":
        p = assets / "teachers_teammate.ico"
    elif sys.platform == "darwin":
        p = assets / "teachers_teammate.icns"
        _ensure_icns(source_png, p)
    else:
        p = source_png

    if not p.exists() and sys.platform in ("win32", "darwin"):
        # Fall back to PNG if symbol generation is not possible in this env.
        p = source_png

    return [f"--icon={p}"] if p.exists() else []


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


# ── Build flags ─────────────────────────────────────────────────────────────
# The single binary bundles both the GUI entry point and all CLI modules so
# that the OCR pipeline is fully functional inside the frozen executable.

_BUILD_BASE: list[str] = [
    "--windowed",
    "--name",
    "teachers-teammate",
    "--collect-all",
    "PySide6",
    # GUI entry point
    "--hidden-import",
    "teachers_teammate.gui",
    # CLI / pipeline modules (bundled so the full pipeline is available)
    "--hidden-import",
    "cv2",
    "--hidden-import",
    "pypdfium2",
    "--hidden-import",
    "teachers_teammate.cli",
    "--hidden-import",
    "teachers_teammate.infrastructure.correction",
    "--hidden-import",
    "teachers_teammate.infrastructure.docx_builder",
    "--hidden-import",
    "teachers_teammate.infrastructure.image_preprocessor",
    "tools/build/run_gui.py",
]


def _build_flags() -> list[str]:
    icon = _icon_flags()
    # macOS: omit --onefile so PyInstaller produces a .app bundle (onedir)
    # Windows: use onedir for faster startup (no temp extraction on each launch)
    if sys.platform in ("darwin", "win32"):
        return [*icon, *_BUILD_BASE]
    return ["--onefile", *icon, *_BUILD_BASE]


# ── Runner ─────────────────────────────────────────────────────────────────


def _run(flags: list[str]) -> None:
    env = os.environ.copy()
    # PySide6 needs a display driver even at analysis time on Linux
    if sys.platform.startswith("linux"):
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", *flags],
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

    _ensure_assets()
    print("==> Building standalone binary…")
    _run(_COMMON + _build_flags())
    if sys.platform == "darwin":
        label = "dist/teachers-teammate.app"
    elif sys.platform == "win32":
        label = "dist/teachers-teammate/teachers-teammate.exe"
    else:
        label = "dist/teachers-teammate"
    print(f"    done → {label}\n")

    # ── macOS: create a DMG installer (uncomment to enable) ──────────────────
    # Wraps the .app bundle into a distributable .dmg with an /Applications
    # symlink for drag-install UX.  Requires macOS (uses hdiutil).
    #
    # if sys.platform == "darwin":
    #     import subprocess as _sp
    #     _version = "0.1.0"  # update or read from pyproject.toml
    #     _sp.run(
    #         ["bash", str(ROOT / "tools" / "packaging" / "make_dmg.sh"), _version],
    #         check=True,
    #     )
    #     print(f"    DMG → dist/teachers-teammate-{_version}-macos.dmg\n")


if __name__ == "__main__":
    main()
