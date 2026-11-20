# Proofreading runs

Every proofreading evaluation run is committed here, as one file per run. A run file says which
texts a model was given, what it answered, how it scored, and on which machine, at which seed, at
which revision — enough to check the run later and to score it again with a metric that did not
exist when it ran.

**The corpora never come in.** The texts come from Falko-MERLIN, W&I+LOCNESS and the invented
French set; they are licensed for research and are not redistributable. A run file therefore holds
hashes, positions, label codes, numbers and *capped* model words — never a source, reference or
output text. `bazel run //tools:store -- validate` enforces that, and the test suite runs it over
every committed file.

| Path | Content |
| --- | --- |
| `FORMAT.md` | The format of every file below, with the version history. |
| `INDEX.md` | Generated list of all runs with their checksums. Do not edit. |
| `export.json` | What the export holds: the sets, their hashes, the scorer. Written by `publish export`. |
| `testsets/<testset>/manifest.json` | Which texts the items are, pinned by hash. Immutable. |
| `testsets/<testset>/<language>.jsonl` | The set's records: one per text, with positions and label codes, no text. |
| `testsets/<testset>/accepted.jsonl` | The alternatives a teacher accepted, which the scorer counts once. |
| `runs/<name>.run.jsonl` | One run. Written once, never edited. |
| `marks/<marker>__<question>.jsonl` | One marker's verdicts on one set. |

`README.md` (this file) and `FORMAT.md` are the only files here written by hand. The machines a run
names are in `evaluations/machines/`, one file per machine.

A run file sits directly below `runs/`. The folder a converted run came from stays in the store as
provenance and is not exported: a run file names its test set in its header, and its own name is a
label derived from the axes, so nothing reads a folder back.

## The four tools

| Tool | What it does |
| --- | --- |
| `bazel run //tools:evaluate -- run` | Sends every text of a set to the models and appends the answers to the store. |
| `bazel run //tools:store -- …` | `convert` an old store once, `validate` the files, `marks` a filled-in sheet, `accept-marks` one marker's verdicts, `import-blind-key` the old blind sheet, `manifest` a set. |
| `bazel run //tools:report -- …` | `table` the numbers, `compare` two groups, `gate` one model against the floors, `passes` what a second pass would add, `sheet` a run for a teacher to mark, `page` the report folder. |
| `bazel run //tools:publish -- …` | `export` local runs into this directory, `index` regenerate `INDEX.md`. |

**The axis vocabulary.** A run is described by two axes, not by a "mode": `exchange` is how the text
travels (`text-first`, `one-request-review`, `one-request-text`), and `steps` is which steps of it
ran (`correct`, `explain`, `unclear`). `text-first/correct` is step ① alone — the old
`correct-only`. The other axes are `model`, `temperature`, `num_ctx`, `compact_json`, `prompt`,
`sampling.<option>`, `machine`, `testset` and the rest of `tools/evaluation/core/axes.py`. Every
command that selects runs takes them as `--exchange`, `--steps`, `--temperature`, `--machine`,
`--testset`, `--digest` and `--compact-json`.

## Where the local files live

Nothing below is committed:

```
~/gec-testset/fm/data/                      the Falko-MERLIN corpus
~/gec-testset/bea/wi+locness/               the W&I+LOCNESS corpus
~/gec-testset/corpora.json                  where the corpora are, for every command below
~/gec-testset/store/testsets/gec-2026-12/   the built set: de.jsonl, en.jsonl, fr.jsonl, manifest.json
~/gec-testset/store/runs/                   one <name>.run.jsonl per run
~/gec-testset/store/marks/                  one <marker>__<question>.jsonl per marker
```

The store is what `evaluate run` writes and what `report --store` reads: source, reference and
output in every row. The repository format is *produced from* the store by an export step; the store
itself is never committed.

## Naming the corpora

Every command that needs corpus text takes the same two options: `--corpora FILE`, a JSON object of
`{name: path}`, and `--corpus NAME=PATH`, repeatable, which overrides an entry of the file. Relative
paths in the file are relative to the file, so one file serves every command from every directory:

```json
{"falko-merlin": "fm/data", "wi": "bea/wi+locness", "locness": "bea/wi+locness"}
```

```bash
bazel run //tools:store -- validate --store ~/gec-testset/store \
    --corpora ~/gec-testset/corpora.json
bazel run //tools:store -- validate --store ~/gec-testset/store \
    --corpus falko-merlin=~/gec-testset/fm/data --corpus wi=~/gec-testset/bea/wi+locness
```

The names are the adapters in `tools/evaluation/input/corpora/`; `--help` lists them with what each
path should point at. A command needs the corpora its manifest (or, for the builder, its recipe)
names, not a fixed pair: a German-only set needs `falko-merlin` alone. The invented French set has a
default location in the repository, so it is never given. A corpus that is given but not needed is
ignored.

