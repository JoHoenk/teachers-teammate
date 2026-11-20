"""Build and release tools.

This file makes ``tools`` the root of its own packages.  Without it PyInstaller, which starts
from ``tools/build/run_gui.py`` and walks up while a folder holds an ``__init__.py``, stops at
``tools/build`` and puts ``tools/`` itself on the module search path.  ``tools/packaging`` then
hides the ``packaging`` library from the bundle, and the app fails when it loads its stylesheet.
"""
