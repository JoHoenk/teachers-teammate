"""Tests for where the API keys are kept: the system keychain, or the settings file."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from teachers_teammate.gui import _credential_store
from teachers_teammate.gui._credential_store import (
    KEYRING_SERVICE,
    CredentialStore,
    KeyLocation,
)
from teachers_teammate.gui._credential_store import _keyring as real_keyring

from ._settings_stubs import FakeKeyring

_FILE_KEY = "credentials/OPENAI_API_KEY"


@pytest.fixture(name="keychain")
def _keychain(monkeypatch) -> FakeKeyring:
    """A working system keychain."""
    fake = FakeKeyring()
    monkeypatch.setattr(_credential_store, "_keyring", lambda: fake)
    return fake


@pytest.mark.gui
def test_without_a_keychain_keys_live_in_the_settings_file(credential_file) -> None:
    """
    Given  a computer without a system keychain
    When   a key is saved, read back and removed
    Then   it is kept in the settings file, reported as such, and gone after removing
    """
    store = CredentialStore()
    assert store.location() is KeyLocation.SETTINGS_FILE

    assert store.set("OPENAI_API_KEY", "sk-1") is KeyLocation.SETTINGS_FILE
    assert credential_file == {_FILE_KEY: "sk-1"}
    assert store.get("OPENAI_API_KEY") == "sk-1"

    store.remove("OPENAI_API_KEY")
    assert credential_file == {}
    assert store.get("OPENAI_API_KEY") == ""


@pytest.mark.gui
def test_with_a_keychain_keys_go_there_and_not_into_the_file(keychain, credential_file) -> None:
    """
    Given  a working system keychain, and an old plain-text copy of the key
    When   a new key is saved
    Then   the keychain holds it, the plain copy is deleted, and reading returns the new key
    """
    credential_file[_FILE_KEY] = "sk-old"
    store = CredentialStore()
    assert store.location() is KeyLocation.KEYCHAIN

    assert store.set("OPENAI_API_KEY", "sk-new") is KeyLocation.KEYCHAIN

    assert keychain.passwords == {(KEYRING_SERVICE, "OPENAI_API_KEY"): "sk-new"}
    assert credential_file == {}
    assert store.get("OPENAI_API_KEY") == "sk-new"


@pytest.mark.gui
def test_a_keychain_that_refuses_the_write_does_not_lose_the_key(
    monkeypatch, credential_file
) -> None:
    """
    Given  a keychain that is locked and refuses to store
    When   a key is saved
    Then   it is kept in the settings file instead, and the caller is told so
    """
    locked = FakeKeyring(refuses_writes=True)
    monkeypatch.setattr(_credential_store, "_keyring", lambda: locked)

    assert CredentialStore().set("OPENAI_API_KEY", "sk-1") is KeyLocation.SETTINGS_FILE

    assert credential_file == {_FILE_KEY: "sk-1"}
    assert CredentialStore().get("OPENAI_API_KEY") == "sk-1"


@pytest.mark.gui
def test_removing_forgets_the_key_in_both_places(keychain, credential_file) -> None:
    """
    Given  a key in the keychain and a leftover plain copy
    When   the key is removed, and a key that was never stored is removed
    Then   both copies are gone and nothing raises
    """
    keychain.passwords[(KEYRING_SERVICE, "OPENAI_API_KEY")] = "sk-1"
    credential_file[_FILE_KEY] = "sk-1"

    CredentialStore().remove("OPENAI_API_KEY")
    CredentialStore().remove("MISTRAL_API_KEY")

    assert keychain.passwords == {}
    assert credential_file == {}


@pytest.mark.gui
def test_start_up_moves_plain_keys_into_the_keychain(
    keychain, credential_file, monkeypatch
) -> None:
    """
    Given  a key an earlier version left in the settings file, a keychain, and no such key in
           the environment
    When   the keys are loaded at start-up
    Then   the key is in the environment, in the keychain, and no longer in the file
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    credential_file[_FILE_KEY] = "sk-old"

    CredentialStore().load_into_environment(["OPENAI_API_KEY", "MISTRAL_API_KEY"])

    assert os.environ["OPENAI_API_KEY"] == "sk-old"
    assert keychain.passwords == {(KEYRING_SERVICE, "OPENAI_API_KEY"): "sk-old"}
    assert credential_file == {}
    assert "MISTRAL_API_KEY" not in os.environ
    monkeypatch.delenv("OPENAI_API_KEY")


