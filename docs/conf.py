# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
from pathlib import Path

from python.runfiles import Runfiles

root_doc = "index"

project = "Teacher's Teammate"
author = "Teacher's Teammate contributors"
from teachers_teammate import __version__ as release  # noqa: PLC0415, E402

extensions = [
    "myst_parser",
    "sphinxcontrib.plantuml",
    "trlc",
]

# MyST settings — enable extra Markdown syntax
myst_enable_extensions = ["colon_fence", "deflist"]

# Resolve the Maven-fetched plantuml binary via Bazel runfiles so the
# version pinned in MODULE.bazel is used instead of any bundled fallback.
_r = Runfiles.Create()
_plantuml_path = None
for _candidate_key in [os.environ.get("TEST_WORKSPACE"), "_main"]:
    if not _candidate_key:
        continue
    _resolved = _r.Rlocation(f"{_candidate_key}/tools/sphinx/plantuml", source_repo="")
    if _resolved and Path(_resolved).exists():
        _plantuml_path = _resolved
        break

if _plantuml_path is None:
    raise ValueError(
        "Could not find plantuml binary via runfiles. "
        "Ensure //tools/sphinx:plantuml is listed in sphinx_build_binary data."
    )

plantuml = _plantuml_path
# Inline SVG so CSS can control sizing and overflow scrolling.
plantuml_output_format = "svg_img"

html_theme = "furo"
html_title = "Teacher's Teammate"
html_favicon = "assets/teachers_teammate.png"
html_static_path = ["_static"]
html_css_files = ["plantuml.css"]
html_js_files = ["plantuml_click.js"]
