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
# after installation.
from teachers_teammate.infrastructure.addon_manager import (
    inject_packages_dir,
)

inject_packages_dir()

# ── spaCy model download dispatch ─────────────────────────────────────────────
# spaCy models live on GitHub Releases, not PyPI.  spaCy's own download command
# knows how to resolve the correct wheel URL — but it uses run_command() which
# spawns [sys.executable, "-m", "pip", "install", ...].  In a frozen build that
# subprocess re-enters the frozen exe without a recognised flag, which triggers
# a full GUI import and fails on modules not bundled (e.g. timeit).
#
# Fix: use spaCy's URL-resolution helpers directly, then call pip internally
# via pip._internal.cli.main — exactly as the --_pip_install_mode path does.
if "--_spacy_download_mode" in sys.argv:
    _idx = sys.argv.index("--_spacy_download_mode")
    _model = sys.argv[_idx + 1]
    _pip_args = list(sys.argv[_idx + 2 :])

    from spacy.cli.download import (  # noqa: PLC0415, E402
        get_compatibility,
        get_model_filename,
        get_version,
    )
    from spacy import about as _spacy_about  # noqa: PLC0415, E402
    from urllib.parse import urljoin  # noqa: PLC0415, E402

    _compat = get_compatibility()
    _ver = get_version(_model, _compat)
    _filename = get_model_filename(_model, _ver, sdist=False)
    _base = _spacy_about.__download_url__
    if not _base.endswith("/"):
        _base += "/"
    _download_url = urljoin(_base, _filename)

    # Apply the same distlib / PyInstaller loader workaround as --_pip_install_mode.
    try:
        import pip._vendor.distlib as _dl_pkg  # noqa: PLC0415
        import pip._vendor.distlib.resources as _dr  # noqa: PLC0415

        _pyinstaller_loader = getattr(_dl_pkg, "__loader__", None)
        if _pyinstaller_loader is not None:
            _loader_type = type(_pyinstaller_loader)
            if _loader_type not in _dr._finder_registry:  # noqa: SLF001
                _dr._finder_registry[_loader_type] = _dr.ResourceFinder  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass

    from pip._internal.cli.main import main as _pip_main  # noqa: PLC0415, E402

    raise SystemExit(_pip_main(["install", *_pip_args, _download_url]))

# ── Early splash screen ───────────────────────────────────────────────────────
# Show the splash *before* importing teachers_teammate.gui so it is visible
# while all the heavy transitive imports (langchain, spaCy, cv2, …) are loaded.
# main_gui() receives it via `early_splash` and reuses it instead of creating
# a second splash after the slow import phase has already finished.
_early_splash = None
try:
    from pathlib import Path as _Path  # noqa: PLC0415

    from PySide6.QtCore import Qt as _Qt  # noqa: PLC0415
    from PySide6.QtGui import QPixmap as _QPixmap  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication as _QApp  # noqa: PLC0415
    from PySide6.QtWidgets import QSplashScreen as _QSplash  # noqa: PLC0415

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
            _early_splash = _QSplash(_pix, _Qt.WindowType.WindowStaysOnTopHint)
            _early_splash.show()
            _qt_app.processEvents()
except Exception:  # noqa: BLE001
    pass

from teachers_teammate.gui import main_gui  # noqa: E402

main_gui(early_splash=_early_splash)
