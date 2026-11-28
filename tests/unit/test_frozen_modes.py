"""The hidden modes of the installed app: the smoke test's checks and the key cleanup."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from teachers_teammate import frozen_modes
from teachers_teammate.infrastructure import credential_store
from teachers_teammate.infrastructure.credential_store import KEYRING_SERVICE

from ..fakes.fake_keyring import FakeKeyring

#: The build spec, as Bazel's runfiles see it or in the source tree when the test runs directly.
_SPEC = (
    Path(os.environ["TEST_SRCDIR"]) / os.environ["BUILD_SPEC"]
    if os.environ.get("TEST_SRCDIR") and os.environ.get("BUILD_SPEC")
    else Path(__file__).resolve().parents[2] / "tools" / "build" / "teachers-teammate.spec"
)


@pytest.fixture(name="keychain")
def _keychain(monkeypatch) -> FakeKeyring:
    """A working system keychain (the settings-file fixture runs first and turns it off)."""
    fake = FakeKeyring()
    monkeypatch.setattr(credential_store, "_keyring", lambda: fake)
    return fake


def test_smoke_test_passes_in_source_mode(tmp_path: Path) -> None:
    """Given the source tree, When the smoke test runs, Then it passes and writes its result."""
    out = tmp_path / "smoke.json"

    exit_code = frozen_modes.run_smoke_test(out)

    assert exit_code == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["failed"] == []
    assert result["versions"]["python"]


def test_smoke_test_names_a_module_that_does_not_import(tmp_path: Path, monkeypatch) -> None:
    """Given a module that cannot be imported, When the smoke test runs, Then it fails and names it."""
    monkeypatch.setattr(
        frozen_modes,
        "_LAZY_MODULES",
        ("qdarkstyle", "teachers_teammate.does_not_exist"),
    )
    out = tmp_path / "smoke.json"

    exit_code = frozen_modes.run_smoke_test(out)

    assert exit_code == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert any("teachers_teammate.does_not_exist" in line for line in result["failed"])


def test_frozen_only_checks_are_skipped_in_source_mode() -> None:
    """Given a source run, When the checks are listed, Then the frozen-only ones are absent."""
    names = [name for name, _ in frozen_modes._checks()]

    assert "assets" not in names
    assert "pip" not in names
    assert "addon constraints" not in names
    assert "ca store" not in names
    assert {"imports", "qt", "reader", "pdf", "docx", "providers"} <= set(names)


def test_stylesheet_check_loads_the_stylesheet_in_every_mode() -> None:
    """
    Given  a source run
    When   the checks are listed and the stylesheet check runs
    Then   it is listed, and loading the stylesheet (qdarkstyle -> qtpy -> packaging) passes
    """
    assert "stylesheet" in [name for name, _ in frozen_modes._checks()]
    assert frozen_modes._check_stylesheet() is None


def test_setup_assistant_check_is_listed_in_both_modes(tmp_path: Path, monkeypatch) -> None:
    """Given a source run and a frozen bundle, When the checks are listed, Then both build the wizard."""
    import sys  # noqa: PLC0415

    assert "setup assistant" in [name for name, _ in frozen_modes._checks()]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert "setup assistant" in [name for name, _ in frozen_modes._checks()]


def test_setup_assistant_check_names_a_service_failure(tmp_path: Path, monkeypatch) -> None:
    """Given a service whose plan_setup raises, When the smoke test runs, Then the line names it."""
    from teachers_teammate.application.setup_plan import SetupService  # noqa: PLC0415

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no plan today")

    monkeypatch.setattr(SetupService, "plan_setup", boom)
    monkeypatch.setattr(
        frozen_modes,
        "_checks",
        lambda: [("setup assistant", frozen_modes._check_setup_assistant)],
    )
    out = tmp_path / "smoke.json"

    exit_code = frozen_modes.run_smoke_test(out)

    assert exit_code == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["failed"] == ["setup assistant: RuntimeError: no plan today"]


def test_setup_assistant_check_names_a_page_that_cannot_be_built(monkeypatch) -> None:
    """Given a page class that raises, When the check runs, Then it names the page and the error."""
    from teachers_teammate.gui.setup import _pages  # noqa: PLC0415

    def boom(_self: object, *_args: object, **_kwargs: object) -> None:
        raise RuntimeError("ClassesPage cannot be built")

    monkeypatch.setattr(_pages.ClassesPage, "__init__", boom)

    problem = frozen_modes._check_setup_assistant()

    assert problem is not None
    assert "ClassesPage" in problem
    assert "RuntimeError" in problem


def test_setup_assistant_check_times_out_with_the_page_it_is_stuck_on(monkeypatch) -> None:
    """Given a plan that never arrives, When the check runs, Then it names the page it waited on."""
    from teachers_teammate.gui import _background  # noqa: PLC0415

    monkeypatch.setattr(_background.LatestTask, "submit", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(frozen_modes, "SETUP_PLAN_TIMEOUT_MS", 300)

    problem = frozen_modes._check_setup_assistant()

    assert problem is not None
    assert "stuck on summary" in problem


def test_setup_assistant_check_notices_a_bundle_without_its_page_models(
    tmp_path: Path, monkeypatch
) -> None:
    """Given a frozen bundle without the page models, When the check runs, Then it says so."""
    import sys  # noqa: PLC0415

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    problem = frozen_modes._check_setup_assistant()

    assert problem is not None
    assert "page models" in problem


def test_setup_assistant_check_leaves_the_environment_as_it_found_it(monkeypatch) -> None:
    """Given no temporary folder and no Qt platform, When the check runs and fails, Then both are back."""
    from teachers_teammate.application.setup_plan import SetupService  # noqa: PLC0415

    watched = ("QT_QPA_PLATFORM", "TEACHERS_TEAMMATE_TMPDIR")
    for name in watched:
        monkeypatch.delenv(name, raising=False)
    before = {name: os.environ.get(name) for name in watched}

    assert frozen_modes._check_setup_assistant() is None
    assert {name: os.environ.get(name) for name in watched} == before

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("no plan today")

    monkeypatch.setattr(SetupService, "plan_setup", boom)
    with pytest.raises(RuntimeError):
        frozen_modes._check_setup_assistant()

    assert {name: os.environ.get(name) for name in watched} == before


def test_forget_keys_removes_every_provider_key(credential_file, keychain) -> None:
    """Given keys of two providers and an unrelated one, When forget_keys runs, Then only the provider keys are gone."""
    credential_file["credentials/OPENAI_API_KEY"] = "sk-openai"
    credential_file["credentials/ANTHROPIC_API_KEY"] = "sk-anthropic"
    credential_file["credentials/UNRELATED"] = "keep me"
    keychain.passwords[(KEYRING_SERVICE, "OPENAI_API_KEY")] = "sk-openai"

    exit_code = frozen_modes.forget_keys()

    assert exit_code == 0
    assert credential_file == {"credentials/UNRELATED": "keep me"}
    assert keychain.passwords == {}


def test_set_update_check_changes_only_that_key(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a settings file with another setting
    When   the installer's switch turns the update check off, and later on again
    Then   the key flips, the other setting stays, and a bad value or a broken file is refused
    """
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path))
    settings = tmp_path / "ocr.toml"
    settings.write_text('language = "German"\n', encoding="utf-8")

    assert frozen_modes.set_update_check("off") == 0
    text = settings.read_text(encoding="utf-8")
    assert "check_for_updates = false" in text
    assert 'language = "German"' in text

    assert frozen_modes.set_update_check("on") == 0
    assert "check_for_updates = true" in settings.read_text(encoding="utf-8")

    assert frozen_modes.set_update_check("maybe") == 2
    settings.write_text("this is = = not toml", encoding="utf-8")
    assert frozen_modes.set_update_check("off") == 1
    assert settings.read_text(encoding="utf-8") == "this is = = not toml"


