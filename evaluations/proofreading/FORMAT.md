# The format of a stored proofreading run

Schema version **1**. Every file below carries `schema: 1`; a reader refuses any other version
rather than guessing. The history is at the end.

The rules the format serves:

| # | Requirement | How the format meets it |
| --- | --- | --- |
| 1 | A committed run can be **checked** later: same texts, same request, same weights, same machine | the manifest hash, the eval version and its fingerprint, the instructions hash, the model digest, the seed and the machine id are in the header |
| 2 | A committed run can be **re-scored** with a metric that did not exist when it ran | the model's edits are stored as positions plus short replacements, not only the scores |
| 3 | A committed run can be **read without the corpora** | scores, per-error outcomes and performance are numbers; every report works on them |
| 4 | The repository holds **no corpus sentence** | hashes, positions, label codes, numbers and capped model words only; `validate` enforces it |
| 5 | Runs are **evidence**: immutable and listed | append-only files, `INDEX.md` with checksums, a test that regenerates it |

Reading a run is possible at two levels. **Level 1** needs nothing but the file: the numbers are
there. **Level 2** needs the corpora: the texts are rebuilt from the pointers and the stored edits
are applied, so the scoring can be recomputed.

Everything is plain JSON with sorted keys, so `git diff` shows what changed and a run is one file.

---

## The three versions

They are easy to mix up, and only one of them groups runs:

| Name | Says | Where | Who changes it |
| --- | --- | --- | --- |
| `schema` | The layout of a JSON file: which fields exist | Every run, manifest and machine file (`1`) | Whoever changes the format |
| `eval_version` | What a run measured and how: prompts, answer format, window, scoring | `tools/model_eval/protocol.json`, copied into every run header | Raised by hand; `run` refuses to start when it was forgotten for the request side |
| `tool_revision` | The commit the tools were at | Every run header | Nobody: git. **Information only** — nothing checks it, groups by it or refuses because of it |

Runs are grouped and compared by `eval_version`, never by commit.

`protocol.json` records every version with its date, its *fingerprint* and a line saying why it was
raised:

```json
{
  "schema": 1,
  "current": 1,
  "versions": [
    {
      "version": 1,
      "date": "2026-11-10",
      "fingerprint": "<sha256>",
      "why": "First version: review and text mode, num_ctx 8192, long texts in pieces as in the app."
    }
  ]
}
```

