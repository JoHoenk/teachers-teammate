"""Developer tool: verify the Ollama model catalog against the real registry and server.

NOT run in CI — it needs network access to the Ollama registry and, for two of the three jobs, a
local Ollama server.  Run it after editing ``teachers_teammate/domain/model_catalog.py``.

Three jobs, three subcommands::

    bazel run //tools:verify_model_catalog -- sizes
    bazel run //tools:verify_model_catalog -- capabilities
    bazel run //tools:verify_model_catalog -- structured-output

``sizes`` (the default) reads every catalog tag's manifest from the registry (or, for
``hf.co/...`` tags, the Hugging Face file list) and compares the real download size with the
catalog's ``download_gb`` estimate (tolerance: ±25 % + 0.5 GB).

``capabilities`` asks a local Ollama server for each model's capabilities and prints whether it
can read images — this is how the ``gemma4:12b`` open question (model plan §10) can be settled.

``structured-output`` sends the review answer format to every writing model and checks that the
answer is valid JSON-schema output, that at least 90 % of the returned sentences can be spliced
back into the text, that the changes carry explanations, and that Ollama did not stop at the
context limit.  Five fixtures (English, German, French, Markdown, one full page of German), three
runs each above temperature 0, and a per-run report of placed / explained / orphans / tokens /
``done_reason`` / seconds.  The verdict is what ``OllamaModelSpec.structured_output`` records;
enter it in the catalog by hand, as the download sizes are.  Needs a pulled model.

``--tag TAG`` (repeatable) restricts any subcommand to models that are not in the catalog yet, so
a candidate can be checked before it is added.

The work lives in :mod:`tools.model_eval`; this file parses arguments and prints.  The older
invocations (``--ollama``, ``--structured-output``, ``--tag``, ``--temperature``) still work.
"""

from __future__ import annotations

import argparse
import sys

from teachers_teammate.domain.context_budget import CONTEXT_STEPS, PROOFREADING_NUM_CTX_CAP
from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG, OllamaModelSpec
from tools.model_eval import defaults
from tools.model_eval.check_rule import CheckRule
from tools.model_eval.download_sizes import check_size, report_real_size
from tools.model_eval.ports import OllamaChatPort
from tools.model_eval.structured_check import (
    StructuredOutputCheck,
    report_lines,
    seed_settings,
)

SUBCOMMANDS = ("sizes", "capabilities", "structured-output")

_WRITING_CATEGORY = "general"
"""Catalog category of the models the app offers for writing."""


def _default_window(window: int | None) -> int:
    """Return the context window to check at.

    Without ``--num-ctx`` the check runs at the largest window the app can send for proofreading:
    a passing verdict has to hold at the window a teacher's run really gets, not only at the
    smallest one.
    """
    return PROOFREADING_NUM_CTX_CAP if window is None else window


def _writing_models(tags: list[str]) -> list[OllamaModelSpec]:
    """Return the catalog's writing models, or only those named by *tags*."""
    specs = [spec for spec in OLLAMA_CATALOG if spec.category == _WRITING_CATEGORY]
    if not tags:
        return specs
    return [spec for spec in specs if spec.tag in tags]


def _sizes(tags: list[str]) -> int:
    """Compare the catalog's download estimates with the registry and return the exit code."""
    specs = [spec for spec in OLLAMA_CATALOG if not tags or spec.tag in tags]
    failures = 0
    for spec in specs:
        check = check_size(spec)
        print(check.report())
        if not check.matches:
            failures += 1
    for tag in tags:
        if all(spec.tag != tag for spec in OLLAMA_CATALOG):
            print(report_real_size(tag))
    print(f"\n{len(specs) - failures}/{len(specs)} tags verified")
    return 1 if failures else 0


def _capabilities(tags: list[str], url: str) -> int:
    """Print each model's capabilities and whether it reads images; return the exit code."""
    port = OllamaChatPort(url)
    catalog = {spec.tag for spec in OLLAMA_CATALOG}
    failures = 0
    for tag in [spec.tag for spec in OLLAMA_CATALOG if not tags or spec.tag in tags] + [
        tag for tag in tags if tag not in catalog
    ]:
        capabilities = port.client.model_capabilities(tag)
        if capabilities is None:
            print(f"{tag}: capabilities could not be read (model not pulled?)")
            failures += 1
            continue
        vision = "vision" in capabilities
        print(
            f"{tag}: capabilities={sorted(capabilities)} → {'READS images' if vision else 'text only'}"
        )
    return 1 if failures else 0


