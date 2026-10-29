"""Chats and duckies belong to the signed-in Ducky account and are sealed for it (migration 0024)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib

import pytest

from backend.store import account_rows, db
from backend.store.repos import chats, duckies, ducky_mirrors
from backend.uefn_plugins import data_crypto, scopes
from backend.uefn_plugins.scopes import LOCAL, account_id_for

PROJECT = "proj_accounts"
OTHER_PROJECT = "proj_other"
ALICE = "https://uefnducky.org|alice@example.com"
BOB = "https://uefnducky.org|bob@example.com"
CAROL = "https://uefnducky.org|carol@example.com"
WORDS = ("pelican", "harbour")  # what Alice wrote; never in the database as text
ONLINE = {"on": True}


class _Who:
    """Who is signed in on this PC (read at call time, like the real login row)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from frontend import duckyos_account

        real = duckyos_account.account_key
        self.key = ""
        monkeypatch.setattr(duckyos_account, "account_key", lambda blob=None: self.key if blob is None else real(blob))

    def be(self, key: str) -> str:
        self.key = key
        return account_rows.current()


def _fake_adk() -> bytes:
    """The server's account data key: stable per account, only while online."""
    from frontend import duckyos_account

    if not ONLINE["on"]:
        raise OSError("offline")
    return hashlib.sha256(b"adk/" + duckyos_account.account_key().encode()).digest()


@pytest.fixture
def who(monkeypatch: pytest.MonkeyPatch) -> _Who:
    data_crypto.reset_for_tests()
    ONLINE["on"] = True
    monkeypatch.setattr(data_crypto, "_fetch_adk", _fake_adk)
    yield _Who(monkeypatch)
    data_crypto.reset_for_tests()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _transcript(words: str) -> list[dict]:
    return [
        {"role": "user", "text": f"tell me about the {words}", "content": f"tell me about the {words}", "ts": 1.0},
        {"role": "assistant", "content": f"here is the {words}", "ts": 2.0, "blocks": [
            {"type": "tool_call", "id": "t1", "name": "workspace_read_file", "arguments": {"path": "notes.md"},
             "result": {"ok": True, "text": f"{words} notes\n" * 1500}},
        ]},
    ]


def _write_chat(conv_id: str, words: str = " ".join(WORDS), project: str = PROJECT) -> list[dict]:
    messages = _transcript(words)
    chats.conv_save(project, {
        "id": conv_id, "title": f"Chat about {words}", "created": 1.0, "updated": 2.0,
        "ducky_name": f"Ducky {words}", "context_summary": f"we talked about {words}",
    }, messages=messages)
    chats.folders_replace(project, [
        {"id": "archive", "name": "Archive"},
        {"id": f"folder-{conv_id}", "name": f"Folder {words}"},
    ])
    return messages


def _ids(project: str = PROJECT) -> set[str]:
    return {c["id"] for c in chats.conv_list(project)}


def _everything_stored() -> str:
    """Every value of every table as text (payloads inflated where they still can be)."""
    conn = db.connect()
    out: list[str] = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in conn.execute(f'SELECT * FROM "{table}"').fetchall():
            for value in row:
                if isinstance(value, bytes):
                    try:
                        value = zlib.decompress(value)
                    except zlib.error:
                        pass
                    value = value.decode("utf-8", "replace")
                out.append(str(value))
    return "\n".join(out).lower()


def _file_bytes() -> bytes:
    conn = db.connect()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return db.db_path().read_bytes()


# --------------------------------------------------------------------------- one account, then another


