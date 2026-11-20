"""The written record of how the evaluations work: the method and the formats.

Data only: ``tests/tools/test_no_plan_references.py`` reads it to check that no document points into
working notes that are not in the repository.  The records themselves — runs, test sets, marks,
machines, sizes, gate records — live in the private repository ``teachers-teammate-evals``, never
here.  Nothing here is built or shipped.
"""

filegroup(
    name = "records",
    srcs = glob(["*.md"]),
    visibility = ["//visibility:public"],
)
