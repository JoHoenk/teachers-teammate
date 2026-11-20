"""The keys of a run, and the file name derived from its axes.

Every attribute a run can vary in is an **axis**, declared once in :mod:`.axis_table`.  This module
builds the four keys from them, each with one job:

* the **compare key** (pooling quality) — the task, the test set, every axis whose value differs
  from its default, and every axis a newer writer added.  Not in it: the seed, the repeat, the
  timeout (the seed is ``base + repeat - 1``, so it differs per repeat), the model's facts and the
  environment.  Two runs with the same compare key measured the same thing and may be pooled;
  ``compare --along`` walks it.  An axis at its default is left out, so adding an axis with a
  default moves no key, no sample, no cache and no file name.
* the **series key** (a gate group) — the compare key plus the machine and whether the run was
  isolated.  The repeats of one setup on one machine share it, so a gate compares like with like.
  The server version is not in it: a server upgrade between repeats stays one series.
* the **run key** (one file) — the series key plus the repeat.  It names a run's file, its score
  cache and its spot-check sample, and it is what a duplicate check compares.
* the **environment key** (speed) — the compare key plus the machine, the server version and
  whether the run was isolated.  Only the speed table reads it.

The file name is a **label** derived from the axes and never read back: a reader that parses a name
is a bug, because a name cannot carry a new axis.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .axis_table import (
    AXES,
    AXIS_BY_NAME,
    AXIS_NAMES,
    DERIVED,
    FIXED,
    MODEL,
    SAMPLING_PREFIX,
    UNKNOWN_PREFIX,
    Axis,
    axis_label,
    axis_tag,
    axis_values,
    digest8,
    model_slug,
    path_safe,
    plain,
)
from .constants import OLLAMA, UNKNOWN_MACHINE
from .hashing import sha256_json
from .records import RunHeader


def date_of(stamp: str) -> str:
    """Return the date part (``YYYY-MM-DD``) of a session's ISO timestamp, as a name and a table use it.

    A run's file name and the index's date column hold the day, not the second: a timestamp carries
    ``:`` and ``+``, which are not path-safe on every platform.
    """
    return stamp.strip()[:10]


def compare_key(header: RunHeader) -> dict[str, Any]:
    """Return the compare key of *header*: what two runs must share to be pooled.

    An axis is in the key only when its value differs from its default, so adding an axis with a
    default moves no key.  An axis a newer writer added is in the key under ``unknown.<name>``, so
    two runs that differ in it never pool by accident.  The fingerprint is not in it: an additive
    protocol version must not split a group.  The seed, the repeat and the timeout are not in it
    either: the seed is derived from the repeat, so repeats would never pool.
    """
    values = axis_values(header.protocol)
    key: dict[str, Any] = {"task": header.task, "testset": header.input.testset}
    for axis in AXES:
        value = plain(values[axis.name])
        if axis.default is not None and value == plain(axis.default):
            continue
        key[axis.name] = value
    key.update(
        {
            f"{UNKNOWN_PREFIX}{name}": plain(value)
            for name, value in sorted(header.protocol.extra.items())
        }
    )
    return key


def compare_key_sha256(header: RunHeader) -> str:
    """Return the hash of the compare key, which is the ``key8`` of a file name."""
    return sha256_json(compare_key(header))


def series_key(header: RunHeader) -> dict[str, Any]:
    """Return the series key of *header*: the compare key plus the machine and isolation.

    The repeats of one setup on one machine share it, which is what a gate compares.  The server
    version is not in it: a server upgrade between repeats stays one series.
    """
    key = compare_key(header)
    key.update(
        {
            "machine": header.environment.machine,
            "isolated": header.environment.isolated,
        }
    )
    return key


def series_key_sha256(header: RunHeader) -> str:
    """Return the hash of the series key, which names a gate group."""
    return sha256_json(series_key(header))


def run_key(header: RunHeader) -> dict[str, Any]:
    """Return the run key of *header*: the series key plus the repeat.

    One run key is one file: it is what a resume looks for, what names a score cache and a
    spot-check sample, and what a duplicate check compares.
    """
    key = series_key(header)
    key["repeat"] = header.run.repeat
    return key


def run_key_sha256(header: RunHeader) -> str:
    """Return the hash of the run key, which names a run's file, cache and sample."""
    return sha256_json(run_key(header))


