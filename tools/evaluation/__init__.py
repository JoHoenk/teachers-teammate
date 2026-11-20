"""The evaluation tooling, in layers: core, metrics, input, store, run, report, publish, checks.

One layer per job, and a layer imports only the layers below it, which the Bazel targets of
``//tools/evaluation`` enforce.

```
publish → report → store → input → core, metrics
run → store, input, core, metrics            checks → run, core, metrics
```

``core`` holds the typed records of every format, the axes and their names, and the lookups every
layer shares.  ``metrics`` holds the pure measurements.  Neither reads a run, sends a request or
writes a file.
"""
