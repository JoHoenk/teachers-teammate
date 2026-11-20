"""The pure measurements of the evaluation tooling: edits, spans, GLEU, text loss, item scores.

Nothing here reads a file, sends a request or writes anything.  A layer above may use one of these
for a guard; only ``report`` composes them into a row score.
"""
