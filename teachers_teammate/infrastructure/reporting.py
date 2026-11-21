"""The command line's reporter: status to stdout, warnings and errors to stderr.

The seam itself (:class:`~teachers_teammate.interfaces.Reporter`) and the GUI's
:class:`~teachers_teammate.interfaces.CallbackReporter` live in ``interfaces``, because the
pipeline is given them; only the console sink is infrastructure.
"""

from __future__ import annotations

import sys


class StdoutReporter:
    """Default reporter: ``status`` → stdout, ``warn``/``error`` → stderr.

    ``error`` adds the ``ERROR:`` prefix the callers used to write themselves.
    """

    def status(self, line: str) -> None:
        print(line, flush=True)

    def warn(self, line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    def error(self, line: str) -> None:
        print(f"ERROR: {line}", file=sys.stderr, flush=True)
