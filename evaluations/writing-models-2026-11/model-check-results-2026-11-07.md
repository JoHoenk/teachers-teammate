# Structured-output check: results of 2026-11-07

Gate 1 of [writing-models-and-proofreading-defaults-plan.md](writing-models-and-proofreading-defaults-plan.md) (WP1/WP2), run with `tools/verify_model_catalog.py`. Raw output: [model-check-raw-2026-11-07.txt](model-check-raw-2026-11-07.txt).

## What was run

| | |
| --- | --- |
| Server | Ollama 0.40.13 on Windows, reached from WSL; an 8 GB graphics card; whether the 14B, 12B and 20B models fit it entirely was not checked (they were slow, which suggests partial offload to the processor; offload changes speed, not answers) |
| Request | the app's real review request: review system prompt (level B2, text language), user message, JSON schema as `format`, `think=False` |
| Options | temperature **0.7** (the app's default), `num_ctx` 8192; the tag's own settings otherwise (`top_k`, `top_p`, penalties) |
| Fixtures | plain EN, plain DE, plain FR (2 sentences each), Markdown (3 sentences with formatting marks), full page DE (invented, ~350 words) |
| Repeats | 3 per fixture, **15 requests per model**; a model passes only if all 15 pass |
| Timeout | 300 s per request in run 1, 900 s in run 2 |

A run **fails** if, in this order: the answer was cut off at the context limit (`done_reason=length`); the request failed, the answer is not valid for the schema, lists no sentences, or leaves the text unchanged; fewer than 90 % of its sentences can be placed in the text; or fewer than 0.50 (the value used in this first run; later revised, see below) of the changes that need an explanation carry a note with text.

**"Explained"** = of the word-diff changes between the student text and the model's corrected text, the share that carries a matched note with non-blank text (spelling/punctuation-only changes without a note are not counted). It checks that an explanation exists, not that it is right. **Orphans** = notes that match no change in the model's own text; reported, not part of the verdict. **This check does not measure whether corrections or explanations are correct**; that is gate 2.

## Result per model

| Model | Role | Runs passed | Explained (all runs) | Lowest run | Full page: answer tokens | Full page: time here |
| --- | --- | --- | --- | --- | --- | --- |
| `gemma4:e4b` | 8 GB candidate | **15/15** | 0.90 | 0.60 | 2478 | 34 s |
| `ministral-3:8b` | 8 GB candidate | **14/15** | 0.82 | 0.40 | 3935 | 147 s |
| `qwen3.5:9b` | current default (official tag) | **14/15** | 0.86 | 0.71 | 3351 | 82 s |
| `unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL` | unsloth build of the default | **11/15** | 0.76 | 0.00 | 3255 | 108 s |
| `gemma4:12b` | 16 GB candidate | **15/15** | 0.94 | 0.67 | 1596 | 72 s |
| `ministral-3:14b` | 16 GB candidate | **15/15** | 0.92 | 0.50 | 2547 | 433 s |
| `gpt-oss:20b` | old default / 16 GB | **11/15** | 0.95 (11 short runs) | – | – | – |

*Explained (all runs)* is the total of explained over needed; the full-page runs dominate it. *Lowest run* is the worst single-run ratio among completed runs (short fixtures have 3 to 7 changes, so one change moves it by about 0.2). Times are for this 8 GB card and not comparable between models that fit it and models that do not.

## Every run that failed

| Model | Fixture | Run | Verdict | Detail |
| --- | --- | --- | --- | --- |
| `ministral-3:8b` | plain EN | 1/3 | too few explained | 2/2 placed, 2/5 explained, 463+322 tok, stop, 19s |
| `qwen3.5:9b` | Markdown | 1/3 | valid answer, but it lists no sentences | 0/0 placed, 0/0 explained, 565+623 tok, stop, 16s |
| `unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL` | plain DE | 1/3 | too few explained | 1/1 placed, 1/3 explained, 521+426 tok, stop, 17s |
| `unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL` | plain FR | 3/3 | too few explained | 2/2 placed, 0/5 explained, 520+119 tok, stop, 4s |
| `unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL` | Markdown | 2/3 | too few explained | 3/3 placed, 0/1 explained, 565+431 tok, stop, 15s |
| `unsloth/Qwen3.5-9B-GGUF:UD-Q4_K_XL` | Markdown | 3/3 | too few explained | 3/3 placed, 1/3 explained, 565+370 tok, stop, 12s |
| `gpt-oss:20b` | plain EN | 1/3 | request failed: Ollama's llama-server crashed (CUDA error, exit status 0xc0000409), a Windows-side fault, not an answer problem | no answer, 26 s |
| `gpt-oss:20b` | full page DE | 1/3 | request failed (model returned an empty response) | no answer after 344 s (the 300 s limit); by hand the same request ends at the window limit, see below |
| `gpt-oss:20b` | full page DE | 2/3 | request failed (model returned an empty response) | no answer after 343 s (the 300 s limit); by hand the same request ends at the window limit, see below |
| `gpt-oss:20b` | full page DE | 3/3 | request failed (model returned an empty response) | no answer after 344 s (the 300 s limit); by hand the same request ends at the window limit, see below |

## Further tests outside the check

| Test | Result |
| --- | --- |
| `gpt-oss:20b`, full page, by hand (0.7, `think:false`, 8192) | `done_reason=length` after 984 + 7208 tokens; 27,758 characters of hidden reasoning, 3,150 characters of cut-off answer. Fails on any GPU at this context size |
| `ministral-3:8b`, prompt of 2,360 tokens with `num_ctx` 2048 | silently cut to 1,027 evaluated tokens, reported as `stop` |
| `ministral-3:8b`, answer longer than the window | reported correctly as `length`, prompt + answer = `num_ctx` exactly |
| same, `truncate=false` | HTTP 400 "exceeds the available context size"; the `ollama` Python client 0.6.3 cannot send it |
| `qwen3.5:9b`, Markdown fixture replayed 8 times by hand | no empty answer reproduced; 3 of 8 put sentences into the `unclear` list |

## Reading the results

- Passing all 15: `gemma4:12b`, `ministral-3:14b`, `gemma4:e4b`. One failed run: `qwen3.5:9b` (valid answer, no sentences) and `ministral-3:8b` (2 of 5 explained). The unsloth build of the same Qwen model fails 4 runs; it ships none of the official tag's sampling settings.
- The explanation rule was revised after this table: a floor of 0.40 per run plus 0.75 over all runs of a model (see `docs/development.md`). The first idea, a threshold just below the default (about 0.70), would have failed every candidate, because the short fixtures have only 3 to 7 changes and one change moves a ratio by about 0.2. With the revised rule `ministral-3:8b` no longer fails on explanations.
- Not measured: Hugging Face (unsloth) builds other than Qwen3.5-9B. Temperature 0.2 and correction quality follow in the sections below.

## What the model is asked to write (measured on the full page)

The answer repeats text. Each changed sentence is written twice (`original` as the anchor, `corrected` as the replacement), and each `changes[]` entry then repeats the changed words again (`original`, `corrected`), plus category and explanation. Share of the answer's characters in one full-page answer per model:

| Part of the answer | `gemma4:e4b` | `qwen3.5:9b` |
| --- | --- | --- |
| sentence `original` + `corrected` | 38 % | 28 % |
| change `original` + `corrected` | 7 % | 5 % |
| change `explanation` | 12 % | 17 % |
| change `category` | 1 % | 2 % |
| `unclear` entries | 3 % | 4 % |
| JSON keys, quotes, braces, indentation | 40 % | 44 % |
| answer tokens (21 / 22 sentences, 16 / 29 changes) | 2,497 | 3,625 |

Indentation is counted in the last row but costs fewer tokens than characters. The same fact is stored in two places (the corrected sentence and the list of changes), and the app has to reconcile them with a word diff: that is where orphans (notes without a change) and unexplained changes come from. So the "explained" figure partly measures how consistent the two copies are, not only whether the model explains.

## Additions after the first check (same day)

### Gate 1, further runs

| Model | Temperature | Runs passed | Failed run |
| --- | --- | --- | --- |
| `qwen3:4b` | 0.7 | 14/15 | plain FR run 2: valid answer, no sentences (12 tokens). Explained over all runs 0.81 |
| `qwen3.5:9b` | 0.2 | 14/15 | full page run 1: only 22 of 26 sentences placed (85 %). Explained over all runs 0.88 |

With the revised explanation rule (floor 0.40 per run, 0.75 over all runs) no run failed on explanations any more; the failures left are the empty answer (`qwen3.5:9b` at 0.7, `qwen3:4b`) and one placement miss.

### Gate 2: quality on public learner corpora

Method, test set and limits: `docs/development.md`, section "Choosing writing models". Eight texts and ten correct sentences per language; German Falko-MERLIN, English W&I+LOCNESS, French invented by an AI model.

| Model | T | F0.5 de | F0.5 en | F0.5 fr | Mean | Left alone de/en/fr | Added marks |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `gemma4:12b` | 0.7 | 0.47 | 0.46 | 0.97 | **0.635** | 5/4/10 | 0/27 |
| `gemma4:12b` | 0.2 | 0.46 | 0.45 | 0.97 | **0.628** | 5/5/10 | 0/27 |
| `gemma4:e4b` | 0.2 | 0.46 | 0.40 | 0.98 | **0.612** | 4/4/10 | 0/27 |
| `gemma4:e4b` | 0.7 | 0.49 | 0.40 | 0.94 | **0.611** | 3/5/10 | 0/27 |
| `qwen3.5:9b` | 0.2 | 0.33 | 0.37 | 0.87 | **0.527** | 0/4/10 | 0/27 |
| `qwen3:4b` | 0.7 | 0.42 | 0.37 | 0.67 | **0.486** | 6/5/8 | 0/27 |
| `ministral-3:14b` | 0.7 | 0.37 | 0.36 | 0.69 | **0.476** | 0/5/8 | 17/27 |
| `qwen3.5:9b` | 0.7 | 0.28 | 0.27 | 0.77 | **0.440** | 0/0/10 | 0/27 |
| `ministral-3:14b` | 0.2 | 0.39 | 0.36 | 0.56 | **0.434** | 3/3/4 | 17/27 |
| `ministral-3:8b` | 0.2 | 0.31 | 0.26 | 0.67 | **0.411** | 2/2/7 | 1/27 |
| `ministral-3:8b` | 0.7 | 0.23 | 0.20 | 0.65 | **0.358** | 2/2/9 | 7/27 |

No review failed and none fell back to corrections only (0 of 27 for every row). Markdown marks a model adds to the proofread text (`**bold**`, written by both Ministral models) are not counted as errors in these scores; "Added marks" only reports how often they occur. Counting them (`report --count-marks`) lowers `ministral-3:14b` to 0.240 / 0.222 and `ministral-3:8b` at 0.7 to 0.275.

### Provisional reading of explanations (an AI model, not a teacher)

Ten explanations per language and model (sample `spotcheck-T0.2.md`, kept outside the repository), judged on one standard: is the stated reason true and not misleading?

| Model | German | English | French |
| --- | --- | --- | --- |
| `gemma4:e4b` | 9 correct, 1 vague | 9 correct, 1 vague | 10 correct |
| `gemma4:12b` | 9 correct, 1 vague | 8 correct, 2 vague | 10 correct |
| `qwen3.5:9b` | 8 correct, 2 vague | 8 correct, 1 wrong, 1 vague | 6 correct, 2 wrong, 2 vague |

A teacher's marks are still needed for rule R1/R3 ("at least 8 of 10 correct per language").

### Does the model fit the graphics card at 8192 tokens

8 GB RTX 3070 Laptop, nothing else on the card (baseline 0 MiB). Share of the model Ollama keeps on the card at 4096 / 8192 tokens: `qwen3:4b` 100/100 %, `gemma4:e4b` 100/100 %, `qwen3.5:9b` 100/88 %, `ministral-3:8b` 92/84 %, `ministral-3:14b` 61/56 %. For `gemma4:12b` Ollama reports about 1 GB whatever the window; `nvidia-smi` shows 6.9 GB in use.

### Speed and memory per model at 8192 tokens (RTX 3070 Laptop 8 GB, 47 GB main memory)

| Model | Tokens per second | Video memory | Free main memory used | Load seconds |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | 97.9 | 3.8 GB | 0.8 GB | 4.5 |
| `gemma4:e4b` | 76.9 | 4.4 GB | 1.1 GB | 6.0 |
| `qwen3.5:9b` | 42.7 | 6.4 GB | 1.3 GB | 10.0 |
| `ministral-3:8b` | 31.6 | 6.2 GB | 1.5 GB | 9.5 |
| `gemma4:12b` | 13.2 | 6.9 GB | 3.6 GB | 13.9 |
| `ministral-3:14b` | 6.6 | 6.3 GB | 4.5 GB | 12.8 |

About 1 GB of "free main memory used" is loading overhead that every model has. `gemma4:12b` and `ministral-3:14b` run partly on the processor.
