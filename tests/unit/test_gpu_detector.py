"""Unit tests for teachers_teammate.infrastructure.gpu_detector."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import gpu_detector
from teachers_teammate.infrastructure.gpu_detector import (
    GpuInfo,
    _parse_amd_smi,
    _parse_rocm_smi,
    _run,
    detect_amd,
    detect_gpus,
    detect_nvidia,
)

# ── _parse_rocm_smi ────────────────────────────────────────────────────────


def test_parse_rocm_smi_extracts_card_series_names() -> None:
    """
    Given  rocm-smi output with two 'Card series' lines
    When   _parse_rocm_smi is called
    Then   both GPU names are extracted in order
    """
    output = (
        "GPU[0]          : Card series:      AMD Radeon RX 6800 XT\n"
        "GPU[1]          : Card series:      AMD Radeon RX 7900 XTX\n"
    )
    assert _parse_rocm_smi(output) == ["AMD Radeon RX 6800 XT", "AMD Radeon RX 7900 XTX"]


def test_parse_rocm_smi_ignores_unrelated_and_blank_lines() -> None:
    """
    Given  rocm-smi output with header noise and blank lines
    When   _parse_rocm_smi is called
    Then   only 'Card series' lines contribute names
    """
    output = "===== ROCm System Management =====\n\nGPU[0] : Card series:  Radeon\nDone\n"
    assert _parse_rocm_smi(output) == ["Radeon"]


def test_parse_rocm_smi_returns_empty_when_no_match() -> None:
    """
    Given  rocm-smi output with no 'Card series' line
    When   _parse_rocm_smi is called
    Then   an empty list is returned
    """
    assert _parse_rocm_smi("nothing useful here\n") == []


# ── _parse_amd_smi ─────────────────────────────────────────────────────────


def test_parse_amd_smi_extracts_market_name() -> None:
    """
    Given  amd-smi output with MARKET_NAME lines (mixed case key)
    When   _parse_amd_smi is called
    Then   the market names are extracted
    """
    output = "    MARKET_NAME: AMD Radeon RX 7900 XTX\n    market_name: AMD Instinct MI300X\n"
    assert _parse_amd_smi(output) == ["AMD Radeon RX 7900 XTX", "AMD Instinct MI300X"]


def test_parse_amd_smi_returns_empty_when_no_match() -> None:
    """
    Given  amd-smi output without a market_name line
    When   _parse_amd_smi is called
    Then   an empty list is returned
    """
    assert _parse_amd_smi("ASIC: some-asic\n") == []


# ── _run ───────────────────────────────────────────────────────────────────


def test_run_returns_stripped_stdout_on_success() -> None:
    """
    Given  subprocess.run succeeds with whitespace-padded stdout
    When   _run is called
    Then   the stripped stdout is returned
    """
    fake = MagicMock(returncode=0, stdout="  hello  \n")
    with patch("subprocess.run", return_value=fake):
        assert _run(["anything"]) == "hello"


def test_run_returns_none_on_nonzero_returncode() -> None:
    """
    Given  subprocess.run exits with a non-zero return code
    When   _run is called
    Then   None is returned
    """
    fake = MagicMock(returncode=1, stdout="ignored")
    with patch("subprocess.run", return_value=fake):
        assert _run(["anything"]) is None


def test_run_returns_none_when_binary_missing() -> None:
    """
    Given  subprocess.run raises FileNotFoundError (binary not on PATH)
    When   _run is called
    Then   None is returned
    """
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert _run(["missing-binary"]) is None


def test_run_returns_none_on_timeout() -> None:
    """
    Given  subprocess.run raises TimeoutExpired
    When   _run is called
    Then   None is returned
    """
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5)):
        assert _run(["slow"]) is None


# ── detect_nvidia ──────────────────────────────────────────────────────────


def test_detect_nvidia_tags_each_line_as_nvidia(monkeypatch) -> None:
    """
    Given  nvidia-smi returns two GPU name lines
    When   detect_nvidia is called
    Then   each is returned as a GpuInfo tagged 'nvidia'
    """
    monkeypatch.setattr(
        gpu_detector, "_run", lambda *_a, **_k: "GeForce RTX 4090\nGeForce RTX 3080\n"
    )
    result = detect_nvidia()
    assert result == [GpuInfo("nvidia", "GeForce RTX 4090"), GpuInfo("nvidia", "GeForce RTX 3080")]


def test_detect_nvidia_returns_empty_when_no_driver(monkeypatch) -> None:
    """
    Given  nvidia-smi is unavailable (_run returns None)
    When   detect_nvidia is called
    Then   an empty list is returned
    """
    monkeypatch.setattr(gpu_detector, "_run", lambda *_a, **_k: None)
    assert detect_nvidia() == []


# ── detect_amd ─────────────────────────────────────────────────────────────


def test_detect_amd_uses_rocm_smi_first(monkeypatch) -> None:
    """
    Given  rocm-smi returns a parseable card series
    When   detect_amd is called
    Then   the AMD GPU is returned without falling back to amd-smi
    """
    monkeypatch.setattr(
        gpu_detector,
        "_run",
        lambda cmd, **_k: "GPU[0] : Card series: Radeon RX 6800 XT" if "rocm-smi" in cmd else None,
    )
    assert detect_amd() == [GpuInfo("amd", "Radeon RX 6800 XT")]


def test_detect_amd_rocm_smi_generic_fallback_when_unparseable(monkeypatch) -> None:
    """
    Given  rocm-smi runs but its output format is not parseable
    When   detect_amd is called
    Then   a single generic 'AMD GPU' entry is returned
    """
    monkeypatch.setattr(
        gpu_detector,
        "_run",
        lambda cmd, **_k: "unexpected output" if "rocm-smi" in cmd else None,
    )
    assert detect_amd() == [GpuInfo("amd", "AMD GPU")]


def test_detect_amd_falls_back_to_amd_smi(monkeypatch) -> None:
    """
    Given  rocm-smi is unavailable but amd-smi returns a market name
    When   detect_amd is called
    Then   the amd-smi GPU is returned
    """

    def fake_run(cmd, **_k):
        if "amd-smi" in cmd:
            return "    MARKET_NAME: AMD Radeon RX 7900 XTX"
        return None

    monkeypatch.setattr(gpu_detector, "_run", fake_run)
    assert detect_amd() == [GpuInfo("amd", "AMD Radeon RX 7900 XTX")]


def test_detect_amd_amd_smi_generic_fallback(monkeypatch) -> None:
    """
    Given  rocm-smi missing and amd-smi output is unparseable
    When   detect_amd is called
    Then   a single generic 'AMD GPU' entry is returned
    """

    def fake_run(cmd, **_k):
        if "amd-smi" in cmd:
            return "no market name field"
        return None

    monkeypatch.setattr(gpu_detector, "_run", fake_run)
    assert detect_amd() == [GpuInfo("amd", "AMD GPU")]


def test_detect_amd_returns_empty_when_no_tools(monkeypatch) -> None:
    """
    Given  neither rocm-smi nor amd-smi is available
    When   detect_amd is called
    Then   an empty list is returned
    """
    monkeypatch.setattr(gpu_detector, "_run", lambda *_a, **_k: None)
    assert detect_amd() == []


# ── detect_gpus ────────────────────────────────────────────────────────────


@pytest.mark.use_case("System_Resource_Monitoring")
def test_detect_gpus_combines_nvidia_and_amd(monkeypatch) -> None:
    """
    Given  one NVIDIA and one AMD GPU are detected
    When   detect_gpus is called
    Then   both are returned, NVIDIA first
    """
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [GpuInfo("nvidia", "RTX 4090")])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [GpuInfo("amd", "RX 7900")])
    assert detect_gpus() == [GpuInfo("nvidia", "RTX 4090"), GpuInfo("amd", "RX 7900")]


def test_detect_gpus_returns_empty_when_none_detected(monkeypatch) -> None:
    """
    Given  no GPUs are detected by either vendor
    When   detect_gpus is called
    Then   an empty list is returned
    """
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == []
