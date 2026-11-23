"""The *Online AI services* tab: one box per pip package an LLM provider needs.

The list comes from :meth:`ProvidersService.list_provider_packages`; a provider that shares a
package with another one (Mistral and Mistral (EU)) is listed once, under the first of them, and
:meth:`ProvidersTab.key_for` maps the other one onto that box.  Installing a package is only
ticking its box here: the round that does it is the page's.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from ....application.composition import AppServices
from ....domain.install_item import InstallItem, InstallKind
from ....texts import addons as txt
from ....texts.settings import license_tooltip
from ._common import ScrolledTab, apply_installed, tick_enabled


class ProvidersTab(ScrolledTab):
    """One box per provider package, with its licence one hover away."""

    def __init__(self, app_service: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._svc = app_service
        self._packages = app_service.providers.list_provider_packages()
        self._pending: list[InstallItem] = []

        group = QGroupBox(txt.PROVIDERS_GROUP)
        group_layout = QVBoxLayout(group)
        for prov, (_, pip_pkg) in self._packages.items():
            services = self._svc.providers.package_services(pip_pkg)
            connector = self._svc.providers.package_is_connector(prov)
            label = txt.provider_package_label(services, pip_pkg, connector=connector)
            cb = self.item_box(self.key_for(prov), label)
            cb.setToolTip(license_tooltip(self._svc.addons.addon_license(pip_pkg)))
            group_layout.addWidget(cb)
        self.body_layout.addWidget(group)
        self.body_layout.addStretch(1)

    def key_for(self, provider: str) -> InstallItem:
        """Return the checkbox item of the package *provider* needs.

        Providers that share a package (Mistral and Mistral EU) have one checkbox, listed
        under the first of them, so a request for the other one is mapped onto it.
        """
        if provider not in self._packages:
            info = self._svc.providers.get_provider_info(provider)
            pip = info.package.pip if info is not None and info.package is not None else None
            for name, (_module, listed_pip) in self._packages.items():
                if pip is not None and listed_pip == pip:
                    provider = name
                    break
        return InstallItem(InstallKind.PROVIDER, provider)

    def pip_packages(self) -> list[str]:
        """Return the pip packages whose box is ticked."""
        packages: list[str] = []
        for provider, (_, pip_pkg) in self._packages.items():
            cb = self.checkboxes.get(self.key_for(provider))
            if cb and cb.isChecked() and cb.isEnabled():
                packages.append(pip_pkg)
        return packages

    def step_label(self, item: InstallItem) -> str | None:
        """Return how the status line names *item*, or ``None`` when this tab does not own it."""
        if item.kind is not InstallKind.PACKAGE:
            return None
        for provider, (_, pip_pkg) in self._packages.items():
            if pip_pkg == item.name:
                return self._provider_name(provider)
        return None

    def preselect(self, entries: Sequence[InstallItem]) -> None:
        """Tick the boxes of the providers a deep link names (sharing a package: one box)."""
        items = [
            self.key_for(entry.name) for entry in entries if entry.kind is InstallKind.PROVIDER
        ]
        self._pending.extend(items)
        tick_enabled(self.checkboxes, items)

    def tick_preselect(self) -> None:
        """Tick the requested boxes again, now that the check knows what is installed."""
        tick_enabled(self.checkboxes, self._pending)
        self._pending.clear()

    def mark_installed(self, results: Mapping[InstallItem, bool]) -> None:
        """Show which packages are importable already and disable their boxes."""
        apply_installed(self.checkboxes, self.labels, results)

    def _provider_name(self, provider: str) -> str:
        """Return the display name of *provider* (its own name when it has none)."""
        return self._svc.providers.display_name(provider)
