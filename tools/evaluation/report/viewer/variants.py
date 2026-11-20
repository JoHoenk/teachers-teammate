"""The variants of a report: one per series key, labelled by the facts that differ.

A *variant* is a configuration: one model, at one temperature, in one exchange with its steps, with
one request profile, on one machine, from one prompt, isolated or not.  Its identity is the series
key of :mod:`...core.axes` — the compare key plus the machine and the isolation — so the repeats of
one variant are the runs that differ in nothing else, and they are what the scorecard's range is
made of.

The label of a variant shows only the facts that differ among the variants on screen, so a table
of one model at three temperatures is not three times "gemma4:12b".  The full facts are kept for
the hover.  Every fact is named and printed by :mod:`...core.axes`, so a label here and a row of a
comparison read the same value the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from ...core import axes, protocol_table
from ...core.axis_table import AXES, AXIS_NAMES
from ...core.records import Protocol, RunHeader, Session
from .. import pooled
from ..groups import GroupKey
from ..load import LoadedRun

RUN_FACTS: tuple[str, ...] = (
    "machine",
    "isolated",
    "date",
    "seed",
    "eval_version",
    "testset",
    "testset_sha256",
    "tool_revision",
    "quant",
    "params",
    "family",
    "ollama_version",
    "mode",
    "prompt8",
)
"""The facts a run records that are not axes: where it ran, when, and what the model is made of.

``mode`` and ``prompt8`` are the readable forms of two axes (``exchange`` with ``steps``, and
``instructions``), kept because the page groups and names prompts by them.
"""

FACT_ORDER: tuple[str, ...] = (*AXIS_NAMES, *RUN_FACTS)
"""The facts a variant is labelled by, in the order a label prints them."""

LABEL_FACTS: tuple[str, ...] = (
    *(axis.name for axis in AXES if axis.in_name),
    "machine",
    "eval_version",
)
"""The facts a reader compares, which a label prints first; the rest only when it has to.

