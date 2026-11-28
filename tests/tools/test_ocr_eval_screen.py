"""The candidate and speed check: drawn pages, what the server says, what a page costs."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.domain.system_facts import ServerFacts
from tools.ocr_eval import candidates, cli, screen
from tools.ocr_eval.engine import Answer, Ask


class FakeFacts:
    """A server that holds one model, reads images, and has never heard of another."""

    def __init__(self, held: dict[str, frozenset[str] | None]) -> None:
        self._held = held

    def server_facts(self) -> ServerFacts:
        return ServerFacts(
            version="0.12.0",
            digests={tag: f"sha256:{index}" for index, tag in enumerate(self._held)},
        )

    def model_capabilities(self, tag: str) -> frozenset[str] | None:
        return self._held.get(tag)


class FakeEngine:
    """An engine that answers every page with text, in a time of its own choosing."""

    def __init__(self, *, answers: tuple[str, ...] = ("text", "text", "text"), memory_mb: int = 7):
        self._answers = list(answers)
        self._memory_mb = memory_mb
        self.model = "fake:tag"
        self.engine = "ollama"
        self.warmed = False

    def warm_up(self) -> None:
        self.warmed = True

    def ask(self, language: str) -> Ask:
        return Ask(
            engine=self.engine,
            model=self.model,
            digest="sha256:fake",
            prompt=f"Read the page in {language}.",
        )

    def read(self, image: Path, language: str) -> Answer:  # the page is only counted
        return Answer(text=self._answers.pop(0), seconds=1.5, memory_mb=self._memory_mb)


def _ink(image) -> int:
    """Return how many pixels of *image* are not white."""
    return sum(image.convert("L").histogram()[:255])


def test_a_drawn_page_holds_the_lines_it_was_asked_for():
    """Given two line counts, when a page is drawn for each, then the one with more lines has
    more ink on it and both are the size of a page."""
    sparse = screen.drawn_page(6)
    dense = screen.drawn_page(25)

    assert sparse.size == screen.PAGE_SIZE
    assert dense.size == screen.PAGE_SIZE
    assert _ink(sparse) > 0
    assert _ink(dense) > _ink(sparse)


def test_the_drawn_pages_are_written_one_file_per_line_count(tmp_path: Path):
    """Given line counts, when the drawn pages are written, then one file per count names it."""
    paths = screen.drawn_pages((25, 6), tmp_path)

    assert [path.name for path in paths] == ["drawn_025.png", "drawn_006.png"]
    assert all(path.is_file() for path in paths)


def test_a_model_the_server_does_not_hold_is_marked_and_one_that_sees_images_is_said_so():
    """Given a server that holds one reading model and one blind model, when the candidates are
    checked, then only the held ones are reported as there, and vision is read from the server."""
    client = FakeFacts({"held:ocr": frozenset({"completion", "vision"}), "held:blind": frozenset()})

    found = screen.presences(client, ["held:ocr", "held:blind", "absent:ocr"])

    assert [row.on_server for row in found] == [True, True, False]
    assert [row.vision for row in found] == [True, False, None]


def test_the_claimed_licence_of_a_catalogued_model_comes_from_the_catalog():
    """Given a catalogued reading model, when it is checked, then its row carries the licence the
    catalog states, and a model nobody knows carries none."""
    tag = candidates.catalogued()[0].tag
    client = FakeFacts({tag: frozenset({"vision"})})

    found = {row.tag: row for row in screen.presences(client, [tag, "nobody/knows-this"])}

    assert found[tag].licence
    assert found["nobody/knows-this"].licence == ""


def test_the_presence_table_says_in_words_what_a_missing_model_is():
    """Given a held and a missing model, when the table is drawn, then the missing one stands out
    and a licence nobody knows is a dash."""
    rows = (
        screen.Presence(tag="glm-ocr:q8_0", on_server=True, vision=True, licence="MIT"),
        screen.Presence(tag="gone:tag", on_server=False, vision=None, licence=""),
    )

    text = screen.presence_table(rows)

    assert "| glm-ocr:q8_0 | yes | yes | MIT |" in text
    assert "| gone:tag | **no** | ? | — |" in text


def test_every_drawn_page_is_timed_and_an_empty_answer_counts_as_unread(tmp_path: Path):
    """Given an engine that answers one of three pages with nothing, when it is timed, then three
    times are kept, the pages are counted, and the empty one is not counted as read."""
    engine = FakeEngine(answers=("text", "", "text"), memory_mb=11)

    timed = screen.timed(
        engine,
        (tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"),
        prompt="app",
        language="German",
    )

    assert engine.warmed
    assert timed.answered == 2
    assert timed.pages == 3
    assert timed.seconds == (1.5, 1.5, 1.5)
    assert timed.memory_mb == 11


def test_the_speed_table_names_the_machine_and_averages_the_pages():
    """Given one engine on three drawn pages, when the table is drawn, then the machine is named
    and the row carries each page's seconds, their average, the memory and the pages it read."""
    rows = (
        screen.Timed(
            name="reader", prompt="", answered=3, pages=3, seconds=(0.5, 1.0, 1.5), memory_mb=900
        ),
    )

    text = screen.speed_table(rows, screen.LINES, machine="rx9070xt-server (x86_64)")

    assert text.splitlines()[0] == "Seconds one drawn page took on rx9070xt-server (x86_64)."
    assert "| engine | prompt | read | 6 lines | 12 lines | 25 lines | s/page | MiB |" in text
    assert "| reader | — | 3/3 | 0.5 | 1.0 | 1.5 | 1.0 | 900 |" in text


def test_the_machine_is_named_by_its_host_and_its_processor():
    """Given this machine, when it is named, then the host and the processor are both in the name."""
    name = screen.machine_name()

    assert name.endswith(")")
    assert "(" in name


@pytest.mark.parametrize(("wanted", "expected"), [("app", ["app"]), ("card", ["card"])])
def test_a_model_whose_card_names_a_prompt_is_timed_with_the_prompt_asked_for(
    wanted: str, expected: list[str]
):
    """Given a model whose card names a prompt, when one prompt is asked for, then only that one
    is used."""
    tag = "glm-ocr:q8_0"

    assert cli._prompts(tag, wanted) == expected


def test_a_model_whose_card_names_no_prompt_is_only_ever_asked_the_apps():
    """Given a model no card prompt is transcribed for, when both prompts are asked for, then it is
    timed with the app's prompt alone."""
    assert cli._prompts("qwen2.5vl:7b", "both") == ["app"]
    assert cli._prompts("qwen2.5vl:7b", "card") == ["app"]