def test_set_update_check_leaves_a_settings_file_that_is_not_utf8(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a settings file an older Windows editor saved in a code page rather than UTF-8
    When   the installer's switch turns the update check off
    Then   it exits with 1 and the file keeps its bytes, instead of ending in a traceback
    """
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path))
    settings = tmp_path / "ocr.toml"
    kept = 'language = "Français"\n'.encode("cp1252")
    settings.write_bytes(kept)

    assert frozen_modes.set_update_check("off") == 1
    assert settings.read_bytes() == kept


def test_set_update_check_writes_into_the_folder_it_is_given(tmp_path: Path, monkeypatch) -> None:
    """
    Given  an install that runs as another account, told the teacher's settings folder
    When   the update check is switched off with that folder
    Then   the file is made there, and the folder of the account that runs it stays untouched
    """
    own = tmp_path / "admin"
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(own))
    teacher = tmp_path / "teacher"

    assert frozen_modes.set_update_check("off", str(teacher)) == 0

    assert "check_for_updates = false" in (teacher / "ocr.toml").read_text("utf-8")
    assert not own.exists()


def test_set_update_check_keeps_the_settings_an_earlier_version_left_in_the_cache(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  an ocr.toml in the OS cache folder, where earlier versions kept it, and none in the
           settings folder
    When   the update check is switched off without a folder
    Then   the old file is moved over first and keeps its setting, so the new file does not hide it
    """
    from teachers_teammate.infrastructure import storage_root  # noqa: PLC0415

    monkeypatch.delenv("TEACHERS_TEAMMATE_TMPDIR", raising=False)
    cache, config = tmp_path / "cache", tmp_path / "config"
    cache.mkdir()
    (cache / "ocr.toml").write_text('language = "German"\n', encoding="utf-8")
    monkeypatch.setattr(
        storage_root, "_platform_dir", lambda kind: cache if kind == "cache" else config
    )

    assert frozen_modes.set_update_check("off") == 0

    text = (config / "ocr.toml").read_text("utf-8")
    assert 'language = "German"' in text
    assert "check_for_updates = false" in text


def test_set_update_check_makes_a_missing_settings_file(tmp_path: Path, monkeypatch) -> None:
    """Given no settings file, When the update check is switched off, Then a file with that one key is made."""
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path / "fresh"))

    assert frozen_modes.set_update_check("off") == 0

    assert "check_for_updates = false" in (tmp_path / "fresh" / "ocr.toml").read_text("utf-8")


