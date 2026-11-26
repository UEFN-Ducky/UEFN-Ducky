"""remote_trust on macOS: the PC key goes through the vault's POSIX path, kept in the
Keychain (python-keyring's macOS backend) or the 0600 key file when no keyring answers,
and Settings is told which. Runs on any OS by forcing darwin and the vault's POSIX branch."""

from __future__ import annotations

import sys

import pytest

from backend.agent import secrets as sec
from frontend import remote_trust as rt
from frontend.test_remote_trust_linux import _keyring


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sec, "_is_linux", lambda: True)  # the vault's POSIX path
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "credentials_path", lambda: tmp_path / "credentials.dat")
    monkeypatch.setattr(sec, "_use_db", lambda: False)
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setattr(sec, "_memory_cache", None)
    return tmp_path


@pytest.mark.parametrize("store, kept", [({}, "keychain"), (None, "file")])
def test_the_mac_pc_key_survives_a_restart_and_says_how_it_is_kept(mac, monkeypatch, store, kept) -> None:
    _keyring(monkeypatch, store)
    pub = rt.pc_pub_b64()
    assert rt.key_protection() == kept
    assert b"PRIVATE KEY" not in (mac / "credentials.dat").read_bytes()
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setattr(sec, "_memory_cache", None)
    assert rt.pc_pub_b64() == pub


def test_a_mac_without_a_vault_says_the_key_is_memory_only(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sec, "_is_linux", lambda: False)
    assert rt.key_protection() == "memory"