The fingerprint is the SHA-256 of the request side, which the tool computes itself
(`protocol.fingerprint()`) by *capturing* it from the app's own corrector: for every answer mode and
language, a hash of the one request the corrector sends (system and user message, answer schema,
options without the run's temperature and seed) and the text budget the app splits a long text by;
plus the repair message and the answer format version. Because it is captured, not listed, a new
request setting is covered without anyone editing a list. `run` compares it with the entry of
`current` before the first request and exits with code 2 when they differ — an uncommitted prompt
edit is caught the same way as a committed one. Printing `protocol.fingerprint()` before and after a
change shows which part moved.
A change of the scoring, the caps or the edit extraction does not move the fingerprint; the golden
numbers of the tests fail instead and remind you to raise the version by hand. Old entries are never
edited or removed, so every committed run stays checkable against the version it names.

---

## `machines/<machine>.json`

Written by hand once per test machine. The export reads it to check that the id exists and that no
required value is still a placeholder; the run header copies the id.

```json
{
  "schema": 1,
  "id": "rx9070xt-server",
  "gpu": {"vendor": "amd", "name": "AMD Radeon RX 9070 XT", "vram_mb": 16304},
  "cpu": "unknown, not recorded",
  "ram_gib": 31,
  "os": "Ubuntu 24, Linux 7, bare metal",
  "accelerator_stack": "ROCm 10.1.0, amd-smi 27.1.0",
  "notes": "Ollama on the same machine; the evaluation tool runs on another one."
}
```

| Key | Type | Meaning |
| --- | --- | --- |
| `schema` | integer | Format version, `1`. |
| `id` | string | The machine id; must equal the file name (`<id>.json`). |
| `gpu` | object | `vendor` (`amd`, `nvidia`, `metal`), `name` as the driver reports it, `vram_mb`. A `name` of `TODO` or an empty one is refused. |
| `cpu` | string | The processor, as precisely as you care to write it. `TODO` or an empty value is refused. |
| `ram_gib` | number | Main memory in GiB. |
| `os` | string | Distribution and kernel, and whether it is bare metal or virtualised. `TODO` or an empty value is refused. |
| `accelerator_stack` | string | Driver, runtime and sampling tool with versions. `TODO` or an empty value is refused. |
| `notes` | string | Anything a reader of a run needs: what else ran on the machine. |

A new machine is a new file; a machine that changes its card or its driver stack is a new machine
id, because a speed figure does not carry over.

## `testsets/<testset>/manifest.json`

Says *which* texts the items are, and what a correct rebuild must look like. It holds no corpus text.

```json
{
  "schema": 1,
  "testset": "gec-2026-12",
  "built": "2026-11-10",
  "selection_seed": 20261110,
  "builder_revision": "8c45408",
  "corpora": {
    "falko-merlin": {
      "licence": "Falko CC BY 3.0, MERLIN CC BY-SA 4.0",
      "restricted": false,
      "files": {"fm-test.m2": "<sha256>", "source/falko-id-ctok-zh1.txt.test": "<sha256>"}
    },
    "wi": {
      "licence": "Cambridge W&I: non-commercial research, excerpts under 100 words",
      "restricted": false,
      "files": {"json/A.dev.json": "<sha256>", "m2/A.dev.gold.bea19.m2": "<sha256>"}
    }
  },
  "items": [
    {
      "id": "de-01", "language": "de", "kind": "errors", "corpus": "falko-merlin",
      "band": "short",
      "pointer": {"origin": "merlin", "text": "1061_0120359", "sentences": [0, 14]},
      "source": {"sha256": "<sha256>", "words": 114, "chars": 538},
      "reference": {"sha256": "<sha256>"},
      "sentence_spans": [[0, 14], [15, 27], [28, 46]],
      "unchanged_sentences": [0, 11, 13],
      "edits": [{"start": 21, "end": 22, "h": "<sha256>", "words": 1, "label": "R:DET:FORM"}]
    }
  ]
}
```

| Key | Meaning |
| --- | --- |
| `testset` | The test-set id; the directory name and every run's `testset`. |
| `built` | The day the builder made it. |
| `selection_seed` | The seed of the shuffles, so the same selection can be made again. The recipe is `tools/model_eval/recipes/<testset with _>.py` at `builder_revision`. |
| `builder_revision` | The commit of `tools/prepare_gec_testset.py` that built it, `+dirty` when the tree had uncommitted changes. |
| `corpora.<name>` | The name of an adapter in `tools/model_eval/corpora/`; `licence`, `restricted`, and the `sha256` of every corpus file the items come from. |
| `items[].id` | The item id, unique in the set. Run files use it; local result rows prefix the language (`de/de-04`). |
| `items[].language` | A code of `tools/model_eval/languages.py` (`de`, `en`, `fr` today). |
| `items[].kind` | `errors` (a text with a reference correction) or `correct` (sentences that must stay untouched). |
| `items[].corpus` | Which corpus; keys correspond to `corpora`. |
| `items[].band` | `short`, `page` or `pages`, the band the recipe drew the text for; absent when the recipe has none. Bands count whitespace-separated words of the source (`short` 80 to 150, `page` 200 to 400, `pages` 600 to 1,200), not `source.words`, which also counts punctuation marks: a `short` text can record `words` above 150. |
| `items[].pointer` | What the builder chose, in the corpus's own terms — see below. |
| `items[].source` | `sha256` of the text as built, its `words`, and `chars`, the length of the source in characters, as the app measures it against its budget. |
| `items[].reference` | `sha256` of the corrected text; absent for a `correct` item. |
| `items[].sentence_spans` | Token spans `[start, end)` of every sentence, in the positions of `edits`. |
| `items[].unchanged_sentences` | Indices into `sentence_spans` of the sentences the reference leaves alone: the corpus calls the sentence unchanged (no edit and, for W&I, no `UNK` annotation) **and** `quality.untouched_spans` finds no change of the reference's word diff in or at the edge of it. A sentence untouched by the diff alone is not listed if the corpus saw an error in it. |
| `items[].edits` | The reference's changes: `start`, `end` (token positions), `h` (SHA-256 of the replacement's words joined by single spaces; a deletion hashes the empty string), `words`, `label`. |
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
by hash, so an edited manifest would make them uncheckable.

