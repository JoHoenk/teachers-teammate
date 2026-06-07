"""CLI composition helpers for bootstrapping runtime dependencies."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import sys

from .application.service import ProcessingApplicationService
from .cli import main


def build_cli_app_service() -> ProcessingApplicationService:
    """Create the default application service used by CLI entrypoints."""
    return ProcessingApplicationService()


def run_cli_entrypoint(
    *,
    argv: Sequence[str] | None = None,
    exit_fn: Callable[[int], None] = sys.exit,
) -> None:
    """Run CLI entrypoint with composed dependencies."""
    main(app_service=build_cli_app_service(), argv=argv, exit_fn=exit_fn)
