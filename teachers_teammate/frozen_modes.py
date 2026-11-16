"""Hidden command-line modes of the installed app.

The frozen build is ``--windowed``: on Windows it has no console, so these modes report
through a file and through the exit code. ``tools/build/run_gui.py`` dispatches them
before the splash screen and before any heavy import.

``run_smoke_test`` is what proves a build is complete: the modules the app loads lazily,
Qt's image plugins, the reader's OpenCV runtime, PDF and DOCX writing, the provider list,
the bundled assets, the model files the app carries itself, the absence of GPL-only Qt
modules, the pip dispatch and the add-on constraints and lock. CI runs it on every build
(see ``.github/workflows/build_artifacts.yml``) and the Windows installer test runs it again
on the installed copy.

``forget_keys`` is what the uninstaller calls before it removes the program files: the API
keys sit in the system keychain, which no uninstaller can enumerate.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import importlib
import importlib.metadata
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

# Modules the app loads lazily — a menu action, an import inside a function. PyInstaller
# cannot see them, so the smoke test is what proves they made it into the bundle.
_LAZY_MODULES: tuple[str, ...] = (
    "teachers_teammate.gui._main_window",
    "teachers_teammate.gui.setup",
    "teachers_teammate.gui.benchmark",
    "teachers_teammate.gui.settings",
    "cv2",
    "pypdfium2",
    "docx",
    "ollama",
    "keyring",
    "yaml",
    "markdown_it",
    "platformdirs",
    "qdarkstyle",
)

# Image formats the app reads (infrastructure/input_providers.py). A bundle diet that drops
# Qt's image plugins shows up here, not in a teacher's first scan.
_IMAGE_FORMATS: tuple[str, ...] = ("png", "jpg", "tiff", "webp")

# The built-in reader runs its ONNX models through OpenCV's dnn module; the lock pins 5.x.
_MIN_CV_MAJOR = 5

# Files the installed app shows: the user guide pages, the license list, the icon and the
# splash image. A missing one is silent in the GUI (an empty tab, no picture), so check it.
_ASSETS: tuple[str, ...] = (
    "teachers_teammate/assets/third_party_licenses.md",
    "teachers_teammate/assets/third_party_inventory.csv",
    "teachers_teammate/assets/teachers_teammate.png",
    "teachers_teammate/assets/teachers_teammate_icon.png",
    "docs/using_the_app.md",
    "docs/advanced_user_guide.md",
    "docs/index.md",
)

# Packages whose copy in the add-on folder would shadow the bundled one and break the app, which
# is why the constraints file must pin them to what the bundle carries.
_CONSTRAINED_RUNTIME_PACKAGES: tuple[str, ...] = ("numpy", "pydantic", "requests", "pillow")

# Qt modules that Qt offers only under the GPL-3.0 or a commercial license. The rest of Qt is
# LGPL-3.0, which lets an Apache-2.0 application link it; one of these in the bundle would make
# the whole application GPL-3.0. ``tools/build/teachers-teammate.spec`` drops the same names
# (a unit test keeps the two lists equal); the smoke test is the check on the finished bundle.
GPL_ONLY_QT_MODULES: tuple[str, ...] = (
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

# A check returns ``None`` when it passes and a short description of the problem when it fails.
Check = Callable[[], "str | None"]


def _root() -> Path:
    """Return the folder the bundled files sit in (``sys._MEIPASS`` when frozen)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _check_imports() -> str | None:
    """Import every lazily loaded module; name the ones that fail."""
    problems: list[str] = []
    for name in _LAZY_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — any failure means "not bundled"
            problems.append(f"{name} ({type(exc).__name__}: {exc})")
    return "; ".join(problems) or None