## The protocol version

`tools/evaluation/core/protocol.json` says what a run measured and how: the prompts, the answer
format, the window, the level and the long-text budget. A version lists the fingerprints it accepts;
the header records `protocol.fingerprint`, and the version is looked up from it. Runs are grouped
and compared by the compare key — the task, the test set and every axis except the fingerprint —
never by commit.

`run` computes the fingerprint of the request side (captured from the app's own corrector, so a
change of the user message or the answer schema counts as well) before the first request and stops
with exit code 2 when the current version does not accept it:

```
the prompts, the answer format or the window have changed since version <version>: the request side
hashes to <sha256>, protocol.json accepts <sha256>.  Either add the fingerprint to version <version>
with a line saying why, or raise the version by hand, or undo the change.
```

An additive change — the same requests, a new variant beside them — adds its fingerprint to the
current version, after the check that every existing request hashes as before. A request-side change
raises the version by hand: add an entry with the new fingerprint, the date and a line saying why,
and set `current` to it. Old entries are never edited or removed, so every committed run stays
checkable against the version it names.

A change of the scoring, the caps or the edit extraction does not move the fingerprint: it raises
`scorer_version` (`tools/evaluation/report/score.py`), and the golden numbers of the tests fail until
it does. The export copies the scorer it used into the redacted header and into `export.json`.

`run` checks every combination of model, temperature, exchange, steps and repeat *before* the first
request, so a conflict on the third model does not stop the run after the first two have run for
hours. A run file without a header, a test set rebuilt under the same name (its question hash
differs), another timeout and every difference in the protocol are refused with exit code 2 and the
files to delete. To see which part of the request side moved after a change, print
`protocol.fingerprint()`: it holds a hash per exchange and language, the text budgets, the repair
message and the answer format.

The export copies the header's fields and never recomputes them, so a prompt changed after the run
stays visible.

## Exporting a run

```bash
# 1. Run the models into the store (answers are appended; an interrupted run resumes)
bazel run //tools:evaluate -- run --store ~/gec-testset/store \
    --testset gec-2026-12 --model gemma4:12b --exchange text-first \
    --repeat 3 --machine rx9070xt-server

# 2. Write it into the repository (refuses a run it cannot rebuild or that breaks the caps)
bazel run //tools:publish -- export --store ~/gec-testset/store \
    --out evaluations/proofreading --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json
```

The export writes `runs/<name>.run.jsonl`, proves it against the real texts (the five-word rule, the
hashes), copies the set's `manifest.json` byte for byte, writes `export.json` and regenerates
`INDEX.md`.

An export is a plan first: one entry per run saying what would happen to it, and nothing is written
while any entry is refused. A committed run file is written once and never merged, so a second
export of the same run says `unchanged`; a run that grew since it was written is `replace`; a run
whose numbers were measured against another key or another scorer is `stale` until `--rescore`; a
run that holds fewer answers than its set has texts is `skipped` unless `--partial` is given. The
two exceptions are derived data: a copied manifest is compared by hash and replaced when the set's
`key_version` rises, and `export.json` is merged, so the sets of an earlier export stay.

`--label` tells two runs of one configuration apart.

The corpora are needed only when the set holds a `restricted` corpus: its items store no words, and
its key is what their hashes are made with. Without it the export refuses and names the flag.

## Checking and listing

```bash
# Format mode: the schema and the caps that need no corpus.
bazel run //tools:store -- validate --store ~/gec-testset/store

# Strict mode: additionally the five-word rule against the real text, and the hashes.
bazel run //tools:store -- validate --store ~/gec-testset/store \
    --corpora ~/gec-testset/corpora.json

# One run file, of the store or of this directory
bazel run //tools:store -- validate evaluations/proofreading/runs/<name>.run.jsonl

# Regenerate INDEX.md; --check fails when it is missing or out of date
bazel run //tools:publish -- index --out evaluations/proofreading
bazel run //tools:publish -- index --out evaluations/proofreading --check

# The results table of the committed runs, or of the store
bazel run //tools:report -- table --out evaluations/proofreading
bazel run //tools:report -- table --out evaluations/proofreading --performance
bazel run //tools:report -- table --store ~/gec-testset/store
```

`validate` exits non-zero and prints one line per problem, so it can be a pre-commit check. A
committed run file is recognised by its `scores`; the caps are checked against the store's texts
when the store has them.

What the test suite does today: `tests/tools/test_evaluation_committed.py` reads every committed run
file in format mode — no store, no corpus — and requires each to pass the schema and the caps that
need no text, to name its scorer and its key, and to be listed in `INDEX.md` as the files give it.
No run is committed yet, so the loop is empty; the test is what keeps the first export honest.

