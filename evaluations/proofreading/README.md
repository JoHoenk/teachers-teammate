# Proofreading runs

Every proofreading evaluation run is committed here, as one file per run. A run file says which
texts a model was given, what it answered, how it scored, and on which machine, at which commit, with
which seed — enough to check the run later and to score it again with a metric that did not exist
when it ran.

**The corpora never come in.** The texts come from Falko-MERLIN, W&I+LOCNESS and the invented
French set; they are licensed for research and are not redistributable. A run file therefore holds
hashes, positions, label codes, numbers and *capped* model words — never a source, reference or
output text. `proofreading_store validate` enforces that, and the test suite runs it over every
committed file.

| Path | Content |
| --- | --- |
| `FORMAT.md` | The schema of every file below, with the version history. |
| `INDEX.md` | Generated list of all runs with their checksums. Do not edit. |
| `machines/<machine>.json` | One file per test machine, written by hand once. |
| `testsets/<testset>/manifest.json` | Which texts the items are, pinned by hash. Immutable. |
| `runs/<testset>/<run-id>.jsonl` | One run. Written once, never edited. |

`README.md` (this file) and `FORMAT.md` are the only files here written by hand besides the machine
files.

## Where the local files live

Nothing below is committed:

```
~/gec-testset/fm/data/               the Falko-MERLIN corpus
~/gec-testset/bea/wi+locness/        the W&I+LOCNESS corpus
~/gec-testset/corpora.json           where the corpora are, for every command below
~/gec-testset/set-2026-12/           the built test set (de.jsonl, en.jsonl, fr.jsonl)
~/gec-testset/results/               the local run cache: <name>.jsonl and <name>.header.json
```

The local cache is what `compare_writing_models run` writes and what `proofreading_store report
--cache` reads: source, reference and output in every row. The repository format is *produced from* that
cache by an export step; the cache itself is never committed.

## Naming the corpora

Every command that needs corpus text takes the same two options: `--corpora FILE`, a JSON object of
`{name: path}`, and `--corpus NAME=PATH`, repeatable, which overrides an entry of the file. Relative
paths in the file are relative to the file, so one file serves every command from every directory:

```json
{"falko-merlin": "fm/data", "wi": "bea/wi+locness", "locness": "bea/wi+locness"}
```

```bash
bazel run //tools:proofreading_store -- validate --out evaluations/proofreading \
    --corpora ~/gec-testset/corpora.json
bazel run //tools:proofreading_store -- validate --out evaluations/proofreading \
    --corpus falko-merlin=~/gec-testset/fm/data --corpus wi=~/gec-testset/bea/wi+locness
```

The names are the adapters in `tools/model_eval/corpora/`; `--help` lists them with what each path
should point at. A command needs the corpora its manifest (or, for the builder, its recipe) names,
not a fixed pair: a German-only set needs `falko-merlin` alone. The invented French set has a
default location in the repository, so it is never given. A corpus that is given but not needed is
ignored.

## The protocol version

`tools/model_eval/protocol.json` says what a run measured and how: the prompts, the answer format,
the window, the level and the long-text budget. Its `current` entry is copied into every run header
as `eval_version` and `fingerprint`, and runs are grouped and compared by that version — never by
commit.

`run` computes the fingerprint of the request side (captured from the app's own corrector, so a
change of the user message or the answer schema counts as well) before the first request and stops with exit
code 2 when it differs from the entry of `current`:

```
the prompts, the answer format or the window have changed since version 1: the request side hashes
to <sha256>, protocol.json records <sha256>.  Either add a version to protocol.json with a line
saying why, or undo the change.
```

So raising the version means adding an entry with the new fingerprint, the date and a line saying
why, and setting `current` to it. Do that when the check refuses, and also when the scoring, the
caps or the edit extraction change — the fingerprint stays the same then, but the golden numbers of
`test_export.py`, `test_validation.py` and `test_comparison.py` fail and remind you. Old entries are
never edited or removed, so every committed run stays checkable against the version it names.

The protocol file was restarted on 2026-11-12, together with the answer format (version 1), when the
early measurements were deleted: it holds a single entry, version 1. Old entries are only ever added
to from here on.

`run` checks every combination of model, temperature, mode and repeat *before* the first request, so
a conflict on the third model does not stop the run after the first two have run for hours. A
results file without a header, a test set rebuilt under the same name (its hash differs), another
timeout and every difference in the protocol are refused with exit code 2 and the files to delete.
To see which part of the request side moved after a change, print `protocol.fingerprint()`: it holds
a hash per answer mode and language, the text budgets, the repair message and the answer format.

