# Model evaluation tools

How a writing model earns a place in the app's catalog (`domain/model_catalog.py`): first its
size is measured, then **gate 1** checks that it fills in the review format reliably, then
**gate 2** compares the quality of its corrections. The tools live in this folder and in
`tools/verify_model_catalog.py`, `tools/compare_writing_models.py`,
`tools/prepare_gec_testset.py`, `tools/proofreading_store.py` and `tools/eval_report.py`; their
records go to `evaluations/` (overview: `evaluations/README.md`).

What the app itself does with the results — the catalog, the ratings, the request each model is
sent — is in `docs/development.md` ("Ollama model catalog", "Request profiles: temperatures and
prompts per model").

## Gate 1: can a model fill in the review format?

`tools/verify_model_catalog.py --structured-output` decides the catalog's `structured_output`
flag (the `explanations ✓` badge).

The check sends the app's request — `proofreading_options()` (see `docs/development.md` → "Soft failures and per-file
warnings") — at the temperature the app sends that model: step ① at its measured value
(`STEP_ONE_TEMPERATURES`, 0.4 for every model not listed there), steps ② and ③ at the value the
model's maker recommends (`MAKER_TEMPERATURES`: the card's value for Gemma, Qwen's non-thinking
row, Ministral's "below 0.1", Mistral NeMo's 0.3, and 0.2 where a maker states nothing). Both
tables are in `teachers_teammate/domain/model_catalog.py` (teachers set no temperature; the app
reads them), and `--temperature` overrides step ① for every
model named, to compare one model at two temperatures with `compare`. It covers six fixture texts — plain English, plain German,
plain French, Markdown with formatting marks, one invented full page of dense German prose (~350
words), and one invented German page of 6,500 characters that is sent as the longest prefix of
whole sentences the app's own budget allows. German tokenises longest of the three languages, so
the two long fixtures are the worst case for the size the app sends.

**The size is the app's, not half the window.** The check measures the request it sends itself
(`structured_check.review_budget`, with the prompt taken out of the window first and the rest
shared with an answer seven times the text), and a test pins that request to the one-request
corrector's (`tools/model_eval/one_request/correction.py:NativeOllamaCorrector.max_text_chars`),
so the gate and the baseline cannot disagree about what fits. At 8192 tokens that is about 2,790 characters
of German, at 16384 about 5,860. A fixture that is longer than its budget is reported as too
long instead of being sent; CI keeps the shipped fixtures within it.

A run is judged in this order: too long for the window, request failed, cut off (`done_reason` is
`length` — a cut-off answer is cut off whether or not what arrived still validates), answer not
valid JSON for the schema, no sentences, nothing changed, then the thresholds: at least 90 % of
its sentences can be placed in the text, at least 80 % of a long fixture's error sentences are
touched and at least one of those that start in its last third, and at least
`CheckRule.explained_floor` (0.40) of the changes that need an explanation carry one with
non-blank text. The coverage verdict is `ANSWERED ONLY PART OF THE TEXT` (soft): a model that
stops halfway through a page is not a model that cannot fill in the format, but it is not one to
recommend either.

At a temperature above 0 every fixture runs three times, so 18 runs. A model passes with at most
`CheckRule.allowed_failed_runs` (1) soft failure in those 18; with exactly `CheckRule.retry_at`
(2) the check runs 18 more and passes with at most `CheckRule.allowed_after_retry` (4) over both
rounds. At temperature 0 a repeat would be the same answer again, so each fixture runs once and
the second round never happens. Any hard failure — cut off, request failed, too long — fails the
model in either round. A pass also needs the explained share over all of its runs together to
reach `CheckRule.explained_overall` (0.75): one stray miss (the empty answer the Qwen models give
about once in 15 runs) does not take the `explanations ✓` badge away, a pattern does.

Each run prints placed / explained / orphans / covered / prompt and answer tokens / margin /
`done_reason` / seconds. The **margin** is what the window had left after the request and the
answer; it is not a verdict, but the summary names the smallest one on the budget fixture and
warns below a tenth of the window, because that is a model that passes today and may not
tomorrow.

**The record and the re-run.** The command prints its own header: date, command line, tool
commit, Ollama version, window, temperature, eval version and fingerprint, and
`ANSWER_FORMAT_VERSION` — a verdict is only valid for the answer format it was made on. Per model
it adds the digest, the quantisation and the parameter size, and notes when the app would run
that model at a larger window than the one checked. `--answers DIR` saves every answer to a
JSON-lines file (they stay local: the fixtures are invented, but the answers are large), and
`verify_model_catalog recheck FILE` judges those answers again with the current rule, without a
server. It refuses a file whose fingerprint differs from the current one, and a run whose text no
longer hashes to what was sent.

**The smoke check, before the eighteen runs.** A *setup* is one model asked one way: tag, request
profile (compact JSON on or off), temperature, window and answer mode. `verify_model_catalog smoke` sends gate 1's own requests, cheap
to expensive, one run each, and stops at the first fixture that fails twice: at most ten requests,
a few seconds to a few minutes. It is gate 1's first stage, not a verdict — a pass means "no sign
of a systematic fault", never "passes gate 1".

**The size limit comes before the first request.** A setup whose model does not fit the card cannot
be judged on quality, so the check asks what the model needs at the window it is about to use (see
*Model sizes*): above `--fit-limit` (default `16gb`, `off` skips the check) the setup fails with no
request sent, and a model with no record at that window is measured on the spot, exactly as `fit`
does — unload, settle, one short request, `/api/ps` — so the screen never decides on a guess, and
the smoke check's own first request then finds the model loaded. A server that cannot be measured
is a warning, not a failure: the screen is worth more than the figure. The header of the run and of
the answers file names the limit, so a gate-1 record says under which limit it was screened.

The fixtures are plain DE, Markdown, plain EN, plain FR and the full page DE; the budget fixture
is left out because it shows the same faults as the full page at twice the cost. A fixture fails
the setup only when **the same fixture fails twice in a row** — the app's own "ask once more", so
one unlucky run never rejects a model. At a 5 % soft-failure rate per run a good model is rejected
0.25 % of the time per fixture; a setup that fails 70 % of the time or more is caught at least
49 % per fixture it fails on. Two failures a second try cannot cure fail at once: a request the
server refused, and the collapse to one sentence on the full page (fewer than 20 % of its error
sentences covered and none in the last third — `qwen3.5:9b` with the compact rule), whose line
names the likely cause and the rule to try instead. One `NO_SENTENCES`, one `CUT_OFF_ONCE`, a
fixture that needed its retry, a partial memory split and a window other than the one asked are
warnings, not failures. A request that waits for its first token in vain (`OCRTimeoutError`) is
retried like any failure and counts as a soft failure when it happens twice. The timeout (120 s)
bounds only that wait — a model that has begun to answer may take minutes — and the first request
waits 600 s longer, because it loads the model. **The window does not stop a runaway**: a model on
Ollama's llama.cpp runner shifts its context and keeps writing (`mistral:latest` wrote 81,920 tokens
in 937 s; a 3B Granite held a run for minutes on a fixture that takes 5 s), and the app sends no
`num_predict`. So `--deadline` (240 s, 600 s for the two long fixtures) bounds the whole answer:
past it the request is abandoned through `total_timeout` of `OllamaClient.chat_with_stats` (the
stream is closed, so the server stops generating), the run counts as cut off, and a setup that
runs away twice fails. `--deadline 0` sets no limit. The app itself sets no deadline and no
`num_predict`; capping the answer there would change the request and is a separate decision. Before the
first fixture the check also reports the setup itself: the
system text carries `COMPACT_ANSWER_RULE` exactly when the profile says on, and `/api/ps` shows
the window and whether the model sits on the card.

