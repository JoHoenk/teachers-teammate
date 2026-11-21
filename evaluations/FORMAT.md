# The evaluation store

The layout of the store the evaluation tools read and write, and the rules that keep its records
checkable. The fields of every file are documented where they are defined, in the typed records of
`tools/evaluation/` (`core/records.py`, `core/manifest.py`, `core/machines.py`,
`checks/model_fit.py`); a reader refuses a file whose `format` it does not know rather than guessing.
How the records are used is in `README.md`.

Everything is plain JSON with sorted keys, so `git diff` shows what changed and a run is one file.

## What a record has to allow

| Requirement | How the store meets it |
| --- | --- |
| A committed run can be **checked** later: same texts, same request, same weights, same machine | the question hash, the key hash, the protocol fingerprint, the instructions hash, the model digest, the seed and the machine are in the header |
| A run can be **re-scored** with a metric that did not exist when it ran | a run file holds the model's output and the app's own review as returned, and no score |
| A run can be **read without the corpora** | the store holds the texts beside the runs |
| The record lives in a **private repository** | the texts come from corpora that may not be passed to a third party; this repository holds the code and the formats, never a record |
| Runs are **evidence** | a run file is written once and never merged; git is the history |

Reading a run is possible at two levels. **Level 1** needs the file and the store's texts: the answer
is scored from what the file holds. **Level 2** needs the corpora: the texts are rebuilt from the
pointers and the stored edits are applied, so the scoring can be recomputed from the source.

## Layout

```
<store>/testsets/<id>/<language>.jsonl            one record per text, per language
<store>/testsets/<id>/manifest.json               what pins the set without holding its text
<store>/testsets/<id>/accepted.jsonl              the accepted alternatives of the key (optional)
<store>/runs/[<folder>/]<name>.run.jsonl          one run file: header, sessions, one answer per text
<store>/marks/<marker>__<question_sha256>.jsonl   one marker's verdicts on one set
<store>/machines/<id>.json                        one machine, named by its id
<store>/model-fit/sizes.jsonl                     one size record per model, machine, device, window
<store>/gates/<record>.md                         a gate's verdict and the runs it rests on
<store>/cache/                                    the score cache, disposable and git-ignored
```

The store is the private repository `teachers-teammate-evals`. The tools refuse a store inside this
repository's working tree. `<folder>` is provenance only (where a converted run came from); nothing
reads it back.

## Four versions, four keys

Only one of these versions groups runs:

| Name | Says | Where | Changed by |
| --- | --- | --- | --- |
| `format` | The layout of a JSON file | every file | whoever changes the format |
| `eval_version` | Which requests a run measured: prompts, answer format, window | `core/protocol.json`, looked up from the header's `protocol.fingerprint` | by hand; `evaluate run` refuses to start when the fingerprint is not in the table |
| `scorer_version` | How the numbers were computed | `SCORER_VERSION` in `report/score.py`, recorded in the score cache | by hand when a number changes; the golden numbers of the tests fail until it is |
| `tool_revision` | The commit the tools were at | every session line | git. Information only: nothing checks, groups by or refuses because of it |

Four keys are built from a header (`core/axes.py`), each with one job:

| Key | Adds to the compare key | Its job |
| --- | --- | --- |
| **compare key** | the task, the test set, and every axis of the protocol except the fingerprint | Pooling: runs of one configuration are compared and reported together. Seed, repeat, timeout, model facts and environment are not in it, so repeats and machines pool. |
| **series key** | `machine`, `isolated` | A gate group: the repeats of one setup on one machine. The server version is not in it, so an upgrade between repeats stays one series. |
| **run key** | `machine`, `isolated`, `repeat` | One file: a resume looks for it, the score cache and a spot-check sample are named by it. |
| **environment key** | `machine`, `ollama_version`, `isolated` | Speed: one model on two machines measured the same quality and different speeds. |

Runs are grouped and compared by the compare key, never by commit and never by `eval_version`.
Every axis of a run is declared once in `core/axis_table.py`; an axis a newer writer added and this
reader does not know is kept and part of the key, so two runs that differ in it never pool.
`core/protocol.json` records each protocol version with its date, the fingerprints it accepts and a
line saying why it was raised; old entries are never edited or removed.

## Test sets

A test set is three things with separate meanings:

- the **question** (`id`, `language`, `source` of every text): what is sent. Another question is
  another set. `question_sha256` hashes the texts' hashes in order, and a run records it.
- the **key** (`kind`, `reference`, `sentences`, `labels`, `sentence_spans`, `unchanged_sentences`,
  `accepted`): read only when scoring. A better key scores the same runs again. `key_version` is
  raised by hand when it changes, and a manifest of a higher version replaces one of a lower
  version; the same version with another `key_sha256` is refused.
- the **provenance** (`origin`, `band`, `corpus`, `pointer`).

**A manifest is immutable.** Another selection is another `testset` id: committed runs pin the
manifest by hash, so an edited manifest would make them uncheckable. It holds no corpus text.

**Pointers** are the builder's choice in the corpus's own terms, which is what makes a rebuild
possible. A pointer carries `split` only when it is not the corpus's default; a manifest pins every
file of each split its items use. The shapes per corpus are in `input/corpora/`.

**Reference replacements are hashes only.** The replacement of a reference edit is the annotator's
word, which is corpus text. Position, label code, word count and hash are enough to check a rebuild
and to score; the words come back from the corpus at level 2.

