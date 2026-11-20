# The format of a stored proofreading run

Format version **2** for a run file, a test-set record and a manifest; **3** for a marks file, whose
marks have a kind; **3** for a redacted run file, which is a different record and not a run file with
fields added; **1** for `export.json`. Every file carries its `format`, and a reader refuses any
version it does not know rather than guessing. The history is at the end.

The rules the format serves:

| # | Requirement | How the format meets it |
| --- | --- | --- |
| 1 | A committed run can be **checked** later: same texts, same request, same weights, same machine | the question hash, the key hash, the protocol fingerprint, the instructions hash, the model digest, the seed and the machine are in the header |
| 2 | A committed run can be **re-scored** with a metric that did not exist when it ran | the model's edits are stored as character positions plus short replacements, not only the scores, and `scorer_version` names the scorer that made the numbers |
| 3 | A committed run can be **read without the corpora** | scores, edits and notes are numbers and capped words; every report works on them |
| 4 | The repository holds **no corpus sentence** | hashes, positions, label codes, numbers and capped model words only; `validate` enforces it |
| 5 | Runs are **evidence**: immutable and listed | append-only files, `INDEX.md` with checksums, a test that regenerates it |

Reading a run is possible at two levels. **Level 1** needs nothing but the file: the numbers are
there. **Level 2** needs the corpora: the texts are rebuilt from the pointers and the stored edits
are applied, so the scoring can be recomputed.

Everything is plain JSON with sorted keys, so `git diff` shows what changed and a run is one file.

---

## The four versions

They are easy to mix up, and only one of them groups runs:

| Name | Says | Where | Who changes it |
| --- | --- | --- | --- |
| `format` | The layout of a JSON file: which fields exist | A run file, a test-set record and a manifest (`2`); a marks file (`3`); a redacted run file (`3`); `export.json` (`1`); `protocol.json` carries `schema: 1` | Whoever changes the format |
| `eval_version` | Which requests a run measured: prompts, answer format, window | `tools/evaluation/core/protocol.json`, looked up from the header's `protocol.fingerprint` | Raised by hand; `evaluate run` refuses to start when the fingerprint is not in the table |
| `scorer_version` | How the numbers were computed | `SCORER_VERSION` in `tools/evaluation/report/score.py`, copied into the redacted header and the export manifest | Raised by hand when a number changes; the golden numbers of the tests fail until it is |
| `tool_revision` | The commit the tools were at | Every session line | Nobody: git. **Information only** — nothing checks it, groups by it or refuses because of it |

Four keys are built from a header, each with one job (`tools/evaluation/core/axes.py`):

| Key | Adds to the compare key | Its one job |
| --- | --- | --- |
| **compare key** | — the task, the test set, and every axis of the protocol except the fingerprint | Pooling: two runs of one configuration are compared and reported together. The seed, the repeat, the timeout, the model's facts and the environment are not in it, so repeats and machines pool. |
| **series key** | `machine`, `isolated` | A gate group: the repeats of one setup on one machine. The server version is not in it, so a server upgrade between repeats stays one series. |
| **run key** | `machine`, `isolated`, `repeat` | One file: a resume looks for it, a score cache and a spot-check sample are named by it, and a duplicate check compares it. |
| **environment key** | `machine`, `ollama_version`, `isolated` | Speed: two runs of one model on two machines measured the same quality and different speeds. |

Runs are grouped and compared by the compare key, never by commit and never by `eval_version`.

`protocol.json` records every version with its date, the fingerprints it accepts and a line saying
why it was raised:

```json
{
  "schema": 1,
  "current": 4,
  "versions": [
    {
      "version": 4,
      "date": "2026-11-16",
      "fingerprints": ["<sha256>"],
      "why": "Additive: step ① may send a named variant of its system prompt…"
    }
  ]
}
```

A version accepts a **list** of fingerprints: an additive change — the same requests, a new variant
beside them — adds its fingerprint to the current version, and a request-side change raises the
version by hand. A fingerprint belongs to exactly one version.

