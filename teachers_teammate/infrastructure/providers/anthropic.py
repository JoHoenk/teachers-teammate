"""Anthropic provider: create a ``ChatAnthropic`` BaseChatModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.data_location import DataRegion
from teachers_teammate.domain.providers import AddonPackage, ProviderInfo
from teachers_teammate.infrastructure.providers._helpers import api_key_check, import_guard

PROVIDER = ProviderInfo(
    name="anthropic",
    display_name="Anthropic (Claude)",
    data_region=DataRegion.NON_EU,
    vendor_country="USA",
    needs_api_key=True,
    # The vendor's page where a key is created; the Settings window links to it.
    key_url="https://console.anthropic.com/settings/keys",
    env_key="ANTHROPIC_API_KEY",
    package=AddonPackage(
        module="langchain_anthropic", pip="langchain-anthropic", version_spec=">=0.1"
    ),
    models=(
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
    ),
)


ANSWER_TOKENS = 4096
"""The answer allowance every model above serves: ``claude-3-opus`` stops there, the others
later.  Enough for a long text-mode proofreading answer, and the app reports an answer cut off
here instead of storing it."""


def list_models(**_kwargs: object) -> list[str]:
    """Return the static Anthropic model list (no public listing API available)."""
    return list(PROVIDER.models)


def create(
    model: str,
    *,
    temperature: float = 0.7,
    api_key: str | None = None,
    **_kwargs: object,
) -> BaseChatModel:
    """Return a ``ChatAnthropic`` instance for *model*.

    The key comes in as *api_key*; with none, LangChain reads ``$ANTHROPIC_API_KEY`` itself.

    The answer allowance is passed as well: ``ChatAnthropic`` sends 1024 tokens of its own when
    none is given, which a text-mode proofreading answer overruns on a long text.
    """
    with import_guard("langchain-anthropic"):
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
    return ChatAnthropic(
        model=model,
        temperature=temperature,
        max_tokens=ANSWER_TOKENS,
        api_key=api_key,
    )


check_connection = api_key_check(PROVIDER.env_key)
