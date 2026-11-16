"""The evaluation report: one local page that shows the texts behind the numbers.

The report is a folder of static files (``index.html``, ``viewer.js``, ``viewer.css`` and one
``data/*.js`` per text) that opens from ``file://`` after unzipping, with no server and nothing
from the network.  Python computes every number and writes the data; the JavaScript only displays
and filters it, so the page cannot disagree with ``proofreading_store report``.

* :mod:`.load` — the test set and the local run caches: headers, rows, and what was skipped.
* :mod:`.variants` — the variants of a report: one per group key, labelled by the facts that differ.
* :mod:`.outcomes` — what a model edit and a reference edit came to, and the sentences they sit in.
* :mod:`.numbers` — the scorecard: per variant, language and band, per repeat, with mean and range.
* :mod:`.model` — one normalised report model, as the JSON the page reads.
* :mod:`.page` — the report folder, its zip and its single-file form.

The command line is ``tools/eval_report.py`` (``bazel run //tools:eval_report -- build``).
"""

__all__: list[str] = []
