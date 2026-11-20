"""Bazel targets for the evaluation tooling, one per layer.

The `deps` of these targets are the import graph, so a layer that imports a layer above it is a
test failure and not a review comment:

```
publish → report → store → input → core, metrics
run → store, input, core, metrics            checks → run, core, metrics
viewer → report, run, store, input, core, metrics
convert → store, input, core, metrics
```

`core` and `metrics` sit below everything.  `viewer` is the one target above `report` that may
import `run`: the page shows the prompts a run sent, and `report` may not reach up to `run`.
"""

load("@rules_python//python:defs.bzl", "py_binary", "py_library")

# The app below the GUI: the domain types (the review, the word diff, the model catalog's names),
# the infrastructure the runs talk to (the Ollama client, the corrector), and the application's
# formatting.  `application_lib` brings the layers below it; the GUI and the command line stay out.
_APP = ["//teachers_teammate:application_lib"]

py_library(
    name = "core",
    srcs = glob(["core/*.py"]),
    data = ["core/protocol.json"],
    visibility = ["//visibility:public"],
    deps = _APP,
)

py_library(
    name = "metrics",
    srcs = glob(["metrics/*.py"]),
    visibility = ["//visibility:public"],
    deps = [":core"] + _APP,
)

py_library(
    name = "store",
    srcs = glob(["store/*.py"]),
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":metrics",
    ] + _APP,
)

# The one-off converter of the old store.  It sits above `store` because it writes the new formats
# through `store`'s writer, and above `input` because it rebuilds a manifest.
py_library(
    name = "convert",
    srcs = glob(["convert/*.py"]),
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":input",
        ":metrics",
        ":store",
    ] + _APP,
)

py_library(
    name = "input",
    srcs = glob(["input/*.py"]) + glob(["input/corpora/*.py"]) + glob(["input/recipes/*.py"]),
    # The invented French set is the one corpus that is in the repository, so its adapter reads it
    # from a path next to the tools and the file has to be in the runfiles.
    data = ["//tools:gec_fr_invented.jsonl"],
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":metrics",
    ] + _APP,
)

py_library(
    name = "run",
    srcs = glob(["run/*.py"]) + glob(["run/one_request/*.py"]),
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":input",
        ":metrics",
        ":store",
    ] + _APP,
)

py_library(
    name = "report",
    srcs = glob(["report/*.py"]) + glob(["report/tables/*.py"]),
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":metrics",
        ":store",
    ] + _APP,
)

# The redacted export and the index: a local run becomes a committed run file, and the index lists
# them.  It sits above `report`, because the numbers it stores are the report's own.
py_library(
    name = "publish",
    srcs = glob(["publish/*.py"]),
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":input",
        ":metrics",
        ":report",
        ":store",
    ] + _APP,
)

# The report page: one static folder with the texts, the variants and the numbers.  It is the only
# target above `report` that imports `run`, because the page shows the instructions a run sent and
# `report` may not reach up to `run`.
py_library(
    name = "viewer",
    srcs = glob(["report/viewer/*.py"]),
    # The script and the style are copied into the report folder as they are, so they sit next to
    # the module that writes them.
    data = [
        "report/viewer/viewer.css",
        "report/viewer/viewer.js",
    ],
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":input",
        ":metrics",
        ":report",
        ":run",
        ":store",
    ] + _APP,
)

# The memory agent: one file, standard library only, no import from this repository, so it can be
# copied alone to the machine with the graphics card.  It is also part of `:run` (the glob there),
# which is harmless; `tests/tools/test_evaluation_gpu_agent.py` enforces the rule.
py_binary(
    name = "gpu_agent",
    srcs = ["run/gpu_agent.py"],
    main = "run/gpu_agent.py",
    visibility = ["//visibility:public"],
)

# The checks ask a model something and judge the answer: gate 1, the smoke check, the model fit and
# the catalog checks.  They send requests through `run`'s ports and never write a run file.
py_library(
    name = "checks",
    srcs = glob(["checks/*.py"]),
    visibility = ["//visibility:public"],
    deps = [
        ":core",
        ":metrics",
        ":run",
    ] + _APP,
)
