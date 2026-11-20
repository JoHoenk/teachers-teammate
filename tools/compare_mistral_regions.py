"""Developer tool: which Mistral models the global and the EU endpoint serve.

NOT run in CI - it needs network access and a Mistral key.  Mistral's documentation says only that
a regional endpoint "only serves models hosted in that region" and names no list, so the one way
to know is to ask both endpoints.  Run it after editing the built-in model list of
``teachers_teammate/infrastructure/providers/_mistral_common.py``, and whenever Mistral changes its
models::

    export MISTRAL_API_KEY=...        # in your own shell: the key is read from here only
    bazel run //tools:compare_mistral_regions

The report lists every entry either endpoint returns, whether each endpoint has it, and what the
app does with it: offered to a teacher, hidden (with the reason: code, audio, embedding, archived
...), retiring, served under another name, or retired.  It also says, for every model of the
built-in list, whether each endpoint serves it; which models only one endpoint offers; and which
fields the EU entries carry, in case Mistral adds a region field.  The key is never printed and
never taken from the command line.

Exit code: 0 when both endpoints answered, 1 when one did not, 2 when no key is set.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import os
import sys
from typing import Any
import urllib.error

from teachers_teammate.domain.model_listing import CuratedModels
from teachers_teammate.infrastructure.providers import _mistral_common as common

ENDPOINTS: dict[str, str] = {"global": common.GLOBAL_BASE_URL, "EU": common.EU_BASE_URL}
"""The endpoints compared, by the name the report calls them."""


OFFERED, RETIRING, ALSO_SERVED, RETIRED, HIDDEN = (
    "offered",
    "retiring",
    "also served",
    "retired",
    "hidden",
)
"""What the app does with an entry, in the order the report lists them."""

_KINDS = (OFFERED, RETIRING, ALSO_SERVED, RETIRED, HIDDEN)


@dataclass(frozen=True)
class Status:
    """What the app does with one entry of an endpoint."""

    kind: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}" if self.detail else self.kind


@dataclass(frozen=True)
class Served:
    """The model names one endpoint answers to, from its ``/models`` entries."""

    entries: int
    names: frozenset[str]
    """Every name a chat-capable, not yet retired model answers to (IDs and aliases)."""
    offered: frozenset[str]
    """The names the app would list for a teacher."""
    fields: frozenset[str]
    """The keys the raw entries carry."""
    status: Mapping[str, Status]
    """What the app does with each entry, by its ID."""


@dataclass(frozen=True)
class Comparison:
    """The two endpoints side by side."""

    global_: Served
    eu: Served
    builtin: tuple[tuple[str, bool, bool], ...]
    """``(name, served on the global endpoint, served on the EU endpoint)`` per built-in model."""

    @property
    def only_global(self) -> list[str]:
        """Return the names the app would list for the global endpoint only."""
        return sorted(self.global_.offered - self.eu.offered)

    @property
    def only_eu(self) -> list[str]:
        """Return the names the app would list for the EU endpoint only."""
        return sorted(self.eu.offered - self.global_.offered)

    @property
    def rows(self) -> list[tuple[str, bool, bool, str]]:
        """Return every entry of either endpoint as ``(id, on global, on EU, what the app does)``.

        Ordered by what the app does with the entry (offered first), then by ID.  When the two
        endpoints disagree about an entry, the text says both.
        """
        rows: list[tuple[int, str, bool, bool, str]] = []
        for model_id in sorted({*self.global_.status, *self.eu.status}):
            on_global, on_eu = model_id in self.global_.status, model_id in self.eu.status
            seen = self.global_.status.get(model_id), self.eu.status.get(model_id)
            first = seen[0] or seen[1]
            if seen[0] and seen[1] and seen[0] != seen[1]:
                text = f"global: {seen[0]} / EU: {seen[1]}"
            else:
                text = str(first)
            rows.append(
                (_KINDS.index(first.kind) if first else 0, model_id, on_global, on_eu, text)
            )
        rows.sort(key=lambda row: (row[0], row[1]))
        return [(model_id, on_global, on_eu, text) for _, model_id, on_global, on_eu, text in rows]


def _status(entry: dict[str, Any], curated: CuratedModels) -> Status:
    """Return what the app does with *entry*, given the curation of its endpoint's answer."""
    reason = common.unusable_reason(entry)
    if reason:
        return Status(HIDDEN, reason)
    model_id = str(entry["id"])
    if model_id in curated.ids:
        return Status(OFFERED)
    retiring = next((model for model in curated.retiring if model.id == model_id), None)
    if retiring is not None:
        return Status(RETIRING, str(retiring.retires_on or ""))
    if model_id in curated.also_served:
        return Status(ALSO_SERVED, "alias or older version of an offered model")
    return Status(RETIRED)


