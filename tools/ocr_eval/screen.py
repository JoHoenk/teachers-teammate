"""The candidate and speed check: drawn pages, no student data.

NOT run in CI — it needs a running Ollama server, and the built-in reader's models for the
reader's row.

    bazel run //tools/ocr_eval:cli -- screen --url http://127.0.0.1:11434

A drawn page is read, not one of the set, so the question *is this a reading model at all* is
answered before a page of the set is spent on it: the tag pulls, the server says the model sees
images, the answer is not empty.  A tag the server does not hold is a row of the table and a line
on stderr; every other row is timed all the same.  What a page costs is measured on the same
pages: :data:`LINES` ruled line counts, the seconds of each and the memory the model holds.  The
reader's row is timed the same way, so whether it is the faster choice on a CPU has its number.

The licence a row shows is what the catalog or the survey claims.  That is not a measurement: the
developer reads the card, and a claim that turns out wrong is corrected in ``candidates.py`` or by
the catalog's verifier, never here.

Numbers only: no page of the set is read, and no page's text is printed or kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import platform
import tempfile
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

from teachers_teammate.domain.system_facts import ServerFacts

from . import candidates
from .engine import Engine

LINES = (6, 12, 25)
"""The drawn pages: a few lines, a page, a dense page."""

PAGE_SIZE = (1240, 1754)
"""A drawn page's pixels: A4 at 150 dpi."""

MARGIN = 130
"""How much white the text leaves on every side of a drawn page."""

FONT_SIZE = 36
"""The drawn text's size: what a scanner of a school page delivers at 150 dpi."""

GAP = 1.6
"""A ruled line's distance below the text, in font sizes."""

WORDS = (
    "Der",
    "Schüler",
    "schreibt",
    "einen",
    "Satz",
    "auf",
    "das",
    "Blatt",
    "und",
    "die",
    "Lehrerin",
    "liest",
    "ihn",
)
"""The words a drawn page is made of: ordinary German, nothing from any page of the set."""

INK = (25, 25, 25)
RULE = (205, 205, 205)


@dataclass(frozen=True)
class Presence:
    """Whether the server has a candidate at all, and whether it reads a page."""

    tag: str
    on_server: bool
    """The server knows the tag: its weights are there to pull."""
    vision: bool | None
    """Whether the server lists vision among the model's capabilities; ``None``: it did not say."""
    licence: str
    """What the catalog or the survey claims the weights come under; ``""`` when neither knows."""


@dataclass(frozen=True)
class Timed:
    """One engine on the drawn pages: what it answered and what each page took."""

    name: str
    """The tag, or ``reader`` for the built-in reader."""
    prompt: str
    """Which prompt the engine was asked with: the app's, the card's, or ``""`` for the reader."""
    answered: int
    """How many drawn pages came back with a non-empty answer."""
    pages: int
    seconds: tuple[float, ...]
    """The seconds each drawn page took, in the order the pages were read."""
    memory_mb: int
    """The most memory the engine held while reading them, in whole MiB."""