def test_addon_constraints_check_compares_the_bundle_with_what_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a frozen bundle whose constraints pin the running numpy, pydantic, requests, pillow
    When   the add-on constraints check runs, and again with numpy pinned to another version
    Then   the first passes and the second names numpy; a bundle without the file fails too
    """
    import importlib.metadata  # noqa: PLC0415
    import sys  # noqa: PLC0415

    from teachers_teammate.infrastructure.addon_list import (  # noqa: PLC0415
        ADDON_CONSTRAINTS_RELATIVE,
    )

    bundle = tmp_path / "bundle"
    pins = bundle / ADDON_CONSTRAINTS_RELATIVE
    pins.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    versions = {
        name: importlib.metadata.version(name)
        for name in frozen_modes._CONSTRAINED_RUNTIME_PACKAGES
    }
    lines = [f"{name}=={version}" for name, version in versions.items()]
    pins.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert frozen_modes._check_addon_constraints() is None

    pins.write_text("\n".join(["numpy==0.0.1", *lines[1:]]) + "\n", encoding="utf-8")
    assert "numpy" in (frozen_modes._check_addon_constraints() or "")

    pins.unlink()
    assert "is not in the bundle" in (frozen_modes._check_addon_constraints() or "")


def test_addon_lock_check_wants_every_addon_pinned(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a frozen bundle whose add-on lock pins the add-ons' own packages
    When   the add-on lock check runs, and again with spaCy left out
    Then   the first passes and the second names spacy; a bundle without the file fails too
    """
    import sys  # noqa: PLC0415

    from teachers_teammate.infrastructure.addon_list import (  # noqa: PLC0415
        ADDON_LOCK_RELATIVE,
        addon_package_names,
    )

    bundle = tmp_path / "bundle"
    lock = bundle / ADDON_LOCK_RELATIVE
    lock.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    names = addon_package_names()
    lock.write_text("".join(f"{name}==1.0\n" for name in names), encoding="utf-8")

    assert frozen_modes._check_addon_lock() is None

    lock.write_text(
        "".join(f"{name}==1.0\n" for name in names if name != "spacy"), encoding="utf-8"
    )
    assert "spacy" in (frozen_modes._check_addon_lock() or "")

    lock.unlink()
    assert "is not in the bundle" in (frozen_modes._check_addon_lock() or "")


