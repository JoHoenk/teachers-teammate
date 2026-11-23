"""The folder of downloaded model files, and the download into it.

Downloads happen only when the teacher asks for them (Add-ons, the fix button, the Setup
Assistant, ``--download-reader``); nothing here is called at start-up.  Every file is checked
against the size and SHA-256 pinned in :mod:`teachers_teammate.domain.local_models` before it
becomes visible under its final name.

The installed app carries the reader and the page models itself (``teachers_teammate/_models``,
fetched at build time by ``tools/build/fetch_models.py``).  A model the bundle holds counts as
installed, so nothing is downloaded and nothing is written next to the program files.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
import errno
import hashlib
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any

from ... import __version__
from ...domain.local_models import HUGGING_FACE_HOST, LocalModel, ModelFile
from ...exceptions import ModelDownloadCancelledError, ModelDownloadError, ModelMissingError
from ...texts.addons import (
    DOWNLOAD_CANCELLED,
    download_damaged,
    download_too_large,
    models_not_downloaded,
)
from ..storage_root import app_data_folder

_logger = logging.getLogger(__name__)

MODELS_DIR_ENV = "TEACHERS_TEAMMATE_MODELS_DIR"
"""Tests and developers point the models folder elsewhere with this variable."""

_MARKER = ".verified"
_CHUNK = 1024 * 1024
_TIMEOUT = (10, 60)

ProgressFn = Callable[[int, int], None]
"""``(bytes done, bytes total)`` over the whole download call."""

_DOWNLOAD_LOCK = threading.Lock()
"""One download at a time in this process: two would write the same ``.part`` files."""
_LOCK_POLL_S = 0.2


@contextmanager
def _one_download_at_a_time(cancel: threading.Event | None) -> Iterator[None]:
    """Hold the download lock; a download that waits for it can still be cancelled."""
    while not _DOWNLOAD_LOCK.acquire(timeout=_LOCK_POLL_S):
        if cancel is not None and cancel.is_set():
            raise ModelDownloadCancelledError(DOWNLOAD_CANCELLED)
    try:
        yield
    finally:
        _DOWNLOAD_LOCK.release()


def default_models_dir() -> Path:
    """Return the folder the models live in (next to the add-on packages), or the env var."""
    override = os.environ.get(MODELS_DIR_ENV)
    if override:
        return Path(override)
    return app_data_folder() / "models"


def default_bundled_models_dir() -> Path | None:
    """Return the folder of the model files that ship with the app, or ``None``.

    Only a frozen build has them: the spec adds ``teachers_teammate/_models`` to the bundle, and
    a source checkout downloads its models like any other.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is None:
        return None
    return Path(meipass) / "teachers_teammate" / "_models"


