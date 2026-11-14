"""Image preparation as an ordered list of steps.

``steps.py`` holds the machinery (a :class:`Step`, the :class:`StepPipeline` that runs a list
of them and implements :class:`~teachers_teammate.interfaces.ImagePreprocessor`), ``auto.py``
the automatic preparation (the steps, their thresholds, ``AUTO_VERSION``), ``page_models.py``
the two models some of its steps use, and ``recipes.py`` the list the settings ask for.  The
evaluation tool builds its own :class:`StepPipeline` from any list of steps.
"""