The fingerprint is the SHA-256 of the request side, which the tool computes itself
(`tools/evaluation/run/protocol.py`) by *capturing* it from the app's own corrector: for every
answer mode and language, a hash of the one request the corrector sends (system and user message,
answer schema, options without the run's temperature and seed) and the text budget the app splits a
long text by; plus the repair message and the answer format version. Because it is captured, not
listed, a new request setting is covered without anyone editing a list. `evaluate run` compares it
with the table before the first request and exits with code 2 when the table does not record it — an
uncommitted prompt edit is caught the same way as a committed one.

A change of the scoring does not move the fingerprint: it raises `scorer_version` instead, and the
golden numbers of the tests fail until it is.

---

## The store

```
<store>/testsets/<id>/<language>.jsonl            one record per text, per language
<store>/testsets/<id>/manifest.json               what pins the set without holding its text
<store>/testsets/<id>/accepted.jsonl              the accepted alternatives of the key
<store>/runs/<folder>/<name>.run.jsonl            one run file, header first
<store>/marks/<marker>__<question_sha256>.jsonl   one marker's verdicts on one set
```

| Path | Holds |
| --- | --- |
| `testsets/<id>/<language>.jsonl` | The texts of one language: source, reference, labels, spans. Corpus text, so it stays in the store and is never committed. |
| `testsets/<id>/manifest.json` | The pointers and hashes that pin the set; no text. |
| `testsets/<id>/accepted.jsonl` | The corrections a marker called valid, which the key accepts as a second annotator's fix. Optional. |
| `runs/<folder>/<name>.run.jsonl` | One run: header, sessions, one answer per text. Holds the model's output, so it stays in the store. |
| `marks/<marker>__<question_sha256>.jsonl` | One marker's verdicts on one set, named by the marker and the set's question hash. |

`<folder>` is the folder a converted run came from (`results-exchange`). It is provenance and not
part of the format: a run file names its test set in its header, and its own name is a label derived
from the axes, so nothing reads the folder back.

The store is local. What the repository holds is the **export** of it, below.

## `testsets/<id>/manifest.json`

Says *which* texts the items are, and what a correct rebuild must look like. It holds no corpus text.

```json
{
  "format": 2,
  "testset": "gec-2026-12",
  "built": "2026-11-10",
  "selection_seed": 20261110,
  "builder_revision": "40a5e37",
  "key_version": 1,
  "corpora": {
    "falko-merlin": {
      "licence": "Falko CC BY 3.0, MERLIN CC BY-SA 4.0",
      "restricted": false,
      "files": {"fm-test.m2": "<sha256>", "source/falko-id-ctok-zh1.txt.test": "<sha256>"}
    }
  },
  "question_sha256": "<sha256>",
  "key_sha256": "<sha256>",
  "items": [
    {
      "id": "de-01", "language": "de", "kind": "errors", "corpus": "falko-merlin",
      "band": "short",
      "pointer": {"origin": "merlin", "text": "1061_0120359", "sentences": [0, 14]},
      "text_sha256": "<sha256>",
      "key_part_sha256": "<sha256>",
      "source": {"sha256": "<sha256>", "words": 114, "chars": 538},
      "reference": {"sha256": "<sha256>"},
      "sentence_spans": [[0, 29], [30, 68], [69, 109]],
      "unchanged_sentences": [0, 11, 13],
      "edits": [{"start": 21, "end": 22, "h": "<sha256>", "words": 1, "label": "R:DET:FORM"}]
    }
  ]
}
```

| Key | Meaning |
| --- | --- |
| `format` | Format version, `2`. A reader refuses another one; the old `schema: 1` files, which pinned token positions, are rewritten by `tools/evaluation/store/convert_v1.py`. |
| `testset` | The test-set id; the directory name and every run's `input.testset`. |
| `built` | The day the builder made it. |
| `selection_seed` | The seed of the shuffles, so the same selection can be made again. The recipe is `tools/evaluation/input/recipes/<testset with _>.py` at `builder_revision`. |
| `builder_revision` | The commit of `tools/prepare_gec_testset.py` that built it, `+dirty` when the tree had uncommitted changes. |
| `key_version` | Raised by hand when the key changes: the same runs, scored against a better key. An export replaces a committed manifest of a lower version and refuses one of the same version with another `key_sha256`. |
| `corpora.<name>` | The name of an adapter in `tools/evaluation/input/corpora/`; `licence`, `restricted`, and the `sha256` of every corpus file the items come from. |
| `question_sha256` | The hash of the whole set's question: its texts' hashes, in order. Another value is another set, whatever the id says. |
| `key_sha256` | The hash of the whole set's key: every text's key group, in order. |
| `items[].id` | The item id, unique in the set. Run files use it with the language (`de/de-04`). |
| `items[].language` | A code of `tools/evaluation/core/languages.py` (`de`, `en`, `fr` today). |
| `items[].kind` | `errors` (a text with a reference correction) or `correct` (sentences that must stay untouched). |
| `items[].corpus` | Which corpus; keys correspond to `corpora`. |
| `items[].band` | `short`, `page` or `pages`, the band the recipe drew the text for; absent when the recipe has none. Bands count whitespace-separated words of the source (`short` 80 to 150, `page` 200 to 400, `pages` 600 to 1,200), not `source.words`, which also counts punctuation marks: a `short` text can record `words` above 150. |
| `items[].pointer` | What the builder chose, in the corpus's own terms — see below. |
| `items[].text_sha256` | The hash of the question, which is what a run binds to. |
| `items[].key_part_sha256` | The hash of this text's key group, so a changed key is visible per text. |
| `items[].level` | The CEFR level the corpus gives the text, when it has one (`gec-2026-12-heldout` records it, `gec-2026-12` does not); meta only, no run and no score reads it. |
| `items[].source` | `sha256` of the text as built, its `words`, and `chars`, the length of the source in characters, as the app measures it against its budget. |
| `items[].reference` | `sha256` of the corrected text; absent for a `correct` item. |
| `items[].sentence_spans` | Character spans `[start, end)` of every sentence, in the positions of `edits`. |
| `items[].unchanged_sentences` | Indices into `sentence_spans` of the sentences the reference leaves alone: the corpus calls the sentence unchanged (no edit and, for W&I, no `UNK` annotation) **and** `quality.untouched_spans` finds no change of the reference's word diff in or at the edge of it. A sentence untouched by the diff alone is not listed if the corpus saw an error in it. |
| `items[].edits` | The reference's changes: `start`, `end` (character positions), `h` (SHA-256 of the replacement's words joined by single spaces; a deletion hashes the empty string), `words`, `label`. |
| `items[].sentence_sha256` | For a `correct` item: one hash per sentence. |