def test_ca_store_check_wants_certificates_in_the_default_context(monkeypatch) -> None:
    """
    Given  a default TLS context that holds CA certificates, and one that holds none
    When   the CA store check runs on each
    Then   the first passes and the second names the missing store
    """
    import ssl  # noqa: PLC0415

    class Store:
        """A TLS context whose store holds *count* CA certificates."""

        def __init__(self, count: int) -> None:
            self._count = count

        def cert_store_stats(self) -> dict[str, int]:
            return {"x509": self._count, "x509_ca": self._count}

    monkeypatch.setattr(ssl, "create_default_context", lambda *args, **kwargs: Store(141))

    assert frozen_modes._check_ca_store() is None

    monkeypatch.setattr(ssl, "create_default_context", lambda *args, **kwargs: Store(0))

    assert "no CA certificate" in (frozen_modes._check_ca_store() or "")


def test_bundled_models_check_ignores_models_downloaded_earlier(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a frozen bundle without its model files, on a computer that downloaded them earlier
    When   the bundled-models check runs
    Then   it fails and names the models, instead of passing on the downloaded copy
    """
    import sys  # noqa: PLC0415

    from teachers_teammate.domain.local_models import PAGE_MODELS, READER_MODELS  # noqa: PLC0415
    from teachers_teammate.infrastructure.onnx.store import MODELS_DIR_ENV  # noqa: PLC0415

    downloads = tmp_path / "downloads"
    for model in (*READER_MODELS, *PAGE_MODELS):
        folder = downloads / model.repo.split("/")[-1] / model.revision
        folder.mkdir(parents=True)
        (folder / ".verified").write_text(model.revision, encoding="utf-8")
        for file in model.files:
            (folder / file.name).write_bytes(b"x" * file.size)
    bundle = tmp_path / "bundle"
    (bundle / "teachers_teammate" / "_models").mkdir(parents=True)
    monkeypatch.setenv(MODELS_DIR_ENV, str(downloads))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    result = frozen_modes._check_bundled_models()

    assert result is not None
    assert "not in the bundle" in result
    assert READER_MODELS[0].key in result


def test_unused_qt_files_names_the_library_and_the_python_module() -> None:
    """Given file names of a bundle, When they are checked, Then only unused Qt modules are named."""
    names = [
        "libQt6Core.so.6",
        "libQt6Charts.so.6",
        "Qt6Graphs.dll",
        "Qt6GraphsWidgets.dll",
        "QtCharts.abi3.so",
        "QtQuick3D.pyd",
        "libQt6Svg.so.6",
        "QtWidgets.abi3.so",
        "libqjpeg.so",
    ]

    assert frozen_modes.unused_qt_files(names) == [
        "Qt6Graphs.dll",
        "Qt6GraphsWidgets.dll",
        "QtCharts.abi3.so",
        "QtQuick3D.pyd",
        "libQt6Charts.so.6",
    ]


def test_qt_module_check_fails_on_an_unused_module(tmp_path: Path, monkeypatch) -> None:
    """Given a bundle folder with QtCharts, When the Qt module check runs, Then it names the file."""
    lib = tmp_path / "PySide6" / "Qt" / "lib"
    lib.mkdir(parents=True)
    (lib / "libQt6Core.so.6").write_bytes(b"")
    (lib / "libQt6Charts.so.6").write_bytes(b"")
    monkeypatch.setattr(frozen_modes, "_root", lambda: tmp_path)

    problem = frozen_modes._check_qt_modules()

    assert problem is not None
    assert "libQt6Charts.so.6" in problem
    assert "libQt6Core" not in problem


def test_qt_module_check_passes_on_a_bundle_of_used_modules(tmp_path: Path, monkeypatch) -> None:
    """Given a bundle folder with only the Qt modules the app uses, When the Qt module check runs, Then it passes."""
    (tmp_path / "libQt6Core.so.6").write_bytes(b"")
    (tmp_path / "QtWidgets.abi3.so").write_bytes(b"")
    monkeypatch.setattr(frozen_modes, "_root", lambda: tmp_path)

    assert frozen_modes._check_qt_modules() is None


def test_reported_version_takes_the_wheels_opencv_version() -> None:
    """
    Given  a cv2 whose ``__version__`` is OpenCV's own 5.1.0 and whose ``version`` module
           carries the wheel's 5.1.0.95
    When   the smoke test asks for the version to report
    Then   the wheel's comes back, which is the version the inventory names
    """
    module = SimpleNamespace(__version__="5.1.0")
    wheel = SimpleNamespace(opencv_version="5.1.0.95")

    reported = frozen_modes._reported_version("cv2", module, lambda name: wheel)

    assert reported == "5.1.0.95"


def test_reported_version_takes_the_dunder_version_of_other_packages() -> None:
    """
    Given  a PySide6 module
    When   the smoke test asks for the version to report
    Then   its ``__version__`` comes back unchanged
    """
    module = SimpleNamespace(__version__="6.12.0")

    reported = frozen_modes._reported_version("PySide6", module, lambda name: None)

    assert reported == "6.12.0"


def test_spec_drops_the_same_unused_modules_as_the_smoke_test() -> None:
    """Given the build spec, When its unused-Qt list is read, Then it equals the smoke test's list."""
    spec = _SPEC
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    listed = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "UNUSED_QT_MODULES" for t in node.targets)
    )

    assert tuple(listed) == frozen_modes.UNUSED_QT_MODULES


