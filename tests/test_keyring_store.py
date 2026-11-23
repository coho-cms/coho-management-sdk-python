"""The keyring store, against a keyring with Windows' limit: 1280 characters a credential.

Cognito's three tokens are several times that, so the store writes them in pieces. The fake
refuses a longer value the way Windows does, with an error that is not a KeyringError.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from coho_management_sdk import auth
from coho_management_sdk.errors import AuthError

WINDOWS_LIMIT = 1280


class CredWriteError(Exception):
    """What win32ctypes raises from CredWrite: not a KeyringError."""


class FakeKeyring:
    def __init__(self, limit: int = WINDOWS_LIMIT) -> None:
        self.limit = limit
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.entries.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        if len(value) > self.limit:
            raise CredWriteError(1783, "CredWrite", "The stub received bad data.")
        self.entries[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        self.entries.pop((service, key), None)

    def keys(self) -> list[str]:
        return sorted(key for _, key in self.entries)


def store_with(keyring: Any) -> auth.KeyringTokenStore:
    store = auth.KeyringTokenStore()
    store._keyring = keyring
    return store


def cognito_sized() -> auth.TokenSet:
    """About the size Cognito's are: two JWTs and a longer encrypted refresh token."""
    return auth.TokenSet(
        access_token="a" * 1100,
        refresh_token="r" * 1800,
        id_token="i" * 1200,
        expires_at=1_900_000_000.0,
    )


def test_a_token_set_over_the_windows_limit_saves_and_loads() -> None:
    keyring = FakeKeyring()
    store = store_with(keyring)
    tokens = cognito_sized()

    store.save("default", tokens)

    assert store.load("default") == tokens
    assert all(len(v) <= WINDOWS_LIMIT for v in keyring.entries.values())
    assert keyring.keys()[0] == "default"
    assert json.loads(keyring.entries[("coho", "default")]) == {"chunks": 5}


def test_a_smaller_token_set_drops_the_pieces_it_no_longer_needs() -> None:
    keyring = FakeKeyring()
    store = store_with(keyring)
    store.save("default", cognito_sized())

    small = auth.TokenSet(access_token="a" * 10)
    store.save("default", small)

    assert store.load("default") == small
    assert keyring.keys() == ["default", "default#1"]


def test_delete_removes_every_piece() -> None:
    keyring = FakeKeyring()
    store = store_with(keyring)
    store.save("default", cognito_sized())
    store.save("other", auth.TokenSet(access_token="o"))

    store.delete("default")

    assert store.load("default") is None
    assert keyring.keys() == ["other", "other#1"]


def test_an_entry_an_older_sdk_wrote_whole_still_loads() -> None:
    keyring = FakeKeyring(limit=100_000)
    tokens = cognito_sized()
    keyring.set_password("coho", "default", json.dumps(tokens.to_dict()))

    assert store_with(keyring).load("default") == tokens


def test_a_missing_piece_reads_as_signed_out() -> None:
    keyring = FakeKeyring()
    store = store_with(keyring)
    store.save("default", cognito_sized())
    keyring.delete_password("coho", "default#3")

    assert store.load("default") is None


def test_a_backend_error_that_is_not_a_keyring_error_is_an_auth_error() -> None:
    store = store_with(FakeKeyring(limit=10))

    with pytest.raises(AuthError, match="keyring write failed"):
        store.save("default", auth.TokenSet(access_token="a" * 50))
