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

# Qt plugins and libraries that must not ship. libqgtk3 pulls GTK3, cairo, pango and glib
# from the build machine; the desktop-portal plugin (kept) gives file dialogs without it.
# The rest is a defensive net: a future import must not drag Chromium or FFmpeg back in.
_DROPPED = (
    "platformthemes/libqgtk3",
    "WebEngine",
    "Qt6Quick3D",
    "Qt6Multimedia",
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
    # The Python module and the plugins that only QtNetwork loads (TLS and network-state
    # backends). Without the library they would be bundled but unloadable, and make_deb.sh
    # could not measure the package's Depends: line. The app talks to servers through
    # requests and httpx, not through Qt.
    "PySide6/QtNetwork.",
    "plugins/tls/",
    "plugins/networkinformation/",
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
    # Found by `check_linux_bundle.py --unused` on the ubuntu-22.04 runner. libbsd is what
    # libXdmcp pulls in there (dropped above), and libmd is what libbsd pulls in; Qt compiles its
    # own pcre2 and libjpeg into the libraries it ships, and Pillow carries its own libjpeg in
    # pillow.libs/. The build image is ubuntu-24.04 now, whose sonames differ (the t64 rename);
    # `--unused` re-measures the list on every build and fails when something new is dead weight.
    "libbsd.so",
    "libmd.so",
    "libjpeg.so",
    "libpcre2-8.so",
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
# The versions this bundle ships, written by build_standalone.py just before PyInstaller runs.
# The installed app gives it to pip as `-c` for every add-on install and compares the add-on
# folder against it after an app update (teachers_teammate/infrastructure/addon_manager.py).
_ADDON_CONSTRAINTS = ROOT / "teachers_teammate" / "_build" / "addon_constraints.txt"
if not _ADDON_CONSTRAINTS.is_file():
    raise SystemExit(
        f"{_ADDON_CONSTRAINTS} is missing: build with tools/build/build_standalone.py "
        "(or run tools/build/addon_constraints.py first)"
    )
datas.append((str(_ADDON_CONSTRAINTS), "teachers_teammate/_build"))
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

# The smoke test (``frozen_modes._check_addon_constraints``) reads the version of these four
# through ``importlib.metadata`` and compares it with the add-on constraints. PyInstaller only
# bundles a package's metadata when a hook asks for it (numpy and pydantic happen to have one;
# requests and pillow do not), so ask for all four here, not by luck of the hooks.
for _shared in ("numpy", "pydantic", "requests", "pillow"):
    datas += copy_metadata(_shared)

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
