"""The Setup Assistant: the questions a first start needs, and the setup plan they lead to.

The main window imports this package lazily, so nothing here is loaded until the assistant is
opened.  ``SetupContext`` is what the main window hands in, ``SetupResult`` what Set up hands
back; the wizard itself is :class:`SetupAssistant`.
"""

from __future__ import annotations

from ._assistant import SetupAssistant
from ._context import SetupContext, SetupResult

__all__ = ["SetupAssistant", "SetupContext", "SetupResult"]
