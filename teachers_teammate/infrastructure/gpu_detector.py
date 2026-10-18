"""Cross-platform GPU detection for NVIDIA and AMD GPUs.

Uses what the operating system and the drivers already provide rather than any
Python package so that detection works before any addon is installed.  Sources,
in the order they are tried:
  - Windows: the display-adapter registry keys (vendor, name and VRAM of every
    vendor; starts no process, so no console window can flash up)
  - Linux:   sysfs ``/sys/class/drm`` (vendor of every card, VRAM of AMD)
  - NVIDIA:  nvidia-smi  (needed on Linux for name and memory; Windows fallback)
  - AMD:     rocm-smi    (ROCm < 5.7) / amd-smi (ROCm 5.7+): the marketing name on
             Linux, and the fallback when the operating system has no answer

AMD integrated graphics (a small slice of system RAM) is not reported: it cannot run
a model and would otherwise hide a discrete card whose memory is unknown.
  - Apple:   unified memory via psutil (Apple Silicon shares RAM between CPU/GPU)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any

from .subprocess_utils import no_console_window_kwargs


@dataclass
class GpuInfo:
    vendor: str  # "nvidia" or "amd"
    name: str
    #: Dedicated graphics memory in MB, or None when it could not be read.
    vram_mb: int | None = None


_VENDOR_IDS = {0x10DE: "nvidia", 0x1002: "amd"}

#: Windows class key of display adapters (subkeys ``0000``, ``0001``, …).
_WIN_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)
#: Windows key that holds one subkey per device instance; its volatile ``Control``
#: subkey (value ``ActiveService``) exists only while the device is started.
_WIN_ENUM_KEY = r"SYSTEM\CurrentControlSet\Enum"
_SYSFS_DRM = Path("/sys/class/drm")

#: AMD adapters with less dedicated memory than this are integrated graphics (the
#: BIOS carve-out of system RAM, typically 512 MB) and are not reported.
_MIN_DEDICATED_AMD_VRAM_MB = 1024
#: The 32-bit ``HardwareInformation.MemorySize`` registry value saturates at 4 GB.
_REGISTRY_DWORD_CAP_MB = 4095


def _run(cmd: list[str], timeout: int = 5) -> str | None:
    """Run *cmd*, return stripped stdout on success, None on any failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **no_console_window_kwargs(),
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


def _vendor_from_pci_id(match_id: str) -> str | None:
    """Return ``"nvidia"``/``"amd"`` for a ``PCI\\VEN_xxxx&DEV_…`` id, else None."""
    found = re.search(r"VEN_([0-9A-F]{4})", match_id.upper())
    return _VENDOR_IDS.get(int(found.group(1), 16)) if found else None


def _bytes_to_mb(value: object) -> int | None:
    """Convert a size in bytes (int, or little-endian registry bytes) to MB."""
    if isinstance(value, (bytes, bytearray)):
        value = int.from_bytes(value, "little")
    if not isinstance(value, int) or value <= 0:
        return None
    return value // 2**20


def _windows_device_is_active(winreg: Any, instance_id: str) -> bool:
    """Return False for an adapter the registry remembers but that is not running.

    The display class key keeps the entry of a card that was unplugged or disabled.
    A device that is started has the volatile ``Enum\\<instance>\\Control`` key with an
    ``ActiveService`` value.  When the instance cannot be looked up at all, the
    adapter counts as active: showing a stale card is better than hiding a real one.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{_WIN_ENUM_KEY}\{instance_id}"):
            pass
    except OSError:
        return True
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"{_WIN_ENUM_KEY}\{instance_id}\Control",
        ) as control:
            winreg.QueryValueEx(control, "ActiveService")
    except OSError:
        return False
    return True


def _read_windows_adapters() -> list[GpuInfo] | None:
    """Return running NVIDIA/AMD display adapters from the Windows registry (no process).

    Drivers write ``HardwareInformation.qwMemorySize`` (64-bit); very old ones only
    write the 32-bit ``MemorySize``, which saturates at 4 GB, so a value at the cap is
    reported as unknown for nvidia-smi to complete.  Other adapters (Intel, virtual
    displays such as the Microsoft Basic Display Adapter) and adapters that are not
    running are ignored.  Returns ``None`` off Windows or when the registry cannot
    be read, so callers can tell "no such adapter" (``[]``) from "do not know".
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg  # noqa: PLC0415
    except ImportError:
        return None

    gpus: list[GpuInfo] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _WIN_DISPLAY_CLASS_KEY) as cls:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(cls, index)
                except OSError:
                    break  # no more subkeys
                index += 1
                if not sub.isdigit():  # skip "Configuration", "Properties"
                    continue
                try:
                    with winreg.OpenKey(cls, sub) as adapter:
                        match_id = str(winreg.QueryValueEx(adapter, "MatchingDeviceId")[0])
                        vendor = _vendor_from_pci_id(match_id)
                        if vendor is None:
                            continue
                        try:
                            instance = str(winreg.QueryValueEx(adapter, "DeviceInstanceID")[0])
                        except OSError:
                            instance = ""
                        if instance and not _windows_device_is_active(winreg, instance):
                            continue
                        name = str(winreg.QueryValueEx(adapter, "DriverDesc")[0]).strip()
                        vram: int | None = None
                        for value_name in (
                            "HardwareInformation.qwMemorySize",
                            "HardwareInformation.MemorySize",
                        ):
                            try:
                                vram = _bytes_to_mb(winreg.QueryValueEx(adapter, value_name)[0])
                            except OSError:
                                continue
                            if vram is not None:
                                if value_name.endswith(".MemorySize") and (
                                    vram >= _REGISTRY_DWORD_CAP_MB
                                ):
                                    vram = None  # saturated 32-bit value: really "4 GB or more"
                                break
                        gpus.append(GpuInfo(vendor, name or f"{vendor.upper()} GPU", vram))
                except OSError:
                    continue  # adapter key unreadable or without the expected values
    except OSError:
        return None
    return gpus


