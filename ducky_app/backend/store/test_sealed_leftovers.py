"""What else held chats and duckies in plaintext once they were sealed per account: free
pages of ducky.db (one rebuild), its backups from before, chat attachments of an account
not signed in, the old duckies settings, parked copies (``account_rows``, ``sealed_files``)."""

from __future__ import annotations

import json
import os
import time

import pytest

from backend.store import account_rows, db, sealed_files
from backend.store.repos import duckies
from backend.store.repos import settings as settings_repo
from backend.store.test_account_rows import (
    ALICE,
    BOB,
    ONLINE,
    WORDS,
    _database_from_before,
    _everything_stored,
    _fake_adk,
    _file_bytes,
    _Who,
    _write_chat,
)
from backend.uefn_plugins import data_crypto


@pytest.fixture
def who(monkeypatch: pytest.MonkeyPatch):
    data_crypto.reset_for_tests()
    ONLINE["on"] = True
    monkeypatch.setattr(data_crypto, "_fetch_adk", _fake_adk)
    yield _Who(monkeypatch)
    data_crypto.reset_for_tests()


def _age(path, seconds: float = 3600.0) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


# --------------------------------------------------------------------------- one sealed file


@pytest.mark.parametrize("size", [0, 1, sealed_files.CHUNK, 2 * sealed_files.CHUNK + 5])
def test_a_sealed_file_opens_whole_for_its_account_only(who: _Who, tmp_path, size: int) -> None:
    alice = who.be(ALICE)
    sealed_files.ensure_keypair(alice)
    data = os.urandom(size)
    path = tmp_path / "f.bin"
    path.write_bytes(data)
    _age(path)
    mtime = path.stat().st_mtime
    assert sealed_files.seal_file(path, alice) and not sealed_files.seal_file(path, alice)
    assert path.read_bytes()[:4] == sealed_files.MAGIC and sealed_files.sealed_for(path) == alice
    assert abs(path.stat().st_mtime - mtime) < 1, "a backup keeps its place in the snapshot order"
    assert sealed_files.read_bytes(path, alice) == data
    bob = who.be(BOB)
    with pytest.raises(sealed_files.WrongAccount):
        sealed_files.read_bytes(path, bob)
    who.be(ALICE)
    assert sealed_files.open_file(path, alice) and path.read_bytes() == data


def test_a_cut_or_changed_sealed_file_never_opens(who: _Who, tmp_path) -> None:
    alice = who.be(ALICE)
    sealed_files.ensure_keypair(alice)
    path = tmp_path / "f.bin"
    path.write_bytes(os.urandom(sealed_files.CHUNK + 10))
    sealed_files.seal_file(path, alice)
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) - 40])
    with pytest.raises(Exception):
        sealed_files.read_bytes(path, alice)
    changed = bytearray(whole)
    changed[-3] ^= 1
    path.write_bytes(bytes(changed))
    with pytest.raises(Exception):
        sealed_files.read_bytes(path, alice)
    path.write_bytes(whole)
    assert len(sealed_files.read_bytes(path, alice)) == sealed_files.CHUNK + 10


def test_the_keys_survive_restoring_a_backup_from_before_them(who: _Who, tmp_path) -> None:
    alice = who.be(ALICE)
    sealed_files.ensure_keypair(alice)
    path = tmp_path / "f.bin"
    path.write_bytes(b"pelican")
    sealed_files.seal_file(path, alice)
    conn = db.connect()
    with db.write_txn(conn):  # an older ducky.db swapped in: no keypair rows in it
        conn.execute("DELETE FROM meta WHERE key LIKE 'file_key_%'")
    sealed_files.forget()
    assert sealed_files.read_bytes(path, alice) == b"pelican"
    sealed_files.ensure_keypair(alice)  # writes the same pair back, never a new one
    assert sealed_files.read_bytes(path, alice) == b"pelican"


# --------------------------------------------------------------------------- chat attachments


