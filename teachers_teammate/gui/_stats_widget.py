"""Live system-stats widget (CPU / RAM / GPU / VRAM)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..infrastructure.gpu_detector import GpuUsage, read_gpu_usage
from ._background import run_in_background

try:
    import psutil as _psutil

    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


class SystemStatsWidget(QGroupBox):
    """Live CPU / RAM / GPU* / VRAM* monitors, refreshed every second while shown.

    The timer runs only while the widget is visible: the System Monitor window is hidden
    most of the time, and nobody reads a gauge that is not on screen.

    The GPU and VRAM rows appear when the graphics card answers: NVML for NVIDIA
    (``nvidia-ml-py`` is bundled) and the ``amdgpu`` driver's sysfs files for AMD.  A
    computer without a card the app can read simply shows CPU and RAM.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("System Stats", parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 8, 6, 6)
        self._layout.setSpacing(3)
        self._gpu_bar: QProgressBar | None = None
        self._gpu_val: QLabel | None = None
        self._vram_bar: QProgressBar | None = None
        self._vram_val: QLabel | None = None

        if not _PSUTIL_AVAILABLE:
            self._layout.addWidget(QLabel("Install psutil for live system stats."))
            self._enabled = False
            return

        self._enabled = True
        self._cpu_bar, self._cpu_val = self._make_row("CPU")
        self._ram_bar, self._ram_val = self._make_row("RAM")
        # The card is asked in a pool thread (NVML start-up can take a while, longer when the
        # card sleeps); its rows appear with the first reading, whenever that comes.
        self._gpu_read_pending = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._refresh()

    def showEvent(self, event: QShowEvent) -> None:  # Qt override
        """Start polling, with one reading at once so the gauges are not stale."""
        super().showEvent(event)
        if self._enabled:
            self._refresh()
            self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:  # Qt override
        """Stop polling while nobody can see the gauges."""
        super().hideEvent(event)
        if self._enabled:
            self._timer.stop()

    def _make_row(self, name: str) -> tuple[QProgressBar, QLabel]:
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
        self._layout.addWidget(row)
        return progress_bar, val

    def _refresh(self) -> None:
        if not self._enabled:
            return

        cpu = _psutil.cpu_percent(interval=None)
        self._cpu_bar.setValue(int(cpu))
        self._cpu_val.setText(f"{cpu:.0f} %")

        mem = _psutil.virtual_memory()
        self._ram_bar.setValue(int(mem.percent))
        self._ram_val.setText(f"{mem.used / 2**30:.1f} / {mem.total / 2**30:.1f} GB")

        self._request_gpu_usage()

    def _request_gpu_usage(self) -> None:
        """Ask the card for its load without blocking the GUI; skip when a read is under way."""
        if self._gpu_read_pending:
            return
        self._gpu_read_pending = True
        run_in_background(read_gpu_usage, self._on_gpu_usage, owner=self)

    def _on_gpu_usage(self, result: object, error: BaseException | None) -> None:
        """Show the card's load (GUI thread); the first reading creates the GPU rows."""
        self._gpu_read_pending = False
        if error is not None or not isinstance(result, GpuUsage):
            return  # the driver gave no reading this second (a reset, a busy device)
        usage = result
        if self._gpu_bar is None:
            self._gpu_bar, self._gpu_val = self._make_row("GPU")
            self._vram_bar, self._vram_val = self._make_row("VRAM")
        self._gpu_bar.setValue(usage.busy_percent)
        if self._gpu_val:
            self._gpu_val.setText(f"{usage.busy_percent} %")
        if usage.vram_used_mb is None or not usage.vram_total_mb:
            return
        if self._vram_bar:
            self._vram_bar.setValue(int(100 * usage.vram_used_mb / usage.vram_total_mb))
        if self._vram_val:
            self._vram_val.setText(
                f"{usage.vram_used_mb / 1024:.1f} / {usage.vram_total_mb / 1024:.1f} GB"
            )