**Pointers** are the builder's choice in the corpus's own terms, which is what makes a rebuild
possible. A pointer is an object, or a list of objects for a `correct` item (one per sentence):

| Corpus | Kind | Pointer |
| --- | --- | --- |
| `falko-merlin` | `errors` | `{"origin": "falko"\|"merlin", "text": <text id>, "sentences": [start, end]}` |
| `falko-merlin` | `correct` | one `{"origin", "text", "index"}` per sentence |
| `wi` | `errors` | `{"level": "A"\|"B"\|"C", "document": <essay id>}` |
| `locness` | `correct` | one `{"level": "N", "document": <essay id>, "span": [start, end]}` per sentence (character span) |
| `invented-fr` | both | `{"text": <id in the invented set>}` |

Every pointer may also carry `split`, the part of the corpus it comes from. Without it the pointer
means the corpus's default split, so a pointer written before `split` existed reads unchanged.
`falko-merlin` has `test` (the default), `dev` and `train`; `wi` has `dev` (the default) and `train`;
`locness` and `invented-fr` have no splits and refuse the key. A manifest pins every file of each
split its items use, so a dev-only set pins exactly the files it reads.

**Reference replacements are hashes only.** The replacement of a reference edit is the annotator's
word, which is corpus text. Position, label code, word count and hash are enough to check a rebuild
and to score; the words come back from the corpus at level 2.

A manifest is immutable. Another selection is another `testset` id: committed runs pin the manifest
by hash, so an edited manifest would make them uncheckable. The key may still move — see
`accepted.jsonl` — and a run records the key it was measured against. A key change raises
`key_version`, which is what lets an export replace the committed manifest instead of refusing it.

## `testsets/<id>/accepted.jsonl`

One line per accepted alternative, written by
`bazel run //tools:store -- accept-marks --testset <id> --marker <name>`:

```json
{"text": "de/de-01", "span": [21, 22], "replacement": "der", "markers": ["jh"]}
```

| Key | Meaning |
| --- | --- |
| `text` | The text's id, `<language>/<record id>`. |
| `span` | Character span of the source the edit replaces. |
| `replacement` | The text the edit puts there. |
| `markers` | Who accepted it, sorted. Absent when the file was written before markers were recorded. |

Only the named marker's `valid` edits are accepted, and only those no other marker called `wrong`;
a disagreement is listed, never resolved. A second marker's run of the command replaces that
marker's contribution and keeps the others, so the file is merged, not rewritten: an edit whose only
marker is the named one goes when that marker no longer calls it valid, and an edit another marker
also accepted stays with the named marker taken out of its `markers`. The command prints what it
added and what it removed.

The file is part of the key group, so adding one moves `key_sha256` and no run: the numbers of a run
are only meaningful against the key they were measured with, and the export records that key.

## `testsets/<id>/<language>.jsonl`

One record per text, `format: 2`:

```json
{
  "format": 2, "id": "de-01", "language": "de", "source": "<the text>",
  "kind": "errors", "reference": "<the corrected text>",
  "labels": [{"span": [21, 22], "replacement": "der", "code": "R:DET:FORM"}],
  "sentence_spans": [[0, 14], [15, 27]],
  "unchanged_sentences": [0],
  "accepted": [{"span": [21, 22], "replacement": "der"}],
  "origin": "merlin", "band": "short", "corpus": "falko-merlin",
  "pointer": {"origin": "merlin", "text": "1061_0120359", "sentences": [0, 14]}
}
```

