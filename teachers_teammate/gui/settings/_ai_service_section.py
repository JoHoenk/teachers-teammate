"""Which AI service and model one AI step uses: proofreading or content review.

Both steps ask the same thing -- which service, which model -- and differ only in the
settings keys.  :class:`AiServiceSpec` holds those keys, so a fix (such as asking the service
again after the provider changed) exists once.

The instructions (prompt, answer formats, explanation options) are not here: they describe
*how a task behaves* and live in the main window's instructions dialog.  There is no
temperature to set: the app sends the value each model's maker recommends.

The service list shows every service, grouped by where it runs, and marks those that still
need a key or a package; an online service shows its API key row right below it.  The model
list that loads once the key is committed is the check that it works.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import html
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QWidget

from ...config import DEFAULTS
from ...domain.data_location import DataLocation, DataRegion
from ...domain.model_listing import ListedModel, ModelListing
from .._data_flow_summary import LocationHint, text_note
from .._hardware_profile_selector import HardwareProfileSelector
from .._model_fetch import (
    _MODEL_GROUPS,
    _STATUS_ERROR,
    _STATUS_KEY_MISSING,
    _STATUS_LOADING,
    _USE_PREFIX,
    _BusyIndicator,
    _listing_status,
    _ModelFetchThread,
    _retire_thread,
    _SettingsAppService,
    _status_busy,
    _status_note,
)
from .._model_guidance import OllamaModelGuidance, OpenDownloads
from .._model_item_delegate import ModelItem, fill_grouped, install
from .._model_wording import (
    SAME_MODEL_TIP,
    USE_SUGGESTION,
    asking_for_models_text,
    checking_models_text,
    retiring_model_text,
    saved_model_not_offered_text,
)
from .._package_hint import PackageHint
from ._credentials import ApiKeyDrafts, ApiKeyRow


@dataclass(frozen=True)
class AiServiceSpec:
    """The settings keys of one AI step, and those of the other step (for the same-model tip)."""

    provider_key: str
    model_key: str
    other_provider_key: str
    other_model_key: str


PROOFREADING = AiServiceSpec(
    provider_key="correction_provider",
    model_key="correction_model",
    other_provider_key="evaluate_provider",
    other_model_key="evaluate_model",
)

CONTENT_REVIEW = AiServiceSpec(
    provider_key="evaluate_provider",
    model_key="evaluate_model",
    other_provider_key="correction_provider",
    other_model_key="correction_model",
)


#: Where a self-hosted service runs, as the service list says it.
_PLACE_OF_SELF_HOSTED = {
    DataLocation.THIS_COMPUTER: "this computer",
    DataLocation.OTHER_COMPUTER: "own server",
    DataLocation.ONLINE: "online",
}
MARK_NOT_INSTALLED = "not installed"
MARK_KEY_NEEDED = "key needed"


def missing_packages(app_service: _SettingsAppService) -> dict[str, str | None]:
    """Return ``{provider: pip name of its missing package, or None}`` for every service.

    Looking for a package refreshes the import caches and scans the import path, so this is
    asked when a package may have changed, not on every key that is typed.
    """
    return {
        provider: app_service.missing_provider_package(provider)
        for provider in app_service.list_providers()
    }


def service_groups(
    app_service: _SettingsAppService,
    ollama_url: str,
    packages: Mapping[str, str | None] | None = None,
) -> list[list[ModelItem]]:
    """Return every AI service, grouped by where it runs, each labelled with what is missing.

    Order: services on this computer or the school's own server, then online services in the
    EU, then every other online service ("outside the EU" also for a service that does not say
    where it processes data: a school can only rely on what is declared).  Inside a group the
    services are sorted by name.  A service that needs a package or a key says so after its
    place, the package first because installing comes first.  *packages* is the answer of
    :func:`missing_packages`; without it the packages are looked for now.

    The place and region come from ``describe_llm_location`` -- the call the location hint
    under the list uses -- so the label and the hint never disagree.
    """
    if packages is None:
        packages = missing_packages(app_service)
    own: list[ModelItem] = []
    eu: list[ModelItem] = []
    other: list[ModelItem] = []
    for provider in app_service.list_providers():
        info = app_service.get_provider_info(provider)
        name = str(info.get("display_name") or provider)
        location = app_service.describe_llm_location(provider, "", ollama_url)
        if info.get("self_hosted"):
            bucket, place = own, _PLACE_OF_SELF_HOSTED[location.location]
        elif location.data_region is DataRegion.EU:
            bucket, place = eu, DataRegion.EU.label
        else:
            bucket, place = other, DataRegion.NON_EU.label
        marks = [place]
        if packages.get(provider):
            marks.append(MARK_NOT_INSTALLED)
        if app_service.provider_key_missing(provider):
            marks.append(MARK_KEY_NEEDED)
        bucket.append(ModelItem(text=name, label=" · ".join(marks), data=provider))
    return [sorted(group, key=lambda item: item.text.lower()) for group in (own, eu, other)]


class AiServiceSection(QWidget):
    """AI service + model of one step, with the hints that explain both.

    Emits :attr:`choice_changed` whenever the service or the model changes, so the page can
    hand the choice to the other section (the same-model tip compares both live).

    A section that was never :meth:`load`-ed (the content review while it shares the
    proofreading model) asks no service for anything.
    """

    #: ``(provider, model)`` of this section changed.
    choice_changed = Signal(str, str)

    def __init__(  # noqa: PLR0913  # independent keyword-only options, like OcrModelPicker's
        self,
        spec: AiServiceSpec,
        *,
        app_service: _SettingsAppService,
        hardware_profile: str = "auto",
        ollama_url: str = DEFAULTS["ollama_url"],
        open_downloads: OpenDownloads | None = None,
        key_drafts: ApiKeyDrafts | None = None,
        profile_selector: HardwareProfileSelector | None = None,
        same_model_tip: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """Build the section.

        *profile_selector* is the page's own "Suggestions are for …" selector: with it the
        section follows that one (the page keeps it up to date) instead of resolving this
        computer again for itself, and shows no line of its own.
        """
        super().__init__(parent)
        self._spec = spec
        self._drafts = key_drafts or ApiKeyDrafts(app_service, parent=self)
        self._show_same_model_tip = same_model_tip
        self._loaded = False
        self._app_service = app_service
        self._fetch_thread: _ModelFetchThread | None = None
        #: The model the teacher saved for this step, and the provider it was saved for, so a
        #: list that no longer offers it can say so instead of silently dropping it — and a
        #: later list of another provider cannot mistake a name carried over for it.
        self._saved_model = ""
        self._saved_model_provider = ""
        #: The list the field was last filled from, so a status link can restore its status line.
        self._shown_listing: ModelListing | None = None
        self._ollama_url = ollama_url or DEFAULTS["ollama_url"]
        self._open_downloads = open_downloads
        self._shared_selector = profile_selector
        #: Which services miss their package (asked again only when a package may have changed).
        self._missing_packages: dict[str, str | None] = {}
        #: The other step's current choice, for the same-model tip.
        self._other_provider = ""
        self._other_model = ""
        self._build(hardware_profile)

    # ── construction ──────────────────────────────────────────────────────────

    def _build(self, hardware_profile: str) -> None:
        form = QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        if self._shared_selector is not None:
            self._hw_selector = self._shared_selector
        else:
            self._hw_selector = HardwareProfileSelector(
                app_service=self._app_service,
                setting=hardware_profile,
                ollama_url=self._ollama_url,
                read_only=True,
                parent=self,
            )
            form.addRow(self._hw_selector)

        self._provider = QComboBox()
        install(self._provider)
        self._missing_packages = missing_packages(self._app_service)
        fill_grouped(
            self._provider,
            service_groups(self._app_service, self._ollama_url, self._missing_packages),
        )
        self._provider.currentIndexChanged.connect(self._on_provider_selected)
        form.addRow("AI service:", self._provider)

        self._where_hint = LocationHint()
        form.addRow("", self._where_hint)

        self._package_hint = PackageHint(self._open_downloads)
        form.addRow("", self._package_hint)

        # One row for whichever service is chosen; the key is entered right where the
        # service is picked.  Shown only for a service that needs a key.
        self._key_row = ApiKeyRow(self._drafts, "", "", inline=True)
        self._key_row.setVisible(False)
        self._key_row.remove_requested.connect(self._on_key_remove_requested)
        form.addRow("", self._key_row)
        self._drafts.changed.connect(self._on_key_changed)

        self._model = QComboBox()
        self._model.setEditable(True)
        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.addWidget(self._model, stretch=1)
        # Says a fetch is running, so a list that stays put for a moment is not a hang.
        self._busy = _BusyIndicator()
        model_layout.addWidget(self._busy)
        form.addRow("AI model:", model_row)

        self._status_lbl = QLabel(_STATUS_LOADING)
        self._status_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.linkActivated.connect(self._on_status_link)
        form.addRow("", self._status_lbl)

        self._guidance = OllamaModelGuidance(
            combo=self._model,
            app_service=self._app_service,
            category="general",
            profile_selector=self._hw_selector,
            ollama_url=self._ollama_url,
            open_downloads=self._open_downloads,
            parent=self,
        )
        form.addRow("", self._guidance)

        self._same_model_tip_lbl = QLabel(SAME_MODEL_TIP)
        self._same_model_tip_lbl.setWordWrap(True)
        self._same_model_tip_lbl.setStyleSheet("color: #888; font-size: 11px;")
        self._same_model_tip_lbl.setVisible(False)
        form.addRow("", self._same_model_tip_lbl)

        self._model.currentTextChanged.connect(lambda _txt: self._update_same_model_tip())
        self._model.currentTextChanged.connect(lambda _txt: self._update_where_hint())
        self._model.currentTextChanged.connect(lambda _txt: self._emit_choice())
        self._hw_selector.resolved_changed.connect(lambda _d: self._update_same_model_tip())

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        """Apply the saved provider and model, then ask the service for models.

        The provider is set without its change handler: the handler also starts a fetch,
        and here exactly one is wanted, after the saved model is in place.
        """
        spec = self._spec
        self._loaded = True
        self._other_provider = str(values.get(spec.other_provider_key, ""))
        self._other_model = str(values.get(spec.other_model_key, ""))
        saved_provider = str(values.get(spec.provider_key, ""))
        provider_found = spec.provider_key not in values
        self._refresh_service_labels(rescan_packages=True)
        if spec.provider_key in values:
            self._provider.blockSignals(True)
            # Every service is listed, so the saved one is found whether or not its key is set
            # (its status says the key is missing).  One that is not listed at all (its
            # module was removed) leaves the first entry shown and its model is not carried
            # over: that name belongs to a service that is no longer there.
            index = self._provider.findData(saved_provider)
            if index >= 0:
                self._provider.setCurrentIndex(index)
            provider_found = self.provider() == saved_provider
            self._provider.blockSignals(False)
        self._saved_model = str(values.get(spec.model_key, "") or "").strip()
        self._saved_model_provider = self.provider()
        self._on_provider_changed(self.provider())
        if spec.model_key in values and provider_found:
            self._model.setCurrentText(str(values[spec.model_key]))
        self._auto_fetch()
        self._update_same_model_tip()

    def values(self) -> dict[str, Any]:
        """Return this step's provider and model."""
        return {
            self._spec.provider_key: self.provider(),
            self._spec.model_key: self._model.currentText().strip(),
        }

    def provider(self) -> str:
        """Return the id of the chosen AI service (the list shows display names)."""
        return str(self._provider.currentData() or "")

    def choice(self) -> tuple[str, str]:
        """Return ``(provider, model)`` as currently shown."""
        return self.provider(), self._model.currentText().strip()

    def key_row(self) -> ApiKeyRow:
        """Return the API key row under the service (hidden for a service without a key)."""
        return self._key_row

    def commit_key(self) -> None:
        """Hand a key typed in this section's row to the drafts now (it may still be waiting)."""
        if self._key_row.env_key:
            self._key_row.commit()

    def set_other_choice(self, provider: str, model: str) -> None:
        """Follow the other step's choice live (it decides the same-model tip)."""
        self._other_provider = provider
        self._other_model = model
        self._update_same_model_tip()

    def set_ollama_url(self, url: str) -> None:
        """Use another local AI server: re-resolve, re-hint, and ask it for its models."""
        new_url = url or DEFAULTS["ollama_url"]
        if new_url == self._ollama_url:
            return
        self._ollama_url = new_url
        if self._shared_selector is None:
            self._hw_selector.set_ollama_url(new_url)
        self._guidance.set_ollama_url(new_url)
        self._refresh_service_labels()
        self._update_where_hint()
        if self.provider() == "ollama" and self._loaded:
            self._refetch_models()

    def on_packages_changed(self) -> None:
        """An install finished: look for the package again, and ask the service."""
        self._refresh_service_labels(rescan_packages=True)
        self._update_package_hint(self.provider())
        if self._loaded:
            self._refetch_models()

    def shutdown(self) -> None:
        """Detach a running model fetch (it cannot be interrupted, so it is parked).

        The section counts as not loaded afterwards: it asks nothing until it is loaded again.
        """
        self._loaded = False
        self._key_row.stop()
        _retire_thread(self._fetch_thread)

    # ── provider change ───────────────────────────────────────────────────────

    def _emit_choice(self) -> None:
        self.choice_changed.emit(self.provider(), self._model.currentText().strip())

    def _refresh_service_labels(self, *, rescan_packages: bool = False) -> None:
        """Rebuild the service list so its marks are true again (a key, a package, the server).

        The packages are looked for again only when asked to: a key or a server address does
        not install anything, and the look scans the import path.
        """
        if rescan_packages:
            self._missing_packages = missing_packages(self._app_service)
        current = self.provider()
        self._provider.blockSignals(True)
        fill_grouped(
            self._provider,
            service_groups(self._app_service, self._ollama_url, self._missing_packages),
        )
        index = self._provider.findData(current)
        if index >= 0:
            self._provider.setCurrentIndex(index)
        self._provider.blockSignals(False)

    def _on_provider_selected(self, _index: int) -> None:
        """The user picked another AI service: show its models, then ask it for the live list."""
        provider = self.provider()
        self._on_provider_changed(provider)
        self._auto_fetch()
        self._emit_choice()

    def _on_provider_changed(self, provider: str) -> None:
        """Show *provider*'s models and clear the field to the one model to keep.

        A service that can be asked for its list fills the field with the live answer a moment
        later; until then the field holds only the model to keep, because its built-in names are
        not installed or offered and would read like the answer.  A model name from the previous
        service is kept only if this one offers it: an Ollama tag in the Mistral field would be
        sent to Mistral as it stands.
        """
        info = self._app_service.get_provider_info(provider)
        previous = self._model.currentText().strip()
        models = list(info.get("models", []))
        default = str(info.get("default_model", ""))
        keep = previous if previous and previous in models else default
        self._model.clear()
        if not self._will_be_asked(provider, info):
            self._model.addItems(models)
        if keep and self._model.findText(keep) < 0:
            # A kept model that is already in the built-in list must not be added twice.
            self._model.addItem(keep)
        self._model.setCurrentText(keep)
        if provider == "ollama":
            # The local list changes whenever a model is pulled or removed, and is cheap to
            # ask for.  An online service's list fetched a moment ago is still good (a failed
            # one is never cached), so it is kept; "Try again" drops it on request.
            self._app_service.invalidate_model_cache(provider, self._ollama_url)
        # Hint line and "Use best choice" button exist only for Ollama.
        self._guidance.setVisible(provider == "ollama")
        self._update_package_hint(provider)
        self._update_key_row(provider)
        self._update_where_hint()

    def _will_be_asked(self, provider: str, info: dict) -> bool:
        """Return whether *provider* will be asked for its model list right now."""
        return bool(info.get("can_list_models")) and not self._app_service.provider_key_missing(
            provider
        )

    def _update_package_hint(self, provider: str) -> None:
        """Show the install hint when *provider*'s package is missing, else hide it."""
        pip_name = self._app_service.missing_provider_package(provider)
        if not pip_name:
            self._package_hint.clear()
            return
        name = self._app_service.get_provider_info(provider).get("display_name", provider)
        self._package_hint.show_missing(provider, str(name), pip_name)

    def _update_key_row(self, provider: str) -> None:
        """Show the key row of *provider*, or hide it when the service needs no key."""
        env_key = self._drafts.env_key_of(provider)
        if not env_key:
            self._key_row.retarget("", "")
            self._key_row.setVisible(False)
            return
        self._key_row.retarget(
            env_key, self._drafts.titles().get(env_key, env_key), self._drafts.key_url_of(env_key)
        )
        self._key_row.setVisible(True)

    def _on_key_remove_requested(self, _row: object) -> None:
        self._drafts.remove(self._key_row.env_key)

    def _on_key_changed(self, env_key: str) -> None:
        """A key was entered or removed (here or on This computer): check it, relabel.

        Every service that reads this key forgets its cached model list, the chosen one or
        not: a list fetched with the old key says nothing about the new one (Mistral and
        Mistral EU share a key).
        """
        for provider in self._drafts.providers_of(env_key):
            self._app_service.invalidate_model_cache(
                provider, self._ollama_url if provider == "ollama" else ""
            )
        if env_key == self._key_row.env_key:
            self._key_row.show_value(self._drafts.value(env_key))
            self._key_row.refresh_line()
        if not self._loaded:
            return  # load() draws the list when the section is first shown
        self._refresh_service_labels()
        if env_key == self._key_row.env_key:
            self._auto_fetch()

    def _update_where_hint(self) -> None:
        """Say where the chosen AI service runs, so online use is never a surprise."""
        location = self._app_service.describe_llm_location(
            self.provider(),
            self._model.currentText().strip(),
            self._ollama_url,
        )
        self._where_hint.set_location(location, text_note(location))

    def _update_same_model_tip(self) -> None:
        """Show the same-model tip for cpu/8gb profiles with differing Ollama models."""
        detection = self._hw_selector.resolved()
        profile = detection.profile if detection is not None else None
        show = (
            self._show_same_model_tip
            and profile in ("cpu", "8gb")
            and self.provider() == "ollama"
            and self._other_provider == "ollama"
            and self._model.currentText().strip() != self._other_model
        )
        self._same_model_tip_lbl.setVisible(show)

    # ── model list ────────────────────────────────────────────────────────────

    def _refetch_models(self) -> None:
        """Invalidate the model cache of the current service and fetch the list again."""
        provider = self.provider()
        self._app_service.invalidate_model_cache(
            provider, self._ollama_url if provider == "ollama" else ""
        )
        self._auto_fetch()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        """Show or hide the busy bar; while a list is on its way, no stale hints.

        *text* names what is happening and is shown in the status line, so the wait has a
        reason ("Asking Ollama which models are installed…") instead of a bare spinner.
        """
        self._busy.setVisible(busy)
        if not busy:
            return
        self._status_lbl.setText(_status_busy(text))
        self._guidance.setVisible(False)

    def _service_name(self, provider: str) -> str:
        """Return the name of *provider* to show the user (never its key)."""
        name = self._app_service.get_provider_info(provider).get("display_name", "")
        return str(name or provider)

    def _is_saved_model(self, model: str) -> bool:
        """Return whether *model* is the choice saved for the provider currently shown."""
        return (
            bool(model)
            and model == self._saved_model
            and self.provider() == self._saved_model_provider
        )

    def _auto_fetch(self) -> None:
        # A fetch still running belongs to a service that may no longer be the chosen one.
        _retire_thread(self._fetch_thread)
        provider = self.provider()
        service = self._service_name(provider)
        if self._app_service.provider_key_missing(provider):
            self._set_busy(False)
            self._status_lbl.setText(_STATUS_KEY_MISSING)
            return
        url = self._ollama_url if provider == "ollama" else ""
        # Ollama always goes through the thread (a cache hit there is instant):
        # classifying custom models must not run on the GUI thread.
        cached = (
            None
            if provider == "ollama"
            else self._app_service.get_cached_listing(provider, base_url=url)
        )
        if cached is not None:
            self._show_listing(cached)
            return
        self._set_busy(True, asking_for_models_text(service))
        if provider == "ollama":

            def fetch(report: Callable[[str], None]) -> ModelListing:
                tags = self._app_service.list_provider_models(provider, base_url=url)
                # Classify here so set_models() only needs cache lookups.  It costs one
                # request per model, so say how many there are before it starts.
                report(checking_models_text(len(tags)))
                self._app_service.classify_ollama_models(tags, url)
                return ModelListing(tags)

        else:

            def fetch(report: Callable[[str], None]) -> ModelListing:
                _ = report  # a single request; nothing to report between start and answer
                return self._app_service.list_provider_models_checked(provider, base_url=url)

        self._fetch_thread = _ModelFetchThread(fetch, tag=provider)
        self._fetch_thread.listing_ready.connect(self._on_listing_ready)
        self._fetch_thread.progress.connect(self._on_fetch_progress)
        self._fetch_thread.start()

    def _on_fetch_progress(self, provider: str, text: str) -> None:
        """Say what the running fetch is doing, unless the user has left that service."""
        if provider != self.provider():
            return
        self._set_busy(True, text)

    def _on_listing_ready(self, provider: str, listing: object) -> None:
        """Show a fetched list, unless it is for a service the user has already left."""
        if provider != self.provider() or not isinstance(listing, ModelListing):
            return
        self._show_listing(listing)

    def _show_listing(self, listing: ModelListing) -> None:
        """Fill the model field and the status line from *listing*."""
        self._set_busy(False)
        self._shown_listing = listing
        provider = self.provider()
        service = self._service_name(provider)
        models = listing.models
        if not models:
            # A failed answer says why (and offers a retry); only a silent empty one is a dash.
            self._status_lbl.setText(
                _listing_status(listing, service) if listing.source == "failed" else _STATUS_ERROR
            )
            if provider == "ollama":
                # Waiting hid the hint; with Ollama not answering it still has something to say
                # (that a tag is not installed, and the button to download the best choice).
                self._guidance.setVisible(True)
                self._guidance.set_models([])
                self._update_same_model_tip()
            return
        self._status_lbl.setText(_listing_status(listing, service))
        if provider == "ollama":
            self._guidance.setVisible(True)
            self._guidance.set_models(models)
            self._update_same_model_tip()  # set_models() blocks the combo's signals
            self._emit_choice()
            return
        previous = self._model.currentText()
        if listing.details:
            self._show_curated(listing, service, previous)
            return
        self._model.clear()
        self._model.addItems(models)
        idx = self._model.findText(previous)
        if idx >= 0:
            self._model.setCurrentIndex(idx)
            return
        # A saved model the service no longer offers stays in the field (the teacher may have
        # good reasons), but is called out: silently swapping it would be worse.
        self._model.setCurrentText(previous)
        if previous and self._is_saved_model(previous) and listing.source == "live":
            self._status_lbl.setText(
                f"{_listing_status(listing, service)} "
                f"{_status_note(saved_model_not_offered_text(previous, service))}"
            )

    def _show_curated(self, listing: ModelListing, service: str, previous: str) -> None:
        """Fill the model field from a curated list: labelled groups, kept selection.

        The names stay the raw model IDs, so what the settings keep is unchanged; the labels
        only say what an entry is.  A model the service is about to retire is never swapped
        silently: it stays chosen, and the note offers the suggested one instead.
        """
        groups: list[list[ModelItem]] = [
            [
                ModelItem(text=model.id, label=model.label, tooltip=model.description)
                for model in listing.details
                if model.group == group
            ]
            for group in _MODEL_GROUPS
        ]
        # Clearing a combo tells its change handlers about an empty name; nothing to say yet.
        self._model.blockSignals(True)
        fill_grouped(self._model, groups)
        self._model.blockSignals(False)
        if previous:
            idx = self._model.findText(previous)
            if idx >= 0:
                self._model.setCurrentIndex(idx)
            else:
                self._model.setCurrentText(previous)
        # With no model chosen the field moved to the first entry without telling anyone.
        self._update_where_hint()
        self._update_same_model_tip()
        self._emit_choice()
        retiring = next((m for m in listing.retiring if m.id == previous), None)
        if retiring is not None:
            self._status_lbl.setText(
                f"{_listing_status(listing, service)} {self._retiring_note(retiring, service)}"
            )
        elif (
            previous
            and previous not in listing.also_served
            and self._is_saved_model(previous)
            and listing.source == "live"
        ):
            self._status_lbl.setText(
                f"{_listing_status(listing, service)} "
                f"{_status_note(saved_model_not_offered_text(previous, service))}"
            )

    def _retiring_note(self, model: ListedModel, service: str) -> str:
        """Return the orange note for a chosen model that is about to leave *service*."""
        text = retiring_model_text(model.id, model.retires_on, service, model.replacement)
        note = _status_note(text)
        if not model.replacement:
            return note
        href = html.escape(f"{_USE_PREFIX}{model.replacement}", quote=True)
        link = f'<a href="{href}">{html.escape(USE_SUGGESTION)}</a>'
        return f"{note} {link}"

    def _on_status_link(self, link: str) -> None:
        """Follow a link in the status line: take the suggested model, or ask the service again."""
        if not link.startswith(_USE_PREFIX):
            self._refetch_models()
            return
        self._model.setCurrentText(link[len(_USE_PREFIX) :])
        # The warning was about the model just replaced, so back to the plain count.
        if self._shown_listing is not None:
            self._status_lbl.setText(
                _listing_status(self._shown_listing, self._service_name(self.provider()))
            )