**The smoke runs are gate 1's runs.** `--answers DIR` saves them like any other answer, so
`structured-output --continue FILE` judges them with the current rule, sends only the runs still
missing (13 of 18 for a setup that passed every fixture first time) and prints one record that
holds all of it. A setup that failed the smoke check is not continued unless `--force` is given,
and never when a second try could not have cured it (a hard failure is a gate-1 FAIL as it stands;
a soft one is "no verdict"). A file that goes on past the failing fixture is an interrupted gate-1
run, not a smoke check, and is continued. A request that timed out is not saved as a run, so gate 1
asks it again under its own timeout. `--no-smoke` skips the stage, and
`compare_writing_models run` smokes each model once per mode before its first session.  The
exchange modes (`exchange`, `correct-only`) are screened with step ①'s own request on the three plain
fixtures, because a comparison sends plain text only; text mode's Markdown fixture and prompt would
skip a model for a fault the run never meets.

The window is `--num-ctx`, 8192 by default: the window the app uses for proofreading, always. A
model the automatic rule would lift above the checked window on a tier's card is noted in the
header; with proofreading capped at the base window that note never fires today, and it is the
reminder to check the larger window when the cap is raised.

**How the explanation rule was set.** The short fixtures have only 3 to 7 changes, so one change
moves a run's ratio by about 0.2, and a single threshold "just below the default" (the obvious
first choice, about 0.70) would have failed every candidate. A full check of seven models (15 runs each) gave the numbers: the then-default `qwen3.5:9b` reaches 0.86 over all runs, the weakest accepted candidate
(`ministral-3:8b`) 0.82 with a weakest run of 2 of 5 (0.40), while the unsloth build of Qwen3.5
had runs with 0 of 5 (it wrote no notes at all). So the floor sits at 0.40 and catches "wrote no
explanations"; the overall share, 0.75, does not depend on one unlucky run. Revisit both if a
model change moves them.

A verdict belongs to the **exact tag** it was measured on. The app sends only the temperature and
`num_ctx`; `top_k`, `top_p`, penalties and the chat template come from how the tag was packaged
(the official `qwen3.5:9b` ships `top_k 20`, `top_p 0.95`, `presence_penalty 1.5`; the same model
imported from a `hf.co/unsloth/…` GGUF ships none of them). The check prints the settings a tag
brings along, so a result can be traced. Check a Hugging Face build as its own tag; do not
transfer a verdict between packagings.

The check talks to `$OLLAMA_HOST` (else `127.0.0.1`); `--ollama-url` overrides it. (The app's own *Settings → This computer → Find…* finds the Windows host for you, see "Finding the Ollama server".) From WSL in
its default NAT mode `127.0.0.1` is WSL itself, so point it at the Windows host
(`ip route show default`), or set `networkingMode=mirrored` in `%USERPROFILE%\.wslconfig` and run
`wsl --shutdown` so `127.0.0.1` reaches Windows. Ollama on Windows must listen on the network
(`OLLAMA_HOST=0.0.0.0`) for the first option.

To check a candidate **before** adding it to the catalog, pass its tag; `--tag` implies
`--structured-output`. A list may mix both: a tag the catalog knows is checked against its
`structured_output` flag and counted in the tally, the others are candidates that only get a pass
or fail:

```bash
bazel run //tools:verify_model_catalog -- --tag ministral-3:8b --tag gemma4:e4b
bazel run //tools:verify_model_catalog -- --tag qwen3.5:9b --temperature 0.2
bazel run //tools:verify_model_catalog -- structured-output --answers ~/gec-testset/gate1 \
    --tag qwen3:4b --tag gemma4:e2b
bazel run //tools:verify_model_catalog -- recheck ~/gec-testset/gate1/*.jsonl
```

## Model sizes: what a model needs on the card

`OllamaModelSpec.memory_gb` and `context_gb_per_8k` say how much a model needs at 8,192 tokens and
how much it grows per further 8,192. They were estimates, or figures from an 8 GB NVIDIA card.
`evaluations/model-fit/sizes.jsonl` holds the measurements that replace them: one JSON object per
line, append-only, numbers only. `evaluations/model-fit/README.md` describes the fields and the
machines; the module is `tools/model_eval/model_fit.py`.

**One record per model, server and window.** It carries what Ollama reports from `/api/ps`
(`size`, `size_vram`, the window it really loaded) and what the card really held, from the memory
agent's window around the load (`idle`, `peak`, `end`, the card's total). Every figure is a raw
byte count; the reports divide by 10⁹, the unit of the catalog's sizes. Nothing derived is stored:
the card's increase, the need, the growth and the tier windows are computed when the file is read,
so a change of the rule costs no new measurement.

**The need is `max(Ollama's size, the card's increase)`.** Ollama's figure leaves out the driver's
and the runtime's share; the card's increase cannot exceed the physical card. Taking the larger is
cautious in both directions. A step that spills — Ollama's `size_vram` below `size`, or the card's
peak within 2 % of its total — is kept and flagged: it is a real figure, but its need is a lower
bound, so it does not feed the growth. A record made with `--no-agent` has Ollama's figure as its
need and is flagged `no card reading`.

**Measuring.** `verify_model_catalog fit` loads the model at every step of `CONTEXT_STEPS` with the
app's own proofreading options, and empties the server before each step so the model is loaded at
exactly the window asked for:

```bash
bazel run //tools:verify_model_catalog -- fit --server rx9070xt \
    --ollama-url http://gpu-server.example:11434 --gpu-agent http://gpu-server.example:11436 \
    --gpu-token-file ~/.config/gpu_agent.token --tag gemma4:e4b --tag qwen3:4b
```