| Field | Content |
| --- | --- |
| `format` | `2`. |
| `id`, `language`, `source` | The text's identity: its id, its language code, the text itself. |
| `request` | What a run *may* send besides the text: `level`, `notes`, `passages` (character spans of the printed task). Absent when a run sends none. |
| `kind` | `errors` (a text with a reference correction) or `correct` (sentences that must stay untouched). |
| `reference` | The corrected text; absent for a `correct` item. |
| `sentences` | The already-correct sentences of a `correct` item. |
| `labels` | The corpus label of every reference change: `span` (characters), `replacement`, `code` (`R:DET:FORM`). |
| `sentence_spans` | The character span of every sentence of the corpus's own segmentation. |
| `unchanged_sentences` | Indices into `sentence_spans` of the sentences the reference leaves alone. |
| `accepted` | The accepted alternatives of this text, as `accepted.jsonl` holds them. |
| `origin`, `band`, `corpus`, `pointer` | Provenance: where the text comes from, the band the recipe drew it for, the corpus, and where in the corpus it sits. |

The record is grouped by what a change of it means. The **question** (`id`, `language`, `source`)
is the text's identity and what is sent with every request; the **request** is what a run may send
besides it; the **key** (`kind`, `reference`, `sentences`, `labels`, `sentence_spans`,
`unchanged_sentences`, `accepted`) is read only when scoring; the rest is provenance. A change of
the question is another set; a change of the key is the same runs scored against a better key.

`text_sha256` is the hash of a text's language and source, `question_sha256` the hash of a set's
`text_sha256` values in order, and `key_sha256` the hash of every text's key group, in order. A run
records the first; the export records both.

## `runs/<folder>/<name>.run.jsonl`

One JSON object per line. Line 1 is the header, then one line per session, then one answer per text.
The file name is a label derived from the axes and is never read back.

### The file name

```
<date>_<host>_<model-slug>_<digest8>__<tag>__…__<key8>.run.jsonl
```

* `date` — `YYYY-MM-DD` of the first session;
* `host` — the machine id, an id of `evaluations/machines/`; for a run of a provider other than
  `ollama`, which ran on no machine of ours, the provider name stands here; `unknown-machine` when
  neither is known (`axes.host_of`, the one place that fallback lives);
* `model-slug` — the tag in lower case with a leading `hf.co/` removed and `/` and `:` replaced by
  `-` (`gemma4:12b` → `gemma4-12b`);
* `digest8` — the first eight hex digits of the model digest, `unknown` when the server gave none;
* `tag` — one per axis that differs from its default (`steps-correct+explain`, `compact_json-true`,
  `num_ctx-16384`, `sampling.top_k-20`, `explainer-<slug>`), then `temperature-<t>` and
  `repeat-<n>`, which always get one;
* `key8` — the first eight hex digits of the compare key's hash.

A reader that parses a name is a bug: a name cannot carry a new axis. An export refuses a name that
already exists; `--label` tells two runs of one configuration apart.

### The header

