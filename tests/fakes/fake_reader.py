"""A stand-in reader, for the tests that never reach a real engine.

The pipeline asks a reader for a page: its text, and the lines the text stands on.  An engine
that cannot say where a line sits answers with the text alone, through ``process_image``; a
mocked reader has no such default.  This wires the two together, so a test scripts
``process_image`` as before and the page carries the text it gives, with no lines.
"""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

from teachers_teammate.domain.page_layout import PageReading


def mock_reader(
    answer: Callable[..., str] | None = None,
) -> MagicMock:
    """Return a mocked reader whose pages carry the text ``process_image`` answers with.

    *answer* reads the page in its place — a test that needs a different text or failure per
    page passes its own function; without it the mock answers what the test scripts on
    ``process_image``.
    """
    reader = MagicMock()
    read_image = answer or reader.process_image
    reader.read_page.side_effect = lambda path, **kwargs: PageReading(
        text=read_image(path, **kwargs)
    )
    return reader
