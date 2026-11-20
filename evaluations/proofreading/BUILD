"""Committed proofreading runs and the files that describe them.

Data only: ``tests/tools/test_evaluation_committed.py`` checks that every committed run passes
format-mode validation and that ``INDEX.md`` is the one the run files give.  The tree is a filegroup
so that the test sees it in the sandbox; nothing here is built or shipped.
"""

filegroup(
    name = "all",
    srcs = glob(["**"]),
    visibility = ["//visibility:public"],
)