class ModelStore:
    """Keeps model sets under ``root / <repo name> / <revision>``.

    A model the bundle carries is read from there instead; the download folder is only used for
    what the app does not ship.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        http_get: Callable[..., Any] | None = None,
        bundled_root: Path | None = None,
    ) -> None:
        self._root = root if root is not None else default_models_dir()
        self._bundled_root = (
            bundled_root if bundled_root is not None else default_bundled_models_dir()
        )
        # ``None`` = ``requests.get``, imported when a download starts: asking whether a model
        # is installed (the cache key does, for every page) must not need the package.
        self._http_get = http_get

    @property
    def root(self) -> Path:
        """Return the folder that holds all model sets."""
        return self._root

    @staticmethod
    def _relative(model: LocalModel) -> Path:
        """Return where *model* sits below a models folder."""
        return Path(model.repo.split("/")[-1]) / model.revision

    def folder(self, model: LocalModel) -> Path:
        """Return the download folder of *model* (it may not exist yet)."""
        return self._root / self._relative(model)

    def bundled_folder(self, model: LocalModel) -> Path | None:
        """Return the folder *model* ships in, or ``None`` when the app does not carry it."""
        if self._bundled_root is None:
            return None
        folder = self._bundled_root / self._relative(model)
        return folder if folder.is_dir() else None

    def bundled_missing(self, models: Iterable[LocalModel]) -> list[LocalModel]:
        """Return the models of *models* the app's own bundle does not carry completely.

        Only the bundle counts, never the download folder: a build that lost its models is
        found out even on a computer where they were downloaded earlier.  A folder that exists
        but lacks the marker or a file at its pinned size is as missing as no folder.
        """
        missing: list[LocalModel] = []
        for model in models:
            folder = self.bundled_folder(model)
            if folder is None or not self._complete(folder, model):
                missing.append(model)
        return missing

    def is_bundled(self, models: Iterable[LocalModel]) -> bool:
        """Return whether the app carries *models* itself, so they need no download."""
        return not self.bundled_missing(models)

    def is_installed(self, model: LocalModel) -> bool:
        """Return whether *model* is complete: the marker is there and every file has its size.

        Cheap on purpose (no hashing): the readiness line asks it every few seconds.
        """
        return self._installed_folder(model) is not None

    def _installed_folder(self, model: LocalModel) -> Path | None:
        """Return the folder *model* is complete in (the bundle first), or ``None``."""
        for folder in (self.bundled_folder(model), self.folder(model)):
            if folder is not None and self._complete(folder, model):
                return folder
        return None

    @staticmethod
    def _complete(folder: Path, model: LocalModel) -> bool:
        """Return whether *folder* holds every file of *model* at its pinned size."""
        if not (folder / _MARKER).is_file():
            return False
        for file in model.files:
            try:
                if (folder / file.name).stat().st_size != file.size:
                    return False
            except OSError:
                return False
        return True

    def missing(self, models: Iterable[LocalModel]) -> list[LocalModel]:
        """Return the models of *models* that are not installed."""
        return [m for m in models if not self.is_installed(m)]

    def missing_bytes(self, models: Iterable[LocalModel]) -> int:
        """Return the bytes still to download for *models* (whole files that are absent)."""
        total = 0
        for model in self.missing(models):
            folder = self.folder(model)
            for file in model.files:
                if not self._file_ok(folder / file.name, file):
                    total += file.size
        return total

    def file(self, model: LocalModel, name: str) -> Path:
        """Return the path of file *name* of *model*.

        Raises:
            ModelMissingError: if *model* is not installed.
        """
        folder = self._installed_folder(model)
        if folder is None:
            raise ModelMissingError(models_not_downloaded(model.repo))
        return folder / name

    # ── download ──────────────────────────────────────────────────────────

    def download(
        self,
        models: Iterable[LocalModel],
        *,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Download what is missing of *models*, checking every file.

        Raises:
            ModelDownloadCancelledError: when *cancel* is set (no partial file is kept).
            ModelDownloadError: for any other failure, with a sentence a teacher can act on.
        """
        with _one_download_at_a_time(cancel):
            # Worked out inside the lock: a download that waited finds the files there.
            todo = self.missing(models)
            total = self.missing_bytes(todo)
            done = 0
            if on_progress:
                on_progress(done, total)
            for model in todo:
                folder = self.folder(model)
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise ModelDownloadError(_not_writable(folder, exc)) from exc
                for file in model.files:
                    target = folder / file.name
                    if self._file_ok(target, file):
                        continue
                    before = done

                    def report(n: int, base: int = before) -> None:
                        if on_progress:
                            on_progress(base + n, total)

                    self._fetch(model.url(file), file, target, report, cancel)
                    done += file.size
                try:
                    (folder / _MARKER).write_text(model.revision, encoding="utf-8")
                except OSError as exc:
                    raise ModelDownloadError(_not_writable(folder, exc)) from exc
                _logger.info("Downloaded %s (%s)", model.repo, model.short_revision)
            if on_progress:
                on_progress(total, total)

    def _file_ok(self, path: Path, file: ModelFile) -> bool:
        try:
            return path.is_file() and path.stat().st_size == file.size
        except OSError:
            return False

    def _fetch(
        self,
        url: str,
        file: ModelFile,
        target: Path,
        report: Callable[[int], None],
        cancel: threading.Event | None,
    ) -> None:
        import requests  # noqa: PLC0415

        part = target.with_name(target.name + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            try:
                response = (self._http_get or requests.get)(
                    url,
                    stream=True,
                    timeout=_TIMEOUT,
                    headers={"User-Agent": f"teachers-teammate/{__version__}"},
                )
            except requests.RequestException as exc:
                raise ModelDownloadError(_network_message(exc)) from exc
            # Closed on every way out (cancel, failure, success): a streamed response keeps
            # its connection until then.
            with closing(response):
                try:
                    response.raise_for_status()
                except requests.RequestException as exc:
                    raise ModelDownloadError(_network_message(exc)) from exc
                try:
                    with part.open("wb") as out:
                        for chunk in response.iter_content(chunk_size=_CHUNK):
                            if cancel is not None and cancel.is_set():
                                raise ModelDownloadCancelledError(DOWNLOAD_CANCELLED)
                            if not chunk:
                                continue
                            out.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                            if size > file.size:
                                # The answer outgrew the file it should be: reading on would
                                # fill the disk for a download that cannot pass the check.
                                raise ModelDownloadError(download_too_large(file.name))
                            report(size)
                except requests.RequestException as exc:
                    raise ModelDownloadError(_network_message(exc)) from exc
                except OSError as exc:
                    raise ModelDownloadError(_disk_message(part, exc)) from exc
            if size != file.size or digest.hexdigest() != file.sha256:
                raise ModelDownloadError(download_damaged(file.name))
            try:
                os.replace(part, target)
            except OSError as exc:
                raise ModelDownloadError(_disk_message(target, exc)) from exc
        finally:
            part.unlink(missing_ok=True)


def _network_message(exc: Exception) -> str:
    return (
        f"Could not reach {HUGGING_FACE_HOST}. Check the internet connection, or ask whoever "
        f"looks after the network to allow {HUGGING_FACE_HOST}. ({exc.__class__.__name__})"
    )


def _disk_message(path: Path, exc: OSError) -> str:
    if getattr(exc, "errno", None) == errno.ENOSPC:
        return f"There is no space left on the disk to save the model files in {path.parent}."
    return _not_writable(path.parent, exc)


def _not_writable(folder: Path, exc: OSError) -> str:
    return f"The folder {folder} cannot be written to: {exc.strerror or exc}"