def served_by(entries: list[dict[str, Any]], today: date) -> Served:
    """Return what an endpoint that answered with *entries* serves.

    The same curation as the app's own model list decides what a chat model is, so the report
    agrees with what the Settings window would show.  A model that retires later is still served
    today, so it counts as served.
    """
    curated = common.curate_mistral_models(entries, today)
    names = {*curated.ids, *curated.also_served, *(model.id for model in curated.retiring)}
    fields = {key for entry in entries if isinstance(entry, dict) for key in entry}
    status = {
        str(entry["id"]): _status(entry, curated)
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    }
    return Served(len(entries), frozenset(names), frozenset(curated.ids), frozenset(fields), status)


def compare(
    global_entries: list[dict[str, Any]],
    eu_entries: list[dict[str, Any]],
    builtin: Sequence[str],
    today: date,
) -> Comparison:
    """Return the two endpoints' answers compared, with each *builtin* model looked up."""
    global_, eu = served_by(global_entries, today), served_by(eu_entries, today)
    return Comparison(
        global_,
        eu,
        tuple((name, name in global_.names, name in eu.names) for name in builtin),
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "NO"


def _summary(name: str, served: Served) -> str:
    """Return the one line that says how many entries *name* answered with and what became of them."""
    counts = Counter(status.kind for status in served.status.values())
    parts = ", ".join(f"{counts[kind]} {kind}" for kind in _KINDS if counts[kind])
    return f"{name.ljust(6)}  {ENDPOINTS[name]}: {served.entries} entries ({parts or 'none'})"


def _all_models(comparison: Comparison) -> list[str]:
    """Return the table of every entry either endpoint returned."""
    rows = comparison.rows
    width = max((len(model_id) for model_id, *_ in rows), default=len("model"))
    lines = [f"All models ({len(rows)}):", f"  {'model'.ljust(width)}  global  EU     in the app"]
    for model_id, on_global, on_eu, text in rows:
        lines.append(
            f"  {model_id.ljust(width)}  {('yes' if on_global else '-').ljust(6)}  "
            f"{('yes' if on_eu else '-').ljust(5)}  {text}"
        )
    return lines


def format_report(comparison: Comparison) -> str:
    """Return the report of *comparison* as text."""
    lines = [
        _summary("global", comparison.global_),
        _summary("EU", comparison.eu),
        "",
        *_all_models(comparison),
        "",
        "Built-in list (what the app offers when a live list is not available):",
    ]
    width = max((len(name) for name, _, _ in comparison.builtin), default=0)
    lines.append(f"  {'model'.ljust(width)}  global  EU")
    for name, on_global, on_eu in comparison.builtin:
        lines.append(f"  {name.ljust(width)}  {_yes_no(on_global).ljust(6)}  {_yes_no(on_eu)}")
    lines += ["", f"Offered on the global endpoint only ({len(comparison.only_global)}):"]
    lines += [f"  {name}" for name in comparison.only_global] or ["  (none)"]
    lines += ["", f"Offered on the EU endpoint only ({len(comparison.only_eu)}):"]
    lines += [f"  {name}" for name in comparison.only_eu] or ["  (none)"]
    lines += ["", "Fields of an EU entry: " + (", ".join(sorted(comparison.eu.fields)) or "-")]
    return "\n".join(lines)


def _fetch(name: str, api_key: str) -> list[dict[str, Any]] | None:
    """Return the entries the endpoint *name* answers with, or ``None`` after saying why not."""
    try:
        return common.fetch_model_entries(ENDPOINTS[name], api_key)
    except urllib.error.HTTPError as exc:  # an OSError too, but its status says why
        print(f"{name} endpoint answered with error {exc.code}", file=sys.stderr)
    except OSError:
        print(f"{name} endpoint: no connection", file=sys.stderr)
    except ValueError, AttributeError:
        print(f"{name} endpoint: the answer is not a model list", file=sys.stderr)
    return None


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed command line (there are no options; ``--help`` explains the tool)."""
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").splitlines()[0],
        epilog="The key is read from the MISTRAL_API_KEY environment variable.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Ask both endpoints for their models and print the comparison."""
    _parse(argv)
    api_key = os.environ.get(common.ENV_KEY, "").strip()
    if not api_key:
        print(f"{common.ENV_KEY} is not set", file=sys.stderr)
        return 2
    answers = {name: _fetch(name, api_key) for name in ENDPOINTS}
    global_entries, eu_entries = answers["global"], answers["EU"]
    if global_entries is None or eu_entries is None:
        return 1
    print(format_report(compare(global_entries, eu_entries, common.FALLBACK_MODELS, date.today())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
