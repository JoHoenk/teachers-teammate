"""Compare writing models on learner texts (gate 2 of the writing-model plan).

Developer tool, needs a running Ollama server, not run in CI.  Every text goes through the app's
own corrector (review mode), so the request is the one a teacher's run sends; the comparison uses
the app's tokeniser and word diff.  The test set comes from ``tools/prepare_gec_testset.py`` and
stays outside the repository (real learner text, licences).

Per language and model it reports

* edit precision / recall / F0.5: the word-diff changes source → model against source →
  reference (a change counts when it covers the same words and has the same replacement);
* how many already-correct sentences came back unchanged;
* how often the review failed or fell back to corrections only;
* the changes that carry an explanation, notes that explain no change (orphans), and seconds.

Run (results are appended, so an interrupted run resumes)::

    bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set \\
        --out ~/gec-testset/results --model qwen3.5:9b --model gemma4:e4b --temperature 0.2
    bazel run //tools:compare_writing_models -- report --out ~/gec-testset/results
    bazel run //tools:compare_writing_models -- sheet --out ~/gec-testset/results \\
        --model qwen3.5:9b --model gemma4:e4b --per-language 10 > spotcheck.md

``sheet`` writes the teacher's spot-check list: explanations to mark correct / wrong / vague.
The work itself lives in :mod:`tools.model_eval`; this file only parses arguments and prints.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from tools.model_eval import defaults, results
from tools.model_eval import report as reporting
from tools.model_eval.comparison import LANGUAGE_NAMES, WritingModelComparison
from tools.model_eval.results import TestRecord


def _corrector_factory(url: str, timeout: float):
    """Return a factory building the app's own Ollama corrector."""
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        NativeOllamaCorrector,
    )
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    client = OllamaClient(url)

    def build(model: str, temperature: float):
        return NativeOllamaCorrector(model, client, timeout=timeout, temperature=temperature)

    return build


def _read_testset(testset: Path, languages: list[str]) -> list[TestRecord]:
    """Return the test records of *languages*, in file order."""
    records: list[TestRecord] = []
    for language in languages:
        records.extend(results.read_records(testset / f"{language}.jsonl"))
    return records


def _run(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    comparison = WritingModelComparison(_corrector_factory(args.ollama_url, args.timeout))
    records = _read_testset(args.testset, args.language)
    for model in args.model:
        for temperature in args.temperature:
            path = args.out / f"{model.replace('/', '_').replace(':', '_')}__T{temperature:g}.jsonl"
            print(f"== {model} @ {temperature:g}", flush=True)
            rows = comparison.run(
                records,
                model=model,
                temperature=temperature,
                done=results.read_done_ids(path),
            )
            for row in rows:
                results.write_rows(path, [row])
                print(f"  {row.id:<12} {row.seconds:5.0f}s  {_detail(row)}", flush=True)
    return 0


def _detail(row) -> str:
    """Return the one-line detail of a row: its score or its unchanged sentences."""
    if row.kind == "errors":
        detail = f"tp/fp/fn {row.true_positives}/{row.false_positives}/{row.false_negatives}"
    else:
        detail = f"unchanged {row.unchanged}/{len(row.sentences)}"
    if row.failed:
        detail += "  FAILED"
    if row.fallback:
        detail += "  FALLBACK"
    return detail


def _report(args: argparse.Namespace) -> int:
    rows = [
        results.rescore(row, count_marks=args.count_marks) for row in results.read_all(args.out)
    ]
    print(reporting.table(reporting.summarise(rows)))
    return 0


def _sheet(args: argparse.Namespace) -> int:
    print(
        reporting.spot_check_sheet(
            results.read_all(args.out),
            models=args.model,
            temperature=args.temperature,
            per_language=args.per_language,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="send every test text to the models")
    run.add_argument("--testset", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--model", action="append", required=True)
    run.add_argument("--temperature", type=float, action="append", default=None)
    run.add_argument("--language", action="append", choices=list(LANGUAGE_NAMES), default=None)
    run.add_argument("--timeout", type=float, default=900.0)
    run.add_argument("--ollama-url", default=defaults.default_ollama_url())

    report = sub.add_parser("report", help="print the results table")
    report.add_argument("--out", type=Path, required=True)
    report.add_argument(
        "--count-marks",
        action="store_true",
        help="count Markdown marks a model added (such as **bold**) as errors; by default they "
        "are ignored",
    )

    sheet = sub.add_parser("sheet", help="print the teacher's spot-check list")
    sheet.add_argument("--out", type=Path, required=True)
    sheet.add_argument("--model", action="append", required=True)
    sheet.add_argument("--temperature", type=float, default=None)
    sheet.add_argument("--per-language", type=int, default=10)

    args = parser.parse_args()
    if args.command == "run":
        args.temperature = args.temperature or [defaults.default_temperature()]
        args.language = args.language or list(LANGUAGE_NAMES)
        return _run(args)
    if args.command == "report":
        return _report(args)
    args.temperature = (
        defaults.default_temperature() if args.temperature is None else args.temperature
    )
    return _sheet(args)


if __name__ == "__main__":
    sys.exit(main())
