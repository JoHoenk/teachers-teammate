"""Offline service stubs shared by the Settings page and window tests.

Each stub answers exactly what the pages ask the application service: provider and model
queries, hardware profiles and ratings, and (for the Add-ons page) what is installed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
from datetime import date
import threading

from teachers_teammate.application.data_flow import llm_location
from teachers_teammate.application.ollama_server import OllamaStatus
from teachers_teammate.application.stored_results import StoredSummary
from teachers_teammate.application.setup_plan import (
    LocalModelChoices,
    PrivacyCase,
    ProofreadingPlace,
    SetupAnswers,
    SetupFacts,
    SetupPlan,
    build_model_choices,
    build_setup_plan,
)
from teachers_teammate.domain.data_location import DataRegion, StageLocation
from teachers_teammate.domain.install_item import InstallItem, InstallKind
from teachers_teammate.domain.model_catalog import (
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    best_model,
)
from teachers_teammate.domain.model_listing import ListedModel, ModelListing
from teachers_teammate.domain.name_models import (
    FALLBACK_SPACY_MODEL,
    SPACY_MODELS,
    spacy_model_for,
)
from teachers_teammate.domain.ollama_discovery import DiscoveryScope, OllamaState
from teachers_teammate.domain.performance_estimate import TimeRange
from teachers_teammate.domain.providers import AddonPackage, ProviderInfo

from ._service_stubs import RecommendationServiceStub, context_choice_from


class InertLatestTask:
    """Stand-in for ``LatestTask`` that runs nothing and answers nothing.

    Replaced for one module's ``LatestTask`` name so a page built in a test starts no
    background work without touching the class every other page uses.
    """

    def __init__(self, _owner=None) -> None:
        pass

    def submit(self, _fn, _on_done) -> int:
        return 0

    def submit_streaming(self, _fn, _on_item, _on_done) -> int:
        return 0

    def cancel(self) -> None:
        pass


class DummyAppService(RecommendationServiceStub):
    """Minimal app-service stub for settings dialog tests."""

    def summary(self) -> StoredSummary:
        """Answer that nothing is stored."""
        return StoredSummary(documents=0, bytes_taken=0)

    def delete(self, *, include_work: bool = False) -> None:
        _ = include_work

    def busy(self) -> bool:
        """Answer that no run is going."""
        return False

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def spacy_model_for_language(self, language: str) -> str:
        """Answer with the real table: the model of a language, else the multilingual one."""
        return spacy_model_for(language)

    #: The spaCy models this stub reports as downloaded.
    installed_spacy: tuple[str, ...] = ()
    #: The model of the language of this computer, when a test wants one.
    system_model: str | None = None

    def list_installed_spacy_models(self) -> list[str]:
        """Answer with the models the test installed."""
        return list(self.installed_spacy)

    def system_spacy_model(self) -> str | None:
        """Answer with the model the test gave this computer's language."""
        return self.system_model

    def extra_name_models(self, language: str) -> list[str]:
        """Answer with the real rule: every model but the one of the document language."""
        own = spacy_model_for(language)
        models = dict.fromkeys([*SPACY_MODELS.values(), FALLBACK_SPACY_MODEL])
        return [model for model in models if model != own]

    def context_choice(self, settings: Mapping[str, object], card_gb: float | None):
        """Answer with the real rule: the page must not promise a window the run does not use."""
        return context_choice_from(settings, card_gb)

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "ppocr"]

    def pending_addon_packages(self) -> tuple[str, ...]:
        """Nothing waits for a restart unless a test says so."""
        return ()

    def get_provider_info(self, provider: str) -> ProviderInfo:
        if provider == "openai":
            return ProviderInfo(
                name="openai",
                display_name="OpenAI (ChatGPT)",
                data_region=DataRegion.NON_EU,
                vendor_country="USA",
                needs_api_key=True,
                key_url="https://platform.openai.com/api-keys",
                env_key="OPENAI_API_KEY",
                can_list_models=True,
                models=("gpt-4o", "gpt-4o-mini"),
            )
        return ProviderInfo(
            name=provider,
            display_name="Ollama" if provider == "ollama" else provider,
            self_hosted=provider == "ollama",
            models=(f"{provider}-model",),
        )

    def describe_llm_location(self, provider: str, model: str, ollama_url: str) -> StageLocation:
        """Answer with the real rule, so the pages' labels and hints follow the provider info."""
        return llm_location(provider, model, ollama_url, self.get_provider_info)

    def get_provider_data_region(self, provider: str) -> str:
        """Answer with the region the provider info names, as the real service does."""
        return self.get_provider_info(provider).data_region.value

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

    def ollama_status(self, url: str) -> OllamaStatus:
        """Answer as a running server would: the pages' status line then has nothing to fix."""
        _ = url
        return OllamaStatus(state=OllamaState.RUNNING, version="0.12.3", model_count=2)

    def discover_ollama_servers(self, scope: DiscoveryScope) -> list:
        _ = scope
        return []

    def start_ollama(self) -> None:
        pass

    def is_local(self, url: str, *, include_wsl_host: bool = False) -> bool:
        """Answer as the real check does for the addresses the pages use."""
        _ = include_wsl_host
        return url.startswith("http://127.0.0.1") or url.startswith("http://localhost")

    def normalize(self, url: str) -> str:
        return url.rstrip("/")

    def addon_license(self, package: str) -> str | None:
        _ = package
        return None

    def system_language(self) -> str | None:
        return None

    def read_gpu_usage(self):
        return None


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

    def get_provider_info(self, provider: str) -> ProviderInfo:
        if provider in ("mistral", "mistral_eu"):
            return ProviderInfo(
                name=provider,
                display_name="Mistral" if provider == "mistral" else "Mistral (EU)",
                data_region=DataRegion.UNKNOWN if provider == "mistral" else DataRegion.EU,
                vendor_country="France",
                needs_api_key=True,
                env_key="MISTRAL_API_KEY",
                package=AddonPackage(module="langchain_mistralai", pip="langchain-mistralai"),
                models=("mistral-small-latest", "open-mistral-nemo"),
            )
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

    def get_provider_info(self, provider: str) -> ProviderInfo:
        info = super().get_provider_info(provider)
        if provider in ("mistral", "mistral_eu"):
            return dataclasses.replace(info, can_list_models=True)
        return info

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        self.release.wait(10)
        return super().list_provider_models_checked(provider, base_url=base_url)


