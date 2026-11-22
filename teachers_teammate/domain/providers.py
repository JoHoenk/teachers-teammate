"""What one LLM provider is: declared once, in the provider's own module.

A module in ``infrastructure/providers/`` builds a :class:`ProviderInfo`.  Everything that
needs a fact about a provider — the model pickers, the Add-ons page, the privacy wording, the
Add-ons installer — reads it from there, so a provider is one file and no table repeats it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data_location import DataRegion


@dataclass(frozen=True)
class AddonPackage:
    """The pip package a provider needs, and how the app finds it again."""

    #: Top-level module name the package is imported by (``langchain_openai``).
    module: str
    #: pip name without a version (``langchain-openai``).
    pip: str
    #: Version range this release was tested with (``">=0.2"``), appended to :attr:`pip`.
    version_spec: str = ""

    @property
    def requirement(self) -> str:
        """The pip requirement: the package with its version range."""
        return f"{self.pip}{self.version_spec}"


@dataclass(frozen=True)
class ProviderInfo:
    """What the app knows about one provider besides how to build its client."""

    #: Provider id: the module name in ``infrastructure/providers/`` and the value in a `Config`.
    name: str
    #: The name a teacher reads.
    display_name: str
    #: Where the vendor says requests are processed.
    data_region: DataRegion = DataRegion.UNKNOWN
    #: Home country of the vendor, for the privacy tooltip.
    vendor_country: str = ""
    #: The provider needs an API key before it can be asked anything.
    needs_api_key: bool = False
    #: The vendor's page where a key is created.
    key_url: str = ""
    #: The environment variable the provider reads its key from.
    env_key: str = ""
    #: The provider talks to an address the teacher names (an Ollama server).
    needs_base_url: bool = False
    #: The service can be asked which models it serves.
    can_list_models: bool = False
    #: The provider runs on a server the teacher names, not at a vendor.
    self_hosted: bool = False
    #: Only its strict mode answers the review JSON schema.
    strict_json_schema: bool = False
    #: The pip package the provider needs; ``None`` for one that ships in the base install.
    package: AddonPackage | None = None
    #: The app works without :attr:`package`: it talks to the service directly.
    package_optional: bool = False
    #: Models to offer when the service cannot be asked; empty when the list is always live.
    models: tuple[str, ...] = ()


def display_name_of(provider: str, info: ProviderInfo | None) -> str:
    """Return the name a teacher reads for *provider*: what it declares, else its id."""
    return (info.display_name if info is not None else "") or provider
