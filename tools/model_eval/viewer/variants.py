"""The variants of a report: one per group key, labelled by the facts that differ.

A *variant* is a configuration: one model, at one temperature, in one answer mode, with one
request profile, on one machine, from one prompt, isolated or not.  Its identity is the
:class:`~..grouping.GroupKey` and the three facts a run's own header adds to it
(:data:`EXTRA_FACTS`): the repeats of one variant are the runs that differ in nothing else, and
they are what the scorecard's range is made of.  Two runs that differ in the prompt, the answer
format or in whether the model was reloaded between texts are two variants, never two repeats.

The label of a variant shows only the facts that differ among the variants on screen, so a table
of one model at three temperatures is not three times "gemma4:12b".  The full facts are kept for
the hover.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, NamedTuple

from ..grouping import GroupKey
from .load import CacheRun

FACT_ORDER: tuple[str, ...] = (
    "model",
    "digest8",
    "quant",
    "params",
    "family",
    "mode",
    "temperature",
    "profile",
    "step1_prompt",
    "num_ctx",
    "machine",
    "provider",
    "ollama_version",
    "prompt8",
    "eval_version",
    "answer_format_version",
    "isolated",
    "seed",
    "testset",
    "testset_sha256",
    "tool_revision",
    "date",
    "explainer",
)
"""The facts a variant is labelled by, in the order a label prints them."""

EXTRA_FACTS: tuple[str, ...] = ("isolated", "prompt8", "answer_format_version")
"""The header facts beyond the group key that make two runs two variants rather than two repeats.

``instructions_sha256`` is which prompt the model was asked; ``isolated`` is whether the model
was unloaded between texts, which changes what it carries over; the answer format is how its
answer is read.  Everything else a header holds (the date, the tool revision, the seed, the server
version) varies between the repeats of one configuration and is not part of its identity."""

LABEL_FACTS: tuple[str, ...] = (
    "model",
    "quant",
    "mode",
    "temperature",
    "profile",
    "num_ctx",
    "machine",
    "explainer",
    "eval_version",
)
"""The facts a reader compares, which a label prints first; the rest only when it has to."""


@dataclass(frozen=True)
class Variant:
    """One configuration of a report: its key, its facts, its repeats and how far it got."""

    id: str
    """The short id the page uses (``v1``), because a label is long and changes with the table."""

    key: GroupKey
    identity: Identity
    """What makes two runs one variant: the group key and the :data:`EXTRA_FACTS`."""

    facts: Mapping[str, str]
    repeats: tuple[int, ...]
    texts_done: Mapping[int, int]
    """How many texts each repeat holds; a run still being written has fewer than the set."""
    complete: bool
    """Whether every repeat holds every text of the test set."""
    label: str = ""
    """The facts that differ among the variants of one report, joined for the table."""

    @property
    def eval_version(self) -> int:
        """Return the eval version the variant was measured under."""
        return self.key.eval_version


class Identity(NamedTuple):
    """What makes two runs one variant: the group key and the :data:`EXTRA_FACTS`."""

    key: GroupKey
    isolated: str
    prompt8: str
    answer_format_version: str

    def order(self) -> tuple[object, ...]:
        """Return a sort key, so the variants of a report come in the same order everywhere."""
        return (self.key.order(), self.key.eval_version, self.isolated, self.prompt8)


def identity_of(header: Mapping[str, Any]) -> Identity:
    """Return what makes the run of *header* one variant.

    Raises:
        KeyError: if the header lacks a field the key is made of.
        ValueError: if a field has a value of the wrong kind.
    """
    facts = facts_of([header])
    return Identity(
        key=GroupKey.of_local(header),
        isolated=facts["isolated"],
        prompt8=facts["prompt8"],
        answer_format_version=facts["answer_format_version"],
    )


def facts_of(headers: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Return the facts of the run headers of one variant, as the page shows them.

    Everything a run was measured under is here, so a new header fact (thinking, a budget, a
    prompt variant) shows up as a variant fact without a change in this module: the keys are the
    header's own, and only the ones the page cannot print as they are get a short form.  A fact
    the repeats do not share (the date, the seed, the tool revision) lists each value once, so the
    hover never shows one run's fact as the variant's.
    """
    found = [_facts(header) for header in headers]
    return {
        name: ", ".join(dict.fromkeys(one[name] for one in found if one[name] != "")) or ""
        for name in found[0]
    }


