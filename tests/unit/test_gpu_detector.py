"""Unit tests for teachers_teammate.infrastructure.gpu_detector."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import gpu_detector
from teachers_teammate.infrastructure.gpu_detector import (
    GpuInfo,
    _parse_amd_smi,
    _parse_amd_smi_vram,
    _parse_rocm_smi,
    _parse_rocm_smi_vram,
    _prefer_complete,
    _read_sysfs_adapters,
    _read_windows_adapters,
    _run,
    detect_amd,
    detect_apple_unified_memory_gb,
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


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_nvidia_parses_name_and_memory_from_one_line(monkeypatch) -> None:
    """
    Given  nvidia-smi returns one "name, memory_mib" line
    When   detect_nvidia is called
    Then   the GPU is returned with its VRAM in MB
    """
    monkeypatch.setattr(gpu_detector, "_run", lambda *_a, **_k: "NVIDIA GeForce RTX 4060, 8188")
    assert detect_nvidia() == [GpuInfo("nvidia", "NVIDIA GeForce RTX 4060", 8188)]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_nvidia_parses_multiple_lines(monkeypatch) -> None:
    """
    Given  nvidia-smi returns two "name, memory_mib" lines
    When   detect_nvidia is called
    Then   both GPUs are returned with their VRAM
    """
    monkeypatch.setattr(
        gpu_detector,
        "_run",
        lambda *_a, **_k: "GeForce RTX 4090, 24564\nGeForce RTX 3080, 10007",
    )
    assert detect_nvidia() == [
        GpuInfo("nvidia", "GeForce RTX 4090", 24564),
        GpuInfo("nvidia", "GeForce RTX 3080", 10007),
    ]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_nvidia_na_memory_yields_none(monkeypatch) -> None:
    """
    Given  nvidia-smi reports "[N/A]" as the memory value
    When   detect_nvidia is called
    Then   the GPU is returned with vram_mb=None
    """
    monkeypatch.setattr(gpu_detector, "_run", lambda *_a, **_k: "Some NVIDIA GPU, [N/A]")
    assert detect_nvidia() == [GpuInfo("nvidia", "Some NVIDIA GPU", None)]


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


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_amd_matches_names_and_memory_by_index(monkeypatch) -> None:
    """
    Given  rocm-smi returns two GPU names and two VRAM values
    When   detect_amd is called
    Then   each GPU carries the VRAM of the same index
    """

    def fake_run(cmd, **_k):
        if "--showproductname" in cmd:
            return (
                "GPU[0] : Card series: Radeon RX 6800 XT\nGPU[1] : Card series: Radeon RX 7900 XTX"
            )
        if "--showmeminfo" in cmd:
            return (
                "GPU[0] : VRAM Total Memory (B): 17163091968\n"
                "GPU[1] : VRAM Total Memory (B): 17179869184"
            )
        return None

    monkeypatch.setattr(gpu_detector, "_run", fake_run)
    assert detect_amd() == [
        GpuInfo("amd", "Radeon RX 6800 XT", 16368),
        GpuInfo("amd", "Radeon RX 7900 XTX", 16384),
    ]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_amd_missing_memory_entries_give_none(monkeypatch) -> None:
    """
    Given  rocm-smi returns two GPU names but only one VRAM value
    When   detect_amd is called
    Then   the remaining GPUs get vram_mb=None
    """

    def fake_run(cmd, **_k):
        if "--showproductname" in cmd:
            return (
                "GPU[0] : Card series: Radeon RX 6800 XT\nGPU[1] : Card series: Radeon RX 7900 XTX"
            )
        if "--showmeminfo" in cmd:
            return "GPU[0] : VRAM Total Memory (B): 17163091968"
        return None

    monkeypatch.setattr(gpu_detector, "_run", fake_run)
    assert detect_amd() == [
        GpuInfo("amd", "Radeon RX 6800 XT", 16368),
        GpuInfo("amd", "Radeon RX 7900 XTX", None),
    ]


# ── _parse_rocm_smi_vram / _parse_amd_smi_vram ──────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_parse_rocm_smi_vram_extracts_bytes_as_mb() -> None:
    """
    Given  rocm-smi --showmeminfo vram output
    When   _parse_rocm_smi_vram is called
    Then   the byte values are returned as MB in GPU order
    """
    output = (
        "===== ROCm System Management =====\n"
        "GPU[0] : VRAM Total Memory (B): 17163091968\n"
        "GPU[1] : VRAM Total Memory (B): 17179869184\n"
    )
    assert _parse_rocm_smi_vram(output) == [16368, 16384]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_parse_rocm_smi_vram_skips_noise_and_returns_empty() -> None:
    """
    Given  rocm-smi output with no VRAM Total Memory lines
    When   _parse_rocm_smi_vram is called
    Then   an empty list is returned
    """
    assert _parse_rocm_smi_vram("GPU[0] : Card series: Radeon\nnothing\n") == []


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_parse_amd_smi_vram_extracts_mb_values() -> None:
    """
    Given  amd-smi static --vram output
    When   _parse_amd_smi_vram is called
    Then   the MB values are returned in GPU order
    """
    output = "GPU: 0\n    SIZE: 16368 MB\nGPU: 1\n    SIZE: 8192 MB\n"
    assert _parse_amd_smi_vram(output) == [16368, 8192]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_parse_amd_smi_vram_returns_empty_when_no_match() -> None:
    """
    Given  amd-smi output without SIZE lines
    When   _parse_amd_smi_vram is called
    Then   an empty list is returned
    """
    assert _parse_amd_smi_vram("ASIC: some-asic\n") == []


# ── detect_apple_unified_memory_gb ───────────────────────────────────────────


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_apple_unified_memory_gb_on_darwin_arm64(monkeypatch) -> None:
    """
    Given  a Darwin/arm64 machine with 16 GiB of RAM
    When   detect_apple_unified_memory_gb is called
    Then   the total memory in GB is returned
    """
    monkeypatch.setattr(gpu_detector.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(gpu_detector.platform, "machine", lambda: "arm64")

    class _FakeVM:
        total = 16 * 2**30

    fake_psutil = type("psutil", (), {"virtual_memory": staticmethod(lambda: _FakeVM())})
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert detect_apple_unified_memory_gb() == 16.0


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_apple_unified_memory_gb_none_off_apple(monkeypatch) -> None:
    """
    Given  a non-Apple machine
    When   detect_apple_unified_memory_gb is called
    Then   None is returned without touching psutil
    """
    monkeypatch.setattr(gpu_detector.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gpu_detector.platform, "machine", lambda: "x86_64")
    assert detect_apple_unified_memory_gb() is None


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_apple_unified_memory_gb_none_on_intel_mac(monkeypatch) -> None:
    """
    Given  a Darwin machine on x86_64 (Intel Mac)
    When   detect_apple_unified_memory_gb is called
    Then   None is returned (unified memory applies to Apple Silicon only)
    """
    monkeypatch.setattr(gpu_detector.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(gpu_detector.platform, "machine", lambda: "x86_64")
    assert detect_apple_unified_memory_gb() is None


# ── detect_gpus ────────────────────────────────────────────────────────────


@pytest.mark.use_case("System_Resource_Monitoring")
def test_detect_gpus_combines_nvidia_and_amd(monkeypatch) -> None:
    """
    Given  one NVIDIA and one AMD GPU are detected
    When   detect_gpus is called
    Then   both are returned, NVIDIA first
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gpu_detector, "_read_sysfs_adapters", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [GpuInfo("nvidia", "RTX 4090")])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [GpuInfo("amd", "RX 7900")])
    assert detect_gpus() == [GpuInfo("nvidia", "RTX 4090"), GpuInfo("amd", "RX 7900")]


