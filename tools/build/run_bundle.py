#!/usr/bin/env python3
"""Run the frozen app the build just made, and say why when it fails.

The bundle's checks (the smoke test, the add-on test, the update banner, the walkthrough) all start
the same executable and read the same kind of result file.  Where that executable is, how a window
is given to it on Linux, and what to print when it fails are written here once, for the three
systems, instead of in a step per system and check.

Usage (from the repository root, after ``tools/build/build_standalone.py``):

    python tools/build/run_bundle.py --result smoke.json -- --_smoke_test smoke.json
    python tools/build/run_bundle.py --result e2e.json --display xvfb -- \\
        --_run_script tests/e2e/walkthrough.py e2e.json --screenshots e2e-shots

Everything after ``--`` goes to the app.  The exit code is the app's; on a failure the result file
is printed, or a line saying there is none (a windowed build has no stderr, and Qt can abort before
the first line of a script).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import subprocess
import sys

BUNDLE = "teachers-teammate"

#: How a Linux run gets a display: none of its own (``offscreen``, no window is built), or a
#: virtual X server (``xvfb``), which the checks that build the real main window need.
DISPLAYS = ("offscreen", "xvfb")

Run = Callable[..., "subprocess.CompletedProcess[bytes]"]


def executable(platform: str, root: Path) -> Path:
    """Return the app inside the bundle under *root*, as the build leaves it on *platform*."""
    if platform == "darwin":
        return root / "dist" / f"{BUNDLE}.app" / "Contents" / "MacOS" / BUNDLE
    suffix = ".exe" if platform == "win32" else ""
    return root / "dist" / BUNDLE / f"{BUNDLE}{suffix}"


def command(
    platform: str, app: Path, arguments: Sequence[str], display: str
) -> tuple[list[str], dict[str, str]]:
    """Return the command line and the extra environment that start *app* with *arguments*.

    Only Linux needs a display of its own; on the other systems *display* is ignored.
    """
    if platform != "linux":
        return [str(app), *arguments], {}
    if display == "xvfb":
        return ["xvfb-run", "-a", str(app), *arguments], {}
    return [str(app), *arguments], {"QT_QPA_PLATFORM": "offscreen"}


def report(result: Path) -> None:
    """Print what the app left in *result*, or that it left nothing."""
    if result.is_file():
        print(result.read_text(encoding="utf-8", errors="replace"))
    else:
        print(f"the app wrote no result ({result})")


def run_bundle(
    result: Path,
    arguments: Sequence[str],
    *,
    display: str = "offscreen",
    platform: str = sys.platform,
    root: Path = Path(),
    environ: Mapping[str, str] | None = None,
    run: Run = subprocess.run,
) -> int:
    """Run the bundle with *arguments* and return its exit code, reporting *result* on failure."""
    argv, extra = command(platform, executable(platform, root), arguments, display)
    env = {**(os.environ if environ is None else environ), **extra}
    code = run(argv, env=env, check=False).returncode
    if code != 0:
        print(f"the app exited with {code}")
        report(result)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bundle as the command line says."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", type=Path, required=True, help="the file the app writes")
    parser.add_argument("--display", choices=DISPLAYS, default="offscreen")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="after --: the app's own")
    options = parser.parse_args(argv)
    arguments = options.arguments[1:] if options.arguments[:1] == ["--"] else options.arguments
    return run_bundle(options.result, arguments, display=options.display)


if __name__ == "__main__":
    raise SystemExit(main())
