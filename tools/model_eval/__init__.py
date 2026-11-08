"""The developer tools that measure writing models, separated from the command-line wrappers.

Nothing here ships in the wheel: the tools decide product facts (the catalog's
``structured_output`` flag, the recommendations) and are used by a developer on a machine with
Ollama, so they live next to the CLIs rather than in ``teachers_teammate/``.  The one exception
is :mod:`teachers_teammate.domain.proofreading_quality`, because both tools need its metrics and
they are pure.

* :mod:`.ports` — the seam: a ``ChatPort`` a test can replace.
* :mod:`.check_rule` — the pass rule as an enum and thresholds instead of upper-cased strings.
* :mod:`.structured_check` — sends the review answer format to one model.
* :mod:`.comparison` — runs a test set through the app's own corrector.
* :mod:`.results` — typed, versioned rows and their JSONL format.
* :mod:`.report` — the results table and the spot-check sheet (pure).
"""