def test_detect_gpus_returns_empty_when_none_detected(monkeypatch) -> None:
    """
    Given  no GPUs are detected by either vendor
    When   detect_gpus is called
    Then   an empty list is returned
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gpu_detector, "_read_sysfs_adapters", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == []


def test_detect_gpus_skips_vendor_tools_when_os_reports_complete_memory(monkeypatch) -> None:
    """
    Given  the operating system reports an AMD GPU with its name and VRAM
    When   detect_gpus is called
    Then   the OS answer is returned and no vendor tool is started for AMD
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        gpu_detector, "_read_sysfs_adapters", lambda: [GpuInfo("amd", "Radeon Pro W6800", 32768)]
    )
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: pytest.fail("rocm-smi must not run"))
    assert detect_gpus() == [GpuInfo("amd", "Radeon Pro W6800", 32768)]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_completes_nvidia_memory_with_nvidia_smi(monkeypatch) -> None:
    """
    Given  sysfs shows an NVIDIA card (vendor only) and nvidia-smi knows its name and VRAM
    When   detect_gpus is called
    Then   the nvidia-smi answer is returned
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        gpu_detector, "_read_sysfs_adapters", lambda: [GpuInfo("nvidia", "NVIDIA GPU")]
    )
    monkeypatch.setattr(
        gpu_detector, "detect_nvidia", lambda: [GpuInfo("nvidia", "RTX 4060", 8188)]
    )
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == [GpuInfo("nvidia", "RTX 4060", 8188)]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_keeps_os_entry_when_tool_is_missing(monkeypatch) -> None:
    """
    Given  sysfs shows an NVIDIA card but nvidia-smi is not installed
    When   detect_gpus is called
    Then   the card is still reported, with unknown VRAM
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        gpu_detector, "_read_sysfs_adapters", lambda: [GpuInfo("nvidia", "NVIDIA GPU")]
    )
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == [GpuInfo("nvidia", "NVIDIA GPU", None)]


