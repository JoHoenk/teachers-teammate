"""The hidden modes of the installed app: the smoke test's checks and the key cleanup."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from teachers_teammate import frozen_modes
from teachers_teammate.gui import _credential_store
from teachers_teammate.gui._credential_store import KEYRING_SERVICE

from ..gui._settings_stubs import FakeKeyring

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
    monkeypatch.setattr(_credential_store, "_keyring", lambda: fake)
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
    assert {"imports", "qt", "reader", "pdf", "docx", "providers"} <= set(names)


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

    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    bundle = tmp_path / "bundle"
    pins = bundle / addon_manager.ADDON_CONSTRAINTS_RELATIVE
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

    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    bundle = tmp_path / "bundle"
    lock = bundle / addon_manager.ADDON_LOCK_RELATIVE
    lock.parent.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    names = addon_manager.addon_package_names()
    lock.write_text("".join(f"{name}==1.0\n" for name in names), encoding="utf-8")

    assert frozen_modes._check_addon_lock() is None

    lock.write_text(
        "".join(f"{name}==1.0\n" for name in names if name != "spacy"), encoding="utf-8"
    )
    assert "spacy" in (frozen_modes._check_addon_lock() or "")

    lock.unlink()
    assert "is not in the bundle" in (frozen_modes._check_addon_lock() or "")


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


def test_gpl_only_qt_files_names_the_library_and_the_python_module() -> None:
    """Given file names of a bundle, When they are checked, Then only GPL-only Qt modules are named."""
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

    assert frozen_modes.gpl_only_qt_files(names) == [
        "Qt6Graphs.dll",
        "Qt6GraphsWidgets.dll",
        "QtCharts.abi3.so",
        "QtQuick3D.pyd",
        "libQt6Charts.so.6",
    ]


def test_qt_license_check_fails_on_a_gpl_only_module(tmp_path: Path, monkeypatch) -> None:
    """Given a bundle folder with QtCharts, When the license check runs, Then it names the file."""
    lib = tmp_path / "PySide6" / "Qt" / "lib"
    lib.mkdir(parents=True)
    (lib / "libQt6Core.so.6").write_bytes(b"")
    (lib / "libQt6Charts.so.6").write_bytes(b"")
    monkeypatch.setattr(frozen_modes, "_root", lambda: tmp_path)

    problem = frozen_modes._check_qt_licenses()

    assert problem is not None
    assert "libQt6Charts.so.6" in problem
    assert "libQt6Core" not in problem


def test_qt_license_check_passes_on_an_lgpl_bundle(tmp_path: Path, monkeypatch) -> None:
    """Given a bundle folder with only LGPL Qt modules, When the license check runs, Then it passes."""
    (tmp_path / "libQt6Core.so.6").write_bytes(b"")
    (tmp_path / "QtWidgets.abi3.so").write_bytes(b"")
    monkeypatch.setattr(frozen_modes, "_root", lambda: tmp_path)

    assert frozen_modes._check_qt_licenses() is None


def test_spec_drops_the_same_gpl_only_modules_as_the_smoke_test() -> None:
    """Given the build spec, When its GPL-only list is read, Then it equals the smoke test's list."""
    spec = _SPEC
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    listed = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "GPL_ONLY_QT_MODULES" for t in node.targets)
    )

    assert tuple(listed) == frozen_modes.GPL_ONLY_QT_MODULES
