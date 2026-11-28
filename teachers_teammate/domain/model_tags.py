"""Model tag spelling: normalise a tag, look it up in a table, shorten it for a narrow line."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def normalize_tag(tag: str) -> str:
    """Return *tag* in normalised form: stripped, with ``:latest`` appended when needed.

    ``"deepseek-ocr"`` → ``"deepseek-ocr:latest"``; tags that already carry a
    ``:tag`` suffix are returned unchanged (apart from surrounding whitespace).
    """
    cleaned = (tag or "").strip()
    if not cleaned:
        return cleaned
    if ":" in cleaned:
        return cleaned
    return f"{cleaned}:latest"


def digest_in(digests: Iterable[tuple[str, str]], tag: str) -> str:
    """Return the digest listed for *tag* among ``(tag, digest)`` pairs, or ``""``.

    Compared over :func:`normalize_tag`, so ``"gemma4"`` and ``"gemma4:latest"`` are one model.
    The digest pins the weights: a tag is a name that can be re-pointed, so it is what tells a
    measurement of an earlier pull from one of this.
    """
    wanted = normalize_tag(tag)
    for name, digest in digests:
        if normalize_tag(name) == wanted:
            return digest
    return ""


def short_model_name(tag: str) -> str:
    """Return *tag* as a short name for a narrow line.

    ``hf.co/<org>/<repo>:<quant>`` → ``<repo>`` without a ``-GGUF`` suffix;
    ``name:latest`` → ``name``; any other ``name:tag`` stays as it is.  The full tag belongs
    in the tooltip.
    """
    cleaned = (tag or "").strip()
    if not cleaned:
        return ""
    name, _, quant = cleaned.rpartition(":")
    if not name:  # no colon at all
        name, quant = cleaned, ""
    if name.startswith("hf.co/"):
        repo = name.rsplit("/", 1)[-1]
        return repo[: -len("-GGUF")] if repo.endswith("-GGUF") else repo
    if quant in ("", "latest"):
        return name
    return cleaned


def tag_value[T](table: Mapping[str, T], tag: str) -> T | None:
    """Return the value *tag* has in *table*, or ``None`` when it has none.

    A tag written with ``:latest`` names the same model, so both spellings are looked up.
    """
    normalized = normalize_tag(tag)
    for candidate in (normalized, normalized.removesuffix(":latest")):
        if candidate in table:
            return table[candidate]
    return None


def measured_value[T](table: Mapping[str, T], tag: str) -> T | None:
    """Return the value of *tag* in *table*, or of the model it is a variant of.

    ``qwen3.5:4b-q8_0`` is the same model in another quantisation, so it has the value of
    ``qwen3.5:4b``; a tag that is neither in the table nor a variant of a tag in it has none.
    """
    exact = tag_value(table, tag)
    if exact is not None:
        return exact
    normalized = normalize_tag(tag)
    for measured, value in table.items():
        if normalized.startswith(f"{measured}-"):
            return value
    return None