def test_prefer_complete_returns_tool_result_when_os_has_no_gpus() -> None:
    """
    Given  the operating system found no GPU of a vendor
    When   _prefer_complete is called
    Then   the vendor tool's result is returned
    """
    assert _prefer_complete([], lambda: [GpuInfo("amd", "X", 1)]) == [GpuInfo("amd", "X", 1)]


# ── _run: no console window on Windows ──────────────────────────────────────


def test_run_hides_console_window_on_windows(monkeypatch) -> None:
    """
    Given  the platform is Windows
    When   _run starts a process
    Then   CREATE_NO_WINDOW is passed so no console window flashes up
    """
    monkeypatch.setattr(sys, "platform", "win32")
    fake = MagicMock(returncode=0, stdout="ok")
    with patch("subprocess.run", return_value=fake) as run:
        assert _run(["nvidia-smi"]) == "ok"
    assert run.call_args.kwargs["creationflags"] == 0x08000000


def test_run_passes_no_creationflags_off_windows(monkeypatch) -> None:
    """
    Given  the platform is Linux
    When   _run starts a process
    Then   no creationflags argument is passed (it is Windows-only)
    """
    monkeypatch.setattr(sys, "platform", "linux")
    fake = MagicMock(returncode=0, stdout="ok")
    with patch("subprocess.run", return_value=fake) as run:
        _run(["nvidia-smi"])
    assert "creationflags" not in run.call_args.kwargs


# ── _read_sysfs_adapters ────────────────────────────────────────────────────


def _make_card(root, name: str, vendor: str, vram_bytes: int | None = None) -> None:
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text(f"{vendor}\n")
    if vram_bytes is not None:
        (device / "mem_info_vram_total").write_text(f"{vram_bytes}\n")


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_read_sysfs_adapters_reads_amd_vram_and_nvidia_vendor(tmp_path) -> None:
    """
    Given  sysfs with an AMD card (16 GiB VRAM), an NVIDIA card and an Intel card
    When   _read_sysfs_adapters is called
    Then   AMD carries its VRAM, NVIDIA has none, and Intel is ignored
    """
    _make_card(tmp_path, "card0", "0x1002", 16 * 2**30)
    _make_card(tmp_path, "card1", "0x10de")
    _make_card(tmp_path, "card2", "0x8086")
    assert _read_sysfs_adapters(tmp_path) == [
        GpuInfo("amd", "AMD GPU", 16384),
        GpuInfo("nvidia", "NVIDIA GPU", None),
    ]


def test_read_sysfs_adapters_ignores_connector_entries(tmp_path) -> None:
    """
    Given  sysfs entries named like display connectors (card0-DP-1)
    When   _read_sysfs_adapters is called
    Then   they are not treated as cards
    """
    _make_card(tmp_path, "card0-DP-1", "0x1002", 2**30)
    assert _read_sysfs_adapters(tmp_path) == []


def test_read_sysfs_adapters_amd_without_vram_file_gives_none(tmp_path) -> None:
    """
    Given  an AMD card whose mem_info_vram_total file is missing
    When   _read_sysfs_adapters is called
    Then   the card is reported with vram_mb=None
    """
    _make_card(tmp_path, "card0", "0x1002")
    assert _read_sysfs_adapters(tmp_path) == [GpuInfo("amd", "AMD GPU", None)]


def test_read_sysfs_adapters_missing_directory_gives_empty(tmp_path) -> None:
    """
    Given  a sysfs root that does not exist (not Linux)
    When   _read_sysfs_adapters is called
    Then   an empty list is returned
    """
    assert _read_sysfs_adapters(tmp_path / "nope") == []


