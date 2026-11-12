# Writing models: gate-1 records and candidate research

The structured-output check (`tools/verify_model_catalog.py structured-output`) decides the
catalog's `structured_output` flag, which the app shows as the `explanations ✓` badge. Its records
go here, one results file and one raw file per run.

The first measurements (2026-11-07 to 2026-11-11) were made while the method was still being
worked out. They were deleted on 2026-11-12 so that the records start again under the settled
method and answer format 1.

| File | Content |
| --- | --- |
| `candidate-models-2026-11-09.md` | Which models were looked at for the next comparison, which were dropped and why, what is not yet researched, and which statements are unverified. Its tables of earlier scores come from the deleted measurements. |

Gate 2's records go here too, as `gate2-<date>.md`: the output of `proofreading_store gate` and
`compare` for every model of a screen, and the pick it rests on.  They are written once the runs
they read are committed, so a record names the run ids it was made from.

These files carry **numbers and verdicts only**: no learner text, no model output. The test set
itself (Falko-MERLIN for German, W&I+LOCNESS for English) is not redistributable and is not part
of this repository; `tools/prepare_gec_testset.py` rebuilds it from the published corpora. Keep
that separation when adding results here — counts and scores go in, texts stay out.

The check needs a running Ollama server (see `docs/development.md`, *Ollama model catalog*). The
answers it saves stay outside the repository; `verify_model_catalog recheck` re-judges them without
a server.