The request is a copy of a short German text (about 170 tokens), long enough to read the model's
speed from: every record stores the answer tokens and the generation time (`tokens_per_second`), and
the report shows the speed next to Ollama's own split between card and processor
(`ollama_card_share`, the share the app's full-speed rule is about), so the rating's share
thresholds can be checked against measured speeds.

`--cpu-only` sends `num_gpu` 0 and measures at 8,192 tokens only: the speed of the processor, for
the tier that has no card. It needs a label of its own (the tool refuses to mix the two kinds), and
the size lookups (`latest`, `size_for_decision`, `catalog_lines`) skip such records, because the
card's share is missing from their need.

`--server` is a short label for the machine; what it stands for is written once in the folder's
README, because Ollama's API cannot report it. A step already stored for the same build and version
is skipped (`--again` measures it again). The ladder stops for a tag when Ollama loaded a smaller
window than asked for (the model's own maximum) or when the request fails; a failing tag does not
stop the next one. Exit codes: 0 every step measured or already stored, 1 a failure, 2 the server
or the agent cannot be reached.

**Reading it back, without a server.** `fit --report` prints, per model and server, the need at
each window, Ollama's figure next to the card's increase, the growth per 8k (short and long span,
flagged when not linear), the difference to the catalog's `memory_gb` (flagged above
`MEMORY_TOLERANCE`, 10 %), and the largest window that still fits a card of a given size
(`--fit-limit`, repeatable; default `8gb` and `16gb`). `--compare A B` puts two machines side by
side and marks each figure that disagrees (need within 10 %, growth within 20 % or 0.1 GB).
`--catalog` prints the two values to enter by hand, taking the larger of two servers that disagree.

**The limit is the app's own rule.** A tier's limit is `card_budget_gb(tier) / FULL_SPEED_SHARE`:
15.11 GB for `16gb`, 7.56 GB for `8gb`. `runs_at_full_speed()` in `domain/model_catalog.py` is the
one place that decides whether a need fits a card at full speed, and `context_budget` uses it, so
the screen and the app cannot disagree about a tier. A number (`--fit-limit 14.5`) is that many GB;
`off` skips the check.

**How the catalog's figures come from it.** `memory_gb` is the need at 8,192 tokens and
`context_gb_per_8k` the growth, both from `fit --report --catalog`, entered by hand in one commit
that names the store lines' dates. Until a model has a measured growth it stays at 8192. The
figures are measured on one backend and cross-checked on another; where the two disagree, the
larger one is entered, because the model has to fit the machine that needs more.

## Memory agent: what the card really holds

Ollama's `/api/ps` reports what it *thinks* it put on the card. The memory agent
(`tools/model_eval/gpu_agent.py`) reports what the card really holds, sampled from the driver, so a
measurement can say how far Ollama's bookkeeping is from the hardware. It is a small stdlib-only
HTTP server that travels alone: it is copied to the machine with the card and runs there, because
the card is on that machine and not on the one that runs the tools.

**What it reports.** `GET /info` returns the platform, the vendor and the source of the reading
(`sysfs` for AMD on Linux, `nvidia-smi` for NVIDIA), the cards with their totals, the card's use
right now, and the main memory's total and available. `POST /window` opens a window and returns its
number; `POST /window/<n>/close` returns the window's statistics: the card's use at the start, the
peak, the mean, the minimum and the end, the main memory's lowest available, the number of samples
and the seconds it was open. A window older than an hour is dropped, and at most eight are kept.

**Starting it.** On the server it runs as a user service, so it survives a logout and starts with
the machine:

```bash
python3 tools/model_eval/gpu_agent.py --token-file ~/.config/gpu_agent.token --port 11436
```

On Windows it runs in the Windows session, not in WSL: WSL's `nvidia-smi` sees the card but reports
0 MiB used. The token file holds one secret; the agent refuses every request without it
(`X-Token`), and the tools read the same file (`--gpu-token-file`). The port has to be reachable
from the machine that runs the tools, so a firewall rule for it is part of the setup. The agent
never writes anything, never loads or unloads a model, and never touches the server: it only reads
the driver and the main memory.

`AgentClient` (`tools/model_eval/agent_client.py`) is the other side: `info()`, `open_window()`,
`close_window(window)` and `settle()`, which waits until the card's use has stayed within a
tolerance for several readings in a row (and at least a short while) before a measurement
starts; the driver can give memory back a second after the server reports a model unloaded. `fit` and the smoke check's size limit are its two users.

## Gate 2: the quality comparison

A writing model must pass two gates before it is recommended.  **Gate 1** is the
structured-output check above (`verify_model_catalog.py`): can it fill in the review format
reliably?  Its first stage is the smoke check (see above): a setup that fails it is not sent the
eighteen runs, and one that passes it is continued from its own answers.  **Gate 2**
(`compare_writing_models.py`) asks how good its corrections are.  Both tools send what the app
sends, at the temperatures the app sends that model (see above; gate 2 goes through
`NativeOllamaCorrector` itself, inside the app's `ChunkingCorrector`, with the app's own tokeniser
and word diff), so a result describes the one-request baseline — including the app's long-text path: a
text longer than the window's budget is proofread in pieces, one request each, and the row records
how many (`outcome.pieces`).

**The order is measure, screen, gate 1, gate 2.**  A model that does not fit the card cannot be
judged on quality, so its size comes first: `fit` measures it (see *Model sizes*), and the smoke
check applies the limit before its first request — a model above `--fit-limit` (default `16gb`)
fails the setup without a request, and one with no record is measured on the spot.  The header of
the run and of the answers file names the limit, so a gate-1 record says under which limit it was
screened.  Only then do the eighteen runs and gate 2 follow.

```bash
# 1. Build the test set from the public corpora (kept outside the repository)
bazel run //tools:prepare_gec_testset -- --testset gec-2026-12 \
    --corpora ~/gec-testset/corpora.json --out ~/gec-testset/set-2026-12
# 2. Send every text to the models (results are appended; an interrupted run resumes)
bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set-2026-12 \
    --out ~/gec-testset/results --model gemma4:e4b --temperature 0.2
#    ... and ask for the corrected text only (no JSON, no explanations) on the same texts
bazel run //tools:compare_writing_models -- run --testset ~/gec-testset/set-2026-12 \
    --out ~/gec-testset/results --model gemma4:e4b --temperature 0.2 --mode text
# 3. Print the table / which kinds of error were found / the explanations a teacher marks
bazel run //tools:proofreading_store -- report --cache ~/gec-testset/results
bazel run //tools:proofreading_store -- report --cache ~/gec-testset/results \
    --testset ~/gec-testset/set-2026-12 --by-error category
bazel run //tools:proofreading_store -- sheet --run RUN_ID --cache ~/gec-testset/results \
    > spotcheck.md
```

**A run that is checked before it starts, and read afterwards.**  `run --dry-run` makes the same
plan the run makes and prints it: one line per session (model, temperature, mode, repeat, how many
texts it would send), the results files that already hold every text, the refusals, and the number
of requests.  It sends nothing but the server probe, which reads the version and the model's
details, so a night's work can be checked in a second; a refusal exits 2, as the run would.
`run --log FILE` appends one JSON line per event — `complete`, `session`, `smoke`, `row` (with the
row's seconds and whether it lost text) — and closes with `finished`, `refused` or `interrupted`.
Ctrl-C is how a night is paused, so a paused run still says where it stopped.  At the end a run
that lost a setup says so: `N of M planned sessions did not run:` and one line per session with the
reason the smoke check gave.  The exit code stays 0 — a script that chains runs decides for itself
what a skipped setup means, and a run that wrote most of its rows is not a failure.

**Evaluation report.** `bazel run //tools:eval_report -- build --testset ~/gec-testset/set-2026-12
--cache ~/gec-testset/results --out ~/gec-testset/reports/NAME [--zip] [--single-file]
[--model TAG] [--languages de,en]` writes one static page (opens from `file://`, no server) with the
scorecard, a heat map, the sentences of every variant next to the reference, and the error types.
Python computes every number with the report's own functions; the page only displays. A run it
cannot use (no header, another test set, sessions that disagree, an older header) is listed in the
footer with the reason. The output must lie outside a git work tree (a report holds corpus text)
and be a new or earlier-report folder. Reading a cache scores every row again, which takes minutes
on long texts; the tool prints each file as it goes. Code: `tools/model_eval/viewer/`.

**Test set.** Per language eleven whole texts with reference corrections, in three bands of
whitespace words: `short` 80 to 150, `page` 200 to 400, `pages` 600 to 1,200 — four short, five page
and two pages.  German: Falko-MERLIN GEC corpus (test split; Falko CC BY 3.0, MERLIN CC BY-SA 4.0),
MERLIN letters and Falko essays whole.  English: W&I+LOCNESS v2.1 (BEA-2019 dev split, levels A, B,
C; non-commercial use only), whole essays.  Every `pages` text is longer than the one-request review
budget at 8192 (about 2,800 characters) and is proofread in pieces, while at
least one `page` text still fits one request, so both paths are measured on real texts.  French: **no public learner
corpus with corrections exists**, so
`tools/gec_fr_invented.jsonl` holds eight invented texts and ten correct sentences written by an AI
model, not a teacher: French results are indicative only.  German and English no longer carry a
glued group of correct sentences; the sentences the reference leaves alone sit inside the texts
(`unchanged_sentences` in the manifest) and are scored as *left alone*.  The
corpora are real learner text and licensed for research, so neither they nor the built set belong
in the repository.

**The held-out set.**  `gec-2026-12` is the set a prompt, a temperature or a text budget is tuned
on, so a choice that wins there may only fit its 22 texts.  `gec-2026-12-heldout` has the same shape
and filters from splits the first never reads — Falko-MERLIN `dev` and W&I `train` — and a choice
is adopted only when it wins on both.  Build it like the first:
`prepare_gec_testset --testset gec-2026-12-heldout --corpora ~/gec-testset/corpora.json --out
~/gec-testset/set-2026-12-heldout`.  French is not part of it.  No text of those splits reaches
the 6,400 characters the app split at in eval version 1, so the two `pages` slots take texts of at
most 5,800 characters (3,272 to 4,444 in the committed set).  The current budget is about 2,800
characters, so every `pages` text of both sets is split by the app now; a test checks each
committed manifest against the budget, not against the two constants.  The recipes share their
procedure (slots, filters, draw) in `tools/model_eval/recipes/_bands.py`, but each recipe writes out
its own `Rules` (bands, character limits, edit filters): a recipe is frozen once its manifest is
committed, so a change for one must not move the other, and a test per recipe pins its draw on the
invented band corpus.  A pointer names its split only when it is not the corpus's default.  An
English item of the held-out set records the text's CEFR level (`level`, `B1`: W&I's `B1.ii`
without the sub-level) and the build copies it into `TestRecord.level`; the manifest and the record
take it from the same place, the corpus.  **No run reads the level yet**: every text is asked at
the default level, in both sets, so their results stay comparable.  Asking each text at its own
level changes the instructions every run hashes, so it is a step for both sets at once.
Falko-MERLIN carries no level.  A set whose recipe records no level (`gec-2026-12`) keeps its
manifest and its records byte for byte.

**Error labels.**  Both corpora come with an M2 file that gives every correction a label
(`R:DET:FORM`, `M:PUNCT`…).  `prepare_gec_testset.py` finds the annotated tokens again in the text
(`tools/model_eval/error_labels.py`) and stores, for every change the reference makes, the label
*code* next to it (`TestRecord.labels`; the codes only, no corpus text).  The labels let the report
ask which kinds of error a model finds.  Matching is by position, so a label is as reliable as that
match.  In the build of `gec-2026-12` (2026-11-10) all 631 German and 350 English reference
changes got a label.  On `gec-2026-11` (2026-11-09) all 118 German and 125 English changes did;
98 % (German) and 99 % (English) of the distinct positions lie directly on an annotated edit, the
rest within one word of it, and 24 labels drawn at random fitted their words on a read-through.
A test set built before the labels existed still works; the by-error report then says so.  The
French texts are invented and have no labels.  Limits to keep in mind: the reference is one
annotator's version; a few "correct" sentences contain errors the annotators missed; British
spelling in the English references makes American spelling count as an error; eleven texts per
language show only large differences.

**Metrics** (per language and band, summed over the texts).  Every number is a
count, not a share: a language's figure is pooled over its texts, so a share computed per text and
then averaged would not be the same number.  `tools/model_eval/item_scores.py` computes them for one
text, at run time and again when the report re-scores a stored row, so the two cannot drift apart.

* *Precision / recall / F0.5*: a change counts when the model's word diff against the source has a
  hunk covering the same words with the same replacement as the reference's (exact match, so
  absolute values are low for every model and only the comparison counts; F0.5 weighs precision
  twice as much as recall because a wrong change costs a teacher more than a missed one).
