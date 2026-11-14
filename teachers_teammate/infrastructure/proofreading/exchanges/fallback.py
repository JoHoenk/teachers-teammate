"""One exchange that falls back to another when it cannot proofread a text.

The prepared one-request exchange (R) is the case: when its answer is unusable, the text is still
corrected by step ① of T, and the teacher gets the corrections without the explanations.  The
chain is a decorator, so the caller sees one exchange and one result.
"""

from __future__ import annotations

from dataclasses import replace

from ....domain.proofreading.result import ExchangeKind
from .base import Exchange, ExchangeCost, ExchangeResult, ProofreadContext


class FallbackExchange:
    """Runs *primary*; when it fails, runs *fallback* and keeps both sets of reports."""

    def __init__(self, primary: Exchange, fallback: Exchange) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def kind(self) -> ExchangeKind:
        """Return the kind of the primary exchange: that is what the profile names."""
        return self._primary.kind

    def proofread(self, source: str, ctx: ProofreadContext) -> ExchangeResult:
        """Return the primary's result, or the fallback's when the primary failed.

        A request that never reached the model ends the file: the fallback would ask the same
        unreachable server.  When the fallback does answer, the primary's warnings are dropped —
        they say the text was kept, and it was not — and when it fails too, the reasons of both
        are shown once each.
        """
        result = self._primary.proofread(source, ctx)
        if not result.failed or any(report.failure == "transport" for report in result.reports):
            return result
        second = self._fallback.proofread(source, ctx)
        warnings = second.warnings
        if second.failed:
            fresh = tuple(warning for warning in second.warnings if warning not in result.warnings)
            warnings = result.warnings + fresh
        return replace(second, warnings=warnings, reports=result.reports + second.reports)

    def max_text_chars(self, ctx: ProofreadContext) -> int | None:
        """Return the smaller budget of the two, because either may carry the text."""
        first = self._primary.max_text_chars(ctx)
        second = self._fallback.max_text_chars(ctx)
        if first is None:
            return second
        if second is None:
            return first
        return min(first, second)

    def cost_model(self) -> ExchangeCost:
        """Return the primary's cost: the fallback runs only when the primary failed."""
        return self._primary.cost_model()
