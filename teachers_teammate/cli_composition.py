"""CLI composition helpers for bootstrapping runtime dependencies."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import sys

from .application.composition import AppServices, build_app_services
from .cli import main


def build_cli_app_service() -> AppServices:
    """Return the application service the CLI entrypoints use."""
    return build_app_services()


def run_cli_entrypoint(
    *,
    argv: Sequence[str] | None = None,
    exit_fn: Callable[[int], None] = sys.exit,
) -> None:
    """Run CLI entrypoint with composed dependencies."""
    main(app_service=build_cli_app_service(), argv=argv, exit_fn=exit_fn)
