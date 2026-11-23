"""Tests for the model store and its checked downloads (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import threading
from typing import Any

import pytest
import requests

from teachers_teammate.domain.local_models import LocalModel, ModelFile
from teachers_teammate.exceptions import (
    ModelDownloadCancelledError,
    ModelDownloadError,
    ModelMissingError,
)
from teachers_teammate.infrastructure.onnx.store import (
    MODELS_DIR_ENV,
    ModelStore,
    default_bundled_models_dir,
    default_models_dir,
)

_BODIES = {"a.bin": b"alpha" * 1000, "b.yml": b"beta: 1\n"}


def _model() -> LocalModel:
    files = tuple(
        ModelFile(name, len(body), hashlib.sha256(body).hexdigest())
        for name, body in _BODIES.items()
    )
    return LocalModel("t-model", "Org/Test_Model", "c" * 40, files)


class _Response:
    def __init__(self, body: bytes, chunk: int = 1000) -> None:
        self._body = body
        self._chunk = chunk
        self.closed = False

    def close(self) -> None:
        """Remember that the connection was given back."""
        self.closed = True

    def raise_for_status(self) -> None:
        """Pretend the answer was 200."""

    def iter_content(self, chunk_size: int = 0) -> Any:
        """Yield the body in small chunks."""
        for i in range(0, len(self._body), self._chunk):
            yield self._body[i : i + self._chunk]


def _getter(
    bodies: dict[str, bytes],
    calls: list[str] | None = None,
    responses: list[_Response] | None = None,
) -> Any:
    def get(url: str, **_kwargs: Any) -> _Response:
        if calls is not None:
            calls.append(url)
        response = _Response(bodies[url.rsplit("/", 1)[-1]])
        if responses is not None:
            responses.append(response)
        return response

    return get


def test_correct_download_installs_and_writes_marker(tmp_path: Path) -> None:
    """
    Given  a store with a fake server that serves the right bytes
    When   the model is downloaded
    Then   it is installed, the files are under root/<repo name>/<revision>, and progress ends
           at the total
    """
    model = _model()
    store = ModelStore(tmp_path, http_get=_getter(_BODIES))
    seen: list[tuple[int, int]] = []

    store.download([model], on_progress=lambda d, t: seen.append((d, t)))

    assert store.is_installed(model)
    assert store.folder(model) == tmp_path / "Test_Model" / ("c" * 40)
    assert store.file(model, "a.bin").read_bytes() == _BODIES["a.bin"]
    assert seen[-1] == (model.size, model.size)
    assert seen[0] == (0, model.size)
    assert not list(store.folder(model).glob("*.part"))


def test_wrong_checksum_is_refused_and_leaves_no_file(tmp_path: Path) -> None:
    """
    Given  a server that serves bytes of the right length but another content
    When   the model is downloaded
    Then   a ModelDownloadError is raised and nothing is installed
    """
    model = _model()
    bad = {"a.bin": b"x" * len(_BODIES["a.bin"]), "b.yml": _BODIES["b.yml"]}
    store = ModelStore(tmp_path, http_get=_getter(bad))

    with pytest.raises(ModelDownloadError, match="damaged"):
        store.download([model])

    assert not store.is_installed(model)
    assert not (store.folder(model) / "a.bin").exists()
    assert not list(store.folder(model).glob("*.part"))


def test_short_body_is_refused(tmp_path: Path) -> None:
    """
    Given  a server that stops before the end of the file
    When   the model is downloaded
    Then   it is refused as damaged
    """
    model = _model()
    short = {"a.bin": _BODIES["a.bin"][:-1], "b.yml": _BODIES["b.yml"]}
    store = ModelStore(tmp_path, http_get=_getter(short))

    with pytest.raises(ModelDownloadError):
        store.download([model])

    assert not store.is_installed(model)


def test_a_body_longer_than_the_file_stops_the_download(tmp_path: Path) -> None:
    """
    Given  a server that keeps sending after the file it should deliver
    When   the model is downloaded
    Then   it stops instead of filling the disk, and nothing is installed
    """
    model = _model()
    oversized = dict(_BODIES, **{"a.bin": _BODIES["a.bin"] + b"more" * 3000})
    store = ModelStore(tmp_path, http_get=_getter(oversized))

    with pytest.raises(ModelDownloadError, match="more data than the file holds"):
        store.download([model])

    assert not store.is_installed(model)
    assert not list(store.folder(model).glob("*.part"))


def test_part_file_is_never_reported_installed(tmp_path: Path) -> None:
    """
    Given  a folder with only a half-written .part file
    When   is_installed and missing are asked
    Then   the model counts as missing
    """
    model = _model()
    store = ModelStore(tmp_path)
    store.folder(model).mkdir(parents=True)
    (store.folder(model) / "a.bin.part").write_bytes(b"half")

    assert not store.is_installed(model)
    assert store.missing([model]) == [model]
    with pytest.raises(ModelMissingError):
        store.file(model, "a.bin")


def test_cancel_removes_the_partial_file(tmp_path: Path) -> None:
    """
    Given  a download whose cancel event is set while the first file arrives
    When   the model is downloaded
    Then   ModelDownloadCancelledError is raised and no .part file remains
    """
    model = _model()
    cancel = threading.Event()
    store = ModelStore(tmp_path, http_get=_getter(_BODIES))

    def stop_after_first_chunk(done: int, total: int) -> None:
        if done > 0:
            cancel.set()

    with pytest.raises(ModelDownloadCancelledError):
        store.download([model], on_progress=stop_after_first_chunk, cancel=cancel)

    assert not store.is_installed(model)
    assert not list(store.folder(model).glob("*.part"))


def test_every_response_is_closed_after_a_download_and_after_a_cancel(tmp_path: Path) -> None:
    """
    Given  a download that finishes and another that is cancelled while a file arrives
    When   both have ended
    Then   every streamed response was closed, so no connection stays open
    """
    model = _model()
    responses: list[_Response] = []
    ModelStore(tmp_path / "ok", http_get=_getter(_BODIES, responses=responses)).download([model])
    assert responses
    assert all(r.closed for r in responses)

    responses.clear()
    cancel = threading.Event()
    store = ModelStore(tmp_path / "stopped", http_get=_getter(_BODIES, responses=responses))

    def stop_after_first_chunk(done: int, total: int) -> None:
        if done > 0:
            cancel.set()

    with pytest.raises(ModelDownloadCancelledError):
        store.download([model], on_progress=stop_after_first_chunk, cancel=cancel)
    assert responses
    assert all(r.closed for r in responses)


def test_two_downloads_at_once_take_turns_and_fetch_each_file_once(tmp_path: Path) -> None:
    """
    Given  two stores on the same folder that both download the same model at the same time
    When   both finish
    Then   neither fails, the files are fetched once (the second finds them there) and no
           .part file remains
    """
    model = _model()
    calls: list[str] = []
    stores = [ModelStore(tmp_path, http_get=_getter(_BODIES, calls)) for _ in range(2)]
    errors: list[BaseException] = []

    def work(store: ModelStore) -> None:
        try:
            store.download([model])
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert sorted(c.rsplit("/", 1)[-1] for c in calls) == ["a.bin", "b.yml"]
    assert stores[0].is_installed(model)
    assert not list(stores[0].folder(model).glob("*.part"))


def test_a_download_waiting_for_another_can_be_cancelled(tmp_path: Path) -> None:
    """
    Given  another download holds the lock and a second one is cancelled while it waits
    When   the second download runs
    Then   it raises ModelDownloadCancelledError without fetching anything
    """
    from teachers_teammate.infrastructure.onnx import store as store_module  # noqa: PLC0415

    calls: list[str] = []
    cancel = threading.Event()
    cancel.set()
    waiting = ModelStore(tmp_path, http_get=_getter(_BODIES, calls))

    with store_module._DOWNLOAD_LOCK, pytest.raises(ModelDownloadCancelledError):
        waiting.download([_model()], cancel=cancel)

    assert calls == []


def test_missing_bytes_counts_only_missing_files(tmp_path: Path) -> None:
    """
    Given  one of two files already in place
    When   missing_bytes is asked
    Then   only the other file's size is counted, and a finished model counts zero
    """
    model = _model()
    store = ModelStore(tmp_path, http_get=_getter(_BODIES))
    store.folder(model).mkdir(parents=True)
    (store.folder(model) / "a.bin").write_bytes(_BODIES["a.bin"])

    assert store.missing_bytes([model]) == len(_BODIES["b.yml"])

    store.download([model])
    assert store.missing_bytes([model]) == 0
    assert store.missing([model]) == []


def test_download_fetches_only_what_is_missing(tmp_path: Path) -> None:
    """
    Given  one file already downloaded
    When   the model is downloaded
    Then   only the other file is requested
    """
    model = _model()
    calls: list[str] = []
    store = ModelStore(tmp_path, http_get=_getter(_BODIES, calls))
    store.folder(model).mkdir(parents=True)
    (store.folder(model) / "a.bin").write_bytes(_BODIES["a.bin"])

    store.download([model])

    assert [c.rsplit("/", 1)[-1] for c in calls] == ["b.yml"]
    assert store.is_installed(model)


def test_unreachable_host_names_the_host(tmp_path: Path) -> None:
    """
    Given  a network that refuses the connection
    When   the model is downloaded
    Then   the error names huggingface.co so the network people know what to allow
    """

    def refuse(_url: str, **_kwargs: Any) -> Any:
        raise requests.ConnectionError("no route")

    store = ModelStore(tmp_path, http_get=refuse)

    with pytest.raises(ModelDownloadError, match=r"huggingface\.co"):
        store.download([_model()])


def test_env_var_moves_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  TEACHERS_TEAMMATE_MODELS_DIR set
    When   a store is made without a root
    Then   it uses that folder
    """
    monkeypatch.setenv(MODELS_DIR_ENV, str(tmp_path / "elsewhere"))

    assert default_models_dir() == tmp_path / "elsewhere"
    assert ModelStore().root == tmp_path / "elsewhere"