def _structured_output(
    tags: list[str],
    url: str,
    temperature: float,
    timeout: float,
    window: int,
) -> int:
    """Check the review answer format and return the exit code.

    With ``--tag`` the models are candidates that are not in the catalog yet: a candidate that
    fails is reported but does not count towards the catalog tally, because no catalog entry has
    gone wrong.
    """
    specs = _writing_models(tags)
    print(f"── structured output at num_ctx {window}, temperature {temperature:g} ──")
    port = OllamaChatPort(url, num_ctx=window)
    check = StructuredOutputCheck(port, rule=CheckRule(), timeout=timeout)
    if tags and not specs:
        return _candidates(tags, check, temperature, window)
    failures = 0
    for spec in specs:
        print(f"{spec.tag}:")
        print(f"  {seed_settings(port, spec.tag)}")
        runs = check.run(spec.tag, temperature, on_run=_print_run(check.rule))
        passed = check.passes(runs)
        print(f"  → catalog: structured_output={passed} (currently {spec.structured_output})")
        if passed != spec.structured_output:
            failures += 1
    print(f"\n{len(specs) - failures}/{len(specs)} writing models verified")
    return 1 if failures else 0


def _candidates(
    tags: list[str], check: StructuredOutputCheck, temperature: float, window: int
) -> int:
    """Check candidate *tags* and return the exit code."""
    passed: list[str] = []
    for tag in tags:
        print(f"{tag}:")
        runs = check.run(tag, temperature, on_run=_print_run(check.rule))
        if check.passes(runs):
            passed.append(tag)
    print(f"\n{len(passed)}/{len(tags)} candidates pass the rule at num_ctx {window}")
    for tag in tags:
        print(f"  {tag}: {'PASS' if tag in passed else 'FAIL'}")
    return 0 if len(passed) == len(tags) else 1


def _print_run(rule: CheckRule):
    """Return the per-run callback: print the report line of each run as it finishes."""

    def report(run, number: int, runs: int) -> None:
        line = report_lines([run], rule)[0]
        suite = f" (run {number}/{runs})" if runs > 1 else ""
        print(f"{line}{suite}", flush=True)

    return report


def _normalise(argv: list[str]) -> list[str]:
    """Map the older flag-only invocation onto a subcommand.

    A bare call stays the registry check it used to be, ``--tag`` used to imply the
    structured-output check, and ``--ollama`` was what enabled the capability check.  The
    help option has to reach the top-level parser, so it is passed through.
    """
    if argv and argv[0] in SUBCOMMANDS:
        return argv
    if not argv or argv[0] in ("-h", "--help"):
        return argv
    if "--structured-output" in argv:
        return ["structured-output", *[item for item in argv if item != "--structured-output"]]
    if "--ollama" in argv:
        return ["capabilities", *[item for item in argv if item != "--ollama"]]
    if "--tag" in argv:
        return ["structured-output", *argv]
    return ["sizes", *argv]


def main(argv: list[str] | None = None) -> int:
    """Run the subcommand named on the command line and return the exit code."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="TAG",
        help="Only this model (repeatable).  Use it to check a candidate before adding it to "
        "the catalog.",
    )

    sizes = sub.add_parser("sizes", parents=[shared], help="compare the download estimates")
    sizes.set_defaults(handler=lambda args: _sizes(args.tag))

    capabilities = sub.add_parser(
        "capabilities", parents=[shared], help="ask a local server what each model can do"
    )
    capabilities.add_argument(
        "--ollama-url",
        default=defaults.default_ollama_url(),
        help="Ollama server (default: $OLLAMA_HOST, else http://127.0.0.1:11434).  From WSL in "
        "its default NAT mode the server on Windows is the host's address, not 127.0.0.1.",
    )
    capabilities.set_defaults(handler=lambda args: _capabilities(args.tag, args.ollama_url))

    structured = sub.add_parser(
        "structured-output",
        parents=[shared],
        help="check that a model answers the review answer format reliably",
    )
    structured.add_argument(
        "--ollama-url",
        default=defaults.default_ollama_url(),
        help="Ollama server (default: $OLLAMA_HOST, else http://127.0.0.1:11434).",
    )
    structured.add_argument(
        "--temperature",
        type=float,
        default=defaults.default_temperature(),
        metavar="X",
        help="Check at temperature X (default: the app's proofreading temperature).",
    )
    structured.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="Time allowed for one request (default %(default)g).  Raise it for a model that "
        "does not fit the graphics card and runs partly on the processor.",
    )
    structured.add_argument(
        "--num-ctx",
        type=int,
        choices=CONTEXT_STEPS,
        default=None,
        metavar="TOKENS",
        help=f"Check at this context window, one of {CONTEXT_STEPS} (default: "
        f"{PROOFREADING_NUM_CTX_CAP}, the largest window the app sends for proofreading).",
    )
    structured.set_defaults(
        handler=lambda args: _structured_output(
            args.tag,
            args.ollama_url,
            args.temperature,
            args.timeout,
            _default_window(args.num_ctx),
        )
    )

    args = parser.parse_args(_normalise(sys.argv[1:] if argv is None else argv))
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
