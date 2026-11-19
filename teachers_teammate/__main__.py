"""Run the teachers-teammate CLI via ``python -m teachers_teammate``."""

from .infrastructure.addon_manager import inject_packages_dir, prepare_packages_dir

# Before anything is imported from the add-on folder: finish an interrupted install.
prepare_packages_dir()
inject_packages_dir()

from .cli_composition import run_cli_entrypoint  # noqa: E402

run_cli_entrypoint()