def _attach(conv_id: str, text: bytes = b"the pelican plans") -> tuple[object, str]:
    from frontend.ui_web import conversation_attachments as ca

    saved = ca.save_chat_file(conv_id, "plans.txt", text)
    assert saved["ok"], saved
    from pathlib import Path

    return Path(saved["path"]), Path(saved["path"]).name


def test_attachments_are_sealed_while_their_owner_is_away(who: _Who) -> None:
    alice = who.be(ALICE)
    _write_chat("chat-a")
    path, _name = _attach("chat-a")
    account_rows.seal_pending(alice, pause_s=0)
    assert path.read_bytes() == b"the pelican plans", "plain while Alice is here: agents read it by path"

    bob = who.be(BOB)
    out = account_rows.seal_pending(bob, pause_s=0)
    assert out.get("attachments_sealed") == 1 and sealed_files.sealed_for(path) == alice
    assert b"pelican" not in path.read_bytes()

    who.be(ALICE)
    assert account_rows.seal_pending(alice, pause_s=0).get("attachments_opened") == 1
    assert path.read_bytes() == b"the pelican plans"


def test_an_attachment_read_before_the_pass_is_opened_on_the_spot(who: _Who) -> None:
    from frontend.ui_web import conversation_attachments as ca
    from frontend.ui_web.chats.store import get_conversations_dir

    alice = who.be(ALICE)
    _write_chat("chat-a")
    path, name = _attach("chat-a")
    account_rows.seal_pending(alice, pause_s=0)
    account_rows.seal_pending(who.be(BOB), pause_s=0)
    assert sealed_files.is_sealed_file(path)
    who.be(ALICE)  # no pass yet
    row = ca.hydrate_attachment_dict({"kind": "file", "path": f"attachments/{name}"}, "chat-a", get_conversations_dir())
    assert row["text"] == "the pelican plans" and path.read_bytes() == b"the pelican plans"


def test_an_owner_who_never_signed_in_here_again_is_sealed_when_they_do(who: _Who) -> None:
    alice = who.be(ALICE)
    _write_chat("chat-a")
    path, _name = _attach("chat-a")
    # Her file keys don't exist yet (made by a build from before them).
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM meta WHERE key LIKE 'file_key_%'")
    (db.app_root() / sealed_files.KEYS_FILE).unlink()
    account_rows.forget()
    bob = who.be(BOB)
    assert account_rows.seal_pending(bob, pause_s=0).get("attachments_left") == 1
    assert path.read_bytes() == b"the pelican plans"
    account_rows.seal_pending(who.be(ALICE), pause_s=0)
    account_rows.seal_pending(who.be(BOB), pause_s=0)
    assert sealed_files.sealed_for(path) == alice


# --------------------------------------------------------------------------- a database from before


def _with_old_extras(monkeypatch) -> None:
    """The database from before, plus the duckies settings and a deleted row whose bytes
    are still in a free page."""
    _database_from_before(monkeypatch)
    conn = db.connect()
    with db.write_txn(conn):
        conn.executemany(
            "INSERT INTO settings(key, value, updated) VALUES (?, ?, 0)",
            [("agent_profiles", json.dumps([{"id": "custom-old", "name": "Pelican builder"}])),
             ("archived_agent_profile_ids", json.dumps(["material-artist"]))],
        )
        conn.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('imported:duckies', '{}', 0)")
    conn.execute("PRAGMA secure_delete=OFF")
    with db.write_txn(conn):
        conn.execute("CREATE TABLE scratch_old(t TEXT)")
        conn.executemany("INSERT INTO scratch_old VALUES (?)", [("harbour pelican " * 40,)] * 2000)
    with db.write_txn(conn):
        conn.execute("DROP TABLE scratch_old")
    settings_repo.forget_snapshot()


