"""One widget per tab of the Add-ons page, plus the pane that shows its install round.

The page (:mod:`.._addons_page`) builds these widgets, hands each tab what a deep link asked
for, and keeps the page protocol; a tab owns its own rows, boxes, worker threads and signal
wiring, so a change on one tab cannot reach into another.  The install round itself is not
here: it comes from outside (:mod:`..._install_round`) and is only shown
(:class:`InstallStatusPane`).
"""

from ._common import is_model_tag
from ._detection import detect_installed
from ._install_status import InstallStatusPane
from ._name_detection_tab import NameDetectionTab
from ._ollama_models_tab import OllamaModelsTab
from ._providers_tab import ProvidersTab
from ._reading_models_tab import ReadingModelsTab
from ._update_banner import UpdateBanner

__all__ = [
    "InstallStatusPane",
    "NameDetectionTab",
    "OllamaModelsTab",
    "ProvidersTab",
    "ReadingModelsTab",
    "UpdateBanner",
    "detect_installed",
    "is_model_tag",
]