def test_another_account_lists_and_reads_none_of_them(who: _Who) -> None:
    who.be(ALICE)
    messages = _write_chat("chat-a")
    duckies.save("custom-a", "custom", {"name": "Alice's ducky", "system_prompt": "pelican harbour"})
    duckies.save("material-artist", "bundled", {"name": "Alice's edit"})

    who.be(BOB)
    assert chats.conv_list(PROJECT) == [] and chats.conv_list_all_projects() == []
    assert chats.conv_get("chat-a") is None and chats.messages_get("chat-a") == []
    assert chats.conv_project_id("chat-a") is None and chats.conv_message_summary("chat-a") == (0, False)
    assert chats.folders_get(PROJECT) == [] and chats.folders_list_all_projects() == []
    assert chats.folder_project_id("folder-chat-a") is None
    assert chats.search_messages(PROJECT, "pelican") == []
    assert duckies.list_all(include_deleted=True) == []
    assert duckies.get("custom-a") is None and duckies.get("material-artist") is None
    assert duckies.history("custom-a") == []

    # Bob can't write over, move or delete Alice's chat either.
    assert chats.conv_save(PROJECT, {"id": "chat-a", "title": "mine now"}, messages=[]).get("skipped") == 1
    assert chats.conv_delete("chat-a") is False
    chats.move_to_project(OTHER_PROJECT, [], [("chat-a", "")])

    who.be(ALICE)
    assert _json(chats.messages_get("chat-a")) == _json(messages)
    doc = chats.conv_get("chat-a", project_id=PROJECT, with_messages=False)
    assert doc["title"] == "Chat about pelican harbour" and doc["context_summary"] == "we talked about pelican harbour"
    assert duckies.get("custom-a")["data"]["system_prompt"] == "pelican harbour"
    assert chats.search_messages(PROJECT, "pelican")[0]["conv_id"] == "chat-a"


def test_the_rows_are_ciphertext_another_account_cannot_open(who: _Who) -> None:
    alice = who.be(ALICE)
    _write_chat("chat-a")
    duckies.save("custom-a", "custom", {"name": "Alice's ducky", "system_prompt": "pelican harbour"})
    duckies.save("custom-a", "custom", {"name": "Alice's ducky", "system_prompt": "pelican harbour v2"})
    conn = db.connect()

    title, state = conn.execute("SELECT title, state FROM conversations WHERE id='chat-a'").fetchone()
    assert title.startswith("enc1:") and state.startswith("enc1:")
    assert all(body.startswith("enc1:") for (body,) in conn.execute("SELECT body FROM messages"))
    assert all(bytes(d).startswith(b"UDE1") for (d,) in conn.execute("SELECT data FROM message_parts"))
    assert conn.execute("SELECT count(*) FROM message_parts").fetchone()[0] == 1
    assert all(n.startswith("enc1:") for (n,) in conn.execute("SELECT name FROM folders"))
    assert all(d.startswith("enc1:") for (d,) in conn.execute("SELECT data FROM duckies"))
    assert all(d.startswith("enc1:") for (d,) in conn.execute("SELECT data FROM duckies_history"))
    assert {a for (a,) in conn.execute("SELECT account_id FROM messages")} == {alice}

    # Not in any table, the search index included, and not in the file itself.
    stored = _everything_stored()
    raw = _file_bytes().lower()
    for word in WORDS:
        assert word not in stored and word.encode() not in raw

    # With the raw row in hand, another account or signed-out can't open it.
    bob = who.be(BOB)
    with pytest.raises(OSError):
        data_crypto.open_text(title, bob)
    who.be("")
    with pytest.raises(OSError):
        data_crypto.open_text(title, LOCAL)


def test_switching_accounts_swaps_chats_and_duckies(who: _Who) -> None:
    from frontend import agent_profiles as ap

    who.be(ALICE)
    _write_chat("chat-a", "alice words")
    duckies.save("material-artist", "bundled", {"name": "Alice's edit"})
    custom = ap.save_agent_profile({"name": "Alice custom"})["id"]
    who.be(BOB)
    _write_chat("chat-b", "bob words")  # the same project, an Archive folder of its own
    duckies.save("material-artist", "bundled", {"name": "Bob's edit"})
    shipped = {t["id"] for t in ap.list_bundled_agent_profile_templates()}

    for key, chat, edit in ((ALICE, "chat-a", "Alice's edit"), (BOB, "chat-b", "Bob's edit"), (ALICE, "chat-a", "Alice's edit")):
        who.be(key)
        assert _ids() == {chat}
        assert {f["id"] for f in chats.folders_get(PROJECT)} == {"archive", f"folder-{chat}"}
        assert duckies.get("material-artist")["data"]["name"] == edit
        listed = {p["id"] for p in ap.list_agent_profiles()}
        assert shipped <= listed, "shipped duckies are there for every account"
        assert (custom in listed) is (key == ALICE)
        assert ap.get_agent_profile("material-artist")["name"] == edit


