# Gate-1 check of the writing models, 2026-11-07

The structured-output check (`tools/verify_model_catalog.py --structured-output`) run against
seven writing models, 15 requests each. It decides the catalog's `explanations ✓` badge.

| File | Content |
| --- | --- |
| `model-check-results-2026-11-07.md` | What was run, the verdict per model, and how the explanation rule was derived. |
| `model-check-raw-2026-11-07.txt` | The tool's raw output, one block per model and fixture. |

Both files carry **numbers and verdicts only**: no learner text, no model output. The test set
itself (Falko-MERLIN for German, W&I+LOCNESS for English) is not redistributable and is not part
of this repository; `tools/prepare_gec_testset.py` rebuilds it from the published corpora. Keep
that separation when adding results here — counts and scores go in, texts stay out.

The check needs a running Ollama server (see `docs/development.md`, *Ollama model catalog*).
