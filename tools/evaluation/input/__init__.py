"""What a test set is: the corpora it draws on, the recipe that chooses, and the manifest.

The layer below `store`: it reads corpora and writes `testsets/<id>/` and the committed manifest,
and it never reads a run.
"""
