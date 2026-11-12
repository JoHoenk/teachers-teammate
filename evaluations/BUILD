"""The written records of the evaluations: Markdown and the raw records of the model checks.

Data only: ``tests/tools/test_no_plan_references.py`` reads them to check that no record points into
working notes that are not in the repository.  The committed runs have their own package
(``//evaluations/proofreading``).  Nothing here is built or shipped.
"""

filegroup(
    name = "records",
    srcs = glob([
        "*.md",
        "model-fit/*",
        "writing-models-2026-11/*",
    ]),
    visibility = ["//visibility:public"],
)
