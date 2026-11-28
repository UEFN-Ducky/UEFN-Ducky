"""Sealed files (``UDF1``): keypairs made by one backend and used by the other, files
sealed by one and opened by the other, chat attachments following who is signed in.
Tests taking ``account`` run signed out and signed in.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from backend.store import db, sealed_files
from backend.store.repos import chats
from backend.uefn_plugins import data_crypto

from conftest import LOGIN
from test_store_chats import _doc, _sql

_MIB = 1024 * 1024
_SIZES = (0, 1, _MIB - 1, _MIB, _MIB + 1, 2 * _MIB + 5)
_WRONG = "This file is locked to another Ducky account. Sign in with that account to open it."


@pytest.fixture(autouse=True)
def _no_opened_keys():
    sealed_files.reset_for_tests()
    yield
    sealed_files.reset_for_tests()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _data(size: int) -> bytes:
    return bytes(i % 251 for i in range(size))


def _refused(rust_store, function: str, **args) -> str:
    status, reply = rust_store.request(f"/__rs/store/sealed_files/{function}", args)
    assert status == 200 and reply["ok"] is False
    return reply["error"]


def _keys(account: str) -> tuple:
    return tuple(_sql("SELECT key, value FROM meta WHERE key IN (?, ?) ORDER BY key", "file_key_priv:" + account, "file_key_pub:" + account))


def _keys_file() -> dict:
    return json.loads((db.app_root() / sealed_files.KEYS_FILE).read_text("utf-8"))


# --------------------------------------------------------------------------- keys


def test_rust_uses_the_keypair_python_made(rust_store, account) -> None:
    assert rust_store.store("sealed_files", "public_key", account=account) is None
    sealed_files.ensure_keypair(account)
    before = _keys(account)
    assert rust_store.store("sealed_files", "public_key", account=account) == _b64(sealed_files.public_key(account))
    rust_store.store("sealed_files", "ensure_keypair")
    assert _keys(account) == before and len(before) == 2


def test_python_uses_the_keypair_rust_made(rust_store, account) -> None:
    rust_store.store("sealed_files", "ensure_keypair")
    pub = rust_store.store("sealed_files", "public_key", account=account)
    stored = dict(_keys(account))
    assert _keys_file() == {account: {"pub": pub, "priv": stored["file_key_priv:" + account]}}
    assert stored["file_key_pub:" + account] == pub and stored["file_key_priv:" + account].startswith("enc1:")
    sealed_files.ensure_keypair(account)
    assert dict(_keys(account)) == stored and _b64(sealed_files.public_key(account)) == pub
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private = sealed_files._private_key(account)
    assert _b64(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)) == pub


def test_a_pair_lost_from_one_place_comes_back_from_the_other(rust_store, account) -> None:
    rust_store.store("sealed_files", "ensure_keypair")
    stored, on_disk = _keys(account), _keys_file()
    _sql("DELETE FROM meta WHERE key LIKE 'file_key_%'")  # an older ducky.db restored
    assert rust_store.store("sealed_files", "public_key", account=account) == on_disk[account]["pub"]
    rust_store.store("sealed_files", "ensure_keypair")
    assert _keys(account) == stored
    (db.app_root() / sealed_files.KEYS_FILE).unlink()
    rust_store.store("sealed_files", "ensure_keypair")
    assert _keys_file() == on_disk and _keys(account) == stored


# --------------------------------------------------------------------------- one file


def test_a_file_sealed_by_one_opens_in_the_other(rust_store, account, tmp_path) -> None:
    sealed_files.ensure_keypair(account)
    for size in _SIZES:
        data = _data(size)
        ours, theirs, copy = tmp_path / f"rust-{size}.bin", tmp_path / f"python-{size}.bin", tmp_path / f"copy-{size}.bin"
        for path in (ours, theirs):
            path.write_bytes(data)
            os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_001_000_000_000))
            assert rust_store.store("sealed_files", "sealed_for", path=str(path)) is None
            assert rust_store.store("sealed_files", "read_bytes", path=str(path)) == _b64(data)  # plain files read as they are
            assert rust_store.store("sealed_files", "open_file", path=str(path)) is False

        assert rust_store.store("sealed_files", "seal_file", path=str(ours), account=account) is True
        assert rust_store.store("sealed_files", "seal_file", path=str(ours), account=account) is False
        assert sealed_files.seal_file(theirs, account) is True
        assert ours.stat().st_size == theirs.stat().st_size and ours.read_bytes()[:4] == b"UDF1" != data[:4]
        for path in (ours, theirs):
            assert path.stat().st_mtime_ns == 1_600_000_001_000_000_000  # sealing keeps the file's times
            assert rust_store.store("sealed_files", "sealed_for", path=str(path)) == account == sealed_files.sealed_for(path)
            assert rust_store.store("sealed_files", "read_bytes", path=str(path), account=account) == _b64(data)
            assert sealed_files.read_bytes(path, account) == data
        rust_store.store("sealed_files", "open_to", path=str(theirs), dest=str(copy))
        assert copy.read_bytes() == data

        assert sealed_files.open_file(ours, account) is True  # Python opens what Rust sealed
        assert rust_store.store("sealed_files", "open_file", path=str(theirs), account=account) is True
        for path in (ours, theirs):
            assert path.read_bytes() == data and path.stat().st_mtime_ns == 1_600_000_001_000_000_000
    assert sorted(p.name for p in tmp_path.glob("*.udf-tmp")) == []


def test_a_cut_or_changed_file_never_opens(rust_store, account, tmp_path) -> None:
    sealed_files.ensure_keypair(account)
    path = tmp_path / "shot.png"
    path.write_bytes(_data(_MIB + 100))
    assert rust_store.store("sealed_files", "seal_file", path=str(path), account=account)
    whole = path.read_bytes()
    for damaged in (whole[:-1], whole[: len(whole) - 120], whole + b"x", whole[:200] + bytes([whole[200] ^ 1]) + whole[201:]):
        path.write_bytes(damaged)
        with pytest.raises(Exception):  # noqa: B017, PT011 — whatever the cipher raises
            sealed_files.read_bytes(path, account)
        assert _refused(rust_store, "read_bytes", path=str(path))
        assert _refused(rust_store, "open_file", path=str(path))
        assert path.read_bytes() == damaged, "a file that does not open is left as it was"


def test_another_accounts_file_stays_shut(rust_store, login, tmp_path) -> None:
    sealed_files.ensure_keypair("_local")
    path = tmp_path / "attachments" / "deep" / "shot.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(_data(5000))
    account = login(LOGIN)
    assert data_crypto.adk(account)
    assert rust_store.store("sealed_files", "seal_file", path=str(path), account="_local")  # anyone can seal for an account
    assert rust_store.store("sealed_files", "sealed_for", path=str(path)) == "_local" == sealed_files.sealed_for(path)
    with pytest.raises(sealed_files.WrongAccount):
        sealed_files.read_bytes(path)
    assert _refused(rust_store, "read_bytes", path=str(path)) == _WRONG
    assert _refused(rust_store, "open_file", path=str(path), account="_local") == _WRONG
    assert rust_store.store("sealed_files", "open_chat_folder", folder=str(tmp_path / "attachments"), account=account) == 0
    # This PC's own key opens them whoever is signed in.
    assert rust_store.store("sealed_files", "open_local_folder", folder=str(tmp_path / "attachments")) == 1
    assert path.read_bytes() == _data(5000)
    assert rust_store.store("sealed_files", "open_local_folder", folder=str(tmp_path / "attachments")) == 0
    assert rust_store.store("sealed_files", "open_local_folder", folder=str(tmp_path / "none")) == 0


# --------------------------------------------------------------------------- chat attachments


def _attachment(conv_id: str, name: str = "shot.png") -> Path:
    path = db.app_root() / "chats" / "projects" / "p1" / "conversations" / conv_id / "attachments" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("by", ["rust", "python"])
def test_chat_attachments_follow_who_is_signed_in(rust_store, login, by: str) -> None:
    chats.conv_save("p1", _doc("made-signed-out"), messages=[])
    sealed_files.ensure_keypair("_local")
    account = login(LOGIN)
    assert data_crypto.adk(account)
    chats.conv_save("p1", _doc("mine"), messages=[])
    sealed_files.ensure_keypair(account)
    theirs, mine, unsaved = _attachment("made-signed-out"), _attachment("mine"), _attachment("never-saved")
    for path in (theirs, mine, unsaved, _attachment("mine", "plain.txt")):
        path.write_bytes(_data(3000))
    assert sealed_files.seal_file(mine, account)  # as another account's sign-in left it

    def arrange() -> dict:
        if by == "rust":
            return rust_store.store("sealed_files", "arrange_chat_files")
        return sealed_files.arrange_chat_files(account)

    assert arrange() == {"attachments_opened": 1, "attachments_sealed": 1}
    assert mine.read_bytes() == _data(3000) and sealed_files.sealed_for(theirs) == "_local"
    assert sealed_files.sealed_for(unsaved) is None, "not a saved chat: left alone"
    assert _sql("SELECT value FROM meta WHERE key='chat_files_for'") == [(account,)]
    # Done once per sign-in, whoever did it.
    assert rust_store.store("sealed_files", "arrange_chat_files") == {} == sealed_files.arrange_chat_files(account)

    assert sealed_files.seal_file(mine, account)
    folder = str(mine.parent)
    if by == "rust":
        assert rust_store.store("sealed_files", "open_chat_folder", folder=folder, account=account) == 1
    else:
        assert sealed_files.open_chat_folder(mine.parent, account) == 1
    assert mine.read_bytes() == _data(3000)


@pytest.mark.parametrize("by", ["rust", "python"])
def test_leftovers_of_an_interrupted_open_are_swept(rust_store, by: str) -> None:
    root = db.app_root()
    (root / "snapshots").mkdir(exist_ok=True)
    old = [root / "a.udf-tmp", root / "snapshots" / "b.udf-tmp", root / "snapshots" / ".ducky-1.check.db-wal", _attachment("c", ".x.udf-tmp")]
    kept = [root / "fresh.udf-tmp", root / "snapshots" / "ducky-1.db", _attachment("c", "shot.png")]
    for path in old + kept:
        path.write_bytes(b"x")
    for path in old + kept[1:]:
        os.utime(path, (1_600_000_000, 1_600_000_000))
    if by == "rust":
        assert rust_store.store("sealed_files", "sweep_leftovers", older_than_s=600.0) == len(old)
    else:
        assert sealed_files.sweep_leftovers() == len(old)
    assert not any(path.exists() for path in old) and all(path.exists() for path in kept)
