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
from contextlib import AbstractContextManager
import threading
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import Config
    from ..domain.anonymization import AnonymizerConfig
    from ..domain.credentials import KeyLocation
    from ..domain.data_location import StageLocation
    from ..domain.formatted_text import TextFormat
    from ..domain.hardware_profile import HardwareDetection, HardwareProfileSetting
    from ..domain.model_catalog import ModelCategory, ModelUses
    from ..domain.model_fit import ModelRating
    from ..domain.model_listing import ModelListing
    from ..domain.ollama_discovery import WaitDetail
    from ..domain.ollama_snapshot import ServerSnapshot
    from ..domain.performance_estimate import TimeRange
    from ..domain.proofreading.compared_result import ComparedResult
    from ..domain.proofreading.option_marks import OptionMark
    from ..domain.proofreading.result import CorrectionOutcome
    from ..domain.proofreading.review import CorrectionReview
    from ..domain.providers import ProviderInfo
    from ..domain.system_facts import GpuInfo, GpuUsage, ServerActivity, SystemUsage
    from ..domain.teacher_work import TeacherWork
    from ..interfaces import Anonymizer, ConnectionCheck, Reporter


class OllamaServer(Protocol):
    """The Ollama server at an address: what it has installed, what it is doing, and pulls.

    ``OllamaServerService`` implements it: the server's address is an argument of every call, so
    no method of a service works with a default address of this computer.
    """

    def snapshot(
        self, url: str, *, max_age: float = ..., timeout: float | None = None
    ) -> ServerSnapshot:
        """Return what the server at *url* has installed, asking it when the answer is old.

        ``max_age=0`` always asks; *timeout* bounds the request.  Blocking network call.
        """
        ...

    def cached_snapshot(self, url: str, *, max_age: float | None = None) -> ServerSnapshot | None:
        """Return the latest answer held for *url* (maybe an unreachable one), or ``None``."""
        ...

    def forget(self, url: str) -> None:
        """Forget what the app knows about the server at *url*."""
        ...

    def generation(self, url: str) -> int:
        """Return how often the answer of *url* was forgotten; read it before asking."""
        ...

    def hold(self, url: str, snapshot: ServerSnapshot, *, generation: int) -> None:
        """Keep *snapshot* as the latest answer of *url*, unless it was forgotten since."""
        ...

    def capabilities(
        self, url: str, tag: str, *, timeout: float, cached_only: bool = False
    ) -> frozenset[str] | None:
        """Return what *tag* can do at *url*, or ``None`` when it cannot say.

        ``cached_only`` never contacts the server.
        """
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

    def is_local(self, url: str, *, include_wsl_host: bool = False) -> bool:
        """Return whether *url* points at this computer (or the Windows host behind WSL)."""
        ...

    def program_found(self) -> bool:
        """Return whether the Ollama program is installed on this computer."""
        ...

    def can_start(self) -> bool:
        """Return whether the app can start Ollama from here."""
        ...


class OllamaClientFactory[C](Protocol):
    """Builds the Ollama client *C* of one address.

    *cancel_event* makes a waiting request abandonable, *timeout* bounds one that must answer,
    *stats_sink* is told what every chat request cost.  A service that only passes the client on
    takes it as ``object``: only the code that drives it knows its shape.
    """

    def __call__(
        self,
        url: str,
        *,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
        stats_sink: Callable[[str, Any], None] | None = None,
    ) -> C: ...


class ProviderDeclarations(Protocol):
    """What the provider modules declare: which providers there are and what each one is."""

    def names(self) -> list[str]:
        """Return the ids of all providers, sorted."""
        ...

    def info(self, provider: str) -> ProviderInfo | None:
        """Return what *provider* declares, or ``None`` when the name is no provider."""
        ...


