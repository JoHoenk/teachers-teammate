"""What a teacher installs, named by a kind and a name.

The Add-ons page, the install round, the Setup Assistant and the fixes that lead to them name
the same things: a pip package, the package of one AI service, a name-detection model, the
built-in reader's model files, the page models, an Ollama model, and the add-ons of an app
update.  One type for all of them, so a box, a step of the round and a status answer carry the
same name instead of a string put together in one place and taken apart in another.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .data_location import PROVIDER_OLLAMA


class InstallKind(StrEnum):
    """The kinds of item the add-ons vocabulary knows."""

    PACKAGE = "package"
    """A pip package of its own (spaCy, a name-detection model file)."""

    PROVIDER = "provider"
    """The pip package of one AI service."""

    SPACY = "spacy"
    """A name-detection model."""

    READER = "reader"
    """The built-in reader's model files."""

    PAGE_MODELS = "page_models"
    """The page models of the automatic image preparation."""

    OLLAMA = PROVIDER_OLLAMA
    """A model on the Ollama server."""

    LANGCHAIN = "langchain"
    """A package the LLM factory imports."""

    UPDATE = "update"
    """The add-ons installed again after an app update."""

    BUNDLED = "bundled"
    """Whether the app carries an item's files itself."""


@dataclass(frozen=True, order=True)
class InstallItem:
    """One thing on the install list: which kind, and which one of that kind."""

    kind: InstallKind
    name: str = ""

    def __str__(self) -> str:
        return self.key

    @property
    def key(self) -> str:
        """Return the item as one string: the kind, and the name when there is one."""
        return f"{self.kind}:{self.name}" if self.name else str(self.kind)

    @property
    def bundled(self) -> InstallItem:
        """Return the item that says whether the app carries this one's files itself."""
        return InstallItem(InstallKind.BUNDLED, self.key)


SPACY_ITEM = InstallItem(InstallKind.PACKAGE, "spacy")
"""spaCy itself: the library the name detection runs on."""

READER_ITEM = InstallItem(InstallKind.READER, "ppocr")
"""The built-in reader's model files."""

PAGE_MODELS_ITEM = InstallItem(InstallKind.PAGE_MODELS, "all")
"""The page models of the automatic image preparation."""

UPDATE_ADDONS_ITEM = InstallItem(InstallKind.UPDATE, "addons")
"""The add-ons installed again after an app update."""