@pytest.mark.gui
def test_a_key_in_the_environment_wins_over_the_stored_one(keychain, monkeypatch) -> None:
    """
    Given  a key in the keychain and another one set in the environment before the app started
    When   the keys are loaded at start-up
    Then   the environment keeps its own key
    """
    keychain.passwords[(KEYRING_SERVICE, "OPENAI_API_KEY")] = "sk-stored"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")

    CredentialStore().load_into_environment(["OPENAI_API_KEY"])

    assert os.environ["OPENAI_API_KEY"] == "sk-env"


@pytest.mark.gui
def test_a_failed_move_leaves_the_plain_copy_for_the_next_start(
    monkeypatch, credential_file
) -> None:
    """
    Given  a keychain that refuses writes and a key in the settings file
    When   the keys are loaded at start-up
    Then   the key is still usable and still in the file (nothing was lost)
    """
    locked = FakeKeyring(refuses_writes=True)
    monkeypatch.setattr(_credential_store, "_keyring", lambda: locked)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    credential_file[_FILE_KEY] = "sk-old"

    CredentialStore().load_into_environment(["OPENAI_API_KEY"])

    assert os.environ["OPENAI_API_KEY"] == "sk-old"
    assert credential_file == {_FILE_KEY: "sk-old"}
    monkeypatch.delenv("OPENAI_API_KEY")


@pytest.mark.gui
@pytest.mark.parametrize(
    ("module", "usable"),
    [
        (None, False),  # keyring is not installed
        (SimpleNamespace(get_keyring=lambda: SimpleNamespace(priority=0)), False),  # "fail" backend
        (SimpleNamespace(get_keyring=lambda: SimpleNamespace(priority=5)), True),
    ],
    ids=["not-installed", "no-backend", "working-backend"],
)
def test_the_keychain_counts_only_when_keyring_has_a_working_backend(
    monkeypatch, module, usable
) -> None:
    """
    Given  keyring missing, installed without any backend (a Linux session with no secret
           service), or installed with a working backend
    When   the app asks for the keychain
    Then   only the working backend counts; the others mean "use the settings file"
    """
    monkeypatch.setitem(sys.modules, "keyring", module)

    assert (real_keyring() is not None) is usable


@pytest.mark.gui
def test_a_keychain_that_raises_while_loading_counts_as_missing(monkeypatch) -> None:
    """
    Given  keyring whose backend cannot be loaded (broken D-Bus, for example)
    When   the app asks for the keychain
    Then   it is treated as missing instead of crashing
    """

    def broken() -> None:
        raise RuntimeError("D-Bus is not available")

    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_keyring=broken))

    assert real_keyring() is None


@pytest.mark.gui
def test_a_refused_replacement_is_not_shadowed_by_the_old_keychain_key(
    monkeypatch, credential_file
) -> None:
    """
    Given  an older key in the keychain, and a keychain that now refuses writes (locked)
    When   a new key is saved, and the keys are loaded at the next start
    Then   the new key is the one in use, it is not thrown away in favour of the old one, and
           the old keychain entry is gone
    """
    locked = FakeKeyring(refuses_writes=True)
    locked.passwords[(KEYRING_SERVICE, "OPENAI_API_KEY")] = "sk-old"
    monkeypatch.setattr(_credential_store, "_keyring", lambda: locked)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert CredentialStore().set("OPENAI_API_KEY", "sk-new") is KeyLocation.SETTINGS_FILE

    assert locked.passwords == {}
    assert CredentialStore().get("OPENAI_API_KEY") == "sk-new"
    CredentialStore().load_into_environment(["OPENAI_API_KEY"])
    assert os.environ["OPENAI_API_KEY"] == "sk-new"
    monkeypatch.delenv("OPENAI_API_KEY")


@pytest.mark.gui
def test_the_newer_plain_key_replaces_a_stale_keychain_key_when_it_is_moved(
    keychain, credential_file, monkeypatch
) -> None:
    """
    Given  a plain copy in the settings file (left by a failed keychain write) and an older
           key under the same name in the keychain, which cannot be deleted
    When   the keys are loaded at start-up
    Then   the plain key is used and overwrites the keychain entry, so the file copy is dropped
           without losing the newer key
    """
    keychain.passwords[(KEYRING_SERVICE, "OPENAI_API_KEY")] = "sk-old"
    credential_file[_FILE_KEY] = "sk-new"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    CredentialStore().load_into_environment(["OPENAI_API_KEY"])

    assert os.environ["OPENAI_API_KEY"] == "sk-new"
    assert keychain.passwords == {(KEYRING_SERVICE, "OPENAI_API_KEY"): "sk-new"}
    assert credential_file == {}
    monkeypatch.delenv("OPENAI_API_KEY")
