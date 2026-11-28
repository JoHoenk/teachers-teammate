"""The comparison of reading models: what each one reads out of the same pages.

A proofreading tool of its own, not ``tools/evaluation/``: that one's test records hold the text
and its run files hold the model's answer, which on a student page is the student's text.  This
package keeps every reading in a local cache, and writes numbers only into the store.

Innermost first; a module imports only the ones above it (the Bazel targets say the same):

* :mod:`.presets` — what a reading is asked under: ``raw``, ``R``, ``R-<step>``, ``R-colour``
* :mod:`.records` — the numbers-only record, and its check
* :mod:`.runs` — where the records sit in the store
* :mod:`.report` — the table a campaign is read from
* :mod:`.pages` — the page set, its version and its split
* :mod:`.candidates` — the reading models a run may name before the catalog knows them
* :mod:`.prepare` — the app's own image preparation, step by step, with what each step measured
* :mod:`.engine` — the app's own ways of reading a page
* :mod:`.metrics` — CER, WER, kept mistakes and the failure flags of one reading
* :mod:`.readings` — the local cache a resumed run reads from
* :mod:`.runner` — one run of a page set
* :mod:`.screen` — the candidate and speed check on drawn pages
"""
