"""Developer probes: tools that answer one question once, for one developer.

Nothing here runs in a build, a run or a gate. A probe is started by hand, prints what it found,
and its figures are then written down where the app or a document needs them — the catalog's
token counts, or whether both Mistral endpoints serve a model. The evaluation harness
(``//tools/evaluation``) is the tooling that measures and keeps records; it lives one level up.
"""