def test_the_old_duckies_settings_go_sealed_into_meta(who: _Who, monkeypatch) -> None:
    who.key = ALICE
    _with_old_extras(monkeypatch)
    alice = account_rows.current()
    out = account_rows.seal_pending(alice, pause_s=0)
    assert out["settings"] == 2
    conn = db.connect()
    assert conn.execute(
        "SELECT count(*) FROM settings WHERE key IN ('agent_profiles', 'archived_agent_profile_ids')"
    ).fetchone()[0] == 0
    kept = conn.execute("SELECT value FROM meta WHERE key=?", ("legacy_duckies_settings:" + alice,)).fetchone()[0]
    assert json.loads(json.loads(account_rows.open_text(kept, alice))["agent_profiles"])[0]["name"] == "Pelican builder"
    assert duckies.get("custom-old")["data"] == {"name": "Pelican builder"}, "the duckies table still has them"
    # A stale copy in memory never writes them back.
    assert settings_repo.save_fields({"agent_profiles": [{"id": "x", "name": "Stale pelican"}]}, {"agent_profiles": []}) == []
    for word in WORDS:
        assert word not in _everything_stored()


def test_one_rebuild_after_sealing_leaves_no_old_plaintext_in_the_file(who: _Who, monkeypatch) -> None:
    who.key = ALICE
    _with_old_extras(monkeypatch)
    alice = account_rows.current()
    assert b"pelican" in _file_bytes()
    out = account_rows.seal_pending(alice, pause_s=0)
    assert out["rebuild_next_start"] == 1
    marker = db.db_path().parent / db.VACUUM_PENDING_NAME
    assert marker.is_file()
    assert b"pelican" in _file_bytes(), "sealed rows, but the old bytes are still in free pages"

    # An IDE/agent bridge's start never rebuilds, nor counts as a wait.
    db.reset_for_tests()
    db.connect()
    assert json.loads(marker.read_text())["waits"] == 0 and b"pelican" in _file_bytes()
    # The panel's start: while another process still has ducky.db open it waits, however often.
    monkeypatch.setattr(db, "_rebuild_here", True)
    holder = open(db.db_path(), "rb")
    try:
        for _ in range(7):
            db.reset_for_tests()
            db.connect()
        if os.name == "nt":
            assert json.loads(marker.read_text())["waits"] == 7 and marker.is_file()
    finally:
        holder.close()
    db.reset_for_tests()
    db.connect()
    assert not marker.exists()
    raw = _file_bytes()
    assert b"pelican" not in raw and b"harbour" not in raw
    assert account_rows.seal_pending(alice, pause_s=0) == {}, "nothing left, no second rebuild"
    assert not marker.exists()


def test_the_rebuild_never_waits_on_this_process_own_checkpoint_thread(monkeypatch) -> None:
    """The wal-checkpoint thread's short-lived connection, open as the panel starts, is
    this process, not another one: the rebuild still runs that start."""
    import sqlite3
    import threading

    db.reset_for_tests()
    db.connect()
    db.request_vacuum()
    marker = db.db_path().parent / db.VACUUM_PENDING_NAME
    db.reset_for_tests()
    monkeypatch.setattr(db, "_rebuild_here", True)
    opened, done = threading.Event(), threading.Event()

    def checkpoint_thread() -> None:
        with db._ckpt_open:
            conn = sqlite3.connect(str(db.db_path()))
            opened.set()
            time.sleep(0.3)
            conn.close()
        done.set()

    worker = threading.Thread(target=checkpoint_thread)
    worker.start()
    assert opened.wait(5)
    db.connect()
    worker.join(5)
    assert done.is_set()
    assert not marker.exists(), "rebuilt this start, not counted as a wait"
    db.reset_for_tests()


def test_a_handle_open_only_for_a_moment_is_not_another_process(monkeypatch) -> None:
    seen = iter([True, False])
    monkeypatch.setattr(db, "_held_elsewhere", lambda _path: next(seen))
    assert db._held_elsewhere_settled(db.db_path(), pause_s=0) is False
    monkeypatch.setattr(db, "_held_elsewhere", lambda _path: True)
    assert db._held_elsewhere_settled(db.db_path(), pause_s=0) is True


