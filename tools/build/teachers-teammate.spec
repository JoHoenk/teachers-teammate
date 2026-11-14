# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Teacher's Teammate.

Committed rather than generated: what ships is what this file lists, and the bundle diet
below stays reviewable. ``tools/build/build_standalone.py`` generates the platform icons
and runs this spec.

The diet: PyInstaller's PySide6 hooks collect the Qt modules the app imports (QtCore,
QtGui, QtWidgets, QtCharts) and their plugins. ``collect_all("PySide6")`` would add about
200 MB of unused Qt — WebEngine (Chromium), Multimedia (FFmpeg), QML/Quick/3D, Designer
and Pdf — and with them the build machine's GTK3, cairo and pango libraries.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent.parent
ASSETS = ROOT / "teachers_teammate" / "assets"

# Qt plugins and libraries that must not ship. libqgtk3 pulls GTK3, cairo, pango and glib
# from the build machine; the desktop-portal plugin (kept) gives file dialogs without it.
# The rest is a defensive net: a future import must not drag Chromium or FFmpeg back in.
_DROPPED = ("platformthemes/libqgtk3", "WebEngine", "Qt6Quick3D", "Qt6Multimedia")


def _keep(entry):
    name = entry[0].replace("\\", "/")
    return not any(part in name for part in _DROPPED)


datas = [
    (str(ASSETS), "teachers_teammate/assets"),
    (
        str(ROOT / "teachers_teammate" / "infrastructure" / "providers"),
        "teachers_teammate/infrastructure/providers",
    ),
    (str(ROOT / "docs" / "using_the_app.md"), "docs"),
    (str(ROOT / "docs" / "advanced_user_guide.md"), "docs"),
    (str(ROOT / "docs" / "index.md"), "docs"),
    (str(ROOT / "README.md"), "."),
]
binaries = []
hiddenimports = [
    # Providers are discovered at runtime from the providers folder (bundled as data above);
    # PyInstaller cannot see the modules behind that discovery.
    "teachers_teammate.infrastructure.providers",
    "teachers_teammate.infrastructure.providers.ollama",
    "teachers_teammate.infrastructure.providers.openai",
    "teachers_teammate.infrastructure.providers.anthropic",
    "teachers_teammate.infrastructure.providers.google",
    "teachers_teammate.infrastructure.providers.mistral",
    "teachers_teammate.infrastructure.providers.mistral_eu",
    "teachers_teammate.infrastructure.providers._mistral_common",
    "teachers_teammate.infrastructure.providers.cohere",
    # Menu actions open these in-process through a lazy import.
    "teachers_teammate.gui",
    "teachers_teammate.gui.benchmark",
    "teachers_teammate.gui.setup",
    # Pipeline modules the GUI reaches through a lazy import.
    "cv2",
    "pypdfium2",
    "teachers_teammate.infrastructure.correction",
    "teachers_teammate.infrastructure.docx_builder",
    "teachers_teammate.infrastructure.preprocessing.recipes",
    # timeit and cProfile are stdlib but some pip/spaCy code paths import them;
    # cProfile depends on the C extension _lsprof.
    "timeit",
    "cProfile",
    "_lsprof",
]

# pip is bundled so the frozen binary can re-invoke itself in pip-dispatch mode for the
# in-app add-on installer.
_pip = collect_all("pip")
datas += _pip[0]
binaries += _pip[1]
hiddenimports += _pip[2]

# keyring finds its platform backends (macOS Keychain, Windows Credential Manager, Linux
# Secret Service) through package metadata, which PyInstaller does not follow.
datas += copy_metadata("keyring")
hiddenimports += collect_submodules("keyring")

a = Analysis(
    [str(ROOT / "tools" / "build" / "run_gui.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # setuptools is a build tool, not a runtime dependency: pip uses its own vendored copy.
    excludes=["setuptools", "_distutils_hack", "pkg_resources"],
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]

pyz = PYZ(a.pure)

if sys.platform == "win32":
    _icon = str(ASSETS / "teachers_teammate.ico")
elif sys.platform == "darwin":
    _icon = str(ASSETS / "teachers_teammate.icns")
else:
    _icon = str(ASSETS / "teachers_teammate_icon.png")

if sys.platform == "linux":
    # Linux stays a single file until the folder build of the packaging plan lands.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="teachers-teammate",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=[_icon],
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="teachers-teammate",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=[_icon],
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="teachers-teammate",
    )
    if sys.platform == "darwin":
        app = BUNDLE(
            coll,
            name="teachers-teammate.app",
            icon=_icon,
            bundle_identifier="io.github.johoenk.teachers-teammate",
        )
