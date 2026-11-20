"""Entry-point wrapper used by PyInstaller for the GUI executable.

When running from source (e.g. ``python tools/build/run_gui.py``), this script
adds the project root to ``sys.path`` so that the ``src`` package is importable.
In a frozen PyInstaller bundle the path is managed automatically.

Frozen-build pip dispatch
-------------------------
When the frozen binary is re-invoked with ``--_pip_install_mode`` as the first
argument, it acts as a pip runner instead of launching the GUI.  This lets the
in-app addon installer call pip inside the same Python interpreter that
PyInstaller bundled, without requiring a system Python.
"""

import sys

# ── pip dispatch (must be first — no heavy imports before this check) ─────────
if "--_pip_install_mode" in sys.argv:
    _idx = sys.argv.index("--_pip_install_mode")
    _pip_args = sys.argv[_idx + 1 :]

    # PyInstaller compatibility: distlib.resources.finder() looks up type(module.__loader__)
    # in _finder_registry to select a finder class. PyInstaller's custom importer type
    # is not registered there, so it raises DistlibException when pip tries to locate
    # its launcher scripts. We register the PyInstaller loader type → ResourceFinder
    # (filesystem-backed) before pip runs, using the same API distlib uses internally.
    try:
        import pip._vendor.distlib as _dl_pkg
        import pip._vendor.distlib.resources as _dr

        _pyinstaller_loader = getattr(_dl_pkg, "__loader__", None)
        if _pyinstaller_loader is not None:
            _loader_type = type(_pyinstaller_loader)
            if _loader_type not in _dr._finder_registry:  # noqa: SLF001
                _dr._finder_registry[_loader_type] = _dr.ResourceFinder  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass

    from pip._internal.cli.main import main as _pip_main

    raise SystemExit(_pip_main(_pip_args))

# ── normal GUI launch ─────────────────────────────────────────────────────────
import os

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.."))

# Inject the user addon packages directory before any optional import so that
# packages installed by the in-app installer are visible from the first launch
# after installation.  prepare_packages_dir() first: it applies what an install
# left pending and cleans up, before anything is imported from the folder.
from teachers_teammate.infrastructure.addon_manager import (
    inject_packages_dir,
    prepare_packages_dir,
)

# The helper modes below (smoke test, forget keys, spaCy model download) are not a start of the
# app: one of them runs while the app, or an install it started, is using the folder, so they
# leave the clean-up to a normal start.
_HELPER_MODES = (
    "--_smoke_test",
    "--_forget_keys",
    "--_set_update_check",
    "--_spacy_download_mode",
)
if not any(mode in sys.argv for mode in _HELPER_MODES):
    prepare_packages_dir()
inject_packages_dir()

# ── smoke test dispatch ──────────────────────────────────────────────────────
# A build is checked by running the app with `--_smoke_test <file>`: the checks live in
# teachers_teammate/frozen_modes.py, the result goes to the file (a --windowed
# build has no console on Windows) and the exit code says whether it passed.
if "--_smoke_test" in sys.argv:
    _idx = sys.argv.index("--_smoke_test")

    from pathlib import Path as _SmokePath

    from teachers_teammate.frozen_modes import run_smoke_test

    raise SystemExit(run_smoke_test(_SmokePath(sys.argv[_idx + 1])))

# ── forget-keys dispatch ─────────────────────────────────────────────────────
# The uninstaller runs `--_forget_keys` before it removes the program files: the API
# keys live in the system keychain, which no uninstaller can enumerate.
if "--_forget_keys" in sys.argv:
    from teachers_teammate.frozen_modes import forget_keys

    raise SystemExit(forget_keys())

# ── update-check dispatch ────────────────────────────────────────────────────
# The installer runs `--_set_update_check on|off [settings folder]` for the choice the teacher made
# on its second page.  The folder is the teacher's own, for an install run with the rights of
# another account.
if "--_set_update_check" in sys.argv:
    _idx = sys.argv.index("--_set_update_check")
    _rest = sys.argv[_idx + 1 :]

    from teachers_teammate.frozen_modes import set_update_check

    raise SystemExit(set_update_check(*_rest[:2]))

# ── spaCy model download dispatch ─────────────────────────────────────────────
# spaCy models live on GitHub Releases, not PyPI.  spaCy's own download command
# knows how to resolve the wheel URL — but it uses run_command() which spawns
# [sys.executable, "-m", "pip", "install", ...].  In a frozen build that
# subprocess re-enters the frozen exe without a recognised flag, which triggers
# a full GUI import and fails on modules not bundled (e.g. timeit).
#
# Fix: the caller passes the pinned wheel URL, and pip runs internally via
# pip._internal.cli.main — exactly as the --_pip_install_mode path does.
if "--_spacy_download_mode" in sys.argv:
    _idx = sys.argv.index("--_spacy_download_mode")
    _download_url = sys.argv[_idx + 1]
    _pip_args = list(sys.argv[_idx + 2 :])

    # Apply the same distlib / PyInstaller loader workaround as --_pip_install_mode.
    try:
        import pip._vendor.distlib as _dl_pkg
        import pip._vendor.distlib.resources as _dr

        _pyinstaller_loader = getattr(_dl_pkg, "__loader__", None)
        if _pyinstaller_loader is not None:
            _loader_type = type(_pyinstaller_loader)
            if _loader_type not in _dr._finder_registry:  # noqa: SLF001
                _dr._finder_registry[_loader_type] = _dr.ResourceFinder  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass

    from pip._internal.cli.main import main as _pip_main

    raise SystemExit(_pip_main(["install", *_pip_args, _download_url]))

# ── Early splash screen ───────────────────────────────────────────────────────
# Show the splash *before* importing teachers_teammate.gui so it is visible
# while all the heavy transitive imports (langchain, spaCy, cv2, …) are loaded.
# main_gui() receives it via `early_splash` and reuses it instead of creating
# a second splash after the slow import phase has already finished.
_early_splash = None
try:
    from pathlib import Path as _Path

    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QPixmap as _QPixmap
    from PySide6.QtWidgets import QApplication as _QApp
    from PySide6.QtWidgets import QSplashScreen as _QSplash

    _qt_app = _QApp.instance() or _QApp(sys.argv)

    if getattr(sys, "frozen", False):
        _assets_root = _Path(getattr(sys, "_MEIPASS", "."))
    else:
        # run_gui.py lives at tools/build/ — walk up to the repo root
        _assets_root = _Path(__file__).resolve().parent.parent.parent

    _splash_img = _assets_root / "teachers_teammate" / "assets" / "teachers_teammate.png"
    if _splash_img.exists():
        _pix = _QPixmap(str(_splash_img))
        if not _pix.isNull():
            if _pix.width() > 600 or _pix.height() > 600:
                _pix = _pix.scaled(
                    600,
                    600,
                    _Qt.AspectRatioMode.KeepAspectRatio,
                    _Qt.TransformationMode.SmoothTransformation,
                )
            # No always-on-top hint: a startup error dialog must never end up hidden behind it.
            _early_splash = _QSplash(_pix)
            _early_splash.show()
            _qt_app.processEvents()
except Exception:  # noqa: BLE001
    pass

try:
    from teachers_teammate.gui import main_gui
except BaseException:
    # A failed import must not leave the splash covering the error dialog.
    if _early_splash is not None:
        _early_splash.close()
    raise

main_gui(early_splash=_early_splash)
