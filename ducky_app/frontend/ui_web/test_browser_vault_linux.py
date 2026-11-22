"""Saved Browser logins on Linux: the cookie key is sealed with the keyring master key."""

from __future__ import annotations

import base64
import json
import sys
import types

import pytest

from backend.agent import secrets as sec
from frontend.ui_web import browser_vault as vault

KEY = bytes(range(32))


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(sec, "_is_linux", lambda: True)
    monkeypatch.setattr(vault, "_is_linux", lambda: True)
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setitem(sys.modules, "keyring", types.ModuleType("keyring"))  # key file fallback
    monkeypatch.setattr(vault, "profile_dir", lambda mode: tmp_path / mode)
    monkeypatch.setattr(vault, "start_watcher", lambda: None)
    (tmp_path / "saved" / "EBWebView").mkdir(parents=True)
    return tmp_path / "saved"


def _state(profile) -> dict:
    return json.loads((profile / "EBWebView" / "Local State").read_text(encoding="utf-8"))


def test_vault_lock_and_unlock_survive_restart(profile, monkeypatch) -> None:
    sealed = base64.b64encode(b"DPAPI" + sec.protect_plain(KEY)).decode()
    (profile / "EBWebView" / "Local State").write_text(json.dumps({"os_crypt": {"encrypted_key": sealed}}), "utf-8")
    assert vault.hello_available()
    assert vault.lock() == ""
    assert "encrypted_key" not in _state(profile)["os_crypt"]
    assert KEY not in (profile / "logins.hello").read_bytes()

    monkeypatch.setattr(sec, "_master_key", None)  # restart
    assert vault.unlock() == ""
    blob = base64.b64decode(_state(profile)["os_crypt"]["encrypted_key"])
    assert blob[:5] == b"DPAPI" and sec.unprotect_plain(blob[5:]) == KEY