Every axis that can be a tag of a file name, plus the machine and the eval version.  A hash
(``instructions``) is left out: it names a prompt and does not describe one.
"""


@dataclass(frozen=True)
class Variant:
    """One configuration of a report: its key, its facts, its repeats and how far it got."""

    id: str
    """The short id the page uses (``v1``), because a label is long and changes with the table."""

    key: GroupKey
    """The series key: what makes two runs one variant, and what a gate groups by."""

    facts: Mapping[str, str]
    repeats: tuple[int, ...]
    texts_done: Mapping[int, int]
    """How many texts each repeat holds; a run still being written has fewer than the set."""
    complete: bool
    """Whether every repeat holds every text of the test set."""
    eval_version: int
    """The newest eval version its repeats were measured under, ``0`` when the table has none."""
    label: str = ""
    """The facts that differ among the variants of one report, joined for the table."""


@lru_cache(maxsize=1)
def _table() -> Mapping[str, Any]:
    """Return the protocol table, read once: every run of a report asks it for its version."""
    return protocol_table.read_table()


def eval_version_of(header: RunHeader) -> int:
    """Return the eval version the run of *header* was measured under, ``0`` when unknown.

    The version is not in the header any more: the header records the fingerprint of its request
    side, and the table says which version accepts that fingerprint.  A run whose protocol the
    table does not record is evidence of nothing, so it reads as version ``0``.
    """
    found = protocol_table.version_of(header.protocol.fingerprint, _table())
    return 0 if found is None else found


def identity_of(header: RunHeader) -> GroupKey:
    """Return what makes the run of *header* one variant: its series key."""
    return GroupKey.series(header)


def facts_of(runs: Sequence[LoadedRun]) -> dict[str, str]:
    """Return the facts of the runs of one variant, as the page shows them.

    Everything a run was measured under is here, so a new header fact (thinking, a budget, a
    prompt variant) shows up as a variant fact without a change in this module: the keys are the
    header's own, and only the ones the page cannot print as they are get a short form.  A fact
    the repeats do not share (the date, the seed, the tool revision) lists each value once, so the
    hover never shows one run's fact as the variant's.
    """
    found = [_facts(run) for run in runs]
    return {
        name: ", ".join(dict.fromkeys(one[name] for one in found if one[name] != "")) or ""
        for name in found[0]
    }


def _facts(run: LoadedRun) -> dict[str, str]:
    """Return the facts of one run: its header's, and the session's date and tool revision."""
    session = _session(run)
    return {
        **_header_facts(run.header),
        "tool_revision": session.tool_revision if session is not None else "",
        "date": axes.date_of(session.date) if session is not None else "",
    }


def _session(run: LoadedRun) -> Session | None:
    """Return the first session of *run*, which is where its date and tool revision sit."""
    return run.run.sessions[0] if run.run.sessions else None


def _header_facts(header: RunHeader) -> dict[str, str]:
    """Return the facts a run's header records, as the page shows them.

    Every axis is printed by :func:`~...core.axes.axis_label`, so a fact here reads the way a row of
    a comparison and a tag of a file name read it.  The rest is what a run records beside its axes:
    where it ran, when, and what the model is made of.
    """
    protocol = header.protocol
    environment = header.environment
    info = header.model_info
    facts = {
        name: axes.axis_label(name, value) for name, value in axes.axis_values(protocol).items()
    }
    facts.update(
        {
            "quant": str(info.get("quantization", "")),
            "params": str(info.get("parameter_size", "")),
            "family": str(info.get("family", "")),
            "mode": pooled.mode_label(protocol),
            "machine": environment.machine,
            "ollama_version": environment.ollama_version,
            "prompt8": _prompt8(protocol),
            "eval_version": str(eval_version_of(header)),
            "isolated": "isolated" if environment.isolated else "shared",
            "seed": str(header.run.seed),
            "testset": header.input.testset,
            "testset_sha256": header.input.question_sha256[:8],
        }
    )
    return facts


def _prompt8(protocol: Protocol) -> str:
    """Return the 8 characters the page names a prompt by: the run's own instruction hash.

    A run records one hash per language, in the table's order; the first stands for the run, so
    two runs of one variant name their prompt alike and two prompts never share a name.
    """
    return protocol.instructions[0][1][:8] if protocol.instructions else ""


def build(runs: Iterable[LoadedRun], texts_total: Mapping[str, int]) -> tuple[Variant, ...]:
    """Return the variants *runs* make, each with its repeats in order and its label.

    Args:
        runs: The runs of the store, which :func:`~..load.distinct_runs` has already freed of
            copies: two files of one run key are one measurement and are not counted twice.
        texts_total: How many texts each test set holds, by set name, which is what a complete run
            of that set has.
    """
    grouped: dict[GroupKey, list[LoadedRun]] = {}
    for run in runs:
        grouped.setdefault(identity_of(run.header), []).append(run)
    built: list[Variant] = []
    for number, key in enumerate(sorted(grouped, key=GroupKey.order), start=1):
        ordered = sorted(grouped[key], key=lambda run: run.repeat)
        repeats = tuple(run.repeat for run in ordered)
        # A text a run holds twice (a resumed run) counts once: the later answer is the one shown.
        texts_done = {run.repeat: len({item.item for item in run.items}) for run in ordered}
        total = texts_total.get(key.testset, 0)
        version = max(eval_version_of(run.header) for run in ordered)
        facts = facts_of(ordered)
        # The repeats of one variant may carry two versions of an additive change, which measured
        # the same thing: the variant reads as the newest of them, and its label says so.
        facts["eval_version"] = str(version)
        built.append(
            Variant(
                id=f"v{number}",
                key=key,
                facts=facts,
                repeats=repeats,
                texts_done=texts_done,
                complete=all(done == total for done in texts_done.values()),
                eval_version=version,
            )
        )
    return labelled(tuple(built))


def differing(variants: Sequence[Variant]) -> tuple[str, ...]:
    """Return the facts whose value is not the same in every variant."""
    names = [name for name in FACT_ORDER if any(name in variant.facts for variant in variants)]
    return tuple(
        name for name in names if len({variant.facts.get(name) for variant in variants}) > 1
    )


def labelled(variants: Sequence[Variant]) -> tuple[Variant, ...]:
    """Return *variants* with the label of each set to the facts that differ among them.

    The model is always part of the label, because a table of one model's temperatures still has
    to say which model it is; every other fact is printed only when it separates the variants.
    The facts a reader compares come first (:data:`LABEL_FACTS`), and a fact that is only
    provenance — the digest, the seed, the tool revision — is added only when two variants would
    otherwise read the same, so a label stays short and is still unique.
    """
    names = differing(variants)
    preferred = [name for name in LABEL_FACTS if name in names]
    rest = [name for name in FACT_ORDER if name in names and name not in preferred]
    chosen = {variant.id: list(preferred) for variant in variants}
    labels = {variant.id: _label(variant, chosen[variant.id]) for variant in variants}
    for name in rest:
        shared = _shared(labels)
        if not shared:
            break
        # A provenance fact is added only where it tells apart variants that read the same, and
        # only if it does: the digest of two variants that differ in their prompt would be noise.
        groups: dict[str, list[Variant]] = {}
        for variant in variants:
            if variant.id in shared:
                groups.setdefault(labels[variant.id], []).append(variant)
        for group in groups.values():
            if len({variant.facts.get(name) for variant in group}) > 1:
                for variant in group:
                    chosen[variant.id].append(name)
                    labels[variant.id] = _label(variant, chosen[variant.id])
    return tuple(replace(variant, label=labels[variant.id]) for variant in variants)


def _label(variant: Variant, names: Sequence[str]) -> str:
    """Return the label of *variant* from the facts *names*, the model first.

    A fact is printed as :func:`~...core.axes.axis_label` printed it, so a label never invents a
    second way to write a value: ``temperature 0.4``, not ``T0.4``.
    """
    return " · ".join(
        [variant.facts.get("model", "")]
        + [variant.facts.get(name, "") for name in names if name != "model"]
    )


def _shared(labels: Mapping[str, str]) -> set[str]:
    """Return the ids of the variants whose label another variant carries too."""
    seen: dict[str, list[str]] = {}
    for key, label in labels.items():
        seen.setdefault(label, []).append(key)
    return {key for group in seen.values() if len(group) > 1 for key in group}


def eval_versions(variants: Sequence[Variant]) -> tuple[int, ...]:
    """Return the eval versions the variants were measured under, in order.

    Two eval versions are never put in one table: the page shows them in separate tabs.
    """
    return tuple(sorted({variant.eval_version for variant in variants}))


__all__ = [
    "FACT_ORDER",
    "LABEL_FACTS",
    "RUN_FACTS",
    "Variant",
    "build",
    "differing",
    "eval_version_of",
    "eval_versions",
    "facts_of",
    "identity_of",
    "labelled",
]