def test_backups_from_before_are_sealed_for_the_account_the_rows_went_to(who: _Who, monkeypatch) -> None:
    from frontend import store_admin

    who.key = ALICE
    _database_from_before(monkeypatch)
    alice = account_rows.current()
    [snap] = list(db.snapshot_dir().glob("ducky-*-pre-migrate-v23.db"))
    replaced = db.db_path().parent / (db.DB_NAME + ".replaced-20261001-000000-1")
    replaced.write_bytes(snap.read_bytes())
    for p in (snap, replaced):
        _age(p)
    assert b"pelican" in snap.read_bytes()
    out = account_rows.seal_pending(alice, pause_s=0)
    assert out["backups"] == 2
    for p in (snap, replaced):
        assert sealed_files.sealed_for(p) == alice and b"pelican" not in p.read_bytes()
    # Alice can still check and restore it; Bob can't.
    assert db.check_db_file(snap) == "ok"
    assert store_admin.stage_restore(snap.name)["ok"]
    store_admin.cancel_restore()
    who.be(BOB)
    staged = store_admin.stage_restore(snap.name)
    assert not staged["ok"] and "another Ducky account" in staged["error"]
    assert not (db.db_path().parent / db.RESTORE_PENDING_NAME).exists()
    # The snapshot taken once everything was sealed stays plain, and so does a later one.
    who.be(ALICE)
    [kept] = list(db.snapshot_dir().glob("ducky-*-sealed.db"))
    assert out["plain_snapshot"] == 1 and not sealed_files.is_sealed_file(kept)
    fresh = db.snapshot(label="after")
    _age(fresh, 120)
    _age(kept, 120)
    account_rows.seal_pending(alice, pause_s=0)
    assert not sealed_files.is_sealed_file(fresh) and not sealed_files.is_sealed_file(kept)


def test_a_backup_too_fresh_to_seal_is_sealed_once_settled_not_at_the_next_start(who: _Who, monkeypatch) -> None:
    who.key = ALICE
    monkeypatch.setattr(sealed_files, "SETTLE_S", 3.0)
    _database_from_before(monkeypatch)
    alice = account_rows.current()
    [snap] = list(db.snapshot_dir().glob("ducky-*-pre-migrate-v23.db"))
    os.utime(snap)  # just written, as when the pass right after the upgrade is quick
    out = account_rows.seal_pending(alice, pause_s=0)
    assert "backups" not in out and not sealed_files.is_sealed_file(snap)
    deadline = time.time() + 15
    while not sealed_files.is_sealed_file(snap) and time.time() < deadline:
        time.sleep(0.2)
    assert sealed_files.sealed_for(snap) == alice and b"pelican" not in snap.read_bytes()


def test_a_restored_sealed_backup_goes_to_its_owner_whoever_is_signed_in(who: _Who, monkeypatch) -> None:
    from frontend import store_admin

    who.key = ALICE
    _database_from_before(monkeypatch)
    alice = account_rows.current()
    [snap] = list(db.snapshot_dir().glob("ducky-*-pre-migrate-v23.db"))
    _age(snap)
    account_rows.seal_pending(alice, pause_s=0)
    assert sealed_files.sealed_for(snap) == alice
    assert store_admin.stage_restore(snap.name)["ok"]
    # Bob signs in before the restart that swaps it in: the old rows still go to Alice.
    who.key = BOB
    db.reset_for_tests()
    bob = account_rows.current()
    owners = {a for (a,) in db.connect().execute("SELECT DISTINCT account_id FROM conversations")}
    assert owners == {alice} and bob != alice


