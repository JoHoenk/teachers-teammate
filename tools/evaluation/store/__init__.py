"""The store: where the files live, how they are read and written, and how they are checked.

The store is the layer between the formats (:mod:`..core`) and the reports (:mod:`..report`).  It
owns four things and nothing else:

* **the layout** (:mod:`.layout`) — one directory with ``testsets/``, ``runs/`` and ``marks/``, and
  the names of the files in them;
* **reading and writing** (:mod:`.reader`, :mod:`.writer`) — thin, because the formats and their
  identities live in :mod:`..core.records`; a run file is append-only, everything else is written
  whole with sorted keys;
* **validation** (:mod:`.validate`) — the checks a file must pass before a report reads it, and the
  lists of what moved (a label, a mark) rather than a silent repair;
* **the marks** (:mod:`.marks`, :mod:`.accept`) — one identity for everything a person marks, the
  rule that turns a marker's ``valid`` verdicts into accepted alternatives, and the one command that
  writes them into the key;
* **the one-off converter** (:mod:`.convert_v1`) — the old ``results-*`` folders and ``set-2026-12``
  read as they are and written in the new formats, importing nothing from the old package.

A store never scores.  Scoring is :mod:`..report`'s work, and keeping it out is what lets a report be
rebuilt from the files alone.
"""

from __future__ import annotations

__all__: list[str] = []