def environment_key(header: RunHeader) -> dict[str, Any]:
    """Return the environment key of *header*: the compare key plus where the run ran.

    Only speed needs it: two runs of one model on two machines measured the same quality and
    different speeds.
    """
    key = compare_key(header)
    key.update(
        {
            "machine": header.environment.machine,
            "ollama_version": header.environment.ollama_version,
            "isolated": header.environment.isolated,
        }
    )
    return key


def varying_axes(headers: Sequence[RunHeader]) -> tuple[str, ...]:
    """Return the names of the compare key whose value differs over *headers*, in its order.

    A report labels a row by the axes that vary within the selection, so two prompt variants or two
    test sets are two rows a reader can tell apart.  An empty selection varies in nothing.
    """
    keys = [compare_key(header) for header in headers]
    if not keys:
        return ()
    names = list(dict.fromkeys([*keys[0], *(name for key in keys for name in key)]))
    return tuple(name for name in names if len({sha256_json(key.get(name)) for key in keys}) > 1)


def axis_text(value: Any) -> str:
    """Return a compare-key value as one short readable word, for a row's label.

    Kept for a caller that has the value and not its axis name; a caller that knows the name uses
    :func:`~.axis_table.axis_label`, which is the one label function.
    """
    return axis_label("", value)


def host_of(header: RunHeader) -> str:
    """Return the machine a run ran on, or its provider, or :data:`UNKNOWN_MACHINE`.

    The one place the fallback lives, so a file name and a table row cannot disagree.
    An Ollama run without a machine is ``unknown-machine`` and not ``ollama``: the provider is
    always set, so falling back to it would name every unlabelled run after the server software.
    """
    machine = header.environment.machine
    if machine:
        return machine
    provider = header.protocol.provider
    return provider if provider and provider != OLLAMA else UNKNOWN_MACHINE


def file_name_tags(header: RunHeader) -> list[str]:
    """Return the tags of a run's file name: one per non-default axis, plus temperature and repeat.

    ``temperature`` and ``repeat`` always get a tag, because they are what a reader looks for
    first.  Every other axis gets one only when it differs from its default, so the common run
    keeps a short name.  Each sampling option is its own tag, so ``presence_penalty-0`` says what
    it is without a reader having to know the order of a dict.

    An isolated run gets ``isolated``, because isolation is in the run key: without the tag, the
    isolated run and the shared one of the same setup would ask for one file name.
    """
    values = axis_values(header.protocol)
    tags: list[str] = []
    for axis in AXES:
        if not axis.in_name or axis.name in {"model", "model_digest", "temperature"}:
            continue
        value = values[axis.name]
        if axis.name == "sampling":
            tags.extend(
                f"{SAMPLING_PREFIX}{name}-{axis_tag(axis.name, option)}"
                for name, option in sorted(value.items())
            )
            continue
        if plain(value) == plain(axis.default):
            continue
        tags.append(f"{axis.name}-{axis_tag(axis.name, value)}")
    tags.append(f"temperature-{axis_tag('temperature', values['temperature'])}")
    tags.append(f"repeat-{header.run.repeat}")
    if header.environment.isolated:
        tags.append("isolated")
    return tags


def run_file_name(header: RunHeader, date: str) -> str:
    """Return the file name of a run: a label derived from its axes, never read back.

    ``<date>_<machine or provider>_<model-slug>_<digest8>__<tags>__<key8>.run.jsonl``.  An empty
    machine is written ``unknown-machine``, so the name stays one path segment.
    """
    parts = [
        date,
        host_of(header),
        model_slug(header.protocol.model),
        digest8(header.protocol.model_digest),
    ]
    tags = file_name_tags(header)
    return f"{'_'.join(parts)}__{'__'.join(tags)}__{compare_key_sha256(header)[:8]}.run.jsonl"


__all__ = [
    "AXES",
    "AXIS_BY_NAME",
    "AXIS_NAMES",
    "DERIVED",
    "FIXED",
    "MODEL",
    "SAMPLING_PREFIX",
    "UNKNOWN_MACHINE",
    "UNKNOWN_PREFIX",
    "Axis",
    "axis_label",
    "axis_tag",
    "axis_text",
    "axis_values",
    "compare_key",
    "compare_key_sha256",
    "date_of",
    "digest8",
    "environment_key",
    "file_name_tags",
    "host_of",
    "model_slug",
    "path_safe",
    "run_file_name",
    "run_key",
    "run_key_sha256",
    "series_key",
    "series_key_sha256",
    "varying_axes",
]