# ── _read_windows_adapters ──────────────────────────────────────────────────


def _fake_winreg(
    adapters: Mapping[str, Mapping[str, object]],
    enum: Mapping[str, Mapping[str, object] | None] | None = None,
) -> SimpleNamespace:
    """A minimal winreg double.

    *adapters* is the display class key: ``{subkey: {value: data}}``.  *enum* is the
    ``Enum`` key: ``{instance id: values of its Control subkey}``, ``None`` meaning
    the instance exists without a ``Control`` subkey (device not running).  Instances
    not listed do not exist.
    """
    enum = enum or {}
    enum_prefix = gpu_detector._WIN_ENUM_KEY + "\\"

    class _Key:
        def __init__(self, values: Mapping[str, object] | None = None) -> None:
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    root = object()

    def open_key(parent, sub):
        if parent is not root:
            return _Key(adapters[sub])  # an adapter subkey of the class key
        if not sub.startswith(enum_prefix):
            return _Key(None)  # the display class key
        path = sub.removeprefix(enum_prefix)
        is_control = path.endswith("\\Control")
        instance = path.removesuffix("\\Control")
        if instance not in enum:
            raise FileNotFoundError(path)
        if not is_control:
            return _Key({})
        control = enum[instance]
        if control is None:
            raise FileNotFoundError(path)
        return _Key(control)

    def enum_key(_key, index):
        names = list(adapters)
        if index >= len(names):
            raise OSError
        return names[index]

    def query(key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1

    return SimpleNamespace(
        HKEY_LOCAL_MACHINE=root,
        OpenKey=open_key,
        EnumKey=enum_key,
        QueryValueEx=query,
    )


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_read_windows_adapters_reads_vendor_name_and_vram(monkeypatch) -> None:
    """
    Given  registry adapters: an NVIDIA card, an AMD card (bytes value), Intel, a virtual
           display and a non-adapter key
    When   _read_windows_adapters is called
    Then   only NVIDIA and AMD are returned with name and VRAM in MB
    """
    adapters = {
        "0000": {
            "MatchingDeviceId": "pci\\ven_10de&dev_2684&subsys_1",
            "DriverDesc": "NVIDIA GeForce RTX 4090",
            "HardwareInformation.qwMemorySize": 24 * 2**30,
        },
        "0001": {
            "MatchingDeviceId": "PCI\\VEN_1002&DEV_73BF",
            "DriverDesc": "AMD Radeon RX 6800 XT",
            "HardwareInformation.qwMemorySize": (16 * 2**30).to_bytes(8, "little"),
        },
        "0002": {"MatchingDeviceId": "PCI\\VEN_8086&DEV_9A49", "DriverDesc": "Intel Iris Xe"},
        "0003": {"MatchingDeviceId": "root\\basicdisplay", "DriverDesc": "Basic Display"},
        "Configuration": {},
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    assert _read_windows_adapters() == [
        GpuInfo("nvidia", "NVIDIA GeForce RTX 4090", 24576),
        GpuInfo("amd", "AMD Radeon RX 6800 XT", 16384),
    ]


def test_read_windows_adapters_falls_back_to_32bit_memory_size(monkeypatch) -> None:
    """
    Given  an old driver that only writes HardwareInformation.MemorySize
    When   _read_windows_adapters is called
    Then   that value is used for VRAM
    """
    adapters = {
        "0000": {
            "MatchingDeviceId": "PCI\\VEN_10DE&DEV_1",
            "DriverDesc": "NVIDIA GeForce GTX 750",
            "HardwareInformation.MemorySize": 2 * 2**30,
        }
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    assert _read_windows_adapters() == [GpuInfo("nvidia", "NVIDIA GeForce GTX 750", 2048)]


def test_read_windows_adapters_without_memory_value_gives_none(monkeypatch) -> None:
    """
    Given  an NVIDIA adapter with no memory value in the registry
    When   _read_windows_adapters is called
    Then   the adapter is returned with vram_mb=None (so nvidia-smi can complete it)
    """
    adapters = {"0000": {"MatchingDeviceId": "PCI\\VEN_10DE&DEV_1", "DriverDesc": "NVIDIA X"}}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    assert _read_windows_adapters() == [GpuInfo("nvidia", "NVIDIA X", None)]


def test_read_windows_adapters_empty_off_windows(monkeypatch) -> None:
    """
    Given  a non-Windows platform
    When   _read_windows_adapters is called
    Then   None ("unknown") is returned without importing winreg
    """
    monkeypatch.setattr(sys, "platform", "linux")
    assert _read_windows_adapters() is None


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_on_windows_starts_no_process_when_registry_is_complete(monkeypatch) -> None:
    """
    Given  Windows with an NVIDIA card whose VRAM is in the registry
    When   detect_gpus is called
    Then   the registry answer is returned and nvidia-smi is never started
    """
    adapters = {
        "0000": {
            "MatchingDeviceId": "PCI\\VEN_10DE&DEV_2684",
            "DriverDesc": "NVIDIA GeForce RTX 4070",
            "HardwareInformation.qwMemorySize": 12 * 2**30,
        }
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    with patch("subprocess.run", side_effect=AssertionError("no process expected")):
        assert detect_gpus() == [GpuInfo("nvidia", "NVIDIA GeForce RTX 4070", 12288)]


def test_detect_gpus_on_windows_with_only_intel_starts_no_process(monkeypatch) -> None:
    """
    Given  Windows whose registry lists only an Intel adapter
    When   detect_gpus is called
    Then   no GPU is reported and no vendor tool is started
    """
    adapters = {"0000": {"MatchingDeviceId": "PCI\\VEN_8086&DEV_9A49", "DriverDesc": "Intel"}}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    with patch("subprocess.run", side_effect=AssertionError("no process expected")):
        assert detect_gpus() == []


def test_detect_gpus_on_windows_falls_back_to_tools_when_registry_unreadable(monkeypatch) -> None:
    """
    Given  Windows where the registry cannot be read
    When   detect_gpus is called
    Then   nvidia-smi is used as before
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(gpu_detector, "_read_windows_adapters", lambda: None)
    monkeypatch.setattr(gpu_detector, "_read_sysfs_adapters", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [GpuInfo("nvidia", "RTX", 8188)])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == [GpuInfo("nvidia", "RTX", 8188)]


# ── stale / saturated registry entries ───────────────────────────────────────


def _nvidia_adapter(**extra: object) -> dict[str, object]:
    return {
        "MatchingDeviceId": "PCI\\VEN_10DE&DEV_2684",
        "DriverDesc": "NVIDIA GeForce RTX 4070",
        "DeviceInstanceID": "PCI\\VEN_10DE&DEV_2684\\4&1",
        "HardwareInformation.qwMemorySize": 12 * 2**30,
        **extra,
    }


def test_read_windows_adapters_skips_adapter_that_is_not_running(monkeypatch) -> None:
    """
    Given  a remembered NVIDIA adapter whose device has no running Control key
    When   _read_windows_adapters is called
    Then   it is not reported (a card that was unplugged must not count)
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "winreg",
        _fake_winreg({"0000": _nvidia_adapter()}, enum={"PCI\\VEN_10DE&DEV_2684\\4&1": None}),
    )
    assert _read_windows_adapters() == []


def test_read_windows_adapters_keeps_running_adapter(monkeypatch) -> None:
    """
    Given  an NVIDIA adapter whose device has a Control key with ActiveService
    When   _read_windows_adapters is called
    Then   it is reported
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(
        sys.modules,
        "winreg",
        _fake_winreg(
            {"0000": _nvidia_adapter()},
            enum={"PCI\\VEN_10DE&DEV_2684\\4&1": {"ActiveService": "nvlddmkm"}},
        ),
    )
    assert _read_windows_adapters() == [GpuInfo("nvidia", "NVIDIA GeForce RTX 4070", 12288)]


def test_read_windows_adapters_keeps_adapter_when_instance_cannot_be_looked_up(
    monkeypatch,
) -> None:
    """
    Given  an adapter whose device instance is not found under Enum at all
    When   _read_windows_adapters is called
    Then   it is still reported (a stale card is better than a hidden real one)
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg({"0000": _nvidia_adapter()}, enum={}))
    assert _read_windows_adapters() == [GpuInfo("nvidia", "NVIDIA GeForce RTX 4070", 12288)]


def test_read_windows_adapters_treats_saturated_32bit_memory_as_unknown(monkeypatch) -> None:
    """
    Given  an old driver whose 32-bit MemorySize is at its 4 GB ceiling
    When   _read_windows_adapters is called
    Then   vram_mb is None, so nvidia-smi can supply the real size
    """
    adapters = {
        "0000": {
            "MatchingDeviceId": "PCI\\VEN_10DE&DEV_1",
            "DriverDesc": "NVIDIA GeForce GTX 1080",
            "HardwareInformation.MemorySize": 0xFFFFFFFF,
        }
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    assert _read_windows_adapters() == [GpuInfo("nvidia", "NVIDIA GeForce GTX 1080", None)]


# ── AMD integrated graphics ──────────────────────────────────────────────────


def test_detect_gpus_on_windows_ignores_amd_integrated_graphics(monkeypatch) -> None:
    """
    Given  Windows whose registry lists an AMD adapter with a 512 MB carve-out
    When   detect_gpus is called
    Then   no GPU is reported and no vendor tool is started
    """
    adapters = {
        "0000": {
            "MatchingDeviceId": "PCI\\VEN_1002&DEV_1638",
            "DriverDesc": "AMD Radeon(TM) Graphics",
            "HardwareInformation.qwMemorySize": 512 * 2**20,
        }
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(adapters))
    with patch("subprocess.run", side_effect=AssertionError("no process expected")):
        assert detect_gpus() == []


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_amd_integrated_does_not_hide_nvidia_with_unknown_memory(
    monkeypatch,
) -> None:
    """
    Given  sysfs shows an AMD integrated chip (512 MB) and an NVIDIA card without nvidia-smi
    When   detect_gpus is called
    Then   only the NVIDIA card is reported, so its unknown memory is not outvoted by 0.5 GB
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        gpu_detector,
        "_read_sysfs_adapters",
        lambda: [GpuInfo("amd", "AMD GPU", 512), GpuInfo("nvidia", "NVIDIA GPU")],
    )
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == [GpuInfo("nvidia", "NVIDIA GPU", None)]


def test_detect_gpus_drops_integrated_amd_reported_by_the_tool(monkeypatch) -> None:
    """
    Given  rocm-smi reports an AMD APU with 512 MB
    When   detect_gpus is called
    Then   it is not reported
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(gpu_detector, "_read_sysfs_adapters", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [GpuInfo("amd", "Radeon 780M", 512)])
    assert detect_gpus() == []


# ── names from sysfs / merging with the tool ─────────────────────────────────


def test_read_sysfs_adapters_uses_product_name_when_the_driver_has_one(tmp_path) -> None:
    """
    Given  an AMD card whose device directory has a product_name file
    When   _read_sysfs_adapters is called
    Then   that name is used instead of the generic one
    """
    _make_card(tmp_path, "card0", "0x1002", 32 * 2**30)
    (tmp_path / "card0" / "device" / "product_name").write_text("AMD Radeon PRO W7800\n")
    assert _read_sysfs_adapters(tmp_path) == [GpuInfo("amd", "AMD Radeon PRO W7800", 32768)]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_asks_the_tool_for_the_name_of_a_generic_amd_card(monkeypatch) -> None:
    """
    Given  sysfs shows an AMD card with 16 GiB but only the generic name
    When   detect_gpus is called
    Then   the rocm-smi name is used and the 16 GiB are kept
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        gpu_detector, "_read_sysfs_adapters", lambda: [GpuInfo("amd", "AMD GPU", 16384)]
    )
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(
        gpu_detector, "detect_amd", lambda: [GpuInfo("amd", "AMD Radeon RX 6800 XT", None)]
    )
    assert detect_gpus() == [GpuInfo("amd", "AMD Radeon RX 6800 XT", 16384)]


def test_detect_gpus_keeps_generic_name_when_the_tool_is_missing(monkeypatch) -> None:
    """
    Given  sysfs shows an AMD card with 16 GiB and rocm-smi is not installed
    When   detect_gpus is called
    Then   the card is reported with the generic name and its memory
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        gpu_detector, "_read_sysfs_adapters", lambda: [GpuInfo("amd", "AMD GPU", 16384)]
    )
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == [GpuInfo("amd", "AMD GPU", 16384)]


def test_detect_gpus_does_not_read_sysfs_off_linux(monkeypatch) -> None:
    """
    Given  macOS (no sysfs, no registry)
    When   detect_gpus is called
    Then   sysfs is not read and the vendor tools alone decide
    """
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        gpu_detector, "_read_sysfs_adapters", lambda: pytest.fail("sysfs is Linux-only")
    )
    monkeypatch.setattr(gpu_detector, "detect_nvidia", lambda: [GpuInfo("nvidia", "RTX", 8188)])
    monkeypatch.setattr(gpu_detector, "detect_amd", lambda: [])
    assert detect_gpus() == [GpuInfo("nvidia", "RTX", 8188)]
