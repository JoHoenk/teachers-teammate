"""Developer tool: what a reading model costs on one page of handwriting.

NOT run in CI - it needs a running Ollama server and the developer's own benchmark pages.

    bazel run //tools:ocr_page_tokens -- --pages ~/handwriting

Every catalogued reading model is sent the app's own OCR request - the same prompt, the same
preprocessing, the same options - once per page, after a warm-up request so the model's load time
is not counted.  The report prints, per model and page, the tokens of the image, the tokens of the
answer, the words the model read, and the two rates.  The figures the catalog needs are the answer
tokens per page (``answer_length``) and the words per page (``TYPICAL_PAGE_WORDS``); the last lines
of the report are those, ready to paste.

``--pages`` takes image files or a directory of them.  The pages are preprocessed exactly as a run
preprocesses them, because the image the model sees decides the image token count.

``--models`` defaults to every catalogued reading model; a tag the catalog does not know is
measured too, so a candidate can be compared before it is entered.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
import sys
import tempfile

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG
from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.ocr_processor import OCR_ANSWER_TOKENS, get_ocr_prompt
from teachers_teammate.infrastructure.ollama_utils import ChatStats, OllamaClient
from teachers_teammate.infrastructure.performance_probe import (
    WARMUP_ANSWER_TOKENS,
    WARMUP_PROMPT,
)
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.preprocessing.recipes import steps_for
from teachers_teammate.infrastructure.preprocessing.steps import StepPipeline
from tools.model_eval.performance import Spread

_NS = 1e9
"""Nanoseconds per second: Ollama reports its durations in nanoseconds."""

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"})

DEFAULT_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class Reading:
    """One page read by one model, as Ollama and the clock saw it."""

    model: str
    page: str
    image_tokens: int
    """Tokens of the request: the image, plus the few words of the prompt."""
    answer_tokens: int
    words: int
    """Words the model wrote: what the page carries, as the model read it."""
    read_tps: float
    """Request tokens per second: how fast the model takes a page in."""
    write_tps: float
    """Answer tokens per second: how fast it writes the text out."""
    done_reason: str = ""

    @property
    def truncated(self) -> bool:
        """Whether the answer stopped at the token cap instead of at the end of the page."""
        return self.done_reason == "length"


def _rate(tokens: int, duration_ns: int) -> float:
    """Return tokens per second of *tokens* over *duration_ns* nanoseconds (0 when unknown)."""
    return tokens / (duration_ns / _NS) if duration_ns > 0 else 0.0


def _pages(paths: Sequence[Path]) -> list[Path]:
    """Return the image files among *paths*, a directory contributing its own images."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES))
        elif path.suffix.lower() in _IMAGE_SUFFIXES:
            found.append(path)
    return found


def _prepared(page: Path, tmp_dir: Path, ocr: OcrConfig) -> Path:
    """Return *page* preprocessed the way a run preprocesses it."""
    preprocessor = StepPipeline(steps_for(ocr, ModelStore()), tmp_dir=tmp_dir)
    processed, _steps = preprocessor.preprocess(page)
    return processed


def _warm_up(client: OllamaClient, model: str) -> None:
    """Load *model* with a one-token request, so the page's timing carries no load."""
    try:
        client.chat_with_stats(model, WARMUP_PROMPT, options={"num_predict": WARMUP_ANSWER_TOKENS})
    except OCRError as error:
        print(f"{model}: the warm-up failed: {error}", file=sys.stderr)


def _read(
    client: OllamaClient, model: str, page: Path, image: Path, language: str
) -> Reading | None:
    """Send the app's OCR request for *image* and return what it cost, or ``None`` on a failure."""
    encoded = base64.b64encode(image.read_bytes()).decode("utf-8")
    try:
        text, stats = client.chat_with_stats(
            model,
            get_ocr_prompt(language),
            images=[encoded],
            options={"temperature": OcrConfig().temperature, "num_predict": OCR_ANSWER_TOKENS},
        )
    except OCRError as error:
        print(f"{model} on {page.name}: {error}", file=sys.stderr)
        return None
    return _reading(model, page.name, text, stats)


