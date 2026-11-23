"""Privacy choices that outlive a run: the data flows the user stopped being asked about.

A run that would send content away from this computer asks first.  Ticking "Don't ask again"
remembers the answer for exactly those settings, keyed by the data flow's fingerprint.  This
module is the one place that reads and writes those answers, so the question, the list in
Settings → Privacy and the reset all agree.

An answer is stored as the fingerprint of what was agreed to, nothing else: the list says
what that means ("Proofreading: Online service (Mistral) — names sent as written") by reading
the fingerprint back (:func:`~teachers_teammate.texts.privacy.describe_fingerprint`), so a
changed wording changes every line and no English is kept in the settings file.  A line may carry a tab and further
text; only the part before the tab is the fingerprint.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ..domain.data_location import DataFlow
from ..texts.privacy import describe_fingerprint

SETTINGS_KEY = "privacy/acknowledged_data_flows"
"""One line per remembered answer: the fingerprint of what was agreed to."""

#: Separates the fingerprint from any text after it on a line; the text is ignored.
_SUMMARY_SEPARATOR = "\t"


@dataclass(frozen=True)
class RememberedFlow:
    """One answer the user chose not to be asked about again."""

    fingerprint: str
    summary: str
    """Readable line saying what was agreed to."""


class FlowAcknowledgements:
    """Reads and writes the remembered answers through a ``QSettings``-like object.

    *settings_factory* is called for every access (a ``QSettings`` is cheap to create and
    this keeps it patchable in tests); the object needs ``value``, ``setValue`` and ``remove``.
    """

    def __init__(self, settings_factory: Callable[[], Any]) -> None:
        self._settings_factory = settings_factory

    def remembered(self) -> list[RememberedFlow]:
        """Return every remembered answer, sorted by its summary."""
        flows = [
            RememberedFlow(fingerprint, describe_fingerprint(fingerprint))
            for fingerprint in self._read()
        ]
        return sorted(flows, key=lambda flow: (flow.summary, flow.fingerprint))

    def fingerprints(self) -> set[str]:
        """Return the fingerprints of the remembered answers."""
        return set(self._read())

    def remember(self, flow: DataFlow) -> None:
        """Stop asking about *flow* (its steps that leave the computer, exactly)."""
        self._write({*self._read(), flow.fingerprint})

    def keep_only(self, fingerprints: Iterable[str]) -> None:
        """Forget every remembered answer whose fingerprint is not in *fingerprints*."""
        keep = set(fingerprints)
        self._write({fp for fp in self._read() if fp in keep})

    def forget_all(self) -> None:
        """Forget every remembered answer: the question comes back before the next such run."""
        self._write({})

    # ── storage ───────────────────────────────────────────────────────────────

    def _read(self) -> set[str]:
        raw = str(self._settings_factory().value(SETTINGS_KEY, "") or "")
        fingerprints: set[str] = set()
        for line in raw.split("\n"):
            fingerprint, _sep, _summary = line.partition(_SUMMARY_SEPARATOR)
            if fingerprint:
                fingerprints.add(fingerprint)
        return fingerprints

    def _write(self, fingerprints: Iterable[str]) -> None:
        settings = self._settings_factory()
        lines = sorted(fingerprints)
        if not lines:
            settings.remove(SETTINGS_KEY)
            return
        settings.setValue(SETTINGS_KEY, "\n".join(lines))


# ── The "hide personal information" view setting ──────────────────────────────

DISPLAY_ANONYMIZED_KEY = "privacy/display_anonymized"
"""``"yes"``/``"no"``: show placeholders instead of names in the preview panel."""

DISPLAY_ANONYMIZED_DEFAULT = True
"""Names are hidden until the user turns it off; a stored choice always wins."""


def display_anonymized(settings_factory: Callable[[], Any]) -> bool:
    """Return whether the previews should show placeholders instead of the names.

    Hidden by default: only an answer the user stored (``"no"``) shows the names.  Without the
    Privacy add-on the preview cannot hide anything, so the setting then has no effect (the
    window greys the entry out) and applies as soon as the add-on is installed.
    """
    stored = str(settings_factory().value(DISPLAY_ANONYMIZED_KEY, "") or "").strip().lower()
    if not stored:
        return DISPLAY_ANONYMIZED_DEFAULT
    return stored in {"yes", "true", "1"}


def set_display_anonymized(settings_factory: Callable[[], Any], enabled: bool) -> None:
    """Remember whether the previews should show placeholders instead of the names."""
    settings_factory().setValue(DISPLAY_ANONYMIZED_KEY, "yes" if enabled else "no")
