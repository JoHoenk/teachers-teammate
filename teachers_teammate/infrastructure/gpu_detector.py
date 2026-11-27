"""Cross-platform GPU detection for NVIDIA and AMD GPUs, and their live load.

Uses what the operating system and the drivers already provide rather than any
Python package so that detection works before any addon is installed.  Sources,
in the order they are tried:
  - Windows: the display-adapter registry keys (vendor, name and VRAM of every
    vendor; starts no process, so no console window can flash up)
  - Linux:   sysfs ``/sys/class/drm`` (vendor of every card, VRAM of AMD)
  - NVIDIA:  nvidia-smi  (needed on Linux for name and memory; Windows fallback; also
             looked up at its usual install paths when it is not on PATH), then the
             bundled ``pynvml`` package (``nvidia-ml-py``)
  - AMD:     rocm-smi    (ROCm < 5.7) / amd-smi (ROCm 5.7+): the marketing name on
             Linux, and the fallback when the operating system has no answer

AMD integrated graphics (a small slice of system RAM) is not reported: it cannot run
a model and would otherwise hide a discrete card whose memory is unknown.
  - Apple:   unified memory via psutil (Apple Silicon shares RAM between CPU/GPU)

The live load of the System Monitor (:func:`read_gpu_usage`) needs no add-on either:
NVML for NVIDIA, and the ``amdgpu`` driver's own sysfs files for AMD.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any

from ..domain.model_catalog import GpuInfo, GpuUsage, SystemUsage
from .subprocess_utils import child_env, no_console_window_kwargs

_logger = logging.getLogger(__name__)

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
            env=child_env(),
            **no_console_window_kwargs(),
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except FileNotFoundError, subprocess.TimeoutExpired, OSError:
        _logger.debug("The vendor tool %s does not answer", cmd[0])
        return None


def _parse_vram_mb(value: str) -> int | None:
    """Return *value* as an int number of MB, or None when non-numeric."""
    try:
        return int(value.strip())
    except ValueError:
        _logger.debug("Not a memory size: '%s'", value)
        return None


def _nvidia_smi_commands() -> list[str]:
    """Return the ``nvidia-smi`` programs to try, in order.

    ``PATH`` first.  A packaged app can start with a ``PATH`` that lacks the driver folder
    although the tool is installed, so on Windows its two usual locations follow.
    """
    commands = ["nvidia-smi"]
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        commands += [
            str(Path(system_root) / "System32" / "nvidia-smi.exe"),
            str(Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"),
        ]
    return commands


def _parse_nvidia_smi(out: str) -> list[GpuInfo]:
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


def _detect_nvidia_nvml() -> list[GpuInfo]:
    """Return NVIDIA GPUs through the optional ``pynvml`` package (empty when unavailable)."""
    try:
        import pynvml  # noqa: PLC0415
    except ImportError:
        return []
    try:
        pynvml.nvmlInit()
        try:
            gpus: list[GpuInfo] = []
            for index in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", "replace")
                total = pynvml.nvmlDeviceGetMemoryInfo(handle).total
                gpus.append(GpuInfo("nvidia", str(name), _bytes_to_mb(int(total))))
            return gpus
        finally:
            pynvml.nvmlShutdown()
    except Exception as exc:  # noqa: BLE001  # NVML raises its own error type per failure (no driver, no device); detection must degrade, not fail
        _logger.info("NVML could not read the NVIDIA GPUs: %s", exc)
        return []


def detect_nvidia() -> list[GpuInfo]:
    """Return detected NVIDIA GPUs via nvidia-smi (cross-platform), then NVML.

    The query returns ``"name, memory_total_mib"`` per line; a non-numeric
    memory value (for example ``[N/A]``) yields ``vram_mb=None``.
    """
    for command in _nvidia_smi_commands():
        out = _run([command, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if out:
            gpus = _parse_nvidia_smi(out)
            if gpus:
                _logger.info("nvidia-smi (%s) reported %s", command, gpus)
                return gpus
    gpus = _detect_nvidia_nvml()
    if gpus:
        _logger.info("NVML reported %s", gpus)
    else:
        _logger.info("No NVIDIA GPU found through nvidia-smi or NVML")
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
            _logger.debug("The rocm-smi VRAM total is not a number: '%s'", raw)
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


def detect_system_memory_gb() -> float | None:
    """Return the total memory (RAM) of this computer in GB, or ``None`` when unreadable.

    ``psutil`` is used when it is installed; otherwise the operating system is asked
    directly, so the answer does not depend on an optional package.
    """
    try:
        import psutil  # noqa: PLC0415

        return psutil.virtual_memory().total / 2**30
    except Exception:  # noqa: BLE001  # psutil may be missing in stripped environments; ask the OS instead
        pass
    try:
        if sys.platform == "win32":
            return _windows_total_memory_bytes() / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except AttributeError, OSError, ValueError:
        _logger.debug("The operating system reports no memory size")
        return None


def detect_available_memory_gb() -> float | None:
    """Return the main memory (RAM) free right now in GB, or ``None`` when unreadable.

    The memory a program could use without the system swapping to disk (psutil's
    ``available``: free memory plus what the system can reclaim at once).  Swap is not counted.
    """
    try:
        import psutil  # noqa: PLC0415

        return psutil.virtual_memory().available / 2**30
    except Exception:  # noqa: BLE001  # psutil may be missing in stripped environments; the check is advisory
        return None


def read_system_usage() -> SystemUsage | None:
    """Return the processor load and main memory in use, or ``None`` without ``psutil``.

    The processor share is measured since the previous call, so the first call answers 0.
    """
    try:
        import psutil  # noqa: PLC0415

        cpu = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
    except Exception:  # noqa: BLE001  # psutil may be missing in stripped environments; the gauges are advisory
        return None
    return SystemUsage(
        cpu_percent=cpu,
        ram_percent=memory.percent,
        ram_used_gb=memory.used / 2**30,
        ram_total_gb=memory.total / 2**30,
    )


def _windows_total_memory_bytes() -> int:
    """Return the physical memory in bytes through ``GlobalMemoryStatusEx``."""
    import ctypes  # noqa: PLC0415

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatus(ctypes.sizeof(_MemoryStatus))  # first field: the structure's size
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # ty: ignore[unresolved-attribute]  # windll exists on Windows only
        raise OSError("GlobalMemoryStatusEx failed")
    return int(status.ullTotalPhys)


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
        # No Enum entry to look at: the adapter counts as active rather than hidden.
        return True
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"{_WIN_ENUM_KEY}\{instance_id}\Control",
        ) as control:
            winreg.QueryValueEx(control, "ActiveService")
    except OSError:
        # No ActiveService value: the driver does not run this device, so it is no card to offer.
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
                                _logger.debug("The adapter has no %s value", value_name)
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


def _sysfs_cards(root: Path = _SYSFS_DRM) -> list[tuple[str, Path]]:
    """Return ``(vendor, device folder)`` of every NVIDIA/AMD card in *root*, in card order."""
    try:
        cards = sorted(path for path in root.iterdir() if re.fullmatch(r"card\d+", path.name))
    except OSError as exc:
        _logger.debug("Cannot list %s: %s", root, exc)
        return []
    found: list[tuple[str, Path]] = []
    for card in cards:
        device = card / "device"
        try:
            vendor = _VENDOR_IDS.get(int((device / "vendor").read_text().strip(), 16))
        except OSError, ValueError:
            _logger.debug("Card %s has no readable vendor id", device)
            continue
        if vendor is not None:
            found.append((vendor, device))
    return found


def _read_sysfs_adapters(root: Path = _SYSFS_DRM) -> list[GpuInfo]:
    """Return NVIDIA/AMD cards from Linux sysfs (no process started).

    The vendor is read for every card.  Only the AMD driver publishes the VRAM
    size (``mem_info_vram_total``, bytes); NVIDIA cards get ``vram_mb=None`` and
    are completed by nvidia-smi.  sysfs rarely has a marketing name (``product_name``
    exists on some AMD cards), so the generic ``"NVIDIA GPU"``/``"AMD GPU"`` is used
    until a vendor tool supplies one (see :func:`_prefer_complete`).
    """
    gpus: list[GpuInfo] = []
    for vendor, device in _sysfs_cards(root):
        vram: int | None = None
        if vendor == "amd":
            try:
                vram = _bytes_to_mb(int((device / "mem_info_vram_total").read_text()))
            except OSError, ValueError:
                vram = None
        name = _generic_name(vendor)
        try:
            name = (device / "product_name").read_text().strip() or name
        except OSError as exc:
            _logger.debug("Card %s has no product name: %s", device, exc)
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

    gpus = for_vendor("nvidia", detect_nvidia) + for_vendor("amd", detect_amd)
    _logger.info(
        "GPU detection (%s): operating system saw %s; result %s",
        "registry" if registry is not None else sys.platform,
        os_gpus,
        gpus,
    )
    return gpus


# ── live load (System Monitor) ────────────────────────────────────────────────


@dataclass
class _NvmlFailure:
    """The last answer NVML gave instead of a load: ``None`` once it answered again."""

    reason: str | None = None


#: What NVML answered last time it failed.  The System Monitor reads the load once a second, so
#: only a changed answer is logged.
_nvml_failure = _NvmlFailure()


def _note_nvml_failure(reason: str | None) -> None:
    """Log *reason* when it differs from the last one; ``None`` forgets it once NVML works."""
    if reason == _nvml_failure.reason:
        return
    _nvml_failure.reason = reason
    if reason is not None:
        _logger.info("NVML could not read the GPU's load: %s", reason)


def read_nvidia_usage() -> GpuUsage | None:
    """Return the live load of the first NVIDIA card through NVML, or ``None``.

    ``nvidia-ml-py`` is bundled (60 KB, pure Python), so this needs no add-on; without
    an NVIDIA driver ``nvmlInit`` fails and the answer is ``None``.
    """
    try:
        import pynvml  # noqa: PLC0415
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            utilisation = pynvml.nvmlDeviceGetUtilizationRates(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        finally:
            pynvml.nvmlShutdown()
    except Exception as exc:  # noqa: BLE001  # NVML raises its own error type per failure (no driver, no device); the gauge must degrade, not fail
        _note_nvml_failure(f"{type(exc).__name__}: {exc}")
        return None
    _note_nvml_failure(None)
    return GpuUsage(
        busy_percent=int(utilisation.gpu),
        vram_used_mb=_bytes_to_mb(int(memory.used)),
        vram_total_mb=_bytes_to_mb(int(memory.total)),
    )


def read_amd_usage(root: Path = _SYSFS_DRM) -> GpuUsage | None:
    """Return the live load of the first AMD card from sysfs, or ``None``.

    The ``amdgpu`` driver publishes ``gpu_busy_percent`` and the two
    ``mem_info_vram_*`` files (bytes) per card, so no package is needed.  A card whose
    driver does not provide them (an older kernel, a virtual machine) gives ``None``.
    """
    for vendor, device in _sysfs_cards(root):
        if vendor != "amd":
            continue
        busy = _read_int(device / "gpu_busy_percent")
        if busy is None:
            continue
        return GpuUsage(
            busy_percent=max(0, min(100, busy)),
            vram_used_mb=_read_mb(device / "mem_info_vram_used"),
            vram_total_mb=_read_mb(device / "mem_info_vram_total"),
        )
    return None


def read_gpu_usage() -> GpuUsage | None:
    """Return the live load of the first graphics card, or ``None`` when none answers.

    NVIDIA through NVML, AMD through sysfs.  Cheap enough for the System Monitor's
    one-second refresh, and it starts no process.
    """
    return read_nvidia_usage() or read_amd_usage()


def _read_int(path: Path) -> int | None:
    """Return the integer in *path*, or ``None`` when it is missing or not a number."""
    try:
        return int(path.read_text().strip())
    except OSError, ValueError:
        # A file the driver does not publish, or one without a number: this card has no answer.
        return None


def _read_mb(path: Path) -> int | None:
    """Return the byte count in *path* as MB, or ``None`` when it is missing."""
    value = _read_int(path)
    return _bytes_to_mb(value) if value is not None else None


class SystemHardwareProbe:
    """This computer's graphics cards and memory, asked through the functions above.

    Each method looks its function up by name when it is called, so patching one of them in a
    test changes what this object answers.
    """

    def gpus(self) -> list[GpuInfo]:
        """Return the graphics cards of this computer."""
        return detect_gpus()

    def apple_unified_memory_gb(self) -> float | None:
        """Return the shared memory of an Apple Silicon computer, or ``None``."""
        return detect_apple_unified_memory_gb()

    def system_memory_gb(self) -> float | None:
        """Return the main memory of this computer, or ``None`` when it cannot be read."""
        return detect_system_memory_gb()

    def available_memory_gb(self) -> float | None:
        """Return the main memory free right now, or ``None`` when it cannot be read."""
        return detect_available_memory_gb()

    def gpu_usage(self) -> GpuUsage | None:
        """Return how busy the first graphics card is, or ``None`` when it cannot be read."""
        return read_gpu_usage()

    def system_usage(self) -> SystemUsage | None:
        """Return the processor load and main memory in use, or ``None`` when unreadable."""
        return read_system_usage()