* *GLEU*, printed next to the GLEU of the unchanged source (`0.62 (src 0.55)`): the corpus score of
  the GEC shared tasks, computed from counts that add up over texts (`tools/model_eval/gleu.py`,
  the formula of Napoles' `compute_gleu`, one text being one line of the official script).  A model
  below its source made the text worse.  It is a guard, not a decision: it says "not lower".
* *Left alone*: of the sentences the reference leaves alone (`unchanged_sentences` in the manifest),
  those the model's diff leaves alone too, by the same rule on both sides
  (`quality.untouched_spans`).  The French `correct` item adds its `unchanged` and
  `sentences_total`, so French keeps the column it had.
* *Coverage*: of the sentences some reference change touches, those some model change touches too.
  It is "looked at", not "fixed": a different replacement still covers the sentence.  Read it
  together with left alone — a model that returns half a page leaves much alone and covers little,
  and either number alone is easy to game.
* *Recall 1st / 2nd half*: the exact matches and misses of the reference's changes in each half of
  the text, a change belonging to the half its start lies in.  It shows a model that stops early or
  drifts towards the end, which one recall figure hides.  The halves are of the text, not of the
  pieces a long text was sent in, so text mode, review mode and every budget compare alike.
* *Added marks*: texts whose proofread text contains Markdown marks (`**`, `__`, `~~`) the source did
  not have; **these marks are ignored when scoring** — the scores are computed on the text without
  them, the same text the edits, the notes and the export use, and `report --count-marks` re-scores
  the raw answer locally — the column only shows how often they occur.
* *Failed / fallback*: reviews that failed or fell back to corrections only, and how many of the
  fallbacks the window cut off (`done_reason: "length"`).  A review-mode fallback keeps its
  corrections in F0.5 and GLEU, because the teacher gets them, but its changes count as
  unexplained, so falling back cannot improve the table.
* *Lost / added*: texts whose answer kept much less of the text than the reference has, or much more
  (`tools/model_eval/text_loss.py`).  Two rules, because one alone misses a case: the words of the
  answer against the reference's (below 80 % or above 125 %), and the longest stretch of words the
  model deleted or inserted that no reference change overlaps (20 tokens or more), which catches a
  dropped sentence in a text that was otherwise corrected heavily.  The reference is the yardstick,
  so a text whose reference cuts a sentence is a text and not a defect.  A `correct` text is
  measured against its source, because it has no other number.  The report prints one line per
  flagged text and repeat below the table, so a flag is read at once; a row written before the words
  were stored shows "—", which is not the same as clean.
* *Explained*: changes that need an explanation and carry one.

A language where nothing was scored at all (no reference edit and no model edit) has no F0.5: the
table prints "—" and the mean leaves it out, instead of counting it as a zero.

**Bands and what decides.**  The report prints one row per configuration and language over all its
texts, and a second table that splits each language into the bands the recipe drew the texts for
(`short`, `page`, `pages`).  A band row is a diagnostic, not a decision: four, five and two texts per
language, so one text moves it by a lot, and the `pages` band is marked as such.  What decides is
the mean over German and English of F0.5 and of GLEU (`report.DECIDING_LANGUAGES`); French is
reported only, because it has no bands and its texts are invented.  The rule that uses these numbers
is gate 2's, and it reads the `page` band, not `pages`.

**Gate 2: the floors, the comparison and the explanations.**  Gate 2 asks two questions, and they
are two tools (`tools/model_eval/gate_two.py`):

* **The floors** (`GateTwoRule.judge`) — does this model, on its own numbers, meet the bar for being
  offered at all?  Pass or fail per model, no other model involved, no baseline needed.  Every floor
  is a hard stop, and the values are data, so a calibration changes a value and not a branch:

  | Floor | Review | Text | How it is counted | Starting value |
  | --- | --- | --- | --- | --- |
  | Failed texts | ✓ | ✓ | `failed` per language over all runs | 0 |
  | Cut-offs that cost the explanations | ✓ | ✓ | `cut_off` per language over all runs: a fallback whose requests include a `length`, so both tries were cut | 0 |
  | Other fallbacks | ✓ | — | `fallback − cut_off` per language over all runs | ≤ 1 per language |
  | Added marks | ✓ | ✓ | `added_marks` per language over all runs | 0 |
  | Texts that lost text in 2 or more repeats | ✓ | ✓ | per text id across the repeats, per language | 0 |
  | Coverage | ✓ | ✓ | Σ`cover` / Σ`cover_of` per language, pooled over the repeats | ≥ 0.90 |
  | Explained share | ✓ | — | Σ`explained` / Σ`needed` per language, pooled | ≥ 0.85 |
  | GLEU above the source | ✓ | ✓ | per language, in every run | strict |
  | Left alone | ✓ | ✓ | Σ`alone` / Σ`alone_of` per language, pooled | ≥ 0.50 |

  Events count over all runs, rates are pooled over the repeats (a rate on one repeat is noisy), and
  the GLEU guard holds in every run.  The lost-text floor is the exception: it counts a text once
  per repeat, so a sentence dropped in one of three repeats is shown by the report and not marked
  here.  A group whose rows were written before the words were stored passes it with the value
  saying "not measured".  *Left alone* is the share of the sentences the reference leaves
  unchanged that the model leaves untouched too: a model that rewrites correct sentences costs the
  teacher a check per sentence.  It pairs with coverage — a model that changes nothing leaves
  everything alone and fails coverage, one that rewrites everything covers everything and fails this
  floor.

  **Measuring and rating are two steps.**  `GateTwoRule.measure(group)` returns one check per floor
  *and language*, for every language the runs hold, and pools nothing across languages; French is
  measured like the others.  `GateTwoRule.rate(measurement, marks)` decides which of them enter the
  outcome: `GateTwoRule.deciding` is data (German and English), so a calibration on the full
  screen changes a value and not a branch, and a check of another language keeps its ✓/✗ with "(not
  deciding)".  `judge` is `rate(measure(group), marks)`, so the command line and its exit codes do
  not change, and the record is grouped by language (`## de`, `## en`, `## fr`, then the
  diagnostics), so each language reads as its own evaluation.  A group with no text in one of the
  deciding languages is `INCOMPLETE`, not a pass.  Printed with the floors, and never
  decisive: recall by half, the `pages` band, the cut-offs the second try absorbed, seconds per 100
  words.
