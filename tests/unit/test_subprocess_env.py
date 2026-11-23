"""Guards for the environment a child process gets.

The window puts the teacher's stored API keys into its own environment, where the provider
libraries read them.  A child that inherits it can keep them for the life of the session — a
detached ``ollama serve`` outlives the app — so every child is started with
:func:`~teachers_teammate.infrastructure.subprocess_utils.child_env`.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from teachers_teammate.infrastructure.subprocess_utils import child_env

_PACKAGE = "teachers_teammate"


def _package_dir() -> Path:
    """Return the package folder, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / _PACKAGE
    return Path(__file__).resolve().parents[2] / _PACKAGE


_SPAWNING = {"run", "Popen", "call", "check_output", "check_call", "getoutput"}


def _is_subprocess_module(node: ast.expr) -> bool:
    """Whether *node* is the ``subprocess`` module, however it was imported."""
    return isinstance(node, ast.Name) and node.id == "subprocess"


def _spawn_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``subprocess`` call that starts a child process."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SPAWNING
        and _is_subprocess_module(node.func.value)
    ]


def _passes_environment(call: ast.Call) -> bool:
    """Whether *call* gives its child an environment of its own."""
    return any(keyword.arg in {None, "env"} for keyword in call.keywords)


def test_every_child_process_gets_its_own_environment() -> None:
    """
    Given  every module of the app
    When   the calls that start a child process are read
    Then   each passes ``env=`` (or unpacks a dict that holds it), so no key is inherited
    """
    offenders: list[str] = []
    for path in sorted(_package_dir().rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(
            f"{path.name}:{call.lineno}"
            for call in _spawn_calls(tree)
            if not _passes_environment(call)
        )

    assert offenders == []


def test_the_child_environment_keeps_the_keys_out_and_the_path_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a provider key in the app's own environment, as the window leaves it
    When   child_env() is called
    Then   the key is gone and the rest of the environment is there
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-a-child")

    environment = child_env()

    assert "OPENAI_API_KEY" not in environment
    assert environment.get("PATH") == os.environ.get("PATH")
