"""Who is signed in, the secrets rows behind it, and the key an account's rows are sealed with."""

from __future__ import annotations

import base64
import json

import pytest

from backend.agent import secrets as agent_secrets
from backend.store.repos import secrets as repo
from backend.uefn_plugins import data_crypto, scopes
from frontend import duckyos_account

from conftest import ADK as _ADK, LOGIN as _LOGIN

_KEY = "https://uefnducky.org|alice@example.com"
_LOCKED = "Waiting for this account's data key. Your chats open once you're online."


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


@pytest.fixture(autouse=True)
def _sign_in(login):
    """Every test starts signed out; ``_sign_in(blob)`` stores a login row."""
    return login


def _entropy_error(rust_store) -> str:
    status, reply = rust_store.request("/__rs/store/account/entropy", {})
    assert status == 200 and reply["ok"] is False
    return reply["error"]


# --------------------------------------------------------------------------- who


def test_signed_out_is_the_local_account_for_both(rust_store) -> None:
    assert rust_store.store("account", "current") == {"key": "", "id": "_local"}
    assert (duckyos_account.account_key(), scopes.account_id()) == ("", "_local")
    assert rust_store.store("account", "entropy") == {"id": "_local", "entropy": _b64(b"local")}


def test_rust_names_the_account_python_signed_in(rust_store, _sign_in) -> None:
    account = _sign_in(_LOGIN)
    assert duckyos_account.account_key() == _KEY
    assert rust_store.store("account", "current") == {"key": _KEY, "id": account}


def test_python_names_the_account_rust_signed_in(rust_store) -> None:
    blob = rust_store.store("seal", "protect_secret", value=json.dumps(_LOGIN))
    rust_store.store("secrets", "set_blob", name=repo.LOGIN_NAME, blob=blob)
    assert agent_secrets.unprotect_text(base64.b64decode(blob)) == json.dumps(_LOGIN)
    assert duckyos_account.account_key() == _KEY
    assert rust_store.store("account", "current")["id"] == scopes.account_id()


def test_a_login_without_a_token_or_session_is_signed_out(rust_store, _sign_in) -> None:
    assert _sign_in({"base_url": "https://uefnducky.org", "email": "alice@example.com"}) == "_local"
    assert rust_store.store("account", "current") == {"key": "", "id": "_local"}


# --------------------------------------------------------------------------- the account data key


def test_an_account_is_locked_until_python_has_cached_its_key(rust_store, _sign_in) -> None:
    account = _sign_in(_LOGIN)
    assert _entropy_error(rust_store) == _LOCKED
    assert data_crypto.adk(account) == _ADK  # fetched, and cached on disk for the next start
    assert rust_store.store("account", "entropy") == {"id": account, "entropy": _b64(b"adk:" + _ADK)}


def test_rows_sealed_for_the_account_open_in_the_other_backend(rust_store, _sign_in) -> None:
    account = _sign_in(_LOGIN)
    by_python = data_crypto.seal_text("pelican harbour é", account)
    entropy = rust_store.store("account", "entropy")["entropy"]
    assert rust_store.store("seal", "open_text", stored=by_python, entropy=entropy) == "pelican harbour é"
    by_rust = rust_store.store("seal", "seal_text", text="pelican harbour é", entropy=entropy)
    assert data_crypto.open_text(by_rust, account) == "pelican harbour é"
    # Signed out it is another secret: the account's rows stay shut.
    status, reply = rust_store.request("/__rs/store/seal/open_text", {"stored": by_python, "entropy": _b64(b"local")})
    assert status == 200 and reply["ok"] is False


def test_the_cached_key_is_only_for_the_login_that_cached_it(rust_store, _sign_in) -> None:
    account = _sign_in(_LOGIN)
    assert data_crypto.adk(account) == _ADK
    # Another device token (signed in again): the cache was wrapped with the old one.
    agent_secrets.set_key(repo.LOGIN_NAME, json.dumps({**_LOGIN, "device_key": "device-token-2"}))
    assert _entropy_error(rust_store) == _LOCKED
    # Another account on this PC never gets the first one's key.
    agent_secrets.set_key(repo.LOGIN_NAME, json.dumps({**_LOGIN, "email": "bob@example.com"}))
    assert rust_store.store("account", "current")["id"] != account
    assert _entropy_error(rust_store) == _LOCKED


# --------------------------------------------------------------------------- secrets rows


def test_secret_rows_written_by_one_are_read_by_the_other(rust_store) -> None:
    assert rust_store.store("secrets", "has_rows") is repo.has_rows() is False
    repo.set_blob("openai", b"\x00\x01py")
    rust_store.store("secrets", "set_blob", name="anthropic", blob=_b64(b"\xffrs"))
    assert repo.all_blobs() == {"anthropic": b"\xffrs", "openai": b"\x00\x01py"}
    assert rust_store.store("secrets", "all_blobs") == [["anthropic", _b64(b"\xffrs")], ["openai", _b64(b"\x00\x01py")]]
    assert rust_store.store("secrets", "get_blob", name="openai") == _b64(b"\x00\x01py")
    assert rust_store.store("secrets", "get_blob", name="none") is None and repo.get_blob("none") is None
    rust_store.store("secrets", "delete", name="openai")
    assert repo.get_blob("openai") is None and repo.has_rows() is True
    rust_store.store("secrets", "replace_all", blobs={"only": _b64(b"x")})
    assert repo.all_blobs() == {"only": b"x"}


def test_only_the_login_row_moves_the_login_version(rust_store) -> None:
    assert rust_store.store("secrets", "login_version") == repo.login_version() == "0"
    rust_store.store("secrets", "set_blob", name="openai", blob=_b64(b"k"))
    assert repo.login_version() == "0"
    rust_store.store("secrets", "set_blob", name=repo.LOGIN_NAME, blob=_b64(b"a"))
    repo.set_blob(repo.LOGIN_NAME, b"b")
    rust_store.store("secrets", "delete", name=repo.LOGIN_NAME)
    assert rust_store.store("secrets", "login_version") == repo.login_version() == "3"
    rust_store.store("secrets", "replace_all", blobs={})
    assert repo.login_version() == "4"
