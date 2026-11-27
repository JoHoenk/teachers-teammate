"""One answer of an Ollama server: its version and the models it has installed.

A server's answer is what every question about it is answered from — which models a run can
use, which digest a measurement belongs to, what a model can do.  Keeping one answer means a
pull or an upgrade changes all of them at once, and nothing compares a list from one moment
with a digest from another.

Nothing here is in memory: what ``/api/ps`` reports changes with every request and is asked
again each time it is needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model_catalog import LoadedModel, ServerFacts, digest_in

SNAPSHOT_TTL_S = 300.0
"""How long an answer of a server is used before it is asked again."""


@dataclass(frozen=True)
class InstalledModel:
    """One installed model: the tag it is asked by, its weights, and how big it is."""

    tag: str
    digest: str = ""
    """The digest of the weights; ``""`` when the server did not report one."""
    size_bytes: int = 0
    """The downloaded bytes; ``0`` when the server did not report them."""


@dataclass(frozen=True)
class ServerSnapshot:
    """One answer of a server: its version and what it has installed."""

    version: str = ""
    """The server's version; ``""`` when it did not say."""
    models: tuple[InstalledModel, ...] = ()
    unreachable: str = ""
    """``""`` means the server answered; otherwise the message saying why it could not be asked.

    An unreachable snapshot lists no model: nothing is known about the server, so nothing of
    it may be shown as if the server had said it.
    """

    @property
    def reason(self) -> str:
        """Return :attr:`unreachable` without the check line's mark, for a line with its own."""
        return self.unreachable.removeprefix("✗").strip()

    @property
    def tags(self) -> list[str]:
        """Return the tags of the installed models, in the order the server listed them."""
        return [model.tag for model in self.models]

    def digest_of(self, tag: str) -> str:
        """Return the digest of *tag*, or ``""`` when the server did not report one."""
        return digest_in(((model.tag, model.digest) for model in self.models), tag)

    def sizes(self) -> dict[str, int]:
        """Return ``{tag: size_bytes}`` of the installed models."""
        return {model.tag: model.size_bytes for model in self.models}

    def facts(self, loaded: tuple[LoadedModel, ...] = ()) -> ServerFacts:
        """Return the version and the digests as :class:`ServerFacts`, with *loaded* in memory.

        What is in memory is asked by the caller that needs it: it changes between two looks,
        so it is never part of a remembered answer.
        """
        return ServerFacts(
            version=self.version,
            digests={model.tag: model.digest for model in self.models},
            loaded=loaded,
        )