* **The comparison** (`compare(a, b)`) — is model *a* better than model *b* beyond the noise?  It is
  used once, at the end of the screen, to fill each tier's places, and it is not a gate.  `BETTER`
  needs three things: the interval of the deciding mean F0.5 over the texts excludes zero on the
  right **and** the gap is at least `MIN_EFFECT` (0.01), and GLEU and the `page` band are not below
  the other's by more than that same 0.01.  The interval is a paired bootstrap over the texts (2,000
  draws, `random.Random(20261111)`, so every machine gets the same answer); the two guards read the
  means over the repeats, not their ranges, because at a low temperature the repeats collapse and a
  range test then passes on a gap of 0.001.  The spread of the repeats is printed next to the
  verdict and decides nothing.  The two groups may differ in model, digest, request profile and
  temperature — each model starts at its own step-① temperature, and a refinement compares one
  model at two — and in nothing else; both temperatures are printed.  It is split the same way:
  `measure_pair(a, b)` builds the per-language figures and the intervals (French's too, a real
  figure), `rate_pair` combines them with the deciding mean, and the per-language figures are
  printed above the verdict and never decide.  Under the verdict, one line per measure — F0.5,
  recall, precision, left alone, coverage — with its own interval and its own `better` / `tie` /
  `worse` by the same rule, then a dominance line: a model dominates the other when it is worse on
  no measure, better on one, and not more than 5 % slower per 100 words (`TIME_TOLERANCE`).

```bash
# the floors and the explanations of one model
bazel run //tools:proofreading_store -- gate --model ministral-3:14b \
    [--mode review] [--temperature X] [--machine rx9070xt-server] [--testset gec-2026-12] \
    --out evaluations/proofreading
# one model against another
bazel run //tools:proofreading_store -- compare --model ministral-3:14b --against gemma4:e4b
# the same comparison on the rows of local result folders
bazel run //tools:proofreading_store -- compare --cache ~/gec-testset/results-correct-only \
    --testset ~/gec-testset/set-2026-12 --model qwen3.5:4b --temperature 0.05 \
    --against qwen3.5:4b --against-temperature 0.7
# one table per model over its temperatures, with the best row and its ties
bazel run //tools:proofreading_store -- sweep --cache ~/gec-testset/results-correct-only \
    --testset ~/gec-testset/set-2026-12 [--reference qwen3.5:4b@0.05] [--corrections]
# what a second and a third pass of each configuration would add, and what they would cost
# (the exchange folder gives step ①'s share of the time, but only a run written with step tags;
# the baseline of 2026-11-14 has none, so its cost column is a dash)
bazel run //tools:proofreading_store -- passes --cache ~/gec-testset/results-correct-only \
    --cache ~/gec-testset/results-exchange --testset ~/gec-testset/set-2026-12
```