The export refuses a local run whose header has no protocol fields (made before the version file
existed: run it again) and a run whose `(eval_version, fingerprint)` is not an entry of
`protocol.json` (made with an unrecorded protocol). It copies the four fields from the header and
never recomputes them, so a prompt changed after the run stays visible.

## Exporting a run

```bash
# 1. Run the models into the local cache (results are appended; an interrupted run resumes)
bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set-2026-12 \
    --out ~/gec-testset/results --model gemma4:12b --temperature 0.7 \
    --seed 20261109 --machine rx9070xt-server

# 2. Write it into the repository (refuses a run it cannot rebuild or that breaks the caps)
bazel run //tools:proofreading_store -- export \
    --cache ~/gec-testset/results --testset gec-2026-12 \
    --manifest evaluations/proofreading/testsets/gec-2026-12/manifest.json \
    --corpora ~/gec-testset/corpora.json \
    --machine rx9070xt-server --out evaluations/proofreading
```

The export writes `runs/<testset>/<run-id>.jsonl`, proves it against the real texts (the five-word
rule, the hashes), and regenerates `INDEX.md`. It refuses to touch a run id that already exists: a
run file is written once.

The corpora are needed for the export because the caps are checked against the real text of every
item. Without the corpora the manifest names, the tool refuses and says which flag to add.

## Checking and listing

```bash
# Format mode: the schema and the caps that need no corpus. What the test suite runs.
bazel run //tools:proofreading_store -- validate --out evaluations/proofreading

# Strict mode: additionally the five-word rule against the real text, and the hashes.
bazel run //tools:proofreading_store -- validate --out evaluations/proofreading \
    --corpora ~/gec-testset/corpora.json

# Regenerate INDEX.md; --check fails when it is missing or out of date
bazel run //tools:proofreading_store -- index --out evaluations/proofreading
bazel run //tools:proofreading_store -- index --out evaluations/proofreading --check

# The results table of the committed runs (`report --cache DIR` prints the same table from a local cache)
bazel run //tools:proofreading_store -- report --out evaluations/proofreading
bazel run //tools:proofreading_store -- report --out evaluations/proofreading --performance
bazel run //tools:proofreading_store -- report evaluations/proofreading/runs/gec-2026-12/<run-id>.jsonl
```

`validate` exits non-zero and prints one line per problem, so it can be a pre-commit check.

`report` builds its table from the typed items of the run files (`run_format.StoredRun.items`), not
from a local cache, and prints the same table `report --cache` prints for the same
run — `test_report_sources.py` checks that. Give it run files and it prints those; give it none and
it prints every run below `--out`. A file that fails format mode is named and skipped, and runs of
different eval versions or test sets are refused in one call. Grouping across repeats, ranges and
the by-error view are not there yet.

### Reading the two tables

The first table has one row per configuration and language, over all texts of that language. The
second, below it, splits every language into its bands — `short`, `page` and `pages` — because a
model can be good on a sentence and bad on a page. Read the band rows as a diagnostic, not as a
decision: they hold four, five and two texts per language, so one text moves a band row by a lot.
The `pages` band is marked as such in the table. The rows of one language add up to its row in the
first table, because both are counts and not means.

The columns are the same in both tables. **F0.5** is the score that decides, precision weighted
twice as much as recall: a wrong correction costs a teacher more than a missed one. **GLEU** sits
next to the GLEU of the unchanged source, `0.62 (src 0.55)`; a model below its source made the text
worse. **Left alone** is how many of the sentences the reference leaves alone the model leaves alone
too, **coverage** how many of the sentences the reference changes the model looked at — read the two
together, since a model that returns half a page leaves much alone and covers little. **Recall 1st /
2nd half** shows a model that stops early or drifts. **Fallback** counts the texts that fell back to
corrections only, and how many of those the window cut off. **Explained** is the share of the
model's changes that carry an explanation; a review-mode fallback counts its changes as
unexplained, so falling back cannot improve the table.

The line under the first table means F0.5 and GLEU over German and English, the two languages that
decide. French is reported only: it has no bands and does not decide.

**GLEU** is the metric of Napoles, Sakaguchi, Post and Tetreault, in the version without a tunable
weight. `tools/model_eval/gleu.py` is our own implementation of the published formula; it contains
no code of the authors' script. Please cite:

* C. Napoles, K. Sakaguchi, M. Post, J. Tetreault (2015). *Ground Truth for Grammatical Error
  Correction Metrics.* Proceedings of ACL-IJCNLP 2015 (Short Papers), pp. 588–593.
  <https://aclanthology.org/P15-2097>
* C. Napoles, K. Sakaguchi, M. Post, J. Tetreault (2016). *GLEU Without Tuning.*
  arXiv:1605.02592. <http://arxiv.org/abs/1605.02592>

## The rule against corpus text

