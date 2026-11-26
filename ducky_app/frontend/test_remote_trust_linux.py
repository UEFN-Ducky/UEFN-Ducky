"""remote_trust on Linux: the PC key is sealed by the Secret Service seal, or by the 0600
key file when no keyring answers, and Settings is told which. Runs on any OS by forcing
the Linux branch of backend.agent.secrets (as test_secrets_linux does)."""

from __future__ import annotations

import sys
import types

import pytest

from backend.agent import secrets as sec
from frontend import remote_trust as rt


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "_is_linux", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")  # on a Mac the same seal is labelled "keychain"
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "credentials_path", lambda: tmp_path / "credentials.dat")
    monkeypatch.setattr(sec, "_use_db", lambda: False)  # the file store, so the test can read the bytes
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setattr(sec, "_memory_cache", None)
    return tmp_path


def _keyring(monkeypatch, store: dict | None) -> None:
    mod = types.ModuleType("keyring")

    def get_password(service, user):
        if store is None:
            raise RuntimeError("No recommended backend was available")
        return store.get((service, user))

    def set_password(service, user, value):
        if store is None:
            raise RuntimeError("No recommended backend was available")
        store[(service, user)] = value

    mod.get_password, mod.set_password = get_password, set_password
    monkeypatch.setitem(sys.modules, "keyring", mod)


@pytest.mark.parametrize("store, kept", [({}, "keyring"), (None, "file")])
def test_the_pc_key_survives_a_restart_and_says_how_it_is_kept(linux, monkeypatch, store, kept) -> None:
    _keyring(monkeypatch, store)
    pub = rt.pc_pub_b64()
    assert rt.key_protection() == kept
    assert b"PRIVATE KEY" not in (linux / "credentials.dat").read_bytes()  # sealed, never PEM on disk
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setattr(sec, "_memory_cache", None)
    assert rt.pc_pub_b64() == pub
