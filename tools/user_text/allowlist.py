"""The literals still allowed at their call site, per file.

Every entry is a ``(path, text)`` pair the ratchet test tolerates.  The dictionary is empty and
stays so: a sentence a teacher reads is defined in ``teachers_teammate/texts``, and the test
fails on a literal at a call site as well as on an entry that matches no finding.  Entries are
keyed by the literal's text rather than its line, so an unrelated edit above it does not turn
the test red; duplicates within one file collapse to a single entry.

Written from the output of ``tools/user_text/inventory.py``.
"""

from __future__ import annotations

#: The literals still allowed at their call site, by module.
ALLOWED: dict[str, frozenset[str]] = {}
