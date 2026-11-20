"""The records, names and lookups every layer of the evaluation tooling shares.

Nothing here writes a file, sends a request or computes a score: ``core`` is the vocabulary the
layers above it speak.  It reads a JSON file it is given (the protocol table) and nothing else.
"""
