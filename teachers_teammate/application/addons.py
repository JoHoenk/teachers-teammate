"""Add-ons: what of them is installed, and the installs themselves.

One place answers "which optional packages does this process find, which ones wait for the next
start, and what does installing one do".  The installs are slow and reach the network, so they
run in a background thread and only after the teacher asked for them.
"""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
from pathlib import Path

from ..domain.local_models import PAGE_MODELS, READER_MODELS
from ..domain.model_catalog import normalize_tag
from ..infrastructure.onnx.store import ModelStore
from .anonymization import AnonymizationService
from .ports import AddonState
from .providers import ProviderService, importable
from .reader import ReaderService

#: Keys of :meth:`AddonService.get_installation_status` for the two model sets the app can carry
#: itself; the Add-ons page names its rows (and its install items) the same way.
READER_STATUS_KEY = "reader:ppocr"
PAGE_MODELS_STATUS_KEY = "page_models:all"
#: In front of a status key: whether the bundle itself carries that item (``bundled:reader:ppocr``).
BUNDLED_PREFIX = "bundled:"


def bundled_status_key(key: str) -> str:
    """Return the status key that says whether the app carries *key*'s files itself."""
    return f"{BUNDLED_PREFIX}{key}"


class AddonService:
    """The optional packages: what is installed, and what installing one does."""

    def __init__(
        self,
        *,
        model_store: ModelStore | None = None,
        providers: ProviderService,
        reader: ReaderService,
        anonymization: AnonymizationService,
        state: AddonState,
    ) -> None:
        self._model_store = model_store or ModelStore()
        # What the packages folder holds: the installs below are this service's own, the
        # questions about them come from there.
        self._state = state
        self._providers = providers
        self._reader = reader
        self._anonymization = anonymization

    # ── What is installed ─────────────────────────────────────────────────

    def get_installation_status(
        self,
        *,
        provider_modules: dict[str, str] | None = None,
        spacy_models: list[str] | None = None,
        ollama_models: list[str] | None = None,
        ollama_url: str = "",
        langchain_packages: dict[str, str] | None = None,
    ) -> dict[str, bool]:
        """Probe availability of optional components and return a status map.

        Keys produced (all optional — only those whose *inputs* are supplied):
        - ``"reader:ppocr"`` — always checked (the built-in reader's model files)
        - ``"page_models:all"`` — always checked (the page models of the automatic preparation)
        - ``"bundled:reader:ppocr"``, ``"bundled:page_models:all"`` — always checked: whether
          the app carries those files itself, so the Add-ons rows say *included* not *downloaded*
        - ``"provider:{name}"`` — for each entry in *provider_modules*
        - ``"spacy:{model_id}"`` — for each id in *spacy_models*
        - ``"ollama:{model}"`` — for each model in *ollama_models*
        - ``"langchain:{key}"`` — for each entry in *langchain_packages*
        """
        result: dict[str, bool] = {}
        result[READER_STATUS_KEY] = self._reader.reader_installed()
        result[PAGE_MODELS_STATUS_KEY] = self._reader.page_models_installed()
        result[bundled_status_key(READER_STATUS_KEY)] = self._model_store.is_bundled(READER_MODELS)
        result[bundled_status_key(PAGE_MODELS_STATUS_KEY)] = self._model_store.is_bundled(
            PAGE_MODELS
        )

        if provider_modules:
            for name, module_name in provider_modules.items():
                result[f"provider:{name}"] = importlib.util.find_spec(module_name) is not None

        if spacy_models:
            installed_spacy = set(self._anonymization.list_installed_spacy_models())
            for model_id in spacy_models:
                result[f"spacy:{model_id}"] = model_id in installed_spacy

        if ollama_models:
            try:
                # Compare normalised tags: "llama3.2" and "llama3.2:latest" are one model.
                pulled = {
                    normalize_tag(name)
                    for name in self._providers.list_provider_models("ollama", base_url=ollama_url)
                }
            except Exception:  # noqa: BLE001
                # Ollama may be unreachable; treat as no models pulled
                pulled = set()
            for model in ollama_models:
                result[f"ollama:{model}"] = normalize_tag(model) in pulled

        if langchain_packages:
            for key, module_name in langchain_packages.items():
                result[f"langchain:{key}"] = importlib.util.find_spec(module_name) is not None

        return result

    def is_addon_available(self, addon: str) -> bool:
        """Return whether the named addon package is importable."""
        return self._state.importable(addon)

    def is_module_importable(self, module: str) -> bool:
        """Return True if *module* can be imported (the package is installed).

        The import caches are refreshed first: a package installed while the app runs
        (Add-ons) is otherwise reported missing until the next start.
        """
        return importable(module)

    def addon_license(self, package: str) -> str | None:
        """Return the license of an add-on's package, or ``None`` when the list does not know it."""
        from ..infrastructure.addon_licenses import addon_license  # noqa: PLC0415

        return addon_license(package)

    def addons_restart_needed(self) -> bool:
        """Return whether an install waits for the next start (``packages.pending/``).

        Windows locks a file the running app has loaded, so a package that could not replace
        its old copy waits there; :func:`prepare_packages_dir` applies it at the next start.
        """
        return self._state.restart_needed()

    def pending_addon_packages(self) -> list[str]:
        """Return the normalised names of the packages that wait for the next start."""
        return self._state.pending()

    def addons_outdated(self) -> bool:
        """Return whether an app update left add-ons that wait for *Update add-ons*.

        They stay off (the app runs on its own bundle) until :meth:`update_addons` ran.  Reads the
        folder names and one small ``WHEEL`` file per package; ``False`` outside the installed
        app.
        """
        return self._state.outdated()

    def addon_in_folder(self, distribution: str) -> bool:
        """Return whether *distribution* is installed as an add-on in the packages folder."""
        return self._state.in_folder(distribution)

    def addon_waits_for_restart(self, distribution: str) -> bool:
        """Return whether *distribution* is installed but waits for the next start."""
        return self._state.waits_for_restart(distribution)

    # ── Installing ────────────────────────────────────────────────────────

    def addon_packages_dir(self) -> Path:
        """Return the persistent user-writable directory for addon packages."""
        return self._state.packages_dir()

    def install_addon(
        self,
        addon: str,
        target: Path,
        progress_cb: Callable[[str], None],
    ) -> None:
        """Install *addon* packages into *target* using the bundled pip."""
        from ..infrastructure.addon_manager import install_addon_subprocess  # noqa: PLC0415

        install_addon_subprocess(addon, target, progress_cb)

    def install_packages(
        self,
        packages: list[str],
        progress_cb: Callable[[str], None],
    ) -> None:
        """Install arbitrary pip *packages* into the addon packages directory."""
        from ..infrastructure.addon_manager import install_packages_subprocess  # noqa: PLC0415

        install_packages_subprocess(packages, self._state.packages_dir(), progress_cb)

    def update_addons(self, progress_cb: Callable[[str], None]) -> None:
        """Install the add-ons in the packages directory again with the app's package versions.

        Slow (downloads): call it from a background thread, and only after the teacher asked.

        Raises:
            OSError: if the packages directory cannot be written.
            RuntimeError: if pip fails for an add-on, or this is not the installed app.
        """
        from ..infrastructure.addon_manager import update_installed_addons  # noqa: PLC0415

        update_installed_addons(progress_cb)

    def clear_stale_addon_modules(self, addon: str) -> None:
        """Evict cached sys.modules entries left over from a failed addon import."""
        from ..infrastructure.addon_manager import clear_stale_modules  # noqa: PLC0415

        clear_stale_modules(addon)
