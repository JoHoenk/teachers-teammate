"""Build the test set for ``tools/compare_writing_models.py`` from public learner corpora.

Developer tool, standard library only, not run in CI.  The corpora are *not* part of the
repository (licences, and real learner text): download them, point this script at them and keep
its output outside the repository too.

Sources (write them into the results you publish):

* German: Falko-MERLIN GEC corpus (Boyd 2018), test split.  Falko CC BY 3.0, MERLIN CC BY-SA 4.0.
  ``https://github.com/adrianeboyd/boyd-wnut2018/releases/download/wnut2018/data.tar.gz``
* English: W&I+LOCNESS v2.1 (BEA-2019 shared task), dev split.  Non-commercial use only; credit
  CECL (Université catholique de Louvain) and the University of Cambridge.
  ``https://www.cl.cam.ac.uk/research/nl/bea2019st/data/wi+locness_v2.1.bea19.tar.gz``
* French: no public learner corpus with corrections is available, so ``gec_fr_invented.jsonl``
  next to this script holds eight invented texts and ten correct sentences, written by the
  tool's author (an AI model, *not* a teacher): treat French results as indicative only.

Output, one file per language (``de.jsonl``, ``en.jsonl``, ``fr.jsonl``), one ``TestRecord`` per
line (``tools.model_eval.results``, ``schema_version: 1``), so ``tools/compare_writing_models.py``
reads it as it reads any other file.  ``kind`` is ``"errors"`` (``source`` has errors,
``reference`` is the corrected text) or ``"correct"`` (``sentences`` are already correct and
``source`` is their join).

Usage::

    python3 -I tools/prepare_gec_testset.py --falko-merlin DIR/data --wi-locness DIR/wi+locness \\
        --out ~/gec-testset/set
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
import re

from tools.model_eval.results import TestRecord, write_records

_SEED = 20261107
_ERROR_TEXTS = 8
_CORRECT_SENTENCES = 10


def _detokenize(text: str) -> str:
    """Undo the space-separated tokenisation of the Falko-MERLIN files."""
    text = re.sub(r"\s+([.,;:!?%)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _words(text: str) -> int:
    return len(text.split())


# ── German: Falko-MERLIN ──────────────────────────────────────────────────────


def _german_groups(directory: Path) -> dict[str, list[tuple[str, str, str]]]:
    """Return ``{origin: [(text id, source, reference) per sentence group]}`` of the test split."""
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for origin in ("falko", "merlin"):
        path = directory / "source" / f"{origin}-id-ctok-zh1.txt.test"
        rows: list[tuple[str, str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                rows.append((parts[0], _detokenize(parts[1]), _detokenize(parts[2])))
        groups[origin] = rows
    return groups


def _german(directory: Path, rng: random.Random) -> list[dict]:
    texts: dict[str, list[tuple[str, str]]] = {}
    for origin, rows in _german_groups(directory).items():
        by_id: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for text_id, source, reference in rows:
            by_id[f"{origin}:{text_id}"].append((source, reference))
        texts.update(by_id)

    # MERLIN texts are short letters and used whole; Falko texts are long essays (10 to 73
    # sentences), so an excerpt of consecutive sentences stands in for the text.
    excerpts: dict[str, list[tuple[str, str]]] = {}
    for key, pairs in texts.items():
        windows = [
            pairs[start : start + size]
            for size in (4, 5, 6, 7, 8, 9)
            for start in range(0, len(pairs) - size + 1)
            if len(pairs) > 9 or (start == 0 and size == len(pairs))
        ]
        windows = [
            w
            for w in windows
            if 50 <= _words(" ".join(a for a, _ in w)) <= 130 and sum(a != b for a, b in w) >= 3
        ]
        if windows:
            excerpts[key] = rng.choice(windows)
    keys = list(excerpts)
    rng.shuffle(keys)
    per_origin = {"falko": [], "merlin": []}
    for key in keys:
        per_origin[key.split(":", 1)[0]].append(key)
    chosen = per_origin["merlin"][: _ERROR_TEXTS // 2] + per_origin["falko"][: _ERROR_TEXTS // 2]

    records = []
    for key in chosen:
        pairs = excerpts[key]
        records.append(
            {
                "id": f"de-{len(records) + 1:02d}",
                "kind": "errors",
                "origin": f"Falko-MERLIN test, {key}"
                + (" (excerpt)" if len(pairs) < len(texts[key]) else ""),
                "source": " ".join(s for s, _ in pairs),
                "reference": " ".join(r for _, r in pairs),
            }
        )

    # Sentences the annotators left alone, one per text.
    sentences = []
    keys = sorted(texts, key=lambda k: (k.startswith("merlin"), rng.random()))  # Falko first
    for key in keys:
        if key in chosen:
            continue
        ok = [
            s
            for s, r in texts[key]
            if s == r
            and 8 <= _words(s) <= 22
            and s[0].isupper()
            and s[-1] in ".?!"
            and not re.search(r"\d|\bX\b|[A-Z]{3}|[„“\"]", s)
        ]
        if ok:
            sentences.append(rng.choice(ok))
        if len(sentences) == _CORRECT_SENTENCES:
            break
    records.append(
        _correct_record("de-correct", "Falko-MERLIN test (unchanged sentences)", sentences)
    )
    return records


# ── English: W&I+LOCNESS ──────────────────────────────────────────────────────


def _apply_edits(text: str, edits: list) -> str:
    """Apply the first annotator's ``[start, end, replacement]`` edits (None = no correction).

    Sorted by span only: the tail is applied first, so the earlier spans keep their offsets.  The
    replacement cannot be part of the key, because it may be ``None``.
    """
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if replacement is not None:
            text = text[:start] + replacement + text[end:]
    return text


def _english(directory: Path, rng: random.Random) -> list[dict]:
    records = []
    quota = {"A": 2, "B": 3, "C": 3}
    for level, count in quota.items():
        rows = [
            json.loads(line)
            for line in (directory / "json" / f"{level}.dev.json").open(encoding="utf-8")
        ]
        candidates = [
            row
            for row in rows
            if 60 <= _words(row["text"]) <= 160
            and row["edits"]
            and len([e for e in row["edits"][0][1] if e[2] is not None]) >= 3
        ]
        rng.shuffle(candidates)
        for row in candidates[:count]:
            records.append(
                {
                    "id": f"en-{len(records) + 1:02d}",
                    "kind": "errors",
                    "origin": f"W&I+LOCNESS dev, level {level} ({row['cefr']}), id {row['id']}",
                    "source": row["text"],
                    "reference": re.sub(
                        r"[ \t]{2,}", " ", _apply_edits(row["text"], row["edits"][0][1])
                    ),
                }
            )

    # Native-speaker essays: sentences without any annotator edit.
    sentences = []
    rows = [json.loads(line) for line in (directory / "json" / "N.dev.json").open(encoding="utf-8")]
    rng.shuffle(rows)
    for row in rows:
        spans = [(e[0], e[1]) for ann in row["edits"] for e in ann[1]]
        text = row["text"]
        ok = []
        for match in re.finditer(
            r"(?:^|(?<=[.!?]\s)|(?<=\n))[A-Z][a-z][^.!?\n\"()]{35,170}[.!?](?=\s|$)", text
        ):
            if not any(s < match.end() and match.start() < e for s, e in spans):
                ok.append(match.group(0))
        if ok:
            sentences.append(rng.choice(ok))
        if len(sentences) == _CORRECT_SENTENCES:
            break
    records.append(
        _correct_record("en-correct", "W&I+LOCNESS dev, N (unedited sentences)", sentences)
    )
    return records


def _correct_record(record_id: str, origin: str, sentences: list[str]) -> dict:
    return {
        "id": record_id,
        "kind": "correct",
        "origin": origin,
        "sentences": sentences,
        "source": " ".join(sentences),
        "reference": " ".join(sentences),
    }


def _french(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["kind"] == "correct":
            record = _correct_record(record["id"], record["origin"], record["sentences"])
        records.append(record)
    return records


def _as_record(language: str, record: dict) -> TestRecord:
    """Return *record* (as the readers of the corpora build it) as a typed test record."""
    return TestRecord(
        id=record["id"],
        language=language,
        kind=record["kind"],
        origin=record["origin"],
        source=record["source"],
        reference=record.get("reference", ""),
        sentences=tuple(record.get("sentences", ())),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--falko-merlin", type=Path, required=True, help="the extracted data/ dir")
    parser.add_argument("--wi-locness", type=Path, required=True, help="the extracted wi+locness/")
    parser.add_argument(
        "--french",
        type=Path,
        default=Path(__file__).with_name("gec_fr_invented.jsonl"),
        help="the invented French set (default: next to this script)",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="output directory (outside the repo)"
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    sets = {
        "de": _german(args.falko_merlin, random.Random(_SEED)),
        "en": _english(args.wi_locness, random.Random(_SEED)),
        "fr": _french(args.french),
    }
    for language, records in sets.items():
        path = args.out / f"{language}.jsonl"
        write_records(path, [_as_record(language, record) for record in records])
        errors = sum(1 for r in records if r["kind"] == "errors")
        words = sum(_words(r["source"]) for r in records if r["kind"] == "errors")
        print(
            f"{path}: {errors} texts with errors ({words} words), "
            f"{len(records) - errors} group of correct sentences"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