**Accepted alternatives** (`accepted.jsonl`) are written by
`bazel run //tools:store -- accept-marks --testset <id> --marker <name>`. Only the named marker's
`valid` edits are accepted, and only those no other marker called `wrong`; a disagreement is listed,
never resolved. The file is merged, not rewritten: a second marker's run replaces that marker's
contribution and keeps the others. It is part of the key, so adding to it moves `key_sha256` and no
run.

## Runs

One JSON object per line: the header, then one line per session (a start or a resume), then one
answer per text. A resume compares the stored header with the new one field by field, except the
fingerprint: two fingerprints of one `eval_version` are one protocol, so an additive change of the
request does not block a long run in progress.

**The file name** is a label derived from the axes and is never read back (a reader that parses a
name is a bug: a name cannot carry a new axis):

```
<date>_<host>_<model-slug>_<digest8>__<tag>__…__<key8>.run.jsonl
```

`host` is the machine id, or the provider's name for a provider that ran on no machine of ours;
`model-slug` is the tag in lower case with `hf.co/` removed and `/` and `:` replaced by `-`; each
`tag` names one axis that differs from its default, then `temperature-<t>` and `repeat-<n>`, which
always appear; `key8` is the compare key's hash. `store convert` refuses a name that already exists;
`--label` tells two runs of one configuration apart.

**`steps`** is what the exchange's one answer holds, and the one rule that says whether a run
explains: `one-request-review` holds all three steps, `one-request-text` holds `correct` alone,
`text-first` holds all three unless the run switched the unclear check off. A review is absent
exactly when `steps` has no `explain`.

**`instructions`** is the SHA-256 of the policy and answer format of each language. It hashes what
went into the request, so a run whose prompt has since changed is recognisable.

## Marks

A teacher's marks of a run's explanations, written by
`bazel run //tools:store -- marks SHEET.md --marker <name>`. The file holds no text: the sheet does,
and it is not committed. One file per marker and set, named by the marker and the set's question
hash, so a mark of another set is refused. The file is merged, not replaced, and written sorted by
identity, so two writers of the same marks produce the same bytes.

A mark's identity is `(kind, text, span, replacement, explanation_sha256)`. The span is in
characters, so a mark survives a tokenizer change; only a moved hunk boundary can invalidate it,
which the reader lists, never drops. The explanation's hash is part of the identity because a verdict
on an explanation is about one model's text: two models that wrote the same explanation share the
verdict, and two that wrote different ones do not. An `edit` mark keeps the three-part identity,
because an edit is the same edit whoever explained it.

The kind makes `wrong` unambiguous: on an `edit` it refuses the edit, on an `explanation` it says
the sentence does not explain it. `correct` (true, and fits the change) and `vague` (true but too
general to help a student) are the spot check's verdicts, and only `correct` counts. `valid` is a
correction the annotators did not make but a person judged right; only an `edit` mark can say it,
and `accept-marks` turns it into key data.

The sheet's sample is the run's, not a cache's: from the run of repeat 1, per deciding language, the
notes of `page`-band texts that explain an edit, at most two per text, drawn with a seed made from
the run key. Every machine draws the same one, and a renamed file draws the same sample.

## Machines and sizes

A run and a size record name a machine by its id from `machines/`, so a typo is refused instead of
becoming a second machine nobody can compare with the first. A machine is not a device: one machine
is measured on its card and on its processor (`device`), and a size record keeps the two apart.
`model-fit/sizes.jsonl` is append-only and holds numbers only; nothing derived is stored.

## Gate records

`gates/<record>.md` holds a gate's verdict (which models passed, which were dropped, and why), the
run files it rests on, by name, and the `bazel run //tools:report -- …` line that prints the figures
again. **No figure**: the numbers are in the runs, and a record that copied them would go stale the
first time a scorer changed. A record is written once the runs it reads are in the store, so it
never names a run that is not there.

## Validation

`bazel run //tools:store -- validate --store <store>` checks the store's files; `validate <run file>`
checks single files. The rules are in `tools/evaluation/store/validate.py`: a test set's ids are
unique and its spans lie inside the text on token boundaries; every label is still an edit of the
reference (a label whose edit moved is **listed**, never dropped); a manifest and its records name
each other both ways with matching hashes; a run's question hash is the set's, an Ollama run names
its machine, the fingerprint is in the table and no text is answered twice; a mark's text exists and
its span lies on token boundaries. **Strict mode** (with the corpora named, `--corpus NAME=PATH` or
`--corpora FILE`) adds the corpus file hashes and a full rebuild of every item.

Nothing repairs a file. A problem is a string a person reads, and the command decides whether to
refuse.

## Reading a run back and the score cache

`read_run_file(lines)` in `core/records.py` returns the header, the sessions and the answers as they
are. `load(root)` in `report/load.py` pairs every answer with the record it names and scores it; a
file or an answer that cannot be used is skipped with a reason, so a report can say "23 / 31 texts"
instead of pretending a run is complete.

`report --cache <dir>` keeps the numbers it computed in `<dir>/<run key hash>.scores.jsonl`, one line
per answer. A line is used only when its `answer_sha256`, its `key_part_sha256` and its
`scorer_version` are the current ones; anything else is scored again, so a stale cache costs time and
never a wrong number.
