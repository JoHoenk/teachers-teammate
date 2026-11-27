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

import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).resolve().parent.parent
ASSETS = ROOT / "teachers_teammate" / "assets"

# Qt modules the app does not use: one in the bundle would be size for nothing, so a future import
# must never drag one in. The smoke test (teachers_teammate/frozen_modes.py) checks the finished
# bundle, and a unit test keeps this list equal to its UNUSED_QT_MODULES.
UNUSED_QT_MODULES = (
    "Charts",
    "DataVisualization",
    "Graphs",
    "HttpServer",
    "Lottie",
    "NetworkAuth",
    "Quick3D",
    "QuickTimeline",
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
    # The unused modules: the library "Qt6<name>" and the Python module "Qt<name>" of each.
    *(f"Qt6{module}" for module in UNUSED_QT_MODULES),
    *(f"Qt{module}" for module in UNUSED_QT_MODULES),
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

# Dropped on Windows only. On Linux and macOS the OpenCV wheel carries FFmpeg as shared
# libraries that ship with the app; on Windows it is one opencv_videoio_ffmpeg*.dll that
# PyInstaller's cv2 hook bundles, and nothing in the app plays video. NOTICE says FFmpeg ships
# on Linux and macOS only.  The image codecs the reader uses live in OpenCV's own library, not in
# this DLL.
_DROPPED_ON_WINDOWS = ("opencv_videoio_ffmpeg",)

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
    # `check_linux_bundle.py --unused` reports these as dead weight of the build image: libbsd is
    # what libXdmcp pulls in (dropped above) and libmd is what libbsd pulls in; Qt compiles its
    # own pcre2 and libjpeg into the libraries it ships, and Pillow carries its own libjpeg in
    # pillow.libs/. The image is Ubuntu 24.04, whose t64 rename gives some libraries another
    # soname; `--unused` re-measures the list on every build and fails when something new is dead
    # weight.
    "libbsd.so",
    "libmd.so",
    "libjpeg.so",
    "libpcre2-8.so",
)


def _keep(entry):
    name = entry[0].replace("\\", "/")
    if any(part in name for part in _DROPPED):
        return False
    if sys.platform == "win32" and any(part in name for part in _DROPPED_ON_WINDOWS):
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

# The add-on versions this release was tested with, written by build_standalone.py next to the
# constraints file.  The installed app gives it to pip as a second `-c`, so an add-on install
# gets the versions the weekly resolution check ran, not whatever is newest that day.
_ADDON_LOCK = ROOT / "teachers_teammate" / "_build" / "addon_lock.txt"
if not _ADDON_LOCK.is_file():
    raise SystemExit(
        f"{_ADDON_LOCK} is missing: build with tools/build/build_standalone.py "
        "(or run tools/build/addon_lock.py first)"
    )
datas.append((str(_ADDON_LOCK), "teachers_teammate/_build"))

# The model files the app carries itself: the built-in reader and the page models, fetched by
# tools/build/fetch_models.py.  Without them the app would ask a teacher to download what the
# installer was supposed to bring, so a build without them is refused here rather than shipped.
_MODELS = ROOT / "teachers_teammate" / "_models"
if not _MODELS.is_dir():
    raise SystemExit(
        f"{_MODELS} is missing: build with tools/build/build_standalone.py "
        "(or run tools/build/fetch_models.py first)"
    )
# A folder that exists is not enough: a cancelled fetch or an older revision pin leaves one that
# holds nothing, or the wrong files, and the app would then ask for a download after all.
sys.path.insert(0, str(ROOT))
from tools.build.fetch_models import incomplete as _incomplete_models  # noqa: E402

_INCOMPLETE = _incomplete_models(_MODELS)
if _INCOMPLETE:
    raise SystemExit(
        f"{_MODELS} does not hold every pinned model file: {', '.join(_INCOMPLETE)}. "
        "Run tools/build/fetch_models.py again."
    )
