"""The local cache of readings: a page is read again only when the ask changed.

A reading is the model's answer, so it is the page's text: the cache stays on the developer's
machine, beside the pages, and never goes into the store.  A campaign reads thirty pages with a
dozen models; without the cache every rerun pays for all of them again.

The key is what decides the answer, :func:`key_of` lists it — the page, the ask, the machine and
the device: seconds belong to one machine on one device, and the cache hands them back.  A changed
tag digest, prompt or threshold therefore misses, and a campaign that only adds a report column
reads nothing again.
"""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_cache_dir

from tools.evaluation.core.formats import FormatError, check_format, write_text_atomic
from tools.evaluation.core.hashing import sha256_json

from .engine import Answer, Ask

FORMAT = 2
"""The version of a cached reading; another version is read again, not guessed at."""

APP_NAME = "teachers-teammate"
"""The app whose cache folder this sits under, spelled the way the app spells it."""

FOLDER = "ocr-eval"
"""This tool's own folder inside it: readings of pages, never the app's cache."""


def cache_root() -> Path:
    """Return where readings are kept: this tool's folder in the user's cache directory."""
    return Path(user_cache_dir(APP_NAME, appauthor=False)) / FOLDER


def key_of(
    ask: Ask,
    *,
    image_sha256: str,
    auto_identity: tuple[str, ...],
    machine: str,
) -> str:
    """Return the key of one reading: everything that decides what the model answers.

    The ask says what the engine was asked; the rest is the page it was asked about and what made
    that page what it is.  The machine is part of it too: a page's seconds are that machine's, so
    the same page read on another one is another measurement.
    """
    return sha256_json(
        {
            "format": FORMAT,
            "image": image_sha256,
            "engine": ask.engine,
            "model": ask.model,
            "digest": ask.digest,
            "prompt": ask.prompt,
            "limits": list(ask.limits),
            "auto_identity": list(auto_identity),
            "detector": list(ask.detector),
            "machine": machine,
            "cpu_only": ask.cpu_only,
        }
    )


class ReadingCache:
    """The readings on disk, one file each, read back by their key."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path_of(self, key: str) -> Path:
        """Return the file one key's reading sits in (a subfolder, so no folder grows unwieldy)."""
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Answer | None:
        """Return the reading of *key*, or ``None`` when it was not read yet."""
        path = self.path_of(key)
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            check_format(record, FORMAT, path)
            return Answer(
                text=str(record["text"]),
                seconds=float(record.get("seconds", 0.0)),
                answer_tokens=int(record.get("answer_tokens", 0)),
                done_reason=str(record.get("done_reason", "")),
                memory_mb=int(record.get("memory_mb", 0)),
            )
        except FormatError, KeyError, ValueError, OSError:
            # A cache is disposable: a file another version wrote, or a half-written one, is a
            # miss, and the page is read again.
            return None

    def put(self, key: str, reading: Answer) -> Path:
        """Write *reading* under *key* and return the file it went to."""
        path = self.path_of(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(
            path,
            json.dumps(
                {
                    "format": FORMAT,
                    "text": reading.text,
                    "seconds": reading.seconds,
                    "answer_tokens": reading.answer_tokens,
                    "done_reason": reading.done_reason,
                    "memory_mb": reading.memory_mb,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
        )
        return path