| Field | Content |
| --- | --- |
| `format`, `type` | `2`, and `"run"`. |
| `task` | `proofread`. A different task gets its own scorer. |
| `input` | `testset` (the manifest's id), `question_sha256`, `texts` (how many answers follow), `languages`. |
| `protocol` | The axes, each stored once under a readable name — see below. |
| `run` | `seed`, `repeat`, `timeout`. Not axes: the seed is derived from the repeat, so repeats would never pool. |
| `model_info` | The tag's `digest`, `quantization`, `parameter_size`, `family`. |
| `environment` | `machine`, `ollama_version`, `isolated`, `gpu_sampler`, and `machine_source` when an empty machine was filled in. `machine` is an id of `evaluations/machines/`, the registry `evaluate run --machine` and `verify_model_catalog fit --machine` check; a run and a size measurement of one machine are joined by it. |

`protocol` holds every axis: `model`, `model_digest`, `exchange` (`text-first`,
`one-request-text`, `one-request-review`, `sentence-walk`, `sentence-list`), `steps` (a subset of
`correct`, `explain`, `unclear`), `step_one_prompt` (the variant of step ①'s prompt), `temperature`,
`sampling` (the options that were sent, sorted by name), `compact_json`, `num_ctx`, `provider`,
`answer_format_version`, `uses` (the request parts the run sent), `instructions` (the hash of what
was sent, per language), `fingerprint` (only to look up `eval_version`; never part of a key) and
`explainer` (the model that wrote the explanations, when another model did).

`steps` is what the exchange's one answer holds, and it is the one rule that says whether a run
explains: `one-request-review` holds all three steps, because its schema asks for edits with
explanations and for the unclear expressions; `one-request-text` holds `correct` alone; `text-first`
holds all three unless the run switched the unclear check off, which only the app applies. A review
is absent exactly when `steps` has no `explain`.

`instructions` is the SHA-256 of the policy and answer format of each language, keyed by the
language code. The comparison asks for the language by name and sends plain text, so the preset of
that language is hashed, with the default level and the answer format of the run's exchange. It
hashes what went into the request, so a run whose prompt has since changed is recognisable — the
export copies the hash the run recorded and never recomputes it.

### The session

One line per start or resume, so an interrupted run can be continued:

| Field | Content |
| --- | --- |
| `format`, `type` | `2`, and `"session"`. |
| `date` | When the session started, as an ISO timestamp. |
| `tool_revision` | The commit the tools were at. Information only. |
| `vram_baseline_mb` | The graphics memory before the model was loaded. |
| `loaded_before` | What the server had in memory, by name. |

A resume compares the stored header with the new one field by field, except the fingerprint: two
fingerprints of one `eval_version` are one protocol, so an additive change of the request does not
block a long run in progress. A fingerprint the table does not record is compared as it is.

### The answer

| Field | Content |
| --- | --- |
| `format`, `type` | `2`, and `"answer"`. |
| `text`, `text_sha256` | The text's id, `<language>/<record id>`, and the hash of its question. |
| `output` | The corrected text as returned, marks included; for a failed text what the teacher got. |
| `failed`, `fallback` | Whether the text failed, and whether it fell back to corrections only. |
| `warning` | The warning the app gave, as text; absent when it gave none. |
| `review` | The app's own review: `version`, `edits` (each with `parts`, `sentences`, `category`, `explanation`, `optional`, `needed`) and `unclear`. Offsets are characters of the source. Absent without explanations. |
| `unclear_count` | Converted rows only: the count of a run before the unclear step quoted its findings. |
| `requests`, `loaded`, `shared` | What each request cost, what the server had loaded, and whether another model shared the card. |
| `vram_baseline_mb`, `vram_peak_mb` | The graphics memory before and at the peak. |
| `pieces` | How many pieces the app split the text into. |
| `seconds` | How long the text took. |
| `step_inferred` | Converted rows only: the steps were inferred from the request count, not recorded. |

**`pieces`** is how many pieces the app splits the text into: its long-text path splits a text over
the window's budget at paragraph and sentence ends, and a text within the budget is one piece,
whatever it holds. For a text that failed, the pieces after the failed one were not sent. A run of
the comparison therefore records how often a model was asked more than once for one text.

## The export

The repository holds no corpus text, so a run is *exported*: the model's edits become character
positions plus capped replacements, the notes categories plus capped explanations, and every outcome
a number. `bazel run //tools:publish -- export --store <store> --out evaluations/proofreading`
writes:

```
<out>/export.json                       the export's own manifest
<out>/testsets/<id>/manifest.json       the store's manifest, written again
<out>/runs/<name>.run.jsonl             one redacted run file
<out>/INDEX.md                          one row per run
```

A run file sits directly below `runs/`. The folder a converted run came from is provenance of the
store and not part of the export: the file names its test set in its header, and its own name is a
label derived from the axes, so nothing reads a folder back.

An export is a **plan** first: one entry per run, and nothing is written while any entry is refused.

| Action | When |
| --- | --- |
| `write` | The run file is not there yet. |
| `unchanged` | The committed file holds exactly this run. |
| `replace (grew)` | The committed file holds the first answers of this run, so the run only grew. |
| `replace (rescore)` | `--rescore` was given and the numbers were measured against another key or another scorer. |
| `stale` | The numbers were measured against another key or another scorer, and `--rescore` was not given. |
| `skipped (incomplete)` | The run holds fewer answers than its set has texts, and `--partial` was not given. |
| `refused` | Writing the run would lose something: another run of the same name, a set that was not read, or a committed file holding answers this run does not. |

A run file is written once: a second export of the same run says `unchanged` rather than merging. A
copied manifest is compared by what it pins, not by its bytes: another `question_sha256` is another
set and is refused, because a committed set is never edited; the same question with a higher
`key_version` replaces the committed manifest, because the same runs are scored against a better
key; a lower or equal `key_version` with another `key_sha256` is refused, because the key moved
without saying so.

Two things in an export are derived and are therefore replaced: a copied manifest, as above, and a
run file whose numbers were measured against another key or another scorer. The second needs
`--rescore`; without it the export lists the run and leaves it alone. `export.json` is merged, not
rewritten: the sets of an earlier export stay, so exporting one set does not hide the runs of the
others. `INDEX.md` is written even when a run file fails, so an aborted export never leaves a run
file the list does not name.

### `export.json`

```json
{
  "format": 1,
  "exported": "2026-11-17",
  "scorer_version": 1,
  "testsets": {
    "gec-2026-12": {
      "manifest_sha256": "<sha256>",
      "question_sha256": "<sha256>",
      "key_sha256": "<sha256>",
      "texts": 31
    }
  }
}
```

| Key | Meaning |
| --- | --- |
| `format` | `1`. |
| `exported` | The day the export was made, not the day of the runs. |
| `scorer_version` | The scorer that computed every number in the export. |
| `testsets.<id>` | What pins the set: the hash of the copied manifest, the hash of its question, the hash of its key, and how many texts it holds. |

A run is placed by three facts: the set it was asked about, the key its numbers were measured
against, and the scorer that computed them. A run that disagrees with the export on any of them is
skipped with a reason, so a report never mixes numbers that were measured against different keys; a
run whose key or scorer moved says so and names `export --rescore`.

### The redacted run

The same shape as a run file — header, session and answer lines — with `format: 3`, two extra
header fields and `scores` in place of `output`:

| Header field | Content |
| --- | --- |
| `scorer_version` | The version of `tools/evaluation/report/score.py` that computed the numbers. |
| `key_sha256` | The key the numbers were measured against, beside `input.question_sha256`. |

A reader that finds either missing refuses the file rather than showing numbers nobody can place.
The two are typed fields of the header and not extras, so a reader cannot mistake a run file for an
export.

A redacted answer line:

| Field | Content |
| --- | --- |
| `format`, `type`, `text`, `text_sha256` | As in a run file. |
| `key_part_sha256` | The hash of this text's key group, so a key that moved for one text makes only the runs holding that text stale. |
| `scores` | Every number of the answer, flat: the counts below. |
| `edits` | The model's changes as `[start, end, field]`, the span in **characters** of the source and the field `{"r": text}` or, when the caps forbid the text, `{"h": hash, "n": words}`. |
| `notes` | One entry per note: `edit` (index into `edits`, or `null`), `category`, `explanation`, `explanation_sha256` (`null` when the text is stored in full), `orphan`. |
| `failed`, `fallback`, `warning`, `unclear_count`, `requests`, `loaded`, `shared`, `vram_baseline_mb`, `vram_peak_mb`, `pieces`, `seconds` | As in a run file. |

**Positions are character offsets** into the stored text, end exclusive, in every stored record.
Token indices exist only inside the scorer, so a tokenizer change moves no stored data.

**The score numbers.** They are counts, not shares: a language's figures are pooled over its texts,
so a share computed per text and then averaged would not be the same number.
`tools/evaluation/report/score.py` computes all of them, at export time and again in a report of the
store, so the two cannot drift apart.

| Key | Meaning |
| --- | --- |
| `tp`, `fp`, `fn` | Changes the answer made that the reference makes too, changes it made that the reference does not, and changes the reference makes that the answer does not. |
| `explained`, `needed` | Changes whose note explains them in non-blank words, and changes that need an explanation at all (a comma does not). |
| `orphans` | Notes that explain no change in the text: the model claimed an edit it did not make. |
| `category_checked`, `category_agreed` | Errors the answer fixed exactly with a note, whose corpus label maps to a category, and of those how many notes carry that category. |
| `unchanged` | Already-correct sentences that came back unchanged. |
| `unclear` | Unclear expressions the review listed. |
| `marks_added` | Whether the answer carries Markdown marks the source does not have. |
| `accepted` | Answer edits that match an accepted alternative of the key. |
| `words_out`, `words_ref` | The words of the answer and of the reference (of the source for a `correct` text). |
| `cut_run`, `added_run` | The longest deleted and the longest inserted run of the answer that no reference change overlaps. |
| `gleu_c`, `gleu_r` | Tokens of the model's text and of the reference. |
| `gleu_m1` … `gleu_m4`, `gleu_p1` … `gleu_p4` | Per n-gram order: the model's n-grams the reference has, minus the ones the source has and the reference removed, floored at 0 per text; and `max(c + 1 − n, 0)`, the n-grams the model's text could have. |
| `gleu_src_c`, `gleu_src_m1` … `gleu_src_m4`, `gleu_src_p1` … `gleu_src_p4` | The same for the unchanged source, the floor a model has to beat. `r` is the same as the model's, so it is not repeated. |
| `tp_h1`, `fn_h1`, `tp_h2`, `fn_h2` | Exact matches and misses of the reference's changes in the first and in the second half of the text. A change belongs to the first half when its start lies before the middle word. |
| `located`, `ref_edits` | Of the reference's changes, how many the model touched at all, and how many there are. |
| `alone`, `alone_of` | Of the sentences the reference leaves alone, those the model's diff leaves alone too, and how many that is. Only for a text whose record has `sentence_spans`. |
| `cover`, `cover_of` | Of the sentences some reference change touches, those some model change touches too, and how many that is. Coverage is "looked at", not "fixed". Only with `sentence_spans`. |

A `correct` text has the words only: it has no reference correction, and its `unchanged` already
says what it is for.

### The caps

`tools/evaluation/store/redaction.py` decides, field by field, whether it may be stored as text. A
**word** is a word or a mark of the app's tokeniser; whitespace is not counted.

| Cap | Value |
| --- | --- |
| `WORD_RUN` | 5 — a field may not copy five consecutive words of the item's source or reference. |
| `MAX_REPLACEMENT_WORDS` | 8 — a longer replacement is stored as `{"h": hash, "n": words}`. |
| `MAX_EXPLANATION_CHARS` | 600 — a longer explanation is shortened and marked with `…`. |
| restricted corpus | An item of a `restricted` corpus stores no words at all: replacements as hashes, explanations as `""` with their hash kept. The hash is an HMAC-SHA256 under a key made from the corpus files, so a dictionary cannot reverse it. |

The comparison is lenient the way a reader would be: marks are dropped and case is folded.
Punctuation is a word of its own, which makes the rule stricter than a word-only count and therefore
safe.

## `INDEX.md`

Generated by `bazel run //tools:publish -- index` from the run files themselves; `index --check`
fails when it is stale. One row per run: run, date, machine, model, digest, one column per axis the runs of the
export differ in, test set, texts, mean F0.5, seconds per 100 words, scorer, and the SHA-256 of the
file.

The axis columns are the varying axes of the compare key, printed with
`core/axis_table.py:axis_label`, so a sweep of temperatures has a `temperature` column reading
`temperature 0.4` and a run of one setup has none. The model, its digest and the test set have
columns of their own and are not repeated as axes. The machine is `axes.host_of`, the one place the
fallback to the provider lives.

Mean F0.5 is the mean over the deciding languages, German and English (`errors` items only,
`correct` items have no precision to speak of); French is reported, not averaged, and a language
where nothing was scored has no F0.5 at all. Seconds per 100 words is scaled by the manifest's word counts of the items, so it is
comparable across run lengths. A *word* is `redaction.word_count` of the source — words **and**
punctuation marks — which is what `items[].source.words` holds, so the index and the local
performance table divide by the same number. Numbers of different scorers are never compared.

## `marks/<marker>__<question_sha256>.jsonl`

A teacher's marks of a run's explanations, written by
`bazel run //tools:store -- marks SHEET.md --marker <name>`. The file holds no text: the sheet does,
and it is not committed. One file per marker and set, named by the marker and the set's question
hash, so a mark of another set is refused.

| Field | Content |
| --- | --- |
| `format` | `3`. |
| `kind` | `edit` or `explanation`: the two questions a marker answers. |
| `text` | The text's id, `<language>/<record id>`. |
| `span` | Character span of the source the edit replaces. |
| `replacement` | The text the edit puts there. |
| `value` | For an `edit` mark: `valid`, `wrong` or `vague`. For an `explanation` mark: `correct`, `wrong` or `vague`. |
| `explanation_sha256` | The hash of the explanation the mark is about; required for an `explanation` mark, forbidden for an `edit` mark. |

`(kind, text, span, replacement, explanation_sha256)` is a mark's identity, so a mark survives a
tokenizer change and only a moved hunk boundary can invalidate it — which the reader lists, never
drops. The explanation's hash is part of it because a verdict on an explanation is about one model's
text: two models that wrote the same explanation share the verdict, and two that wrote different
ones do not. An `edit` mark keeps the three-part identity, because an edit is the same edit whoever
explained it.

The kind is what makes `wrong` unambiguous: an `edit` mark's `wrong` refuses the edit, an
`explanation` mark's `wrong` says the sentence does not explain it. `correct` (true, and fits the
change) and `vague` (true but too general to help a student) are the spot check's verdicts, and only
`correct` counts. `valid` is a correction the annotators did not make but a person judged right, and
it is what `accept-marks` turns into key data; only an `edit` mark can say it.

Two producers write this file: `store marks` from a filled-in sheet, and `store import-blind-key`
from the old blind sheet, which maps its token positions to characters once.

The file is merged, not replaced: marking a second run's sheet keeps the first run's marks, a mark of
the same identity is replaced, and the lines are written sorted by identity, so two writers of the
same marks produce the same bytes.

The sheet's sample is the run's, not a cache's: from the run of repeat 1, per deciding language, the
notes of `page`-band texts that explain an edit, at most two per text, drawn with a seed made from
the run key. Every machine draws the same one, and the sheet's header carries the run key beside the
name, so a renamed file draws the same sample.

## What validation checks

`bazel run //tools:store -- validate --store <store>` checks the store's files; `validate <run file>`
checks single files, and a committed one is recognised by its `scores`. The rules live in
`tools/evaluation/store/validate.py`.

**A test set:**

* the ids are unique, and `kind` is `errors` or `correct`;
* an `errors` text has a reference, a `correct` text its sentences;
* every span — a label, a sentence, an accepted alternative — lies inside the text and on token
  boundaries;
* every `unchanged_sentences` index is one of the spans;
* every label is still an edit of the reference — a label whose edit moved is **listed**, never
  dropped.

**A manifest:** `question_sha256` matches the records, the items and the records name each other both
ways, and every `text_sha256` and `source.sha256` is the record's.

**A run file:** the question hash is the set's, an Ollama run names its machine (or says the machine
was assumed), the fingerprint is in the table, no text is answered twice, and every answer's text is
in the set with the right `text_sha256`.

