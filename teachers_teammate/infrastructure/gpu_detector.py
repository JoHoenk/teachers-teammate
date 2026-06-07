"""Cross-platform GPU detection for NVIDIA and AMD GPUs.

Uses command-line tools that ship with the respective drivers rather than any
Python package so that detection works before any addon is installed:
  - NVIDIA: nvidia-smi  (available on Linux, Windows, macOS with NVIDIA driver)
  - AMD:    rocm-smi    (Linux/Windows with ROCm < 5.7)
            amd-smi     (Linux/Windows with ROCm 5.7+)
"""

from __future__ import annotations

from dataclasses import dataclass
import subprocess


@dataclass
class GpuInfo:
    vendor: str  # "nvidia" or "amd"
    name: str


def _run(cmd: list[str], timeout: int = 5) -> str | None:
    """Run *cmd*, return stripped stdout on success, None on any failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def detect_nvidia() -> list[GpuInfo]:
    """Return detected NVIDIA GPUs via nvidia-smi (cross-platform)."""
    out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"])
    if out:
        return [GpuInfo("nvidia", line.strip()) for line in out.splitlines() if line.strip()]
    return []


def _parse_rocm_smi(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        # "GPU[0]          : Card series:      AMD Radeon RX 6800 XT"
        if "Card series" in line and ":" in line:
            names.append(line.split(":", 2)[-1].strip())
    return names


def _parse_amd_smi(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        # "    MARKET_NAME: AMD Radeon RX 7900 XTX"
        if "market_name" in line.lower() and ":" in line:
            names.append(line.split(":", 1)[1].strip())
    return names


def detect_amd() -> list[GpuInfo]:
    """Return detected AMD GPUs via rocm-smi or amd-smi (Linux/Windows with ROCm)."""
    out = _run(["rocm-smi", "--showproductname"])
    if out:
        names = _parse_rocm_smi(out)
        if names:
            return [GpuInfo("amd", n) for n in names]
        # rocm-smi found but output format differs — report generic detection
        return [GpuInfo("amd", "AMD GPU")]

    out = _run(["amd-smi", "static", "--asic"])
    if out:
        names = _parse_amd_smi(out)
        if names:
            return [GpuInfo("amd", n) for n in names]
        return [GpuInfo("amd", "AMD GPU")]

    return []


def detect_gpus() -> list[GpuInfo]:
    """Return all detected NVIDIA and AMD GPUs."""
    return detect_nvidia() + detect_amd()
