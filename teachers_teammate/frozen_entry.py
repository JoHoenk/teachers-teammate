"""The entry point of the installed app: PyInstaller freezes this module.

A source run starts the GUI with ``python -m teachers_teammate.gui``. Everything a bundle is
started for besides a normal start dispatches here, before the GUI is imported: the smoke tests,
the walkthrough script, pip for the in-app add-on installer, and the uninstaller's key removal.

It builds concrete classes, so it is a composition exception (AGENTS.md).
"""

from __future__ import annotations

from pathlib import Path
import sys

#: These are not a start of the app: one of them runs while the app, or an install it started, is
#: using the add-on folder, so they leave its clean-up to a normal start.
_HELPER_MODES = (
    "--_smoke_test",
    "--_addon_smoke_test",
    "--_forget_keys",
    "--_set_update_check",
    "--_spacy_download_mode",
)


def _pip_main(args: list[str]) -> int:
    """Run pip inside this interpreter, the only one a frozen bundle has.

    PyInstaller compatibility: ``distlib.resources.finder()`` looks up ``type(module.__loader__)``
    in ``_finder_registry`` to select a finder class, and PyInstaller's importer type is not
    registered there, so it raises ``DistlibException`` when pip looks for its launcher scripts.
    The loader type is registered with the same API distlib uses, before pip runs.
    """
    try:
        from pip._vendor import distlib  # noqa: PLC0415
        from pip._vendor.distlib import resources as distlib_resources  # noqa: PLC0415

        loader = getattr(distlib, "__loader__", None)
        registry = distlib_resources._finder_registry  # noqa: SLF001
        if loader is not None and type(loader) not in registry:
            registry[type(loader)] = distlib_resources.ResourceFinder
    except Exception:  # noqa: BLE001  # no distlib: pip works without the registration
        pass

    from pip._internal.cli.main import main as pip_main  # noqa: PLC0415

    return pip_main(args)


def _inject_addon_packages(argv: list[str]) -> None:
    """Make the packages the in-app installer added importable, before anything imports them."""
    from teachers_teammate.infrastructure.addon_manager import (  # noqa: PLC0415
        inject_packages_dir,
        prepare_packages_dir,
    )

    if "--_run_script" in argv:
        # A script runs on the bundle alone: the add-on folder is the teacher's own, and the
        # script's isolation does not reach it.
        return
    if not any(mode in argv for mode in _HELPER_MODES):
        # prepare_packages_dir() first: it applies what an install left pending and cleans up.
        prepare_packages_dir()
    inject_packages_dir()


def _splash_image() -> Path:
    """Return the splash image of a frozen bundle, or of this source tree."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "teachers_teammate" / "assets" / (
            "teachers_teammate.png"
        )
    return Path(__file__).resolve().parent / "assets" / "teachers_teammate.png"


def _start_gui() -> int:
    """Show the splash while the GUI's heavy imports load, then start the GUI."""
    splash = None
    try:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QPixmap  # noqa: PLC0415
        from PySide6.QtWidgets import QApplication, QSplashScreen  # noqa: PLC0415

        app = QApplication.instance() or QApplication(sys.argv)
        image = _splash_image()
        if image.exists():
            pixmap = QPixmap(str(image))
            if not pixmap.isNull():
                if pixmap.width() > 600 or pixmap.height() > 600:
                    pixmap = pixmap.scaled(
                        600,
                        600,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                # No always-on-top hint: a startup error dialog must never hide behind it.
                splash = QSplashScreen(pixmap)
                splash.show()
                app.processEvents()
    except Exception:  # noqa: BLE001  # a bundle without Qt: the GUI import below reports it
        splash = None

    try:
        from teachers_teammate.gui import main_gui  # noqa: PLC0415
    except BaseException:
        # A failed import must not leave the splash covering the error dialog.
        if splash is not None:
            splash.close()
        raise

    main_gui(early_splash=splash)
    return 0


def main() -> int:
    """Dispatch the mode ``sys.argv`` names, and start the GUI when it names none."""
    argv = sys.argv

    if "--_pip_install_mode" in argv:
        return _pip_main(argv[argv.index("--_pip_install_mode") + 1 :])

    _inject_addon_packages(argv)

    # Each mode imports its own part of ``frozen_modes`` here: that module reads GUI modules at
    # import time, and a normal start must reach the splash first.
    if "--_smoke_test" in argv:
        from teachers_teammate.frozen_modes import run_smoke_test  # noqa: PLC0415

        return run_smoke_test(Path(argv[argv.index("--_smoke_test") + 1]))

    if "--_addon_smoke_test" in argv:
        from teachers_teammate.frozen_modes import run_addon_smoke_test  # noqa: PLC0415

        return run_addon_smoke_test(Path(argv[argv.index("--_addon_smoke_test") + 1]))

    if "--_run_script" in argv:
        rest = argv[argv.index("--_run_script") + 1 :]
        if len(rest) < 2:
            return 2

        from teachers_teammate.frozen_modes import run_script  # noqa: PLC0415

        return run_script(Path(rest[0]), Path(rest[1]), rest[2:])

    if "--_forget_keys" in argv:
        from teachers_teammate.frozen_modes import forget_keys  # noqa: PLC0415

        return forget_keys()

    if "--_set_update_check" in argv:
        from teachers_teammate.frozen_modes import set_update_check  # noqa: PLC0415

        return set_update_check(*argv[argv.index("--_set_update_check") + 1 :][:2])

    if "--_spacy_download_mode" in argv:
        # spaCy's own download command spawns `python -m pip install`, which re-enters this
        # frozen executable without a recognised flag. The caller passes the wheel URL instead,
        # and pip runs inside this interpreter, as in the --_pip_install_mode path.
        index = argv.index("--_spacy_download_mode")
        return _pip_main(["install", *argv[index + 2 :], argv[index + 1]])

    return _start_gui()


if __name__ == "__main__":
    raise SystemExit(main())