def test_signed_out_sees_only_the_local_chats(who: _Who) -> None:
    assert who.be("") == LOCAL
    _write_chat("chat-local", "local words")
    duckies.save("custom-local", "custom", {"name": "Local ducky"})
    who.be(ALICE)
    _write_chat("chat-a")
    assert _ids() == {"chat-a"} and duckies.get("custom-local") is None

    who.be("")
    assert _ids() == {"chat-local"} and duckies.get("custom-local")["data"]["name"] == "Local ducky"
    assert chats.search_messages(PROJECT, "pelican") == []
    title = db.connect().execute("SELECT title FROM conversations WHERE id='chat-local'").fetchone()[0]
    assert title.startswith("enc1:"), "signed out is sealed too (this Windows user only)"


def test_no_key_yet_locks_and_writes_nothing(who: _Who) -> None:
    ONLINE["on"] = False
    who.be(CAROL)
    with pytest.raises(data_crypto.Locked):
        chats.conv_save(PROJECT, {"id": "chat-c", "title": "offline"}, messages=_transcript("offline words"))
    with pytest.raises(data_crypto.Locked):
        chats.folders_replace(PROJECT, [{"id": "f1", "name": "offline folder"}])
    with pytest.raises(data_crypto.Locked):
        duckies.save("custom-c", "custom", {"name": "offline ducky"})
    conn = db.connect()
    for table in ("conversations", "messages", "folders", "duckies", "duckies_history"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table

    ONLINE["on"] = True
    data_crypto._LAST_TRY.clear()  # the next try a minute later
    chats.conv_save(PROJECT, {"id": "chat-c", "title": "online"}, messages=_transcript("online words"))
    assert _ids() == {"chat-c"}


def test_a_chat_of_another_account_serves_none_of_its_files(who: _Who) -> None:
    from frontend.ui_web import conversation_attachments as ca

    who.be(ALICE)
    _write_chat("chat-a")
    assert ca._signed_in_account_may_open("chat-a")
    assert ca._signed_in_account_may_open("chat-new"), "a chat not saved yet is the current account's"
    who.be(BOB)
    assert not ca._signed_in_account_may_open("chat-a")
    with pytest.raises(ValueError):
        ca.resolve_chat_attachment_path("chat-a", "capture.png")


def test_an_account_switch_drops_the_old_accounts_rows_in_every_window(who: _Who, monkeypatch) -> None:
    from frontend import duckyos_account
    from frontend.ui_web import agent_modes

    alice = who.be(ALICE)
    _write_chat("chat-a")
    pushed: list[dict] = []
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda push=None: pushed.append)
    monkeypatch.setattr(agent_modes, "push_ui_event", pushed.append)
    who.be(BOB)
    duckyos_account._note_login_change(
        {"device_key": "d1", "email": "alice@example.com", "base_url": "https://uefnducky.org"},
        {"device_key": "d2", "email": "bob@example.com", "base_url": "https://uefnducky.org"},
    )
    changed = [e for e in pushed if e.get("type") == "chats_changed"]
    assert changed and changed[0]["open"] is False
    assert changed[0]["removed_conv_ids"] == ["chat-a"]
    assert set(changed[0]["removed_folder_ids"]) == {"archive", "folder-chat-a"}
    assert any(e.get("type") == "duckyos_account_changed" for e in pushed)
    assert account_rows.owned_ids(alice)[0] == ["chat-a"]


