#!/usr/bin/env python3
"""Fetch the model files the bundle ships: the built-in reader and the page models.

The installed app carries them (``teachers_teammate/_models``), so the default setup works
without a download: the built-in reader reads, and *Automatic* image preparation has its two
models.  Everything else — spaCy, the Ollama models — stays a download the Setup Assistant
offers, because those are large and only some teachers switch them on.

The files are the ones pinned in ``teachers_teammate/domain/local_models.py``: same revision,
same size, same SHA-256.  They are fetched through the app's own ``ModelStore``, so the check
that refuses a file which changed upstream is the one the app runs.  A file that is already
there and correct is not fetched again, which is what makes a CI cache of the folder work.

Usage (from the repo root, inside the build environment, before PyInstaller runs):

    python tools/build/fetch_models.py

Output: ``teachers_teammate/_models/`` (git-ignored), which the spec adds to the bundle.
``build_standalone.py`` calls :func:`fetch` itself.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from teachers_teammate.domain.local_models import LocalModel

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "teachers_teammate" / "_models"

# The build environment installs the project (`pip install -e .`), but a developer running this
# from a plain checkout has not: the repo root makes the app importable either way.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: How much may be downloaded between two progress lines.
_PROGRESS_STEP = 10_000_000


def bundled_models() -> tuple[LocalModel, ...]:
    """Return the model sets the app carries itself (the reader, then the page models)."""
    from teachers_teammate.domain.local_models import (  # noqa: PLC0415
        PAGE_MODELS,
        READER_MODELS,
    )

    return (*READER_MODELS, *PAGE_MODELS)


def fetch(output: Path = OUTPUT) -> int:
    """Download what is missing of the bundled models into *output*; return how many there are."""
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    models = bundled_models()
    store = ModelStore(output)
    missing = store.missing(models)
    if not missing:
        return len(models)

    printed = 0

    def report(done: int, total: int) -> None:
        nonlocal printed
        if done - printed >= _PROGRESS_STEP or done == total:
            printed = done
            print(f"    {done / 1e6:.0f} of {total / 1e6:.0f} MB", flush=True)

    print(f"==> Fetching {len(missing)} of {len(models)} model sets…")
    store.download(models, on_progress=report)
    return len(models)


def incomplete(output: Path = OUTPUT) -> list[str]:
    """Return the keys of the bundled models *output* does not hold completely.

    A folder that exists but is empty, left by a cancelled fetch or by an older revision pin,
    counts as missing: it would be bundled and then read as "not there" by the app.
    """
    from teachers_teammate.infrastructure.onnx.store import ModelStore  # noqa: PLC0415

    store = ModelStore(output, bundled_root=output)
    return [model.key for model in store.bundled_missing(bundled_models())]


def main() -> int:
    """Fetch the bundled models and say what the folder holds."""
    count = fetch()
    print(f"    {count} model sets in {OUTPUT.relative_to(ROOT)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
