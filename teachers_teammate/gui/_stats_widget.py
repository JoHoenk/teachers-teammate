"""Live system-stats widget (CPU / RAM / GPU / VRAM)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

try:
    import psutil as _psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

_nvml = None
try:
    import pynvml as _nvml

    _nvml.nvmlInit()
    _NVML_AVAILABLE = True
except Exception:  # noqa: BLE001  # nvmlInit() raises if no NVIDIA driver is present; treat as unavailable
    _NVML_AVAILABLE = False

_AMD_GPU = None
_AMDGPU_AVAILABLE = False
try:
    import pyamdgpuinfo

    _gpus = pyamdgpuinfo.detect_gpus()
    if _gpus:
        _AMD_GPU = _gpus[0]
        _AMDGPU_AVAILABLE = True
except Exception:  # noqa: BLE001  # pyamdgpuinfo raises if no AMD GPU is present; treat as unavailable
    pass


class SystemStatsWidget(QGroupBox):
    """Live CPU / RAM / GPU* / VRAM* monitors, refreshed every second.

    GPU and VRAM rows are shown when pynvml (NVIDIA) or pyamdgpuinfo (AMD)
    is installed and a supported GPU is detected.  A button to open the
    Downloads dialog is shown otherwise.
    """

    open_downloads_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("System Stats", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(3)

        if not _PSUTIL_AVAILABLE:
            layout.addWidget(QLabel("Install psutil for live system stats."))
            self._enabled = False
            return

        self._enabled = True

        def _make_row(name: str) -> tuple[QProgressBar, QLabel]:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            lbl = QLabel(name)
            lbl.setFixedWidth(40)
            progress_bar = QProgressBar()
            progress_bar.setRange(0, 100)
            progress_bar.setTextVisible(False)
            progress_bar.setFixedHeight(10)
            val = QLabel("\u2013")
            val.setFixedWidth(100)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            fnt = val.font()
            fnt.setPointSize(8)
            val.setFont(fnt)
            h.addWidget(lbl)
            h.addWidget(progress_bar, stretch=1)
            h.addWidget(val)
            layout.addWidget(row)
            return progress_bar, val

        self._cpu_bar, self._cpu_val = _make_row("CPU")
        self._ram_bar, self._ram_val = _make_row("RAM")
        self._gpu_bar: QProgressBar | None = None
        self._gpu_val: QLabel | None = None
        self._vram_bar: QProgressBar | None = None
        self._vram_val: QLabel | None = None
        if _NVML_AVAILABLE or _AMDGPU_AVAILABLE:
            self._gpu_bar, self._gpu_val = _make_row("GPU")
            self._vram_bar, self._vram_val = _make_row("VRAM")
        else:
            _gpu_btn = QPushButton("GPU monitoring unavailable — Open Downloads…")
            _gpu_btn.clicked.connect(self.open_downloads_requested)
            layout.addWidget(_gpu_btn)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        if not self._enabled:
            return

        cpu = _psutil.cpu_percent(interval=None)
        self._cpu_bar.setValue(int(cpu))
        self._cpu_val.setText(f"{cpu:.0f} %")

        mem = _psutil.virtual_memory()
        self._ram_bar.setValue(int(mem.percent))
        self._ram_val.setText(f"{mem.used / 2**30:.1f} / {mem.total / 2**30:.1f} GB")

        if _NVML_AVAILABLE and self._gpu_bar is not None:
            assert _nvml is not None
            try:
                handle = _nvml.nvmlDeviceGetHandleByIndex(0)
                util = _nvml.nvmlDeviceGetUtilizationRates(handle)
                minfo = _nvml.nvmlDeviceGetMemoryInfo(handle)
                self._gpu_bar.setValue(int(util.gpu))
                if self._gpu_val:
                    self._gpu_val.setText(f"{util.gpu:.0f} %")
                vram_pct = int(100 * minfo.used / minfo.total) if minfo.total else 0
                if self._vram_bar:
                    self._vram_bar.setValue(vram_pct)
                if self._vram_val:
                    self._vram_val.setText(
                        f"{minfo.used / 2**30:.1f} / {minfo.total / 2**30:.1f} GB"
                    )
            except Exception:  # noqa: BLE001  # pynvml driver queries can fail at runtime (driver error, GPU reset); skip this update cycle
                pass
        elif _AMDGPU_AVAILABLE and _AMD_GPU is not None and self._gpu_bar is not None:
            try:
                gpu_pct = int(_AMD_GPU.query_load() * 100)
                vram_used = _AMD_GPU.query_vram_usage()
                vram_total = _AMD_GPU.query_vram_size()
                self._gpu_bar.setValue(gpu_pct)
                if self._gpu_val:
                    self._gpu_val.setText(f"{gpu_pct:.0f} %")
                if vram_total:
                    vram_pct = int(100 * vram_used / vram_total)
                    if self._vram_bar:
                        self._vram_bar.setValue(vram_pct)
                    if self._vram_val:
                        self._vram_val.setText(
                            f"{vram_used / 2**30:.1f} / {vram_total / 2**30:.1f} GB"
                        )
            except Exception:  # noqa: BLE001  # pyamdgpuinfo metric queries can fail at runtime (driver error, GPU reset); skip this update cycle
                pass
