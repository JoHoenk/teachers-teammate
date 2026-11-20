"""The Check tab: one document at a time, full width.

See :mod:`._page` for the page itself — the header that says which document is open and
moves between documents, and the preview panel it holds.
"""

from ._page import CheckPage, CopyKind, CopyState
from ._run_strip import RunState

__all__ = ["CheckPage", "CopyKind", "CopyState", "RunState"]
