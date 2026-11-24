"""CLI composition helpers for bootstrapping runtime dependencies."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import sys

from .application.composition import AppServices, build_app_services
from .cli import exit_on_version, main


def build_cli_app_service() -> AppServices:
    """Return the application's services, as the CLI entrypoints use them."""
    return build_app_services()


def run_cli_entrypoint(
    *,
    argv: Sequence[str] | None = None,
    exit_fn: Callable[[int], None] = sys.exit,
) -> None:
    """Run CLI entrypoint with composed dependencies."""
    exit_on_version(argv)
    main(app_service=build_cli_app_service(), argv=argv, exit_fn=exit_fn)
