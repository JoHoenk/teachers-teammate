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
    from ..domain.credentials import KeyLocation
    from ..domain.model_catalog import GpuInfo, GpuUsage, ServerFacts
    from ..domain.ollama_discovery import WaitDetail
    from ..infrastructure.ollama_utils import ServerActivity
    from ..interfaces import Anonymizer, ConnectionCheck


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

    def check_connection(self, url: str, model: str = "") -> ConnectionCheck:
        """Return ``(connected, model_ok, message)`` for the server at *url*."""
        ...

    def model_capabilities(self, url: str, tag: str, *, timeout: float) -> frozenset[str] | None:
        """Return the capabilities the server reports for *tag* (``{"completion", "vision"}``, …).

        ``None`` when it cannot say.
        """
        ...

    def server_facts(self, url: str) -> ServerFacts:
        """Return the version, the model digests and the loaded models at *url*."""
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


#: Builds the Ollama client of one address; *cancel_event* makes a waiting request abandonable.
#: The client is opaque here: only the probe that drives it knows its shape.
OllamaClientFactory = Callable[[str, threading.Event | None], object]


class Credentials(Protocol):
    """The API keys of the online AI services, as the parts that ask a service see them."""

    def key_for(self, provider: str) -> str | None:
        """Return the key *provider* sends to its service, or ``None`` when there is none."""
        ...


class SecretStore(Protocol):
    """Where the API keys are kept: the system's secret store, else the app's settings file."""

    def location(self) -> KeyLocation:
        """Return where a key saved now would be kept."""
        ...

    def get(self, env_key: str) -> str:
        """Return the stored key for *env_key* (``""`` when there is none)."""
        ...

    def set(self, env_key: str, value: str) -> KeyLocation:
        """Store *value* for *env_key* and return where it ended up."""
        ...

    def remove(self, env_key: str) -> None:
        """Forget the key for *env_key* wherever it is kept."""
        ...

    def migrate(self, env_key: str) -> None:
        """Move a key still kept in the settings file into the secret store, when there is one."""
        ...


class RunActivity(Protocol):
    """The run in progress, as the parts of the app that must wait for it see it.

    ``RunService`` implements it: anything that writes the files a run is writing asks first.
    """

    def busy(self) -> bool:
        """Return whether a run is going in this process."""
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


class ReleaseFeed(Protocol):
    """Where the newest published release of this project is read."""

    def latest(self) -> tuple[str, str]:
        """Return the ``(tag, url)`` of the newest release, or raise the failure of the ask."""
        ...