## `runs/<testset>/<run-id>.jsonl`

One JSON object per line. Line 1 is the header, every other line one item. The file name is the run
id, and the header repeats it; a renamed file is refused.

### The run id

```
<date>_<machine>_<model-slug>_<digest8>_<mode>[_<explainer>]_T<t>_r<n>
```

* `date` — `YYYY-MM-DD` of the run;
* `machine` — the machine id;
* `model-slug` — the tag in lower case with `/` and `:` replaced by `-` and a leading `hf.co/`
  removed (`gemma4:12b` → `gemma4-12b`);
* `digest8` — the first eight hex digits of the model digest, `unknown` when the server gave none;
* `mode` — `review`, `text` or `two-step`;
* `explainer` — the explaining model's slug, only in `two-step`;
* `T<t>` — the temperature (`T0.7`; `T0` for 0);
* `r<n>` — the repeat, or a `--label` when two runs of one configuration would collide.

Two runs of the same configuration on one day differ in `r<n>` (or in `--label`). An export refuses
a run id that already exists.

### The header

| Field | Content |
| --- | --- |
| `schema`, `type` | Format version `1`, and `"run"`. |
| `run_id` | The id above; equal to the file name. |
| `testset`, `manifest_sha256` | Which items, pinned by the hash of `manifest.json`. |
| `machine` | The machine id; the server URL is not stored. |
| `kind` | `quality` (other things were allowed on the server) or `speed` (isolated, sampled). |
| `date`, `tool_revision` | When, and at which commit. The revision is a pointer for a human reader; nothing checks it. |
| `eval_version`, `fingerprint` | The protocol the run measured and the SHA-256 of its request side, both copied from `protocol.json` as they were at run time. |
| `ollama_version`, `model`, `model_info` | The version, the tag, and the tag's `digest`, `quantization`, `parameter_size`, `family`. |
| `mode`, `explainer`, `input` | `review`, `text` or `two-step`; the explaining model; `model` or `reference`. |
| `temperature`, `num_ctx`, `seed`, `repeat` | The request. |
| `request_profile` | The model's request profile as the app resolved it: `compact_json` (the
  compact-answer rule in the fixed part of a review request) and `temperature` (`null` when the
  profile leaves it to the app's default).  Two runs of one tag with different profiles are two
  requests, so gate 2 never pools them. |
| `instructions_sha256`, `answer_format_version` | Hash of the policy text of every language in the run plus the answer format, **recorded at run time**, so a prompt changed since stays visible. |
| `isolated`, `loaded_before`, `gpu_sampler`, `vram_baseline_mb` | The protocol: idle start, what was loaded, which sampler, the graphics memory before the model was loaded. |
| `cut_fields` | How many fields the caps had to cut. |
| `texts` | How many item lines follow. |

`instructions_sha256` is the SHA-256 of the policy and answer format of each language, in
alphabetical order of the language names (`English`, `French`, `German`), joined by `\n\n---\n\n`,
each as `## <language name>\n<policy>\n\n<fixed>`. The comparison asks for the language by name and
sends plain text, so the preset of that language is hashed, with the default level and the answer
format of the run's `mode`. It hashes what went into the request, so a run whose prompt has since
changed is recognisable — the export copies the hash the run recorded and never recomputes it.

### The item

| Field | Content |
| --- | --- |
| `item` | The id from the manifest. |
| `outcome` | `failed`, `fallback`, `has_review`, `warning_code`, `pieces`, `seconds`. |
| `scores` | The required numbers `tp`, `fp`, `fn`, `unchanged`, `explained`, `needed`, `orphans`, `category_checked`, `category_agreed`, `marks_added`, `notes_linked`, the numbers of the table below, and any further key whose value is a number. A new metric needs no schema change, and a number cannot carry corpus text, so requirement 4 holds for a key nobody has seen yet. |
| `reference` | One entry per reference edit of the manifest, same order: `[exact, located, category_given or null]`. |
| `edits` | The model's edits as `[start, end, replacement]`; the replacement is `{"r": "text"}` or, when the caps forbid the text, `{"h": sha256, "n": words}` (for an item of a `restricted` corpus `h` is an HMAC-SHA256 under a key made from the corpus files, so a dictionary cannot reverse it). |
| `notes` | One entry per note: `edit` (index into `edits`, or `null`), `category`, `explanation`, `explanation_sha256` (`null` when the text is stored in full), `orphan`. |
| `unclear` | How many unclear expressions the model listed. |
| `output_sha256` | SHA-256 of the source with the edits applied. |
| `performance` | `requests`, `loaded`, `shared`, `vram_peak_mb`, `vram_baseline_mb`, `seed`, `repeat` (as `performance.py` records them), or `null` for a run that recorded none. |

**Positions.** `edits` are over the app's tokens of the source (`tokenize`, whitespace tokens
included), exactly as `quality.ordered_edits` produces them: `start` and `end` are token
indices, `end == start` for an insertion, and the replacement is the text between them. Applying
them to the rebuilt source, tail first, gives the model's text without the whitespace-only
differences the scoring already ignores. That is what `output_sha256` checks.

