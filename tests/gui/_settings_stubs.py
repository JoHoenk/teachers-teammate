"""Offline service stubs shared by the Settings page and window tests.

Each stub answers exactly what the pages ask the application service: provider and model
queries, hardware profiles and ratings, and (for the Add-ons page) what is installed.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import threading

from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.model_listing import ListedModel, ModelListing

from ._service_stubs import RecommendationServiceStub, context_choice_from


class DummyAppService(RecommendationServiceStub):
    """Minimal app-service stub for settings dialog tests."""

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def context_choice(self, settings: Mapping[str, object], card_gb: float | None):
        """Answer with the real rule: the page must not promise a window the run does not use."""
        return context_choice_from(settings, card_gb)

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract", "paddleocr"]

    def default_preprocess_for_engine(self, engine: str) -> str:
        return {
            "ollama": "grayscale",
            "tesseract": "clahe",
            "paddleocr": "grayscale",
        }.get(engine, "")

    def get_provider_info(self, provider: str) -> dict:
        if provider == "openai":
            return {
                "models": ["gpt-4o", "gpt-4o-mini"],
                "default_model": "gpt-4o-mini",
                "needs_api_key": True,
                "env_key": "OPENAI_API_KEY",
            }
        return {
            "models": [f"{provider}-model"],
            "default_model": f"{provider}-model",
            "needs_api_key": False,
            "env_key": "",
        }

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        _ = base_url
        return [f"{provider}-model"]

    def list_ocr_models(self, base_url: str = "") -> list[str]:
        return self.list_provider_models("ollama", base_url=base_url)

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return None

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        pass

    def is_module_importable(self, module: str) -> bool:
        return True

    def list_correction_prompt_presets(self) -> dict[str, str]:
        from teachers_teammate.infrastructure.correction import PRESET_PROMPTS  # noqa: PLC0415

        return dict(PRESET_PROMPTS)

    def default_correction_prompt(self) -> str:
        from teachers_teammate.infrastructure.correction import ENGLISH_PROMPT  # noqa: PLC0415

        return ENGLISH_PROMPT

    def default_answer_formats(self) -> tuple[str, str]:
        from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
            REVIEW_FORMAT,
            TEXT_FORMAT,
        )

        return REVIEW_FORMAT, TEXT_FORMAT

    def correction_notation_hint(self) -> str:
        from teachers_teammate.domain.notation import with_notation_hint  # noqa: PLC0415

        return with_notation_hint("", "markdown", task="correction").strip()

    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str:
        _ = text_format
        return ""


class CachingDummyAppService(DummyAppService):
    def __init__(self) -> None:
        super().__init__()
        self._cache: dict[tuple, list[str]] = {}

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return self._cache.get((provider, base_url))

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        self._cache.pop((provider, base_url), None)

    def seed_cache(self, provider: str, models: list[str], base_url: str = "") -> None:
        self._cache[(provider, base_url)] = models


class ProviderAppService(CachingDummyAppService):
    """Service with Mistral and Mistral (EU) whose model answers and missing package are set."""

    def __init__(self) -> None:
        super().__init__()
        self.checked_calls: list[str] = []
        self.invalidated: list[str] = []
        self.listing = ModelListing(["glm-live", "mistral-large-latest"], "live")
        self.missing: dict[str, str] = {}

    def list_providers(self) -> list[str]:
        return ["mistral", "mistral_eu", "ollama", "openai"]

    def get_provider_info(self, provider: str) -> dict:
        if provider in ("mistral", "mistral_eu"):
            return {
                "display_name": "Mistral" if provider == "mistral" else "Mistral (EU)",
                "models": ["mistral-small-latest", "open-mistral-nemo"],
                "default_model": "mistral-small-latest",
                "needs_api_key": True,
                "env_key": "MISTRAL_API_KEY",
            }
        return super().get_provider_info(provider)

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        self.checked_calls.append(provider)
        return self.listing

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        self.invalidated.append(provider)
        super().invalidate_model_cache(provider, base_url)

    def missing_provider_package(self, provider: str) -> str | None:
        return self.missing.get(provider)


class SlowMistralService(ProviderAppService):
    """Mistral that can be asked for its list, but answers only when released."""

    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def get_provider_info(self, provider: str) -> dict:
        info = dict(super().get_provider_info(provider))
        if provider in ("mistral", "mistral_eu"):
            info["can_list_models"] = True
        return info

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        self.release.wait(10)
        return super().list_provider_models_checked(provider, base_url=base_url)


class NonListableService(DummyAppService):
    """A provider whose models cannot be listed: only its built-in list exists."""

    def list_providers(self) -> list[str]:
        return ["anthropic"]

    def get_provider_info(self, provider: str) -> dict:
        return {
            "display_name": "Anthropic",
            "models": ["claude-opus-4-5", "claude-haiku-4-5", "claude-3-5-sonnet-20241022"],
            "default_model": "claude-haiku-4-5",
            "needs_api_key": True,
            "env_key": "ANTHROPIC_API_KEY",
        }


def curated_listing() -> ModelListing:
    """Return a curated Mistral answer: two current models, a pinned one, a fine-tuned one."""
    details = (
        ListedModel("mistral-small-latest", "current", "★ suggested", "Small and fast."),
        ListedModel("mistral-large-latest", "current"),
        ListedModel("mistral-small-2506", "pinned", "pinned version"),
        ListedModel("ft:mistral-small-latest:5f1c", "own", "your model"),
    )
    return ModelListing(
        [model.id for model in details],
        "live",
        details=details,
        retiring=(
            ListedModel(
                "mistral-large-2407",
                retires_on=date(2026, 11, 30),
                replacement="mistral-large-latest",
            ),
        ),
    )


class CatalogAppService(CachingDummyAppService):
    """Dummy service whose Ollama server has real catalog models installed."""

    def __init__(self, models: list[str]) -> None:
        super().__init__()
        self._models = models

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        _ = base_url
        return self._models if provider == "ollama" else [f"{provider}-model"]


# ── what the Add-ons page asks in addition ─────────────────────────────────────


class AddonsCapableService(DummyAppService):
    """``DummyAppService`` that also answers the Add-ons page's installation queries."""

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {}

    def get_installation_status(self, *, ollama_models=None, **_kwargs) -> dict[str, bool]:
        return {f"ollama:{m}": False for m in (ollama_models or [])}

    def list_ollama_model_sizes(self, url: str = "") -> dict[str, int]:
        _ = url
        return {}