# ── the model files the app carries itself ────────────────────────────────


def _bundle(folder: Path, model: LocalModel) -> Path:
    """Write *model* into *folder* the way the build's fetch script does."""
    target = folder / model.repo.split("/")[-1] / model.revision
    target.mkdir(parents=True)
    for name, body in _BODIES.items():
        (target / name).write_bytes(body)
    (target / ".verified").write_text(model.revision, encoding="utf-8")
    return target


def test_a_bundled_model_is_installed_without_a_download(tmp_path: Path) -> None:
    """
    Given  a model the app carries itself and an empty download folder
    When   the store is asked whether it is installed and where its files are
    Then   it is installed, the files are read from the bundle, and nothing is downloaded
    """
    model = _model()
    bundled = _bundle(tmp_path / "bundle", model)
    calls: list[str] = []
    store = ModelStore(
        tmp_path / "downloads",
        http_get=_getter(_BODIES, calls),
        bundled_root=tmp_path / "bundle",
    )

    assert store.is_bundled([model])
    assert store.is_installed(model)
    assert store.missing([model]) == []
    assert store.file(model, "a.bin") == bundled / "a.bin"

    store.download([model])

    assert calls == []
    assert not store.folder(model).exists()


def test_a_damaged_bundled_file_falls_back_to_the_download_folder(tmp_path: Path) -> None:
    """
    Given  a bundle whose copy of one file is truncated, and a complete download
    When   the store is asked where the model is
    Then   the download folder answers, so a damaged bundle file is not read
    """
    model = _model()
    bundled = _bundle(tmp_path / "bundle", model)
    (bundled / "a.bin").write_bytes(b"short")
    store = ModelStore(
        tmp_path / "downloads", http_get=_getter(_BODIES), bundled_root=tmp_path / "bundle"
    )
    store.download([model])

    assert store.is_installed(model)
    assert store.file(model, "a.bin") == store.folder(model) / "a.bin"