class NonListableService(DummyAppService):
    """A provider whose models cannot be listed: only its built-in list exists."""

    def list_providers(self) -> list[str]:
        return ["anthropic"]

    def get_provider_info(self, provider: str) -> ProviderInfo:
        return ProviderInfo(
            name=provider,
            display_name="Anthropic",
            data_region=DataRegion.NON_EU,
            vendor_country="USA",
            needs_api_key=True,
            env_key="ANTHROPIC_API_KEY",
            models=("claude-opus-4-5", "claude-haiku-4-5", "claude-3-5-sonnet-20241022"),
        )


def select_provider(section, provider_id: str) -> None:
    """Choose the AI service with id *provider_id* in *section*, as the user would."""
    index = section._provider.findData(provider_id)
    assert index >= 0, f"{provider_id} is not offered"
    section._provider.setCurrentIndex(index)


def commit_key(row, value: str) -> None:
    """Type *value* into a key row and leave the field, so it is committed."""
    row.field.setText(value)
    row.field.editingFinished.emit()


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


class MeasuredCatalogAppService(CatalogAppService):
    """A catalog service that also reports one model as measured on this computer."""

    #: The tag the speed test measured, and how long one page takes with it.
    measured_tag = "qwen3.5:9b"
    measured_page = TimeRange(25.0, 70.0)

    def page_times(
        self,
        tags: Sequence[str],
        category: ModelCategory,
        ollama_url: str,
        *,
        hardware: HardwareDetection | None = None,
    ) -> dict[str, TimeRange]:
        _ = category, ollama_url, hardware
        if self.measured_tag not in tags:
            return {}
        return {self.measured_tag: self.measured_page}


# ── what the Add-ons page asks in addition ─────────────────────────────────────


class AddonsCapableService(DummyAppService):
    """``DummyAppService`` that also answers the Add-ons page's installation queries."""

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {}

    def get_installation_status(self, *, ollama_models=None, **_kwargs) -> dict[InstallItem, bool]:
        return {InstallItem(InstallKind.OLLAMA, m): False for m in (ollama_models or [])}

    def list_ollama_model_sizes(self, url: str) -> dict[str, int]:
        _ = url
        return {}