class OnlineProviders(ProviderDeclarations, Protocol):
    """The online AI services: what each declares, and asking one something.

    The counterpart of :class:`OllamaServer` for the vendors: ``LlmFactoryProviders`` implements
    it over the provider modules.  *api_key* is the key the app holds for that service (``""``
    when it holds none), so no method reads a key of its own.
    """

    def data_region(self, provider: str) -> str:
        """Return where *provider* processes data: ``"eu"``, ``"non_eu"`` or ``"unknown"``."""
        ...

    def list_models_checked(self, provider: str, *, api_key: str) -> ModelListing:
        """Return the models of *provider*, and whether the list is live or built in."""
        ...

    def check_connection(self, provider: str, *, api_key: str) -> tuple[bool, str]:
        """Return ``(connected, message)`` for *provider*: does it answer with that key?"""
        ...


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

    def system_usage(self) -> SystemUsage | None:
        """Return the processor load and main memory in use, or ``None`` when unreadable."""
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


class RunSession(RunActivity, Protocol):
    """What a comparison does to the run: it counts as one while it asks a model."""

    def running(self) -> AbstractContextManager[None]:
        """Count the caller as a run for as long as the block lasts."""
        ...


class CompareResults(Protocol):
    """The stored answers of the comparison: one per text and model, on disk.

    ``CompareStore`` implements it.  A comparison reads what an earlier one stored, so a model
    the teacher tries again costs no request; the analysed result is never logged and never
    leaves the cache the retention already empties.
    """

    def results(self, text_sha: str) -> dict[str, ComparedResult]:
        """Return every stored answer about the text *text_sha*, by the name of its file.

        A model that answered twice has two entries: ``key`` and ``key~2``.
        """
        ...

    def load(self, text_sha: str, key: str, repeat: int = 0) -> ComparedResult | None:
        """Return the stored answer of the settings *key* and its run *repeat* about the text."""
        ...

    def save(self, result: ComparedResult) -> None:
        """Write *result*, replacing a stored answer of the same settings, text and run.

        Raises:
            OSError: the file cannot be written; the caller shows the model as failed.
        """
        ...


class MarkedDocument(Protocol):
    """What finding task text reads of one stored document, and nothing more.

    ``domain.document_state.DocumentState`` satisfies it; this layer sees only the fields the
    detection needs, so the cache's own type stays in the cache.
    """

    @property
    def source_hash(self) -> str:
        """Return the content hash of the document: the key of the teacher's work on it."""

    @property
    def raw_text(self) -> str:
        """Return the transcription as the reader left it."""

    @property
    def raw_text_format(self) -> str:
        """Return the notation ``raw_text`` is written in."""

    @property
    def task_marks(self) -> tuple[str, ...]:
        """Return the passages the document is marked with."""


class TaskTextStore(Protocol):
    """The store of a queue's documents, as finding their printed text reads and writes it.

    ``infrastructure.state_repository.StateRepository`` implements it.
    """

    def load_valid(self, input_file: Path) -> MarkedDocument | None:
        """Return the stored document of *input_file*, and ``None`` when there is none."""

    def add_task_passages(self, input_file: Path, passages: Sequence[str]) -> bool:
        """Store *passages* with *input_file*; return whether any of them was new."""


class WorkLookup(Protocol):
    """The teacher's work, as finding task text reads it: their "not task" entries.

    ``infrastructure.teacher_work_store.TeacherWorkStore`` implements it.
    """

    def load(self, source_hash: str) -> TeacherWork | None:
        """Return the teacher's work on the document *source_hash*, or ``None`` without any."""


class OptionMarks(Protocol):
    """The teacher's verdicts on compared changes: one file per text, in the data root.

    ``OptionMarkStore`` implements it.  A mark is the teacher's work and can never be made again,
    so it does not go into the cache with the answers it judges.
    """

    def marks(self, text_sha: str) -> tuple[OptionMark, ...]:
        """Return the verdicts about the text *text_sha*: none when nothing is stored."""
        ...

    def save(self, text_sha: str, marks: tuple[OptionMark, ...]) -> None:
        """Write *marks* as the verdicts about the text *text_sha*; none deletes the file.

        Raises:
            OSError: the file cannot be written; the caller tells the teacher.
        """
        ...