**A committed run file** additionally needs `scorer_version` and `key_sha256`, and its stored fields
are checked against the caps.

**A marks file:** the text exists and the span lies on token boundaries; a mark whose edit no stored
answer makes any more is listed.

**Strict mode** (with the corpora named, `--corpus NAME=PATH` or `--corpora FILE`) adds the corpus
file hashes and a full rebuild of every item, and applies the caps against the real source and
reference.

Nothing here repairs a file. A problem is a string a person reads, and the command decides whether to
refuse.

## Reading a run back

`tools/evaluation/core/records.py:read_run_file(lines)` returns a `RunFile`: the header, the sessions
and the answers as they are, and refuses an unknown format version and a line that is neither a
session nor an answer. `tools/evaluation/store/redacted.py:read_redacted_file(path)` returns a
`RedactedRun` the same way, and refuses a line that holds an `output` — that is a run file, not an
export.

`tools/evaluation/report/load.py:load(root)` pairs every answer of the store with the record it
names, scores it, and returns `LoadedItem`s; `load_export(root)` does the same for an export, taking
the numbers the file stored and placing each answer in the manifest the export copied. A file or an
answer that cannot be used is skipped with a reason, so a report can say "23 / 31 texts" instead of
pretending a run is complete. Level 1 works on those numbers alone. Level 2 (`resolve` the pointer,
check the hashes, apply the edits) is what the rebuild does; an edit stored as `{h, n}` cannot be
rebuilt and stays at level 1.

