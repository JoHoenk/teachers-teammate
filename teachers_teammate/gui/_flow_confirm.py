"""The question before text leaves this computer: a run asks it, so does a model comparison.

Both send a document's content to a model, so both ask the same question, with the same remembered
"don't ask again" answers: one place words it and one place decides whether it is asked.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QWidget

from ..application.composition import AppServices
from ..application.model_comparison import Candidate
from ..config import Config
from ..domain.data_location import DataFlow
from ..texts import app as app_text
from ..texts import dialogs as dlg
from ..texts.privacy import advice_text, level_text, stage_badge
from . import _dialogs
from ._privacy_settings import FlowAcknowledgements

__all__ = ["confirm_comparison_flow", "confirm_data_flow"]


def confirm_data_flow(
    window: QWidget, flow: DataFlow, acknowledgements: FlowAcknowledgements
) -> bool:
    """Ask once before content leaves this computer; return whether the work may go on.

    Nothing is asked when *flow* stays on this computer or the teacher chose not to be asked about
    it again.  A tick in the question remembers the answer for this exact flow.
    """
    if not flow.leaves_computer or flow.fingerprint in acknowledgements.fingerprints():
        return True
    question = dlg.documents_leave_computer(
        level_text(flow.level),
        [(app_text.stage_title(stage.stage), stage_badge(stage)) for stage in flow.outbound],
        [advice_text(tip) for tip in flow.advice],
    )
    answer = _dialogs.choose(window, question)
    if not answer.accepted:
        return False
    if answer.checked:
        acknowledgements.remember(flow)
    return True


def confirm_comparison_flow(
    window: QWidget,
    services: AppServices,
    acknowledgements: FlowAcknowledgements,
    config: Config,
    candidates: Sequence[Candidate],
) -> bool:
    """Ask before the text goes to the models of a comparison that are not on this computer.

    One question per distinct flow, as the run asks it, so a teacher who agreed once to a provider
    is not asked again for it.
    """
    asked: set[str] = set()
    for candidate in candidates:
        flow = services.data_flow.describe_data_flow(
            services.comparison.data_flow_config(config, candidate)
        )
        if flow.fingerprint in asked:
            continue
        asked.add(flow.fingerprint)
        if not confirm_data_flow(window, flow, acknowledgements):
            return False
    return True
