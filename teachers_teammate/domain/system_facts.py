"""What an Ollama server and its machine report: graphics cards, memory and loaded models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .model_tags import digest_in, normalize_tag
from .ollama_discovery import WaitDetail, WaitKind


@dataclass
class GpuInfo:
    """One graphics card of this computer, as detection found it."""

    vendor: str  # "nvidia" or "amd"
    name: str
    #: Dedicated graphics memory in MB, or None when it could not be read.
    vram_mb: int | None = None


@dataclass
class GpuUsage:
    """How busy one graphics card is right now, and how much of its memory it uses."""

    #: Share of the card's computing units in use, 0 to 100.
    busy_percent: int
    #: Dedicated graphics memory in use, in MB, or None when it could not be read.
    vram_used_mb: int | None = None
    #: Dedicated graphics memory in total, in MB, or None when it could not be read.
    vram_total_mb: int | None = None


@dataclass(frozen=True)
class SystemUsage:
    """How busy this computer's processor is right now, and how much main memory is in use."""

    #: Share of the processor in use since the previous reading, 0 to 100.
    cpu_percent: float
    #: Share of main memory in use, 0 to 100.
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float


@dataclass(frozen=True)
class LoadedModel:
    """A model the server holds in memory, with how much of it sits on the graphics card."""

    name: str
    size_bytes: int
    """Memory the model takes in total, on the card and in main memory together."""
    size_vram_bytes: int
    """The part of it on the graphics card."""
    context_length: int = 0
    expires_at: str = ""
    """When the server will unload it; it moves on every poll while the model is generating."""

    @property
    def on_card_share(self) -> float:
        """Return the share (0 to 1) of the model that sits on the card."""
        return self.size_vram_bytes / self.size_bytes if self.size_bytes else 0.0


@dataclass(frozen=True)
class ServerActivity:
    """What the server is doing right now, as far as ``/api/ps`` can tell.

    "Busy" is inferred: a model whose ``expires_at`` moved since the previous look is
    generating.  That is undocumented behaviour, so it only chooses the wording of a note and
    never what the app does.
    """

    loaded: tuple[LoadedModel, ...] = ()
    """The models the server holds in memory."""
    busy: tuple[str, ...] = ()
    """The models that were generating when the server was asked."""
    reachable: bool = True
    """The server answered.  ``False`` means nothing is known, not that nothing is loaded."""

    def has(self, model: str) -> bool:
        """Return whether *model* is in memory."""
        wanted = normalize_tag(model)
        return any(normalize_tag(item.name) == wanted for item in self.loaded)

    def loaded_names(self) -> tuple[str, ...]:
        """Return the names of the models in memory."""
        return tuple(item.name for item in self.loaded)

    def busy_other_than(self, model: str) -> str:
        """Return a model that is generating and is not *model*, or ``""``."""
        wanted = normalize_tag(model)
        for name in self.busy:
            if normalize_tag(name) != wanted:
                return name
        return ""

    def wait_detail(self, model: str) -> WaitDetail:
        """Return what to tell the teacher about a request for *model* that has no answer yet.

        Nothing when the model is in memory: it is reading the text, so the first token is
        close.  Otherwise another model is still generating, or *model* is being loaded.
        """
        if not self.reachable or self.has(model):
            return WaitDetail()
        other = self.busy_other_than(model)
        if other:
            return WaitDetail(WaitKind.BUSY, model, other)
        return WaitDetail(WaitKind.LOADING, model)


@dataclass(frozen=True)
class ServerFacts:
    """What the server says about itself: its version, the model digests, what is loaded.

    The version and the digests are what makes a stored measurement go out of date: a tag is a
    name that can be re-pointed, and Ollama counts tokens differently between versions.
    """

    version: str = ""
    """The server's version; ``""`` when it did not say."""
    digests: Mapping[str, str] = field(default_factory=dict)
    """``{tag: digest}`` of the installed models."""
    loaded: tuple[LoadedModel, ...] = ()
    """The models the server held in memory when it was asked."""

    def digest_of(self, tag: str) -> str:
        """Return the digest of *tag*, or ``""`` when the server did not report one."""
        return digest_in(self.digests.items(), tag)

    def loaded_names(self) -> tuple[str, ...]:
        """Return the names of the models the server held in memory."""
        return tuple(model.name for model in self.loaded)

    def loaded_model(self, tag: str) -> LoadedModel | None:
        """Return the loaded model named *tag*, or ``None`` when it is not loaded."""
        wanted = normalize_tag(tag)
        for model in self.loaded:
            if normalize_tag(model.name) == wanted:
                return model
        return None