## The score cache

`report --store … --cache <dir>` keeps the numbers it computed in
`<dir>/<run key hash>.scores.jsonl`, one line per answer, so a second report of the same store scores
nothing again. A line is used only when its `answer_sha256`, its `key_part_sha256` and its
`scorer_version` are the current ones; anything else is scored again, so a stale cache costs time and
never a wrong number. The file is named by the run key, so two runs of one compare key on two
machines have two files, and a flush writes every line it read plus the new ones, in one step.

## Version history

| Version | Change |
| --- | --- |
| 1 | 2026-11-09. The first format: `machines/`, `testsets/`, `runs/`, `INDEX.md`; the caps of `redaction.py`; format and strict validation. Superseded by version 2; no run file of it is committed. |
| 2 | 2026-11-17. The store and its export: `testsets/<id>/{manifest.json,<language>.jsonl,accepted.jsonl}`, `runs/<folder>/<name>.run.jsonl`, `marks/<marker>__<question_sha256>.jsonl`, `export.json`. Positions are character offsets. The header holds the axes under `protocol` and the machine under `environment`; `eval_version` is looked up from the fingerprint and no longer covers scoring, which has its own `scorer_version`; the redacted header also records `key_sha256`. `request_profile`, `kind`, `output_sha256`, the per-reference outcome list, `machines/` and `marks/<run-id>.json` are gone. |

The marks file has a version of its own, because it is not a run:

| Version | Change |
| --- | --- |
| 1, 2 | The spot check's marks, with one value list for every mark. Superseded; no marks file of them is committed. |
| 3 | 2026-11-20. A mark has a `kind` (`edit` or `explanation`) and the value list of its kind, so `wrong` on an edit and `wrong` on an explanation are two different verdicts. `import-blind-key` writes edit marks from the old blind sheet. |

The machines a run names are no longer a file of this directory: `evaluations/machines/` holds one
JSON file per machine, and `machine` is an id from it.