class ProviderAddonsService(ProviderAppService):
    """``ProviderAppService`` with the Add-ons page's installation queries and package list."""

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {
            "mistral": ("langchain_mistralai", "langchain-mistralai"),
            "openai": ("langchain_openai", "langchain-openai"),
        }

    def get_installation_status(self, *, ollama_models=None, **_kwargs) -> dict[InstallItem, bool]:
        status = {InstallItem(InstallKind.OLLAMA, m): False for m in (ollama_models or [])}
        for name in self.list_provider_packages():
            status[InstallItem(InstallKind.PROVIDER, name)] = name not in self.missing
        return status

    def list_ollama_model_sizes(self, url: str) -> dict[str, int]:
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


class SetupAppService(DummyAppService):
    """A service that answers the assistant's plan question with the real rules.

    ``setup_facts`` is canned (a 16 GB computer with a 16 GB card and plenty of disk), so the
    plan the pages see is the one the real rules produce for those facts.
    """

    def __init__(self) -> None:
        super().__init__()
        #: The Ollama tags this computer has.
        self.installed: list[str] = []
        #: Whether Ollama answers, so its models can be pulled now.
        self.ollama_running = True
        #: Free space where Ollama keeps its models.
        self.free_disk_gb: float | None = 200.0
        #: The answers every plan was asked for, in order.
        self.plan_calls: list[SetupAnswers] = []
        #: The pip package an online service still needs, by provider.
        self.missing: dict[str, str] = {}

    @property
    def setup(self) -> SetupAppService:
        """Answer the assistant's plan questions itself (``services_of`` points here too)."""
        return self

    def missing_provider_package(self, provider: str) -> str | None:
        """Answer with the package this stub was told is missing."""
        return self.missing.get(provider)

    def setup_facts(self, answers: SetupAnswers, current: Mapping[str, object]) -> SetupFacts:
        """Answer with canned facts about this computer."""
        return SetupFacts(
            profile=HardwareProfile.CARD_16GB,
            system_memory_gb=32.0,
            card_gb=16.0,
            ollama_running=self.ollama_running,
            ollama_here=self.is_local(answers.ollama_url, include_wsl_host=True),
            installed_tags=frozenset(self.installed),
            free_disk_gb=self.free_disk_gb,
            removal_on=bool(current.get("anonymization_enabled")),
            name_detection=self.anonymizer_ready,
            spacy_installed=self.anonymizer_ready,
            spacy_model="de_core_news_sm",
            installed_spacy_models=frozenset(self.installed_spacy),
            missing_package=self.missing_provider_package(answers.online_choice[0]) or "",
        )

    def privacy_case(self, answers: SetupAnswers) -> PrivacyCase:
        """Answer as the real service does: the address is asked only for a local proofreading."""
        here = answers.proofreading is not ProofreadingPlace.LOCAL or self.is_local(
            answers.ollama_url, include_wsl_host=True
        )
        return answers.privacy_case(ollama_here=here)

    def plan_setup(self, answers: SetupAnswers, current: Mapping[str, object]) -> SetupPlan:
        """Answer with the real plan for these answers and facts."""
        self.plan_calls.append(answers)
        facts = self.setup_facts(answers, current)
        return build_setup_plan(
            answers,
            current,
            facts,
            rate=lambda tag, task: self.rate_ollama_models(
                [tag], task, facts.profile, answers.ollama_url
            )[0],
            best=lambda task: best_model(task, facts.profile),
            default_model=self.default_model_for_task,
        )

    def plan_with_model_choices(
        self, answers: SetupAnswers, current: Mapping[str, object]
    ) -> tuple[SetupPlan, LocalModelChoices]:
        """Answer with the real plan and choices for these answers and facts."""
        facts = self.setup_facts(answers, current)
        plan = self.plan_setup(answers, current)
        step = next((row for row in plan.steps if row.stage == "correction"), None)
        choices = build_model_choices(
            facts,
            suggested=step.model if step is not None else "",
            rate=lambda tag, task: self.rate_ollama_models(
                [tag], task, facts.profile, answers.ollama_url
            )[0],
        )
        return plan, choices