def _read_sysfs_adapters(root: Path = _SYSFS_DRM) -> list[GpuInfo]:
    """Return NVIDIA/AMD cards from Linux sysfs (no process started).

    The vendor is read for every card.  Only the AMD driver publishes the VRAM
    size (``mem_info_vram_total``, bytes); NVIDIA cards get ``vram_mb=None`` and
    are completed by nvidia-smi.  sysfs rarely has a marketing name (``product_name``
    exists on some AMD cards), so the generic ``"NVIDIA GPU"``/``"AMD GPU"`` is used
    until a vendor tool supplies one (see :func:`_prefer_complete`).
    """
    try:
        cards = sorted(path for path in root.iterdir() if re.fullmatch(r"card\d+", path.name))
    except OSError:
        return []
    gpus: list[GpuInfo] = []
    for card in cards:
        device = card / "device"
        try:
            vendor = _VENDOR_IDS.get(int((device / "vendor").read_text().strip(), 16))
        except (OSError, ValueError):
            continue
        if vendor is None:
            continue
        vram: int | None = None
        if vendor == "amd":
            try:
                vram = _bytes_to_mb(int((device / "mem_info_vram_total").read_text()))
            except (OSError, ValueError):
                vram = None
        name = _generic_name(vendor)
        try:
            name = (device / "product_name").read_text().strip() or name
        except OSError:
            pass
        gpus.append(GpuInfo(vendor, name, vram))
    return gpus


def _generic_name(vendor: str) -> str:
    return f"{vendor.upper()} GPU"


def _is_integrated(gpu: GpuInfo) -> bool:
    """Return True for AMD integrated graphics (known, tiny dedicated memory)."""
    return (
        gpu.vendor == "amd" and gpu.vram_mb is not None and gpu.vram_mb < _MIN_DEDICATED_AMD_VRAM_MB
    )


def _prefer_complete(os_gpus: list[GpuInfo], tool: Callable[[], list[GpuInfo]]) -> list[GpuInfo]:
    """Return *os_gpus* when they are complete; otherwise ask *tool*.

    Complete means every GPU has its VRAM and a real name (not the generic one).
    The vendor tool (``nvidia-smi`` …) is a process and only runs when the operating
    system could not give a complete answer.  Where the tool answers, its names win
    and a memory size it could not read is taken over from the operating system;
    where it does not, the operating system's answer stands.
    """
    if os_gpus and all(
        gpu.vram_mb is not None and gpu.name != _generic_name(gpu.vendor) for gpu in os_gpus
    ):
        return os_gpus
    from_tool = tool()
    if not from_tool:
        return os_gpus
    if len(from_tool) == len(os_gpus):
        return [
            GpuInfo(tool_gpu.vendor, tool_gpu.name, tool_gpu.vram_mb or os_gpu.vram_mb)
            for tool_gpu, os_gpu in zip(from_tool, os_gpus, strict=True)
        ]
    return from_tool


def detect_gpus() -> list[GpuInfo]:
    """Return all detected NVIDIA and AMD GPUs.

    The operating system is asked first (Windows registry / Linux sysfs); a
    vendor tool only runs for a vendor whose memory it could not provide.  A
    readable Windows registry is authoritative: a vendor missing from it is
    absent and its tool is not started (no process, no console window).  sysfs is
    not (it can be empty under WSL2 while nvidia-smi works), so there a missing
    vendor is still looked up with its tool.  AMD integrated graphics is dropped.
    """
    registry = _read_windows_adapters() if sys.platform == "win32" else None
    if registry is not None:
        os_gpus = registry
    elif sys.platform.startswith("linux"):
        os_gpus = _read_sysfs_adapters()
    else:
        os_gpus = []

    def for_vendor(vendor: str, tool: Callable[[], list[GpuInfo]]) -> list[GpuInfo]:
        found = [gpu for gpu in os_gpus if gpu.vendor == vendor and not _is_integrated(gpu)]
        if registry is not None and not found:
            return []
        return [gpu for gpu in _prefer_complete(found, tool) if not _is_integrated(gpu)]

    return for_vendor("nvidia", detect_nvidia) + for_vendor("amd", detect_amd)
