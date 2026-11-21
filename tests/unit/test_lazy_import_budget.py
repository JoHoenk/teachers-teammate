"""The application layer's lazy imports only go down.

``application/`` imports ``infrastructure`` at the top of a module wherever it can: a module-level
import costs nothing extra, and it keeps a service's collaborators visible.  What stays inside a
function is one of two things — a package that is an add-on and must not be imported at start-up
(spaCy, a DOCX writer, an LLM provider's package) or a collaborator the module cannot name yet.
Either way it costs a ``# noqa: PLC0415``.

This test is the ratchet on that count: it is lowered when a suppression goes, never raised
without a reason that is written down here.  Suppressing a new import inside a function means
editing the number, which is the moment to ask whether a port in ``application/ports.py`` would
do instead.
"""

from __future__ import annotations

import os
from pathlib import Path

BUDGET = 67
"""How many ``# noqa: PLC0415`` suppressions ``teachers_teammate/application/`` may carry."""

PACKAGE = "teachers_teammate"
LAYER = "application"
MARKER = "# noqa: PLC0415"


def _layer_dir() -> Path:
    """Return the application layer's folder, from the Bazel runfiles or the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / PACKAGE / LAYER
    return Path(__file__).resolve().parents[2] / PACKAGE / LAYER


def _suppressions() -> dict[str, int]:
    """Return ``{module: count}`` of the lazy-import suppressions of the layer, biggest first."""
    found: dict[str, int] = {}
    for path in sorted(_layer_dir().glob("*.py")):
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if MARKER in line)
        if count:
            found[path.name] = count
    return dict(sorted(found.items(), key=lambda item: (-item[1], item[0])))


def test_the_lazy_imports_of_the_application_layer_are_below_the_budget() -> None:
    """
    Given  the modules of ``teachers_teammate/application/``
    When   their ``# noqa: PLC0415`` suppressions are counted
    Then   the count equals the budget — a removal lowers the budget, a new one needs a reason
    """
    found = _suppressions()
    total = sum(found.values())
    breakdown = "\n".join(f"  {count:3d}  {module}" for module, count in found.items())

    assert total == BUDGET, (
        f"application/ carries {total} lazy imports, the budget is {BUDGET}.\n"
        f"More: give the service the collaborator through its constructor (a Protocol in\n"
        f"application/ports.py) instead of importing it inside the method; less: lower BUDGET.\n"
        f"{breakdown}"
    )
