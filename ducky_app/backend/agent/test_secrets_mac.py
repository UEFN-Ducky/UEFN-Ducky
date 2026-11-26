"""macOS seals secrets like Linux, with the master key in the Keychain (keyring's macOS backend)."""

from __future__ import annotations

import sys

import pytest

from backend.agent import secrets as sec
from backend.agent.test_secrets_linux import _fake_keyring


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sec.sys, "platform", "darwin")
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "_master_key", None)
    return tmp_path


def test_darwin_takes_the_keyring_seal(mac) -> None:
    assert sys.platform == "darwin"
    assert sec._uses_keyring_seal() and sec._dpapi_available()


def test_round_trip_keeps_master_key_in_keychain(mac, monkeypatch) -> None:
    store: dict = {}
    _fake_keyring(monkeypatch, store)
    blob = sec.protect_bytes(b"payload", b"adk:1")
    assert blob[0] == 1 and sec.unprotect_bytes(blob, b"adk:1") == b"payload"
    with pytest.raises(OSError):
        sec.unprotect_bytes(blob, b"adk:2")
    assert list(store) == [("UEFN-Ducky", "master-key")]
    assert not (mac / "master.key").exists()
    monkeypatch.setattr(sec, "_master_key", None)  # a new process reads it back from the Keychain
    assert sec.unprotect_bytes(blob, b"adk:1") == b"payload"


def test_locked_keychain_falls_back_to_key_file(mac, monkeypatch) -> None:
    _fake_keyring(monkeypatch, None, broken=True)
    blob = sec.seal(b"secret")
    assert len((mac / "master.key").read_bytes()) == 32
    assert sec.unseal(blob) == b"secret"