**`warning_code`** is `""`, `fallback`, `review_partial` (only part of the text got a review answer),
`unplaced` (the model named a correction it could not place in the text) or `other`. A text that was
sent in pieces joins the warnings of its pieces, so the codes are looked for inside the warning, and
the more specific one wins: `review_partial`, then `fallback`, then `unplaced`, then `other`.
`review_partial` means some pieces fell back to corrections only and the others kept their
explanations (`fallback` and `has_review` are both true); `fallback` means the whole text fell back
(`fallback` true, `has_review` false). A failed text stays `failed: true`, whatever its warning says.
The warning's *text* is not stored, because it is the model's words.

**`pieces`** is how many pieces the app splits the text into: its long-text path splits a text over
the window's budget at paragraph and sentence ends, and a text within the budget is one piece,
whatever it holds. For a text that failed, the pieces after the failed one were not sent. A run of
the comparison therefore records how often a model was asked more than once for one text.

**The score numbers.** They are counts, not shares: a language's figures are
pooled over its texts, so a share computed per text and then averaged would not be the same number.
`item_scores.item_scores` computes all of them, at run time and again in `rescore`, so a run and a
report cannot drift apart.

| Key | Meaning |
| --- | --- |
| `gleu_c`, `gleu_r` | Tokens of the model's text and of the reference. |
| `gleu_m1` … `gleu_m4` | Per n-gram order: the model's n-grams the reference has, minus the ones the source has and the reference removed, floored at 0 per text. |
| `gleu_p1` … `gleu_p4` | Per n-gram order: `max(c + 1 − n, 0)`, the n-grams the model's text could have. |
| `gleu_src_c`, `gleu_src_m1` … `gleu_src_m4`, `gleu_src_p1` … `gleu_src_p4` | The same for the unchanged source, the floor a model has to beat. `r` is the same as the model's, so it is not repeated. |
| `tp_h1`, `fn_h1`, `tp_h2`, `fn_h2` | Exact matches and misses of the reference's changes in the first and in the second half of the text. A change belongs to the first half when its start lies before the middle word. `tp_h1 + tp_h2 = tp` and `fn_h1 + fn_h2 = fn`. |
| `alone`, `alone_of` | Of the sentences the reference leaves alone, those the model's diff leaves alone too, and how many that is. Only for an item whose manifest entry has `sentence_spans`. |
| `cover`, `cover_of` | Of the sentences some reference change touches, those some model change touches too, and how many that is. Coverage is "looked at", not "fixed". Only with `sentence_spans`. |