def drawn_page(lines: int) -> Image.Image:
    """Return a white page with *lines* ruled lines of drawn text, waiting to be read."""
    image = Image.new("RGB", PAGE_SIZE, color=(255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=FONT_SIZE)
    width = PAGE_SIZE[0] - 2 * MARGIN
    room = (PAGE_SIZE[1] - 2 * MARGIN) / max(lines, 1)
    step = min(room, FONT_SIZE * GAP)
    for row in range(lines):
        top = MARGIN + row * step
        draw.text((MARGIN, top), _sentence(row, font, width), fill=INK, font=font)
        ruled = top + FONT_SIZE * 1.3
        draw.line((MARGIN, ruled, MARGIN + width, ruled), fill=RULE, width=2)
    return image


def _sentence(row: int, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, width: float) -> str:
    """Return the words of drawn line *row*, as many as fit across *width*."""
    words: list[str] = []
    for offset in range(len(WORDS)):
        word = WORDS[(row + offset) % len(WORDS)]
        candidate = " ".join([*words, word])
        if words and font.getlength(candidate) > width:
            break
        words.append(word)
    return " ".join(words)


def drawn_pages(lines: Sequence[int], folder: Path) -> tuple[Path, ...]:
    """Return the paths the drawn pages are kept at in *folder*, one per line count."""
    paths: list[Path] = []
    for count in lines:
        path = folder / f"drawn_{count:03d}.png"
        drawn_page(count).save(path)
        paths.append(path)
    return tuple(paths)


class Facts(Protocol):
    """What the check asks the server: the digests it holds, and what one model can do."""

    def server_facts(self) -> ServerFacts:
        """Return the server's version, its model digests and what it holds in memory."""

    def model_capabilities(self, tag: str) -> frozenset[str] | None:
        """Return what *tag* can do, or ``None`` when the server did not say."""


def presences(client: Facts, tags: Sequence[str]) -> tuple[Presence, ...]:
    """Return, for every tag, whether the server has it and whether it reads a page.

    The server's facts are asked once for all the tags: they list every digest the server holds.
    """
    facts = client.server_facts()
    return tuple(_presence(client, facts, tag) for tag in tags)


def _presence(client: Facts, facts: ServerFacts, tag: str) -> Presence:
    """Return what the server says about *tag*, asked with *facts* already in hand."""
    on_server = bool(facts.digest_of(tag))
    capabilities = client.model_capabilities(tag) if on_server else None
    return Presence(
        tag=tag,
        on_server=on_server,
        vision=None if capabilities is None else "vision" in capabilities,
        licence=candidates.licence(tag),
    )


def timed(engine: Engine, images: Sequence[Path], *, prompt: str, language: str) -> Timed:
    """Return what *engine* answered on the drawn pages *images*, and what each page cost.

    The engine is warmed up first, so no page's time carries the loading of the model.
    """
    engine.warm_up()
    seconds: list[float] = []
    answered = 0
    memory = 0
    for image in images:
        answer = engine.read(image, language)
        seconds.append(answer.seconds)
        answered += 1 if answer.text.strip() else 0
        memory = max(memory, answer.memory_mb)
    return Timed(
        name=engine.model,
        prompt=prompt,
        answered=answered,
        pages=len(images),
        seconds=tuple(seconds),
        memory_mb=memory,
    )


def presence_table(rows: Sequence[Presence]) -> str:
    """Return what the server said about every candidate, as one markdown table."""
    lines = [
        "| model | on the server | sees images | licence (claimed) |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.tag} | {_said(row.on_server)} | {_vision(row.vision)} | {row.licence or '—'} |"
        )
    return "\n".join(lines)


def speed_table(rows: Sequence[Timed], lines: Sequence[int], *, machine: str) -> str:
    """Return the seconds and memory of every engine as one markdown table, machine named."""
    header = ["engine", "prompt", "read", *(f"{count} lines" for count in lines), "s/page", "MiB"]
    body = [
        [
            row.name,
            row.prompt or "—",
            f"{row.answered}/{row.pages}",
            *(f"{seconds:.1f}" for seconds in row.seconds),
            f"{_average(row.seconds):.1f}",
            f"{row.memory_mb}",
        ]
        for row in rows
    ]
    return "\n".join(
        [
            f"Seconds one drawn page took on {machine}.",
            "",
            _out(header),
            _out(["-"] * len(header)),
            *(_out(cells) for cells in body),
        ]
    )


def machine_name() -> str:
    """Return this machine, as the tables name it: its host name and its processor."""
    processor = platform.processor() or platform.machine() or "unknown processor"
    return f"{platform.node() or 'this machine'} ({processor})"


def drawn_folder(keep: Path | None) -> tuple[tempfile.TemporaryDirectory | None, Path]:
    """Return the folder the drawn pages go to, and the temporary folder to keep alive for it.

    A run that keeps no drawn page gets a temporary folder, which the caller holds open while it
    reads: a model reads the file, so it must not be taken away first.
    """
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        return None, keep
    temporary = tempfile.TemporaryDirectory(prefix="ocr-eval-screen-")
    return temporary, Path(temporary.name)


def _average(seconds: Sequence[float]) -> float:
    """Return the mean of *seconds*, or 0.0 when there is none."""
    return sum(seconds) / len(seconds) if seconds else 0.0


def _said(value: bool) -> str:
    """Return *value* as a table cell that a missing answer stands out in."""
    return "yes" if value else "**no**"


def _vision(value: bool | None) -> str:
    """Return whether a model reads images, ``?`` when the server did not say."""
    return "?" if value is None else _said(value)


def _out(cells: Sequence[str]) -> str:
    """Return one markdown table row."""
    return "| " + " | ".join(cells) + " |"
