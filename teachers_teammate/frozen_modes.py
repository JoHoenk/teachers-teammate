"""Hidden command-line modes of the installed app.

The frozen build is ``--windowed``: on Windows it has no console, so these modes report
through a file and through the exit code. ``tools/build/run_gui.py`` dispatches them
before the splash screen and before any heavy import.

``run_smoke_test`` is what proves a build is complete: the modules the app loads lazily,
Qt's image plugins, the reader's OpenCV runtime, PDF and DOCX writing, the provider list,
the Setup Assistant building a plan, the bundled assets, the model files the app carries
itself, the absence of GPL-only Qt modules, the pip dispatch and the add-on constraints and
lock. CI runs it on every build
(see ``.github/workflows/build_artifacts.yml``) and the Windows installer test runs it again
on the installed copy.

``run_addon_smoke_test`` installs every add-on through the app's own pip and merge and uses it;
it needs the network, so CI runs it as a step of its own.

``forget_keys`` is what the uninstaller calls before it removes the program files: the API
keys sit in the system keychain, which no uninstaller can enumerate.

``run_script`` runs a script handed to the app — ``--_run_script <script.py> <file.json> [args…]``
— in the app's own process.  The app walkthrough is such a script: it builds the real main
window and plays a teacher, so no test-only code ships in the bundle, and a changed script runs
against an installed copy without rebuilding it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import importlib
import importlib.metadata
from io import BytesIO
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication

    from teachers_teammate.application.composition import AppServices
    from teachers_teammate.application.setup_plan import SetupAnswers, SetupPlan
    from teachers_teammate.gui.setup import SetupAssistant

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

# The address the Setup Assistant check points the app at.  Nothing listens on port 9, so the
# answer is the same on every machine and an Ollama on a developer's computer cannot change it.
SETUP_OLLAMA_URL = "http://127.0.0.1:9"

# How long the Setup Assistant check waits for the setup plan to arrive.  A module constant, so a test
# can lower it instead of waiting half a minute for a timeout it provoked itself.
SETUP_PLAN_TIMEOUT_MS = 30_000

# The pages the check clicks through: Welcome, Classes, Local AI, Proofreading, Privacy → Summary.
SETUP_PAGES = 5

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
    """Start Qt headless and check the image formats the app reads.

    A ``QApplication``, not a ``QGuiApplication``: the Setup Assistant check builds a ``QWizard``
    afterwards, and Qt refuses to create a ``QApplication`` once a ``QGuiApplication`` exists.
    A ``QApplication`` is a ``QGuiApplication``, so this check's own work is unaffected.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImageReader  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    if QApplication.instance() is None:
        QApplication([])
    supported = {bytes(fmt.data()).decode() for fmt in QImageReader.supportedImageFormats()}
    missing = [fmt for fmt in _IMAGE_FORMATS if fmt not in supported]
    if missing:
        return f"Qt cannot read {', '.join(missing)} (has {', '.join(sorted(supported))})"
    return None


def _setup_answers(settings: Mapping[str, object]) -> SetupAnswers:
    """Return the answers the check walks the wizard with: handwritten pages, local proofreading."""
    from teachers_teammate.application.setup_plan import (  # noqa: PLC0415
        HandIn,
        ProofreadingPlace,
        SetupAnswers,
    )

    return SetupAnswers(
        language=str(settings["language"]),
        hand_in=HandIn.HANDWRITTEN,
        proofreading=ProofreadingPlace.LOCAL,
        ollama_url=str(settings["ollama_url"]),
        hardware_profile=str(settings["hardware_profile"]),
        local_ai_shown=True,
    )


def _setup_plan_problem(plan: SetupPlan, frozen: bool) -> str | None:
    """Return what is wrong with *plan*, or ``None`` when it is what the defaults should give."""
    from teachers_teammate.application.setup_plan import DownloadKind  # noqa: PLC0415
    from teachers_teammate.domain.data_location import PROVIDER_OLLAMA  # noqa: PLC0415

    if not plan.steps:
        return "the setup plan has no step"
    provider = plan.settings.get("correction_provider")
    if provider != PROVIDER_OLLAMA:
        return f"the setup plan proofreads with {provider!r} instead of {PROVIDER_OLLAMA!r}"
    if frozen:
        # The bundle carries the page models, so the defaults (handwritten pages, automatic image
        # preparation) must not plan their download: this is the real ``setup_facts`` seeing them.
        planned = [item.name for item in plan.downloads if item.kind is DownloadKind.PAGE_MODELS]
        if planned:
            return "the bundle's page models were not found: the setup plan downloads them"
    return None


