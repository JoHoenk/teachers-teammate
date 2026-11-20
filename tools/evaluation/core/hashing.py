"""The hashes of the evaluation tooling: one function, one rule, so two layers cannot disagree.

Every hash in a stored file is the SHA-256 of UTF-8 text, and a hash of a structure is the SHA-256
of its JSON with sorted keys and unescaped non-ASCII.  That is the whole rule; it lives here so a
test set, a run file and a score cache cannot each invent their own.

The identities themselves (which fields a hash covers) are in :mod:`.records`, because they are a
property of the format and not of the hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_text(text: str) -> str:
    """Return the SHA-256 of *text* as UTF-8, which is how every hash in a stored file is made."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Return the SHA-256 of the bytes of *path*, which is how a copied file is pinned."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_json(value: Any) -> str:
    """Return the SHA-256 of *value* as sorted-key JSON, so two equal structures hash alike.

    ``ensure_ascii=False`` keeps a German umlaut one character, so the hash of a text does not
    depend on how a writer happened to escape it.
    """
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False))


__all__ = ["file_sha256", "sha256_json", "sha256_text"]
