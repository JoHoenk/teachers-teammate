"""The newest release of this project, as GitHub's API names it.

The one place that speaks HTTPS for the update check.  A build that is not started by a teacher
(the CLI) asks nothing, and nothing here runs at start-up on its own: the service calls it, and
the switch on the Privacy page decides whether the service is called at all.
"""

from __future__ import annotations

from http.client import HTTPException
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ..domain.releases import RELEASES_API
from ..exceptions import ReleaseFeedError

#: How long the answer may take.  The check runs off the GUI thread and nobody waits on it.
_TIMEOUT_SECONDS = 5


class GitHubReleaseFeed:
    """The newest published release: its tag and its page."""

    def latest(self) -> tuple[str, str]:
        """Return the ``(tag, url)`` of the newest release; raise :exc:`ReleaseFeedError` else."""
        request = Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        try:
            with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                data = json.loads(response.read())
        except HTTPError as exc:
            raise ReleaseFeedError(exc.code, rate_limited=_rate_limited(exc)) from exc
        except (OSError, HTTPException, ValueError) as exc:
            # No answer (offline, a timeout, a cut connection) or one that is not JSON: the same
            # nothing to offer.
            raise ReleaseFeedError() from exc
        if not isinstance(data, dict):
            raise ReleaseFeedError()
        return str(data.get("tag_name", "")), str(data.get("html_url", ""))


def _rate_limited(error: HTTPError) -> bool:
    """Whether *error* is GitHub's hourly limit rather than a refusal that would work later."""
    remaining = (error.headers or {}).get("x-ratelimit-remaining", "")
    return error.code in (403, 429) and str(remaining).strip() == "0"
