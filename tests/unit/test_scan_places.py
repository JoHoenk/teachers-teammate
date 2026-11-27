"""Where a scan's lines stand in the text read from it: the alignment and the reading alongside."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

from teachers_teammate.domain.page_layout import PageReading, ReadLine
from teachers_teammate.exceptions import RunStoppedError
from teachers_teammate.infrastructure.text_alignment import opcodes, places
from teachers_teammate.infrastructure.workflow.scan_places import ScanPlaces

_BOX = ((0.1, 0.1), (0.9, 0.1), (0.9, 0.2), (0.1, 0.2))


def _page(*lines: str) -> PageReading:
    """Return one page read by the built-in reader, one box per line."""
    read = tuple(ReadLine(text=line, confidence=0.9, boxes=(_BOX,)) for line in lines)
    return PageReading(text="\n".join(lines), lines=read)


def test_each_line_keeps_the_page_it_was_read_from() -> None:
    """
    Given  two pages read by the built-in reader, and the reading the run stored with page labels
    When   the lines are placed
    Then   each line keeps the index of its own page and its own words
    """
    pages = [_page("Die Katze sitzt."), _page("Der Hund bellt.", "Die Maus läuft.")]
    reading = "Page 1:\nDie Katze sitzt.\nPage 2:\nDer Hund bellt.\nDie Maus läuft."

    placed = places(pages, reading, "plain")

    assert [box.page for box in placed] == [0, 1, 1]
    assert [reading[box.start : box.end] for box in placed] == [
        "Die Katze sitzt.",
        "Der Hund bellt.",
        "Die Maus läuft.",
    ]


def test_a_letter_that_lower_cases_to_two_moves_no_place() -> None:
    """
    Given  a reading holding "İ", which ``str.lower`` makes two characters of
    When   the lines are placed
    Then   every line still stands on its own words
    """
    pages = [_page("İlayda kam heim.", "Der Hund bellt laut.")]
    reading = "İlayda kam heim.\nDer Hund bellt laut."

    placed = places(pages, reading, "plain")

    assert [reading[box.start : box.end] for box in placed] == [
        "İlayda kam heim.",
        "Der Hund bellt laut.",
    ]


def test_the_alignment_ignores_case() -> None:
    """
    Given  two texts that differ only in case
    When   they are aligned
    Then   they match all along
    """
    assert [tag for tag, *_ in opcodes("Die KATZE", "die katze")] == ["equal"]


def test_the_built_in_reader_reads_alongside_until_the_places_are_asked_for(tmp_path: Path) -> None:
    """
    Given  a run whose reader gives no lines, and the built-in reader beside it
    When   the pages are begun and the places asked for
    Then   the built-in reader read every page, and its lines are placed in the stored text
    """
    reader = MagicMock()
    reader.read_page.side_effect = [_page("Die Katze sitzt."), _page("Der Hund bellt.")]
    pages = [tmp_path / "1.png", tmp_path / "2.png"]
    pending = ScanPlaces(reader).begin(pages, "German")

    try:
        placed = pending.places(
            [PageReading("Die Katze sitzt."), PageReading("Der Hund bellt.")],
            "Page 1:\nDie Katze sitzt.\nPage 2:\nDer Hund bellt.",
            "plain",
            name="doc.pdf",
        )
    finally:
        pending.close()

    assert [box.page for box in placed] == [0, 1]
    assert [c.args[0] for c in reader.read_page.call_args_list] == pages


def test_a_reading_with_lines_needs_no_reader_alongside() -> None:
    """
    Given  the built-in reader as the run's reader, with its lines
    When   the places are asked for
    Then   they come from those lines, and no thread is started
    """
    pending = ScanPlaces(None).begin([Path("p.png")], "German")

    placed = pending.places([_page("Die Katze sitzt.")], "Die Katze sitzt.", "plain", name="d")
    pending.close()

    assert [(box.start, box.end) for box in placed] == [(0, len("Die Katze sitzt."))]


def test_closing_lets_the_reading_alongside_go_after_its_page(tmp_path: Path) -> None:
    """
    Given  a reading alongside that is in the middle of the first of three pages
    When   the document is closed before its places are asked for
    Then   close() returns once that page is read, and no further page is read
    """
    bound = threading.Event()
    holder: list = []

    def _slow(*_args: object, **_kwargs: object) -> PageReading:
        bound.wait(5)
        holder[0]._abandoned.wait(5)  # the page ends only once the document was closed
        return _page("Die Katze sitzt.")

    reader = MagicMock()
    reader.read_page.side_effect = _slow
    pending = ScanPlaces(reader).begin([tmp_path / f"{n}.png" for n in range(3)], "German")
    holder.append(pending)
    bound.set()

    pending.close()

    assert reader.read_page.call_count == 1


def test_a_stop_while_reading_alongside_gives_no_places(tmp_path: Path) -> None:
    """
    Given  a reading alongside that "Stop now" ends on its first page
    When   the places are asked for
    Then   there are none, and nothing is raised: the stop is the run's to report
    """
    stop = threading.Event()

    def _stopped(*_args: object, **_kwargs: object) -> PageReading:
        stop.set()
        raise RunStoppedError

    reader = MagicMock()
    reader.read_page.side_effect = _stopped
    pending = ScanPlaces(reader, stop).begin([tmp_path / "1.png"], "German")

    placed = pending.places(
        [PageReading("Die Katze sitzt.")], "Die Katze sitzt.", "plain", name="d"
    )
    pending.close()

    assert placed == ()


def test_waiting_for_the_reading_alongside_is_said(tmp_path: Path) -> None:
    """
    Given  a reading alongside that is still on its page when the places are asked for
    When   the places are asked for
    Then   the wait is said once before it begins, and the places come once the page is read
    """
    said = threading.Event()

    def _slow(*_args: object, **_kwargs: object) -> PageReading:
        said.wait(5)
        return _page("Die Katze sitzt.")

    reader = MagicMock()
    reader.read_page.side_effect = _slow
    pending = ScanPlaces(reader).begin([tmp_path / "1.png"], "German")

    try:
        placed = pending.places(
            [PageReading("Die Katze sitzt.")],
            "Die Katze sitzt.",
            "plain",
            name="d",
            on_wait=said.set,
        )
    finally:
        pending.close()

    assert said.is_set()
    assert len(placed) == 1


def test_a_reading_alongside_that_is_done_says_no_wait(tmp_path: Path) -> None:
    """
    Given  a reading alongside that read every page before the places are asked for
    When   the places are asked for
    Then   no wait is said
    """
    reader = MagicMock()
    reader.read_page.return_value = _page("Die Katze sitzt.")
    on_wait = MagicMock()
    pending = ScanPlaces(reader).begin([tmp_path / "1.png"], "German")
    pending._thread.join(5)

    pending.places(
        [PageReading("Die Katze sitzt.")], "Die Katze sitzt.", "plain", name="d", on_wait=on_wait
    )
    pending.close()

    on_wait.assert_not_called()