# ── the add-on smoke test ────────────────────────────────────────────────────


@pytest.fixture(name="frozen_addons")
def _frozen_addons(monkeypatch) -> dict[str, list[str]]:
    """A frozen app whose pip does nothing, with one add-on; returns what was installed."""
    import sys  # noqa: PLC0415

    from teachers_teammate.infrastructure import addon_list, addon_pip  # noqa: PLC0415

    installed: dict[str, list[str]] = {"addons": [], "models": []}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(addon_list, "addon_requirements", lambda: {"privacy": ["spacy"]})
    monkeypatch.setattr(addon_list, "addon_module", lambda _addon: "json")
    monkeypatch.setattr(
        addon_pip,
        "install_addon_subprocess",
        lambda addon, _target, _log, **_kw: installed["addons"].append(addon),
    )
    monkeypatch.setattr(
        addon_pip,
        "download_spacy_model_subprocess",
        lambda model, _target, _log: installed["models"].append(model),
    )
    monkeypatch.setattr(frozen_modes, "_check_name_detection", lambda: None)
    return installed


def test_addon_smoke_test_refuses_to_install_from_source(tmp_path: Path) -> None:
    """
    Given  a source run
    When   the add-on smoke test runs
    Then   it installs nothing and fails, saying only the installed app installs add-ons
    """
    out = tmp_path / "addons.json"

    exit_code = frozen_modes.run_addon_smoke_test(out)

    assert exit_code == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["failed"] == ["add-ons are installed only by the installed app"]