def test_a_corrupt_database_restores_from_a_plain_snapshot_first(who: _Who, monkeypatch) -> None:
    who.key = ALICE
    _database_from_before(monkeypatch)
    alice = account_rows.current()
    [old] = list(db.snapshot_dir().glob("ducky-*.db"))
    _age(old, 7200)
    account_rows.seal_pending(alice, pause_s=0)
    assert sealed_files.is_sealed_file(old)
    [kept] = list(db.snapshot_dir().glob("ducky-*-sealed.db"))
    _age(kept, 7300)  # older than the sealed one, still tried first
    assert db.restore_snapshot() == kept
    db.reset_for_tests()
    assert db.integrity_check(db.connect()) == "ok"


def test_with_only_sealed_snapshots_one_is_opened_before_the_swap(who: _Who, monkeypatch) -> None:
    who.key = ALICE
    _database_from_before(monkeypatch)
    alice = account_rows.current()
    [old] = list(db.snapshot_dir().glob("ducky-*.db"))
    _age(old)
    account_rows.seal_pending(alice, pause_s=0)
    for plain in db.snapshot_dir().glob("ducky-*-sealed.db"):
        plain.unlink()
    started = time.monotonic()
    assert db.restore_snapshot() == old
    assert time.monotonic() - started < 30, "never waits on its own open lock"
    db.reset_for_tests()
    assert db.integrity_check(db.connect()) == "ok"
    assert not list(db.snapshot_dir().glob("*" + sealed_files.TMP_SUFFIX))


def test_parked_copies_are_sealed_for_the_account_that_claimed_them(who: _Who) -> None:
    alice = who.be(ALICE)
    duckies.save("material-artist", "bundled", {"name": "Alice's edit"})
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO duckies(id, ducky_id, kind, data, created, updated) VALUES "
            "('material-artist', 'material-artist', 'bundled', '{\"name\": \"stale pelican\"}', 0, 0)"
        )
    from backend.uefn_plugins.scopes import forget_claims

    forget_claims()  # this process found nothing left to claim already; the next start looks again
    account_rows.current()
    account_rows.seal_pending(alice, pause_s=0)
    [(data,)] = conn.execute(
        "SELECT data FROM duckies WHERE substr(account_id, 1, 8) = ?", (account_rows.PARKED,)
    ).fetchall()
    assert data.startswith("enc1:") and "pelican" in account_rows.open_text(data, alice)


# --------------------------------------------------------------------------- races and leftovers


def test_a_pass_for_an_account_no_longer_signed_in_touches_no_file(who: _Who) -> None:
    alice = who.be(ALICE)
    _write_chat("chat-a")
    a_path, _ = _attach("chat-a")
    account_rows.seal_pending(alice, pause_s=0)
    bob = who.be(BOB)
    _write_chat("chat-b", words="otter lagoon")
    b_path, _ = _attach("chat-b", b"the otter plans")
    account_rows.seal_pending(bob, pause_s=0)
    assert sealed_files.sealed_for(a_path) == alice and not sealed_files.is_sealed_file(b_path)
    # A pass that started for Alice and outlived the switch to Bob.
    assert sealed_files.arrange_chat_files(alice) == {}
    assert account_rows.seal_pending(alice, pause_s=0).get("attachments_sealed") is None
    assert b_path.read_bytes() == b"the otter plans", "the signed-in account's files stay plain"