def _check_qt() -> str | None:
    """Start Qt headless and check the image formats the app reads."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImageReader  # noqa: PLC0415

    if QGuiApplication.instance() is None:
        QGuiApplication([])
    supported = {bytes(fmt.data()).decode() for fmt in QImageReader.supportedImageFormats()}
    missing = [fmt for fmt in _IMAGE_FORMATS if fmt not in supported]
    if missing:
        return f"Qt cannot read {', '.join(missing)} (has {', '.join(sorted(supported))})"
    return None


def _check_reader_runtime() -> str | None:
    """Check the OpenCV runtime the built-in reader needs."""
    import cv2  # noqa: PLC0415

    if not hasattr(cv2.dnn, "readNetFromONNX"):
        return "cv2.dnn.readNetFromONNX is missing"
    major = int(str(cv2.__version__).split(".")[0])
    if major < _MIN_CV_MAJOR:
        return f"cv2 {cv2.__version__} is older than {_MIN_CV_MAJOR}.0"
    return None


def _check_pdf() -> str | None:
    """Create a PDF page with pypdfium2."""
    import pypdfium2  # noqa: PLC0415

    document = pypdfium2.PdfDocument.new()
    try:
        document.new_page(100, 100)
    finally:
        document.close()
    return None


def _check_docx() -> str | None:
    """Write a DOCX document into memory."""
    import docx  # noqa: PLC0415

    document = docx.Document()
    document.add_paragraph("smoke test")
    document.save(BytesIO())
    return None


def _check_providers() -> str | None:
    """Check that every provider module on disk is listed and imports."""
    from teachers_teammate.infrastructure import llm_factory  # noqa: PLC0415

    listed = llm_factory.list_providers()
    if not listed:
        return "no provider is listed"
    on_disk = sorted(
        path.stem
        for path in (Path(llm_factory.__file__).parent / "providers").glob("*.py")
        if not path.stem.startswith("_")
    )
    if listed != on_disk:
        return f"listed {listed} but bundled {on_disk}"
    problems: list[str] = []
    for name in listed:
        try:
            importlib.import_module(f"teachers_teammate.infrastructure.providers.{name}")
        except Exception as exc:  # noqa: BLE001 — any failure means "not bundled"
            problems.append(f"{name} ({type(exc).__name__}: {exc})")
    return "; ".join(problems) or None


def _check_assets() -> str | None:
    """Check the bundled assets (frozen builds only)."""
    root = _root()
    missing = [rel for rel in _ASSETS if not (root / rel).is_file()]
    if missing:
        return "missing " + ", ".join(missing)
    licenses = root / _ASSETS[0]
    if licenses.stat().st_size == 0:
        return f"{_ASSETS[0]} is empty"
    return None


def gpl_only_qt_files(names: Iterable[str]) -> list[str]:
    """Return the file or folder names (without their folder) that belong to a GPL-only Qt module.

    Matches the library (``libQt6Charts.so.6``, ``Qt6Charts.dll``) and the Python module
    (``QtCharts.abi3.so``, ``QtCharts.pyd``) of each module in :data:`GPL_ONLY_QT_MODULES`.
    """
    prefixes = tuple(
        f"{lib}{qt}{module}"
        for module in GPL_ONLY_QT_MODULES
        for qt in ("Qt6", "Qt")
        for lib in ("lib", "")
    )
    return sorted(name for name in names if name.startswith(prefixes))


def _check_qt_licenses() -> str | None:
    """Check that no GPL-only Qt module is in the bundle (frozen builds only)."""
    found: list[str] = []
    for folder, folders, files in os.walk(_root()):
        names = [*folders, *files]
        found += [str(Path(folder, name)) for name in gpl_only_qt_files(names)]
    if found:
        return "GPL-only Qt modules in the bundle: " + ", ".join(sorted(found))
    return None


def _check_pip() -> str | None:
    """Check that the bundled pip answers through the app's pip dispatch (frozen only)."""
    try:
        result = subprocess.run(
            [sys.executable, "--_pip_install_mode", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except OSError as exc:
        return f"pip dispatch could not start: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return f"pip dispatch exited {result.returncode}: {detail[-1] if detail else 'no output'}"
    return None


def _check_addon_constraints() -> str | None:
    """Check that the add-on constraints match the packages the bundle really carries (frozen only).

    The installed app gives this file to pip for every add-on install; a missing or stale one
    would let an add-on bring its own numpy or pydantic.
    """
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    path = addon_manager.addon_constraints_path()
    if path is None:
        return f"{addon_manager.ADDON_CONSTRAINTS_RELATIVE} is not in the bundle"
    pins = addon_manager.read_constraints(path)
    if not pins:
        return f"{path.name} lists no package"
    problems: list[str] = []
    for name in _CONSTRAINED_RUNTIME_PACKAGES:
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            problems.append(f"{name} is not in the bundle")
            continue
        pinned = pins.get(name.lower())
        if pinned != installed:
            problems.append(f"{name} is {installed} but the constraints say {pinned}")
    return "; ".join(problems) or None


def _check_addon_lock() -> str | None:
    """Check that the add-on lock ships and pins the add-ons themselves (frozen only).

    The installed app gives this file to pip as a second ``-c``; a bundle without it would
    install whatever version of spaCy or langchain-openai is newest that day.
    """
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    path = addon_manager.addon_lock_path()
    if path is None:
        return f"{addon_manager.ADDON_LOCK_RELATIVE} is not in the bundle"
    pins = addon_manager.read_constraints(path)
    if not pins:
        return f"{path.name} lists no package"
    missing = [name for name in addon_manager.addon_package_names() if name not in pins]
    return f"not pinned: {', '.join(missing)}" if missing else None


def _check_bundled_models() -> str | None:
    """Check that the reader and the page models ship with the app (frozen only).

    They are what makes the default setup work without a download; a bundle without them would
    still start, and only ask a teacher for files the installer was supposed to bring.
    """
    from teachers_teammate.domain.local_models import PAGE_MODELS, READER_MODELS  # noqa: PLC0415
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    # The bundle alone: a model downloaded earlier into the user's folder must not hide a build
    # that left its own copy out.
    missing = ModelStore().bundled_missing((*READER_MODELS, *PAGE_MODELS))
    if missing:
        return "not in the bundle: " + ", ".join(model.key for model in missing)
    return None


def _check_keychain() -> str | None:
    """Check that a real keychain backend is available (Windows and macOS only)."""
    import keyring  # noqa: PLC0415

    backend = keyring.get_keyring()
    if getattr(backend, "priority", 0) <= 0:
        return f"no usable keychain backend ({type(backend).__name__})"
    return None


def _checks() -> list[tuple[str, Check]]:
    """Return the checks that apply to this build and platform."""
    frozen = bool(getattr(sys, "frozen", False))
    checks: list[tuple[str, Check]] = [
        ("imports", _check_imports),
        ("qt", _check_qt),
        ("reader", _check_reader_runtime),
        ("pdf", _check_pdf),
        ("docx", _check_docx),
        ("providers", _check_providers),
    ]
    if frozen:
        checks += [
            ("assets", _check_assets),
            ("models", _check_bundled_models),
            ("qt licenses", _check_qt_licenses),
            ("pip", _check_pip),
            ("addon constraints", _check_addon_constraints),
            ("addon lock", _check_addon_lock),
        ]
    if sys.platform in ("win32", "darwin"):
        checks.append(("keychain", _check_keychain))
    return checks


def _versions() -> dict[str, str]:
    """Return the versions of the runtime packages, for the result file."""
    versions = {"python": sys.version.split()[0]}
    for name in ("PySide6", "cv2", "numpy", "pypdfium2", "docx", "ollama", "keyring"):
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — a missing package is a failed check, not a crash
            continue
        version = getattr(module, "__version__", "")
        if version:
            versions[name] = str(version)
    return versions


def forget_keys() -> int:
    """Delete every provider's API key from the keychain and from the settings file.

    The uninstaller runs this before it removes the program files: the keys live in the
    Windows Credential Manager, which no uninstaller can enumerate.  No Qt application is
    needed — ``QSettings`` works without one.
    """
    from teachers_teammate.gui._credential_store import CredentialStore  # noqa: PLC0415
    from teachers_teammate.infrastructure import llm_factory  # noqa: PLC0415

    store = CredentialStore()
    for provider in llm_factory.list_providers():
        env_key = str(llm_factory.get_provider_info(provider).get("env_key", ""))
        if env_key:
            store.remove(env_key)
    return 0


def run_smoke_test(out_path: Path) -> int:
    """Run every check, write the JSON result to *out_path*, return 0 when all pass.

    The file is written even when a check fails, so a CI job can upload it as an artifact.
    """
    failed: list[str] = []
    for name, check in _checks():
        try:
            problem = check()
        except Exception as exc:  # noqa: BLE001 — a check that raises is a failed check
            problem = f"{type(exc).__name__}: {exc}"
        if problem:
            failed.append(f"{name}: {problem}")
    result = {"ok": not failed, "failed": failed, "versions": _versions()}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if not failed else 1
