"""Run the teachers-teammate CLI via ``python -m teachers_teammate``."""

from .texts.messages import install

# First: the add-on folder check below can fail, and a teacher reads that failure as a
# sentence, not as a key with its data.
install()

from .infrastructure.addon_manager import inject_packages_dir, prepare_packages_dir  # noqa: E402

# Before anything is imported from the add-on folder: finish an interrupted install.
prepare_packages_dir()
inject_packages_dir()

from .cli_composition import run_cli_entrypoint  # noqa: E402

run_cli_entrypoint()