def test_a_pass_cut_short_never_looks_done(who: _Who, monkeypatch) -> None:
    alice = who.be(ALICE)
    _write_chat("chat-a")
    a_path, _ = _attach("chat-a")
    account_rows.seal_pending(alice, pause_s=0)
    bob = who.be(BOB)
    real = sealed_files.seal_file

    def busy(path, *args, **kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(sealed_files, "seal_file", busy)
    assert account_rows.seal_pending(bob, pause_s=0).get("attachments_left") == 1
    monkeypatch.setattr(sealed_files, "seal_file", real)
    # Alice back, then Bob again: the pass runs again and seals what was left.
    account_rows.seal_pending(who.be(ALICE), pause_s=0)
    account_rows.seal_pending(who.be(BOB), pause_s=0)
    assert sealed_files.sealed_for(a_path) == alice


def test_a_still_sealed_attachment_is_read_in_memory_never_as_ciphertext(who: _Who, monkeypatch) -> None:
    from frontend.ui_web import conversation_attachments as ca
    from frontend.ui_web.chats.store import get_conversations_dir

    alice = who.be(ALICE)
    _write_chat("chat-a")
    path, name = _attach("chat-a")
    account_rows.seal_pending(alice, pause_s=0)
    account_rows.seal_pending(who.be(BOB), pause_s=0)
    who.be(ALICE)
    monkeypatch.setattr(sealed_files, "open_chat_folder", lambda folder, account: 0)  # another thread has it
    row = ca.hydrate_attachment_dict({"kind": "file", "path": f"attachments/{name}"}, "chat-a", get_conversations_dir())
    assert row["text"] == "the pelican plans" and sealed_files.is_sealed_file(path)
    who.be(BOB)
    row = ca.hydrate_attachment_dict({"kind": "file", "path": f"attachments/{name}"}, "chat-a", get_conversations_dir())
    assert "text" not in row


def test_plain_copies_left_by_a_crash_are_swept(who: _Who) -> None:
    alice = who.be(ALICE)
    _write_chat("chat-a")
    path, _ = _attach("chat-a")
    left = path.with_name(f".{path.name}.999.1{sealed_files.TMP_SUFFIX}")
    left.write_bytes(b"the pelican plans")
    check = db.snapshot_dir() / ".ducky-x.999.check.db"
    check.parent.mkdir(parents=True, exist_ok=True)
    check.write_bytes(b"pelican")
    fresh = path.with_name(f".{path.name}.998.1{sealed_files.TMP_SUFFIX}")
    fresh.write_bytes(b"being written now")
    for p in (left, check):
        _age(p)
    assert account_rows.seal_pending(alice, pause_s=0)["leftovers_removed"] == 2
    assert not left.exists() and not check.exists() and fresh.exists()


def test_the_file_keys_are_made_as_soon_as_the_data_key_is_here(who: _Who) -> None:
    alice = who.be(ALICE)
    assert sealed_files.public_key(alice) is None
    account_rows.ready(alice)
    assert sealed_files.public_key(alice) is not None


def test_a_snapshot_sealed_for_one_account_keeps_none_of_anothers_records(who: _Who, monkeypatch) -> None:
    from backend.store.repos import search as search_repo

    who.key = ALICE
    _database_from_before(monkeypatch)
    alice = account_rows.current()
    bob_scope = "a_bob~personal"
    search_repo.upsert_one(search_repo.Doc(
        doc_key=f"record:wiki:{bob_scope}:p", source_key=f"record:wiki:{bob_scope}", kind="record",
        title="Bob otter page", body="otter lagoon", scope=bob_scope, plugin_id="wiki", content_hash="h",
    ), b"\0" * 8, "m1")
    snap = db.snapshot(label="before")
    _age(snap)
    account_rows.seal_pending(alice, pause_s=0)
    assert sealed_files.sealed_for(snap) == alice
    plain = snap.with_name("opened.db")
    sealed_files.open_to(snap, plain)
    assert b"otter" not in plain.read_bytes()


def test_a_restore_swap_outlasts_a_handle_open_for_a_moment(tmp_path, monkeypatch) -> None:
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    real, fails = type(src).replace, iter([True, True])

    def flaky(self, target):
        if next(fails, False):
            raise PermissionError(32, "held by a scanner")
        return real(self, target)

    monkeypatch.setattr(type(src), "replace", flaky)
    db._replace_retrying(src, dst, pause_s=0)
    assert dst.read_text() == "x" and not src.exists()
    src.write_text("y")
    monkeypatch.setattr(type(src), "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(32, "held")))
    with pytest.raises(PermissionError):
        db._replace_retrying(src, dst, pause_s=0)
