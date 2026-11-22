"""Plugin data on Linux: data_crypto seals through secrets' Linux seal and survives a restart."""

from __future__ import annotations

import json
import sys
import types

import pytest

from backend.agent import secrets as sec
from backend.uefn_plugins import data_crypto, scopes

ADK = bytes(range(32))


@pytest.fixture
def linux_account(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "_is_linux", lambda: True)
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setitem(sys.modules, "keyring", types.ModuleType("keyring"))  # no keyring: key file
    monkeypatch.setattr(scopes, "account_id", lambda: "acct-1")
    monkeypatch.setattr(data_crypto, "ensure_migrated", lambda _account: None)
    monkeypatch.setattr(data_crypto, "_cache_path", lambda: tmp_path / "account_data_key.bin")
    monkeypatch.setattr(data_crypto, "_device_token", lambda: "dky_v1_tok")
    monkeypatch.setattr(data_crypto, "_fetch_adk", lambda: ADK)
    data_crypto._KEYS.clear()
    data_crypto._LAST_TRY.clear()
    data_crypto._open.cache_clear()
    yield tmp_path
    data_crypto._KEYS.clear()
    data_crypto._LAST_TRY.clear()
    data_crypto._open.cache_clear()


def _restart(monkeypatch) -> None:
    """What a new process starts with: no key in memory, server unreachable."""
    data_crypto._KEYS.clear()
    data_crypto._LAST_TRY.clear()
    data_crypto._open.cache_clear()
    monkeypatch.setattr(sec, "_master_key", None)

    def offline() -> bytes:
        raise OSError("offline")

    monkeypatch.setattr(data_crypto, "_fetch_adk", offline)


def test_plugin_token_survives_restart(linux_account, monkeypatch) -> None:
    token = json.dumps({"refresh_token": "google-workspace-rt"})
    stored = data_crypto.seal_text(token, "acct-1")
    blob = data_crypto.seal_bytes(b"scoped file", "acct-1")
    local = data_crypto.seal_text("signed out", data_crypto._LOCAL)
    assert stored.startswith("enc1:") and "google-workspace-rt" not in stored
    assert (linux_account / "account_data_key.bin").is_file()
    _restart(monkeypatch)
    assert data_crypto.open_text(stored, "acct-1") == token
    assert data_crypto.open_bytes(blob, "acct-1") == b"scoped file"
    assert data_crypto.open_text(local, data_crypto._LOCAL) == "signed out"


def test_other_account_cannot_open(linux_account) -> None:
    stored = data_crypto.seal_text("mine", "acct-1")
    with pytest.raises(OSError):
        data_crypto._unseal(stored, b"adk:" + b"\x00" * 32)
