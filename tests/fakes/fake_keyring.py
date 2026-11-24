"""In-memory stand-in for the ``keyring`` module: a system keychain that is not on this machine."""

from __future__ import annotations


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
