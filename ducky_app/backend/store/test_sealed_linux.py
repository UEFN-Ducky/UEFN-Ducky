"""Sealed chats on Linux: account_rows / chat_seal / sealed_files / sealed_records all seal
through data_crypto -> secrets' Linux seal. A private chat opens after a restart, and
unsealing keeps memory flat."""

from __future__ import annotations

import gc
import json
import sys
import tracemalloc
import types

import pytest

from backend.agent import secrets as sec
from backend.store import account_rows
from backend.store.repos import chats
from backend.store.test_account_rows import ALICE, ONLINE, PROJECT, _fake_adk, _Who, _write_chat
from backend.uefn_plugins import data_crypto


@pytest.fixture
def linux_who(monkeypatch, tmp_path):
    monkeypatch.setattr(sec, "_is_linux", lambda: True)
    monkeypatch.setattr(sec, "master_key_path", lambda: tmp_path / "master.key")
    monkeypatch.setattr(sec, "_master_key", None)
    monkeypatch.setitem(sys.modules, "keyring", types.ModuleType("keyring"))  # key file fallback
    data_crypto.reset_for_tests()
    ONLINE["on"] = True
    monkeypatch.setattr(data_crypto, "_fetch_adk", _fake_adk)
    yield _Who(monkeypatch)
    data_crypto.reset_for_tests()


def test_private_chat_opens_after_restart(linux_who: _Who, monkeypatch) -> None:
    linux_who.be(ALICE)
    messages = _write_chat("chat-linux")
    stored = chats.conv_get("chat-linux", project_id=PROJECT, with_messages=False)
    assert stored and stored["title"] == "Chat about pelican harbour"

    # Restart: every opened value, data key and the master key leave memory.
    data_crypto.reset_for_tests()
    monkeypatch.setattr(sec, "_master_key", None)
    assert json.dumps(chats.messages_get("chat-linux"), sort_keys=True, default=str) == json.dumps(
        messages, sort_keys=True, default=str
    )
    assert account_rows.open_blob(account_rows.seal_blob(b"attachment", account_rows.current()),
                                  account_rows.current()) == b"attachment"


def test_a_backup_made_after_sealing_never_looks_older_on_linux(monkeypatch) -> None:
    """Linux stat has no birth time; a backdated mtime (utime, copy2) must not pass for it."""
    from types import SimpleNamespace

    from backend.store import sealed_files

    monkeypatch.setattr(sealed_files, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(sealed_files, "os", SimpleNamespace(name="posix"))
    backdated = SimpleNamespace(st_mtime=100.0, st_ctime=5000.0)  # no st_birthtime, as on Linux
    assert sealed_files._made(backdated) == 5000.0
    monkeypatch.setattr(sealed_files, "sys", SimpleNamespace(platform="darwin"))
    assert sealed_files._made(SimpleNamespace(st_mtime=100.0, st_ctime=5000.0, st_birthtime=42.0)) == 42.0


def test_thousand_unseals_keep_memory_flat(linux_who: _Who) -> None:
    linux_who.be("")  # signed out: plain account seal, no data key needed
    row = "x" * 64 * 1024
    stored = data_crypto.seal_text(row, data_crypto._LOCAL)
    entropy = b"local"
    for _ in range(50):  # warm up imports and OpenSSL's one-time allocations
        data_crypto._unseal(stored, entropy)
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(1000):
            assert len(data_crypto._unseal(stored, entropy)) == len(row)
        gc.collect()
        grew = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    assert grew < 256 * 1024, f"traced memory grew {grew} bytes over 1000 unseals"
