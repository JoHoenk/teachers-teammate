# The evaluation store

Layout and rules of the store the evaluation tools read and write. The fields of every file are
documented in the typed records of `tools/evaluation/` (`core/records.py`, `core/manifest.py`,
`core/machines.py`, `checks/model_fit.py`). A reader refuses a file whose `format` it does not know.

Everything is JSON with sorted keys, so `git diff` shows what changed. Commands such as
`store validate` are the tools of [TOOLS.md](TOOLS.md).

## Requirements

| A record must allow | How |
| --- | --- |
| **Checking** a run later: same texts, request, weights, machine | the header holds the question hash, key hash, protocol fingerprint, instructions hash, model digest, seed and machine |
| **Re-scoring** with a new metric | a run file holds the model's output and the app's review as returned, no score |
| **Reading without the corpora** | the store holds the texts beside the runs |
| **Privacy** | the store is the private repository `teachers-teammate-evals`; the tools refuse a store inside this repository |
| **Evidence** | a run file is written once and never merged; git is the history |

Level 1 reading needs the run file and the store's texts. Level 2 needs the corpora: the texts are
rebuilt from the pointers, so every score can be recomputed from the source.

## Layout

```
<store>/testsets/<id>/<language>.jsonl            one record per text
<store>/testsets/<id>/manifest.json               pins the set without holding its text
<store>/testsets/<id>/accepted.jsonl              accepted alternatives of the key (optional)
<store>/runs/[<folder>/]<name>.run.jsonl          header, sessions, one answer per text
<store>/marks/<marker>__<question_sha256>.jsonl   one marker's verdicts on one set
<store>/machines/<id>.json                        one machine
<store>/model-fit/sizes.jsonl                     one size record per model, machine, device, window
<store>/gates/<record>.md                         a gate's verdict and the runs it rests on
<store>/cache/                                    score cache, disposable, git-ignored
```

`<folder>` records where a converted run came from; nothing reads it.

## Versions

| Name | Says | Where | Changed by |
| --- | --- | --- | --- |
| `format` | the layout of a JSON file | every file | whoever changes the format |
| `eval_version` | which requests a run measured: prompts, answer format, window | `core/protocol.json`, looked up by the header's `protocol.fingerprint` | by hand; `evaluate run` refuses an unknown fingerprint |
| `scorer_version` | how the numbers are computed | `SCORER_VERSION` in `report/score.py` | by hand; the golden-number tests fail until it is raised |
| `tool_revision` | the tools' commit | every session line | git; information only |

`core/protocol.json` lists each protocol version with its date, accepted fingerprints and reason.
Entries are never edited or removed. A change that only adds a request (a setup, a profile variant)
keeps every earlier request hash; a change of scoring raises `scorer_version`, not the protocol.

## Keys

Built from a header by `core/axes.py`; every axis is declared once in `core/axis_table.py`.

| Key | Contents | Job |
| --- | --- | --- |
| **compare** | task, test set, every protocol axis except the fingerprint; axes at their default are left out | pooling: runs of one configuration are compared together; seeds, repeats and machines pool |
| **series** | compare + `machine`, `isolated` | one gate group: the repeats of one setup on one machine |
| **run** | series + `repeat` | one file; names the score cache and the spot-check sample |
| **environment** | compare + `machine`, `ollama_version`, `isolated` | speed |

Runs are grouped by the compare key, never by commit or `eval_version`. An axis this reader does not
know is kept (`unknown.<name>`) and is part of the key, so runs that differ in it never pool.

## Test sets

- **Question** (`id`, `language`, `source`): what is sent. `question_sha256` hashes the texts'
  hashes in order; a run records it.
- **Key** (`kind`, `reference`, `sentences`, `labels`, `sentence_spans`, `unchanged_sentences`,
  `accepted`): read only when scoring, so a better key re-scores the same runs. Raise `key_version`
  by hand when it changes; the same version with another `key_sha256` is refused.
- **Provenance** (`origin`, `band`, `corpus`, `pointer`).