A `correct` item has none of them: it has no reference correction, and its `unchanged` and
`sentences_total` already say what it is for.

**`reference`** at level 1 lets a report say which kinds of error a model found without the corpora.
`category_given` is `null` when it cannot be judged: the model fixed no such error, or the corpus
label maps to no category.

## `INDEX.md`

Generated by `proofreading_store index` from the run files themselves. One row per run: id, date,
machine, model, digest, mode, explainer, temperature, repeat, kind, texts, mean F0.5, seconds per 100
words, eval version, and the SHA-256 of the file. A test regenerates it and compares: a run added
without the index, or a file edited afterwards, fails.

Mean F0.5 is the mean over the languages that have scored texts (`errors` items only, `correct` items
have no precision to speak of); a language where nothing was scored has no F0.5 at all and is left
out of the mean. Seconds per 100 words is scaled by the manifest's word counts of the items, so it is
comparable across run lengths. A *word* is `redaction.word_count` of the source — words **and**
punctuation marks — which is what `items[].source.words` holds, so the index and the local
performance table divide by the same number.

## `marks/<run-id>.json`

A teacher's marks of a run's explanations, written by `proofreading_store marks SHEET.md`.  The
file holds no text: the sheet does, and it is not committed.  One file per run; a second marker adds
`marks/<run-id>.<n>.json` later, without a schema change.