def test_an_empty_or_damaged_bundle_folder_is_not_bundled(tmp_path: Path) -> None:
    """
    Given  a bundle whose folder for the model exists but is empty, and one with a short file
    When   the store is asked what the bundle is missing
    Then   the model is missing in both, though a folder is there
    """
    model = _model()
    empty = tmp_path / "empty"
    (empty / "Test_Model" / model.revision).mkdir(parents=True)
    assert ModelStore(tmp_path / "d1", bundled_root=empty).bundled_missing([model]) == [model]
    assert not ModelStore(tmp_path / "d1", bundled_root=empty).is_bundled([model])

    damaged = _bundle(tmp_path / "damaged", model)
    (damaged / "a.bin").write_bytes(b"short")
    assert ModelStore(tmp_path / "d2", bundled_root=tmp_path / "damaged").bundled_missing(
        [model]
    ) == [model]


def test_bundled_missing_ignores_what_was_downloaded(tmp_path: Path) -> None:
    """
    Given  a model that sits complete in the download folder and not in the bundle
    When   the store is asked what the bundle is missing
    Then   the model is still missing from the bundle, though it counts as installed
    """
    model = _model()
    store = ModelStore(
        tmp_path / "downloads", http_get=_getter(_BODIES), bundled_root=tmp_path / "bundle"
    )
    store.download([model])

    assert store.is_installed(model)
    assert store.bundled_missing([model]) == [model]


def test_a_model_the_bundle_does_not_carry_is_not_bundled(tmp_path: Path) -> None:
    """
    Given  a bundle folder that holds another model
    When   the store is asked about this one
    Then   it is not bundled and not installed, so the app offers the download
    """
    store = ModelStore(tmp_path / "downloads", bundled_root=tmp_path / "bundle")

    assert not store.is_bundled([_model()])
    assert not store.is_installed(_model())
    assert store.missing([_model()]) == [_model()]


def test_the_bundle_folder_is_only_read_in_a_frozen_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a source checkout, and then a frozen build
    When   the default bundle folder is worked out
    Then   a checkout has none (it downloads its models) and a build reads its own
    """
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert default_bundled_models_dir() is None

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert default_bundled_models_dir() == tmp_path / "teachers_teammate" / "_models"
