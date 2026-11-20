"""The evaluation report: one local page that shows the texts behind the numbers.

The report is a folder of static files (``index.html``, ``viewer.js``, ``viewer.css`` and one
``data/*.js`` per text) that opens from ``file://`` after unzipping, with no server and nothing
from the network.  Python computes every number and writes the data; the JavaScript only displays
and filters it, so the page cannot disagree with the report's own tables.

* :mod:`.variants` — the variants of a report: one per group key, labelled by the facts that differ.
* :mod:`.outcomes` — what a model edit and a reference edit came to, and the sentences they sit in.
* :mod:`.numbers` — the scorecard: per variant, language and band, per repeat, with mean and range.
* :mod:`.prompts` — the instructions a variant's requests carried, as text a reader can read.
* :mod:`.model` — one normalised report model, as the JSON the page reads.
* :mod:`.page` — the report folder, its zip and its single-file form.

The store is read by :mod:`..load`, which is not part of the page: this package is a client of the
report layer and of :mod:`...run.protocol`, and nothing under ``report/`` outside this folder may
import ``run``.  The command line is ``tools/report.py``.
"""

__all__: list[str] = []
