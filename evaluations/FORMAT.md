# The format of the evaluation records

Format version **2** for a run file, a test-set record and a manifest; **3** for a marks file, whose
marks have a kind; **1** for a machine file; **3** for a size record. Every file carries its
`format`, and a reader refuses any version it does not know rather than guessing. The history is at
the end.

The rules the format serves:

| # | Requirement | How the format meets it |
| --- | --- | --- |
| 1 | A committed run can be **checked** later: same texts, same request, same weights, same machine | the question hash, the key hash, the protocol fingerprint, the instructions hash, the model digest, the seed and the machine are in the header |
| 2 | A committed run can be **re-scored** with a metric that did not exist when it ran | a run file holds the model's output and the app's own review as returned, and no score, so any scorer can read it; the score cache records the `scorer_version` of the numbers it holds |
| 3 | A run can be **read without the corpora** | the store holds the texts beside the runs, so a clone reports without downloading a corpus |
| 4 | The record lives in a **private repository** | the texts come from corpora that may not be passed to a third party; this repository holds the code and the formats, never a record |
| 5 | Runs are **evidence**: immutable | a run file is written once and never merged; git is the history |

Reading a run is possible at two levels. **Level 1** needs the file and the store's texts: the
answer is scored from what the file holds. **Level 2** needs the corpora: the texts are rebuilt from
the pointers and the stored edits are applied, so the scoring can be recomputed from the source.

Everything is plain JSON with sorted keys, so `git diff` shows what changed and a run is one file.

---

## The four versions

They are easy to mix up, and only one of them groups runs:

| Name | Says | Where | Who changes it |
| --- | --- | --- | --- |
| `format` | The layout of a JSON file: which fields exist | A run file, a test-set record and a manifest (`2`); a marks file (`3`); a machine file (`1`); a size record (`3`); `protocol.json` carries `schema: 1` | Whoever changes the format |
| `eval_version` | Which requests a run measured: prompts, answer format, window | `tools/evaluation/core/protocol.json`, looked up from the header's `protocol.fingerprint` | Raised by hand; `evaluate run` refuses to start when the fingerprint is not in the table |
| `scorer_version` | How the numbers were computed | `SCORER_VERSION` in `tools/evaluation/report/score.py`, recorded in the score cache | Raised by hand when a number changes; the golden numbers of the tests fail until it is |
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
<store>/machines/<id>.json                        one machine, named by its id
<store>/model-fit/sizes.jsonl                     one size record per model, machine, device, window
<store>/gates/<record>.md                         a gate's verdict and the runs it rests on
<store>/cache/                                    the score cache, disposable and git-ignored
```

| Path | Holds |
| --- | --- |
| `testsets/<id>/<language>.jsonl` | The texts of one language: source, reference, labels, spans. Corpus text, so it stays in the private repository and is never published. |
| `testsets/<id>/manifest.json` | The pointers and hashes that pin the set; no text. |
| `testsets/<id>/accepted.jsonl` | The corrections a marker called valid, which the key accepts as a second annotator's fix. Optional. |
| `runs/<folder>/<name>.run.jsonl` | One run: header, sessions, one answer per text. Holds the model's output, so it stays in the private repository. |
| `marks/<marker>__<question_sha256>.jsonl` | One marker's verdicts on one set, named by the marker and the set's question hash. |
| `machines/<id>.json` | One machine: its card, processor, memory, OS and driver stack. |
| `model-fit/sizes.jsonl` | What each model needs on the card, per machine, device and window. |
| `gates/<record>.md` | A gate's verdict, the runs it rests on and the command that reproduces the figures. |
| `cache/` | The score cache. Disposable, git-ignored. |

`<folder>` is the folder a converted run came from (`results-exchange`). It is provenance and not
part of the format: a run file names its test set in its header, and its own name is a label derived
from the axes, so nothing reads the folder back.

The store is the private repository `teachers-teammate-evals`. The tools default to a clone at
`~/git/teachers-teammate-evals` and refuse a store inside this repository's working tree: nothing of
the store is committed here.

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
| `key_version` | Raised by hand when the key changes: the same runs, scored against a better key. A manifest of a higher version replaces one of a lower version; the same version with another `key_sha256` is refused. |
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
`key_version`, which is what lets a new manifest replace the old one instead of being refused.

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
are only meaningful against the key they were measured with, and a run records that key.

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
records the first; a report reads both from the store.

## `runs/<folder>/<name>.run.jsonl`

One JSON object per line. Line 1 is the header, then one line per session, then one answer per text.
The file name is a label derived from the axes and is never read back.

### The file name

```
<date>_<host>_<model-slug>_<digest8>__<tag>__…__<key8>.run.jsonl
```

* `date` — `YYYY-MM-DD` of the first session;
* `host` — the machine id, an id of the store's `machines/`; for a run of a provider other than
  `ollama`, which ran on no machine of ours, the provider name stands here; `unknown-machine` when
  neither is known (`axes.host_of`, the one place that fallback lives);
* `model-slug` — the tag in lower case with a leading `hf.co/` removed and `/` and `:` replaced by
  `-` (`gemma4:12b` → `gemma4-12b`);
* `digest8` — the first eight hex digits of the model digest, `unknown` when the server gave none;
* `tag` — one per axis that differs from its default (`steps-correct+explain`, `compact_json-true`,
  `num_ctx-16384`, `sampling.top_k-20`, `explainer-<slug>`), then `temperature-<t>` and
  `repeat-<n>`, which always get one;
* `key8` — the first eight hex digits of the compare key's hash.

A reader that parses a name is a bug: a name cannot carry a new axis. `store convert` refuses a name
that already exists; `--label` tells two runs of one configuration apart.

### The header

| Field | Content |
| --- | --- |
| `format`, `type` | `2`, and `"run"`. |
| `task` | `proofread`. A different task gets its own scorer. |
| `input` | `testset` (the manifest's id), `question_sha256`, `texts` (how many answers follow), `languages`. |
| `protocol` | The axes, each stored once under a readable name — see below. |
| `run` | `seed`, `repeat`, `timeout`. Not axes: the seed is derived from the repeat, so repeats would never pool. |
| `model_info` | The tag's `digest`, `quantization`, `parameter_size`, `family`. |
| `environment` | `machine`, `ollama_version`, `isolated`, `gpu_sampler`, and `machine_source` when an empty machine was filled in. `machine` is an id of the store's `machines/`, the registry `evaluate run --machine` and `verify_model_catalog fit --machine` check; a run and a size measurement of one machine are joined by it. |

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
hashes what went into the request, so a run whose prompt has since changed is recognisable.

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

`store marks` writes this file from a filled-in sheet.

The file is merged, not replaced: marking a second run's sheet keeps the first run's marks, a mark of
the same identity is replaced, and the lines are written sorted by identity, so two writers of the
same marks produce the same bytes.

The sheet's sample is the run's, not a cache's: from the run of repeat 1, per deciding language, the
notes of `page`-band texts that explain an edit, at most two per text, drawn with a seed made from
the run key. Every machine draws the same one, and the sheet's header carries the run key beside the
name, so a renamed file draws the same sample.

## `machines/<id>.json`

One machine, named by its id. A run and a size record name a machine by this id, so a typo is
refused instead of becoming a second machine nobody can compare with the first.

```json
{
  "format": 1,
  "id": "rx9070xt-server",
  "gpu": {"vendor": "amd", "name": "AMD Radeon RX 9070 XT", "vram_mb": 16304},
  "cpu": "Intel Core i5-13600KF, 14 cores (6 P + 8 E) / 20 threads",
  "ram_gib": 31,
  "os": "Ubuntu 24, Linux 7, bare metal",
  "accelerator_stack": "ROCm 10.1.0, amd-smi 27.1.0",
  "notes": "Ollama on the same machine; the evaluation tool runs on another one.",
  "aliases": ["rx9070xt", "rx9070xt-cpu"]
}
```

| Field | Meaning |
| --- | --- |
| `format` | `1`. |
| `id` | The machine's id: the file name, and every record's `machine`. |
| `gpu` | `vendor`, `name` and `vram_mb` of the card; `null` for a machine without one. |
| `cpu`, `ram_gib` | The processor and the main memory. |
| `os`, `accelerator_stack` | The operating system and the driver stack. Ollama's API cannot report them, and a difference between two backends is only readable with them. |
| `notes` | What a reader has to know to compare two machines. |
| `aliases` | The labels the records used before the registry. Documentation, not ids: a command line that names one is refused like any other unknown machine. |

A machine is not a device: one machine is measured on its card and on its processor, and a size
record keeps the two apart.

## `model-fit/sizes.jsonl`

What each model needs on the card, one JSON object per line, append-only. Numbers only.

```json
{"format": 3, "measured_at": "2026-11-13T19:04:11Z", "machine": "rx9070xt-server",
 "device": "gpu", "ollama_version": "0.40.15", "tool_commit": "abc1234",
 "tag": "gemma4:e4b", "digest": "sha256:…", "num_ctx": 16384,
 "answer_tokens": 172, "generation_seconds": 2.4,
 "options": {"temperature": 1.0, "num_ctx": 16384}, "load_seconds": 4.2,
 "ollama": {"context_length": 16384, "size_bytes": 6012345678, "size_vram_bytes": 6012345678},
 "card": {"agent_sha256": "…", "source": "sysfs", "total_bytes": 17095983104,
          "idle_bytes": 715128832, "peak_bytes": 7301234688, "end_bytes": 7299137536},
 "ram": {"total_bytes": 33285996544, "idle_available_bytes": 30100000000,
         "min_available_bytes": 29200000000}}
