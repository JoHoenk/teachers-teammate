"""Cross-platform GPU detection for NVIDIA and AMD GPUs.

Uses command-line tools that ship with the respective drivers rather than any
Python package so that detection works before any addon is installed:
  - NVIDIA: nvidia-smi  (available on Linux, Windows, macOS with NVIDIA driver)
  - AMD:    rocm-smi    (Linux/Windows with ROCm < 5.7)
            amd-smi     (Linux/Windows with ROCm 5.7+)
  - Apple:  unified memory via psutil (Apple Silicon shares RAM between CPU/GPU)
"""

from __future__ import annotations

from dataclasses import dataclass
import platform
import subprocess


@dataclass
class GpuInfo:
    vendor: str  # "nvidia" or "amd"
    name: str
    #: Dedicated graphics memory in MB, or None when it could not be read.
    vram_mb: int | None = None


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


def _parse_vram_mb(value: str) -> int | None:
    """Return *value* as an int number of MB, or None when non-numeric."""
    try:
        return int(value.strip())
    except ValueError:
        return None


def detect_nvidia() -> list[GpuInfo]:
    """Return detected NVIDIA GPUs via nvidia-smi (cross-platform).

    The query returns ``"name, memory_total_mib"`` per line; a non-numeric
    memory value (for example ``[N/A]``) yields ``vram_mb=None``.
    """
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if not out:
        return []
    gpus: list[GpuInfo] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, _, mem_raw = line.rpartition(",")
        name = name.strip()
        if not name:
            continue
        gpus.append(GpuInfo("nvidia", name, _parse_vram_mb(mem_raw)))
    return gpus


def _parse_rocm_smi(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        # "GPU[0]          : Card series:      AMD Radeon RX 6800 XT"
        if "Card series" in line and ":" in line:
            names.append(line.split(":", 2)[-1].strip())
    return names


def _parse_rocm_smi_vram(output: str) -> list[int]:
    """Return VRAM in MB per GPU, in GPU order, from ``rocm-smi --showmeminfo vram``.

    Typical line: ``GPU[0] : VRAM Total Memory (B): 17163091968`` (bytes).
    Lines without a parseable number are skipped.
    """
    values: list[int] = []
    for line in output.splitlines():
        if "VRAM Total Memory" not in line or ":" not in line:
            continue
        raw = line.rsplit(":", 1)[-1].strip()
        try:
            values.append(int(float(raw) / 2**20))
        except ValueError:
            continue
    return values


def _parse_amd_smi(output: str) -> list[str]:
    names: list[str] = []
    for line in output.splitlines():
        # "    MARKET_NAME: AMD Radeon RX 7900 XTX"
        if "market_name" in line.lower() and ":" in line:
            names.append(line.split(":", 1)[1].strip())
    return names


def _parse_amd_smi_vram(output: str) -> list[int]:
    """Return VRAM in MB per GPU, in GPU order, from ``amd-smi static --vram``.

    Typical line: ``SIZE: 16368 MB``.  Lines without a parseable number are
    skipped.
    """
    values: list[int] = []
    for line in output.splitlines():
        if "size" not in line.lower() or ":" not in line:
            continue
        raw = line.split(":", 1)[1].strip()
        raw_mb = raw.removesuffix("MB").strip()
        mb = _parse_vram_mb(raw_mb)
        if mb is not None:
            values.append(mb)
    return values


def _match_by_index(names: list[str], vram_values: list[int]) -> list[GpuInfo]:
    """Zip *names* with *vram_values*; missing entries get ``vram_mb=None``."""
    return [
        GpuInfo("amd", name, vram_values[i] if i < len(vram_values) else None)
        for i, name in enumerate(names)
    ]


def detect_amd() -> list[GpuInfo]:
    """Return detected AMD GPUs via rocm-smi or amd-smi (Linux/Windows with ROCm).

    Memory values are fetched from a separate query and matched to the names by
    index; a GPU without a memory entry gets ``vram_mb=None``.
    """
    out = _run(["rocm-smi", "--showproductname"])
    if out:
        names = _parse_rocm_smi(out)
        if not names:
            # rocm-smi found but output format differs — report generic detection
            return [GpuInfo("amd", "AMD GPU")]
        vram = _parse_rocm_smi_vram(_run(["rocm-smi", "--showmeminfo", "vram"]) or "")
        return _match_by_index(names, vram)

    out = _run(["amd-smi", "static", "--asic"])
    if out:
        names = _parse_amd_smi(out)
        if not names:
            return [GpuInfo("amd", "AMD GPU")]
        vram = _parse_amd_smi_vram(_run(["amd-smi", "static", "--vram"]) or "")
        return _match_by_index(names, vram)

    return []


def detect_apple_unified_memory_gb() -> float | None:
    """Return total unified memory (GB) on Apple Silicon, or None elsewhere.

    On Apple Silicon the GPU shares system RAM; the total is the upper bound
    for what a model can use, so it feeds the same profile thresholds as
    dedicated GPU memory (scaled by the Apple GPU share, see
    :mod:`teachers_teammate.domain.model_catalog`).
    """
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return None
    try:
        import psutil  # noqa: PLC0415

        return psutil.virtual_memory().total / 2**30
    except Exception:  # noqa: BLE001  # psutil may be missing in stripped environments; degrade to None
        return None


def detect_gpus() -> list[GpuInfo]:
    """Return all detected NVIDIA and AMD GPUs."""
    return detect_nvidia() + detect_amd()