| Field | Content |
| --- | --- |
| `schema`, `type` | `1`, and `"marks"`. |
| `run_id` | The run whose explanations were marked. |
| `date`, `marker` | When, and who. |
| `sample_seed` | The seed the sample was drawn with, so a mark of another sample is refused. |
| `marks` | One entry per marked note: `item`, `note` (the index in the item's stored notes), `explanation_sha256` (the hash of the explanation the teacher read) and `mark`. |

`mark` is `correct` (true, and fits the change), `wrong` (false, or it explains a change that is
itself wrong) or `vague` (true but too general to help a student).  Only `correct` counts.

`validate` checks a marks file against its run: the run exists, the sample seed is the run's, every
`(item, note)` is in that run's sample, and every `explanation_sha256` is the one the run stored.  A
mark of another sample, or of an explanation that changed since, is refused.

## What validation checks

Two modes of one function, `tools/model_eval/validation.validate_run(path, corpora=None)`:

**Format mode** (no corpora; the test suite runs it over every committed file):

* line 1 is a header of schema 1 with `type: "run"`, and `run_id` is the file name;
* every header and item field present with the right type, and no unknown field;
* `eval_version` is a positive integer and `fingerprint` a SHA-256, and the pair is an entry of
  `protocol.json` — a run made with an unrecorded protocol is refused;
* `mode` is one of the answer modes, and `explainer` is set only for `two-step`;
* `outcome.fallback`, `has_review` and `warning_code` do not contradict each other: `review_partial`
  needs a fallback and a review, `fallback` a fallback and no review, and a fallback is one of the
  two;
* `texts` equals the number of item lines;
* `manifest_sha256` is the hash of the manifest of the run's test set, and `testset` is its id;
* every item of the manifest has a line, and no line names an item the manifest does not have;
* the number of `reference` outcomes equals the manifest's edits for that item;
* a stored replacement has at most 8 words, and a note text at most 600 characters;
* a `restricted` item holds no `r` and no explanation text;
* no *string* sits under a field named `source`, `reference` or `output`, and no string under a field
  name the format does not know has more than 8 words — a sentence has nowhere to hide;
* a note's `edit` index points at an edit of the same item.

**Strict mode** (with corpora; the export always runs it) adds: the five-word rule against the real
source and reference of every item, and the `source` and `output_sha256` hashes.

The export never *writes* a file that strict mode refuses: it cuts the field instead and records how
many fields it cut in the header's `cut_fields`.

## Reading a run back

`tools/model_eval/run_format.read_run(path)` returns a `StoredRun`: the header and the item lines
as they are, and refuses an unknown schema version and a header that does not match the file name.
`StoredRun.items(manifest)` types them — one `StoredItem` per line, with the manifest entry of each —
and a `StoredItem` satisfies `scoring.ScoredItem`, so `report.summarise` builds the same table from a
committed run as from a local cache. Level 1 works on those numbers alone. Level 2 (`resolve` the pointer, check the hashes, apply the edits) is what the rebuild
does; an item whose edit is stored as `{h, n}` cannot be rebuilt and stays at level 1.

## Version history

| Version | Change |
| --- | --- |
| 1 | 2026-11-09. The first format: `machines/`, `testsets/`, `runs/`, `INDEX.md`; the caps of `redaction.py`; format and strict validation. |
| 1 | 2026-11-10, before the first committed run. `eval_version` and `fingerprint` in the header, `outcome.pieces`, the warning code `review_partial`; `tool_dirty` removed (the revision is information only); the index column `dirty` became `eval`. No version bump: no run file was committed yet. |
| 1 | 2026-11-10, before the first committed run. The fingerprint of eval version 1 was replaced: it is now captured from the app's own corrector (user message, answer schema and repair message included) instead of listed by hand. The warning codes `review_partial` and `fallback` mean "some pieces" and "the whole text", and the validator checks them against `has_review`. No version bump: no run file was committed yet. |
| 1 | 2026-11-10, before the first committed run. `scores` may carry further numeric keys (a new metric needs no schema change); the header's `mode` must be one of the answer modes, and `explainer` only with `two-step`. No version bump: no run file was committed yet. |
| 1 | 2026-11-10, before the first committed run. "Per 100 words" is defined: `redaction.word_count` of the source, words and punctuation marks, the figure `items[].source.words` already holds. No version bump: no run file was committed yet. |
| 1 | 2026-11-10, before the first committed run. `legacy/` and the `legacy` key removed: the conversion was dropped, so the format no longer carries its parts. No version bump: no run file was committed yet. |
| 1 | 2026-11-10, before the first committed run. An optional `split` in a pointer; a pointer without it means the corpus's default split, so older manifests read unchanged. No version bump: no run file was committed yet. |
| 1 | 2026-11-10, before the first committed run. Optional manifest keys `band`, `source.chars`, `sentence_spans` and `unchanged_sentences`. A manifest without them reads unchanged. No version bump: no run file was committed yet. |
| 1 | 2026-11-11, before the first committed run. The header's `request_profile` (the model's
  compact-JSON rule and temperature), so two requests of one tag never pool. No version bump: no
  run file was committed yet. |
| 1 | 2026-11-11, before the first committed run. `marks/<run-id>.json`, the teacher's marks of a
  run's explanations, and its check in `validate`. No version bump: it is a file of its own. |
| 1 | 2026-11-11, before the first committed run. `scores` carries the GLEU counts of the model and of the source, the recall by half, and left alone and coverage for an item with `sentence_spans`. No version bump: the keys are numbers under a field the format already allows. |