class ProviderAddonsService(ProviderAppService):
    """``ProviderAppService`` with the Add-ons page's installation queries and package list."""

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {
            "mistral": ("langchain_mistralai", "langchain-mistralai"),
            "openai": ("langchain_openai", "langchain-openai"),
        }

    def get_installation_status(self, *, ollama_models=None, **_kwargs) -> dict[str, bool]:
        status = {f"ollama:{m}": False for m in (ollama_models or [])}
        for name in self.list_provider_packages():
            status[f"provider:{name}"] = name not in self.missing
        return status

    def list_ollama_model_sizes(self, url: str = "") -> dict[str, int]:
        _ = url
        return {}


class FakeKeyring:
    """In-memory stand-in for the ``keyring`` module (a working system keychain)."""

    def __init__(self, *, refuses_writes: bool = False) -> None:
        self.passwords: dict[tuple[str, str], str] = {}
        self.refuses_writes = refuses_writes

    def get_password(self, service: str, name: str) -> str | None:
        return self.passwords.get((service, name))

    def set_password(self, service: str, name: str, value: str) -> None:
        if self.refuses_writes:
            raise RuntimeError("the keychain is locked")
        self.passwords[(service, name)] = value

    def delete_password(self, service: str, name: str) -> None:
        if (service, name) not in self.passwords:
            raise RuntimeError("no such password")
        del self.passwords[(service, name)]
