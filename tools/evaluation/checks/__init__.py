"""The checks that ask a model something and judge the answer, beside the pipeline.

Gate 1 (does the model answer the review format at all), the smoke check (is this setup
systematically broken), the model-fit measurement and the catalog checks.  A check sends requests
and writes its own files; it never writes a run file, which is why this layer sits beside `run`
rather than inside it.
"""