datas.append((str(_MODELS), "teachers_teammate/_models"))
binaries = []
# Providers are discovered at runtime from the providers folder (bundled as data above);
# PyInstaller cannot see the modules behind that discovery.
_PROVIDERS_PACKAGE = "teachers_teammate.infrastructure.providers"
_PROVIDER_MODULES = [
    _PROVIDERS_PACKAGE if path.stem == "__init__" else f"{_PROVIDERS_PACKAGE}.{path.stem}"
    for path in sorted((ROOT / "teachers_teammate" / "infrastructure" / "providers").glob("*.py"))
]
hiddenimports = [
    *_PROVIDER_MODULES,
    # Menu actions open these in-process through a lazy import.
    "teachers_teammate.gui",
    "teachers_teammate.gui.setup",
    # Pipeline modules the GUI reaches through a lazy import.
    "cv2",
    # The smoke test reports the wheel's OpenCV version from here; ``cv2.__version__`` is
    # OpenCV's own, and nothing else in the app imports this module.
    "cv2.version",
    "pypdfium2",
    "teachers_teammate.infrastructure.proofreading.chunking",
    "teachers_teammate.infrastructure.proofreading.corrector",
    "teachers_teammate.infrastructure.docx_builder",
    "teachers_teammate.infrastructure.preprocessing.recipes",
    # qdarkstyle imports qtpy when the stylesheet is loaded, and qtpy imports packaging at once.
    # Named here so the bundle does not depend on the analysis following that chain; the
    # smoke test's "stylesheet" check proves it did.
    "qtpy",
    # timeit and cProfile are stdlib but some pip/spaCy code paths import them;
    # cProfile depends on the C extension _lsprof.
    "timeit",
    "cProfile",
    "_lsprof",
    # stdlib that only the Google add-on (google-genai) imports.
    "wave",
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

# The add-on constraints promise packaging to the add-ons, so the add-on install drops its own
# copy; spaCy imports packaging.requirements, which nothing in the app does.  All of it, then.
hiddenimports += collect_submodules("packaging")

# The smoke test (``frozen_modes._check_addon_constraints``) reads the version of these four
# through ``importlib.metadata`` and compares it with the add-on constraints. PyInstaller only
# bundles a package's metadata when a hook asks for it (numpy and pydantic happen to have one;
# requests and pillow do not), so ask for all four here, not by luck of the hooks.
for _shared in ("numpy", "pydantic", "requests", "pillow"):
    datas += copy_metadata(_shared)

a = Analysis(
    [str(ROOT / "teachers_teammate" / "frozen_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # setuptools is a build tool, not a runtime dependency: pip uses its own vendored copy.
    # readline and dbm.gnu stay out because nothing in a windowed app has a terminal to complete
    # in or opens a gdbm database, and because the host-library policy keeps the build machine's
    # copies out of the bundle — not because of their license.
    excludes=["setuptools", "_distutils_hack", "pkg_resources", "readline", "dbm.gnu"],
    noarchive=False,
    optimize=0,
)
# A folder of this repository on PyInstaller's search path hides a library of the same name
# (tools/packaging hid `packaging`: the bundle shipped the folder's __init__.py and no
# packaging.version).  Nothing of tools/ belongs in the app, so none may be in the analysis.
_FROM_TOOLS = sorted(
    name for name, path, _kind in a.pure if Path(path).is_relative_to(ROOT / "tools")
)
if _FROM_TOOLS:
    raise SystemExit(f"modules of tools/ are in the bundle, they hide libraries: {_FROM_TOOLS}")
a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]

# The .deb depends on the distribution's packages for the build machine's libraries (X11, glib,
# OpenSSL, libstdc++, ...) instead of shipping them: the distribution patches them and carries
# their sources.  `check_linux_bundle.py --depends` names the packages, and `--host-libs` fails
# the build when one is still in the bundle.
# Without dpkg (not a Debian-based machine) nothing is left out, and `--host-libs` fails on it.
if sys.platform.startswith("linux") and shutil.which("dpkg-query"):
    from tools.build import check_linux_bundle  # noqa: E402

    _deny, _rows = check_linux_bundle.host_policy()
    _system = check_linux_bundle.system_libraries(
        [(entry[0], Path(entry[1])) for entry in a.binaries], _deny, _rows
    )
    a.binaries = [entry for entry in a.binaries if entry[0] not in _system]

pyz = PYZ(a.pure)

if sys.platform == "win32":
    _icon = str(ASSETS / "teachers_teammate.ico")
elif sys.platform == "darwin":
    _icon = str(ASSETS / "teachers_teammate.icns")
else:
    _icon = str(ASSETS / "teachers_teammate_icon.png")

# The version resource Windows shows in the exe's properties, in Task Manager and in the
# SmartScreen prompt, written by tools/build/make_version_resource.py just before this spec
# runs.  Its product name and product version are the ones the setup and the uninstaller carry
# so a build without it is refused here.
_VERSION_INFO = None
if sys.platform == "win32":
    _VERSION_INFO = ROOT / "teachers_teammate" / "_build" / "version_info.txt"
    if not _VERSION_INFO.is_file():
        raise SystemExit(
            f"{_VERSION_INFO} is missing: build with tools/build/build_standalone.py "
            "(or run tools/build/make_version_resource.py first)"
        )

# A folder on every platform, never a single file: the .deb installs it under /opt, and the Qt
# libraries stay replaceable files instead of being packed into an archive that unpacks to a
# temporary directory on every start.
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
    version=str(_VERSION_INFO) if _VERSION_INFO is not None else None,
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
    # The keys PyInstaller does not write itself.  The two version keys come from the stamped
    # version, in the numeric form Apple asks for (0.5.7, never 0.5.7rc1); the minimum macOS is
    # the one number check_macos_bundle.py checks the bundle against.  ROOT is on sys.path
    # already (the spec put it there for fetch_models).
    from teachers_teammate import __version__ as _pkg_version  # noqa: E402
    from tools.build.check_macos_bundle import MACOS_MINIMUM  # noqa: E402
    from tools.build.make_version_resource import LEGAL_COPYRIGHT, numeric_version  # noqa: E402

    _numbers = numeric_version(_pkg_version)
    _short_version = ".".join(str(number) for number in _numbers[:3])
    app = BUNDLE(
        coll,
        name="teachers-teammate.app",
        icon=_icon,
        bundle_identifier="io.github.johoenk.teachers-teammate",
        version=_short_version,
        info_plist={
            "CFBundleShortVersionString": _short_version,
            "CFBundleVersion": ".".join(str(number) for number in _numbers),
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": LEGAL_COPYRIGHT,
            "LSMinimumSystemVersion": MACOS_MINIMUM,
        },
    )
