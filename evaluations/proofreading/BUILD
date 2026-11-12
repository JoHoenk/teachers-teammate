"""Committed proofreading runs and the files that describe them.

Data only: the store tests check that every committed run passes format-mode validation and that
``INDEX.md`` is the one the run files give (tests/tools/test_validation.py and test_index.py). The tree is a filegroup so
that those tests see it in the sandbox; nothing here is built or shipped.
"""

filegroup(
    name = "all",
    srcs = glob(["**"]),
    visibility = ["//visibility:public"],
)