A command names a model, not a file: the runs are found below `--out`, and the parts of the key the
command does not name come from the newest run of the model — the newest one that matches the mode,
the temperature (each model's own starting temperature unless `--temperature` is given) and the
eval version, so an older run of another mode cannot decide which test set
is read.  A comparison takes the test set and the machine from `--model`, never from `--against`, so
the two groups are always the same evaluation.  What is left ambiguous is refused rather than
guessed — two digests of one tag, or two request profiles, ask for `--digest` or `--compact-json`.  `gate` reads `marks/<run-id>.json` of repeat 1 when it is there, and prints a note
for a catalog model in review mode whose `structured_output` flag is not set (the gate-1 records are
prose, so the flag is set by hand).  One line per check (`✓`, `✗`, `·` for a diagnostic), then the
outcome.  Exit codes: `gate` 0 for `PASS` / `PASS_PENDING`, 1 for `FAIL`, 2 for `INCOMPLETE`;
`compare` 0 for `BETTER`, 1 for `TIE` / `WORSE`, 2 for `NOT_COMPARABLE` or an incomplete group.  The
output is the record: `evaluations/writing-models-2026-11/gate2-<date>.md`, numbers only.

**Comparing local result folders.**  `compare --cache DIR [--cache DIR] --testset DIR` judges the
rows a run wrote instead of a committed run: every folder is read with the test set, every row is
scored again, and the groups are formed from the headers, so a folder that lost a repeat says so
(`skipped <file>: <reason>`) instead of comparing two runs.  `--cache` and `--out` are exclusive,
and `--testset` is required, because the rows are scored against the texts on disk.  `--mode`
defaults to the mode the folders hold, and is refused when they hold several.  A refinement of one
model names both temperatures (`--against-temperature`), and a comparison of the two exchange modes
names both modes (`--against-mode correct-only --corrections`): with `--corrections` the exchange
and correct-only modes count as one measurement and the explainer is ignored, because step ① writes
the corrections of both.  Any other pair of modes stays not comparable, and the comparison prints
"corrections only" when the flag is used.

**A sweep of temperatures.**  `sweep --cache DIR [--cache DIR] --testset DIR` prints one table per
model over its temperatures: the deciding mean F0.5 with German and English beside it, recall and
precision, the share of the sentences the reference leaves alone that the model left alone, the
share of the touched sentences it looked at, the false alarms per text, the spread of the repeats
(the highest minus the lowest F0.5, the sampler's own noise), the texts that lost or added text, the
seconds per 100 words, the answer tokens per text, and the fixes, wrong changes and misses per page
of 300 words.  The last column is the difference of the deciding mean F0.5 against a reference row
with its 95 % interval: `--reference MODEL@TEMPERATURE[:MODE]` names it, and without the flag the
first row of each model is the reference.  Below each table the best row is named — the highest
F0.5 — together with every row that is not worse than it beyond the noise, because a tie goes to the
cheaper configuration and that is a choice, not a number.  A reference in another answer mode is
refused unless `--corrections` is given, which compares the corrections only.  A row is only
measured against a reference it can be compared with — the same test set, window, machine and eval
version, the same texts, and (without `--corrections`) the same answer mode; otherwise its Δ column
says "not comparable".  `--corrections` also reaches the ties below each table.

**Passes.**  `passes --cache DIR [--cache DIR] --testset DIR` asks what a second and a third pass of
one configuration would buy if the passes were merged by a vote.  An edit is a word-diff change of
the source, and its *votes* are how many of the repeats made it.  The table shows the single pass
(the mean of the repeats), the edits at least two of three made, and the edits every repeat made;
then the flag — if a single pass's edits that not every repeat made were shown as uncertain, what
share of its edits that is, and how many of its false alarms and how many of its real fixes it would
catch; then the precision of an edit made by one, two and three passes.  The last column is the time
the passes cost against one pass, `1 + (k - 1) × step ①'s share`, because a second and a third pass
repeat only step ①: a correct-only run shows `3.00x`, and a run without step tags shows a dash.  A
group of one repeat has no vote to take and is left out, and a committed run is refused, because it
keeps no texts to diff.  A repeat that lacks a text another has (an interrupted run) is refused
with the repeat's number.

**The explanation check.**  Before a model takes a star place, a teacher marks a sample of its
explanations.  The sample is defined by the *committed* run, not by the cache: from the run of repeat
1, per deciding language, the notes linked to an edit with a non-blank explanation, from `page`-band
texts only, at most two per text, drawn with `random.Random(f"{run_id}/{language}")` — ten per
language, fewer when a text has fewer such notes, and the threshold then scales (80 %, rounded up).
`proofreading_store sheet --run RUN_ID --cache DIR` prints the sheet with the source sentences
from the local cache and refuses a row whose output or explanation differs from the committed run;
`proofreading_store marks SHEET.md --marker NAME` writes `marks/<run-id>.json` (no text, hashes
only; `--marker` is required, because the name goes into the repository), and `validate` checks it
against its run — `gate` reads a marks file only when that check passes, so a mark of another sample
or of an explanation that changed since counts as unmarked.  A mark is `correct`, `wrong` or `vague`; only `correct` counts.
No marks is `PASS_PENDING`, which is enough for an alternative's place, not for a star's.

**The pick** happens once per screen, by hand, and is written down with the `compare` outputs it
rests on: per tier, the eligible models (gate 1 for review mode, the floors, and the tier's memory)
are ranked by the deciding mean F0.5; a current star or alternative that is eligible keeps its place
unless a challenger is `BETTER`; a place whose holder is not eligible goes to the highest-ranked
eligible model, a tie going to the smaller and then the faster model.  At most two models per tier.
The floors are to be calibrated after the runs of the full screen: a floor that a current star fails, or that every
model fails, is reconsidered before it is used, and its value, reason and new threshold are recorded.

**Deferred: splitting equal-length edits.**  Two reference edits of equal length in one block are not split token by
token.  Measured on the smoke run of 2026-11-10, the split would turn 631 German reference
edits into 634 and 350 English ones into 351, and move F0.5 by at most 0.006.  It is not worth
rebuilding the committed manifest, re-pinning the regression tests and relabelling; it moves to the
ERRANT work item, which cuts edits properly on both sides anyway.

Two further views need the labels (`report --testset … --by-error category|type`):

* *Found exactly (found in place) / errors* per kind of error: of that many reference errors, the
  model made exactly the reference change for the first number and changed at least the same
  words for the second.  The gap is "noticed it, fixed it differently", which the F-score counts as
  a miss; a large gap means the F-score underrates the model (or the single reference is narrow).
  Kinds are the app's six categories (`--by-error category`; `other` is every label with no clear
  counterpart, and `unlabelled` the changes no annotated edit was near) or the corpus word types
  (`--by-error type`).
* *Same category*: of the errors a model fixed exactly and explained, how often its category is the
  one the label stands for (`error_labels.category_of`).  A rough diagnostic only: the corpus labels
  were not made for these categories, and `other` is not checked.  It is stored per row at run time
  (from the review), so rows from before 2026-11-09 show nothing there.

**Answer mode.**  `run --mode text` asks for the corrected text only, the app's `text` mode, on the
same texts; the default is the app's `review` mode.  Rows record their mode, the table keeps the
modes apart, and text-mode results go to files of their own (`…__text.jsonl`).  Text-mode rows have
no explanations, so "Explained", orphans and "Same category" stay empty for them.  It answers two
questions: what the JSON format costs in correction quality, and (since step 1 of a two-step design
*is* text mode) what the corrections of a two-step design would be before anyone builds it.

`run --mode exchange` asks the proofreading exchange (corrected text, explanations, unclear check)
and `run --mode correct-only` asks **step ① of the exchange alone**.  The edits of the exchange are
the word diff of the source against step ①'s text, and steps ② and ③ change none of them, so
correct-only scores exactly the exchange's corrections for about a quarter of its time (step ① is
16 to 26 % of it).  It is the exchange's own code: the port refuses the requests of steps ② and ③
(`OnlyStepsPort`) and the exchange treats that as "no explanations, no unclear list", so the prompt,
the cut-off handling and the text budget (hence the pieces of a long text) are the exchange's, and
a test pins that its request hashes like the exchange's first one.  Its rows have no review, no
warning and no explanations, like text mode's.  Use it for every question about the corrections
(temperature, sampling values, prompts, passes, thinking, budgets); use `exchange` for the
explanations, the unclear flags and the time of a whole page.

`run --option NAME=VALUE` (repeatable) sends sampling values with step ①, besides the temperature:
`top_k`, `top_p`, `min_p`, `presence_penalty`, `frequency_penalty`, `repeat_penalty`
(`SAMPLING_OPTIONS`).  Without it the request carries the temperature and the window only and the
model's own tag decides the rest, as in the app.  A run with options writes files of its own
(`…__presence_penalty-0__r1.jsonl`), lists them in its header's `request_profile`, and
`report --cache` refuses a folder that pools runs asked with different values: keep one folder per
set of sampling values.  Every request of the exchange is tagged with its step in the performance
record (`step`, derived from its system prompt), and `--performance` prints step ①'s share of the
time.

`run --prompt NAME` asks step ① with a named variant of its system prompt (`keep`, `find` or
`shot`, `CORRECT_VARIANTS` in `domain/correction_prompts.py`) instead of the model's own;
`--prompt standard` sends the standard `CORRECT_SYSTEM` to a model that has an adopted variant
(to measure the two against each other).  Only the exchange and correct-only modes send it, so
another mode refuses.  The variants are written from what the evaluation showed and never from a
test text: `keep` for the models that over-edit (the false alarms of those models are mostly extra
edits inside sentences that have a mistake, so a sentence with a mistake changes only the words of
the mistake; a short list says what is no mistake and is not to be added either, an optional comma
included), `find` for the models that leave mistakes alone (a checklist of what to look at in every
sentence, its comma rule per language), `shot` with two invented worked examples (labelled so that
no label can start an answer; the exchange refuses an answer that starts with a `Word:` label the
text does not start with: `framing_problem`).  A variant's rules and examples come from German and
English, the languages that were measured, and say so: a model's variant is sent for every text
language.  A variant is part of the request profile (`StepProfile.prompt`,
step ① only; `STEP_ONE_PROMPTS` in `model_catalog.py` is the table of the models that are sent one,
empty until a measurement on both test sets adopts a variant; another quantisation of a listed
model is sent the same one).  A run with a variant writes files of its own
(`…__prompt-keep__r1.jsonl`; the name follows the prompt the requests carry, so a model with an
adopted variant writes that file without a flag and `--prompt standard` writes the plain one),
lists it in its header's `request_profile`, and is refused by `report --cache` when it is pooled
with a run of another prompt.  The fingerprint hashes the text of every variant, so a changed
variant needs a new protocol version once a version has been merged or measured (version 4 added
them; its requests are version 3's with three more entries, which the recorded fingerprint of
version 3 proves); a variant that a measurement used is never edited, another text is another id.
The cache key of the correction stage carries the variant (id and hash of its text,
`prompt_identity()`) only for a model that has one.

**The request per model.**  The tools send each model exactly what the app sends: its
`RequestProfile` (temperatures per step, `think`, sampling values, step-① prompt variant). The
rules are in `docs/development.md` → "Request profiles: temperatures and prompts per model".  The
evaluation mirrors the app's second try after a cut-off: `RecordingClient` sends `seed + k` for the
*k*-th identical request, so a repeat samples afresh as the app does, and gate 1 sends the second
try and stores both attempts (`attempt` 1 and 2).  Its verdicts: cut off twice is `TRUNCATED`
(hard), cut off once and then answered is `CUT_OFF_ONCE` (soft), and a second answer that fails
another test gets that test's verdict.

**Protocol version.**  What a run measured and how — the prompts, the answer format, the window, the
level and the long-text budget — is versioned in `tools/model_eval/protocol.json`, not by commit.
`run` computes the fingerprint of that request side (captured from the one-request corrector: system
and user message, answer schema, options, text budgets, repair message) before the first request
and stops with exit code 2 when it differs from the entry of `current`, so an uncommitted prompt edit is caught like a
committed one.  A version that only *adds* something (a mode, a profile variant) leaves every
earlier request hash as it was; the entry then says so, and the results of the earlier version are
re-stamped after a comparison of the two fingerprints per mode, language and profile showed that
(version 3 did this for the correct-only mode and the sampled profile).  Raising the version means adding an entry with the new fingerprint, the date and a
line saying why, and setting `current` to it; do it when the check refuses, and also when the
scoring, the caps or the edit extraction change (the fingerprint stays the same then, but the golden
numbers of the tests fail).  Old entries are never edited or removed.  A run resumed after a tool,
Ollama or model change is refused as well, before the first request of any combination: the
sessions of one run have to agree on the protocol, the test set (by hash), the machine, the model
digest and the request settings including the timeout, and a results file without a header is
refused.  The export copies the
protocol from the run header and refuses a run whose version pair `protocol.json` does not record.
`tool_revision` stays in the header as a pointer for a human reader; nothing checks it.

**Does a model fit the graphics card at the base window (`PROOFREADING_NUM_CTX`, 8192), and how
fast is it?**  Measured with `nvidia-smi`, `ollama ps` and Windows' own memory counters on one
computer (RTX 3070 Laptop with 8 GB, 47 GB of main memory), window 8192 tokens, 250 generated
tokens, nothing else on the card:

| Model | Tokens per second | Video memory used | Free main memory used | On the card at 4096 → 8192 (Ollama's report) |
| --- | --- | --- | --- | --- |
| `qwen3:4b` | 98 | 3.8 GB | 0.8 GB | 100 % → 100 % |
| `gemma4:e4b` | 77 | 4.4 GB | 1.1 GB | 100 % → 100 % |
| `qwen3.5:9b` | 43 | 6.4 GB | 1.3 GB | 100 % → 88 % |
| `ministral-3:8b` | 32 | 6.2 GB | 1.5 GB | 92 % → 84 % |
| `gemma4:12b` | 13 | 6.9 GB | 3.6 GB | not reliable |
| `ministral-3:14b` | 6.6 | 6.3 GB | 4.5 GB | 61 % → 56 % |

*Free main memory used* is the drop of free memory while the model is loaded; about 1 GB of it is
loading overhead that every model has, even those that sit entirely on the card, so the share that
really runs on the processor is roughly the value minus 1 GB (`gemma4:12b` about 2.6 GB,
`ministral-3:14b` about 3.5 GB).  Two consequences: `gemma4:12b` does **not** run natively on an 8 GB
card (it needs about 9.5 GB in total, which is the catalog's `memory_gb`, and generates at a sixth of
the speed of `gemma4:e4b`), and Ollama's size report is wrong for it (about 1 GB), so speed is the
reliable signal.  On this card Ollama keeps at most about 5.6 GB of weights on the GPU; the fixed window
of 8192 tokens moves part of `qwen3.5:9b` and `ministral-3:8b` to the processor.  `memory_gb` in the
catalog is the total a model needs at that window (card plus main memory).

**Speed, memory and repeatable runs.**  Every row of a `run` records what its text cost
(`ResultRow.performance`, `tools/model_eval/performance.py`): each request with Ollama's token
counts and durations, the models the server had loaded, the graphics-memory peak, the seed.  The
report shows it with `report --performance`: seconds per 100 words of student text (what a teacher
waits), answer tokens per second (how fast the machine runs the model), answer tokens per 100
words, requests per text (a repair or fallback adds one), graphics memory added over the idle
baseline.  Options of `run`:

| Option | Effect |
| --- | --- |
| `--seed N` (default 20261109), `--no-seed` | The app sends no seed; the evaluation sends one so a run can be repeated on the same machine and software.  `--no-seed` asks as the app does. |
| `--repeat N` | Run every text N times; repeat k uses seed + k - 1 and a file `…__rk.jsonl` |
| `--isolate` | Unload every model first, refuse a server that will not become idle, unload the model afterwards.  Only on a server you own |
| `--gpu-sampler amd\|nvidia`, `--gpu-ssh "ssh.exe user@host"` | Sample the graphics memory in use once a second, on this machine or through SSH (from WSL the Windows client `ssh.exe` holds the key) |
| `--machine NAME` | Written into the header |

Each results file gets a `….header.json` (a list, one entry per invocation) with the tool commit,
Ollama version, the model's digest, quantisation and size, window, seed, whether the server was
idle, and the idle graphics memory.  The protocol (`tools/model_eval/measure.py`): unload and check,
start the sampler, one unscored warm-up request that loads the model, then per text note what is
loaded before and after.  A text is **shared** when another model was loaded at either point; shared
texts are left out of every speed and memory figure.  Samples are stamped when they arrive here,
because the server's clock can be seconds off (it was 2 s ahead), and a one-second sampling
interval is the limit of the memory peak.  The speed runs still sample with the local process or
through SSH; the memory agent (see *Memory agent*) is the reading the fit check uses, and it is
where the speed runs' sampler is going — it reads the card on the machine that has it, without a
key and without a remote command.

**Committing a run as evidence.**  The cache above stays outside the repository (it holds the
source and reference texts).  `proofreading_store export` turns one run of that cache into a run
file below `evaluations/proofreading/` and checks it against the test-set manifest and the
corpora: scores and per-error outcomes are numbers, the model's edits are stored as positions and
short replacements, a replacement or explanation that would copy the corpus is cut down (at most 8
words per replacement, no run of 5 copied source words, no words at all from a restricted corpus),
and everything is hashed, so a committed run holds no corpus sentence.  `validate` checks a file
without the corpora (as the test suite does for every committed one), `index` regenerates the
checksums in `INDEX.md`, and `manifest` checks that the test set there is still the one the corpora
give — every corpus file and every item — and that it was built from a clean tree:

```bash
bazel run //tools:proofreading_store -- export --cache ~/gec-testset/results \
    --testset gec-2026-12 --manifest evaluations/proofreading/testsets/gec-2026-12/manifest.json \
    --machine rx9070xt-server --corpora ~/gec-testset/corpora.json
bazel run //tools:proofreading_store -- index --check
```

The rules, the file format and how to read a run back are in
`evaluations/proofreading/README.md` and `FORMAT.md`; the test-set manifest and the machine files
are written once and then pinned.

Things to know when reading the figures:

* **Compare speed only between runs on the same machine.**  The server has an AMD Radeon RX 9070
  XT, the laptop an NVIDIA RTX 3070; backends, memory and drivers differ.
* **Ollama's own size is unreliable for some models.**  `/api/ps` reported 0.39 GB, fully on the
  card, for `gemma4:e2b` while `amd-smi` showed the model adding 3.4 GB.  The report's *On the
  card* column is that estimate; trust the graphics-memory column.
* **Tokens per second is not waiting time.**  Models write different amounts for the same text:
  `gemma4:e2b` wrote 139 answer tokens per 100 words in text mode and 1039 in review mode, at the
  same 224 to 231 tokens per second, so a text took 0.7 s per 100 words against 4.8 s.
* **The AMD sampler was checked on a Radeon RX 9070 XT (`amd-smi` 27.1.0) and the NVIDIA one on a
  GeForce RTX 3070 Laptop.**  Other cards and versions may print other columns.
* A sampler started over SSH is killed by process id on the remote machine; stopping the local
  `ssh.exe` alone leaves `amd-smi` running there.

**Where the code lives.**  `tools/model_eval/` is one module per job, and the two command lines
(`compare_writing_models.py`, `proofreading_store.py`) only parse arguments and print:

| Module | Job |
| --- | --- |
| `answer_mode` | How a model is asked to proofread: `review`, `text` (the one-request baseline), `exchange`, `correct-only` (step ① alone), `two-step`. |
| `languages` | The language codes of the test set and the names the corrector is asked in. |
| `redaction` | The caps that keep corpus text out of the repository, and what a "word" is. |
| `performance` | What one text cost: requests, tokens, seconds, memory. |
| `error_labels` | The corpus labels of the reference edits and the category agreement. |
| `scoring` | What the reports compute from counts: one F-beta, pooled. |
| `results` | The typed, versioned rows of the local cache and their JSONL format. |
| `run_format` | One committed run file: its schema, its typed items, read and write. |
| `layout` | Where the parts of the repository format live, and what a run is called. |
| `validation` | Format mode and strict mode of checking a run file. |
| `export` | A local run becomes a committed run file. |
| `index` | `INDEX.md`, one row per run. |
| `protocol` | The version file of the request side and its fingerprint. |
| `session` | The sessions of one run and whether it may be resumed. |
| `app_corrector` | The one-request corrector, as the tools build it — the baseline the exchange is measured against. |
| `one_request/` | The one-request path itself: `correction.py` (the correctors), `correction_schema.py` (the answer model), `answer_format.py`, `presets.py` (the per-language policy presets). Nothing under `teachers_teammate/` imports it. |
| `comparison` | Runs a test set through that corrector and turns the answers into rows. |
| `measure` | The requests, the server and the graphics memory of a measured run. |
| `runner` | The run loop: plan, resume, warm-up, header, sampler. |
| `summary` | The numbers a report is made of: one summary per model, temperature, language and band. |
| `report` | The results table, the band table, the by-error view and the performance table. |
| `grouping` | Which runs may be compared: the group key, the runs of one key, and the spread of a number over the repeats. |
| `gate_two` | Gate 2: the floors of one model, the comparison of two, the paired bootstrap. |
| `spot_check` | The explanation check: the sample of a run, the sheet, the marks. |
| `selection` | Which committed runs a gate-2 command reads, and what it refuses as ambiguous. |
| `testset` | The test set: its manifest, its items and the corpora they come from. |
| `corpora` | One module per corpus: what it may be used for, which files it is, how a pointer becomes a text. |
| `recipes` | One module per test-set id: the selection rule, its seed and the corpora it needs. |

`report` is a leaf that only the command lines import — the numbers it prints come from `summary`,
which `grouping` and `gate_two` read instead — and `measure` and `runner` are reached only through
`runner` and the comparison command line; a test in `test_session.py` reads the imports of every
module and refuses anything else.

## Adding a corpus or a test set

A corpus is one module in `tools/model_eval/corpora/`, named after the corpus with `-` replaced by
`_`. Nothing lists it: the package finds every module whose name does not start with `_` and reads
its `ADAPTER`, the way the app finds its providers. The class declares what a manifest needs —
`NAME`, `LICENCE` (copied word for word into every manifest that uses it), `RESTRICTED` (the licence
forbids passing on any part, so a run over it stores no words at all), `LANGUAGES`, `HINT` (what
`--corpus NAME=PATH` expects, for refusals and `--help`), `SPLITS` (the default first) and, for a
corpus that lives in the repository, `DEFAULT_ROOT` — and it implements two things: `files()`, every
file it reads keyed by the name the manifest records, and `resolve(item)`, which turns a pointer into
a text. **An adapter reads only the files it pins**: that is what makes `corpus_problems` meaningful,
and a test resolves every item of the invented set while recording the paths opened.

The selection *rule* is not the corpus's: one corpus serves several sets and one set draws on
several corpora. It lives in `tools/model_eval/recipes/`, one module per test-set id, found the same
way, setting `RECIPE` to a `Recipe(testset, seed, corpora, select)`. `prepare_gec_testset --testset ID`
picks the recipe, and `ID` is required: with two recipes a default would build the wrong set sooner
or later. A recipe never changes once its manifest is committed — another selection is another
test-set id.

Each needs its tests: `test_corpora.py` for the declarations, the refusals and the splits,
`test_corpus_extension.py` for the pattern (it defines a toy corpus inside the test file, adds it
with `monkeypatch.setitem(corpora.adapters(), …)` and takes it through build, check, export and both
modes of `validate`, without touching anything outside the test), and `test_corpus_regression.py`
for the committed set, which runs only with `GEC_CORPORA` set to a `{name: path}` file.