def _reading(model: str, page: str, text: str, stats: ChatStats) -> Reading:
    """Build the record of one page from the answer and Ollama's statistics."""
    return Reading(
        model=model,
        page=page,
        image_tokens=stats.prompt_eval_count,
        answer_tokens=stats.eval_count,
        words=len(text.split()),
        read_tps=_rate(stats.prompt_eval_count, stats.prompt_eval_duration),
        write_tps=_rate(stats.eval_count, stats.eval_duration),
        done_reason=stats.done_reason,
    )


def _spread(values: Sequence[float]) -> tuple[int, int, int]:
    """Return the 10th percentile, median and 90th percentile of *values*, as whole numbers."""
    spread = Spread.of(values)
    return (0, 0, 0) if spread is None else spread.rounded()


def _report(readings: Sequence[Reading]) -> str:
    """Return the per-page table and, per model, the figures the catalog wants."""
    if not readings:
        return "No page was read."
    lines = [
        "| Model | Page | Image tokens | Answer tokens | Words | Read tokens/s "
        "| Write tokens/s | Stopped at |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in readings:
        stopped = "the token cap" if item.truncated else "the end of the page"
        lines.append(
            f"| `{item.model}` | {item.page} | {item.image_tokens} | {item.answer_tokens} "
            f"| {item.words} | {item.read_tps:.0f} | {item.write_tps:.0f} | {stopped} |"
        )
    lines += ["", "Per model, over the pages it read:", ""]
    for model in dict.fromkeys(item.model for item in readings):
        mine = [item for item in readings if item.model == model]
        low, typical, high = _spread([item.answer_tokens for item in mine])
        words_low, words_typical, words_high = _spread([item.words for item in mine])
        lines.append(
            f"- `{model}`: answer tokens per page {low} / {typical} / {high}, "
            f"words per page {words_low} / {words_typical} / {words_high}, "
            f"read {median(item.read_tps for item in mine):.0f} tokens/s, "
            f"write {median(item.write_tps for item in mine):.0f} tokens/s "
            f"({len(mine)} pages)"
        )
    return "\n".join(lines)


def _catalog_lines(readings: Sequence[Reading]) -> str:
    """Return the catalog entries to paste: the answer length and the page's word count."""
    if not readings:
        return ""
    lines = ["", "For the catalog, from the pages above:", ""]
    for model in dict.fromkeys(item.model for item in readings):
        mine = [item for item in readings if item.model == model]
        low, typical, high = _spread([item.answer_tokens for item in mine])
        lines.append(
            f"`{model}`: answer_length=AnswerLength(low={low}, typical={typical}, high={high})"
        )
    words = _spread([item.words for item in readings])[1]
    lines += [
        "",
        f"TYPICAL_PAGE_WORDS = {words}",
        "",
        "Answer tokens per page, not per 100 words: a reading model answers with the page's text.  "
        "A page whose answer stopped at the token cap is a lower bound, not a measurement.",
    ]
    return "\n".join(lines)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pages",
        nargs="+",
        required=True,
        type=Path,
        help="the benchmark pages: image files, or directories of them",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="the models to measure (default: every catalogued reading model)",
    )
    parser.add_argument(
        "--url", default=DEFAULT_URL, help="the Ollama server (default: %(default)s)"
    )
    parser.add_argument("--language", default="English", help="the language of the pages")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Measure every model on every page and print the report; return the exit code."""
    args = _parse(argv)
    pages = _pages(args.pages)
    if not pages:
        print(f"no image below {', '.join(str(path) for path in args.pages)}", file=sys.stderr)
        return 1
    models = args.models or [spec.tag for spec in OLLAMA_CATALOG if spec.category == "ocr"]
    ocr = OcrConfig()
    client = OllamaClient(args.url)
    readings: list[Reading] = []
    with tempfile.TemporaryDirectory(prefix="ocr-page-tokens-") as tmp:
        tmp_dir = Path(tmp)
        prepared = [(page, _prepared(page, tmp_dir, ocr)) for page in pages]
        for model in models:
            _warm_up(client, model)
            for page, image in prepared:
                if (reading := _read(client, model, page, image, args.language)) is not None:
                    readings.append(reading)
    print(_report(readings))
    print(_catalog_lines(readings))
    return 0 if readings else 1


if __name__ == "__main__":
    sys.exit(main())
