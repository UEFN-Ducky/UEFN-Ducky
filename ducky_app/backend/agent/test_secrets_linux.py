"""The Linux seal (secrets._linux_seal): runs on any OS by forcing the Linux branch."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from backend.agent import secrets as sec

APP_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def linux(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "_is_linux", lambda: True)
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "_master_key", None)
    return tmp_path


def _fake_keyring(monkeypatch, store: dict | None, *, broken: bool = False) -> None:
    mod = types.ModuleType("keyring")

    def get_password(service, user):
        if broken:
            raise RuntimeError("No recommended backend was available")
        return store.get((service, user))

    def set_password(service, user, value):
        if broken:
            raise RuntimeError("No recommended backend was available")
        store[(service, user)] = value

    mod.get_password, mod.set_password = get_password, set_password
    monkeypatch.setitem(sys.modules, "keyring", mod)


def test_round_trip_with_keyring_and_wrong_entropy_fails(linux, monkeypatch) -> None:
    store: dict = {}
    _fake_keyring(monkeypatch, store)
    blob = sec.protect_bytes(b"payload", b"adk:1")
    assert blob[0] == 1 and sec.unprotect_bytes(blob, b"adk:1") == b"payload"
    with pytest.raises(OSError):
        sec.unprotect_bytes(blob, b"adk:2")
    assert sec.unprotect_text(sec.protect_text("sk-openai")) == "sk-openai"
    assert sec.unprotect_plain(sec.protect_plain(b"k")) == b"k"
    with pytest.raises(OSError):  # None entropy and b"" entropy are different seals, like DPAPI
        sec.unseal(sec.seal(b"x", None), b"")
    assert len(store) == 1 and not (linux / "master.key").exists()
    # A new process reads the key back from the keyring.
    monkeypatch.setattr(sec, "_master_key", None)
    assert sec.unprotect_bytes(blob, b"adk:1") == b"payload"


def test_no_keyring_falls_back_to_0600_key_file(linux, monkeypatch, caplog) -> None:
    _fake_keyring(monkeypatch, None, broken=True)
    with caplog.at_level("WARNING"):
        blob = sec.seal(b"secret")
    key_file = linux / "master.key"
    assert key_file.read_bytes() and len(key_file.read_bytes()) == 32
    assert "No keyring" in caplog.text
    if os.name == "posix":
        assert key_file.stat().st_mode & 0o777 == 0o600
    # Keyring comes back later: the existing key file still wins, old blobs stay readable.
    _fake_keyring(monkeypatch, {})
    monkeypatch.setattr(sec, "_master_key", None)
    assert sec.unseal(blob) == b"secret"


def test_restart_new_process_reads_old_blob(linux, monkeypatch) -> None:
    _fake_keyring(monkeypatch, None, broken=True)
    blob = sec.protect_text("sk-after-restart")
    child = textwrap.dedent(
        f"""
        import sys, types
        from pathlib import Path
        sys.modules["keyring"] = types.ModuleType("keyring")  # no keyring: file fallback
        from backend.agent import secrets as sec
        sec._is_linux = lambda: True
        sec.master_key_path = lambda: Path({str(linux / "master.key")!r})
        print(sec.unprotect_text(bytes.fromhex({blob.hex()!r})))
        """
    )
    out = subprocess.run([sys.executable, "-c", child], cwd=APP_ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "sk-after-restart"


def test_keys_persist_across_restart(linux, monkeypatch) -> None:
    """The OpenAI key is still set after a restart (files backend)."""
    _fake_keyring(monkeypatch, {})
    monkeypatch.setattr(sec, "_use_db", lambda: False)
    monkeypatch.setattr(sec, "credentials_path", lambda: linux / "credentials.dat")
    sec.clear_memory_cache()
    try:
        sec.set_key("openai", "sk-live")
        raw = (linux / "credentials.dat").read_bytes()
        assert raw[:5] == b"UMCP\x01" and b"sk-live" not in raw
        sec.clear_memory_cache()
        monkeypatch.setattr(sec, "_master_key", None)
        assert sec.get_key("openai") == "sk-live"
    finally:
        sec.clear_memory_cache()
