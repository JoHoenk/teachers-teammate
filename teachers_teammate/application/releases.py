"""The update check as a service: ask the feed, apply the rule, offer the release or nothing."""

from __future__ import annotations

import logging

from ..domain.releases import DEVELOPMENT_VERSION, ReleaseOffer, newer_release
from ..exceptions import ReleaseFeedError
from .ports import ReleaseFeed

_logger = logging.getLogger(__name__)


class ReleaseService:
    """Whether a newer release of this project exists, asked once per start.

    The installed version comes in at build time (``composition``), so a build that stamps its
    ``__version__`` before the services are built is compared as that version.
    """

    def __init__(self, feed: ReleaseFeed, installed_version: str) -> None:
        self._feed = feed
        self._installed_version = installed_version

    def newer_release(self) -> ReleaseOffer | None:
        """Return the release to offer this teacher, or ``None``.

        Best effort: no answer, no network and an unreadable answer all give ``None``, and the
        start goes on.  A failed ask writes one info line, because a log a teacher sends is the
        only place a rate limit (403, 429) would show.  A development build asks nobody: its
        placeholder sorts below every release, so every answer would be an offer.
        """
        if self._installed_version == DEVELOPMENT_VERSION:
            return None
        try:
            tag, url = self._feed.latest()
        except ReleaseFeedError as exc:
            status = f" {exc.status}" if exc.status else ""
            limited = " (rate limited)" if exc.rate_limited else ""
            _logger.info("The update check failed: %s%s%s", type(exc).__name__, status, limited)
            return None
        return newer_release(tag, url, self._installed_version)
