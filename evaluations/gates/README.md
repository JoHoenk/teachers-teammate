# The gates: what a model has to pass before it enters the catalog

Two gates decide a model's place in `teachers_teammate/domain/model_catalog.py`. Gate 1 is the
structured-output check: does the model answer in the app's format at all, and does it explain? It
decides the `structured_output` flag, which the app shows as the `explanations ✓` badge. Gate 2 is
the proofreading comparison: does the model correct and explain well enough, measured against the
floors in `tools/evaluation/report/gates.py`?

| File | Content |
| --- | --- |
| `candidate-models-2026-11-09.md` | Which models were looked at for the next comparison, which were dropped and why, what is not yet researched, and which statements are unverified. Its tables of earlier scores come from measurements that were deleted on 2026-11-12. |

## What is not here yet

**Gate-1 records.** The local records of 2026-11-11 were made under a protocol that is no longer in
`tools/evaluation/core/protocol.json`, and they hold the models' answers. They are not committed:
the records start again under the current protocol, and then they are numbers and verdicts only.

**Gate-2 records.** `gate2-<date>.md` is written after the first export: the output of
`report gate` and `report compare` for every model of a screen, and the pick it rests on. It is
written once the runs it reads are committed, so a record names the run ids it was made from.

## Running the gates

```bash
bazel run //tools:verify_model_catalog -- smoke --tag TAG --machine ID --answers DIR
bazel run //tools:report -- gate --store DIR --model TAG
bazel run //tools:report -- compare --store DIR --along temperature
```

`smoke` sends gate 1's cheapest fixtures and judges the answers; `--answers` keeps them outside the
repository, and `verify_model_catalog recheck` re-judges them without a server. `gate` judges one
model against gate 2's floors, `compare` puts the groups of a store side by side and marks what is
beyond the noise.

These files carry **numbers and verdicts only**: no learner text, no model output. The test set
itself (Falko-MERLIN for German, W&I+LOCNESS for English) is not redistributable and is not part of
this repository; `tools/prepare_gec_testset.py` rebuilds it from the published corpora. Keep that
separation when adding results here — counts and scores go in, texts stay out.

The checks need a running Ollama server (see `docs/development.md`, *Ollama model catalog*).
