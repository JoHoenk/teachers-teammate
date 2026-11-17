"""The proofreading exchange: the diff, the edits, the review and the Markdown shield.

Pure domain code: no I/O, no Qt, no provider imports.  ``infrastructure/proofreading``
holds the connections and the request runner; ``review.py`` is the stored review and its
alignment with the current text.
"""
