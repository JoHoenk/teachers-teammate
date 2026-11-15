"""The hidden modes of the installed app: the smoke test's checks and the key cleanup."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from teachers_teammate import frozen_modes
from teachers_teammate.gui import _credential_store
from teachers_teammate.gui._credential_store import KEYRING_SERVICE

from ..gui._settings_stubs import FakeKeyring


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
    spec = Path(__file__).resolve().parents[2] / "tools" / "build" / "teachers-teammate.spec"
    tree = ast.parse(spec.read_text(encoding="utf-8"))
    listed = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "GPL_ONLY_QT_MODULES" for t in node.targets)
    )

    assert tuple(listed) == frozen_modes.GPL_ONLY_QT_MODULES