def _wait_for_setup_plan(app: QCoreApplication, assistant: SetupAssistant) -> SetupPlan | None:
    """Pump the event loop until the summary page's plan arrives, or the timeout passes."""
    deadline = time.monotonic() + SETUP_PLAN_TIMEOUT_MS / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        plan = assistant.plan()
        if plan is not None:
            return plan
        time.sleep(0.01)
    app.processEvents()
    return assistant.plan()


def _walk_setup_assistant(
    service: AppServices,
    settings: Mapping[str, object],
    frozen: bool,
) -> str | None:
    """Build the wizard on *service*, click through to the summary, check the setup plan."""
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    from teachers_teammate.gui._background import wait_for_background_tasks  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant, SetupContext  # noqa: PLC0415
    from teachers_teammate.gui.setup._flow import ORDER  # noqa: PLC0415

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    elif not isinstance(app, QApplication):
        # A QGuiApplication is already running: Qt aborts the process on a second application,
        # so say what is wrong instead of taking the whole smoke test down with it.
        return f"Qt runs as {type(app).__name__}, so no wizard can be built"
    try:
        assistant = SetupAssistant(SetupContext(settings=settings, app_service=service))
    except Exception as exc:  # noqa: BLE001 — a page that cannot be built is a failed check
        return f"the wizard could not be built ({type(exc).__name__}: {exc})"
    try:
        assistant.show()
        for _ in range(SETUP_PAGES):
            assistant.next()
        plan = _wait_for_setup_plan(app, assistant)
        if plan is None:
            current = assistant.currentId()
            page = ORDER[current] if current >= 0 else "no page"
            return f"no setup plan after {SETUP_PLAN_TIMEOUT_MS / 1000:g} s, stuck on {page}"
        return _setup_plan_problem(plan, frozen)
    finally:
        assistant.reject()
        assistant.deleteLater()
        app.processEvents()
        wait_for_background_tasks()


