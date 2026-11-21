"""The command line's reporter: status to stdout, warnings and errors to stderr.

The seam itself (:class:`~teachers_teammate.interfaces.Reporter`) and the GUI's
:class:`~teachers_teammate.interfaces.CallbackReporter` live in ``interfaces``, because the
pipeline is given them; only the console sink is infrastructure.
"""

from __future__ import annotations

import sys

from ..domain.run_summary import KeepAwakeState as KeepAwakeState  # noqa: PLC0414
from ..interfaces import CallbackReporter as CallbackReporter  # noqa: PLC0414
from ..interfaces import OnFileDoneFn as OnFileDoneFn  # noqa: PLC0414
from ..interfaces import OnFileStartedFn as OnFileStartedFn  # noqa: PLC0414
from ..interfaces import OnFirstTokenFn as OnFirstTokenFn  # noqa: PLC0414
from ..interfaces import OnOcrDoneFn as OnOcrDoneFn  # noqa: PLC0414
from ..interfaces import OnStageDetailFn as OnStageDetailFn  # noqa: PLC0414
from ..interfaces import OnStageStartedFn as OnStageStartedFn  # noqa: PLC0414
from ..interfaces import ProgressCallbacks as ProgressCallbacks  # noqa: PLC0414
from ..interfaces import Reporter as Reporter  # noqa: PLC0414


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
