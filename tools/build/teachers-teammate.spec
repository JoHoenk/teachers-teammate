# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Teacher's Teammate.

Committed rather than generated: what ships is what this file lists, and the bundle diet
below stays reviewable. ``tools/build/build_standalone.py`` generates the platform icons
and runs this spec.

The diet: PyInstaller's PySide6 hooks collect the Qt modules the app imports (QtCore,
QtGui, QtWidgets) and their plugins. ``collect_all("PySide6")`` would add about
200 MB of unused Qt — WebEngine (Chromium), Multimedia (FFmpeg), QML/Quick/3D, Designer
and Pdf — and with them the build machine's GTK3, cairo and pango libraries.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent.parent
ASSETS = ROOT / "teachers_teammate" / "assets"

# Qt modules that Qt offers only under the GPL-3.0 or a commercial license. The rest of Qt is
# LGPL-3.0, which lets an Apache-2.0 application link it; one of these in the bundle would make
# the whole application GPL-3.0, so a future import must never drag one in. The smoke test
# (teachers_teammate/frozen_modes.py) checks the finished bundle, and a unit test keeps this
# list equal to its GPL_ONLY_QT_MODULES.
GPL_ONLY_QT_MODULES = (
    "Charts",
    "DataVisualization",
    "Graphs",
    "HttpServer",
    "Lottie",
    "Quick3D",
    "VirtualKeyboard",
    "WaylandCompositor",
)

# Qt plugins and libraries that must not ship. libqgtk3 pulls GTK3, cairo, pango and glib
# from the build machine; the desktop-portal plugin (kept) gives file dialogs without it.
# The rest is a defensive net: a future import must not drag Chromium or FFmpeg back in.
_DROPPED = (
    "platformthemes/libqgtk3",
    "WebEngine",
    "Qt6Multimedia",
    # The GPL-only modules: the library "Qt6<name>" and the Python module "Qt<name>" of each.
    *(f"Qt6{module}" for module in GPL_ONLY_QT_MODULES),
    *(f"Qt{module}" for module in GPL_ONLY_QT_MODULES),
    # Platform plugins for devices without a desktop (framebuffer, KMS, VNC, test stubs): the
    # app runs under X11 (xcb) or Wayland, and the smoke test under offscreen.
    "platforms/libqeglfs",
    "platforms/libqlinuxfb",
    "platforms/libqvnc",
    "platforms/libqminimal",
    "platforms/libqvkkhrdisplay",
    "plugins/egldeviceintegrations/",
    "libQt6EglFSDeviceIntegration",
    "libQt6EglFsKmsSupport",
    # Raw input devices and the TUIO touch plugin, which is the only reason for QtNetwork.
    "plugins/generic/",
    "libQt6Network",
    # The app never loads a QTranslator, so Qt's own translations are never read.
    "Qt/translations/",
)

# Dropping the libqgtk3 plugin does not drop what it linked against: PyInstaller had already
# walked its dependencies, so the whole GTK3 stack stayed in the bundle with nothing loading
# it. These are the libraries that only libgtk-3.so.0 reached. `check_linux_bundle.py
# --unused` lists every bundled library no other bundled file needs, which is how this list
# was found and how it stays honest when PySide6 changes.
_DROPPED_LIBS = (
    "libgtk-3.so",
    "libgdk-3.so",
    "libgdk_pixbuf-2.0.so",
    "libglycin-2.so",
    "libcairo.so",
    "libcairo-gobject.so",
    "libpango-1.0.so",
    "libpangocairo-1.0.so",
    "libpangoft2-1.0.so",
    "libatk-1.0.so",
    "libatk-bridge-2.0.so",
    "libatspi.so",
    "libepoxy.so",
    "libpixman-1.so",
    "liblcms2.so",
    "libfribidi.so",
    "libthai.so",
    "libdatrie.so",
    "libgraphite2.so",
    "libharfbuzz.so",
    "libgio-2.0.so",
    "libgobject-2.0.so",
    "libgmodule-2.0.so",
    "libmount.so",
    "libblkid.so",
    "libselinux.so",
    "libseccomp.so",
    # Xlib extensions GTK3 uses; Qt talks to X through xcb instead.
    "libXRes.so",
    "libXcomposite.so",
    "libXcursor.so",
    "libXdamage.so",
    "libXext.so",
    "libXfixes.so",
    "libXi.so",
    "libXinerama.so",
    "libXrandr.so",
    "libXrender.so",
    "libXau.so",
    "libXdmcp.so",
    # Kerberos, which only libQt6Network linked against.
    "libgssapi_krb5.so",
    "libkrb5.so",
    "libk5crypto.so",
    "libkrb5support.so",
    "libcom_err.so",
    "libkeyutils.so",
)


def _keep(entry):
    name = entry[0].replace("\\", "/")
    if any(part in name for part in _DROPPED):
        return False
    # Only the top level of the bundle holds host libraries; a Python extension module with
    # a similar name must never be caught by this.
    return not ("/" not in name and name.startswith(_DROPPED_LIBS))


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
    # readline would bundle the build machine's GPL-licensed libreadline; nothing in a windowed
    # app has a terminal to complete in.
    excludes=["setuptools", "_distutils_hack", "pkg_resources", "readline"],
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

# A folder on every platform, never a single file: the .deb installs it under /opt, the
# portable tarball unpacks it, and the Qt libraries stay replaceable files instead of being
# packed into an archive that unpacks to a temporary directory on every start.
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