# --------------------------------------------------------------------------- a database from before


def _database_from_before(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """A ducky.db at schema 23 (no accounts, everything plaintext), written the old way.
    Returns its row counts."""
    real_files = db.migration_files
    old_files = [(n, p) for n, p in real_files() if n <= 23]
    db.reset_for_tests()
    monkeypatch.setattr(db, "migration_files", lambda: old_files)
    conn = db.connect()
    assert db.user_version(conn) == 23
    result = {"ok": True, "text": "pelican notes\n" * 1500}
    part = json.dumps(result, ensure_ascii=False)
    digest = hashlib.sha256(part.encode()).hexdigest()
    envelope = {"m": {"role": "assistant", "content": "read harbour", "blocks": [{"name": "read", "result": None}]},
                "x": [["blocks", 0, "result", digest]]}
    inline = {"role": "assistant", "content": "big harbour", "blocks": [{"name": "read", "result": result}]}
    with db.write_txn(conn):
        for conv_id, project in (("old-1", PROJECT), ("old-2", OTHER_PROJECT)):
            conn.execute(
                "INSERT INTO conversations(id, project_id, folder_id, title, updated, state) VALUES (?, ?, 'f-old', ?, 5, ?)",
                (conv_id, project, f"Old pelican {conv_id}", json.dumps({"ducky_name": "Harbour ducky"})),
            )
        user = {"role": "user", "text": "about the pelican", "ts": 1.0}
        rows = [  # (chat, seq, role, stored body, fmt, search text)
            ("old-1", 0, "user", user, 0, chats.message_search_text(user)),
            ("old-1", 1, "assistant", envelope, 1, chats.message_search_text(inline)),
            ("old-2", 0, "assistant", inline, 0, chats.message_search_text(inline)),
        ]
        for conv_id, seq, role, message, fmt, text in rows:
            body = json.dumps(message, ensure_ascii=False)
            cur = conn.execute(
                "INSERT INTO messages(conv_id, seq, hash, role, text, body, fmt) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, chats._hash(body), role, text, body, fmt),
            )
            if fmt:
                conn.execute("INSERT INTO message_parts(hash, data, size, created) VALUES (?, ?, ?, 0)",
                             (digest, zlib.compress(part.encode()), len(part)))
                conn.execute("INSERT INTO message_part_refs(message_id, hash) VALUES (?, ?)", (cur.lastrowid, digest))
        conn.executemany(
            "INSERT INTO folders(project_id, id, name) VALUES (?, ?, ?)",
            [(PROJECT, "archive", "Archive"), (PROJECT, "f-old", "Pelican folder"), (OTHER_PROJECT, "archive", "Archive")],
        )
        conn.executemany(
            "INSERT INTO duckies(id, kind, data, archived, deleted, created, updated) VALUES (?, ?, ?, ?, 0, 1, 1)",
            [("custom-old", "custom", json.dumps({"name": "Pelican builder"}), 0),
             ("material-artist", "bundled", json.dumps({"name": "Harbour artist"}), 1)],
        )
        conn.execute(
            "INSERT INTO duckies_history(id, kind, data, archived, deleted, saved) VALUES "
            "('custom-old', 'custom', ?, 0, 0, 0)", (json.dumps({"name": "Pelican builder v0"}),),
        )
    counts = _counts(conn)
    db.reset_for_tests()
    monkeypatch.setattr(db, "migration_files", real_files)
    return counts


_TABLES = ("conversations", "messages", "message_parts", "folders", "duckies", "duckies_history")


def _counts(conn) -> dict[str, int]:
    return {t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in _TABLES}


def _old_rows_read_back() -> None:
    assert _ids(PROJECT) == {"old-1"} and _ids(OTHER_PROJECT) == {"old-2"}
    one = chats.conv_get("old-1", with_messages=True)
    assert one["title"] == "Old pelican old-1" and one["ducky_name"] == "Harbour ducky"
    assert one["messages"][0]["text"] == "about the pelican"
    assert one["messages"][1]["blocks"][0]["result"]["text"].startswith("pelican notes")
    assert chats.messages_get("old-2")[0]["blocks"][0]["result"]["text"].startswith("pelican notes")
    assert {f["id"]: f["name"] for f in chats.folders_get(PROJECT)} == {"archive": "Archive", "f-old": "Pelican folder"}
    assert duckies.get("custom-old")["data"] == {"name": "Pelican builder"}
    assert duckies.get("material-artist")["archived"] is True
    assert [h["data"] for h in duckies.history("custom-old")] == [{"name": "Pelican builder v0"}]
    assert chats.search_messages(PROJECT, "pelican")


def test_a_database_from_before_keeps_every_chat_and_ducky(who: _Who, monkeypatch) -> None:
    who.key = ALICE  # signed in when the app first opens the upgraded database
    before = _database_from_before(monkeypatch)
    conn = db.connect()
    assert db.user_version(conn) == db.head_version()
    alice = account_rows.current()
  # the first account to look claims them, all of them
    assert {a for (a,) in conn.execute("SELECT DISTINCT account_id FROM conversations")} == {alice}
    assert _counts(conn) == before
    _old_rows_read_back()  # readable at once, before anything is sealed

    done = account_rows.seal_pending(alice, pause_s=0)
    assert done["conversations"] == 2 and done["folders"] == 3 and done["duckies"] == 2 and done["sealed"] == 3
    assert _counts(conn) == {**before, "message_parts": before["message_parts"] + 1}  # the big inline row went compact
    _old_rows_read_back()
    stored = _everything_stored()
    for word in WORDS:
        assert word not in stored
    assert account_rows.seal_pending(alice, pause_s=0) == {}, "nothing left on the next boot"

    who.be(BOB)
    assert _ids(PROJECT) == set() and duckies.get("custom-old") is None
    assert chats.search_messages(PROJECT, "pelican") == []


def test_a_database_from_before_opened_signed_out_goes_to_local(who: _Who, monkeypatch) -> None:
    before = _database_from_before(monkeypatch)
    assert who.be("") == LOCAL
    _old_rows_read_back()
    account_rows.seal_pending(LOCAL, pause_s=0)
    _old_rows_read_back()
    assert _counts(db.connect()) == {**before, "message_parts": before["message_parts"] + 1}
    who.be(ALICE)
    assert _ids(PROJECT) == set() and chats.folders_get(PROJECT) == [] and duckies.list_all(include_deleted=True) == []


def test_an_old_copy_the_account_already_has_is_kept_aside(who: _Who) -> None:
    alice = who.be(ALICE)
    duckies.save("material-artist", "bundled", {"name": "Alice's edit"})
    conn = db.connect()
    # An old row turning up late (an older build's process), for a ducky Alice already edited.
    with db.write_txn(conn):
        conn.execute(
            "INSERT INTO duckies(id, ducky_id, kind, data, created, updated) VALUES "
            "('material-artist', 'material-artist', 'bundled', '{\"name\": \"stale\"}', 0, 0)"
        )
    scopes.forget_claims()  # this process found nothing left to claim already; the next start looks again
    account_rows.current()
    assert duckies.get("material-artist")["data"]["name"] == "Alice's edit"
    owners = [a for (a,) in conn.execute("SELECT account_id FROM duckies WHERE ducky_id='material-artist' ORDER BY created DESC")]
    assert owners[0] == alice and owners[1].startswith(account_rows.PARKED), "kept, never deleted"
    assert conn.execute("SELECT count(*) FROM duckies WHERE account_id=''").fetchone()[0] == 0


def _statements_of(fn) -> list[str]:
    conn = db.connect()
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        fn()
    finally:
        conn.set_trace_callback(None)
    return seen


def _looks_for_old_rows(statements: list[str]) -> bool:
    return any(t in s for s in statements for t in ("conversations", "folders", "duckies"))


def test_old_rows_are_looked_for_once_per_account_and_again_after_a_restore(who: _Who) -> None:
    assert _looks_for_old_rows(_statements_of(lambda: who.be(ALICE)))
    assert not _looks_for_old_rows(_statements_of(lambda: who.be(ALICE)))
    assert not _looks_for_old_rows(_statements_of(account_rows.current))
    # A restore swaps the file in on the next first open: the old rows may be back.
    db.reset_for_tests()
    assert _looks_for_old_rows(_statements_of(account_rows.current))
    assert not _looks_for_old_rows(_statements_of(account_rows.current))


def test_search_tokens_match_like_the_words_did() -> None:
    key = b"k" * 32
    text = account_rows.blind_text("Spawner wiring for goblin_needle, Café déjà vu", key)
    assert "goblin" not in text and "spawner" not in text
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(text, tokenize='unicode61')")
    conn.execute("INSERT INTO t(rowid, text) VALUES (1, ?)", (text,))

    def hit(query: str) -> bool:
        q = account_rows.blind_query(query, key)
        return bool(q) and conn.execute("SELECT count(*) FROM t WHERE t MATCH ?", (q,)).fetchone()[0] == 1

    assert hit("goblin_needle") and hit("GOBLIN") and hit("cafe") and hit("wiring spawner")
    assert hit("goblin needle")  # both words, or the two as one phrase
    assert not hit("needle_goblin") and not hit("gobli") and not hit("wizard")
    assert account_rows.blind_query("!!", key) == ""
    assert account_rows.blind_text("same", key) != account_rows.blind_text("same", b"j" * 32)


def test_who_is_signed_in_is_read_once_and_follows_a_sign_in_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from backend.agent import secrets
    from backend.store.repos import secrets as repo
    from frontend import duckyos_account

    if sys.platform != "win32":
        pytest.skip("the login row is DPAPI-sealed")
    data_crypto.reset_for_tests()
    secrets.forget_login_stamp()
    duckyos_account.forget_account_key()
    assert secrets.login_stamp() is not None  # the login lives in ducky.db: the memo is on
    reads = [0]
    real = repo.login_version
    monkeypatch.setattr(repo, "login_version", lambda: reads.__setitem__(0, reads[0] + 1) or real())
    assert {account_rows.current() for _ in range(1000)} == {LOCAL}
    assert reads[0] <= 1, reads[0]
    login = {"device_key": "d", "email": "ana@example.com", "base_url": "https://uefnducky.org"}
    secrets.set_key(duckyos_account._CREDENTIALS_KEY, json.dumps(login))
    assert account_rows.current() == account_id_for("https://uefnducky.org|ana@example.com")
    secrets.clear_key(duckyos_account._CREDENTIALS_KEY)
    assert account_rows.current() == LOCAL
    # Another process's sign-in (the meta row moves, this process's counter doesn't) shows
    # once the stamp is older than STAMP_TTL_S.
    monkeypatch.setattr(secrets, "STAMP_TTL_S", 0.0)
    before = reads[0]
    account_rows.current()
    assert reads[0] == before + 1
    data_crypto.reset_for_tests()


def test_the_file_keys_are_looked_for_once_per_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.store import sealed_files

    account_rows.forget()
    tries = [0]

    def failing(account: str) -> None:
        tries[0] += 1
        raise OSError("keys file busy")

    monkeypatch.setattr(sealed_files, "ensure_keypair", failing)
    for _ in range(1000):
        account_rows._ensure_file_keys(LOCAL)
    assert tries[0] == 1  # a failed try waits FILE_KEYS_RETRY_S, not the next call
    monkeypatch.setattr(sealed_files, "ensure_keypair", lambda account: tries.__setitem__(0, tries[0] + 1))
    account_rows.forget()  # a login change tries again at once
    for _ in range(1000):
        account_rows._ensure_file_keys(LOCAL)
    assert tries[0] == 2
    account_rows.forget()
