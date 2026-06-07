"""Unit tests for teachers_teammate.cli_composition."""

from __future__ import annotations


def test_run_cli_entrypoint_uses_composed_app_service(monkeypatch) -> None:
    """
    Given  run_cli_entrypoint with patched builder and main function
    When   run_cli_entrypoint() is called
    Then   main() receives the built app service and forwards argv/exit_fn
    """
    import teachers_teammate.cli_composition as mod  # noqa: PLC0415

    fake_service = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(mod, "build_cli_app_service", lambda: fake_service)

    def _fake_main(*, app_service, argv=None, exit_fn=None):
        captured["app_service"] = app_service
        captured["argv"] = argv
        captured["exit_fn"] = exit_fn

    monkeypatch.setattr(mod, "main", _fake_main)

    def exit_fn(_code: int) -> None:
        return None

    mod.run_cli_entrypoint(argv=["--help"], exit_fn=exit_fn)

    assert captured["app_service"] is fake_service
    assert captured["argv"] == ["--help"]
    assert captured["exit_fn"] is exit_fn
