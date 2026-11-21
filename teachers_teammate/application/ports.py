"""The collaborators a service branches on, as the narrowest shape it needs.

A service in this layer does not reach into ``infrastructure`` from a method body: what it needs
of a module comes in through its constructor, typed by one of these protocols.
``composition.py`` hands over the class of the running app; a test hands over a fake, instead of
patching a module path.

Each protocol names only what its callers ask.  The rest of that module — installs with their
own progress and errors, the machine facts of this process, the pure helpers on strings and
names — stays a module, and a service keeps importing it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import threading
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ..domain.anonymization import AnonymizerConfig
    from ..domain.model_catalog import GpuUsage
    from ..domain.ollama_discovery import WaitDetail
    from ..infrastructure.gpu_detector import GpuInfo
    from ..infrastructure.ollama_utils import ServerActivity
    from ..interfaces import Anonymizer


class OllamaServer(Protocol):
    """The Ollama server at an address: what it holds, what it is doing, and pulling a model.

    ``OllamaServerService`` implements it: the server's address is an argument of every call, so
    no method of a service works with a default address of this computer.
    """

    def listed_models(self, url: str) -> tuple[list[str], str]:
        """Return ``(installed, unreachable)`` for the server at *url*."""
        ...

    def list_ollama_model_sizes(self, url: str) -> dict[str, int]:
        """Return ``{model_name: size_bytes}`` for the models installed at *url*."""
        ...

    def activity(self, url: str, model: str = "") -> ServerActivity:
        """Return what the server at *url* holds in memory."""
        ...

    def wait_for(self, url: str, model: str) -> WaitDetail:
        """Return why *model* has to wait for another one at *url*, or nothing."""
        ...

    def pull_ollama_model(
        self,
        url: str,
        model_name: str,
        progress_cb: Callable[[str, float, float], None] | None = None,
        abort_event: threading.Event | None = None,
    ) -> None:
        """Pull (download) *model_name* from the server at *url*."""
        ...

    def unload(self, url: str, model: str) -> None:
        """Drop *model* out of the server's memory."""
        ...

    def is_local(self, url: str, *, include_wsl_host: bool = False) -> bool:
        """Return whether *url* points at this computer (or the Windows host behind WSL)."""
        ...

    def program_found(self) -> bool:
        """Return whether the Ollama program is installed on this computer."""
        ...

    def can_start(self) -> bool:
        """Return whether the app can start Ollama from here."""
        ...


class AddonState(Protocol):
    """The add-on packages folder: what it holds, and what waits for the next start."""

    def packages_dir(self) -> Path:
        """Return the folder the add-on packages are installed into."""
        ...

    def outdated(self) -> bool:
        """Return whether an app update left add-ons that wait for *Update add-ons*."""
        ...

    def in_folder(self, distribution: str) -> bool:
        """Return whether *distribution* is installed as an add-on."""
        ...

    def restart_needed(self) -> bool:
        """Return whether an install waits for the next start (``packages.pending/``)."""
        ...

    def pending(self) -> list[str]:
        """Return the names of the packages that wait for the next start."""
        ...

    def waits_for_restart(self, distribution: str) -> bool:
        """Return whether *distribution* is installed but waits for the next start."""
        ...

    def importable(self, addon: str) -> bool:
        """Return whether the named add-on package can be imported now."""
        ...


class HardwareProbe(Protocol):
    """This computer: its graphics cards and the memory a model has to fit into."""

    def gpus(self) -> Sequence[GpuInfo]:
        """Return the graphics cards of this computer."""
        ...

    def apple_unified_memory_gb(self) -> float | None:
        """Return the shared memory of an Apple Silicon computer, or ``None``."""
        ...

    def system_memory_gb(self) -> float | None:
        """Return the main memory of this computer, or ``None`` when it cannot be read."""
        ...

    def available_memory_gb(self) -> float | None:
        """Return the main memory free right now, or ``None`` when it cannot be read."""
        ...

    def gpu_usage(self) -> GpuUsage | None:
        """Return how busy the first graphics card is, or ``None`` when it cannot be read."""
        ...


class NameModels(Protocol):
    """Name detection's spaCy models: which are installed, and loading one."""

    def installed(self) -> list[str]:
        """Return the sorted names of the installed spaCy models, or ``[]`` without spaCy."""
        ...

    def anonymizer(self, language: str, config: AnonymizerConfig | None = None) -> Anonymizer:
        """Return an anonymizer for *language*, loaded from the models *config* names."""
        ...