def _check_setup_assistant() -> str | None:
    """Build the Setup Assistant on the real service and walk it to the setup plan.

    The service first, on this thread: a failure inside ``setup_facts`` keeps its own exception
    type and message, which the summary page would throw away and show as a headline.  Then the
    wizard itself, so the pages, the thread pool and the setup plan arriving from it are exercised too.

    Nothing lands in the user's folders: the cache, the config root and the data root are one
    temporary folder for the duration of the check.
    """
    from teachers_teammate.application.composition import build_app_services  # noqa: PLC0415
    from teachers_teammate.config import DEFAULTS  # noqa: PLC0415

    frozen = bool(getattr(sys, "frozen", False))
    previous = {
        name: os.environ.get(name) for name in ("QT_QPA_PLATFORM", "TEACHERS_TEAMMATE_TMPDIR")
    }
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        with tempfile.TemporaryDirectory(
            prefix="teachers-teammate-smoke-", ignore_cleanup_errors=True
        ) as folder:
            os.environ["TEACHERS_TEAMMATE_TMPDIR"] = folder
            settings = dict(DEFAULTS)
            settings["ollama_url"] = SETUP_OLLAMA_URL
            # The real detection runs in the frozen process: subprocesses, psutil, the platform's
            # own memory query.  That is part of what this check is for.
            settings["hardware_profile"] = "auto"
            service = build_app_services()
            plan = service.setup.plan_setup(_setup_answers(settings), settings)
            problem = _setup_plan_problem(plan, frozen)
            if problem is not None:
                return problem
            return _walk_setup_assistant(service, settings, frozen)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _check_stylesheet() -> str | None:
    """Load the dark stylesheet the way the main window does.

    ``import qdarkstyle`` alone is not enough: qdarkstyle imports ``qtpy`` (and qtpy imports
    ``packaging``) only when the stylesheet is loaded, so a bundle without either one passes the
    import check and then fails when the window opens.
    """
    import qdarkstyle  # noqa: PLC0415

    if not qdarkstyle.load_stylesheet(qt_api="pyside6"):
        return "qdarkstyle.load_stylesheet returned an empty stylesheet"
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
    from teachers_teammate.infrastructure.subprocess_utils import (  # noqa: PLC0415
        child_env,
    )

    try:
        result = subprocess.run(
            [sys.executable, "--_pip_install_mode", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=child_env(),
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
        ("stylesheet", _check_stylesheet),
        ("setup assistant", _check_setup_assistant),
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


def _reported_version(
    name: str, module: object, import_module: Callable[[str], object] = importlib.import_module
) -> str:
    """Return the version of the distribution behind *module*, ``""`` when it carries none.

    ``cv2.__version__`` is OpenCV's own version (``5.1.0``); the wheel's build number, which is
    the version ``third_party_licenses/inventory.csv`` names (``5.1.0.95``), is kept apart in
    ``cv2.version.opencv_version``.  Reporting the wrong one fails every bundle's smoke check.
    """
    if name == "cv2":
        try:
            return str(getattr(import_module("cv2.version"), "opencv_version", ""))
        except ImportError:
            # A wheel without ``cv2.version`` has no build number to report; the check fails.
            return ""
    return str(getattr(module, "__version__", "") or "")


def _versions() -> dict[str, str]:
    """Return the versions of the runtime packages, for the result file."""
    versions = {"python": sys.version.split()[0]}
    for name in ("PySide6", "cv2", "numpy", "pypdfium2", "docx", "ollama", "keyring"):
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — a missing package is a failed check, not a crash
            continue
        version = _reported_version(name, module)
        if version:
            versions[name] = version
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
        info = llm_factory.get_provider_info(provider)
        env_key = info.env_key if info is not None else ""
        if env_key:
            store.remove(env_key)
    return 0


def set_update_check(value: str, folder: str = "") -> int:
    """Switch the start-up update check on (``"on"``) or off (``"off"``) in the settings file.

    The installer runs this for the box on its second page.  Only the one key changes: the file
    is read, the key set and the file written back the way the main window saves it, so a
    teacher's other settings stay (comments do not: the main window does not keep them either).
    A settings file that cannot be read is left alone and the exit code is 1; the installer
    goes on, and the check stays as it was.

    Args:
        value: ``"on"`` or ``"off"``; anything else is refused (exit code 2).
        folder: The settings folder of the teacher.  Empty means the folder of the account that
            runs this.  An install that Windows runs with the rights of another account (an
            administrator's password typed for a teacher) names the teacher's folder here.
    """
    from teachers_teammate.config import (  # noqa: PLC0415
        ConfigFileParseError,
        load_config_file,
        save_settings_file,
    )
    from teachers_teammate.infrastructure.storage_root import (  # noqa: PLC0415
        default_config_path,
        resolve_config_path,
    )

    if value not in ("on", "off"):
        return 2
    try:
        if folder:
            path = Path(folder) / default_config_path().name
        else:
            # The settings an earlier version left in the cache folder are moved first, so the
            # new file does not hide them.
            path = resolve_config_path(None) or default_config_path()
        values = load_config_file(path).values if path.is_file() else {}
        values["check_for_updates"] = value == "on"
        save_settings_file(path, values)
    except ConfigFileParseError, OSError:
        # The exit code carries this to the installer; the settings file is left as it is.
        return 1
    return 0


#: The add-on check hides a name in this sentence, with the model of this language.
_ADDON_CHECK_LANGUAGE = "English"
_ADDON_CHECK_TEXT = "Yesterday Anna Schmidt moved to Berlin."
_ADDON_CHECK_NAME = "Schmidt"
#: How many lines of pip's output the result file keeps when a check failed.
_ADDON_LOG_TAIL = 200


def _install_addons(target: Path, log: list[str]) -> list[str]:
    """Install every add-on and the check language's spaCy model into *target*; return failures."""
    from teachers_teammate.domain.name_models import spacy_model_for  # noqa: PLC0415
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    problems: list[str] = []
    for addon in addon_manager.addon_requirements():
        try:
            addon_manager.install_addon_subprocess(addon, target, log.append)
        except (OSError, RuntimeError) as exc:
            problems.append(f"install {addon}: {exc}")
    model = spacy_model_for(_ADDON_CHECK_LANGUAGE)
    try:
        addon_manager.download_spacy_model_subprocess(model, target, log.append)
    except (OSError, RuntimeError) as exc:
        problems.append(f"download {model}: {exc}")
    return problems


def _use_addons(target: Path) -> None:
    """Put *target* first on the import path, as ``inject_packages_dir`` does at a start."""
    sys.path.insert(0, str(target))
    if sys.platform == "win32" and target.is_dir():
        os.add_dll_directory(str(target))
    importlib.invalidate_caches()


def _check_addon_imports() -> list[str]:
    """Import the module of every add-on; name the ones that fail."""
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    problems: list[str] = []
    for addon in addon_manager.addon_requirements():
        module = addon_manager.addon_module(addon)
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 — any failure is what this check looks for
            problems.append(f"import {module}: {type(exc).__name__}: {exc}")
    return problems


def _check_name_detection() -> str | None:
    """Hide a name with spaCy the way a run does."""
    from teachers_teammate.infrastructure.anonymizer import SpacyAnonymizer  # noqa: PLC0415

    anonymized, _mapping = SpacyAnonymizer(_ADDON_CHECK_LANGUAGE).anonymize(_ADDON_CHECK_TEXT)
    if _ADDON_CHECK_NAME in anonymized:
        return f"the name was not hidden: {anonymized!r}"
    return None


def run_addon_smoke_test(out_path: Path) -> int:
    """Install every add-on as the Add-ons page does, use it, write the JSON result; 0 if all pass.

    The add-ons go into a temporary folder through the app's own pip and merge, so a package the
    bundle promises (``addon_constraints.txt``) but carries only in part shows up here.  Needs the
    network; CI runs it on every build.  Only the installed app: from source, pip would install
    into the active environment.
    """
    failed: list[str] = []
    log: list[str] = []
    if not getattr(sys, "frozen", False):
        failed.append("add-ons are installed only by the installed app")
    else:
        with tempfile.TemporaryDirectory(
            prefix="teachers-teammate-addons-", ignore_cleanup_errors=True
        ) as folder:
            target = Path(folder) / "packages"
            failed += _install_addons(target, log)
            _use_addons(target)
            failed += _check_addon_imports()
            try:
                problem = _check_name_detection()
            except Exception as exc:  # noqa: BLE001 — a check that raises is a failed check
                problem = f"{type(exc).__name__}: {exc}"
            if problem:
                failed.append(f"name detection: {problem}")
    result: dict[str, object] = {"ok": not failed, "failed": failed}
    if failed:
        result["pip"] = [line.rstrip() for line in log[-_ADDON_LOG_TAIL:]]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if not failed else 1


def _write_result(out_path: Path, result: dict[str, object]) -> None:
    """Write *result* as the JSON file a walkthrough run is read from."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def _script_exit(exc: SystemExit) -> tuple[int, str]:
    """Return the exit code a ``SystemExit`` stands for and its message, if it carries one.

    A message counts as code 1, as Python counts it.
    """
    code = exc.code
    if code is None:
        return 0, ""
    if isinstance(code, int):
        return code, ""
    return 1, str(code)


def _add_failure(out_path: Path, failure: str, extra: dict[str, object]) -> None:
    """Add *failure* to the result the script already wrote, or write a result of its own.

    What the script recorded before it failed stays: a walk that got through nine steps says so.
    """
    result: dict[str, object] = {}
    try:
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            result = loaded
    except OSError, ValueError:
        # No result file to read, or one this run cannot use: the failure alone is the result.
        pass
    failed = result.get("failed")
    result["ok"] = False
    result["failed"] = [*(failed if isinstance(failed, list) else []), failure]
    result.update(extra)
    _write_result(out_path, result)


def run_script(script: Path, out_path: Path, args: list[str]) -> int:
    """Run *script* in this process as ``__main__``; 0 when what it checked passed.

    The script writes the result file itself.  This runner adds to it when the script failed
    without saying so (an exception that got away, an exit with a message) and writes it when
    there is none (a missing script, a script that ended without a result).  A result left by an
    earlier run is removed first, so a script that writes nothing cannot pass on it.  On Windows
    the windowed build has no stderr, so the file is the only place a crash shows.

    Args:
        script: The path of the script to run.
        out_path: The file the result belongs in; the script gets it as its first argument.
        args: Further arguments for the script.

    Returns:
        0, the exit code the script chose, or 1 for a failure of its own.
    """
    out_path.unlink(missing_ok=True)
    if not script.is_file():
        _write_result(out_path, {"ok": False, "failed": [f"no script at {script}"]})
        return 2
    previous = sys.argv
    sys.argv = [str(script), str(out_path), *args]
    code = 0
    message = ""
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code, message = _script_exit(exc)
    except BaseException as exc:  # noqa: BLE001 — anything the script raises is its failure
        _add_failure(
            out_path,
            f"uncaught: {type(exc).__name__}: {exc}",
            {"traceback": traceback.format_exc()},
        )
        return 1
    finally:
        sys.argv = previous
    if message:
        _add_failure(out_path, f"exit: {message}", {})
    elif not out_path.is_file():
        _write_result(out_path, {"ok": False, "failed": ["the script wrote no result"]})
        return code or 1
    return code


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