Rules:

- **A manifest is immutable.** Runs pin it by hash; another selection is another test-set id. It
  holds no corpus text.
- **Pointers** are in the corpus's own terms (`input/corpora/`); `split` only when not the default.
- **Reference replacements are stored as hashes** (position, label, word count, hash): the words
  are corpus text and come back from the corpus at level 2.
- **`accepted.jsonl`** comes from `store accept-marks`: the named marker's `valid` edits that no
  other marker called `wrong`. Disagreements are listed, never resolved. The file is merged per
  marker. It is part of the key: adding to it moves `key_sha256`, not a run.

## Runs

One JSON object per line: the header, one line per session (start or resume), one line per answer.
A resume compares the stored header field by field, except the fingerprint: fingerprints of one
`eval_version` are one protocol.

**File name**, a label derived from the axes and never parsed:

```
<date>_<host>_<model-slug>_<digest8>__<tag>__…__<key8>.run.jsonl
```

`host` is the machine id or the provider name; `model-slug` is the tag in lower case without
`hf.co/`, with `/` and `:` as `-`; each `tag` is an axis away from its default, then always
`temperature-<t>` and `repeat-<n>`; `key8` is the compare-key hash. `store convert` refuses an
existing name; `--label` separates two runs of one configuration.

**`steps`** is what the exchange's answer holds: `one-request-review` all three, `one-request-text`
only `correct`, `text-first` all three unless the unclear check was off. No `explain` means no
review.

**`instructions`** is the SHA-256 of each language's policy and answer format as sent, so a run with
a since-changed prompt is recognisable.

**`step_one_answers`** on an answer line holds the model's own step-① answers, every try, when the
text failed, fell back or lost text; `output` is what the teacher got, so this is where a failure is
read. Absent on every other line.

## Marks

Written by `store marks SHEET.md --marker <name>`. The file holds no text (the sheet does, and is not
committed). One file per marker and set; a mark for another set is refused. Merged, sorted by
identity, so two writers produce the same bytes.

- **Identity:** `(kind, text, span, replacement, explanation_sha256)`; an `edit` mark omits the
  explanation hash. Spans are in characters, so a mark survives a tokenizer change; a moved hunk
  boundary is listed, never dropped.
- **Verdicts:** `wrong` (on an `edit`: refuses the edit; on an `explanation`: does not explain it),
  `correct`, `vague` (true but too general), `valid` (an `edit` the annotators missed but a person
  accepts; `accept-marks` turns it into key data). Only `correct` counts as correct in the gate's explanation line.
- **Sample:** from the repeat-1 run, per deciding language, explanations of edits in `page`-band
  texts, at most two per text, seeded by the run key, so every machine and a renamed file draw the
  same sample.

## Machines and sizes

A run or size record names a machine by its id in `machines/`; an unknown id is refused. A machine
is measured per `device` (card or processor). `model-fit/sizes.jsonl` is append-only and holds raw
numbers only.

## Gate records

`gates/<record>.md`: the verdict (passed, dropped and why), the run files it rests on, and the
`bazel run //tools:report -- …` line that prints the figures. No figure. Written only once its runs
are in the store.

## Validation

`store validate --store <store>` checks the store; `validate <run file>` single files. Rules:
`tools/evaluation/store/validate.py`. With the corpora named (`--corpus NAME=PATH` or
`--corpora FILE`) it also checks the corpus file hashes and rebuilds every item. Nothing repairs a
file; problems are listed and the command decides whether to refuse.

## Reading back and the score cache

`read_run_file()` (`core/records.py`) returns header, sessions and answers as stored. `load()`
(`report/load.py`) pairs each answer with its record and scores it; an unusable file or answer is
skipped with a reason, so a report says "23 / 31 texts".

`report --cache <dir>` keeps scores in `<dir>/<run key hash>.scores.jsonl`. A line is reused only
when its `answer_sha256`, `key_part_sha256` and `scorer_version` are current.