`report table` builds its table from the numbers the export stored (`report/load.py:load_export`),
not from a local cache, and prints the same table `--store` prints for the same run. `--out` prints
every run below it; `--testset` and `--folder` narrow it. A file that fails format mode is named and
skipped, and a run whose set, key or scorer the export does not hold is skipped with the reason.
`--by-error` and `--false-alarms` need `--store`, because an export holds no text.

### Reading the two tables

The first table has one row per configuration and language, over all texts of that language. The
second, below it, splits every language into its bands — `short`, `page` and `pages` — because a
model can be good on a sentence and bad on a page. Read the band rows as a diagnostic, not as a
decision: they hold four, five and two texts per language, so one text moves a band row by a lot.
The `pages` band is marked as such in the table. The rows of one language add up to its row in the
first table, because both are counts and not means. The band table shows fewer columns: only the
score, GLEU, left alone, coverage, the two halves and fallback.

**F0.5** is the score that decides, precision weighted twice as much as recall: a wrong correction
costs a teacher more than a missed one. **GLEU** sits next to the GLEU of the unchanged source,
`0.62 (src 0.55)`; a model below its source made the text worse. **Found the spot** is the share of
the reference's changes the model touched at all, with the gap to **R** in brackets: what it noticed
and fixed differently, which the F-score counts as a miss. **Left alone** is how many of the
sentences the reference leaves alone the model leaves alone too, **coverage** how many of the
sentences the reference changes the model looked at — read the two together, since a model that
returns half a page leaves much alone and covers little. **Recall 1st / 2nd half** shows a model
that stops early or drifts. **Fixed / Wrong / Missed per page** are the true, false and missed
changes per 300 words. **Failed** counts the texts the app could not answer, which the teacher got
back unchanged. **Fallback** counts the texts that fell back to corrections only, and how many of
those the window cut off. **Lost / added** counts the texts whose answer kept much less or added
much more text than the reference. **Explained** is the share of the model's changes that carry an
explanation; a text whose review could not be read is left out of that share, so a fallback neither
helps nor hurts it. **Orphans/text** counts the notes that explain no change.

The line under the first table means F0.5 and GLEU over German and English, the two languages that
decide. French is reported only: it has no bands and does not decide.

**GLEU** is the metric of Napoles, Sakaguchi, Post and Tetreault, in the version without a tunable
weight. `tools/evaluation/metrics/gleu.py` is our own implementation of the published formula; it
contains no code of the authors' script. Please cite:

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

`restricted` is a corpus whose licence forbids passing on any part; the manifest marks it, and its
items store no words at all. The current set has none: the W&I essays are excerpts under 100 words,
which the licence allows, and German is CC BY-SA.

A replacement stored as `{h, n}` costs a reader of the run the ability to rebuild the model's text
for that item; everything else about it stays.

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
    --corpora ~/gec-testset/corpora.json --store ~/gec-testset/store \
    --built 2026-11-10
```

The builder chooses *pointers* (which text, which sentences) and resolves them through the same
function a rebuild calls (`tools/evaluation/input/testset.resolve`), so build and rebuild cannot
drift apart. The manifest records the pointers and the hashes of what they resolve to, and no corpus
text.

`--testset` picks the *recipe* in `tools/evaluation/input/recipes/`, which holds the selection rule,
its seed and the corpora it needs; it is required, because with two recipes a default would build
the wrong set sooner or later. A recipe never changes once its manifest is committed: another
selection is another test-set id.

`--built` pins the day the set is built (default: today), so two builds of one day are
byte-identical. After writing the manifest the builder checks it against the corpora — every corpus
file and every item — and exits 1 when something no longer matches. A tree with uncommitted changes
is a warning, not a refusal: a trial build is allowed, a set that is committed is not.

The same check is one command on a manifest that is already there:

```bash
bazel run //tools:store -- manifest \
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
  PYTHONHASHSEED=$seed bazel run //tools:prepare_gec_testset -- --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json --built 2026-11-10 \
    --store "/tmp/gec-$seed/store" || exit 1
done
diff -r /tmp/gec-1/store/testsets/gec-2026-12 /tmp/gec-2/store/testsets/gec-2026-12
bazel run //tools:store -- manifest \
  --manifest /tmp/gec-1/store/testsets/gec-2026-12/manifest.json \
  --corpora ~/gec-testset/corpora.json
```

`diff` prints nothing and `manifest` exits 0 when the set is accepted. Then
`/tmp/gec-1/store/testsets/gec-2026-12/manifest.json` is copied to
`testsets/<testset>/manifest.json` and committed in a commit of its own, whose message records the
builder revision, the two hash seeds and the figures of the set; the built set stays in the store
for the runs.

## The link to `docs/development.md`

`docs/development.md` (*Ollama model catalog*) describes how the runs are made and what the numbers
mean. This directory is where they are kept. Keep the rule when adding anything here: counts and
scores go in, texts stay out.