def _facts(header: Mapping[str, Any]) -> dict[str, str]:
    """Return the facts of one run header."""
    info = header.get("model_info") or {}
    profile = header.get("request_profile") or {}
    digest = str(info.get("digest", ""))
    prompt = str(header.get("instructions_sha256", ""))
    testset_hash = str(header.get("testset_sha256") or header.get("manifest_sha256") or "")
    explainer = header.get("explainer")
    return {
        "model": str(header.get("model", "")),
        "digest8": digest[:8],
        "quant": str(info.get("quantization", "")),
        "params": str(info.get("parameter_size", "")),
        "family": str(info.get("family", "")),
        "mode": str(header.get("mode", "")),
        "temperature": f"{float(header.get('temperature', 0.0)):g}",
        "profile": "compact" if profile.get("compact_json") else "full",
        "step1_prompt": str(profile.get("prompt", "")),
        "num_ctx": str(header.get("num_ctx", "")),
        "machine": str(header.get("machine") or ""),
        "provider": str(header.get("provider", "")),
        "ollama_version": str(header.get("ollama_version", "")),
        "prompt8": prompt[:8],
        "eval_version": str(header.get("eval_version", "")),
        "answer_format_version": str(header.get("answer_format_version", "")),
        "isolated": "isolated" if header.get("isolated") else "shared",
        "seed": str(header.get("seed", "")),
        "testset": str(header.get("testset", "")),
        "testset_sha256": testset_hash[:8],
        "tool_revision": str(header.get("tool_revision", "")),
        "date": str(header.get("date", ""))[:10],
        "explainer": "-" if explainer is None else str(explainer),
    }


def newest_runs(
    runs: Sequence[CacheRun],
) -> tuple[tuple[CacheRun, ...], tuple[tuple[CacheRun, CacheRun], ...]]:
    """Return the runs without doubles, and the doubles with the run that replaced each.

    Two files that hold the same repeat of the same variant are one measurement that was written
    twice (a run copied to another cache, or started again).  The one with more texts stays, so a
    run that is still being written never replaces a finished one, and of two equally far the
    newer one by its header's date, then by its name.  The other is reported as skipped, so the
    footer says why a file of a cache does not count.
    """
    chosen: dict[tuple[Identity, int], CacheRun] = {}
    dropped: list[tuple[CacheRun, CacheRun]] = []
    for run in runs:
        key = (identity_of(run.header), run.repeat)
        other = chosen.get(key)
        if other is None:
            chosen[key] = run
            continue
        keep, lose = (run, other) if _rank(run) > _rank(other) else (other, run)
        chosen[key] = keep
        dropped.append((lose, keep))
    kept = {id(run) for run in chosen.values()}
    return tuple(run for run in runs if id(run) in kept), tuple(dropped)


def _rank(run: CacheRun) -> tuple[int, str, str]:
    """Return what decides which of two files of one run stays: texts, date, name."""
    return (len({row.id for row in run.rows}), str(run.header.get("date", "")), run.name)


def build(runs: Iterable[CacheRun], texts_total: int) -> tuple[Variant, ...]:
    """Return the variants *runs* make, each with its repeats in order and its label.

    Args:
        runs: The runs of every cache of the report.
        texts_total: How many texts the test set holds, which is what a complete run has.

    Raises:
        ValueError: if two runs of one variant carry the same repeat number, because a repeat is
            one run and two would be counted twice.
    """
    grouped: dict[Identity, list[CacheRun]] = {}
    for run in runs:
        grouped.setdefault(identity_of(run.header), []).append(run)
    built: list[Variant] = []
    for number, identity in enumerate(sorted(grouped, key=Identity.order), start=1):
        key = identity.key
        ordered = sorted(grouped[identity], key=lambda run: run.repeat)
        repeats = tuple(run.repeat for run in ordered)
        if len(set(repeats)) != len(repeats):
            raise ValueError(
                f"{key.model}: two runs of repeat {sorted(repeats)} are in one variant "
                f"({', '.join(run.name for run in ordered)}); a repeat is one run"
            )
        # A text a run holds twice (a resumed run) counts once: the later row is the one shown.
        texts_done = {run.repeat: len({row.id for row in run.rows}) for run in ordered}
        built.append(
            Variant(
                id=f"v{number}",
                key=key,
                identity=identity,
                facts=facts_of([run.header for run in ordered]),
                repeats=repeats,
                texts_done=texts_done,
                complete=all(done == texts_total for done in texts_done.values()),
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


SHOWN_AS: Mapping[str, str] = {"temperature": "T{}", "num_ctx": "ctx {}", "eval_version": "eval {}"}
"""How a fact reads in a label, so a bare number says what it is (``T1``, not ``1``)."""


def _label(variant: Variant, names: Sequence[str]) -> str:
    """Return the label of *variant* from the facts *names*, the model first."""
    return " · ".join(
        [variant.facts.get("model", "")]
        + [
            SHOWN_AS.get(name, "{}").format(variant.facts.get(name, ""))
            for name in names
            if name != "model"
        ]
    )


def _shared(labels: Mapping[str, str]) -> set[str]:
    """Return the ids of the variants whose label another variant carries too."""
    seen: dict[str, list[str]] = {}
    for key, label in labels.items():
        seen.setdefault(label, []).append(key)
    return {key for group in seen.values() if len(group) > 1 for key in group}


def eval_versions(variants: Sequence[Variant]) -> tuple[int, ...]:
    """Return the eval versions the variants were measured under, in order.

    Two eval versions are never put in one table (D2): the page shows them in separate tabs.
    """
    return tuple(sorted({variant.eval_version for variant in variants}))


__all__ = [
    "EXTRA_FACTS",
    "FACT_ORDER",
    "Identity",
    "Variant",
    "build",
    "differing",
    "eval_versions",
    "facts_of",
    "identity_of",
    "labelled",
    "newest_runs",
]
