"""The developer tools that measure writing models, separated from the command-line wrappers.

Nothing here ships in the wheel: the tools decide product facts (the catalog's
``structured_output`` flag, the recommendations) and are used by a developer on a machine with
Ollama, so they live next to the CLIs rather than in ``teachers_teammate/``.

**The request and the answer**

* :mod:`.ports` — the seam: a ``ChatPort`` a test can replace.
* :mod:`.answer_mode` — how a model is asked to proofread (review, text, two-step).
* :mod:`.languages` — the language codes of the test set and the names the corrector is asked in.
* :mod:`.defaults` — the defaults both command lines share: server address, request profile, temperature.
* :mod:`.app_corrector` — the app's own corrector, as the tools build it.
* :mod:`.protocol` — the version file of the request side and its fingerprint.

**Gate 1: does the model answer the review format?**

* :mod:`.gate1_fixtures` — the invented texts gate 1 sends (data only).
* :mod:`.structured_check` — sends the review answer format to one model.
* :mod:`.check_rule` — the pass rule as an enum and thresholds instead of upper-cased strings.
* :mod:`.smoke` — the per-setup screen before the long runs.
* :mod:`.gate1_answers` — the answers a check received, saved so they can be judged again.
* :mod:`.gate_one` — continuing a saved run, judging saved answers, saving new ones.
* :mod:`.download_sizes` — the real download size of a catalog tag.

**Gate 2: how good are the corrections?**

* :mod:`.comparison` — runs a test set through the app's own corrector.
* :mod:`.runner` — the run loop: plan, resume, warm-up, header, sampler.
* :mod:`.measure` — the requests, the server and the graphics memory of a measured run.
* :mod:`.session` — the sessions of one run and whether it may be resumed.
* :mod:`.progress` — how far a long run is.
* :mod:`.performance` — what one text cost: requests, tokens, seconds, memory (pure).

**Scoring and reports (pure)**

* :mod:`.quality` — edit agreement, explanations and sentences left alone, against a reference.
* :mod:`.gleu` — GLEU from counts that can be summed over texts.
* :mod:`.item_scores` — the numbers of one item.
* :mod:`.error_labels` — the corpus labels of the reference edits and the category agreement.
* :mod:`.scoring` — what the reports compute from counts: one F-beta, pooled.
* :mod:`.summary` — one summary per model, temperature, language and band.
* :mod:`.report` — the results table and the spot-check sheet.
* :mod:`.grouping` — the runs that may be compared, and the spread of one number over the repeats.
* :mod:`.gate_two` — the floors a model meets, and the comparison of two models.
* :mod:`.selection` — which committed runs a gate-2 command reads.
* :mod:`.spot_check` — the sample of explanations a teacher marks, the marks, the sheet.

**The repository format**

* :mod:`.results` — typed, versioned rows and their JSONL format.
* :mod:`.run_format` — one run file: its schema, its typed items, read and write.
* :mod:`.layout` — where the parts of the repository format live, and what a run is called.
* :mod:`.redaction` — the caps that keep corpus text out of the repository (pure).
* :mod:`.validation` — format mode and strict mode of checking a run file.
* :mod:`.export` — a local run becomes a committed run file.
* :mod:`.index` — ``INDEX.md``, one row per run.

**The test set**

* :mod:`.testset` — the test set: its manifest, its items and the corpora they come from.
* :mod:`.spans` — character spans to the token spans the word diff and the manifest use.
* :mod:`.corpora` — one adapter module per corpus, found at run time (licence, files, pointers).
* :mod:`.recipes` — one module per test-set id: which texts a set chooses from which corpora.
* :mod:`._plugins` — how both packages find their modules.

**Command lines**

* :mod:`.cli_paths` — a relative path means the repository under ``bazel run``.
"""