def test_addon_smoke_test_installs_every_addon_and_the_model(
    tmp_path: Path, frozen_addons: dict[str, list[str]]
) -> None:
    """
    Given  a frozen app whose add-ons install and import
    When   the add-on smoke test runs
    Then   it installs every add-on and the English spaCy model and passes
    """
    out = tmp_path / "addons.json"

    exit_code = frozen_modes.run_addon_smoke_test(out)

    assert exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"ok": True, "failed": []}
    assert frozen_addons == {"addons": ["privacy"], "models": ["en_core_web_sm"]}


def test_addon_smoke_test_names_an_addon_that_installs_but_does_not_import(
    tmp_path: Path, frozen_addons: dict[str, list[str]], monkeypatch
) -> None:
    """
    Given  an add-on that installs but whose module fails to import (a bundled package is partial)
    When   the add-on smoke test runs
    Then   it fails and names the module and the error
    """
    from teachers_teammate.infrastructure import addon_list  # noqa: PLC0415

    monkeypatch.setattr(addon_list, "addon_module", lambda _addon: "teachers_teammate.nope")
    out = tmp_path / "addons.json"

    exit_code = frozen_modes.run_addon_smoke_test(out)

    assert exit_code == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["failed"] == [
        "import teachers_teammate.nope: ModuleNotFoundError: No module named 'teachers_teammate.nope'"
    ]
    assert "pip" in result


def test_addon_smoke_test_names_a_failed_install_and_a_failed_name_detection(
    tmp_path: Path, frozen_addons: dict[str, list[str]], monkeypatch
) -> None:
    """
    Given  an install that fails offline and name detection that raises
    When   the add-on smoke test runs
    Then   both failures are listed
    """
    from teachers_teammate.exceptions import AddonInstallError, InstallFailure  # noqa: PLC0415
    from teachers_teammate.infrastructure import addon_pip  # noqa: PLC0415

    def offline(*_args: object, **_kwargs: object) -> None:
        raise AddonInstallError(InstallFailure.OFFLINE)

    def no_spacy() -> None:
        raise ImportError("spacy is installed but cannot be loaded: packaging.requirements")

    monkeypatch.setattr(addon_pip, "install_addon_subprocess", offline)
    monkeypatch.setattr(frozen_modes, "_check_name_detection", no_spacy)
    out = tmp_path / "addons.json"

    exit_code = frozen_modes.run_addon_smoke_test(out)

    assert exit_code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["failed"] == [
        "install privacy: offline",
        "name detection: ImportError: spacy is installed but cannot be loaded: "
        "packaging.requirements",
    ]


def _script(tmp_path: Path, body: str) -> Path:
    """Write *body* as a script a run is asked to play."""
    path = tmp_path / "walk.py"
    path.write_text(body, encoding="utf-8")
    return path


def _writes_the_result(result: str) -> str:
    """Return a script body that writes *result* to the file it is given, and then ends."""
    return (
        f"import pathlib, sys\npathlib.Path(sys.argv[1]).write_text({result!r}, encoding='utf-8')\n"
    )


@pytest.mark.parametrize(
    ("ending", "expected"),
    [
        ("raise SystemExit(0)", 0),
        ("raise SystemExit(3)", 3),
        ("raise SystemExit()", 0),
        ("raise SystemExit('a message')", 1),
    ],
)
def test_run_script_passes_the_exit_code_through(
    tmp_path: Path, ending: str, expected: int
) -> None:
    """
    Given  a script that ends by exiting with a code, no code, or a message
    When   the app runs it
    Then   the run ends with that code (a message counting as 1, as Python counts it)
    """
    script = _script(tmp_path, _writes_the_result("{}") + ending)
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(script, out, []) == expected