class ProofreadAnswer(Protocol):
    """One text's answer, as a comparison reads it: the texts, the review and what went wrong.

    ``workflow/proofread_text.py``'s outcome satisfies it, and this layer sees only this much of
    it: what a comparison says about an answer, and nothing about how it was made.  Its members
    are read-only, because the outcome is a value: nothing below writes into an answer.
    """

    @property
    def outcome(self) -> CorrectionOutcome:
        """The corrector's answer as it came back: the failure flags are read here."""
        ...

    @property
    def text(self) -> str:
        """The answer, with the real names back."""
        ...

    @property
    def review(self) -> CorrectionReview | None:
        """The explanations, with the real names back, or ``None`` when they were dropped."""
        ...

    @property
    def stored_warning(self) -> str:
        """What a stored result keeps of the answer: its warning and names left hidden."""
        ...

    @property
    def names_hidden(self) -> int:
        """How many places were hidden before the request; ``0`` when nothing was sent hidden."""
        ...


class ProofreadingUnit(Protocol):
    """One text, proofread: hide its names, answer, put the names back."""

    def __call__(
        self,
        text: str,
        text_format: TextFormat = "plain",
        *,
        name: str = "",
        passages: Sequence[str] = (),
        not_task: Sequence[str] = (),
        stop_requested: Callable[[], bool] | None = None,
    ) -> ProofreadAnswer:
        """Proofread *text* and return its answer with the names restored.

        A passage of *passages* is printed text, not the student's writing: it is not sent and
        comes back as it stands.  A line of *not_task* is proofread although a passage covers it.
        """
        ...


class ProofreadUnitBuilder(Protocol):
    """Builds the unit that proofreads one text with one model: a run's, or a comparison's."""

    def __call__(
        self, config: Config, *, reporter: Reporter, stop_event: threading.Event | None = None
    ) -> ProofreadingUnit:
        """Return the unit of *config*, writing its narrative and its warnings to *reporter*.

        Setting *stop_event* abandons the request that is waiting, which raises
        :exc:`~teachers_teammate.exceptions.RunStoppedError`.
        """
        ...


class ModelAdvice(Protocol):
    """What this computer makes of a model: the profile it is rated against, and the rating.

    ``ModelRecommendationService`` implements it.  A comparison reads the ratings it already
    worked out on the GUI thread, and classifies the models it lists only off that thread.
    """

    def cached_hardware_profile(
        self, setting: HardwareProfileSetting, ollama_url: str
    ) -> HardwareDetection | None:
        """Resolve *setting* without probing this computer, or ``None`` while it is unknown."""
        ...

    def classify_ollama_models(
        self, tags: Sequence[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        """Return what each Ollama model tag may be used for, ``None`` when it is unknown."""
        ...

    def rate_for(
        self,
        tags: Sequence[str],
        task: ModelCategory,
        detection: HardwareDetection | None,
        setting: HardwareProfileSetting,
        ollama_url: str,
        *,
        cached_only: bool = False,
    ) -> list[ModelRating]:
        """Return a :class:`~teachers_teammate.domain.model_fit.ModelRating` per tag."""
        ...


class PageTimes(Protocol):
    """What this computer measured on a server: the seconds a page takes with a model.

    ``SpeedEstimateService`` implements it.  A comparison asks it for the models it lists, so a
    row says how long a text will take before the teacher starts it.
    """

    def page_times(
        self,
        tags: Sequence[str],
        category: ModelCategory,
        ollama_url: str,
        *,
        hardware: HardwareDetection | None = None,
        words: int = ...,
    ) -> dict[str, TimeRange]:
        """Return the measured seconds of *words* words of *tags*, keyed by tag.

        A model that was never measured is left out.
        """
        ...


class ProviderCatalogue(Protocol):
    """The models to choose from: what is installed or listed, and where each one runs.

    ``ProviderService`` implements it.  ``get_cached_models`` is the list the app already holds,
    which a window reads; ``list_provider_models`` may ask the provider, off the GUI thread.
    """

    def list_providers(self) -> list[str]:
        """Return the available LLM providers."""
        ...

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        """Return the models of *provider*, asking for them when the held list has expired."""
        ...

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        """Return the model list of *provider* the app holds, or ``None`` when it holds none."""
        ...

    def provider_key_missing(self, provider: str) -> bool:
        """Return whether *provider* needs an API key the app has none of."""
        ...

    def missing_provider_package(self, provider: str) -> str | None:
        """Return the package *provider* needs but the app cannot import, else ``None``."""
        ...

    def describe_llm_location(self, provider: str, model: str, ollama_url: str) -> StageLocation:
        """Return where proofreading with *provider* and *model* runs."""
        ...