```

| Field | Meaning |
| --- | --- |
| `format` | `3` since the machine and the device. |
| `measured_at` | When the request ran, UTC. |
| `machine` | An id of `machines/`. |
| `device` | `gpu` for the card, `cpu` for the processor (`--cpu-only`). A size measured on the card says nothing about the processor, so the two are never pooled. |
| `ollama_version` | The server's version. A different version is a different backend, so a record only matches a run of the same version, and a report marks a record of another version. |
| `tool_commit` | The commit that made the record. |
| `tag`, `digest` | The model, and the build of it. A tag that is pushed again gets a new digest, and its old records no longer match. |
| `num_ctx` | The window the request asked for. |
| `answer_tokens`, `generation_seconds` | The answer (Ollama's `eval_count`) and the time the model took to write it (`eval_duration`, load excluded). The speed is their quotient, computed when read; an answer under 20 tokens gives none. |
| `options` | The options the request sent, i.e. the app's own proofreading request at that window. |
| `load_seconds` | What a reload costs. |
| `ollama.context_length` | The window Ollama really loaded (`/api/ps`). Smaller than `num_ctx` when the model's own maximum is smaller, and then the ladder stops. |
| `ollama.size_bytes`, `size_vram_bytes` | Ollama's own figures. `size_vram` below `size` means part of the model is in main memory. |
| `card.idle_bytes` | The card's use after the server was emptied and had settled, before the load. |
| `card.peak_bytes` | The highest sample while the window was open. |
| `card.end_bytes` | The sample at close, with the model still loaded. |
| `card.total_bytes` | The card's total, so a peak can be read as a share of it. |
| `ram.*` | Main memory: total, available when idle, and the lowest available during the window. |

`card` and `ram` are `null` for a record made with `--no-agent`. Every byte figure is a raw byte
count; the reports divide by 10⁹ for GB, the same unit as the catalog's sizes, so a figure can be
compared with `memory_gb` without a conversion.

**Nothing derived is stored.** The card's increase (`peak - idle`), the need
(`max(ollama size, card increase)`), the growth per 8k and the tier windows are computed when the
file is read, so a change of the rule costs no new measurement.

## `gates/<record>.md`

A gate's verdict, the runs it rests on and the command that reproduces the figures. **No figure**:
the numbers are in the runs, and a record that copied them would go stale the first time a scorer
changed.

| Part | Content |
| --- | --- |
| Verdict | Which models passed, which were dropped, and why. |
| Runs | The run files the verdict rests on, by name. |
| Command | The `bazel run //tools:report -- …` line that prints the figures again. |

A gate-1 record says which models answered the app's format and which did not; a gate-2 record says
which passed the floors and which won the comparison. Both are written once the runs they read are
in the store, so a record never names a run that is not there.

## What validation checks

`bazel run //tools:store -- validate --store <store>` checks the store's files; `validate <run file>`
checks single files. The rules live in `tools/evaluation/store/validate.py`.

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

**A marks file:** the text exists and the span lies on token boundaries; a mark whose edit no stored
answer makes any more is listed.

**Strict mode** (with the corpora named, `--corpus NAME=PATH` or `--corpora FILE`) adds the corpus
file hashes and a full rebuild of every item.

Nothing here repairs a file. A problem is a string a person reads, and the command decides whether to
refuse.

## Reading a run back

`tools/evaluation/core/records.py:read_run_file(lines)` returns a `RunFile`: the header, the sessions
and the answers as they are, and refuses an unknown format version and a line that is neither a
session nor an answer.

`tools/evaluation/report/load.py:load(root)` pairs every answer of the store with the record it
names, scores it, and returns `LoadedItem`s. A file or an answer that cannot be used is skipped with
a reason, so a report can say "23 / 31 texts" instead of pretending a run is complete. Level 1 works
on the store's texts alone. Level 2 (`resolve` the pointer, check the hashes, apply the edits) is
what the rebuild does.

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
| 1 | 2026-11-09. The first format. Superseded by version 2; no run file of it is in the store. |
| 2 | 2026-11-17. The store: `testsets/<id>/{manifest.json,<language>.jsonl,accepted.jsonl}`, `runs/<folder>/<name>.run.jsonl`, `marks/<marker>__<question_sha256>.jsonl`. Positions are character offsets. The header holds the axes under `protocol` and the machine under `environment`; `eval_version` is looked up from the fingerprint and no longer covers scoring, which has its own `scorer_version`. `request_profile`, `kind`, `output_sha256`, the per-reference outcome list, `machines/` and `marks/<run-id>.json` are gone. |

The marks file has a version of its own, because it is not a run:

| Version | Change |
| --- | --- |
| 1, 2 | The spot check's marks, with one value list for every mark. Superseded; no marks file of them is committed. |
| 3 | 2026-11-20. A mark has a `kind` (`edit` or `explanation`) and the value list of its kind, so `wrong` on an edit and `wrong` on an explanation are two different verdicts. |

The machines a run names are not a file of this directory: the store's `machines/` holds one JSON
file per machine, and `machine` is an id from it.