def test_run_script_gives_the_script_its_arguments_and_restores_them(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  the arguments the app was started with
    When   a script is run with further arguments of its own
    Then   the script sees the script path, the result file and those arguments, and the app's
           own arguments are back afterwards
    """
    import sys  # noqa: PLC0415

    script = _script(
        tmp_path,
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv), encoding='utf-8')\n",
    )
    out = tmp_path / "e2e.json"
    monkeypatch.setattr(sys, "argv", ["teachers-teammate", "--_run_script"])

    assert frozen_modes.run_script(script, out, ["--screenshots", "shots"]) == 0

    assert json.loads(out.read_text(encoding="utf-8")) == [
        str(script),
        str(out),
        "--screenshots",
        "shots",
    ]
    assert sys.argv == ["teachers-teammate", "--_run_script"]


def test_run_script_records_an_exception_that_got_away(tmp_path: Path) -> None:
    """
    Given  a script that fails without writing anything
    When   the app runs it
    Then   the run fails with 1 and the result names the error type, its message and its
           traceback, which is the only trace a windowed build has
    """
    script = _script(tmp_path, "raise ValueError('the reader is missing')\n")
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(script, out, []) == 1

    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["failed"] == ["uncaught: ValueError: the reader is missing"]
    assert "walk.py" in result["traceback"]


def test_run_script_names_a_script_that_is_not_there(tmp_path: Path) -> None:
    """
    Given  the path of a script that does not exist
    When   the app is asked to run it
    Then   it stops with 2 and the result names the path
    """
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(tmp_path / "missing.py", out, []) == 2

    assert json.loads(out.read_text(encoding="utf-8"))["failed"] == [
        f"no script at {tmp_path / 'missing.py'}"
    ]


def test_run_script_leaves_the_result_the_script_wrote_alone(tmp_path: Path) -> None:
    """
    Given  a script that writes its own result
    When   the app runs it
    Then   the result file is exactly what the script wrote
    """
    script = _script(tmp_path, _writes_the_result('{"ok": true, "steps": []}'))
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(script, out, []) == 0

    assert out.read_text(encoding="utf-8") == '{"ok": true, "steps": []}'


def test_run_script_keeps_what_the_script_wrote_before_it_failed(tmp_path: Path) -> None:
    """
    Given  a script that wrote its steps so far and then raised
    When   the app runs it
    Then   the result keeps those steps and adds the failure and the traceback
    """
    script = _script(
        tmp_path,
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'ok': True, 'steps': ['run']}))\n"
        "raise ValueError('step two broke')\n",
    )
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(script, out, []) == 1

    result = json.loads(out.read_text(encoding="utf-8"))
    assert (result["ok"], result["steps"]) == (False, ["run"])
    assert result["failed"] == ["uncaught: ValueError: step two broke"]
    assert "traceback" in result


def test_run_script_does_not_pass_on_the_result_of_an_earlier_run(tmp_path: Path) -> None:
    """
    Given  a result file an earlier run left behind, saying ok
    When   a script that writes nothing is run
    Then   the earlier result is gone: the run fails and says the script wrote none
    """
    out = tmp_path / "e2e.json"
    out.write_text('{"ok": true, "steps": []}', encoding="utf-8")

    assert frozen_modes.run_script(_script(tmp_path, "pass\n"), out, []) == 1

    assert json.loads(out.read_text(encoding="utf-8"))["failed"] == ["the script wrote no result"]


def test_run_script_records_the_message_of_an_exit(tmp_path: Path) -> None:
    """
    Given  a script that exits with a message and wrote no result
    When   the app runs it
    Then   the exit code is 1 and the result carries the message, which a windowed build cannot
           print
    """
    script = _script(tmp_path, "raise SystemExit('the reader is missing')\n")
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(script, out, []) == 1

    result = json.loads(out.read_text(encoding="utf-8"))
    assert (result["ok"], result["failed"]) == (False, ["exit: the reader is missing"])


def test_run_script_records_a_script_that_wrote_no_result(tmp_path: Path) -> None:
    """
    Given  a script that ends without writing its result
    When   the app runs it
    Then   the run fails with 1 and the result says so
    """
    script = _script(tmp_path, "pass\n")
    out = tmp_path / "e2e.json"

    assert frozen_modes.run_script(script, out, []) == 1

    result = json.loads(out.read_text(encoding="utf-8"))
    assert (result["ok"], result["failed"]) == (False, ["the script wrote no result"])
