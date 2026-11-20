"""Every tool binary starts: ``--help`` exits 0, and so does each subcommand's ``--help``.

A tool that cannot import its main module fails here, not when a developer runs it.  The binary is
started the way a person starts it, with the runfiles Bazel gives it, so a package the target forgot
to name is a failure and not a traceback on somebody's machine.

``--help`` is the cheapest thing a tool can be asked to do: argparse answers it before any file is
read and before any server is contacted, so a failure is the start-up itself.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

WORKSPACE = "_main"
"""The workspace directory inside the runfiles tree."""

BINARIES: dict[str, tuple[str, ...]] = {
    "tools/compare_mistral_regions": (),
    "tools/evaluate": ("run",),
    "tools/extract_test_specs": (),
    "tools/ocr_page_tokens": (),
    "tools/prepare_gec_testset": (),
    "tools/report": ("table", "compare", "gate", "passes", "sheet", "page"),
    "tools/store": (
        "convert",
        "validate",
        "marks",
        "accept-marks",
        "manifest",
    ),
    "tools/verify_model_catalog": (),
    "tools/evaluation/gpu_agent": (),
}
"""Every tool binary, and the subcommands it has; a tool without subcommands has an empty tuple."""

TIMEOUT_S = 120.0
"""How long one start may take: a cold import of the app's runtime, and nothing else."""


def _root() -> Path:
    """Return the tree the binaries are found in: the runfiles root, or the repository."""
    found = os.environ.get("RUNFILES_DIR", "")
    if found:
        return Path(found) / WORKSPACE
    return Path(__file__).resolve().parents[2]


def _help(path: Path, *args: str) -> subprocess.CompletedProcess:
    """Return what ``path *args --help`` printed and exited with."""
    return subprocess.run(
        [str(path), *args, "--help"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def _problems(name: str, *args: str) -> list[str]:
    """Return what went wrong starting the binary *name* with *args*, or nothing."""
    path = _root() / name
    if not path.is_file():
        return [f"{name}: not in the runfiles"]
    done = _help(path, *args)
    if done.returncode == 0:
        return []
    command = " ".join((name, *args, "--help"))
    return [f"{command}: exit {done.returncode}\n{done.stderr.strip()}"]


def test_every_tool_binary_starts():
    """
    Given  every tool binary of ``tools/`` and ``tools/evaluation/``
    When   each one is asked for its help
    Then   it exits 0, so its main module and everything it pulls in at start-up import
    """
    problems = [problem for name in BINARIES for problem in _problems(name)]

    assert problems == [], "\n\n".join(problems)


def test_every_subcommand_starts():
    """
    Given  every tool binary that has subcommands
    When   each subcommand is asked for its help
    Then   it exits 0, so the subcommand's own imports are reached too
    """
    problems = [
        problem
        for name, subcommands in BINARIES.items()
        for subcommand in subcommands
        for problem in _problems(name, subcommand)
    ]

    assert problems == [], "\n\n".join(problems)
