"""The cache of readings: one key per ask, and a file that is a miss when it does not fit."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from tools.ocr_eval import readings
from tools.ocr_eval.engine import Answer, Ask

ASK = Ask(
    engine="ollama",
    model="qwen3-vl:8b",
    digest="abc123",
    prompt="Read the page.",
    limits=(("num_predict", 2048.0), ("temperature", 0.0)),
)
"""One model asked one page: what a key is made of, beside the page itself."""

IMAGE = "a" * 64
IDENTITY = ("v1", "gray", "+turn")
MACHINE = "rx9070xt-server"


def _key(
    ask: Ask = ASK,
    *,
    image_sha256: str = IMAGE,
    auto_identity: tuple[str, ...] = IDENTITY,
    machine: str = MACHINE,
) -> str:
    """Return the key of one reading, with one part of it replaced."""
    return readings.key_of(
        ask, image_sha256=image_sha256, auto_identity=auto_identity, machine=machine
    )


def test_a_key_follows_the_ask() -> None:
    """Given one ask and the same ask with one thing changed
    When   their keys are made
    Then   every change makes another key, so a reading is never reused for another ask
    """
    base = _key()
    changes = [
        replace(ASK, engine="ppocr"),
        replace(ASK, model="gemma4:e4b"),
        replace(ASK, digest="def456"),
        replace(ASK, prompt="Text Recognition:"),
        replace(ASK, limits=(("num_predict", 1024.0),)),
        replace(ASK, detector=(("max_side", 960.0),)),
        replace(ASK, cpu_only=True),
    ]

    for ask in changes:
        assert _key(ask) != base


def test_a_key_follows_the_page_and_the_machine() -> None:
    """Given one reading, and the same one of another page, another set, another machine
    When   their keys are made
    Then   every one of them is another key, because another page was asked or it was another
           machine's seconds
    """
    base = _key()

    assert _key(image_sha256="b" * 64) != base
    assert _key(auto_identity=("v2", "gray")) != base
    assert _key(machine="laptop") != base


def test_the_same_ask_makes_the_same_key() -> None:
    """Given one ask asked twice
    When   both keys are made
    Then   they are the same, so a rerun reads the page no second time
    """
    assert _key() == _key(replace(ASK))


def test_a_reading_comes_back_as_it_was_put(tmp_path: Path) -> None:
    """Given a reading in the cache
    When   it is read back
    Then   the text and what reading it cost are returned, the memory it held included
    """
    cache = readings.ReadingCache(tmp_path)
    key = _key()
    put = Answer("Hallo Welt", seconds=1.5, answer_tokens=9, done_reason="stop", memory_mb=6144)

    cache.put(key, put)

    assert cache.get(key) == put


def test_a_key_nobody_read_is_a_miss(tmp_path: Path) -> None:
    """Given a cache without the page
    When   it is asked for that page
    Then   it says it does not have it
    """
    assert readings.ReadingCache(tmp_path).get(_key()) is None


def test_a_file_of_another_format_is_read_again(tmp_path: Path) -> None:
    """Given a cached file of a format this version does not know
    When   it is asked for
    Then   it is a miss, and the page is read again
    """
    cache = readings.ReadingCache(tmp_path)
    key = _key()
    path = cache.path_of(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": 99, "text": "Hallo"}), encoding="utf-8")

    assert cache.get(key) is None


def test_readings_sit_in_this_tool_s_own_folder() -> None:
    """Given where the readings are kept
    When   the folder is asked for
    Then   it is this tool's folder in the user's cache directory, not the app's
    """
    assert readings.cache_root().name == readings.FOLDER