A **word** is a word or a mark of the app's tokeniser; whitespace is not counted. A field is cut
down when it breaks one of these rules, in this order of preference:

| Field | Rule | When broken |
| --- | --- | --- |
| Replacement of a model edit | at most 8 words **and** no 5 consecutive words that stand consecutively (case-folded, marks ignored) in the source or reference of the item | stored as `{h, n}` |
| Explanation | no 5 consecutive source or reference words | stored as `…` |
| Anything of a `restricted` item | no words at all | replacements as `{h, n}`; the explanation as `""`, its hash kept |
| Reference edit (manifest) | never text | always `{h, words}` |

`restricted` is the LOCNESS part of the English corpus: its licence forbids passing on any part. The
ten English correct sentences come from those essays, so their items are stored without words. For
the W&I essays (excerpts under 100 words are allowed) and for German, the common rule applies.

A replacement stored as `{h, n}` costs a reader of the run the ability to rebuild the model's text
for that item; everything else about it stays. On the 11 runs of 2026-11-07 that would have hit 2 of
3,604 replacements, and the quote rule 42 of 2,542 explanations.

Hashes are a **check**, not a protection: the corpora are public, so anyone who has them can
reproduce a hash. They reveal nothing the corpora do not.

The exception is a `restricted` corpus. A plain SHA-256 of a word or two can be reversed by trying a
dictionary, which would hand out words the licence forbids passing on, so the hashes of a restricted
item's fields are HMAC-SHA256 under a key made from the corpus files themselves
(`CorpusAdapter.redaction_key()`): only someone who holds the corpus can recompute them, and an
export of a restricted item without the corpora is refused.

## Building the test set and its manifest

```bash
bazel run //tools:prepare_gec_testset -- --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json --out ~/gec-testset/set-2026-12 \
    --manifest evaluations/proofreading/testsets/gec-2026-12/manifest.json \
    --built 2026-11-10
```

The builder chooses *pointers* (which text, which sentences) and resolves them through the same
function a rebuild calls (`tools/model_eval/testset.resolve`), so build and rebuild cannot drift
apart. The manifest records the pointers and the hashes of what they resolve to, and no corpus text.

`--testset` picks the *recipe* in `tools/model_eval/recipes/`, which holds the selection rule, its
seed and the corpora it needs; it is required, because with two recipes a default would build the
wrong set sooner or later. A recipe never changes once its manifest is committed: another selection
is another test-set id.

`--built` pins the day the set is built (default: today), so two builds of one day are
byte-identical. After writing the manifest the builder checks it against the corpora — every corpus
file and every item — and exits 1 when something no longer matches. A tree with uncommitted changes
is a warning, not a refusal: a trial build is allowed, a set that is committed is not.

The same check is one command on a manifest that is already there, and it also reports a `+dirty`
builder revision as a problem:

```bash
bazel run //tools:proofreading_store -- manifest \
    --manifest evaluations/proofreading/testsets/gec-2026-12/manifest.json \
    --corpora ~/gec-testset/corpora.json
```

A manifest is **immutable**: another selection is another test-set id. Committed runs pin it by hash,
so changing a manifest would make them uncheckable.

### Accepting a new set

A manifest is committed only when two builds of it agree, byte for byte, under two hash seeds, and
when the check above passes on the result. On a clean tree, with the day of the build for `--built`:

```bash
test -z "$(git status --porcelain)" || { echo "commit first"; exit 1; }
for seed in 1 2; do
  PYTHONHASHSEED=$seed PYTHONPATH=. .venv/bin/python tools/prepare_gec_testset.py \
    --testset gec-2026-12 --corpora ~/gec-testset/corpora.json --built 2026-11-10 \
    --out "/tmp/gec-$seed/set" --manifest "/tmp/gec-$seed/manifest.json" || exit 1
done
cmp /tmp/gec-1/manifest.json /tmp/gec-2/manifest.json
diff -r /tmp/gec-1/set /tmp/gec-2/set
bazel run //tools:proofreading_store -- manifest --manifest /tmp/gec-1/manifest.json \
  --corpora ~/gec-testset/corpora.json
```

`cmp` and `diff` print nothing and `manifest` exits 0 when the set is accepted. Then
`/tmp/gec-1/manifest.json` is copied to `testsets/<testset>/manifest.json` and committed in a commit
of its own, whose message records the builder revision, the two hash seeds and the figures of the
set; the built set is copied to `~/gec-testset/set-2026-12` for the runs. The system `python3` lacks
`pydantic`, hence `.venv/bin/python` outside Bazel.

## The link to `docs/development.md`

`docs/development.md` describes how the runs are made and what the numbers mean. This directory is
where they are kept. Keep the rule when adding anything here: counts and scores go in, texts stay
out.
